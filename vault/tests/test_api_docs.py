import importlib.util

from django.conf import settings
from django.test import TestCase

try:
    from openapi_spec_validator import validate
except ImportError:
    import unittest

    raise unittest.SkipTest(
        "OpenAPI validator runs in development/CI tests, not runtime images."
    ) from None

from vault.models import Instance, User


class APIDocumentationTests(TestCase):
    def test_checked_in_contract_is_valid_reproducible_and_matches_routes(self):
        Instance.objects.update_or_create(pk=1, defaults={"initialized": True})
        spec = self.client.get("/api/openapi.json").json()
        validate(spec)
        path = settings.BASE_DIR / "scripts/build_openapi.py"
        module = importlib.util.spec_from_file_location("build_openapi", path)
        builder = importlib.util.module_from_spec(module)
        module.loader.exec_module(builder)
        self.assertEqual(spec, builder.build())
        from django.urls import resolve

        for path in spec["paths"]:
            route = (
                path.replace("{id}", "00000000-0000-4000-8000-000000000000")
                .replace("{part_id}", "00000000-0000-4000-8000-000000000001")
                .replace("{format}", "xlsx")
            )
            self.assertTrue(resolve("/api/v1" + route))

    def test_explorer_requires_login_and_never_embeds_a_key(self):
        Instance.objects.update_or_create(pk=1, defaults={"initialized": True})
        self.assertEqual(self.client.get("/automations/explorer/").status_code, 302)
        user = User.objects.create_user("developer@example.test", "password")
        self.client.force_login(user)
        response = self.client.get("/automations/explorer/")
        self.assertContains(response, 'id="api-key" type="password"')
        self.assertNotContains(response, "lv_")
