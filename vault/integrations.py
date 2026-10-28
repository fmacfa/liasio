"""Session-only management; automation keys cannot mint more credentials."""

import secrets
import shlex
from uuid import UUID
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST
from .api_auth import credential_version, digest, valid
from .mfa import cipher, consume_code
from .access import collections_for
from .models import APIToken, Audit, User, Webhook
from .security import limited
from .webhooks import EVENTS, enqueue, parse_destination, pause_hook

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
    validity = forms.ChoiceField(
        label=_("Expiration"),
        choices=[
            ("days", _("Dans un nombre de jours")),
            ("never", _("Sans expiration")),
        ],
        initial="days",
        required=False,
        help_text=_(
            "Sans expiration : la clé reste utilisable jusqu’à sa révocation ou un changement de mot de passe/MFA."
        ),
    )
    days = forms.IntegerField(
        label=_("Validité en jours"),
        min_value=1,
        max_value=365,
        initial=90,
        required=False,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "pattern": "[0-9]*"}),
    )
    field_order = ["name", "scopes", "validity", "days", "password", "code"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound and self.data.get("validity") == "never":
            self.fields["days"].disabled = True

    def clean(self):
        data = super().clean()
        if data.get("validity") != "never" and not data.get("days"):
            self.add_error("days", _("Indiquez une validité de 1 à 365 jours."))
        return data


class WebhookForm(ConfirmForm):
    name = forms.CharField(label=_("Nom du webhook"), max_length=80)
    url = forms.CharField(
        label=_("URL de réception"),
        max_length=2000,
        widget=forms.URLInput,
        help_text=_(
            "Collez l’URL fournie par votre outil (n8n, votre serveur…). Elle doit déjà accepter les notifications POST en HTTPS public sur le port 443."
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
    collections = collections_for(request.user, write=True)
    selected = request.GET.get("collection")
    if selected:
        try:
            UUID(selected)
        except ValueError:
            raise Http404
    collection = (
        get_object_or_404(collections, pk=selected) if selected else collections.first()
    )
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
                            )
                            .filter(
                                Q(expires_at__isnull=True)
                                | Q(expires_at__gt=timezone.now())
                            )
                            .count()
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
                                expires_at=(
                                    None
                                    if form.cleaned_data["validity"] == "never"
                                    else timezone.now()
                                    + timedelta(days=form.cleaned_data["days"])
                                ),
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
    base_url = settings.APP_URL.rstrip("/")
    endpoint, command = None, None
    if collection:
        endpoint = base_url + reverse("api_link_batch_create", args=[collection.pk])
        # No credential is ever inserted into an example. The shell reads it from
        # the user's environment, and the trusted URL is quoted as shell data.
        command = " \\\n  ".join(
            [
                "curl --fail-with-body --silent --show-error",
                "--request POST " + shlex.quote(endpoint),
                '--header "Authorization: Bearer ${LINKVAULT_API_KEY}"',
                "--header 'Content-Type: application/json'",
                "--header 'Idempotency-Key: " + secrets.token_hex(16) + "'",
                '--data \'{"name":"Clients","names":["A","B"]}\'',
            ]
        )
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
            "api_base_url": base_url + "/api/v1/",
            "api_collections": collections,
            "api_collection": collection,
            "api_batch_endpoint": endpoint,
            "api_batch_command": command,
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
                enqueue(user.pk, "webhook.test", {"message": "Liasio test"}, only=pk)
                messages.success(
                    request, _("Événement de test ajouté à la file d’attente.")
                )
                return redirect("webhook", pk=pk)
            if action == "pause":
                pause_hook(hook)
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
                    # Normalize queues left by a pre-upgrade automatic pause too.
                    if not hook.active:
                        pause_hook(hook)
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
    for delivery in page:
        delivery.explanation = delivery_explanation(delivery.http_status)
    from cryptography.fernet import InvalidToken

    try:
        destination = cipher().decrypt(hook.destination.encode()).decode()
    except (InvalidToken, UnicodeError):
        destination = None
    return render(
        request,
        "integrations/webhook.html",
        {
            "nav": "integrations",
            "hook": hook,
            "page": page,
            "form": form,
            "secret": secret,
            "destination": destination,
        },
    )


def delivery_explanation(status):
    if status == 404:
        return _(
            "Adresse introuvable : vérifiez le chemin et activez le workflow destinataire."
        )
    if status == 405:
        return _(
            "Cette adresse n’accepte pas POST. Utilisez une URL de réception de webhook."
        )
    if status in (401, 403):
        return _(
            "Le destinataire refuse l’accès. Vérifiez sa configuration et le secret de signature."
        )
    if status and 300 <= status < 400:
        return _("Redirection refusée. Enregistrez directement l’URL HTTPS finale.")
    if status == 429 or (status and status >= 500):
        return _(
            "Destinataire temporairement indisponible. Une reprise est prévue si des tentatives restent disponibles."
        )
    return ""
