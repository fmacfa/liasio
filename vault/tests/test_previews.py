import io
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from unittest import skipUnless

from PIL import Image
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from scripts.preview_runtime import MAX_SOURCE
from vault.forms import LinkForm
from vault.models import Asset, Collection, Event, ShareLink
from vault.previews import (
    CACHE_ENTRIES,
    PreviewBusy,
    PreviewUnavailable,
    cache_directory,
    cache_path,
    derivative,
    prune_cache,
    renderer_lock,
    socket_render,
)
from vault.security import capability
from vault.storage import add_files
from vault.trash import purge, trash_assets
from .test_security import IsolatedFiles, PASSWORD
from .test_capabilities import legacy_capability


def picture(format="PNG", size=(120, 80)):
    output = io.BytesIO()
    image = Image.new("RGB", size, "#395b46")
    if format == "PDF":
        # A differently colored second page makes first-page-only rendering visible.
        image.save(
            output, "PDF", save_all=True, append_images=[Image.new("RGB", size, "red")]
        )
        return output.getvalue()
    exif = Image.Exif()
    exif[270] = "Private metadata that must not escape"
    image.save(output, format, exif=exif if format != "PDF" else b"")
    return output.getvalue()


@override_settings(DEBUG=True, PREVIEW_SOCKET="", PREVIEWS_ENABLED=True)
class PreviewTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.account()
        self.folder = Collection.objects.create(owner=self.owner, title="Visuels")
        self.asset = self.add("Image.png", picture())
        self.link = ShareLink.objects.create(
            collection=self.folder, label="Client", allow_previews=True, max_downloads=1
        )
        self.token = capability("share", self.link.pk)
        self.url = reverse("file_preview", args=[self.token, self.asset.pk])

    def add(self, path, content):
        add_files(
            self.owner, self.folder.pk, [SimpleUploadedFile(path, content)], [path]
        )
        return Asset.objects.get(collection=self.folder, path=path)

    def test_image_derivative_strips_metadata_limits_pixels_and_preserves_original(
        self,
    ):
        # Use an unlinked second collection to preserve immutable active bundles.
        self.link.revoked_at = timezone.now()
        self.link.save()
        self.asset = self.add("Grand.jpg", picture("JPEG", (2200, 1200)))
        original = self.asset.blob.read()
        output = derivative(self.asset)
        with Image.open(io.BytesIO(output)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertLessEqual(max(image.size), 1600)
            self.assertFalse(image.getexif())
            self.assertNotIn("icc_profile", image.info)
        self.assertNotIn(b"Private metadata", output)
        self.asset.blob.open()
        self.assertEqual(self.asset.blob.read(), original)
        with patch(
            "vault.previews.render", side_effect=AssertionError("Cache expected")
        ):
            self.assertEqual(derivative(self.asset), output)

    def test_preview_does_not_consume_link_and_exhaustion_blocks_cached_bytes(self):
        for _ in range(2):
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "image/jpeg")
            self.assertIn("no-store", response["Cache-Control"])
            self.assertEqual(response["X-Content-Type-Options"], "nosniff")
            self.assertEqual(response["Cross-Origin-Resource-Policy"], "same-origin")
        self.link.refresh_from_db()
        self.assertEqual(self.link.downloads, 0)
        self.assertFalse(
            Event.objects.filter(link=self.link, kind="download_started").exists()
        )
        download = self.client.post(reverse("download", args=[self.token]))
        self.assertEqual(download.status_code, 200)
        b"".join(download.streaming_content)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_password_grant_is_required_for_images_as_well_as_the_page(self):
        self.link.password_hash = make_password(PASSWORD)
        self.link.save()
        with patch("vault.previews.derivative") as decoder:
            self.assertEqual(self.client.get(self.url).status_code, 404)
            decoder.assert_not_called()
        response = self.client.post(
            reverse("unlock", args=[self.token]), {"password": PASSWORD}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        session = self.client.session
        session["grants"] = {str(self.link.pk): time.time() - 1}
        session.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_legacy_preview_uses_same_grant_and_revocation_as_compact_link(self):
        legacy_url = reverse(
            "file_preview", args=[legacy_capability("share", self.link.pk), self.asset.pk]
        )
        self.link.password_hash = make_password(PASSWORD)
        self.link.save(update_fields=["password_hash"])
        self.assertEqual(self.client.get(legacy_url).status_code, 404)
        self.client.post(reverse("unlock", args=[self.token]), {"password": PASSWORD})
        self.assertEqual(self.client.get(legacy_url).status_code, 200)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        ShareLink.objects.filter(pk=self.link.pk).update(revoked_at=timezone.now())
        self.assertEqual(self.client.get(legacy_url).status_code, 404)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_revocation_expiry_suspension_trash_optout_and_scanning_block_cache(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        for field, value in (
            ("revoked_at", timezone.now()),
            ("expires_at", timezone.now() - timedelta(seconds=1)),
            ("allow_previews", False),
        ):
            old = getattr(self.link, field)
            ShareLink.objects.filter(pk=self.link.pk).update(**{field: value})
            self.assertEqual(self.client.get(self.url).status_code, 404)
            ShareLink.objects.filter(pk=self.link.pk).update(**{field: old})
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.owner.is_active = True
        self.owner.save(update_fields=["is_active"])
        with override_settings(ANTIVIRUS_REQUIRED=True):
            self.assertEqual(self.client.get(self.url).status_code, 404)
        Asset.objects.filter(pk=self.asset.pk).update(deleted_at=timezone.now())
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_asset_is_bound_to_this_collection_and_access_rechecked_after_render(self):
        other = self.collection(self.account("other@example.com")).files.first()
        self.assertEqual(
            self.client.get(
                reverse("file_preview", args=[self.token, other.pk])
            ).status_code,
            404,
        )

        def revoked(_):
            ShareLink.objects.filter(pk=self.link.pk).update(revoked_at=timezone.now())
            return b"\xff\xd8\xff"

        with patch("vault.previews.derivative", side_effect=revoked):
            self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_native_failure_is_cached_and_production_never_decodes_locally(self):
        with patch("vault.previews.render", return_value=None) as decoder:
            for _ in range(2):
                self.assertEqual(self.client.get(self.url).status_code, 422)
            self.assertEqual(decoder.call_count, 1)
        cache_path(self.asset).with_suffix(".none").unlink()
        with override_settings(DEBUG=False), patch("vault.previews.render") as decoder:
            self.assertEqual(self.client.get(self.url).status_code, 422)
            decoder.assert_not_called()

    def test_busy_renderer_does_not_affect_downloads_or_leave_a_cache_file(self):
        with renderer_lock():
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertIn("Retry-After", response)
        self.assertFalse(cache_path(self.asset).with_suffix(".jpg").exists())
        self.link.refresh_from_db()
        self.assertEqual(self.link.downloads, 0)

    def test_cache_has_size_count_and_age_bounds_and_purge_removes_derivative(self):
        root = cache_directory()
        for number in range(CACHE_ENTRIES + 2):
            (root / f"v1-{number}-test.none").touch()
        expired = root / "v1-old-test.jpg"
        expired.write_bytes(b"abc")
        os.utime(expired, (0, 0))
        with renderer_lock():
            prune_cache(room=1)
        self.assertLess(len(list(root.glob("*.none"))), CACHE_ENTRIES)
        self.assertFalse(expired.exists())
        oldest, newest = root / "v1-budget-old.jpg", root / "v1-budget-new.jpg"
        oldest.write_bytes(b"123456")
        newest.write_bytes(b"123456")
        os.utime(oldest, (time.time() - 60, time.time() - 60))
        with patch("vault.previews.CACHE_BYTES", 10), renderer_lock():
            prune_cache(room=4)
        self.assertFalse(oldest.exists())
        self.assertTrue(newest.exists())
        derivative(self.asset)
        cached = cache_path(self.asset).with_suffix(".jpg")
        self.assertTrue(cached.exists())
        trash_assets(self.owner, [self.asset.pk])
        with self.captureOnCommitCallbacks(execute=True):
            purge(self.owner, "asset", self.asset.pk)
        self.assertFalse(cached.exists())

    def test_new_links_offer_previews_existing_links_remain_opted_out_and_toggle_is_owned(
        self,
    ):
        self.assertTrue(LinkForm()["allow_previews"].value())
        old = ShareLink.objects.create(collection=self.folder, label="Ancien")
        self.assertFalse(old.allow_previews)
        url = reverse("link_previews", args=[old.pk])
        self.client.force_login(self.account("stranger@example.com"))
        self.assertEqual(self.client.post(url, {"enabled": "1"}).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {"enabled": "1"}).status_code, 302)
        old.refresh_from_db()
        self.assertTrue(old.allow_previews)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.owner)
        self.assertEqual(protected.post(url, {"enabled": "0"}).status_code, 403)

    def test_unsupported_large_and_malformed_files_fall_back_without_original_bytes(
        self,
    ):
        self.link.revoked_at = timezone.now()
        self.link.save()
        invalid = self.add("Incorrect.png", b"<html><script>alert(1)</script></html>")
        with self.assertRaises(PreviewUnavailable):
            derivative(invalid)
        with patch.object(self.asset, "size", MAX_SOURCE + 1):
            with (
                patch("vault.previews.render") as decoder,
                self.assertRaises(PreviewUnavailable),
            ):
                derivative(self.asset)
            decoder.assert_not_called()
        with override_settings(PREVIEWS_ENABLED=False):
            self.assertEqual(self.asset.preview_kind, "")


