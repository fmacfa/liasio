import re
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from vault.mail import claim, deliver_one, enqueue, payload, render_message
from .test_security import IsolatedFiles

from vault.models import (
    Collection,
    Instance,
    MailDelivery,
    RecipientChallenge,
    ShareLink,
    User,
)
from vault.storage import add_files


@override_settings(
    EMAIL_HOST="smtp.example.test",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    ANTIVIRUS_REQUIRED=False,
)
class MailTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        Instance.objects.update_or_create(pk=1, defaults={"initialized": True})
        self.user = User.objects.create_user(
            "sender@example.test", "strong-pass", display_name="Sender"
        )
        self.other = User.objects.create_user("other@example.test", "strong-pass")
        self.folder = Collection.objects.create(owner=self.user, title="Files")
        add_files(
            self.user,
            self.folder.pk,
            [SimpleUploadedFile("hello.txt", b"hello")],
            ["hello.txt"],
        )
        self.link = ShareLink.objects.create(collection=self.folder, label="Alice")
        self.client.force_login(self.user)

    def test_draft_requires_confirmation_and_is_private(self):
        response = self.client.post(
            f"/mail/compose/?link={self.link.pk}",
            {
                "subject": "Hello $name",
                "body": "$collection\n$link",
                "email_0": "alice@example.test",
            },
        )
        self.assertEqual(response.status_code, 302)
        draft = MailDelivery.objects.get()
        self.assertEqual(draft.state, "draft")
        self.assertNotIn(self.link.public_url, draft.payload)
        self.assertEqual(payload(draft)["subject"], "Hello Alice")
        self.assertFalse(deliver_one())
        other = Client()
        other.force_login(self.other)
        self.assertEqual(other.get(response.url).status_code, 404)
        self.assertEqual(self.client.get(response.url).status_code, 200)
        self.assertEqual(
            self.client.post(response.url, {"confirm": "send"}).status_code, 302
        )
        self.assertTrue(deliver_one())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.link.public_url, mail.outbox[0].body)
        draft.refresh_from_db()
        self.assertEqual(draft.state, "sent")
        self.assertEqual(draft.payload, "")
        self.assertEqual(
            self.client.post(response.url, {"confirm": "send"}).status_code, 404
        )
        self.assertFalse(deliver_one())

    def test_permissions_rechecked_and_fair_owner_claim(self):
        first = enqueue(
            self.user, "alice@example.test", "Hello", "body", link=self.link
        )
        enqueue(self.user, "alice@example.test", "Hello", "body", link=self.link)
        second = enqueue(self.other, self.other.email, "Test", "test", kind="test")
        self.assertEqual(claim().pk, first.pk)
        self.assertEqual(claim().pk, second.pk)
        self.assertIsNone(claim())
        User.objects.update(mail_lease_until=timezone.now() - timedelta(seconds=1))
        MailDelivery.objects.update(lease_until=timezone.now() - timedelta(seconds=1))
        self.link.revoked_at = timezone.now()
        self.link.save()
        deliver_one()
        self.assertFalse(
            any(self.link.public_url in m.body for m in getattr(mail, "outbox", []))
        )

    def test_new_mfa_requirement_cancels_queued_share_message(self):
        from vault.models import Workspace

        row = enqueue(self.user, "alice@example.test", "Hello", "body", link=self.link)
        Workspace.objects.create(owner=self.user, name="Secure team", require_mfa=True)
        deliver_one()
        row.refresh_from_db()
        self.assertEqual(row.state, "canceled")
        self.assertFalse(getattr(mail, "outbox", []))

    def test_failed_delivery_retries_without_clearing_payload(self):
        row = enqueue(self.user, self.user.email, "Test", "body", kind="test")
        with patch(
            "vault.mail.EmailMessage.send",
            side_effect=OSError("private-server-details"),
        ):
            self.assertTrue(deliver_one())
        row.refresh_from_db()
        self.assertEqual(row.state, "pending")
        self.assertEqual(row.error, "smtp")
        self.assertEqual(row.attempts, 1)
        self.assertTrue(row.payload)
        self.assertFalse(deliver_one())

    def test_templates_reject_header_injection_and_attribute_traversal(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            render_message("Subject\nBcc: stolen@example.test", "body", {})
        with self.assertRaises(ValidationError):
            render_message("Subject", "${name.__class__}", {"name": "Alice"})

    def test_otp_is_browser_bound_one_use_and_gates_download(self):
        self.link.allowed_emails = ["alice@example.test"]
        self.link.save()
        visitor = Client()
        share = "/s/" + self.link.public_url.rstrip("/").split("/")[-1] + "/"
        verify = share + "verify/"
        self.assertContains(visitor.get(share), "Vérifiez votre adresse")
        self.assertEqual(visitor.post(share + "download/").status_code, 403)
        visitor.post(verify, {"action": "request", "email": "mallory@example.test"})
        self.assertEqual(RecipientChallenge.objects.count(), 0)
        visitor.post(verify, {"action": "request", "email": "alice@example.test"})
        row = MailDelivery.objects.get(kind="otp")
        code = re.search(r"\b[0-9]{6}\b", payload(row)["body"]).group()
        self.assertNotIn(code, RecipientChallenge.objects.get().code_hash)
        stranger = Client()
        self.assertEqual(
            stranger.post(verify, {"action": "verify", "code": code}).status_code, 200
        )
        self.assertEqual(
            visitor.post(verify, {"action": "verify", "code": code}).status_code, 302
        )
        self.assertContains(visitor.get(share), "hello.txt")
        self.assertEqual(stranger.post(share + "download/").status_code, 403)
        response = visitor.post(share + "download/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b"".join(response.streaming_content))
        self.link.allowed_emails = ["someone-else@example.test"]
        self.link.save()
        self.assertEqual(visitor.post(share + "download/").status_code, 403)

    def test_otp_attempt_limit_and_expiry(self):
        self.link.allowed_emails = ["alice@example.test"]
        self.link.save()
        visitor = Client()
        path = "/s/" + self.link.public_url.rstrip("/").split("/")[-1] + "/verify/"
        visitor.post(path, {"action": "request", "email": "alice@example.test"})
        code = re.search(
            r"\b[0-9]{6}\b", payload(MailDelivery.objects.get())["body"]
        ).group()
        wrong = "000001" if code == "000000" else "000000"
        for _ in range(5):
            visitor.post(path, {"action": "verify", "code": wrong})
        self.assertEqual(
            visitor.post(path, {"action": "verify", "code": code}).status_code, 200
        )
        self.assertIsNone(RecipientChallenge.objects.get().verified_at)

    def test_notifications_are_opt_in(self):
        from vault.mail import notify

        notify(self.user.pk, "uploads", "new files")
        self.assertFalse(MailDelivery.objects.exists())
        User.objects.filter(pk=self.user.pk).update(notify_uploads=True)
        notify(self.user.pk, "uploads", "new files")
        row = MailDelivery.objects.get()
        self.assertEqual(row.recipient, self.user.email)
        self.assertNotIn("/s/", payload(row)["body"])


class MailWorkerShutdownTests(TestCase):
    def test_sigterm_stops_idle_and_active_workers_without_forced_kill(self):
        import os
        import subprocess
        import sys
        from django.conf import settings

        # A real child process exercises the installed signal handler and thread drain.
        # No database or SMTP connection is opened; only the queue adapters are replaced.
        program = """
import os, signal, threading, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'linkvault.settings')
import django
django.setup()
from django.conf import settings
from unittest.mock import patch
from vault.management.commands.deliver_mail import Command
settings.EMAIL_HOST = 'smtp.example.test' if os.environ['TEST_MAIL_ACTIVE'] == '1' else ''
state = {'finished': 0}
def send():
    time.sleep(0.2)
    state['finished'] += 1
    return True
def heartbeat(**kwargs):
    threading.Timer(0.05, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
with patch('vault.management.commands.deliver_mail.ServiceState.objects.update_or_create', side_effect=heartbeat), patch('vault.management.commands.deliver_mail.deliver_one', side_effect=send):
    Command().handle(loop=True, workers=2)
assert state['finished'] == (2 if settings.EMAIL_HOST else 0)
print('graceful stop')
"""
        for active in ("0", "1"):
            result = subprocess.run(
                [sys.executable, "-c", program],
                cwd=settings.BASE_DIR,
                env={**os.environ, "TEST_MAIL_ACTIVE": active},
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("graceful stop", result.stdout)
