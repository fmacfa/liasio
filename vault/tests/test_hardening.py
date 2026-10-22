"""Small deterministic regressions for the hardening policy; no external I/O."""

import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import close_old_connections, connection, connections, transaction
from django.http import Http404
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone

from examples.upload import PrivateState, upload
from vault.mfa import cipher
from vault.models import Audit, ServiceState, User, Webhook, WebhookDelivery
from vault.trash import purge, restore, trash_assets, trash_collection
from vault.webhooks import claim_delivery, deliver_one, enqueue, pause_hook
from .test_security import IsolatedFiles, PASSWORD


def hook_for(owner):
    return Webhook.objects.create(
        owner=owner,
        name="Fixture",
        hostname="example.com",
        destination=cipher().encrypt(b"https://example.com/fixture").decode(),
        secret=cipher().encrypt(b"fixture-signing-key").decode(),
        events=["link.opened"],
    )


def pending(hook, **fields):
    return WebhookDelivery.objects.create(
        webhook=hook, kind="link.opened", payload={}, **fields
    )


class WebhookSchedulingTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.a = self.account()
        self.b = self.account("second@example.com")
        self.hook_a, self.hook_b = hook_for(self.a), hook_for(self.b)

    def test_turns_are_per_owner_not_per_hook_and_persist_between_calls(self):
        second_hook = hook_for(self.a)
        first = pending(self.hook_a, next_at=timezone.now() - timedelta(minutes=3))
        pending(second_hook, next_at=timezone.now() - timedelta(minutes=2))
        pending(self.hook_b)
        with patch("vault.webhooks.send", return_value=204) as send:
            self.assertTrue(deliver_one())
            self.assertEqual(send.call_args.args[0].pk, first.pk)
            self.assertEqual(
                ServiceState.objects.get(pk="webhook_scheduler").data["last_owner_id"],
                self.a.pk,
            )
            self.assertTrue(deliver_one())
            self.assertTrue(deliver_one())
            self.assertFalse(deliver_one())
        self.assertEqual(
            [call.args[1].owner_id for call in send.call_args_list],
            [self.a.pk, self.b.pk, self.a.pk],
        )

    def test_one_owner_cannot_occupy_two_worker_slots(self):
        pending(self.hook_a)
        pending(self.hook_a)
        pending(self.hook_b)
        first, second = claim_delivery(), claim_delivery()
        self.assertEqual({first[2], second[2]}, {self.a.pk, self.b.pk})
        self.assertIsNone(claim_delivery())

    def test_history_expiry_during_selection_does_not_stop_dispatch(self):
        ServiceState.objects.get_or_create(key="webhook_scheduler")
        for phase in ("after_owner", "after_delivery"):
            with self.subTest(phase=phase):
                delivery = pending(self.hook_a)
                if phase == "after_owner":
                    original = ServiceState.save

                    def expire_at_cursor(instance, *args, **kwargs):
                        result = original(instance, *args, **kwargs)
                        if instance.pk == "webhook_scheduler":
                            WebhookDelivery.objects.filter(pk=delivery.pk).delete()
                        return result

                    interception = patch.object(ServiceState, "save", expire_at_cursor)
                else:
                    original = Webhook.objects.select_for_update

                    def expire_at_hook(*args, **kwargs):
                        WebhookDelivery.objects.filter(pk=delivery.pk).delete()
                        return original(*args, **kwargs)

                    interception = patch.object(
                        Webhook.objects, "select_for_update", expire_at_hook
                    )
                with interception, patch("vault.webhooks.send") as send:
                    self.assertTrue(deliver_one())
                    send.assert_not_called()
                self.assertIsNone(claim_delivery())

    def test_expired_owner_and_delivery_leases_recover(self):
        delivery = pending(self.hook_a)
        claimed = claim_delivery()
        self.assertIsNone(claim_delivery())
        past = timezone.now() - timedelta(seconds=1)
        User.objects.filter(pk=self.a.pk).update(webhook_lease_until=past)
        WebhookDelivery.objects.filter(pk=delivery.pk).update(next_at=past)
        with patch("vault.webhooks.send", return_value=204):
            self.assertTrue(deliver_one())
        delivery.refresh_from_db()
        self.a.refresh_from_db()
        self.assertEqual((delivery.state, delivery.attempts), ("delivered", 2))
        self.assertIsNone(self.a.webhook_lease)
        self.assertNotEqual(claimed[0].lease, delivery.lease)

    def test_inflight_pause_does_not_resurrect_delivery(self):
        delivery = pending(self.hook_a)

        def pause_during_send(*_args):
            with transaction.atomic():
                pause_hook(Webhook.objects.select_for_update().get(pk=self.hook_a.pk))
            return 204

        with patch("vault.webhooks.send", side_effect=pause_during_send):
            deliver_one()
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "canceled")
        self.assertIsNone(delivery.delivered_at)
        self.assertIsNone(delivery.lease)

    def test_old_worker_cannot_release_new_owner_lease(self):
        pending(self.hook_a)
        replacement = uuid.uuid4()

        def replace_lease(*_args):
            User.objects.filter(pk=self.a.pk).update(webhook_lease=replacement)
            return 204

        with patch("vault.webhooks.send", side_effect=replace_lease):
            deliver_one()
        self.a.refresh_from_db()
        self.assertEqual(self.a.webhook_lease, replacement)

    def test_resume_cancels_legacy_pending_and_future_rows_only(self):
        self.hook_a.active = False
        self.hook_a.save()
        pending(self.hook_a)
        pending(
            self.hook_a, next_at=timezone.now() + timedelta(days=1), lease=uuid.uuid4()
        )
        history = pending(self.hook_a, state="delivered")
        foreign = pending(self.hook_b)
        self.client.force_login(self.a)
        response = self.client.post(
            f"/automations/webhooks/{self.hook_a.pk}/",
            {"action": "resume", "password": PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.hook_a.deliveries.filter(state="canceled").count(), 2)
        history.refresh_from_db()
        foreign.refresh_from_db()
        self.assertEqual((history.state, foreign.state), ("delivered", "pending"))
        enqueue(self.a.pk, "link.opened", {})
        self.assertEqual(self.hook_a.deliveries.filter(state="pending").count(), 1)

    def test_suspension_cancels_future_and_leased_rows(self):
        delivery = pending(
            self.hook_a, next_at=timezone.now() + timedelta(days=1), lease=uuid.uuid4()
        )
        foreign = pending(self.hook_b)
        self.client.force_login(self.account("admin@example.com", admin=True))
        self.client.post(f"/team/members/{self.a.pk}/toggle/")
        delivery.refresh_from_db()
        foreign.refresh_from_db()
        self.assertEqual((delivery.state, foreign.state), ("canceled", "pending"))
        self.assertIsNone(delivery.lease)


class ConcurrentWebhookClaims(IsolatedFiles, TransactionTestCase):
    def test_dispatch_allows_an_event_producer_to_commit_its_owner_reference(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL foreign-key row-lock interaction")
        owner = self.account()
        hook = hook_for(owner)
        pending(hook)
        ServiceState.objects.get_or_create(key="webhook_scheduler")
        hook_locked, owner_locked = threading.Event(), threading.Event()
        original = ServiceState.save

        def cursor_saved(instance, *args, **kwargs):
            result = original(instance, *args, **kwargs)
            if instance.pk == "webhook_scheduler":
                owner_locked.set()
            return result

        def produce():
            close_old_connections()
            try:
                with transaction.atomic():
                    Webhook.objects.select_for_update().get(pk=hook.pk)
                    Audit.objects.create(actor_id=owner.pk, action="fixture_event")
                    hook_locked.set()
                    self.assertTrue(owner_locked.wait(5))
            finally:
                connections.close_all()

        def claim():
            close_old_connections()
            try:
                self.assertTrue(hook_locked.wait(5))
                return claim_delivery()[2]
            finally:
                connections.close_all()

        with (
            patch.object(ServiceState, "save", cursor_saved),
            ThreadPoolExecutor(max_workers=2) as workers,
        ):
            producer, consumer = workers.submit(produce), workers.submit(claim)
            producer.result(timeout=10)
            self.assertEqual(consumer.result(timeout=10), owner.pk)

    def test_two_workers_claim_different_owners(self):
        a, b = self.account(), self.account("second@example.com")
        hook_a, hook_b = hook_for(a), hook_for(b)
        pending(hook_a)
        pending(hook_a)
        pending(hook_b)

        def claim():
            close_old_connections()
            try:
                result = claim_delivery()
                return result[2]
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(claim) for _ in range(2)]
            self.assertEqual({f.result(timeout=10) for f in futures}, {a.pk, b.pk})
        self.assertIsNone(claim_delivery())


class RetentionRecheckTests(IsolatedFiles, TestCase):
    def test_retrash_renews_retention_under_the_purge_lock(self):
        owner = self.account()
        for kind in ("asset", "collection"):
            with self.subTest(kind=kind):
                collection = self.collection(owner)
                asset = collection.files.get()
                item = asset if kind == "asset" else collection
                delete = (
                    (lambda: trash_assets(owner, [asset.pk]))
                    if kind == "asset"
                    else (lambda: trash_collection(owner, collection.pk))
                )
                delete()
                cutoff = timezone.now() - timedelta(days=30)
                type(item).all_objects.filter(pk=item.pk).update(
                    deleted_at=cutoff - timedelta(days=1)
                )
                restore(owner, kind, item.pk)
                delete()
                with self.assertRaises(Http404):
                    purge(owner, kind, item.pk, expired_before=cutoff)
                self.assertTrue(Path(asset.blob.path).exists())
                # Explicit owner-requested purge is still allowed immediately.
                with self.captureOnCommitCallbacks(execute=True):
                    purge(owner, kind, item.pk)
                self.assertFalse(Path(asset.blob.path).exists())


class PrivateClientStateTests(SimpleTestCase):
    def test_atomic_updates_are_private_and_ignore_predictable_tmp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "state.json.tmp"
            legacy.write_text("unrelated")
            with PrivateState(root / "state.json") as state:
                self.assertIsNone(state.read())
                state.save({"collection": "one"})
                state.save({"collection": "two"})
                self.assertEqual(state.read(), {"collection": "two"})
                self.assertEqual(
                    stat.S_IMODE((root / "state.json").stat().st_mode), 0o600
                )
            self.assertEqual(legacy.read_text(), "unrelated")
            self.assertEqual(
                sorted(p.name for p in root.iterdir()), ["state.json", "state.json.tmp"]
            )

    def test_non_private_directory_and_symlink_state_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o755)
            with self.assertRaises(ValueError):
                with PrivateState(root / "state.json"):
                    pass
            root.chmod(0o700)
            (root / "existing").write_text("unrelated")
            (root / "state.json").symlink_to(root / "existing")
            with PrivateState(root / "state.json") as state, self.assertRaises(OSError):
                state.read()
            self.assertEqual((root / "existing").read_text(), "unrelated")

    def test_two_clients_cannot_update_the_same_state_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            with PrivateState(path):
                with self.assertRaises(BlockingIOError):
                    with PrivateState(path):
                        pass

    def test_upload_uses_saved_ids_and_rejects_changed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "file.txt"
            source.write_bytes(b"hello")
            private = root / "state"
            private.mkdir(mode=0o700)

            class FixtureAPI:
                origin = "https://example.com"
                changed = False
                calls = []

                def request(self, path, data=None, key=None, headers=None):
                    self.calls.append(path)
                    if path.endswith("collections/create/"):
                        return {"id": "collection"}
                    if path.endswith("uploads/start/"):
                        return {"id": "upload"}
                    if path.endswith("status/"):
                        if self.changed:
                            source.write_bytes(b"different")
                        return {
                            "id": "upload",
                            "state": "pending",
                            "chunk_bytes": 5,
                            "parts": [
                                {
                                    "id": "part",
                                    "path": "file.txt",
                                    "size": 5,
                                    "offset": 0,
                                }
                            ],
                        }
                    if path.endswith("chunk/"):
                        assert data == b"hello"
                        return {"offset": 5}
                    if path.endswith("links/"):
                        return {"id": "link"}
                    return {"id": "link", "url": "https://example.com/s/fixture/"}

            api = FixtureAPI()
            state = private / "state.json"
            self.assertEqual(upload(api, source, "Fixture", state)["id"], "link")
            api.calls = []
            upload(api, source, "Fixture", state)
            self.assertEqual(api.calls, ["/api/v1/links/link/"])
            api.changed = True
            with self.assertRaisesMessage(ValueError, "File changed"):
                upload(api, source, "Fixture", private / "second.json")


class DecoderSandboxTests(SimpleTestCase):
    @skipUnless(sys.platform == "linux", "Production Landlock boundary is Linux-only")
    def test_decoder_cannot_modify_sibling_socket_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "request"
            broker = root / "broker"
            work.mkdir()
            broker.mkdir()
            marker = broker / "marker"
            marker.write_text("broker-owned")
            code = """
import pathlib, sys
from scripts.preview_sandbox import confine
work, broker = map(pathlib.Path, sys.argv[1:])
confine(work)
(work / 'result').write_text('allowed')
for operation in [lambda: (broker / 'marker').unlink(), lambda: (broker / 'marker').write_text('changed'), lambda: (broker / 'new').write_text('new')]:
    try:
        operation()
    except PermissionError:
        continue
    raise AssertionError('write outside request directory was permitted')
"""
            result = subprocess.run(
                [sys.executable, "-c", code, str(work), str(broker)],
                cwd=settings.BASE_DIR,
                capture_output=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(marker.read_text(), "broker-owned")
            self.assertEqual((work / "result").read_text(), "allowed")
