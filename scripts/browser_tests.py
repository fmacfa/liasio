"""Real Chromium desktop/mobile regressions; run explicitly, outside the unit suite."""

import hashlib
import os
from pathlib import Path
from unittest.mock import patch

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import sync_playwright, expect

from vault.models import Collection, Instance, ShareLink, User


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
        expect(page.locator("[name=assets]")).to_have_count(1)
        page.locator("[name=assets]").check()
        with page.expect_download() as download:
            page.locator("#selected-download button").click()
        self.assertEqual(download.value.suggested_filename, "brief.txt")
        self.assertEqual(Path(download.value.path()).read_bytes(), contents)
        visitor.close()
        self.assertEqual(self.errors, [])

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
