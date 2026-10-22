"""Session-only management; automation keys cannot mint more credentials."""

import secrets
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST
from .api_auth import credential_version, digest, valid
from .mfa import cipher, consume_code
from .models import APIToken, Audit, User, Webhook, WebhookDelivery
from .security import limited
from .webhooks import EVENTS, enqueue, parse_destination

SCOPES = [
    ("read", _("Lire mes dossiers, fichiers et liens")),
    ("files:write", _("Créer des dossiers et importer des fichiers")),
    ("links:write", _("Créer et révoquer des liens")),
]


class ConfirmForm(forms.Form):
    password = forms.CharField(
        label=_("Mot de passe actuel"),
        max_length=128,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    code = forms.CharField(
        label=_("Code de double authentification"),
        max_length=32,
        required=False,
        help_text=_("Requis si la double authentification est activée."),
    )


class TokenForm(ConfirmForm):
    name = forms.CharField(label=_("Nom de la clé"), max_length=80)
    scopes = forms.MultipleChoiceField(
        label=_("Permissions"), choices=SCOPES, widget=forms.CheckboxSelectMultiple
    )
    days = forms.IntegerField(
        label=_("Validité en jours"), min_value=1, max_value=365, initial=90
    )
    field_order = ["name", "scopes", "days", "password", "code"]


class WebhookForm(ConfirmForm):
    name = forms.CharField(label=_("Nom du webhook"), max_length=80)
    url = forms.CharField(
        label=_("URL de réception"),
        max_length=2000,
        widget=forms.URLInput,
        help_text=_(
            "HTTPS public, port 443. Les adresses internes et les redirections sont refusées."
        ),
    )
    events = forms.MultipleChoiceField(
        label=_("Événements"), choices=EVENTS, widget=forms.CheckboxSelectMultiple
    )
    field_order = ["name", "url", "events", "password", "code"]

    def clean_url(self):
        value = self.cleaned_data["url"]
        parse_destination(value)
        return value


def confirmed(user, form):
    if not user.is_active or not user.check_password(form.cleaned_data["password"]):
        form.add_error("password", _("Mot de passe incorrect."))
        return False
    if user.mfa_secret and not consume_code(user, form.cleaned_data["code"]):
        form.add_error("code", _("Code incorrect, expiré ou déjà utilisé."))
        return False
    return True


@login_required
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters()
def index(request):
    action = request.POST.get("action")
    token_form = TokenForm(
        request.POST if action == "token" else None, auto_id="token_%s"
    )
    webhook_form = WebhookForm(
        request.POST if action == "webhook" else None, auto_id="webhook_%s"
    )
    secret, secret_kind = None, None
    if request.method == "POST":
        if limited(request, "integration-settings", 10):
            from .views import too_many

            return too_many(request)
        form = (
            token_form
            if action == "token"
            else webhook_form
            if action == "webhook"
            else None
        )
        if form is None:
            raise Http404
        if form.is_valid():
            with transaction.atomic():
                user = User.objects.select_for_update().get(pk=request.user.pk)
                if confirmed(user, form):
                    if action == "token":
                        if (
                            APIToken.objects.filter(
                                owner=user,
                                revoked_at__isnull=True,
                                expires_at__gt=timezone.now(),
                            ).count()
                            >= 10
                        ):
                            form.add_error(
                                None,
                                _(
                                    "Révoquez une clé avant d’en créer une autre (10 maximum)."
                                ),
                            )
                        else:
                            token = APIToken(
                                owner=user,
                                name=form.cleaned_data["name"],
                                scopes=form.cleaned_data["scopes"],
                                expires_at=timezone.now()
                                + timedelta(days=form.cleaned_data["days"]),
                                credential_version=credential_version(user),
                            )
                            secret = f"lv_{token.pk.hex}.{secrets.token_urlsafe(32)}"
                            token.digest = digest(secret)
                            token.save()
                            secret_kind = _("Clé API")
                            Audit.objects.create(
                                actor=user,
                                action="api_key_created",
                                target=str(token.pk),
                            )
                            token_form = TokenForm(auto_id="token_%s")
                    elif Webhook.objects.filter(owner=user).count() >= 10:
                        form.add_error(
                            None,
                            _(
                                "Supprimez un webhook avant d’en créer un autre (10 maximum)."
                            ),
                        )
                    else:
                        secret = secrets.token_urlsafe(32)
                        hook = Webhook.objects.create(
                            owner=user,
                            name=form.cleaned_data["name"],
                            destination=cipher()
                            .encrypt(form.cleaned_data["url"].encode())
                            .decode(),
                            hostname=parse_destination(form.cleaned_data["url"])[0],
                            secret=cipher().encrypt(secret.encode()).decode(),
                            events=form.cleaned_data["events"],
                        )
                        secret_kind = _("Secret de signature")
                        Audit.objects.create(
                            actor=user, action="webhook_created", target=str(hook.pk)
                        )
                        webhook_form = WebhookForm(auto_id="webhook_%s")
    keys = list(
        APIToken.objects.filter(owner=request.user).order_by("-created_at")[:50]
    )
    for key in keys:
        key.usable = valid(key, request.user)
    from .models import ServiceState

    heartbeat = ServiceState.objects.filter(
        key="webhooks", updated_at__gte=timezone.now() - timedelta(minutes=10)
    ).exists()
    return render(
        request,
        "integrations/index.html",
        {
            "nav": "integrations",
            "token_form": token_form,
            "webhook_form": webhook_form,
            "keys": keys,
            "hooks": Webhook.objects.filter(owner=request.user).order_by("-created_at"),
            "secret": secret,
            "secret_kind": secret_kind,
            "worker_running": heartbeat,
        },
    )


@login_required
@require_POST
def revoke_token(request, pk):
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=request.user.pk)
        token = get_object_or_404(APIToken, pk=pk, owner=user)
        token.revoked_at = timezone.now()
        token.save(update_fields=["revoked_at"])
        Audit.objects.create(actor=user, action="api_key_revoked", target=str(pk))
    messages.success(request, _("Clé API révoquée."))
    return redirect("integrations")


