import re
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.contrib.staticfiles import finders
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone, translation
from vault.models import Audit, Invitation, OperationalAlert, ShareLink, Event
from vault.help import GUIDES
from vault.security import capability
from vault.storage import add_files
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
        self.assertContains(share, "Download files")
        self.assertContains(share, self.folder.title)
        self.assertEqual(self.link.downloads, 0)
        self.client.post("/language/", {"language": "fr", "next": "/links/"})
        self.assertContains(self.client.get("/links/"), "Mes liens")

    def test_all_workspace_and_help_pages_render_in_all_languages(self):
        pages = [
            "/",
            "/upload/",
            "/collections/",
            "/collections/new/",
            f"/collections/{self.folder.pk}/",
            f"/collections/{self.folder.pk}/link/",
            f"/collections/{self.folder.pk}/links/batch/",
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
        for language in ("fr", "en", "de"):
            self.client.cookies["django_language"] = language
            for page in pages + [f"/help/{slug}/" for slug, *_ in GUIDES]:
                with self.subTest(language=language, page=page):
                    response = self.client.get(page)
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, f'lang="{language}"')
                    self.assertEqual(response.headers["Content-Language"], language)
                    self.assertNotContains(response, 'type="number"')
                    if page.startswith("/help/"):
                        for source in re.findall(
                            r'<img[^>]+src="([^"]+)"', response.content.decode()
                        ):
                            self.assertTrue(
                                source.startswith(("/static/guide/", "/static/brand/"))
                            )
                            self.assertIsNotNone(
                                finders.find(source.removeprefix("/static/")), source
                            )
        response = self.client.get("/help/getting-started/")
        self.assertContains(
            response, "https://github.com/fmacfa/liasio/blob/main/README.md"
        )
        self.assertContains(response, "/static/guide/user-guide/de/login.png")
        self.assertContains(response, "Erste Schritte mit Liasio")
        self.assertNotContains(response, "Get started with Liasio")
        self.assertContains(response, "docs/guide/de/getting-started.md")
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

    def test_recipient_pages_negotiate_browser_language_including_regions(self):
        url = self.link.public_url
        for header, language, label in (
            ("de-DE,de;q=0.9,en;q=0.8", "de", "Dateien herunterladen"),
            ("de-AT", "de", "Dateien herunterladen"),
            ("de-CH", "de", "Dateien herunterladen"),
            ("en-GB", "en", "Download files"),
            ("fr-CA", "fr", "Télécharger les fichiers"),
            ("es-ES,de;q=0.8,en;q=0.5", "de", "Dateien herunterladen"),
            ("de;q=0.5,en;q=0.9", "en", "Download files"),
            ("es-ES", "fr", "Télécharger les fichiers"),
            ("", "fr", "Télécharger les fichiers"),
        ):
            with self.subTest(header=header):
                response = Client().get(url, HTTP_ACCEPT_LANGUAGE=header)
                self.assertContains(response, label)
                self.assertContains(response, f'lang="{language}"')
                self.assertContains(response, self.folder.title)
                self.assertEqual(response.headers["Content-Language"], language)
                self.assertIn("Accept-Language", response.headers["Vary"])
                self.assertIn("no-store", response.headers["Cache-Control"])
                self.assertNotIn("django_language", response.cookies)
        self.link.refresh_from_db()
        self.assertEqual(self.link.downloads, 0)

    def test_manual_language_takes_precedence_without_changing_link_conditions(self):
        self.link.password_hash = make_password("link-password")
        self.link.max_downloads = 1
        self.link.save()
        browser = Client()
        url = self.link.public_url
        self.assertContains(
            browser.get(url, HTTP_ACCEPT_LANGUAGE="de-CH"), "Freigabe öffnen"
        )
        changed = browser.post("/language/", {"language": "de", "next": url})
        self.assertEqual(changed.status_code, 302)
        self.assertEqual(changed.cookies["django_language"].value, "de")
        self.assertTrue(changed.cookies["django_language"]["httponly"])
        self.assertEqual(changed.cookies["django_language"]["samesite"], "Lax")
        locked = browser.get(url, HTTP_ACCEPT_LANGUAGE="en")
        self.assertContains(locked, "Geschützte Freigabe")
        self.assertNotContains(locked, "Dateien herunterladen")
        response = browser.post(
            url + "unlock/", {"password": "wrong"}, HTTP_ACCEPT_LANGUAGE="en"
        )
        self.assertContains(response, "Falsches Passwort.", status_code=403)
        self.link.refresh_from_db()
        self.assertEqual(self.link.downloads, 0)
        self.assertEqual(self.link.max_downloads, 1)
        self.assertTrue(self.link.password_hash)
        browser.post("/language/", {"language": "fr", "next": url})
        self.assertContains(
            browser.get(url, HTTP_ACCEPT_LANGUAGE="de"), "Ouvrir le partage"
        )
        self.link.expires_at = timezone.now() - timedelta(hours=1)
        self.link.save()
        unavailable = Client().get(url, HTTP_ACCEPT_LANGUAGE="de")
        self.assertContains(
            unavailable, "Dieser Link ist nicht mehr verfügbar", status_code=410
        )

    def test_german_plurals_javascript_validation_and_stored_labels(self):
        browser = Client()
        response = browser.get(self.link.public_url, HTTP_ACCEPT_LANGUAGE="de")
        self.assertContains(response, "1 Datei ·")
        self.link.delete()
        add_files(
            self.user,
            self.folder.pk,
            [SimpleUploadedFile("second.txt", b"two")],
            ["second.txt"],
        )
        self.link = ShareLink.objects.create(collection=self.folder, label="Customer")
        self.assertContains(
            browser.get(self.link.public_url, HTTP_ACCEPT_LANGUAGE="de"), "2 Dateien ·"
        )
        self.assertContains(
            browser.get("/jsi18n/", HTTP_ACCEPT_LANGUAGE="de-AT"),
            "Upload wird vorbereitet",
        )
        response = browser.post(
            "/login/", {"email": "invalid", "password": "x"}, HTTP_ACCEPT_LANGUAGE="de"
        )
        self.assertContains(response, "gültige E-Mail-Adresse")
        audit = Audit.objects.create(actor=self.user, action="member_suspended")
        alert = OperationalAlert.objects.create(
            key="quota-42",
            message="Le compte n° 42 atteint au moins 90 % de son quota (envois réservés compris).",
        )
        self.client.cookies["django_language"] = "de"
        self.assertContains(self.client.get("/team/"), "Mitglied gesperrt")
        self.assertContains(
            self.client.get("/operations/"), "Konto Nr. 42 erreicht mindestens 90 %"
        )
        audit.refresh_from_db()
        alert.refresh_from_db()
        self.assertEqual(audit.action, "member_suspended")
        self.assertTrue(alert.message.startswith("Le compte"))
        with translation.override("de"):
            self.assertEqual(self.link.status_code, "active")
            self.assertEqual(str(self.link.status), "Aktiv")
            self.assertEqual(
                Event(link=self.link, kind="opened").get_kind_display(),
                "Seite geöffnet",
            )

    @override_settings(
        EMAIL_HOST="smtp.example.invalid",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_german_invitation_and_password_reset_email(self):
        browser = Client()
        invitation = Invitation.objects.create(
            email="invite@example.com", invited_by=self.user
        )
        self.assertContains(
            browser.get(invitation.public_url, HTTP_ACCEPT_LANGUAGE="de"),
            "Mein Konto erstellen",
        )
        response = browser.post(
            "/forgot-password/", {"email": self.user.email}, HTTP_ACCEPT_LANGUAGE="de"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Prüfen Sie Ihr E-Mail-Postfach.")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Ihr Liasio-Passwort zurücksetzen")
        self.assertIn("eine Stunde lang gültig", mail.outbox[0].body)
        self.assertNotIn("réinitialisation", mail.outbox[0].body)

    def test_early_upload_rejections_use_the_browser_language(self):
        response = Client().generic(
            "POST",
            "/collections/",
            data=b"x",
            CONTENT_LENGTH=str(2 * 1024**2),
            HTTP_ACCEPT_LANGUAGE="de-CH",
        )
        self.assertContains(response, "Upload zu groß.", status_code=413)
        self.assertEqual(response.headers["Content-Language"], "de")

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
