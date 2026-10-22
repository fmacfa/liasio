import hashlib
import io
import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pyotp
from django.conf import settings
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command, CommandError
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from vault.mfa import cipher, consume_code, recovery_codes
from vault.models import Asset, Collection, OperationalAlert, ShareLink, UploadSession
from vault.operations import monitor
from vault.resumable import cleanup_expired, part_path
from vault.storage import add_files, delete_assets, delete_collection
from vault.trash import purge, purge_expired, restore
from .test_security import IsolatedFiles, PASSWORD


class MFATests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.user = self.account()
        self.secret = pyotp.random_base32()

    def enable(self):
        self.user.mfa_secret = cipher().encrypt(self.secret.encode()).decode()
        self.codes = recovery_codes(self.user)
        self.user.save()

    def password_login(self):
        return self.client.post(
            "/login/", {"email": self.user.email, "password": PASSWORD}
        )

    def test_password_alone_cannot_authenticate_and_totp_replay_is_rejected(self):
        self.enable()
        self.assertRedirects(self.password_login(), "/login/mfa/")
        self.assertNotIn("_auth_user_id", self.client.session)
        code = pyotp.TOTP(self.secret).now()
        self.assertRedirects(self.client.post("/login/mfa/", {"code": code}), "/")
        self.client.post("/logout/")
        self.password_login()
        self.assertContains(
            self.client.post("/login/mfa/", {"code": code}), "déjà utilisé"
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_recovery_code_is_consumed_and_not_stored_plaintext(self):
        self.enable()
        self.assertNotIn(self.codes[0], json.dumps(self.user.recovery_codes))
        self.password_login()
        self.assertRedirects(
            self.client.post("/login/mfa/", {"code": self.codes[0]}), "/"
        )
        self.user.refresh_from_db()
        self.assertEqual(len(self.user.recovery_codes), 9)
        self.assertFalse(consume_code(self.user, self.codes[0]))

    def test_pending_login_expires_and_password_change_invalidates_it(self):
        self.enable()
        self.password_login()
        pending = self.client.session
        data = pending["pending_login"]
        data["at"] -= 301
        pending["pending_login"] = data
        pending.save()
        self.assertRedirects(
            self.client.post("/login/mfa/", {"code": self.codes[0]}), "/login/"
        )
        self.password_login()
        self.user.set_password(PASSWORD + "new")
        self.user.save()
        self.assertRedirects(
            self.client.post("/login/mfa/", {"code": self.codes[0]}), "/login/"
        )

    def test_suspension_invalidates_pending_mfa_after_reactivation(self):
        self.enable()
        self.password_login()
        admin = Client()
        admin.force_login(self.account("admin@example.com", admin=True))
        admin.post(f"/team/members/{self.user.pk}/toggle/")
        admin.post(f"/team/members/{self.user.pk}/toggle/")
        self.assertRedirects(
            self.client.post("/login/mfa/", {"code": self.codes[0]}), "/login/"
        )
        self.assertNotIn("_auth_user_id", self.client.session)
        self.password_login()
        self.assertRedirects(
            self.client.post("/login/mfa/", {"code": self.codes[0]}), "/"
        )

    def test_setup_requires_password_and_valid_code_and_invalidates_old_sessions(self):
        other = Client()
        other.force_login(self.user)
        self.client.force_login(self.user)
        self.client.post("/account/mfa/", {"action": "begin", "password": "incorrect"})
        self.assertNotIn("mfa_setup", self.client.session)
        self.client.post("/account/mfa/", {"action": "begin", "password": PASSWORD})
        setup = self.client.session["mfa_setup"]
        secret = cipher().decrypt(setup["secret"].encode()).decode()
        qr = self.client.get("/account/mfa/qr/")
        self.assertEqual(qr.status_code, 200)
        self.assertEqual(qr["Content-Type"], "image/svg+xml")
        result = self.client.post(
            "/account/mfa/",
            {
                "action": "enable",
                "password": PASSWORD,
                "code": pyotp.TOTP(secret).now(),
            },
        )
        self.assertContains(result, "Conservez ces codes")
        codes = result.context["codes"]
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.mfa_secret, secret)
        self.assertRedirects(other.get("/"), "/login/?next=/")
        self.assertNotContains(self.client.get("/account/mfa/"), codes[0])
        self.assertEqual(self.client.get("/account/mfa/qr/").status_code, 404)
        self.client.post(
            "/account/mfa/",
            {"action": "disable", "password": PASSWORD, "code": codes[0]},
        )
        self.user.refresh_from_db()
        self.assertFalse(self.user.mfa_secret)
        self.assertFalse(other.get("/").status_code == 200)

    def test_missing_mfa_marker_cannot_use_existing_authenticated_session(self):
        self.enable()
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get("/"), "/login/")

    def test_wrong_key_fails_closed(self):
        self.enable()
        with override_settings(MFA_ENCRYPTION_KEY="", SECRET_KEY="x" * 64):
            self.assertFalse(consume_code(self.user, pyotp.TOTP(self.secret).now()))

    def test_rate_limit_challenge(self):
        self.enable()
        self.password_login()
        for _ in range(10):
            self.client.post("/login/mfa/", {"code": "invalid"})
        self.assertEqual(
            self.client.post("/login/mfa/", {"code": self.codes[0]}).status_code, 429
        )


class TrashTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.user = self.account()
        self.collection = self.collection(self.user)
        self.asset = self.collection.files.get()
        self.link = ShareLink.objects.create(collection=self.collection, label="test")
        self.client.force_login(self.user)

    def test_restore_does_not_reactivate_old_links_or_duplicate_quota(self):
        delete_collection(self.user, self.collection.pk)
        self.assertFalse(Collection.objects.filter(pk=self.collection.pk).exists())
        self.assertTrue(Asset.all_objects.filter(pk=self.asset.pk).exists())
        self.assertEqual(self.client.get(self.link.public_url).status_code, 410)
        restore(self.user, "collection", self.collection.pk)
        self.assertTrue(Collection.objects.filter(pk=self.collection.pk).exists())
        self.assertEqual(self.client.get(self.link.public_url).status_code, 410)
        self.user.refresh_from_db()
        self.assertEqual(self.user.used_bytes, self.asset.size)

    def test_purge_releases_quota_and_removes_blob_after_commit(self):
        delete_assets(self.user, [self.asset.pk])
        path = Path(self.asset.blob.path)
        with self.captureOnCommitCallbacks(execute=True):
            purge(self.user, "asset", self.asset.pk)
        self.assertFalse(path.exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.used_bytes, 0)

    def test_collection_restore_keeps_earlier_individual_deletions_in_trash(self):
        delete_assets(self.user, [self.asset.pk])
        delete_collection(self.user, self.collection.pk)
        restore(self.user, "collection", self.collection.pk)
        self.assertFalse(Asset.objects.filter(pk=self.asset.pk).exists())

    def test_retention_purges_old_trash_only(self):
        delete_assets(self.user, [self.asset.pk])
        purge_expired()
        self.assertTrue(Asset.all_objects.filter(pk=self.asset.pk).exists())
        Asset.all_objects.filter(pk=self.asset.pk).update(
            deleted_at=timezone.now() - timedelta(days=31)
        )
        with self.captureOnCommitCallbacks(execute=True):
            purge_expired()
        self.assertFalse(Asset.all_objects.filter(pk=self.asset.pk).exists())

    def test_foreign_restore_and_purge_are_rejected_and_purge_needs_confirmation(self):
        delete_assets(self.user, [self.asset.pk])
        endpoint = f"/trash/asset/{self.asset.pk}/"
        self.assertContains(
            self.client.post(endpoint, {"action": "purge"}), "Supprimer définitivement"
        )
        self.assertTrue(Asset.all_objects.filter(pk=self.asset.pk).exists())
        other = self.account("other@example.com")
        self.client.force_login(other)
        self.assertEqual(
            self.client.post(endpoint, {"action": "restore"}).status_code, 404
        )
        self.assertEqual(
            self.client.post(
                endpoint, {"action": "purge", "confirm": "purge"}
            ).status_code,
            404,
        )

    def test_trash_reserves_paths_and_remains_in_backup(self):
        self.link.revoked_at = timezone.now()
        self.link.save()
        delete_assets(self.user, [self.asset.pk])
        with self.assertRaises(ValidationError):
            add_files(
                self.user,
                self.collection.pk,
                [SimpleUploadedFile("note.txt", b"new")],
                [self.asset.path],
            )
        output = io.StringIO()
        call_command("verify_storage", stdout=output)
        self.assertEqual(json.loads(output.getvalue())["files"], 1)


class ResumableTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.user = self.account()
        self.item = Collection.objects.create(owner=self.user, title="Upload")
        self.client.force_login(self.user)

    def start(self, size=10, path="folder/document.txt", client=None):
        return (client or self.client).post(
            f"/collections/{self.item.pk}/uploads/start/",
            {"files": [{"path": path, "size": size, "fingerprint": "a" * 64}]},
            content_type="application/json",
        )

    def test_start_rejects_non_object_json(self):
        for data in ([], None, True, "files", 12):
            with self.subTest(data=data):
                response = self.client.post(
                    f"/collections/{self.item.pk}/uploads/start/",
                    json.dumps(data),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.start().status_code, 201)

    def block(self, session, data, offset=0, client=None, checksum=None):
        return (client or self.client).post(
            f"/uploads/{session['id']}/parts/{session['parts'][0]['id']}/chunk/",
            data,
            content_type="application/octet-stream",
            HTTP_UPLOAD_OFFSET=str(offset),
            HTTP_UPLOAD_CHECKSUM=checksum or hashlib.sha256(data).hexdigest(),
        )

    def test_interruption_resume_checksums_and_finish_idempotency(self):
        session = self.start().json()
        self.assertEqual(self.block(session, b"hello").json()["offset"], 5)
        self.assertEqual(self.block(session, b"hello").status_code, 409)
        self.assertEqual(
            self.block(session, b"world", 5, checksum="0" * 64).status_code, 400
        )
        self.assertEqual(
            self.client.post(f"/uploads/{session['id']}/finish/").status_code, 400
        )
        status = self.client.get(f"/uploads/{session['id']}/status/").json()
        self.assertEqual(status["parts"][0]["offset"], 5)
        self.block(session, b"world", 5)
        for _ in range(2):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(f"/uploads/{session['id']}/finish/")
            self.assertEqual(response.status_code, 200)
        asset = self.item.files.get()
        self.assertEqual(asset.sha256, hashlib.sha256(b"helloworld").hexdigest())
        self.user.refresh_from_db()
        self.assertEqual((self.user.used_bytes, self.user.reserved_bytes), (10, 0))

    def test_cancel_and_expiry_release_reservations(self):
        session = self.start().json()
        self.block(session, b"hello")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f"/uploads/{session['id']}/cancel/")
        self.assertFalse((settings.UPLOAD_STAGING_ROOT / session["id"]).exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.reserved_bytes, 0)
        session = self.start().json()
        UploadSession.objects.filter(pk=session["id"]).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(self.block(session, b"hello").status_code, 400)
        cleanup_expired()
        self.user.refresh_from_db()
        self.assertEqual(self.user.reserved_bytes, 0)

    def test_quota_and_duplicate_reservations_are_atomic(self):
        self.user.quota_bytes = 15
        self.user.save()
        self.assertEqual(self.start().status_code, 201)
        self.assertEqual(self.start(path="another.txt").status_code, 400)
        self.assertEqual(self.start(size=5).status_code, 400)

    def test_scope_path_limits_and_missing_csrf(self):
        self.assertEqual(self.start(path="../bad.txt").status_code, 400)
        self.assertEqual(self.start(path="script.html").status_code, 400)
        self.assertEqual(self.start(size=settings.MAX_FILE_BYTES + 1).status_code, 400)
        session = self.start().json()
        other = self.account("other@example.com")
        foreign = Client()
        foreign.force_login(other)
        self.assertEqual(self.block(session, b"hello", client=foreign).status_code, 404)
        self.assertEqual(
            foreign.post(f"/uploads/{session['id']}/finish/").status_code, 404
        )
        guarded = Client(enforce_csrf_checks=True)
        guarded.force_login(self.user)
        self.assertEqual(self.start(client=guarded).status_code, 403)

    def test_crash_tail_is_truncated_before_resume(self):
        session = self.start().json()
        self.block(session, b"hello")
        part = UploadSession.objects.get(pk=session["id"]).parts.get()
        with part_path(part).open("ab") as target:
            target.write(b"uncommitted tail")
        self.assertEqual(self.block(session, b"world", 5).status_code, 200)
        self.assertEqual(part_path(part).read_bytes(), b"helloworld")

    def test_antivirus_failure_keeps_resumable_bytes_and_no_public_assets(self):
        session = self.start().json()
        self.block(session, b"helloworld")
        with patch("vault.storage.scan", side_effect=ValidationError("Rejected")):
            response = self.client.post(f"/uploads/{session['id']}/finish/")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.item.files.exists())
        self.user.refresh_from_db()
        self.assertEqual((self.user.used_bytes, self.user.reserved_bytes), (0, 10))

    def test_empty_file_and_collection_deletion_cancel_pending(self):
        session = self.start(size=0).json()
        self.assertEqual(
            self.client.post(f"/uploads/{session['id']}/finish/").status_code, 200
        )
        self.assertEqual(self.item.files.get().size, 0)
        session = self.start(path="other.txt").json()
        delete_collection(self.user, self.item.pk)
        self.user.refresh_from_db()
        self.assertEqual(self.user.reserved_bytes, 0)
        self.assertEqual(self.block(session, b"hello").status_code, 400)

    def test_active_share_prevents_commit_even_if_created_after_start(self):
        session = self.start().json()
        self.block(session, b"helloworld")
        ShareLink.objects.create(collection=self.item, label="Concurrent")
        self.assertEqual(
            self.client.post(f"/uploads/{session['id']}/finish/").status_code, 400
        )


class OperationsTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.user = self.account(admin=True)
        self.item = self.collection(self.user)
        self.client.force_login(self.user)

    def test_staff_quota_edit_accounts_for_reserved_uploads(self):
        self.user.reserved_bytes = 1024**3
        self.user.save()
        url = f"/operations/members/{self.user.pk}/quota/"
        self.client.post(url, {"quota_gib": "0.5"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.quota_bytes, settings.DEFAULT_QUOTA_BYTES)
        self.client.post(url, {"quota_gib": "2"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.quota_bytes, 2 * 1024**3)
        for invalid in ("NaN", "Infinity", "-1", "10000000000"):
            self.assertEqual(
                self.client.post(url, {"quota_gib": invalid}).status_code, 302
            )
        self.client.force_login(self.account("member@example.com"))
        self.assertEqual(self.client.get("/operations/").status_code, 404)
        self.assertEqual(self.client.post(url, {"quota_gib": "3"}).status_code, 404)

    @override_settings(MONITORING_TOKEN="a" * 40)
    def test_monitor_endpoint_requires_bearer_and_contains_no_account_data(self):
        self.assertEqual(self.client.get("/ready/").status_code, 404)
        self.assertEqual(
            self.client.get(
                "/ready/", HTTP_AUTHORIZATION="Bearer invalide-é"
            ).status_code,
            404,
        )
        response = self.client.get("/ready/", HTTP_AUTHORIZATION="Bearer " + "a" * 40)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.user.email)
        self.assertTrue(response.json()["ready"])

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST="smtp.example.test",
        MONITOR_EMAIL="ops@example.test",
    )
    def test_alerts_deduplicate_until_recovery(self):
        status = {
            "disk_used_percent": 90,
            "disk_free_bytes": 100,
            "antivirus": True,
            "maintenance": True,
            "backup_verified_recently": True,
            "ready": True,
        }
        with patch("vault.operations.snapshot", return_value=status):
            monitor()
            monitor()
        self.assertEqual(len(mail.outbox), 1)
        with patch(
            "vault.operations.snapshot",
            return_value={**status, "disk_used_percent": 10},
        ):
            monitor()
        self.assertFalse(OperationalAlert.objects.get(pk="disk").active)
        with patch("vault.operations.snapshot", return_value=status):
            monitor()
        self.assertEqual(len(mail.outbox), 2)

    def test_verify_storage_detects_corruption(self):
        asset = self.item.files.get()
        Path(asset.blob.path).write_bytes(b"corrupted")
        with self.assertRaises(CommandError):
            call_command("verify_storage", stdout=io.StringIO())

    def test_pages_render(self):
        for url in ("/operations/", "/trash/", "/account/mfa/"):
            self.assertEqual(self.client.get(url).status_code, 200)
