"""Compact capabilities and compatibility with links already distributed."""

import base64
import hashlib
import hmac
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.http import Http404
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from vault.models import Event, Invitation, ShareLink, User
from vault.security import capability, verify_capability
from .test_security import IsolatedFiles, PASSWORD


def legacy_capability(purpose, object_id):
    """Frozen pre-upgrade wire format; deliberately independent of the codec."""
    identifier = uuid.UUID(str(object_id)).hex
    signature = hmac.new(
        settings.SECRET_KEY.encode(),
        f"linkvault:{purpose}:{identifier}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{identifier}.{signature}"


class CapabilityCodecTests(SimpleTestCase):
    identifier = uuid.UUID("00112233-4455-4677-8899-aabbccddeeff")

    @override_settings(
        SECRET_KEY="capability-format-test-key-not-a-real-instance-secret-2026"
    )
    def test_fixed_wire_vectors_survive_future_refactoring(self):
        vectors = {
            "share": (
                "ABEiM0RVRneImaq7zN3u_2kZg-H8pxLEDI60k9JO1Ks",
                "00112233445546778899aabbccddeeff.2ad0d872022dd8179e825ec05d0b8a8b7f787b022418bd9b1aa29f488b794e67",
            ),
            "invite": (
                "ABEiM0RVRneImaq7zN3u_xkqaS1XNOcWl5cVbQR2cAw",
                "00112233445546778899aabbccddeeff.ff63bca58537f5f974445d3b2d91cd2178342f78a4e571ac0f6ddd8bb737a26f",
            ),
        }
        for purpose, (compact, legacy) in vectors.items():
            self.assertEqual(capability(purpose, self.identifier), compact)
            self.assertEqual(verify_capability(purpose, compact), self.identifier)
            self.assertEqual(verify_capability(purpose, legacy), self.identifier)

    def test_compact_tokens_are_stable_url_safe_and_preserve_the_uuid(self):
        token = capability("share", self.identifier)
        self.assertRegex(token, r"\A[A-Za-z0-9_-]{43}\Z")
        self.assertEqual(token, capability("share", str(self.identifier)))
        self.assertEqual(verify_capability("share", token), self.identifier)
        self.assertNotEqual(token, capability("share", uuid.uuid4()))
        self.assertEqual(len(base64.urlsafe_b64decode(token + "=")), 32)

    def test_legacy_formats_and_cross_purpose_separation(self):
        for encoder in (capability, legacy_capability):
            for purpose, other in (("share", "invite"), ("invite", "share")):
                with self.subTest(encoder=encoder.__name__, purpose=purpose):
                    token = encoder(purpose, self.identifier)
                    self.assertEqual(verify_capability(purpose, token), self.identifier)
                    with self.assertRaises(Http404):
                        verify_capability(other, token)

    def test_every_identifier_or_signature_bit_is_authenticated(self):
        original = base64.urlsafe_b64decode(capability("share", self.identifier) + "=")
        for bit in range(256):
            altered = bytearray(original)
            altered[bit // 8] ^= 1 << (bit % 8)
            token = base64.urlsafe_b64encode(altered).decode().rstrip("=")
            with self.subTest(bit=bit), self.assertRaises(Http404):
                verify_capability("share", token)
        legacy = legacy_capability("share", self.identifier)
        for position in (0, 31, 33, 96):
            altered = (
                legacy[:position]
                + ("0" if legacy[position] != "0" else "1")
                + legacy[position + 1:]
            )
            with self.subTest(legacy_position=position), self.assertRaises(Http404):
                verify_capability("share", altered)

    def test_noncanonical_encodings_and_malformed_values_fail_closed(self):
        token = capability("share", self.identifier)
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        noncanonical = token[:-1] + alphabet[alphabet.index(token[-1]) | 1]
        self.assertEqual(
            base64.urlsafe_b64decode(noncanonical + "="),
            base64.urlsafe_b64decode(token + "="),
        )
        legacy = legacy_capability("share", self.identifier)
        for bad in (
            noncanonical, token + "=", token[:-1], token + "A", token + "\n",
            "+" + token[1:], "/" + token[1:], "é" + token[1:],
            self.identifier.hex, str(self.identifier), legacy.upper(), legacy + "\n",
            "A" * 10000, "", None, 0, {}, token.encode(),
        ):
            with self.subTest(value=repr(bad)[:80]), self.assertRaises(Http404):
                verify_capability("share", bad)

    def test_secret_rotation_invalidates_both_formats_and_new_urls_work(self):
        tokens = [
            encoder("share", self.identifier)
            for encoder in (capability, legacy_capability)
        ]
        with override_settings(SECRET_KEY="changed-test-instance-secret-" * 3):
            for token in tokens:
                with self.assertRaises(Http404):
                    verify_capability("share", token)
            self.assertEqual(
                verify_capability("share", capability("share", self.identifier)),
                self.identifier,
            )


class CapabilityCompatibilityTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.account(admin=True)
        self.folder = self.collection(self.owner)
        self.link = ShareLink.objects.create(
            collection=self.folder, label="Client", max_downloads=1
        )
        self.short = reverse("share", args=[capability("share", self.link.pk)])
        self.legacy = reverse("share", args=[legacy_capability("share", self.link.pk)])

    def test_both_urls_share_open_tracking_and_one_download_allowance(self):
        self.assertEqual(self.client.get(self.legacy).status_code, 200)
        self.assertEqual(self.client.get(self.short).status_code, 200)
        self.assertEqual(Event.objects.filter(link=self.link, kind="opened").count(), 1)
        response = self.client.post(self.legacy + "download/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b"".join(response.streaming_content))
        self.assertEqual(self.client.post(self.short + "download/").status_code, 410)
        self.assertEqual(self.client.get(self.legacy).status_code, 410)
        self.assertEqual(
            Event.objects.filter(link=self.link, kind="download_started").count(), 1
        )
        self.link.refresh_from_db()
        self.assertEqual(self.link.downloads, 1)

    def test_password_grants_are_shared_without_bypassing_protection(self):
        self.link.password_hash = make_password(PASSWORD)
        self.link.save(update_fields=["password_hash"])
        for unlock_url, other_url in ((self.short, self.legacy), (self.legacy, self.short)):
            with self.subTest(unlock_url=unlock_url):
                client = Client()
                for url in (unlock_url, other_url):
                    self.assertNotContains(client.get(url), self.folder.title)
                    self.assertEqual(client.post(url + "download/").status_code, 403)
                self.assertRedirects(
                    client.post(unlock_url + "unlock/", {"password": PASSWORD}),
                    unlock_url,
                )
                self.assertContains(client.get(other_url), self.folder.title)

    def test_revocation_expiration_and_suspension_apply_to_both_urls(self):
        for changes in (
            {"revoked_at": timezone.now()},
            {"revoked_at": None, "expires_at": timezone.now() - timedelta(seconds=1)},
        ):
            ShareLink.objects.filter(pk=self.link.pk).update(**changes)
            for url in (self.short, self.legacy):
                for suffix in ("", "download/", "unlock/"):
                    response = (
                        self.client.post(url + suffix)
                        if suffix else self.client.get(url)
                    )
                    self.assertEqual(response.status_code, 410)
        ShareLink.objects.filter(pk=self.link.pk).update(revoked_at=None, expires_at=None)
        User.objects.filter(pk=self.owner.pk).update(is_active=False)
        for url in (self.short, self.legacy):
            self.assertEqual(self.client.get(url).status_code, 410)
            self.assertEqual(self.client.post(url + "download/").status_code, 410)

    def test_branding_requires_the_same_grant_and_active_link_for_both_formats(self):
        self.link.password_hash = make_password(PASSWORD)
        self.link.save(update_fields=["password_hash"])
        for url in (self.short, self.legacy):
            self.assertEqual(self.client.get(url + "brand/style/").status_code, 404)
        self.client.post(self.short + "unlock/", {"password": PASSWORD})
        for url in (self.short, self.legacy):
            self.assertEqual(self.client.get(url + "brand/style/").status_code, 200)
        ShareLink.objects.filter(pk=self.link.pk).update(revoked_at=timezone.now())
        for url in (self.short, self.legacy):
            self.assertEqual(self.client.get(url + "brand/style/").status_code, 404)

    def test_invitation_remains_single_use_across_both_formats(self):
        for encoder in (capability, legacy_capability):
            invite = Invitation.objects.create(
                email=f"{encoder.__name__}@example.com", invited_by=self.owner
            )
            token = encoder("invite", invite.pk)
            url = reverse("accept_invitation", args=[token])
            response = Client().post(url, {
                "display_name": "Invited",
                "password": PASSWORD,
                "password_confirm": PASSWORD,
            })
            self.assertEqual(response.status_code, 302)
            self.assertTrue(
                User.objects.filter(email=invite.email, is_staff=False).exists()
            )
            for alternate in (capability, legacy_capability):
                reused = reverse(
                    "accept_invitation", args=[alternate("invite", invite.pk)]
                )
                self.assertEqual(Client().get(reused).status_code, 410)
