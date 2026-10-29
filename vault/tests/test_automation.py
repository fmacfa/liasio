import hashlib
import json
import secrets
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.db import transaction
from django.test import Client, TestCase, SimpleTestCase
from django.utils import timezone
from django.core.exceptions import ValidationError

from vault.api_auth import credential_version, digest
from vault.mfa import cipher
from vault.models import (
    APIToken,
    APIReceipt,
    Collection,
    ShareLink,
    Webhook,
    WebhookDelivery,
)
from vault.storage import download_bundle
from vault.trash import trash_collection
from vault.webhooks import (
    enqueue,
    deliver_one,
    parse_destination,
    public_addresses,
    send,
    PinnedHTTPS,
)
from .test_security import IsolatedFiles, PASSWORD


class AutomationTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.user = self.account()
        self.other = self.account("other@example.com")
        self.key, self.raw = self.key_for(self.user)
        self.browser = Client(enforce_csrf_checks=True)
        self.browser.force_login(self.user)

    def key_for(self, user, scopes=None):
        key = APIToken(
            owner=user,
            name="Workflow",
            scopes=scopes or ["read", "files:write", "links:write"],
            credential_version=credential_version(user),
            expires_at=timezone.now() + timedelta(days=1),
        )
        raw = f"lv_{key.pk.hex}.{secrets.token_urlsafe(32)}"
        key.digest = digest(raw)
        key.save()
        return key, raw

    def post(self, path, data, key="test-request-001", **headers):
        return self.client.post(
            "/api/v1/" + path,
            data=json.dumps(data),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + self.raw,
            HTTP_IDEMPOTENCY_KEY=key,
            **headers,
        )

    def get(self, path):
        return self.client.get(
            "/api/v1/" + path, HTTP_AUTHORIZATION="Bearer " + self.raw
        )

    def hook(self, owner=None, events=None):
        return Webhook.objects.create(
            owner=owner or self.user,
            name="Receiver",
            hostname="example.com",
            destination=cipher().encrypt(b"https://example.com/receiver").decode(),
            secret=cipher().encrypt(b"a-random-signing-secret-for-tests").decode(),
            events=events
            or [
                "collection.created",
                "files.uploaded",
                "link.created",
                "link.revoked",
                "link.opened",
                "download.started",
            ],
        )

    def test_bearer_only_csrf_scope_expiry_revocation_and_user_isolation(self):
        with patch("vault.api_auth.limited", return_value=True):
            throttled = self.get("collections/")
        self.assertEqual(throttled.status_code, 429)
        self.assertEqual(throttled.headers["Retry-After"], "60")
        self.assertEqual(self.browser.get("/api/v1/collections/").status_code, 401)
        self.assertEqual(
            self.browser.post("/automations/keys/%s/revoke/" % self.key.pk).status_code,
            403,
        )
        self.assertEqual(self.get("collections/").status_code, 200)
        foreign = Collection.objects.create(owner=self.other, title="Private")
        self.assertEqual(self.get(f"collections/{foreign.pk}/").status_code, 404)
        self.assertEqual(
            self.post(
                f"collections/{foreign.pk}/links/", {"label": "Forbidden"}
            ).status_code,
            404,
        )
        self.key.scopes = ["read"]
        self.key.save()
        self.assertEqual(
            self.post("collections/create/", {"title": "Forbidden"}).status_code, 403
        )
        for changes in [
            {"revoked_at": timezone.now()},
            {"expires_at": timezone.now() - timedelta(seconds=1)},
        ]:
            APIToken.objects.filter(pk=self.key.pk).update(**changes)
            self.assertEqual(self.get("collections/").status_code, 401)

    def test_password_change_invalidates_existing_key(self):
        self.user.set_password("A-new-password-unique-12345!")
        self.user.save()
        self.assertEqual(self.get("collections/").status_code, 401)

    def test_suspend_reactivate_does_not_resurrect_keys_or_webhooks(self):
        admin = self.account("admin@example.com", admin=True)
        hook = self.hook()
        self.client.force_login(admin)
        self.client.post(f"/team/members/{self.user.pk}/toggle/")
        self.client.post(f"/team/members/{self.user.pk}/toggle/")
        self.assertEqual(self.get("collections/").status_code, 401)
        hook.refresh_from_db()
        self.assertFalse(hook.active)

    def test_upload_to_single_use_link_end_to_end_with_idempotent_retries(self):
        hook = self.hook()
        response = self.post(
            "collections/create/", {"title": "Automated", "description": "Presentation"}
        )
        self.assertEqual(response.status_code, 201, response.content)
        folder = response.json()["id"]
        self.assertEqual(
            self.post(
                "collections/create/",
                {"title": "Automated", "description": "Presentation"},
            ).json()["id"],
            folder,
        )
        self.assertEqual(
            self.post("collections/create/", {"title": "Different"}).status_code, 409
        )
        self.assertEqual(Collection.objects.filter(owner=self.user).count(), 1)
        content = b"An automated file."
        checksum = hashlib.sha256(content).hexdigest()
        manifest = {
            "files": [
                {
                    "path": "Folder/note.txt",
                    "size": len(content),
                    "fingerprint": checksum,
                }
            ]
        }
        start = self.post(
            f"collections/{folder}/uploads/start/", manifest, key="upload-request-001"
        )
        self.assertEqual(start.status_code, 201, start.content)
        upload = start.json()
        self.assertEqual(
            self.post(
                f"collections/{folder}/uploads/start/",
                manifest,
                key="upload-request-001",
            ).json()["id"],
            upload["id"],
        )
        url = f"/api/v1/uploads/{upload['id']}/parts/{upload['parts'][0]['id']}/chunk/"
        headers = {
            "HTTP_AUTHORIZATION": "Bearer " + self.raw,
            "HTTP_UPLOAD_OFFSET": "0",
            "HTTP_UPLOAD_CHECKSUM": checksum,
        }
        block = self.client.post(
            url, content, content_type="application/octet-stream", **headers
        )
        self.assertEqual(block.json()["offset"], len(content))
        retry = self.client.post(
            url, content, content_type="application/octet-stream", **headers
        )
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(retry.json()["offset"], len(content))
        for _attempt in range(2):
            finish = self.post(f"uploads/{upload['id']}/finish/", {})
            self.assertEqual(finish.status_code, 200, finish.content)
        data = {"label": "Recipient", "max_downloads": 1, "allow_previews": True}
        created = self.post(
            f"collections/{folder}/links/", data, key="link-request-001"
        )
        self.assertEqual(created.status_code, 201, created.content)
        replay = self.post(f"collections/{folder}/links/", data, key="link-request-001")
        self.assertEqual(replay.json(), created.json())
        self.assertEqual(replay.headers["Idempotency-Replayed"], "true")
        link = ShareLink.objects.get(pk=created.json()["id"])
        with download_bundle(link) as archive:
            self.assertTrue(archive.read())
        link.refresh_from_db()
        self.assertEqual(link.status_code, "exhausted")
        with self.assertRaises(ValidationError):
            download_bundle(link)
        self.assertEqual(hook.deliveries.filter(kind="files.uploaded").count(), 1)
        self.assertEqual(hook.deliveries.filter(kind="link.created").count(), 1)
        self.assertEqual(hook.deliveries.filter(kind="download.started").count(), 1)
        self.assertNotIn(
            link.public_url, APIReceipt.objects.filter(token=self.key).last().response
        )

    def test_ingestion_rejects_traversal_quota_wrong_checksum_and_active_link_mutation(
        self,
    ):
        folder = self.collection(self.user)
        for path in ["../secret.txt", "hidden/.env", "unsafe.exe"]:
            r = self.post(
                f"collections/{folder.pk}/uploads/start/",
                {"files": [{"path": path, "size": 1, "fingerprint": "a" * 64}]},
            )
            self.assertEqual(r.status_code, 400)
        ShareLink.objects.create(collection=folder)
        r = self.post(
            f"collections/{folder.pk}/uploads/start/",
            {"files": [{"path": "extra.txt", "size": 1, "fingerprint": "a" * 64}]},
        )
        self.assertEqual(r.status_code, 400)
        self.user.quota_bytes = self.user.used_bytes
        self.user.save()
        empty = Collection.objects.create(owner=self.user, title="Quota")
        r = self.post(
            f"collections/{empty.pk}/uploads/start/",
            {"files": [{"path": "extra.txt", "size": 1, "fingerprint": "a" * 64}]},
        )
        self.assertEqual(r.status_code, 400)

    def test_api_antivirus_fail_closed(self):
        folder = self.collection(self.user)
        with self.settings(ANTIVIRUS_REQUIRED=True):
            response = self.post(
                f"collections/{folder.pk}/links/", {"label": "Unscanned"}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_request")
        self.assertEqual(ShareLink.objects.count(), 0)

    def test_link_creation_receipt_replays_after_its_expiry(self):
        folder = self.collection(self.user)
        now = timezone.now()
        data = {
            "label": "Short-lived",
            "expires_at": (now + timedelta(minutes=1)).isoformat(),
        }
        created = self.post(f"collections/{folder.pk}/links/", data)
        self.assertEqual(created.status_code, 201, created.content)
        with patch("vault.api.timezone.now", return_value=now + timedelta(minutes=2)):
            replayed = self.post(f"collections/{folder.pk}/links/", data)
        self.assertEqual(replayed.status_code, 201, replayed.content)
        self.assertEqual(replayed.json(), created.json())
        self.assertEqual(replayed.headers["Idempotency-Replayed"], "true")
        self.assertEqual(ShareLink.objects.count(), 1)

    def test_session_secret_creation_requires_password_and_shows_only_once(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/automations/",
            {
                "action": "token",
                "name": "n8n",
                "scopes": ["read"],
                "days": 30,
                "password": "wrong",
            },
        )
        self.assertContains(response, "incorrect")
        self.assertEqual(APIToken.objects.count(), 1)
        response = self.client.post(
            "/automations/",
            {
                "action": "token",
                "name": "n8n",
                "scopes": ["read"],
                "days": 30,
                "password": PASSWORD,
            },
        )
        secret = response.context["secret"]
        self.assertTrue(secret.startswith("lv_"))
        self.assertNotContains(self.client.get("/automations/"), secret)
        self.assertEqual(
            self.client.get(
                f"/automations/webhooks/{self.hook(self.other).pk}/"
            ).status_code,
            404,
        )

    def test_transaction_rollback_and_owner_subscription_isolation(self):
        own, foreign = self.hook(), self.hook(self.other)
        try:
            with transaction.atomic():
                Collection.objects.create(owner=self.user, title="Rolled back")
                raise RuntimeError
        except RuntimeError:
            pass
        self.assertEqual(WebhookDelivery.objects.count(), 0)
        collection = self.collection(self.user)
        link = ShareLink.objects.create(collection=collection)
        trash_collection(self.user, collection.pk)
        self.assertEqual(own.deliveries.filter(kind="link.revoked").count(), 1)
        self.assertFalse(foreign.deliveries.exists())
        payload = json.dumps(own.deliveries.first().payload)
        self.assertNotIn(self.user.email, payload)
        self.assertNotIn(link.public_url, payload)

    def test_worker_success_retry_terminal_error_and_pause(self):
        hook = self.hook()
        enqueue(self.user.pk, "link.opened", {"link_id": "fixture"})
        delivery = hook.deliveries.get()
        with patch("vault.webhooks.send", return_value=503):
            self.assertTrue(deliver_one())
        delivery.refresh_from_db()
        self.assertEqual((delivery.state, delivery.attempts), ("pending", 1))
        self.assertGreater(delivery.next_at, timezone.now())
        with patch("vault.webhooks.send") as sender:
            self.assertFalse(deliver_one())
            sender.assert_not_called()
        WebhookDelivery.objects.filter(pk=delivery.pk).update(next_at=timezone.now())
        with patch("vault.webhooks.send", return_value=204):
            deliver_one()
        delivery.refresh_from_db()
        self.assertEqual((delivery.state, delivery.attempts), ("delivered", 2))
        enqueue(self.user.pk, "link.opened", {})
        with patch("vault.webhooks.send", return_value=302):
            deliver_one()
        self.assertTrue(
            hook.deliveries.filter(state="failed", http_status=302).exists()
        )
        enqueue(self.user.pk, "link.opened", {})
        hook.active = False
        hook.save()
        with patch("vault.webhooks.send") as sender:
            deliver_one()
            sender.assert_not_called()
        self.assertTrue(hook.deliveries.filter(state="canceled").exists())

    def test_attempt_limit_and_queue_limit_are_bounded(self):
        hook = self.hook()
        enqueue(self.user.pk, "link.opened", {})
        delivery = hook.deliveries.get()
        delivery.attempts = 6
        delivery.save()
        with patch("vault.webhooks.send") as sender:
            deliver_one()
            sender.assert_not_called()
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "failed")
        WebhookDelivery.objects.bulk_create(
            [
                WebhookDelivery(webhook=hook, kind="link.opened", payload={})
                for _ in range(1000)
            ]
        )
        enqueue(self.user.pk, "link.opened", {})
        hook.refresh_from_db()
        self.assertFalse(hook.active)
        self.assertEqual(hook.last_error, "queue_full")
        self.assertEqual(hook.deliveries.filter(state="pending").count(), 0)
        self.assertEqual(
            hook.deliveries.filter(state="canceled", error="queue_full").count(), 1000
        )

    def test_signed_payload_has_stable_id_and_no_secret_or_response_capture(self):
        import hmac

        hook = self.hook()
        enqueue(self.user.pk, "link.created", {"link_id": "fixture"})
        delivery = hook.deliveries.get()
        with (
            patch("vault.webhooks.public_addresses", return_value=["93.184.216.34"]),
            patch("vault.webhooks.PinnedHTTPS") as connection,
        ):
            connection.return_value.getresponse.return_value.status = 204
            self.assertEqual(send(delivery, hook), 204)
            args, kwargs = connection.return_value.request.call_args
            self.assertEqual(args, ("POST", "/receiver"))
            headers, body = kwargs["headers"], kwargs["body"]
            secret = cipher().decrypt(hook.secret.encode())
            expected = hmac.new(
                secret,
                headers["X-LinkVault-Timestamp"].encode() + b"." + body,
                hashlib.sha256,
            ).hexdigest()
            self.assertEqual(headers["X-LinkVault-Signature"], "v1=" + expected)
            self.assertEqual(json.loads(body)["id"], str(delivery.event_id))
            connection.return_value.getresponse.return_value.read.assert_not_called()


