"""Optional OIDC code flow with explicit binding, PKCE, state and nonce."""

import hmac
import secrets
from urllib.parse import urlparse

from authlib.common.errors import AuthlibBaseError
from authlib.integrations.django_client import OAuth
from joserfc.errors import JoseError
from requests.exceptions import RequestException
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .mfa import begin_login, complete_login, consume_code
from .models import Audit, OIDCIdentity, User
from .security import limited


def client():
    if not settings.OIDC_ISSUER:
        raise Http404
    oauth = OAuth()
    remote = oauth.register(
        "identity",
        client_id=settings.OIDC_CLIENT_ID,
        client_secret=settings.OIDC_CLIENT_SECRET,
        server_metadata_url=settings.OIDC_ISSUER.rstrip("/")
        + "/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid profile email",
            "code_challenge_method": "S256",
            "default_timeout": (5, 10),
        },
    )
    metadata = remote.load_server_metadata()
    if metadata.get("issuer") != settings.OIDC_ISSUER:
        raise ValueError("Issuer mismatch")
    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        url = urlparse(metadata.get(field, ""))
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.fragment
        ):
            raise ValueError("Invalid provider endpoint")
    # Accept only asymmetric ID token signatures, including when discovery advertises more.
    metadata["id_token_signing_alg_values_supported"] = [
        "RS256",
        "ES256",
        "PS256",
        "EdDSA",
    ]
    return remote


def start_flow(request, purpose):
    state = secrets.token_urlsafe(32)
    flows = request.session.get("oidc_flows", {})
    flows[state] = {
        "purpose": purpose,
        "at": timezone.now().timestamp(),
        "user": request.user.pk if purpose == "bind" else None,
        "hash": request.user.get_session_auth_hash() if purpose == "bind" else "",
    }
    request.session["oidc_flows"] = dict(list(flows.items())[-5:])
    return client().authorize_redirect(
        request,
        settings.APP_URL + reverse("oidc_callback"),
        state=state,
        nonce=secrets.token_urlsafe(32),
    )


def failure(request):
    return render(
        request,
        "error.html",
        {
            "title": _("Connexion d’entreprise indisponible"),
            "detail": _(
                "La vérification a échoué ou cette identité n’est pas reliée à votre compte. Connectez-vous avec votre mot de passe pour la relier depuis Mon compte."
            ),
        },
        status=403,
    )


@require_POST
def login(request):
    if limited(request, "oidc-login", 20):
        from .views import too_many

        return too_many(request)
    try:
        return start_flow(request, "login")
    except (AuthlibBaseError, RequestException, JoseError, ValueError, KeyError):
        return failure(request)


class BindForm(forms.Form):
    password = forms.CharField(
        label=_("Mot de passe actuel"), max_length=128, widget=forms.PasswordInput
    )
    code = forms.CharField(
        label=_("Code de double authentification (si activée)"),
        max_length=32,
        required=False,
    )


@login_required
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters()
def settings_view(request):
    if not settings.OIDC_ISSUER:
        raise Http404
    form = BindForm(request.POST if request.method == "POST" else None)
    if request.method == "POST":
        if limited(request, "oidc-bind", 10):
            from .views import too_many

            return too_many(request)
        if form.is_valid():
            with transaction.atomic():
                user = User.objects.select_for_update().get(
                    pk=request.user.pk, is_active=True
                )
                valid = user.check_password(form.cleaned_data["password"])
                if valid and user.mfa_secret:
                    valid = consume_code(user, form.cleaned_data["code"])
                if valid and request.POST.get("action") == "unlink":
                    OIDCIdentity.objects.filter(
                        user=user, issuer=settings.OIDC_ISSUER
                    ).delete()
                    Audit.objects.create(actor=user, action="oidc_unlinked")
                    messages.success(
                        request,
                        _(
                            "Connexion d’entreprise dissociée. Votre mot de passe reste utilisable."
                        ),
                    )
                    return redirect("oidc_settings")
            if valid:
                try:
                    return start_flow(request, "bind")
                except (
                    AuthlibBaseError,
                    RequestException,
                    JoseError,
                    ValueError,
                    KeyError,
                ):
                    return failure(request)
            form.add_error(None, _("Mot de passe ou code incorrect."))
    return render(
        request,
        "account/oidc.html",
        {
            "nav": "account",
            "form": form,
            "bound": OIDCIdentity.objects.filter(
                user=request.user, issuer=settings.OIDC_ISSUER
            ).exists(),
        },
    )


@require_GET
@sensitive_variables()
def callback(request):
    if limited(request, "oidc-callback", 30):
        from .views import too_many

        return too_many(request)
    flows = request.session.get("oidc_flows", {})
    flow = flows.pop(request.GET.get("state", ""), None)
    request.session["oidc_flows"] = flows
    if not flow or not 0 <= timezone.now().timestamp() - flow["at"] < 300:
        return failure(request)
    try:
        token = client().authorize_access_token(
            request,
            claims_options={"iss": {"essential": True, "value": settings.OIDC_ISSUER}},
            leeway=30,
        )
        claims = token[
            "userinfo"
        ]  # Authlib verifies signature, audience, exp and the saved nonce first.
        subject = claims.get("sub")
        if (
            not isinstance(subject, str)
            or not subject
            or len(subject) > 255
            or claims.get("iss") != settings.OIDC_ISSUER
        ):
            return failure(request)
        if flow["purpose"] == "bind":
            if not request.user.is_authenticated or request.user.pk != flow["user"]:
                return failure(request)
            with transaction.atomic():
                user = User.objects.select_for_update().get(
                    pk=flow["user"], is_active=True
                )
                if not hmac.compare_digest(user.get_session_auth_hash(), flow["hash"]):
                    return failure(request)
                identity, created = OIDCIdentity.objects.get_or_create(
                    issuer=settings.OIDC_ISSUER,
                    subject=subject,
                    defaults={"user": user},
                )
                if identity.user_id != user.pk:
                    return failure(request)
                Audit.objects.create(actor=user, action="oidc_linked")
            messages.success(
                request, _("Connexion d’entreprise reliée à votre compte.")
            )
            return redirect("oidc_settings")
        identity = (
            OIDCIdentity.objects.select_related("user")
            .filter(issuer=settings.OIDC_ISSUER, subject=subject, user__is_active=True)
            .first()
        )
        if not identity:
            return failure(request)
        if identity.user.mfa_secret:
            begin_login(request, identity.user)
            return redirect("mfa_challenge")
        complete_login(request, identity.user)
        return redirect("dashboard")
    except (
        AuthlibBaseError,
        RequestException,
        JoseError,
        ValueError,
        KeyError,
        IntegrityError,
        User.DoesNotExist,
    ):
        return failure(request)
