from django.test import Client, TestCase
from vault.models import LoginSession, Workspace, WorkspaceMember
from .test_security import IsolatedFiles
from .test_batch_api import api_key


class SessionTests(IsolatedFiles, TestCase):
    def test_revoke_other_device_preserves_current_and_other_accounts(self):
        user = self.account()
        other = self.account("other@example.com")
        device, outsider = Client(), Client()
        self.client.force_login(user)
        device.force_login(user)
        outsider.force_login(other)
        device.get("/")
        outsider.get("/")
        self.assertEqual(self.client.get("/account/sessions/").status_code, 200)
        target = LoginSession.objects.get(session_id=device.session.session_key)
        foreign = LoginSession.objects.get(session_id=outsider.session.session_key)
        self.assertEqual(
            self.client.post(
                "/account/sessions/", {"action": "revoke", "session": str(foreign.pk)}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                "/account/sessions/", {"action": "revoke", "session": str(target.pk)}
            ).status_code,
            302,
        )
        self.assertEqual(device.get("/").status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(outsider.get("/").status_code, 200)

    def test_workspace_policy_requires_enrollment_in_browser_and_api(self):
        owner = self.account()
        member = self.account("member@example.com")
        workspace = Workspace.objects.create(owner=owner, name="MFA", require_mfa=True)
        WorkspaceMember.objects.create(workspace=workspace, user=member)
        self.client.force_login(member)
        self.assertRedirects(
            self.client.get("/collections/"),
            "/account/mfa/",
            fetch_redirect_response=False,
        )
        self.assertEqual(self.client.get("/account/mfa/").status_code, 200)
        _key, raw = api_key(member)
        self.assertEqual(
            self.client.get(
                "/api/v1/collections/", HTTP_AUTHORIZATION="Bearer " + raw
            ).status_code,
            401,
        )

    def test_api_credentials_cannot_reassign_browser_session_inventory(self):
        user = self.account()
        api_user = self.account("api@example.com")
        self.client.force_login(user)
        _key, raw = api_key(api_user)
        self.assertEqual(
            self.client.get(
                "/api/v1/collections/", HTTP_AUTHORIZATION="Bearer " + raw
            ).status_code,
            200,
        )
        self.assertFalse(LoginSession.objects.filter(user=api_user).exists())

    def test_upgrade_inventories_existing_sessions_once_for_later_revocation(self):
        import importlib
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection
        from django.contrib.sessions.models import Session
        from django.utils import timezone
        from datetime import timedelta

        user = self.account()
        device, anonymous = Client(), Client()
        device.force_login(user)
        session = anonymous.session
        session["anonymous"] = True
        session.save()
        expired = Session.objects.create(
            session_key="expired-example",
            session_data=Session.objects.get(
                pk=device.session.session_key
            ).session_data,
            expire_date=timezone.now() - timedelta(days=1),
        )
        migration = importlib.import_module("vault.migrations.0014_loginsession")
        migration.register_legacy_sessions(apps, SimpleNamespace(connection=connection))
        self.assertTrue(
            LoginSession.objects.filter(
                session_id=device.session.session_key, user=user
            ).exists()
        )
        self.assertFalse(LoginSession.objects.filter(session_id=expired.pk).exists())
        self.assertFalse(
            LoginSession.objects.filter(session_id=session.session_key).exists()
        )
        self.client.force_login(user)
        self.client.post("/account/sessions/", {"action": "revoke_others"})
        self.assertEqual(device.get("/").status_code, 302)
