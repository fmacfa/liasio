import hashlib
import json
import struct
import zipfile
import zlib
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone

from vault.brand_media import SIGNATURE, canonical_png, chunk, contrast_color
from vault.file_tree import file_tree
from vault.forms import AccountForm, LinkForm
from vault.models import Branding, Collection, ShareLink
from vault.security import capability
from vault.storage import add_files, download_bundle
from .test_security import IsolatedFiles
from . import test_automation


def png(width=2, height=2, metadata=False, pixels=None):
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = (
        pixels if pixels is not None else (b"\0" + b"\x20\x80\x60\xff" * width) * height
    )
    return (
        SIGNATURE
        + chunk(b"IHDR", header)
        + (chunk(b"tEXt", b"Private metadata\0not retained") if metadata else b"")
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class LinkConditionsTests(SimpleTestCase):
    def test_days_are_resolved_from_creation_and_date_is_timezone_aware(self):
        now = timezone.now()
        with patch("vault.forms.timezone.now", return_value=now):
            form = LinkForm(
                {"label": "Delivery", "expiry_mode": "days", "expires_in_days": 7}
            )
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data["expires_at"], now + timedelta(days=7))
        form = LinkForm(
            {
                "label": "Delivery",
                "expiry_mode": "date",
                "expires_at": (now + timedelta(days=2)).isoformat(),
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(timezone.is_aware(form.cleaned_data["expires_at"]))

    def test_expiration_validation_and_legacy_clients(self):
        for data in (
            {"expiry_mode": "date"},
            {"expiry_mode": "days"},
            {"expiry_mode": "days", "expires_in_days": "0"},
            {"expires_in_days": "3651"},
            {"expires_in_days": "1.5"},
            {"expires_at": (timezone.now() - timedelta(hours=1)).isoformat()},
        ):
            with self.subTest(data=data):
                self.assertFalse(LinkForm({"label": "X", **data}).is_valid())
        form = LinkForm(
            {
                "label": "X",
                "expires_at": (timezone.now() + timedelta(days=2)).isoformat(),
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNotNone(form.cleaned_data["expires_at"])
        self.assertTrue(LinkForm({"label": "X"}).is_valid())

    def test_short_link_password_keeps_account_rules_and_numeric_limits(self):
        for password in ("", "a", "123", " padded "):
            form = LinkForm({"label": "X", "password": password})
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data["password"], password)
        self.assertFalse(LinkForm({"label": "X", "password": "x" * 129}).is_valid())
        for maximum in ("0", "-1", "100001", "1.2", "1e3"):
            self.assertFalse(
                LinkForm({"label": "X", "max_downloads": maximum}).is_valid()
            )
        self.assertEqual(LinkForm().fields["max_downloads"].widget.input_type, "text")
        self.assertFalse(
            AccountForm(
                {"display_name": "Camille", "password": "a", "password_confirm": "a"}
            ).is_valid()
        )


class ShareExperienceTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.user = self.account()
        self.client.force_login(self.user)
        self.collection = Collection.objects.create(
            owner=self.user, title="Mixed files"
        )

    def test_mixed_batches_keep_structure_order_and_zip_paths(self):
        for paths in (
            ["Z-note.txt"],
            [
                "Project/Plans/Level-2.txt",
                "Project/Plans/Level-1.txt",
                "Project/Readme.txt",
            ],
        ):
            add_files(
                self.user,
                self.collection.pk,
                [SimpleUploadedFile("file.txt", b"test") for _ in paths],
                paths,
            )
        assets = list(self.collection.files.all())
        tree = file_tree(assets)
        self.assertEqual([n["name"] for n in tree], ["Z-note.txt", "Project"])
        self.assertEqual(
            [n["name"] for n in tree[1]["children"]], ["Plans", "Readme.txt"]
        )
        self.assertEqual(tree[1]["count"], 3)
        self.assertEqual(tree[1]["size"], 12)
        link = ShareLink.objects.create(collection=self.collection, label="Delivery")
        with download_bundle(link) as archive:
            with zipfile.ZipFile(archive) as bundle:
                self.assertEqual(bundle.namelist(), [asset.path for asset in assets])
        for url in (
            f"/collections/{self.collection.pk}/",
            f"/s/{capability('share', link.pk)}/",
        ):
            response = self.client.get(url)
            self.assertContains(response, 'class="file-folder" open')
            self.assertContains(response, "Level-2.txt")

    def test_resumable_upload_preserves_manifest_order_and_durable_offsets(self):
        paths = ["Z-root.txt", "Folder/B.txt", "Folder/Nested/A.txt"]
        manifest = [
            {"path": name, "size": 4, "fingerprint": "a" * 64} for name in paths
        ]
        response = self.client.post(
            f"/collections/{self.collection.pk}/uploads/start/",
            json.dumps({"files": manifest}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        session = response.json()
        self.assertEqual([part["path"] for part in session["parts"]], paths)
        for part in session["parts"]:
            url = f"/uploads/{session['id']}/parts/{part['id']}/chunk/"
            headers = {
                "HTTP_UPLOAD_OFFSET": "0",
                "HTTP_UPLOAD_CHECKSUM": hashlib.sha256(b"test").hexdigest(),
            }
            first = self.client.post(
                url, b"test", content_type="application/octet-stream", **headers
            )
            self.assertEqual(first.json()["offset"], 4)
            retried = self.client.post(
                url, b"test", content_type="application/octet-stream", **headers
            )
            self.assertEqual(retried.status_code, 409)
            self.assertEqual(retried.json()["offset"], 4)
        self.assertEqual(
            self.client.post(
                f"/uploads/{session['id']}/finish/",
                "{}",
                content_type="application/json",
            ).status_code,
            200,
        )
        self.assertEqual(
            list(self.collection.files.values_list("path", flat=True)), paths
        )

    def test_browser_keeps_seven_day_default(self):
        form = self.client.get(f"/collections/{self.collection.pk}/link/").context[
            "form"
        ]
        self.assertEqual(form["expiry_mode"].value(), "days")
        self.assertEqual(form["expires_in_days"].value(), 7)

    def test_short_password_unlock_is_exact_and_never_plaintext(self):
        add_files(
            self.user,
            self.collection.pk,
            [SimpleUploadedFile("file.txt", b"test")],
            ["file.txt"],
        )
        response = self.client.post(
            f"/collections/{self.collection.pk}/link/",
            {
                "label": "Short",
                "password": " a ",
                "expiry_mode": "days",
                "expires_in_days": 2,
            },
        )
        self.assertEqual(response.status_code, 302)
        link = ShareLink.objects.get()
        self.assertNotEqual(link.password_hash, " a ")
        self.assertTrue(check_password(" a ", link.password_hash))
        guest = Client()
        url = f"/s/{capability('share', link.pk)}/"
        self.assertNotContains(guest.get(url), "file.txt")
        self.assertEqual(
            guest.post(url + "unlock/", {"password": "a"}).status_code, 403
        )
        self.assertEqual(
            guest.post(url + "unlock/", {"password": " a "}).status_code, 302
        )
        self.assertContains(guest.get(url), "file.txt")


class BrandingImageTests(SimpleTestCase):
    def test_canonical_png_removes_metadata_and_preserves_pixels(self):
        result = canonical_png(png(metadata=True), "logo")
        self.assertNotIn(b"Private metadata", result)
        self.assertEqual(result, canonical_png(result, "logo"))
        self.assertEqual(contrast_color("#ffffff"), "#000000")
        self.assertEqual(contrast_color("#000000"), "#ffffff")

    def test_rejects_active_content_corruption_trailing_data_and_bombs(self):
        valid = png()
        inputs = [
            b"<svg onload='alert(1)'/>",
            valid + b"<script/>",
            valid[:-3],
            valid[:20] + b"X" + valid[21:],
            png(513, 1),
            png(pixels=b"\0" * 100000),
            png(pixels=b"\x05" + b"\0" * 8 + b"\0" * 9),
            b"x" * (512 * 1024 + 1),
        ]
        for data in inputs:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                canonical_png(data, "logo")


class BrandingAccessTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.user = self.account()
        self.collection = self.collection(self.user)
        self.client.force_login(self.user)
        self.data = {
            "name": "Studio Demo",
            "theme": "ocean",
            "accent_color": "#eecb50",
            "background_color": "#e4ecfa",
            "tagline": "A new identity",
        }

    def upload(self, value=None):
        return self.client.post(
            "/branding/",
            {
                **self.data,
                "logo": SimpleUploadedFile(
                    "logo.png",
                    png(metadata=True) if value is None else value,
                    content_type="image/png",
                ),
            },
        )

    def test_upload_color_removal_and_separate_bounded_storage(self):
        self.assertEqual(self.upload().status_code, 302)
        brand = Branding.objects.get(user=self.user)
        self.assertGreater(brand.image_bytes, 0)
        self.assertNotIn(b"Private metadata", bytes(brand.logo_data))
        self.assertEqual(
            self.client.get("/branding/media/logo/")["Content-Type"], "image/png"
        )
        css = self.client.get("/branding/media/style/")
        self.assertContains(css, "--accent:#eecb50")
        self.assertContains(css, "--accent-contrast:#000000")
        self.assertContains(self.client.get("/storage/"), "Gérer les images")
        self.assertEqual(
            self.client.post(
                "/branding/", {**self.data, "remove_logo": "on"}
            ).status_code,
            302,
        )
        self.assertEqual(self.client.get("/branding/media/logo/").status_code, 404)

    def test_invalid_images_css_and_antivirus_failure_leave_previous_brand_intact(self):
        self.upload()
        previous = bytes(Branding.objects.get(user=self.user).logo_data)
        self.assertContains(self.upload(b"<svg/>"), "Image invalide")
        response = self.client.post(
            "/branding/", {**self.data, "accent_color": "red;url(x)"}
        )
        self.assertIn("accent_color", response.context["form"].errors)
        with patch(
            "vault.storage.scan", side_effect=ValidationError("Rejected by scanner")
        ):
            self.assertContains(self.upload(), "Rejected by scanner")
        brand = Branding.objects.get(user=self.user)
        self.assertEqual(bytes(brand.logo_data), previous)
        self.assertEqual(brand.accent_color, self.data["accent_color"])

    def test_media_obeys_password_expiration_revocation_consumption_and_owner(self):
        self.upload()
        link = ShareLink.objects.create(
            collection=self.collection,
            label="Protected",
            password_hash=make_password("x"),
            max_downloads=1,
        )
        url = f"/s/{capability('share', link.pk)}/"
        guest = Client()
        for kind in ("logo", "style"):
            self.assertEqual(guest.get(url + f"brand/{kind}/").status_code, 404)
        self.assertEqual(
            guest.post(url + "unlock/", {"password": "x"}).status_code, 302
        )
        self.assertEqual(guest.get(url + "brand/logo/").status_code, 200)
        for changes in (
            {"revoked_at": timezone.now()},
            {"downloads": 1},
            {"expires_at": timezone.now() - timedelta(seconds=1)},
        ):
            ShareLink.objects.filter(pk=link.pk).update(
                revoked_at=None, downloads=0, expires_at=None
            )
            ShareLink.objects.filter(pk=link.pk).update(**changes)
            self.assertEqual(guest.get(url + "brand/logo/").status_code, 404)
        ShareLink.objects.filter(pk=link.pk).update(expires_at=None)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(guest.get(url + "brand/logo/").status_code, 404)
        other = self.account("other@example.com")
        self.client.force_login(other)
        self.assertEqual(self.client.get("/branding/media/logo/").status_code, 404)

    def test_auth_csrf_request_size_and_image_size_limits(self):
        guest = Client(enforce_csrf_checks=True)
        self.assertEqual(guest.post("/branding/", self.data).status_code, 403)
        guest.force_login(self.user)
        self.assertEqual(guest.post("/branding/", self.data).status_code, 403)
        self.assertEqual(
            self.client.post(
                "/branding/",
                b"x" * (3 * 1024**2 + 1),
                content_type="application/octet-stream",
            ).status_code,
            413,
        )
        self.assertContains(self.upload(b"x" * (512 * 1024 + 1)), "Image invalide")


class APIConditionsTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.user = self.account()
        self.key, self.raw = self.key_for(self.user)

    key_for = test_automation.AutomationTests.key_for
    post = test_automation.AutomationTests.post

    def test_relative_expiry_and_short_password_replay_without_extending_expiry(self):
        collection = self.collection(self.user)
        path = f"collections/{collection.pk}/links/"
        response = self.post(
            path, {"label": "API", "expires_in_days": 3, "password": "x"}
        )
        self.assertEqual(response.status_code, 201, response.content)
        link = ShareLink.objects.get()
        self.assertTrue(check_password("x", link.password_hash))
        self.assertAlmostEqual(
            (link.expires_at - timezone.now()).total_seconds(), 3 * 86400, delta=10
        )
        self.assertEqual(
            self.post(
                path, {"label": "API", "expires_in_days": 3, "password": "x"}
            ).json(),
            response.json(),
        )
        for i, value in enumerate((True, 1.5, "3", -1, 0, 3651)):
            self.assertEqual(
                self.post(
                    path,
                    {"label": "Bad", "expires_in_days": value},
                    key=f"invalid-days-{i:03}",
                ).status_code,
                400,
            )
        self.assertEqual(
            self.post(
                path,
                {
                    "label": "Both",
                    "expires_in_days": 1,
                    "expires_at": (timezone.now() + timedelta(days=2)).isoformat(),
                },
                key="both-expirations",
            ).status_code,
            400,
        )
