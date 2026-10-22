import io
import secrets
import shlex
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from vault.api_auth import credential_version
from vault.integrations import TokenForm
from vault.mfa import cipher
from vault.models import APIToken, Collection, Webhook, WebhookDelivery
from .test_security import IsolatedFiles, PASSWORD


class AutomationExperienceTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.user = self.account(admin=True)
        self.client.force_login(self.user)

    def create_key(self, **fields):
        return self.client.post(
            "/automations/",
            {
                "action": "token",
                "name": "Workflow",
                "scopes": ["read", "links:write"],
                "password": PASSWORD,
                **fields,
            },
        )

    def test_explicit_unlimited_key_and_legacy_duration_remain_revocable(self):
        expiring = self.create_key(days=30)
        self.assertIsNotNone(expiring.context["secret"])
        original = APIToken.objects.get()
        expiry = original.expires_at
        self.assertAlmostEqual((expiry - timezone.now()).days, 29)
        unlimited = self.create_key(validity="never", days="ignored stale value")
        raw = unlimited.context["secret"]
        key = APIToken.objects.exclude(pk=original.pk).get()
        self.assertIsNone(key.expires_at)
        self.assertNotEqual(key.digest, raw)
        original.refresh_from_db()
        self.assertEqual(original.expires_at, expiry)
        self.assertNotContains(self.client.get("/automations/"), raw)
        self.assertContains(self.client.get("/automations/"), "Sans expiration")
        headers = {"HTTP_AUTHORIZATION": "Bearer " + raw}
        self.assertEqual(
            self.client.get("/api/v1/collections/", **headers).status_code, 200
        )
        self.client.post(f"/automations/keys/{key.pk}/revoke/")
        self.assertEqual(
            self.client.get("/api/v1/collections/", **headers).status_code, 401
        )

    def test_duration_validation_and_password_still_required(self):
        base = {"name": "Test", "scopes": ["read"], "password": PASSWORD}
        for changes in (
            {},
            {"validity": "days"},
            {"days": 0},
            {"days": -1},
            {"days": 366},
            {"days": "1.5"},
            {"days": "no"},
            {"validity": "unknown"},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(TokenForm({**base, **changes}).is_valid())
        self.assertTrue(TokenForm({**base, "validity": "never"}).is_valid())
        self.assertTrue(TokenForm({**base, "days": 90}).is_valid())
        self.create_key(validity="never", password="wrong")
        self.assertFalse(APIToken.objects.exists())

    def test_nonexpiring_keys_count_toward_the_ten_key_limit(self):
        APIToken.objects.bulk_create(
            [
                APIToken(
                    owner=self.user,
                    name=f"Key {i}",
                    digest=secrets.token_hex(32),
                    credential_version=credential_version(self.user),
                    scopes=["read"],
                    expires_at=None,
                )
                for i in range(10)
            ]
        )
        response = self.create_key(validity="never")
        self.assertIsNone(response.context["secret"])
        self.assertEqual(APIToken.objects.count(), 10)
        oldest = APIToken.objects.first()
        oldest.revoked_at = timezone.now()
        oldest.save()
        self.assertIsNotNone(self.create_key(validity="never").context["secret"])
        self.assertEqual(APIToken.objects.filter(revoked_at__isnull=True).count(), 10)

    def test_unlimited_key_keeps_scope_password_and_account_boundaries(self):
        response = self.create_key(validity="never", scopes=["read"])
        raw = response.context["secret"]
        headers = {"HTTP_AUTHORIZATION": "Bearer " + raw}
        self.assertEqual(
            self.client.post("/api/v1/collections/create/", {}, **headers).status_code,
            403,
        )
        self.user.is_active = False
        self.user.save()
        self.assertEqual(
            self.client.get("/api/v1/collections/", **headers).status_code, 401
        )

        self.user.is_active = True
        self.user.set_password("Another-long-test-password-346!")
        self.user.save()
        self.assertEqual(
            self.client.get("/api/v1/collections/", **headers).status_code, 401
        )

    def test_housekeeping_keeps_unlimited_keys_and_purges_old_revocations(self):
        self.create_key(validity="never")
        active = APIToken.objects.get()
        for revoked, expired in (
            (timezone.now() - timedelta(days=31), None),
            (None, timezone.now() - timedelta(days=31)),
        ):
            APIToken.objects.create(
                owner=self.user,
                name="Old",
                digest="x" * 64,
                credential_version=credential_version(self.user),
                revoked_at=revoked,
                expires_at=expired,
            )
        call_command("housekeeping", stdout=io.StringIO())
        self.assertEqual(
            list(APIToken.objects.values_list("pk", flat=True)), [active.pk]
        )

    def test_generated_command_creates_named_batch_without_a_webhook(self):
        folder = self.collection(self.user)
        raw = self.create_key(validity="never").context["secret"]
        response = self.client.get("/automations/", {"collection": folder.pk})
        command = response.context["api_batch_command"]
        self.assertNotIn(raw, command)
        self.assertNotIn("--location", command)
        parts = shlex.split(command.replace("\\\n", ""))
        headers = [parts[i + 1] for i, part in enumerate(parts) if part == "--header"]
        operation = next(
            item.split(": ", 1)[1]
            for item in headers
            if item.startswith("Idempotency-Key:")
        )
        body = parts[parts.index("--data") + 1]
        created = self.client.post(
            f"/api/v1/collections/{folder.pk}/link-batches/",
            body,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + raw,
            HTTP_IDEMPOTENCY_KEY=operation,
        )
        self.assertEqual(created.status_code, 201, created.content)
        items = created.json()["items"]
        self.assertEqual([item["label"] for item in items], ["A", "B"])
        self.assertTrue(
            all(item["url"] and item["expires_at"] is None for item in items)
        )
        self.assertFalse(Webhook.objects.exists())

    def test_example_collection_selection_does_not_expose_other_owners_or_trash(self):
        other = self.account("other@example.com")
        foreign = Collection.objects.create(owner=other, title="Foreign confidential")
        trashed = Collection.all_objects.create(
            owner=self.user, title="Deleted collection", deleted_at=timezone.now()
        )
        for value in (str(foreign.pk), str(trashed.pk), "not-a-uuid"):
            self.assertEqual(
                self.client.get("/automations/", {"collection": value}).status_code, 404
            )
        invalid_post = self.client.post(
            "/automations/?collection=not-a-uuid",
            {
                "action": "token",
                "name": "Lost secret",
                "scopes": ["read"],
                "validity": "never",
                "password": PASSWORD,
            },
        )
        self.assertEqual(invalid_post.status_code, 404)
        self.assertFalse(APIToken.objects.exists())
        response = self.client.get("/automations/")
        self.assertIsNone(response.context["api_batch_command"])
        self.assertNotContains(response, foreign.title)
        self.assertNotContains(response, trashed.title)

    def test_receiver_url_is_owner_only_and_http_errors_are_explained(self):
        destination = "https://example.com/receiver/private-path"
        secret = "dummy-signing-secret-do-not-display"
        hook = Webhook.objects.create(
            owner=self.user,
            name="Receiver",
            hostname="example.com",
            destination=cipher().encrypt(destination.encode()).decode(),
            secret=cipher().encrypt(secret.encode()).decode(),
            events=["link.created"],
        )
        WebhookDelivery.objects.create(
            webhook=hook,
            kind="webhook.test",
            payload={},
            state="failed",
            http_status=404,
        )
        with patch("vault.webhooks.send") as send:
            response = self.client.get(f"/automations/webhooks/{hook.pk}/")
            send.assert_not_called()
        self.assertContains(response, destination)
        self.assertContains(response, "Adresse introuvable")
        self.assertNotContains(response, secret)
        self.assertEqual(response["Cache-Control"], "no-store, private")
        self.client.force_login(self.account("other@example.com"))
        self.assertEqual(
            self.client.get(f"/automations/webhooks/{hook.pk}/").status_code, 404
        )

    def test_quota_accepts_comma_and_period_but_still_validates_range(self):
        for value in ("2,50", "2.50"):
            self.client.post(
                f"/operations/members/{self.user.pk}/quota/", {"quota_gib": value}
            )
            self.user.refresh_from_db()
            self.assertEqual(self.user.quota_bytes, int(2.5 * 1024**3))
        for invalid in ("-1", "1048577", "NaN", "Infinity", "1,2,3"):
            self.client.post(
                f"/operations/members/{self.user.pk}/quota/", {"quota_gib": invalid}
            )
            self.user.refresh_from_db()
            self.assertEqual(self.user.quota_bytes, int(2.5 * 1024**3))
