"""Capture guide images from a disposable test database, never a live instance.

Run: python manage.py test scripts.guide_screenshots.GuideScreenshots.test_capture
Uses Playwright/Chromium from the development dependencies. No mail is sent.
"""

from datetime import timedelta
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from playwright.sync_api import expect

from scripts.browser_tests import BrowserTests
from vault.mail import enqueue
from vault.models import (
    Collection,
    FileRequest,
    GroupAccess,
    MailBatch,
    ShareLink,
    SharingPreset,
    TeamGroup,
    User,
    Workspace,
    WorkspaceMember,
)
from vault.storage import add_files
from vault.versions import snapshot


class GuideScreenshots(BrowserTests):
    @override_settings(EMAIL_HOST="smtp.example.test")
    def test_capture(self):
        colleague = User.objects.create_user(
            "camille@example.test", display_name="Camille Martin"
        )
        space = Workspace.objects.create(owner=self.user, name="Atelier Nova")
        for user in (self.user, colleague):
            WorkspaceMember.objects.create(workspace=space, user=user)
        group = TeamGroup.objects.create(workspace=space, name="Design")
        group.members.add(colleague)
        folder = Collection.objects.create(
            owner=self.user, workspace=space, title="Project Nova"
        )
        add_files(
            self.user,
            folder.pk,
            [SimpleUploadedFile("brief.txt", b"Fictional demonstration document.\n")],
            ["Documents/brief.txt"],
        )
        GroupAccess.objects.create(group=group, collection=folder, role="collaborator")
        revision = snapshot(folder, self.user, "01")
        link = ShareLink.objects.create(
            collection=folder,
            label="Client A",
            revision=revision,
            max_downloads=3,
            expires_at=timezone.now() + timedelta(days=30),
        )
        add_files(
            self.user,
            folder.pk,
            [
                SimpleUploadedFile(
                    "brief.txt", b"Updated fictional demonstration document.\n"
                )
            ],
            ["Documents/brief.txt"],
            replace_files=True,
        )
        snapshot(folder, self.user, "02")
        SharingPreset.objects.create(
            owner=self.user, name="Clients", expires_in_days=30, max_downloads=3
        )
        request = FileRequest.objects.create(
            owner=self.user,
            title="Project Nova",
            max_bytes=100 * 1024**2,
            max_submissions=5,
            expires_at=timezone.now() + timedelta(days=7),
        )
        batch = MailBatch.objects.create(owner=self.user)
        routes = {
            "workspaces": f"/workspaces/{space.pk}/",
            "versions": f"/collections/{folder.pk}/versions/",
            "file-requests": f"/requests/{request.pk}/",
            "file-request-upload": "/file-requests/"
            + request.public_url.split("/file-requests/")[1],
            "advanced-operations": "/operations/",
            "api-explorer": "/automations/explorer/",
            "sessions": "/account/sessions/",
            "presets": "/presets/",
            "help": "/help/",
        }
        content = {
            "en": ("Your documents", "Hello Camille,\n\nHere is your personal link:\n"),
            "fr": (
                "Vos documents",
                "Bonjour Camille,\n\nVoici votre lien personnel :\n",
            ),
            "de": (
                "Ihre Dokumente",
                "Guten Tag Camille,\n\nhier ist Ihr persönlicher Link:\n",
            ),
        }
        for language in ("en", "fr", "de"):
            for row in batch.deliveries.all():
                row.delete()
            subject, body = content[language]
            enqueue(
                self.user,
                colleague.email,
                subject,
                body + link.public_url,
                state="draft",
                batch=batch,
                link=link,
            )
            routes["email"] = f"/mail/drafts/{batch.pk}/"
            self.context.add_cookies(
                [
                    {
                        "name": "django_language",
                        "value": language,
                        "url": self.live_server_url,
                    }
                ]
            )
            destination = (
                Path(__file__).resolve().parents[1]
                / "docs/screenshots/user-guide"
                / language
            )
            destination.mkdir(parents=True, exist_ok=True)
            for name, route in routes.items():
                response = self.page.goto(
                    self.live_server_url + route, wait_until="networkidle"
                )
                self.assertEqual(response.status, 200, route)
                expect(self.page.locator("h1")).to_be_visible()
                if name == "api-explorer":
                    expect(
                        self.page.locator("#api-operation option").first
                    ).to_be_attached()
                self.page.screenshot(
                    path=str(destination / f"{name}.png"),
                    full_page=True,
                    animations="disabled",
                )
        self.assertFalse(self.errors)
