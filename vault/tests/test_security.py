import io
import json
import tempfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import close_old_connections, connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from vault.models import (
    Asset,
    Branding,
    Collection,
    Event,
    Instance,
    Invitation,
    RateBucket,
    ShareLink,
    User,
)
from vault.security import capability, rate_allowed
from vault.storage import add_files, download_bundle, safe_path, scan

PASSWORD = "A-long-test-passphrase-2026!"
FAST_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
TEST_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


class IsolatedFiles:
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        staging = Path(self.directory.name) / "uploads"
        staging.mkdir()
        self.override = override_settings(
            UPLOAD_STAGING_ROOT=staging,
            MIN_FREE_DISK_BYTES=0,
            MEDIA_ROOT=Path(self.directory.name) / "files",
            FILE_UPLOAD_TEMP_DIR=self.directory.name,
            STORAGES=TEST_STORAGE,
            ANTIVIRUS_REQUIRED=False,
            CLAMAV_HOST="",
            PASSWORD_HASHERS=FAST_HASHERS,
            ALLOWED_HOSTS=["testserver", "127.0.0.1"],
            SECURE_SSL_REDIRECT=False,
            TRUST_PROXY=False,
            SETUP_TOKEN="test-bootstrap-token-at-least-32-characters-long",
        )
        self.override.enable()
        self.addCleanup(self.override.disable)

    def account(self, email="owner@example.com", admin=False):
        user = User.objects.create_user(
            email, PASSWORD, display_name=email.split("@")[0], is_staff=admin
        )
        Branding.objects.create(user=user, name="Test Studio")
        return user

    def collection(self, user):
        item = Collection.objects.create(
            owner=user, title="Dossier client", description="Document privé"
        )
        add_files(
            user,
            item.pk,
            [SimpleUploadedFile("note.txt", b"hello")],
            ["Dossier/note.txt"],
        )
        return item