@login_required
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters()
def webhook_detail(request, pk):
    hook = get_object_or_404(Webhook, pk=pk, owner=request.user)
    form, secret = ConfirmForm(request.POST or None), None
    if request.method == "POST":
        if limited(request, "webhook-action", 20):
            from .views import too_many

            return too_many(request)
        action = request.POST.get("action")
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            hook = Webhook.objects.select_for_update().get(pk=pk, owner=user)
            if not user.is_active:
                raise Http404
            if action == "test" and hook.active:
                enqueue(user.pk, "webhook.test", {"message": "LinkVault test"}, only=pk)
                messages.success(
                    request, _("Événement de test ajouté à la file d’attente.")
                )
                return redirect("webhook", pk=pk)
            if action == "pause":
                hook.active = False
                hook.save(update_fields=["active"])
                hook.deliveries.filter(state="pending").update(
                    state="canceled", error="endpoint_disabled"
                )
                Audit.objects.create(
                    actor=user, action="webhook_paused", target=str(pk)
                )
                return redirect("webhook", pk=pk)
            if (
                action in {"resume", "rotate", "delete"}
                and form.is_valid()
                and confirmed(user, form)
            ):
                if action == "delete":
                    hook.delete()
                    Audit.objects.create(
                        actor=user, action="webhook_deleted", target=str(pk)
                    )
                    return redirect("integrations")
                if action == "resume":
                    hook.active, hook.last_error = True, ""
                    hook.save(update_fields=["active", "last_error"])
                else:
                    secret = secrets.token_urlsafe(32)
                    hook.secret = cipher().encrypt(secret.encode()).decode()
                    hook.save(update_fields=["secret"])
                Audit.objects.create(
                    actor=user, action="webhook_" + action, target=str(pk)
                )
                form = ConfirmForm()
    from django.core.paginator import Paginator

    page = Paginator(hook.deliveries.order_by("-created_at"), 25).get_page(
        request.GET.get("page")
    )
    return render(
        request,
        "integrations/webhook.html",
        {
            "nav": "integrations",
            "hook": hook,
            "page": page,
            "form": form,
            "secret": secret,
        },
    )
