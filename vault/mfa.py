"""TOTP with encrypted secrets, replay protection and one-use recovery codes."""

from django.utils.translation import gettext_lazy as _
import base64
import hashlib
import hmac
import io
import secrets
import uuid

import pyotp
import segno
from cryptography.fernet import Fernet, InvalidToken
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods

from .models import Audit, User
from .security import limited, rate_allowed


class CodeForm(forms.Form):
    code = forms.CharField(
        label=_("Code de votre application ou code de récupération"),
        max_length=32,
        widget=forms.TextInput(
            attrs={"autocomplete": "one-time-code", "autofocus": True}
        ),
    )


class PasswordForm(forms.Form):
    password = forms.CharField(
        label=_("Mot de passe actuel"), max_length=128, widget=forms.PasswordInput
    )


def cipher():
    # Existing installations keep a stable fallback key when upgrading.
    key = settings.MFA_ENCRYPTION_KEY or base64.urlsafe_b64encode(
        hashlib.sha256(("linkvault:mfa:v1:" + settings.SECRET_KEY).encode()).digest()
    )
    return Fernet(key)


def consume_code(user, code):
    """Caller holds the User row lock. Never accept a reused time step."""
    if not user.mfa_secret or not isinstance(code, str) or len(code) > 32:
        return False
    value = code.strip().replace(" ", "")
    if len(value) == 6 and value.isascii() and value.isdigit():
        try:
            totp = pyotp.TOTP(cipher().decrypt(user.mfa_secret.encode()).decode())
        except (InvalidToken, ValueError):
            return False
        step = int(timezone.now().timestamp()) // 30
        for candidate in (step, step - 1, step + 1):
            if candidate > user.mfa_last_step and hmac.compare_digest(
                totp.at(candidate * 30), value
            ):
                user.mfa_last_step = candidate
                user.save(update_fields=["mfa_last_step"])
                return True
    for index, encoded in enumerate(user.recovery_codes):
        if check_password(value.upper(), encoded):
            user.recovery_codes.pop(index)
            user.save(update_fields=["recovery_codes"])
            Audit.objects.create(actor=user, action="mfa_recovery_used")
            return True
    return False


def recovery_codes(user):
    codes = [secrets.token_hex(8).upper() for _ in range(10)]
    user.recovery_codes = [make_password(code) for code in codes]
    return codes


def complete_login(request, user):
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session["mfa_version"] = str(user.mfa_version)
    request.session.pop("pending_login", None)
    Audit.objects.create(actor=user, action="login")


def begin_login(request, user):
    request.session.flush()
    request.session["pending_login"] = {
        "id": user.pk,
        "hash": user.get_session_auth_hash(),
        "at": timezone.now().timestamp(),
        "version": str(user.mfa_version),
    }


def throttled(request, scope, user):
    return limited(request, scope, 15) or not rate_allowed(
        scope + "-account", str(user.pk), 10
    )