class DestinationTests(SimpleTestCase):
    def test_reject_ambiguous_destinations(self):
        for url in [
            "http://example.com",
            "https://user:secret@example.com/",
            "https://localhost/",
            "https://example.com:8443/",
            "https://example.com/?key=secret",
            "https://example.com/#fragment",
            "https://example.com\\@127.0.0.1",
            "https://example.com/\r\nX: injected",
        ]:
            with self.subTest(url=url), self.assertRaises(ValidationError):
                parse_destination(url)
        self.assertEqual(
            parse_destination("https://example.com/webhook/abc"),
            ("example.com", "/webhook/abc"),
        )

    def test_every_resolved_address_must_be_public(self):
        for address in [
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "192.168.0.1",
            "::1",
            "fe80::1",
            "::ffff:127.0.0.1",
            "64:ff9b::7f00:1",
            "224.0.0.1",
        ]:
            with (
                patch(
                    "vault.webhooks.socket.getaddrinfo",
                    return_value=[
                        (2, 1, 6, "", ("93.184.216.34", 443)),
                        (2, 1, 6, "", (address, 443)),
                    ],
                ),
                self.assertRaises(ValueError),
            ):
                public_addresses("example.com")

    def test_pinned_connection_uses_public_ip_but_original_tls_hostname(self):
        connection = PinnedHTTPS("example.com", "93.184.216.34")
        connection._context = MagicMock()
        with patch("vault.webhooks.socket.create_connection") as connect:
            connection.connect()
            connect.assert_called_once_with(("93.184.216.34", 443), timeout=5)
            connection._context.wrap_socket.assert_called_once_with(
                connect.return_value, server_hostname="example.com"
            )


class ReceiverVerificationTests(SimpleTestCase):
    def test_signature_timestamp_and_tampering(self):
        import hmac, uuid
        from examples.verify_webhook import verify

        body = json.dumps(
            {"id": str(uuid.uuid4()), "type": "webhook.test", "data": {}}
        ).encode()
        secret = "receiver-test-secret"
        signature = hmac.new(
            secret.encode(), b"1000." + body, hashlib.sha256
        ).hexdigest()
        headers = {
            "X-LinkVault-Timestamp": "1000",
            "X-LinkVault-Signature": "v1=" + signature,
        }
        self.assertEqual(
            verify(body, headers, secret, now=1001)["type"], "webhook.test"
        )
        for content, when in [(body + b" ", 1001), (body, 1301), (body, 699)]:
            with self.assertRaises(ValueError):
                verify(content, headers, secret, now=when)
