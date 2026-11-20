"""Real Chromium desktop/mobile regressions; run explicitly, outside the unit suite."""

import hashlib
import os
from pathlib import Path
from unittest.mock import patch

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from playwright.sync_api import sync_playwright, expect

from vault.models import Collection, Instance, ShareLink, User
from vault.security import capability
from vault.storage import add_files


@override_settings(
    ANTIVIRUS_REQUIRED=False,
    PREVIEWS_ENABLED=False,
    CLAMAV_HOST="",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
)
class BrowserTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The synchronous Playwright driver owns an event loop on this test thread.
        # ORM calls here are serial; this exemption is never enabled by application code.
        cls.async_guard = patch.dict(os.environ, {"DJANGO_ALLOW_ASYNC_UNSAFE": "true"})
        cls.async_guard.start()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.async_guard.stop()
        super().tearDownClass()

    def setUp(self):
        Instance.objects.update_or_create(pk=1, defaults={"initialized": True})
        self.user = User.objects.create_user(
            "alex@example.test",
            "Disposable-browser-password-2026!",
            display_name="Alex Morgan",
            is_staff=True,
        )
        self.context = self.browser.new_context(
            viewport={"width": 1440, "height": 1000}, locale="en-GB"
        )
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(self.live_server_url + "/login/")
        self.page.locator("[name=email]").fill(self.user.email)
        self.page.locator("[name=password]").fill("Disposable-browser-password-2026!")
        self.page.locator("form.auth-form button").click()
        self.page.wait_for_url(self.live_server_url + "/")

    def tearDown(self):
        self.context.close()
        super().tearDown()

    def test_upload_interrupt_resume_then_create_link_and_download_selection(self):
        folder = Collection.objects.create(owner=self.user, title="Project documents")
        self.page.goto(self.live_server_url + f"/collections/{folder.pk}/")
        # Two blocks: the first persists, the second fails once, then resumes at its durable offset.
        contents = b"a" * (5 * 1024**2)
        calls = []

        def interrupt(route):
            offset = int(route.request.headers.get("upload-offset", "0"))
            calls.append(offset)
            if len(calls) == 2:
                route.abort("internetdisconnected")
            else:
                route.continue_()

        self.page.route("**/chunk/", interrupt)
        self.page.locator("[data-file-input]").set_input_files(
            {"name": "brief.txt", "mimeType": "text/plain", "buffer": contents}
        )
        self.page.locator(".upload-submit").click()
        expect(self.page.locator(".upload-selection")).to_contain_text(
            "interrupted", timeout=30000
        )
        self.page.locator(".upload-submit").click()
        self.page.wait_for_url(
            self.live_server_url + f"/collections/{folder.pk}/",
            wait_until="networkidle",
            timeout=30000,
        )
        expect(self.page.locator(".file-info").first).to_contain_text("brief.txt")
        self.assertEqual(calls[:3], [0, 4 * 1024**2, 4 * 1024**2])
        self.assertEqual(
            folder.files.get().sha256, hashlib.sha256(contents).hexdigest()
        )
        self.page.goto(self.live_server_url + f"/collections/{folder.pk}/link/")
        self.page.locator("[name=label]").fill("Client A")
        self.page.locator("[name=max_downloads]").fill("3")
        self.page.locator("[name=password]").fill("x")
        self.page.locator(
            "form[data-link-form] button[type=submit],form[data-link-form] button.button"
        ).last.click()
        self.page.wait_for_url("**/links/")
        link = ShareLink.objects.get(label="Client A")
        visitor = self.browser.new_context(locale="de-DE")
        page = visitor.new_page()
        page.goto(self.live_server_url + "/s/" + link.public_url.split("/s/")[1])
        expect(page.locator("html")).to_have_attribute("lang", "de")
        page.locator("[name=password]").fill("x")
        page.locator("form[action$='/unlock/'] button").click()
        expect(page.get_by_role("checkbox")).to_have_count(1)
        page.get_by_role("checkbox").check()
        with page.expect_download() as download:
            page.locator("#selected-download button").click()
        self.assertEqual(download.value.suggested_filename, "brief.txt")
        self.assertEqual(Path(download.value.path()).read_bytes(), contents)
        visitor.close()
        self.assertEqual(self.errors, [])

    def test_individual_download_with_other_checkbox_selected_without_javascript(self):
        folder = Collection.objects.create(owner=self.user, title="Plans")
        paths = ["z.txt", "Plans/plan 10.txt", "Plans/plan 2.txt", "à lire.txt"]
        add_files(
            self.user,
            folder.pk,
            [SimpleUploadedFile("document.txt", path.encode()) for path in paths],
            paths,
        )
        self.page.goto(self.live_server_url + f"/collections/{folder.pk}/")
        self.page.locator("[data-file-input]").set_input_files(
            [
                {"name": name, "mimeType": "text/plain", "buffer": b"preview"}
                for name in ("Z.txt", "plan 10.txt", "État.txt", "Plan 2.txt")
            ]
        )
        expect(self.page.locator(".selection-file > span")).to_have_text(
            ["État.txt", "Plan 2.txt", "plan 10.txt", "Z.txt"]
        )
        link = ShareLink.objects.create(collection=folder, max_downloads=2)
        with self.browser.new_context(
            locale="fr-FR",
            java_script_enabled=False,
            viewport={"width": 360, "height": 780},
        ) as guest:
            page = guest.new_page()
            page.goto(f"{self.live_server_url}/s/{capability('share', link.pk)}/")
            expect(page.locator(".file-info strong")).to_have_text(
                ["plan 2.txt", "plan 10.txt", "à lire.txt", "z.txt"]
            )
            page.get_by_role("checkbox", name="Sélectionner z.txt", exact=True).check()
            button = page.get_by_role(
                "button", name="Télécharger Plans/plan 2.txt", exact=True
            )
            button.scroll_into_view_if_needed()
            expect(button).to_be_in_viewport()
            self.assertLessEqual(
                button.bounding_box()["x"] + button.bounding_box()["width"], 360
            )
            with page.expect_download() as single:
                button.click()
            self.assertEqual(single.value.suggested_filename, "plan 2.txt")
            self.assertEqual(
                Path(single.value.path()).read_bytes(), b"Plans/plan 2.txt"
            )
            with page.expect_download() as selection:
                page.get_by_role("button", name="Télécharger la sélection").click()
            self.assertEqual(selection.value.suggested_filename, "z.txt")
            self.assertEqual(Path(selection.value.path()).read_bytes(), b"z.txt")
        link.refresh_from_db()
        self.assertEqual(link.downloads, 2)

    def test_mobile_forms_navigation_and_accessible_labels(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        for path in (
            "/workspaces/",
            "/requests/",
            "/presets/",
            "/mail/templates/",
            "/account/sessions/",
            "/automations/explorer/",
        ):
            self.page.goto(self.live_server_url + path)
            expect(self.page.locator("h1")).to_have_count(1)
            self.assertLessEqual(
                self.page.evaluate("document.documentElement.scrollWidth"), 391, path
            )
            self.assertEqual(self.page.locator('input[type="number"]').count(), 0)
            unlabeled = self.page.locator(
                "input:not([type=hidden]):not([type=submit]),select,textarea"
            ).evaluate_all(
                "elements => elements.filter(el => !el.labels?.length && !el.getAttribute('aria-label')).map(el => el.name || el.id)"
            )
            self.assertEqual(unlabeled, [], path)
            self.assertTrue(self.page.get_by_role("navigation").count())
        self.assertEqual(self.errors, [])