@require_http_methods(["GET", "POST"])
@sensitive_post_parameters()
def challenge(request):
    from .views import too_many

    pending = request.session.get("pending_login", {})
    user = User.objects.filter(pk=pending.get("id"), is_active=True).first()
    if (
        not user
        or not user.mfa_secret
        or timezone.now().timestamp() - pending.get("at", 0) > 300
        or not hmac.compare_digest(
            pending.get("hash", ""), user.get_session_auth_hash()
        )
        or pending.get("version") != str(user.mfa_version)
    ):
        request.session.pop("pending_login", None)
        return redirect("login")
    form = CodeForm(request.POST or None)
    if request.method == "POST":
        if throttled(request, "mfa-login", user):
            return too_many(request)
        if form.is_valid():
            with transaction.atomic():
                user = User.objects.select_for_update().get(pk=user.pk)
                if (
                    user.is_active
                    and pending["hash"] == user.get_session_auth_hash()
                    and pending["version"] == str(user.mfa_version)
                    and consume_code(user, form.cleaned_data["code"])
                ):
                    complete_login(request, user)
                    return redirect("dashboard")
            form.add_error("code", _("Code incorrect, expiré ou déjà utilisé."))
    return render(request, "account/mfa_challenge.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters()
def settings_view(request):
    from .views import too_many

    error = None
    codes = None
    if request.method == "POST":
        if throttled(request, "mfa-settings", request.user):
            return too_many(request)
        password = request.POST.get("password", "")
        code = request.POST.get("code", "")
        action = request.POST.get("action")
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            if len(password) > 128 or not user.check_password(password):
                error = _("Mot de passe incorrect.")
            elif action == "begin" and not user.mfa_secret:
                request.session["mfa_setup"] = {
                    "secret": cipher().encrypt(pyotp.random_base32().encode()).decode(),
                    "at": timezone.now().timestamp(),
                    "hash": user.get_session_auth_hash(),
                }
            elif action == "enable" and not user.mfa_secret:
                setup = request.session.get("mfa_setup", {})
                if (
                    timezone.now().timestamp() - setup.get("at", 0) > 600
                    or setup.get("hash") != user.get_session_auth_hash()
                ):
                    error = _("Configuration expirée. Recommencez.")
                else:
                    user.mfa_secret = setup["secret"]
                    if consume_code(user, code):
                        codes = recovery_codes(user)
                        user.mfa_version = uuid.uuid4()
                        user.save(
                            update_fields=[
                                "mfa_secret",
                                "mfa_version",
                                "recovery_codes",
                            ]
                        )
                        request.session.pop("mfa_setup", None)
                        request.session["mfa_version"] = str(user.mfa_version)
                        Audit.objects.create(actor=user, action="mfa_enabled")
                    else:
                        error = _("Code incorrect ou expiré.")
            elif action in {"disable", "regenerate"} and user.mfa_secret:
                if consume_code(user, code):
                    user.mfa_version = uuid.uuid4()
                    codes = recovery_codes(user) if action == "regenerate" else None
                    if action == "disable":
                        user.mfa_secret, user.recovery_codes, user.mfa_last_step = (
                            "",
                            [],
                            -1,
                        )
                    user.save()
                    request.session["mfa_version"] = str(user.mfa_version)
                    Audit.objects.create(actor=user, action="mfa_" + action)
                    messages.success(
                        request,
                        _(
                            "Double authentification mise à jour. Les autres sessions sont déconnectées."
                        ),
                    )
                else:
                    error = _("Code incorrect, expiré ou déjà utilisé.")
            else:
                error = _("Action indisponible.")
        if codes or (action == "disable" and not error):
            from django.contrib.sessions.models import Session

            for session in (
                Session.objects.filter(expire_date__gt=timezone.now())
                .exclude(session_key=request.session.session_key)
                .iterator()
            ):
                if str(session.get_decoded().get("_auth_user_id", "")) == str(
                    request.user.pk
                ):
                    session.delete()
        request.user.refresh_from_db()
    pending = request.session.get("mfa_setup", {})
    setup_secret = None
    if (
        not request.user.mfa_secret
        and timezone.now().timestamp() - pending.get("at", 0) <= 600
    ):
        try:
            setup_secret = cipher().decrypt(pending["secret"].encode()).decode()
        except (KeyError, InvalidToken):
            request.session.pop("mfa_setup", None)
    return render(
        request,
        "account/mfa_settings.html",
        {
            "nav": "account",
            "error": error,
            "codes": codes,
            "setup_secret": setup_secret,
            "password_form": PasswordForm(),
            "code_form": CodeForm(),
        },
    )


@login_required
@require_GET
def qr(request):
    pending = request.session.get("mfa_setup", {})
    if (
        request.user.mfa_secret
        or timezone.now().timestamp() - pending.get("at", 0) > 600
    ):
        raise Http404
    try:
        secret = cipher().decrypt(pending["secret"].encode()).decode()
    except (KeyError, InvalidToken):
        raise Http404 from None
    uri = pyotp.TOTP(secret).provisioning_uri(
        request.user.email, issuer_name="Liasio"
    )
    output = io.BytesIO()
    segno.make(uri, micro=False).save(output, kind="svg", scale=5)
    return HttpResponse(output.getvalue(), content_type="image/svg+xml")


class MFASessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            version = request.session.get("mfa_version")
            if (version and version != str(request.user.mfa_version)) or (
                request.user.mfa_secret and not version
            ):
                logout(request)
                return redirect("login")
        return self.get_response(request)
