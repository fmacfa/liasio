"""Mailbox possession gate. Challenges are link- and browser-bound, never URL tokens."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from .models import RecipientChallenge, User
from .security import limited, rate_allowed


def emails(value):
    if isinstance(value, str):
        value = value.replace(",", "\n").splitlines()
    if not isinstance(value, list) or len(value) > 100:
        raise ValidationError(_("Indiquez au maximum 100 adresses e-mail."))
    field = forms.EmailField(max_length=254)
    return (
        sorted(
            set(
                field.clean(item.strip().lower())
                for item in value
                if isinstance(item, str) and item.strip()
            )
        )
        if all(isinstance(item, str) for item in value)
        else invalid()
    )


def invalid():
    raise ValidationError(_("Adresse e-mail invalide."))


def digest(value):
    return hmac.new(
        settings.SECRET_KEY.encode(), ("recipient:" + value).encode(), hashlib.sha256
    ).hexdigest()


def browser_nonce(request):
    if "recipient_nonce" not in request.session:
        request.session["recipient_nonce"] = secrets.token_urlsafe(32)
    return request.session["recipient_nonce"]


def allowed(request, link):
    if not link.allowed_emails:
        return True
    identifier = request.session.get("recipient_grants", {}).get(str(link.pk))
    if not identifier or "recipient_nonce" not in request.session:
        return False
    return RecipientChallenge.objects.filter(
        pk=identifier,
        link=link,
        email__in=link.allowed_emails,
        session_digest=digest(request.session["recipient_nonce"]),
        verified_at__isnull=False,
        expires_at__gt=timezone.now(),
    ).exists()


class AddressForm(forms.Form):
    email = forms.EmailField(label=_("Votre adresse e-mail"), max_length=254)


class CodeForm(forms.Form):
    code = forms.RegexField(
        r"\A[0-9]{6}\Z",
        label=_("Code reçu par e-mail"),
        widget=forms.TextInput(
            attrs={"autocomplete": "one-time-code", "inputmode": "numeric"}
        ),
    )


def gate(request, link, token, *, status=200):
    from .views import password_allowed

    if not password_allowed(request, link):
        from .forms import UnlockForm

        return render(
            request,
            "share/unlock.html",
            {"form": UnlockForm(), "token": token},
            status=status,
        )
    return render(
        request,
        "share/verify.html",
        {"address_form": AddressForm(), "code_form": CodeForm(), "token": token},
        status=status,
    )


@require_http_methods(["GET", "POST"])
@sensitive_post_parameters()
def verify(request, token):
    from .views import public_link, unavailable, password_allowed, too_many
    from .mail import enqueue

    link = public_link(token)
    if link.status_code != "active" or not link.collection.owner.is_active:
        return unavailable(request)
    if not password_allowed(request, link):
        return gate(request, link, token)
    if allowed(request, link):
        return redirect("share", token=token)
    address_form, code_form = AddressForm(), CodeForm()
    info = ""
    nonce = browser_nonce(request)
    if request.method == "POST":
        if limited(request, "recipient-verify", 30) or not rate_allowed(
            "recipient-link", str(link.pk), 60
        ):
            return too_many(request)
        if request.POST.get("action") == "request":
            address_form = AddressForm(request.POST)
            if address_form.is_valid():
                email = address_form.cleaned_data["email"].lower()
                if not rate_allowed("recipient-address", str(link.pk) + email, 5):
                    return too_many(request)
                # Identical visible outcome whether the address is permitted or not.
                info = _(
                    "Si cette adresse est autorisée, un code valable 10 minutes lui sera envoyé."
                )
                with transaction.atomic():
                    owner = User.objects.select_for_update().get(
                        pk=link.collection.owner_id
                    )
                    link.refresh_from_db()
                    RecipientChallenge.objects.filter(
                        link=link, session_digest=digest(nonce)
                    ).delete()
                    request.session["recipient_pending"] = str(secrets.token_hex(16))
                    if (
                        email in link.allowed_emails
                        and owner.is_active
                        and link.status_code == "active"
                    ):
                        code = f"{secrets.randbelow(1000000):06d}"
                        challenge = RecipientChallenge.objects.create(
                            link=link,
                            email=email,
                            session_digest=digest(nonce),
                            code_hash="",
                            expires_at=timezone.now() + timedelta(minutes=10),
                        )
                        challenge.code_hash = digest(str(challenge.pk) + ":" + code)
                        challenge.save(update_fields=["code_hash"])
                        request.session["recipient_pending"] = str(challenge.pk)
                        try:
                            enqueue(
                                owner,
                                email,
                                _("Votre code Liasio"),
                                _(
                                    "Votre code : %(code)s\nValable 10 minutes pour accéder aux fichiers partagés."
                                )
                                % {"code": code},
                                kind="otp",
                                link=link,
                                expires_at=challenge.expires_at,
                            )
                        except ValidationError:
                            challenge.delete()
        elif request.POST.get("action") == "verify":
            code_form = CodeForm(request.POST)
            if code_form.is_valid():
                with transaction.atomic():
                    challenge = (
                        RecipientChallenge.objects.select_for_update()
                        .filter(
                            pk=request.session.get("recipient_pending"),
                            link=link,
                            session_digest=digest(nonce),
                            email__in=link.allowed_emails,
                            expires_at__gt=timezone.now(),
                            verified_at__isnull=True,
                            attempts__lt=5,
                        )
                        .first()
                    )
                    if challenge:
                        challenge.attempts += 1
                        match = hmac.compare_digest(
                            challenge.code_hash,
                            digest(
                                str(challenge.pk) + ":" + code_form.cleaned_data["code"]
                            ),
                        )
                        if match:
                            challenge.verified_at = timezone.now()
                            challenge.expires_at = timezone.now() + timedelta(hours=1)
                            challenge.code_hash = ""
                        challenge.save()
                        if match:
                            grants = request.session.get("recipient_grants", {})
                            grants[str(link.pk)] = str(challenge.pk)
                            request.session["recipient_grants"] = dict(
                                list(grants.items())[-50:]
                            )
                            request.session.pop("recipient_pending", None)
                            request.session.cycle_key()
                            return redirect("share", token=token)
                code_form.add_error(
                    None,
                    _(
                        "Code incorrect, expiré ou déjà utilisé. Demandez un nouveau code."
                    ),
                )
    return render(
        request,
        "share/verify.html",
        {
            "address_form": address_form,
            "code_form": code_form,
            "token": token,
            "info": info,
        },
    )
