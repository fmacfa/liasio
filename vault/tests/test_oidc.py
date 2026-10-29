import time
from urllib.parse import parse_qs, urlparse

try:
    import responses
except ImportError:
    import unittest

    raise unittest.SkipTest(
        "Mock OIDC integration runs in development/CI tests, not runtime images."
    ) from None
from joserfc import jwt
from joserfc.jwk import RSAKey
from django.test import Client, TestCase, override_settings

from vault.models import Instance, OIDCIdentity, User


@override_settings(
    OIDC_ISSUER="https://identity.example.test",
    OIDC_CLIENT_ID="liasio-test",
    OIDC_CLIENT_SECRET="fixture-secret",
)
class OIDCTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = RSAKey.generate_key(2048)

    def setUp(self):
        Instance.objects.update_or_create(pk=1, defaults={"initialized": True})
        self.user = User.objects.create_user(
            "member@example.test", "local-test-password"
        )

    def provider(self):
        metadata = {
            "issuer": "https://identity.example.test",
            "authorization_endpoint": "https://identity.example.test/authorize",
            "token_endpoint": "https://identity.example.test/token",
            "jwks_uri": "https://identity.example.test/keys",
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"],
        }
        responses.get(
            "https://identity.example.test/.well-known/openid-configuration",
            json=metadata,
        )
        public = self.key.as_dict(private=False)
        public["kid"] = "test"
        responses.get("https://identity.example.test/keys", json={"keys": [public]})

    def callback(self, browser, response, subject="member-123", **overrides):
        query = parse_qs(urlparse(response.url).query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertTrue(query["code_challenge"])
        claims = {
            "iss": "https://identity.example.test",
            "sub": subject,
            "aud": "liasio-test",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "nonce": query["nonce"][0],
            "email": self.user.email,
            "email_verified": True,
            **overrides,
        }
        encoded = jwt.encode({"alg": "RS256", "kid": "test"}, claims, self.key)
        responses.post(
            "https://identity.example.test/token",
            json={
                "access_token": "fixture-access-token",
                "token_type": "Bearer",
                "id_token": encoded,
            },
        )
        path = "/oidc/callback/?state=" + query["state"][0] + "&code=fixture-code"
        result = browser.get(path)
        self.assertEqual(
            browser.get(path).status_code, 403, "Callback state must be single-use"
        )
        return result

    @responses.activate
    def test_explicit_binding_then_sso_login(self):
        self.provider()
        self.client.force_login(self.user)
        response = self.client.post(
            "/account/oidc/", {"password": "local-test-password", "action": "bind"}
        )
        self.assertEqual(self.callback(self.client, response).status_code, 302)
        self.assertEqual(OIDCIdentity.objects.get().user, self.user)
        visitor = Client()
        result = self.callback(visitor, visitor.post("/oidc/login/"))
        self.assertEqual(result.url, "/")
        self.assertEqual(visitor.session["_auth_user_id"], str(self.user.pk))
        self.assertFalse(
            any(
                "access_token" in key or "id_token" in key
                for key in visitor.session.keys()
            )
        )

    @responses.activate
    def test_same_email_does_not_provision_or_connect_account(self):
        self.provider()
        visitor = Client()
        response = self.callback(visitor, visitor.post("/oidc/login/"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(User.objects.count(), 1)
        self.assertNotIn("_auth_user_id", visitor.session)

    @responses.activate
    def test_invalid_nonce_audience_and_expiration_are_rejected(self):
        self.provider()
        OIDCIdentity.objects.create(
            user=self.user, issuer="https://identity.example.test", subject="member-123"
        )
        for claims in (
            {"nonce": "wrong"},
            {"aud": "another-client"},
            {"exp": int(time.time()) - 300},
        ):
            visitor = Client()
            result = self.callback(visitor, visitor.post("/oidc/login/"), **claims)
            self.assertEqual(result.status_code, 403)
            self.assertNotIn("_auth_user_id", visitor.session)

    @responses.activate
    def test_binding_cannot_move_identity_to_a_different_account(self):
        self.provider()
        other = User.objects.create_user("other@example.test", "another-password")
        OIDCIdentity.objects.create(
            user=other, issuer="https://identity.example.test", subject="member-123"
        )
        self.client.force_login(self.user)
        result = self.callback(
            self.client,
            self.client.post("/account/oidc/", {"password": "local-test-password"}),
        )
        self.assertEqual(result.status_code, 403)
        self.assertEqual(OIDCIdentity.objects.get().user, other)