class AccountTests(IsolatedFiles, TestCase):
    def test_first_setup_requires_server_secret_then_closes(self):
        Instance.objects.update_or_create(pk=1, defaults={"initialized": False})
        data = {
            "email": "Admin@EXAMPLE.com",
            "display_name": "Camille",
            "password": PASSWORD,
            "password_confirm": PASSWORD,
            "setup_token": "incorrect",
        }
        self.assertEqual(self.client.post("/setup/", data).status_code, 200)
        self.assertFalse(User.objects.exists())
        data["setup_token"] = "clé incorrecte"
        self.assertEqual(self.client.post("/setup/", data).status_code, 200)
        self.assertFalse(User.objects.exists())
        data["setup_token"] = settings.SETUP_TOKEN
        self.assertRedirects(self.client.post("/setup/", data), "/")
        admin = User.objects.get()
        self.assertEqual(admin.email, "admin@example.com")
        self.assertTrue(admin.is_staff)
        self.client.logout()
        self.assertRedirects(self.client.post("/setup/", data), "/login/")
        self.assertEqual(User.objects.count(), 1)

    def test_login_case_insensitive_and_logout_post_only(self):
        user = self.account()
        response = self.client.post(
            "/login/", {"email": "OWNER@EXAMPLE.COM", "password": PASSWORD}
        )
        self.assertRedirects(response, "/")
        self.assertEqual(self.client.get("/logout/").status_code, 405)
        self.assertRedirects(self.client.post("/logout/"), "/login/")
        self.assertFalse(self.client.session.get("_auth_user_id"))

    def test_login_bruteforce_is_limited(self):
        self.account()
        for _ in range(10):
            self.client.post(
                "/login/", {"email": "owner@example.com", "password": "wrong"}
            )
        response = self.client.post(
            "/login/", {"email": "owner@example.com", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 429)

    def test_invitation_is_email_bound_single_use_and_not_admin(self):
        admin = self.account(admin=True)
        invite = Invitation.objects.create(email="new@example.com", invited_by=admin)
        url = reverse("accept_invitation", args=[capability("invite", invite.pk)])
        data = {
            "email": "attacker@example.com",
            "display_name": "New member",
            "password": PASSWORD,
            "password_confirm": PASSWORD,
            "is_staff": True,
        }
        self.assertRedirects(self.client.post(url, data), "/")
        user = User.objects.get(email="new@example.com")
        self.assertFalse(user.is_staff)
        self.assertEqual(self.client.post(url, data).status_code, 410)
        self.assertFalse(User.objects.filter(email="attacker@example.com").exists())

    def test_expired_and_revoked_invites_fail(self):
        admin = self.account(admin=True)
        for extra in [
            {"expires_at": timezone.now() - timedelta(seconds=1)},
            {"revoked_at": timezone.now()},
        ]:
            invite = Invitation.objects.create(
                email="new@example.com", invited_by=admin, **extra
            )
            self.assertEqual(
                self.client.get(
                    reverse("accept_invitation", args=[capability("invite", invite.pk)])
                ).status_code,
                410,
            )

    def test_member_cannot_invite_or_read_team(self):
        member = self.account()
        self.client.force_login(member)
        self.assertEqual(self.client.get("/team/").status_code, 404)
        self.assertEqual(
            self.client.post("/team/", {"email": "new@example.com"}).status_code, 404
        )

    def test_suspend_revokes_links_and_sessions_even_after_reactivation(self):
        admin = self.account("admin@example.com", admin=True)
        member = self.account()
        item = self.collection(member)
        link = ShareLink.objects.create(collection=item, label="Share")
        member_client = Client()
        member_client.force_login(member)
        self.client.force_login(admin)
        url = reverse("member_toggle", args=[member.pk])
        self.assertRedirects(self.client.post(url), "/team/")
        link.refresh_from_db()
        self.assertIsNotNone(link.revoked_at)
        self.assertEqual(member_client.get("/").status_code, 302)
        self.client.post(url)
        self.assertEqual(member_client.get("/").status_code, 302)

    def test_password_change_invalidates_other_sessions(self):
        user = self.account()
        second = Client()
        self.client.force_login(user)
        second.force_login(user)
        data = {
            "old_password": PASSWORD,
            "new_password1": "Another-new-secure-passphrase!",
            "new_password2": "Another-new-secure-passphrase!",
        }
        self.assertRedirects(self.client.post("/account/", data), "/account/")
        self.assertEqual(second.get("/").status_code, 302)

    def test_long_password_rejected(self):
        user = self.account()
        self.client.force_login(user)
        self.client.post(
            "/account/",
            {
                "old_password": PASSWORD,
                "new_password1": "x" * 1000,
                "new_password2": "x" * 1000,
            },
        )
        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))

    @override_settings(
        EMAIL_HOST="smtp.example.com",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_password_reset_token_and_host_are_safe(self):
        from django.core import mail

        self.account()
        self.assertEqual(
            self.client.post(
                "/forgot-password/", {"email": "owner@example.com"}
            ).status_code,
            200,
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(settings.APP_URL, mail.outbox[0].body)
        self.assertNotIn("testserver", mail.outbox[0].body)
        self.assertEqual(
            self.client.post(
                "/forgot-password/", {"email": "missing@example.com"}
            ).status_code,
            200,
        )
        self.assertEqual(len(mail.outbox), 1)


class SharingTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.account()
        self.item = self.collection(self.owner)
        self.link = ShareLink.objects.create(
            collection=self.item, label="Client", max_downloads=1
        )
        self.url = reverse("share", args=[capability("share", self.link.pk)])
        self.download_url = self.url + "download/"

    def test_unique_link_page_does_not_consume_download_and_head_not_counted(self):
        self.assertEqual(self.client.head(self.url).status_code, 200)
        self.assertFalse(Event.objects.filter(kind="opened").exists())
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.get(self.url)
        self.link.refresh_from_db()
        self.assertEqual(self.link.downloads, 0)
        self.assertEqual(Event.objects.filter(kind="opened").count(), 1)

    def test_single_use_download_preserves_tree_then_expires(self):
        response = self.client.post(self.download_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/octet-stream")
        self.assertIn("attachment", response["Content-Disposition"])
        content = b"".join(response.streaming_content)
        with zipfile.ZipFile(io.BytesIO(content)) as bundle:
            self.assertEqual(bundle.read("Dossier/note.txt"), b"hello")
        self.assertEqual(self.client.post(self.download_url).status_code, 410)
        self.assertEqual(Event.objects.filter(kind="download_started").count(), 1)

    def test_get_and_head_cannot_download(self):
        self.assertEqual(self.client.get(self.download_url).status_code, 405)
        self.assertEqual(self.client.head(self.download_url).status_code, 405)
        self.link.refresh_from_db()
        self.assertEqual(self.link.downloads, 0)

    def test_password_hides_metadata_and_blocks_direct_post(self):
        self.link.password_hash = make_password(PASSWORD)
        self.link.save()
        response = self.client.get(self.url)
        self.assertNotContains(response, self.item.title)
        self.assertNotContains(response, "note.txt")
        self.assertEqual(self.client.post(self.download_url).status_code, 403)
        self.assertEqual(
            self.client.post(self.url + "unlock/", {"password": "wrong"}).status_code,
            403,
        )
        self.assertRedirects(
            self.client.post(self.url + "unlock/", {"password": PASSWORD}), self.url
        )
        response = self.client.post(self.download_url)
        self.assertEqual(response.status_code, 200)
        list(response.streaming_content)

    def test_expired_unlock_grant_fails(self):
        self.link.password_hash = make_password(PASSWORD)
        self.link.save()
        session = self.client.session
        session["grants"] = {str(self.link.pk): timezone.now().timestamp() - 1}
        session.save()
        self.assertEqual(self.client.post(self.download_url).status_code, 403)

    def test_revocation_and_expiration_checked_for_download(self):
        for changes in [
            {"revoked_at": timezone.now()},
            {"revoked_at": None, "expires_at": timezone.now() - timedelta(seconds=1)},
        ]:
            ShareLink.objects.filter(pk=self.link.pk).update(**changes)
            self.assertEqual(self.client.get(self.url).status_code, 410)
            self.assertEqual(self.client.post(self.download_url).status_code, 410)

    def test_missing_blob_does_not_consume_link(self):
        self.item.files.get().blob.delete(save=False)
        self.assertEqual(self.client.post(self.download_url).status_code, 503)
        self.link.refresh_from_db()
        self.assertEqual(self.link.downloads, 0)

    def test_invalid_capability_and_cross_purpose_token(self):
        token = capability("share", self.link.pk)
        tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
        self.assertEqual(
            self.client.get(reverse("share", args=[tampered])).status_code, 404
        )
        self.assertEqual(
            self.client.get(reverse("share", args=[token + "é"])).status_code, 404
        )
        self.assertEqual(
            self.client.get(
                reverse("share", args=[capability("invite", self.link.pk)])
            ).status_code,
            404,
        )

    def test_other_account_including_admin_cannot_access_resources(self):
        other = self.account("other@example.com", admin=True)
        self.client.force_login(other)
        for route in [
            "collection",
            "collection_edit",
            "collection_delete",
            "link_create",
        ]:
            self.assertEqual(
                self.client.get(reverse(route, args=[self.item.pk])).status_code,
                404,
                route,
            )
        self.assertEqual(
            self.client.post(reverse("link_revoke", args=[self.link.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse("collection_upload", args=[self.item.pk]),
                {"files": SimpleUploadedFile("a.txt", b"x")},
            ).status_code,
            404,
        )
        self.assertNotContains(self.client.get("/links/"), "Client")
        self.assertNotContains(self.client.get("/activity/"), self.item.title)

    def test_csrf_download_and_logout(self):
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post(self.download_url).status_code, 403)
        strict.get(self.url)
        response = strict.post(
            self.download_url, HTTP_X_CSRFTOKEN=strict.cookies["csrftoken"].value
        )
        self.assertEqual(response.status_code, 200)
        list(response.streaming_content)

    def test_xss_escaped_and_security_headers(self):
        self.item.title = '<script>alert("test")</script>'
        self.item.save()
        response = self.client.get(self.url)
        self.assertNotContains(response, '<script>alert("test")</script>')
        self.assertContains(response, "&lt;script&gt;")
        self.assertIn("script-src 'self'", response["Content-Security-Policy"])
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertEqual(response["Cache-Control"], "no-store, private")

    def test_csv_formula_injection_neutralized(self):
        self.link.label = '=HYPERLINK("evil")'
        self.link.save()
        Event.objects.create(link=self.link, kind="opened")
        self.client.force_login(self.owner)
        response = self.client.get("/activity/?export=csv")
        self.assertContains(response, "'=HYPERLINK")

    def test_real_pages_render(self):
        self.owner.is_staff = True
        self.owner.save()
        self.client.force_login(self.owner)
        urls = [
            "/",
            "/collections/",
            "/collections/new/",
            "/links/",
            "/activity/",
            "/branding/",
            "/account/",
            "/team/",
        ]
        urls += [
            reverse(route, args=[self.item.pk])
            for route in [
                "collection",
                "collection_edit",
                "collection_delete",
                "link_create",
            ]
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_delete_retains_bytes_and_quota_but_revokes_links(self):
        file_path = Path(self.item.files.get().blob.path)
        self.client.force_login(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("collection_delete", args=[self.item.pk]))
        self.assertTrue(file_path.exists())
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.used_bytes, 5)
        self.assertFalse(ShareLink.objects.filter(revoked_at__isnull=True).exists())


class UploadTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.account()
        self.item = Collection.objects.create(owner=self.owner, title="Upload")
        self.client.force_login(self.owner)

    def test_folder_upload_and_digest(self):
        response = self.client.post(
            reverse("collection_upload", args=[self.item.pk]),
            {
                "files": [
                    SimpleUploadedFile("a.txt", b"a"),
                    SimpleUploadedFile("b.txt", b"b"),
                ],
                "paths": json.dumps(["Folder/a.txt", "Folder/Sub/b.txt"]),
            },
        )
        self.assertRedirects(response, reverse("collection", args=[self.item.pk]))
        self.assertEqual(
            set(self.item.files.values_list("path", flat=True)),
            {"Folder/a.txt", "Folder/Sub/b.txt"},
        )
        self.assertEqual(len(self.item.files.first().sha256), 64)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.used_bytes, 2)

    def test_traversal_hidden_and_dangerous_extensions_rejected(self):
        for path in [
            "../secret.txt",
            "/a.txt",
            "folder/../a.txt",
            "..\\a.txt",
            "folder/.env",
            "a.html",
            "a.svg",
            "a.exe",
            "CON.txt",
            "test:a.txt",
            "f/./a.txt",
            "a\x00.txt",
            "a\u202etxt.txt",
        ]:
            with self.subTest(path=path), self.assertRaises(ValidationError):
                safe_path(path)

    def test_duplicate_paths_rejected_case_insensitively(self):
        with self.assertRaises(ValidationError):
            add_files(
                self.owner,
                self.item.pk,
                [SimpleUploadedFile("a.txt", b"x"), SimpleUploadedFile("A.txt", b"y")],
                ["a.txt", "A.txt"],
            )
        self.assertFalse(Asset.objects.exists())

    def test_quota_enforced_without_partial_files(self):
        User.objects.filter(pk=self.owner.pk).update(quota_bytes=2)
        with self.assertRaises(ValidationError):
            add_files(
                self.owner,
                self.item.pk,
                [SimpleUploadedFile("a.txt", b"large")],
                ["a.txt"],
            )
        self.assertFalse(Asset.objects.exists())

    def test_zip_file_directory_collision_rejected_across_uploads(self):
        add_files(
            self.owner,
            self.item.pk,
            [SimpleUploadedFile("a.txt", b"original")],
            ["a.txt"],
        )
        with self.assertRaises(ValidationError):
            add_files(
                self.owner,
                self.item.pk,
                [SimpleUploadedFile("b.txt", b"nested")],
                ["A.txt/b.txt"],
            )
        self.assertEqual(self.item.files.count(), 1)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.used_bytes, len(b"original"))

    @override_settings(MAX_FILE_BYTES=4)
    def test_streaming_limit_rejects_entire_upload(self):
        response = self.client.post(
            reverse("collection_upload", args=[self.item.pk]),
            {
                "files": [
                    SimpleUploadedFile("small.txt", b"a"),
                    SimpleUploadedFile("large.txt", b"too-large"),
                ]
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Asset.objects.exists())

    @override_settings(MAX_UPLOAD_BYTES=5)
    def test_aggregate_collection_limit(self):
        add_files(
            self.owner, self.item.pk, [SimpleUploadedFile("a.txt", b"1234")], ["a.txt"]
        )
        with self.assertRaises(ValidationError):
            add_files(
                self.owner,
                self.item.pk,
                [SimpleUploadedFile("b.txt", b"12")],
                ["b.txt"],
            )

    @override_settings(ANTIVIRUS_REQUIRED=True, CLAMAV_HOST="")
    def test_antivirus_unavailable_fails_closed(self):
        with self.assertRaises(ValidationError):
            add_files(
                self.owner,
                self.item.pk,
                [SimpleUploadedFile("a.txt", b"test")],
                ["a.txt"],
            )
        self.assertFalse(Asset.objects.exists())

    def test_upload_rollback_removes_written_blobs(self):
        with patch(
            "vault.storage.Asset.objects.create", side_effect=RuntimeError("db failure")
        ):
            with self.assertRaises(RuntimeError):
                add_files(
                    self.owner,
                    self.item.pk,
                    [SimpleUploadedFile("a.txt", b"test")],
                    ["a.txt"],
                )
        self.assertEqual(list(Path(settings.MEDIA_ROOT).rglob("*.*")), [])
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.used_bytes, 0)

    def test_active_link_freezes_files(self):
        ShareLink.objects.create(collection=self.item, label="Active")
        with self.assertRaises(ValidationError):
            add_files(
                self.owner,
                self.item.pk,
                [SimpleUploadedFile("a.txt", b"test")],
                ["a.txt"],
            )

    def test_no_link_for_empty_collection(self):
        response = self.client.post(
            reverse("link_create", args=[self.item.pk]), {"label": "Empty"}
        )
        self.assertContains(response, "Ajoutez au moins un fichier")
        self.assertFalse(ShareLink.objects.exists())

    def test_anonymous_upload_blocked_before_parsing(self):
        self.client.logout()
        with patch(
            "django.http.multipartparser.MultiPartParser.parse",
            side_effect=AssertionError("Must not parse"),
        ):
            self.assertEqual(
                self.client.post(
                    reverse("collection_upload", args=[self.item.pk]),
                    {"files": SimpleUploadedFile("a.txt", b"test")},
                ).status_code,
                403,
            )

    def test_body_limit_and_host_validation(self):
        response = self.client.post(
            "/login/",
            data=b"x",
            content_type="text/plain",
            CONTENT_LENGTH=str(2 * 1024 * 1024),
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            self.client.get("/", HTTP_HOST="attacker.example").status_code, 400
        )


class MaintenanceTests(IsolatedFiles, TestCase):
    def test_retention_purges_events_not_lifetime_count(self):
        user = self.account()
        item = self.collection(user)
        link = ShareLink.objects.create(collection=item, label="Link", downloads=5)
        event = Event.objects.create(link=link, kind="opened")
        Event.objects.filter(pk=event.pk).update(
            created_at=timezone.now() - timedelta(days=91)
        )
        rate_allowed("test", "ip", 2, 1)
        RateBucket.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        call_command("housekeeping", stdout=io.StringIO())
        self.assertFalse(Event.objects.exists())
        self.assertFalse(RateBucket.objects.exists())
        link.refresh_from_db()
        self.assertEqual(link.downloads, 5)

    def test_rate_limit_ignores_spoofed_forwarded_header(self):
        from vault.security import client_ip
        from django.test import RequestFactory

        request = RequestFactory().get("/", HTTP_X_REAL_IP="forged", REMOTE_ADDR="real")
        self.assertEqual(client_ip(request), "real")

    def test_production_argon2_hash(self):
        with override_settings(
            PASSWORD_HASHERS=["django.contrib.auth.hashers.Argon2PasswordHasher"]
        ):
            self.assertTrue(make_password(PASSWORD).startswith("argon2$argon2id$"))


class ConcurrencyTests(IsolatedFiles, TransactionTestCase):
    reset_sequences = True

    def test_two_simultaneous_one_use_downloads_only_one_authorized(self):
        owner = self.account()
        item = self.collection(owner)
        link = ShareLink.objects.create(collection=item, label="One", max_downloads=1)
        barrier = threading.Barrier(2)

        def attempt():
            close_old_connections()
            try:
                current = ShareLink.objects.select_related("collection").get(pk=link.pk)
                barrier.wait(timeout=10)
                try:
                    bundle = download_bundle(current)
                    bundle.close()
                    return "accepted"
                except ValidationError:
                    return "denied"
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertCountEqual(results, ["accepted", "denied"])
        link.refresh_from_db()
        self.assertEqual(link.downloads, 1)
        self.assertEqual(Event.objects.filter(kind="download_started").count(), 1)

    def test_concurrent_uploads_cannot_exceed_quota(self):
        owner = self.account()
        owner.quota_bytes = 5
        owner.save()
        item = Collection.objects.create(owner=owner, title="Concurrent")
        barrier = threading.Barrier(2)

        def attempt(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    add_files(
                        owner,
                        item.pk,
                        [SimpleUploadedFile(f"{index}.txt", b"1234")],
                        [f"{index}.txt"],
                    )
                    return "accepted"
                except ValidationError:
                    return "denied"
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertCountEqual(results, ["accepted", "denied"])
        owner.refresh_from_db()
        self.assertEqual(owner.used_bytes, 4)


class ClamProtocolTests(IsolatedFiles, TestCase):
    @override_settings(CLAMAV_HOST="clamav", ANTIVIRUS_REQUIRED=True)
    def test_clean_infected_error_and_partial_responses(self):
        for reply, accepted in [
            (b"stream: OK\x00", True),
            (b"stream: Eicar FOUND\x00", False),
            (b"stream: ERROR\x00", False),
            (b"stream: OK", False),
            (b"", False),
        ]:
            with (
                self.subTest(reply=reply),
                patch("vault.storage.socket.create_connection") as connect,
            ):
                client = connect.return_value.__enter__.return_value
                client.recv.side_effect = [reply, b""]
                file = SimpleUploadedFile("a.txt", b"test")
                if accepted:
                    self.assertTrue(scan(file))
                else:
                    with self.assertRaises(ValidationError):
                        scan(file)
                self.assertEqual(file.tell(), 0)
