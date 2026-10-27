import base64
import io
import uuid
from unittest.mock import patch

from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.http import Http404
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from vault.models import ShareLink
from vault.security import capability, verify_capability
from vault.share_urls import code_digest, short_code
from .test_capabilities import legacy_capability
from .test_security import IsolatedFiles


class ShortCodeFormatTests(SimpleTestCase):
    @override_settings(
        SECRET_KEY="capability-format-test-key-not-a-real-instance-secret-2026"
    )
    def test_frozen_128_bit_wire_format(self):
        identifier = uuid.UUID("00112233-4455-4677-8899-aabbccddeeff")
        self.assertEqual(short_code(identifier), "pCwbnHzZ6ssd-6nfDeuwJQ")
        self.assertEqual(short_code(str(identifier)), short_code(identifier))
        self.assertEqual(len(base64.urlsafe_b64decode(short_code(identifier) + "==")), 16)
        self.assertNotEqual(short_code(identifier), short_code(uuid.uuid4()))

    def test_malformed_codes_and_other_purposes_never_query_the_database(self):
        code = short_code(uuid.uuid4())
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        noncanonical = code[:-1] + alphabet[alphabet.index(code[-1]) | 1]
        self.assertEqual(
            base64.urlsafe_b64decode(code + "=="),
            base64.urlsafe_b64decode(noncanonical + "=="),
        )
        for bad in (
            noncanonical, code + "=", code + "==", code[:-1], code + "A",
            code + "\n", "+" + code[1:], "/" + code[1:], "é" + code[1:],
        ):
            with self.subTest(value=bad), self.assertRaises(Http404):
                verify_capability("share", bad)
        for purpose in ("invite", "reset", "unknown"):
            with self.subTest(purpose=purpose), self.assertRaises(Http404):
                verify_capability(purpose, code)


class ShortShareURLTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.folder = self.collection(self.account())
        self.link = ShareLink.objects.create(collection=self.folder, label="Client")

    def test_saved_and_bulk_created_links_use_short_urls_without_plaintext_tokens(self):
        bulk = ShareLink.objects.using("default").bulk_create([
            ShareLink(collection=self.folder, label="A"),
            ShareLink(id=None, collection=self.folder, label="B"),
        ])
        for link in [self.link, *bulk]:
            link.refresh_from_db()
            code = short_code(link.pk)
            self.assertEqual(link.short_code_digest, code_digest(code))
            with self.assertNumQueries(0):
                self.assertTrue(link.public_url.endswith(f"/s/{code}/"))
            with self.assertNumQueries(1):
                self.assertEqual(verify_capability("share", code), link.pk)
            self.assertNotIn(code, str(ShareLink.objects.filter(pk=link.pk).values().get()))

    def test_each_code_bit_is_authenticated_and_unknown_codes_fail_closed(self):
        original = base64.urlsafe_b64decode(short_code(self.link.pk) + "==")
        for bit in range(128):
            altered = bytearray(original)
            altered[bit // 8] ^= 1 << (bit % 8)
            code = base64.urlsafe_b64encode(altered).decode().rstrip("=")
            with self.subTest(bit=bit), self.assertRaises(Http404):
                verify_capability("share", code)
        with self.assertRaises(Http404):
            verify_capability("share", short_code(uuid.uuid4()))

    def test_corrupted_lookup_does_not_authorize_another_link(self):
        fake = short_code(uuid.uuid4())
        real = short_code(self.link.pk)
        ShareLink.objects.filter(pk=self.link.pk).update(short_code_digest=code_digest(fake))
        for code in (fake, real):
            with self.subTest(code=code), self.assertRaises(Http404):
                verify_capability("share", code)
        # A repair only changes the lookup, and does not disclose capabilities.
        output = io.StringIO()
        call_command("refresh_share_urls", stdout=output)
        self.assertNotIn(real, output.getvalue())
        self.assertEqual(verify_capability("share", real), self.link.pk)

    def test_duplicate_digest_fails_without_overwriting_an_existing_link(self):
        code = short_code(self.link.pk)
        with patch("vault.share_urls.short_code", return_value=code):
            with self.assertRaises(IntegrityError), transaction.atomic():
                ShareLink.objects.create(collection=self.folder, label="Collision")
        self.assertEqual(ShareLink.objects.count(), 1)
        self.assertEqual(verify_capability("share", code), self.link.pk)

    def test_key_rotation_invalidates_old_codes_and_refreshes_new_urls(self):
        old_codes = [short_code(self.link.pk)] + [
            encoder("share", self.link.pk) for encoder in (capability, legacy_capability)
        ]
        original = ShareLink.objects.values().get(pk=self.link.pk)
        with override_settings(SECRET_KEY="intentionally-rotated-test-instance-key-" * 3):
            for code in old_codes:
                with self.assertRaises(Http404):
                    verify_capability("share", code)
            # Copying before refresh still returns a working signed URL, with no writes.
            with self.assertNumQueries(0):
                fallback = self.link.public_url.rsplit("/", 2)[1]
            self.assertEqual(len(fallback), 43)
            self.assertEqual(verify_capability("share", fallback), self.link.pk)
            call_command("refresh_share_urls", stdout=io.StringIO())
            self.link.refresh_from_db()
            new = self.link.public_url.rsplit("/", 2)[1]
            self.assertEqual(len(new), 22)
            self.assertEqual(verify_capability("share", new), self.link.pk)
            with self.assertRaises(Http404):
                verify_capability("share", old_codes[0])
            current = ShareLink.objects.values().get(pk=self.link.pk)
            self.assertNotEqual(original.pop("short_code_digest"), current.pop("short_code_digest"))
            self.assertEqual(original, current)
            with CaptureQueriesContext(connection) as queries:
                call_command("refresh_share_urls", stdout=io.StringIO())
            self.assertFalse(any(q["sql"].lstrip().upper().startswith("UPDATE") for q in queries))

    def test_old_writer_missing_lookup_uses_working_fallback_until_refresh(self):
        ShareLink.objects.filter(pk=self.link.pk).update(short_code_digest=None)
        self.link.refresh_from_db()
        with self.assertNumQueries(0):
            token = self.link.public_url.rsplit("/", 2)[1]
        self.assertEqual(len(token), 43)
        self.assertEqual(verify_capability("share", token), self.link.pk)
        call_command("refresh_share_urls", stdout=io.StringIO())
        self.link.refresh_from_db()
        self.assertEqual(len(self.link.public_url.rsplit("/", 2)[1]), 22)


class ShortShareMigrationTests(TransactionTestCase):
    old = [("vault", "0009_apitoken_optional_expiry")]
    new = [("vault", "0010_sharelink_short_code_digest")]

    def test_existing_links_are_backfilled_in_batches_without_changing_records(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.old)
        try:
            apps = executor.loader.project_state(self.old).apps
            owner = apps.get_model("vault", "User").objects.create(
                email="migration@example.com", display_name="Migration", password="!"
            )
            folder = apps.get_model("vault", "Collection").objects.create(
                owner=owner, title="Existing collection"
            )
            links = apps.get_model("vault", "ShareLink").objects
            links.bulk_create([
                links.model(collection=folder, label=f"Client {n}", downloads=n, max_downloads=600)
                for n in range(503)
            ])
            before = list(links.order_by("pk").values())
            executor = MigrationExecutor(connection)
            executor.migrate(self.new)
            after = list(ShareLink.objects.order_by("pk").values())
            for row in after:
                digest = row.pop("short_code_digest")
                self.assertEqual(digest, code_digest(short_code(row["id"])))
                self.assertEqual(verify_capability("share", short_code(row["id"])), row["id"])
            self.assertEqual(after, before)
        finally:
            MigrationExecutor(connection).migrate(self.new)
