import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.db import close_old_connections, connections
from django.test import Client, TestCase, TransactionTestCase

from vault.forms import SetupForm
from vault.models import Audit, Branding, Instance, User
from .test_security import IsolatedFiles, PASSWORD


def signup_data(**changes):
    return {
        "email": "admin@example.com",
        "password": PASSWORD,
        "display_name": "Camille",
        "password_confirm": PASSWORD,
        **changes,
    }


class SetupTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        Instance.objects.update_or_create(pk=1, defaults={"initialized": False})

    def test_setup_in_all_languages_needs_no_installation_key(self):
        for language, title in (
            ("fr", "Créons votre espace."),
            ("en", "Let’s create your workspace."),
            ("de", "Richten wir Ihren Arbeitsbereich ein."),
        ):
            response = self.client.get("/setup/", HTTP_ACCEPT_LANGUAGE=language)
            self.assertContains(response, title)
            self.assertNotContains(response, "setup_token")
            self.assertNotContains(response, "SETUP_TOKEN")
            self.assertContains(response, "data-setup-form")
            self.assertContains(response, "data-setup-panel", count=2)
            self.assertContains(response, 'autocomplete="username"')

    def test_server_validates_all_fields_and_does_not_echo_passwords(self):
        for changes in (
            {"email": "invalid"},
            {"password": "short", "password_confirm": "short"},
            {"password_confirm": "A-different-confirmation-2026!"},
            {"display_name": ""},
        ):
            with self.subTest(changes=changes):
                response = self.client.post("/setup/", signup_data(**changes))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertContains(response, "ressaisissez votre mot de passe")
                self.assertNotContains(response, PASSWORD)
                for field in response.context["form"]:
                    if field.errors:
                        self.assertContains(response, f'id="{field.auto_id}_error"', count=1)
                self.assertFalse(User.objects.exists())
                self.assertFalse(Instance.objects.get(pk=1).initialized)

    def test_csrf_is_required_and_plain_form_submission_works(self):
        browser = Client(enforce_csrf_checks=True)
        self.assertEqual(browser.post("/setup/", signup_data()).status_code, 403)
        self.assertFalse(User.objects.exists())
        browser.get("/setup/")
        response = browser.post(
            "/setup/", signup_data(csrfmiddlewaretoken=browser.cookies["csrftoken"].value)
        )
        self.assertRedirects(response, "/")
        self.assertEqual(browser.session["_auth_user_id"], str(User.objects.get().pk))
        self.assertNotIn("password", browser.session)

    def test_setup_attempts_remain_rate_limited(self):
        for _ in range(5):
            self.assertEqual(self.client.post("/setup/", {}).status_code, 200)
        self.assertEqual(self.client.post("/setup/", signup_data()).status_code, 429)
        self.assertFalse(User.objects.exists())

    def test_initialized_instance_stays_closed_without_any_users(self):
        Instance.objects.filter(pk=1).update(initialized=True)
        self.assertRedirects(self.client.get("/setup/"), "/login/")
        self.assertRedirects(self.client.post("/setup/", signup_data()), "/login/")
        self.assertFalse(User.objects.exists())

    def test_existing_user_also_closes_an_uninitialized_instance(self):
        self.account()
        self.assertRedirects(self.client.get("/setup/"), "/login/")
        self.assertRedirects(self.client.post("/setup/", signup_data()), "/login/")
        self.assertEqual(User.objects.count(), 1)


class SetupConcurrencyTests(IsolatedFiles, TransactionTestCase):
    def test_two_first_signups_create_exactly_one_administrator(self):
        Instance.objects.update_or_create(pk=1, defaults={"initialized": False})
        barrier = threading.Barrier(2)
        original_validation = SetupForm.is_valid

        def validate_together(form):
            valid = original_validation(form)
            # Both requests have passed the initial empty-instance check.
            barrier.wait(timeout=10)
            return valid

        def attempt(index):
            close_old_connections()
            try:
                return Client().post(
                    "/setup/", signup_data(email=f"admin-{index}@example.com")
                ).url
            finally:
                connections["default"].close()

        with patch.object(SetupForm, "is_valid", validate_together):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(attempt, range(2)))
        self.assertCountEqual(results, ["/", "/login/"])
        self.assertEqual(User.objects.count(), 1)
        self.assertTrue(User.objects.get().is_superuser)
        self.assertEqual(Branding.objects.count(), 1)
        self.assertEqual(Audit.objects.filter(action="instance_initialized").count(), 1)
        self.assertTrue(Instance.objects.get(pk=1).initialized)
