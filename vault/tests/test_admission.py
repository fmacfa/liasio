import io
import threading
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.uploadhandler import TemporaryFileUploadHandler
from django.core.management import call_command
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from vault.capacity import (
    CapacityUnavailable,
    active_leases,
    capacity_lock,
    reserve_temporary,
)
from vault.forms import BoundedPasswordChangeForm, BoundedSetPasswordForm
from vault.models import Collection, ShareLink, User
from vault.operations import snapshot
from vault.storage import download_bundle
from .test_security import IsolatedFiles, PASSWORD


class PasswordInterleavingTests(IsolatedFiles, TestCase):
    def form(self, kind, user):
        form = kind(
            user,
            data={
                "old_password": PASSWORD,
                "new_password1": PASSWORD + "next",
                "new_password2": PASSWORD + "next",
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def test_both_password_forms_preserve_concurrent_quota_and_consumed_codes(self):
        for index, kind in enumerate(
            (BoundedPasswordChangeForm, BoundedSetPasswordForm)
        ):
            user = self.account(f"user-{index}@example.com")
            form = self.form(kind, user)
            User.objects.filter(pk=user.pk).update(
                used_bytes=100,
                reserved_bytes=200,
                quota_bytes=500,
                recovery_codes=["remaining-hash"],
                mfa_last_step=123,
            )
            form.save()
            user.refresh_from_db()
            self.assertEqual(
                (user.used_bytes, user.reserved_bytes, user.quota_bytes),
                (100, 200, 500),
            )
            self.assertEqual(user.recovery_codes, ["remaining-hash"])
            self.assertEqual(user.mfa_last_step, 123)
            self.assertTrue(user.check_password(PASSWORD + "next"))

    def test_suspension_and_changed_mfa_or_reset_authority_cannot_be_overwritten(self):
        changes = (
            {"is_active": False},
            {"mfa_version": uuid.uuid4(), "mfa_secret": "new-enrollment"},
            {"password": "changed-credential"},
            {"last_login": timezone.now()},
        )
        for index, kind in enumerate(
            (BoundedPasswordChangeForm, BoundedSetPasswordForm)
        ):
            for number, change in enumerate(changes):
                user = self.account(f"user-{index}-{number}@example.com")
                form = self.form(kind, user)
                User.objects.filter(pk=user.pk).update(**change)
                with self.assertRaises(ValidationError):
                    form.save()
                user.refresh_from_db()
                for field, expected in change.items():
                    self.assertEqual(getattr(user, field), expected)


class TemporaryCapacityTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.user = self.account()
        self.item = self.collection(self.user)
        self.client.force_login(self.user)

    def upload(self):
        return self.client.post(
            f"/collections/{self.item.pk}/upload/",
            {"files": SimpleUploadedFile("new.txt", b"contents")},
        )

    def test_exhausted_quota_and_low_disk_reject_before_body_materialization(self):
        with patch.object(
            TemporaryFileUploadHandler,
            "new_file",
            side_effect=AssertionError("body was parsed"),
        ):
            User.objects.filter(pk=self.user.pk).update(quota_bytes=5)
            self.assertEqual(self.upload().status_code, 413)
            User.objects.filter(pk=self.user.pk).update(quota_bytes=1024**3)
            with patch(
                "vault.capacity.shutil.disk_usage", return_value=SimpleNamespace(free=1)
            ):
                self.assertEqual(self.upload().status_code, 503)

    def test_actual_bytes_cannot_exceed_account_allowance_and_leases_are_released(self):
        User.objects.filter(pk=self.user.pk).update(quota_bytes=6)
        self.assertEqual(self.upload().status_code, 302)
        self.assertEqual(self.item.files.count(), 1)
        with capacity_lock():
            self.assertEqual(active_leases(), [])
        User.objects.filter(pk=self.user.pk).update(quota_bytes=1024**3)
        self.upload()
        self.assertEqual(self.item.files.count(), 2)
        with capacity_lock():
            self.assertEqual(active_leases(), [])

    def test_archive_admission_fails_before_zip_and_does_not_consume_link(self):
        link = ShareLink.objects.create(
            collection=self.item, label="test", max_downloads=1
        )
        with (
            patch(
                "vault.capacity.shutil.disk_usage", return_value=SimpleNamespace(free=1)
            ),
            patch(
                "vault.storage.tempfile.TemporaryFile",
                side_effect=AssertionError("ZIP allocated"),
            ),
        ):
            with self.assertRaises(CapacityUnavailable):
                download_bundle(link)
        link.refresh_from_db()
        self.assertEqual(link.downloads, 0)

    def test_archive_reservation_lasts_until_stream_close_and_handles_failure(self):
        link = ShareLink.objects.create(collection=self.item, label="test")
        response = self.client.post(link.public_url + "download/")
        self.assertEqual(response.status_code, 200)
        with capacity_lock():
            self.assertEqual(len(active_leases()), 1)
        b"".join(response.streaming_content)
        self.assertTrue(response.closed)
        with capacity_lock():
            self.assertEqual(active_leases(), [])
        with patch(
            "vault.storage.zipfile.ZipFile.write", side_effect=OSError("fixture error")
        ):
            with self.assertRaises(OSError):
                download_bundle(link)
        with capacity_lock():
            self.assertEqual(active_leases(), [])

    def test_worker_exit_recovers_lease_without_expiring_active_downloads(self):
        lease = reserve_temporary(1024)
        with capacity_lock():
            self.assertEqual(len(active_leases()), 1)
        lease.handle.close()  # Kernel closes descriptors on process exit.
        with capacity_lock():
            self.assertEqual(active_leases(), [])
        self.assertFalse(lease.path.exists())

    def test_multipart_parallel_limit_and_resumable_share_same_disk_budget(self):
        with (
            reserve_temporary(1024, owner_id=self.user.pk, kind="multipart"),
            reserve_temporary(1024, owner_id=self.user.pk, kind="multipart"),
        ):
            with self.assertRaises(CapacityUnavailable):
                reserve_temporary(1024, owner_id=self.user.pk, kind="multipart")
        item = Collection.objects.create(owner=self.user, title="Another")
        with patch(
            "vault.capacity.shutil.disk_usage", return_value=SimpleNamespace(free=3000)
        ):
            with reserve_temporary(2000):
                response = self.client.post(
                    f"/collections/{item.pk}/uploads/start/",
                    {
                        "files": [
                            {"path": "a.txt", "size": 1000, "fingerprint": "a" * 64}
                        ]
                    },
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.reserved_bytes, 0)

    def test_rechecking_old_backup_does_not_refresh_snapshot_age(self):
        old = (timezone.now() - timedelta(days=10)).isoformat()
        for _ in range(2):
            call_command(
                "record_backup_check", snapshot_created_at=old, stdout=io.StringIO()
            )
            self.assertFalse(snapshot()["backup_verified_recently"])


class ConcurrentCapacityTests(IsolatedFiles, TransactionTestCase):
    def test_simultaneous_workers_cannot_overbook_disk(self):
        barrier = threading.Barrier(2)
        release = threading.Event()

        def attempt():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                with reserve_temporary(6000):
                    release.wait(timeout=5)
                    return True
            except CapacityUnavailable:
                return False
            finally:
                close_old_connections()

        with (
            patch(
                "vault.capacity.shutil.disk_usage",
                return_value=SimpleNamespace(free=10000),
            ),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            futures = [pool.submit(attempt) for _ in range(2)]
            try:
                done, _ = wait(futures, timeout=4, return_when=FIRST_COMPLETED)
                self.assertEqual(len(done), 1)
                self.assertFalse(next(iter(done)).result())
            finally:
                release.set()
            self.assertEqual(sorted(f.result() for f in futures), [False, True])