@skipUnless(sys.platform == "linux", "Production renderer requires Linux Landlock")
class RendererProtocolTests(SimpleTestCase):
    """Exercise the production Unix-socket protocol, including real PDFium."""

    def test_socket_service_renders_image_pdf_and_refuses_malformed_payload(self):
        # Short /tmp path stays within macOS's Unix socket path limit.
        with tempfile.TemporaryDirectory(prefix="lv-preview-", dir="/tmp") as directory:
            path = Path(directory) / "render.sock"
            server = subprocess.Popen(
                [
                    sys.executable,
                    str(settings.BASE_DIR / "scripts/preview_server.py"),
                    str(path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                for _ in range(100):
                    if path.exists():
                        break
                    if server.poll() is not None:
                        self.fail("Renderer exited before binding its socket")
                    time.sleep(0.02)
                self.assertTrue(path.exists())
                with override_settings(PREVIEW_SOCKET=str(path)):
                    for format, kind in (("PNG", "image"), ("PDF", "pdf")):
                        source = Path(directory) / format
                        source.write_bytes(picture(format))

                        class Blob:
                            def open(self, mode):
                                return source.open(mode)

                        class Fixture:
                            blob = Blob()
                            size = source.stat().st_size

                        result = socket_render(Fixture(), kind)
                        self.assertIsNotNone(result)
                        with Image.open(io.BytesIO(result)) as image:
                            self.assertEqual(image.format, "JPEG")
                            self.assertLessEqual(max(image.size), 1600)
                            red, green, _ = image.getpixel(
                                (image.width // 2, image.height // 2)
                            )
                            self.assertGreater(green, red)
                for malformed in (b'{"kind":"pdf","size":999999999}\n', b"[]\n"):
                    with socket.socket(socket.AF_UNIX) as client:
                        client.settimeout(2)
                        client.connect(str(path))
                        client.sendall(malformed)
                        self.assertEqual(client.recv(1), b"")
            finally:
                server.terminate()
                server.wait(timeout=10)


@skipUnless(
    os.environ.get("RUN_PREVIEW_INTEGRATION") == "1",
    "Requires deployed isolated preview service",
)
class LiveRendererTests(SimpleTestCase):
    def test_container_socket_renders_real_images_and_pdfs(self):
        with tempfile.TemporaryDirectory() as directory:
            for format, kind in (("JPEG", "image"), ("PDF", "pdf")):
                source = Path(directory) / format
                source.write_bytes(picture(format))

                class Blob:
                    def open(self, mode):
                        return source.open(mode)

                class Fixture:
                    blob = Blob()
                    size = source.stat().st_size

                result = socket_render(Fixture(), kind)
                self.assertIsNotNone(result)
                with Image.open(io.BytesIO(result)) as image:
                    self.assertEqual(image.format, "JPEG")
                    self.assertFalse(image.getexif())
