from django.test import TestCase, Client
from django.utils import translation
from vault.models import Collection, ShareLink, Event
from vault.help import GUIDES
from vault.security import capability
from .test_security import IsolatedFiles


class LanguageTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.user = self.account(admin=True)
        self.folder = self.collection(self.user)
        self.link = ShareLink.objects.create(collection=self.folder, label="Customer")
        self.client.force_login(self.user)

    def test_language_selection_is_persistent_safe_and_does_not_translate_state(self):
        self.assertContains(self.client.get("/links/"), "Mes liens")
        selected = self.client.post(
            "/language/", {"language": "en", "next": "https://attacker.invalid/"}
        )
        self.assertEqual(selected.status_code, 302)
        self.assertNotIn("attacker.invalid", selected.url)
        response = self.client.get("/links/")
        self.assertContains(response, "My links")
        self.assertContains(response, "Revoke")
        self.assertContains(response, 'lang="en"')
        with translation.override("en"):
            self.assertEqual(self.link.status_code, "active")
            self.assertEqual(str(self.link.status), "Active")
            self.assertEqual(
                Event(link=self.link, kind="opened").get_kind_display(), "Page opened"
            )
        share = self.client.get(f"/s/{capability('share', self.link.pk)}/")
        self.assertContains(share, "Download collection")
        self.assertContains(share, self.folder.title)
        self.assertEqual(self.link.downloads, 0)
        self.client.post("/language/", {"language": "fr", "next": "/links/"})
        self.assertContains(self.client.get("/links/"), "Mes liens")

    def test_all_workspace_and_help_pages_render_in_both_languages(self):
        pages = [
            "/",
            "/upload/",
            "/collections/",
            "/collections/new/",
            f"/collections/{self.folder.pk}/",
            f"/collections/{self.folder.pk}/link/",
            "/links/",
            "/activity/",
            "/branding/",
            "/storage/",
            "/trash/",
            "/team/",
            "/account/",
            "/account/mfa/",
            "/operations/",
            "/automations/",
            "/help/",
        ]
        for language in ("fr", "en"):
            self.client.cookies["django_language"] = language
            for page in pages + [f"/help/{slug}/" for slug, *_ in GUIDES]:
                with self.subTest(language=language, page=page):
                    response = self.client.get(page)
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, f'lang="{language}"')
                    if page.startswith("/help/"):
                        for source in re.findall(r'<img[^>]+src="([^"]+)"', response.content.decode()):
                            self.assertTrue(source.startswith(("/static/guide/", "/static/brand/")))
                            self.assertIsNotNone(finders.find(source.removeprefix("/static/")), source)
        response = self.client.get("/help/getting-started/")
        self.assertContains(
            response, "https://github.com/fmacfa/liasio/blob/main/README.md"
        )
        self.assertContains(response, "/static/guide/user-guide/en/login.png")
        self.assertContains(
            self.client.get("/help/automation/"),
            "https://github.com/fmacfa/liasio/blob/main/docs/API.md",
        )

    def test_public_help_and_javascript_catalog(self):
        browser = Client()
        self.assertContains(
            browser.get("/help/", HTTP_ACCEPT_LANGUAGE="en"), "Find your way around"
        )
        self.assertContains(
            browser.get("/help/", HTTP_ACCEPT_LANGUAGE="fr"), "Prenez vos repères"
        )
        self.assertContains(
            browser.get("/jsi18n/", HTTP_ACCEPT_LANGUAGE="en"), "Preparing upload"
        )
        self.assertContains(
            browser.get("/login/", HTTP_ACCEPT_LANGUAGE="en"), "Sign in"
        )
        self.assertContains(
            browser.get("/login/", HTTP_ACCEPT_LANGUAGE="fr"), "Se connecter"
        )
        self.assertEqual(browser.get("/help/not-a-guide/").status_code, 404)

    def test_automation_form_identifiers_are_unique(self):
        from html.parser import HTMLParser

        class Ids(HTMLParser):
            def __init__(self):
                super().__init__()
                self.values = []

            def handle_starttag(self, tag, attrs):
                self.values.extend(v for k, v in attrs if k == "id")

        parser = Ids()
        parser.feed(self.client.get("/automations/").content.decode())
        self.assertEqual(len(parser.values), len(set(parser.values)))
import re

from django.contrib.staticfiles import finders
