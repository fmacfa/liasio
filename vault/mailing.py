"""Explicit two-step approval for individual, batch and invitation emails."""

from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from .access import collections_for, locked_collection
from .batches import accessible_batches, rate_limited, throttled
from .mail import enqueue, payload, permitted, render_message
from .models import (
    Audit,
    Invitation,
    MailBatch,
    MailDelivery,
    MailTemplate,
    ShareLink,
    User,
)


class MessageForm(forms.Form):
    subject = forms.CharField(
        label=_("Objet"), max_length=160, initial=_("Fichiers partagés : $collection")
    )
    body = forms.CharField(
        label=_("Message"),
        max_length=10000,
        widget=forms.Textarea,
        initial=_(
            "Bonjour $name,\n\nVoici votre lien personnel :\n$link\n\nBonne réception."
        ),
        help_text=_(
            "Variables : $name, $link, $collection. Les mots de passe des liens ne sont jamais inclus automatiquement."
        ),
    )

    def __init__(self, items, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for i, item in enumerate(items):
            self.fields[f"email_{i}"] = forms.EmailField(
                label=item["name"], initial=item.get("email", ""), max_length=254
            )

    def clean(self):
        data = super().clean()
        if data.get("subject") and data.get("body"):
            render_message(
                data["subject"],
                data["body"],
                {"name": "", "link": "", "collection": ""},
            )
        return data


def sources(request):
    links = ShareLink.objects.filter(
        collection__in=collections_for(request.user, write=True)
    )
    if request.GET.get("link"):
        link = get_object_or_404(links, pk=request.GET["link"])
        return [
            {
                "link": link,
                "name": link.label,
                "email": link.allowed_emails[0]
                if len(link.allowed_emails) == 1
                else "",
            }
        ]
    if request.GET.get("batch"):
        batch = get_object_or_404(
            accessible_batches(request.user),
            pk=request.GET["batch"],
            completed_at__isnull=False,
        )
        column = next(
            (
                i
                for i, h in enumerate(batch.headers)
                if h.lower() in ("email", "e-mail", "courriel")
            ),
            None,
        )
        return [
            {
                "link": item.link,
                "name": item.link.label,
                "email": batch.source_rows[item.position][column]
                if column is not None
                else "",
            }
            for item in batch.items.select_related("link__collection").order_by(
                "position"
            )
        ]
    if request.GET.get("invitation") and request.user.is_staff:
        invitation = get_object_or_404(
            Invitation, pk=request.GET["invitation"], invited_by=request.user
        )
        if not invitation.active:
            raise Http404
        return [
            {
                "invitation": invitation,
                "name": invitation.email,
                "email": invitation.email,
            }
        ]
    raise Http404


@login_required
@require_http_methods(["GET", "POST"])
def compose(request):
    items = sources(request)
    if not items:
        raise Http404
    initial = {}
    if request.GET.get("template"):
        template = get_object_or_404(
            MailTemplate, owner=request.user, pk=request.GET["template"]
        )
        initial = {"subject": template.subject, "body": template.body}
    form = MessageForm(
        items, request.POST if request.method == "POST" else None, initial=initial
    )
    if request.method == "POST":
        if rate_limited(request, "mail-drafts", 10):
            return throttled()
        if form.is_valid():
            try:
                with transaction.atomic():
                    # One source collection per batch; serialize permission changes before creating drafts.
                    if items[0].get("link"):
                        locked_collection(
                            request.user,
                            items[0]["link"].collection_id,
                            request=request,
                        )
                    sender = User.objects.select_for_update().get(
                        pk=request.user.pk, is_active=True
                    )
                    batch = MailBatch.objects.create(owner=sender)
                    for i, item in enumerate(items):
                        link, invitation = item.get("link"), item.get("invitation")
                        recipient = form.cleaned_data[f"email_{i}"].lower()
                        if invitation and recipient != invitation.email.lower():
                            raise ValidationError(
                                _(
                                    "Une invitation est réservée à son adresse d’origine."
                                )
                            )
                        subject, body = render_message(
                            form.cleaned_data["subject"],
                            form.cleaned_data["body"],
                            {
                                "name": item["name"],
                                "link": link.public_url
                                if link
                                else invitation.public_url,
                                "collection": link.collection.title
                                if link
                                else _("Invitation Liasio"),
                            },
                        )
                        enqueue(
                            sender,
                            recipient,
                            subject,
                            body,
                            state="draft",
                            batch=batch,
                            link=link,
                            invitation=invitation,
                            kind="share" if link else "invitation",
                        )
                return redirect("mail_review", pk=batch.pk)
            except ValidationError as exc:
                form.add_error(None, exc)
    return render(
        request,
        "mail/compose.html",
        {
            "nav": "mail",
            "form": form,
            "templates": MailTemplate.objects.filter(owner=request.user),
            "source_query": request.GET.urlencode(),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def review(request, pk):
    batch = get_object_or_404(MailBatch, owner=request.user, pk=pk)
    if request.method == "POST":
        if rate_limited(request, "mail-send", 10):
            return throttled()
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk, is_active=True)
            batch = MailBatch.objects.select_for_update().get(pk=batch.pk)
            if request.POST.get("action") == "cancel":
                batch.deliveries.filter(state__in=("draft", "pending")).update(
                    state="canceled", payload=""
                )
            elif request.POST.get("confirm") == "send" and not batch.confirmed_at:
                if not settings.EMAIL_HOST:
                    messages.error(
                        request, _("Configurez le serveur SMTP avant l’envoi.")
                    )
                    return redirect("mail_review", pk=batch.pk)
                deliveries = list(
                    batch.deliveries.select_related(
                        "sender", "link__collection__owner", "invitation"
                    )
                )
                if not deliveries or any(
                    d.state != "draft" or not permitted(d) for d in deliveries
                ):
                    messages.error(
                        request,
                        _(
                            "Un lien ou un brouillon n’est plus disponible. Préparez un nouvel envoi."
                        ),
                    )
                    return redirect("mail_review", pk=batch.pk)
                batch.deliveries.update(state="pending")
                batch.confirmed_at = timezone.now()
                batch.save(update_fields=["confirmed_at"])
                Audit.objects.create(
                    actor=request.user, action="mail_queued", target=str(batch.pk)
                )
            else:
                raise Http404
        return redirect("mail")
    items = list(batch.deliveries.order_by("created_at", "pk"))
    for item in items:
        item.content = payload(item) if item.payload else None
    return render(
        request,
        "mail/review.html",
        {"nav": "mail", "batch": batch, "deliveries": items},
    )


class NotificationForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("notify_downloads", "notify_uploads")
        labels = {
            "notify_downloads": _("Me prévenir des téléchargements autorisés"),
            "notify_uploads": _("Me prévenir des nouveaux fichiers"),
        }


@login_required
@require_http_methods(["GET", "POST"])
def index(request):
    form = NotificationForm(
        request.POST if request.method == "POST" else None, instance=request.user
    )
    if request.method == "POST" and form.is_valid():
        User.objects.filter(pk=request.user.pk).update(**form.cleaned_data)
        messages.success(
            request,
            _(
                "Préférences enregistrées. Les notifications sont limitées à 10 par heure."
            ),
        )
        return redirect("mail")
    page = Paginator(
        MailDelivery.objects.filter(sender=request.user)
        .exclude(kind="otp")
        .order_by("-created_at"),
        30,
    ).get_page(request.GET.get("page"))
    return render(
        request,
        "mail/index.html",
        {
            "nav": "mail",
            "deliveries": page,
            "page": page,
            "form": form,
            "drafts": MailBatch.objects.filter(
                owner=request.user,
                confirmed_at__isnull=True,
                deliveries__state="draft",
                created_at__gt=timezone.now() - timedelta(days=1),
            ).distinct()[:20],
        },
    )


class TemplateForm(forms.ModelForm):
    class Meta:
        model = MailTemplate
        fields = ("name", "subject", "body")
        labels = {
            "name": _("Nom du modèle"),
            "subject": _("Objet"),
            "body": _("Message"),
        }

    def clean(self):
        data = super().clean()
        if data.get("subject") and data.get("body"):
            render_message(
                data["subject"],
                data["body"],
                {"name": "", "link": "", "collection": ""},
            )
        if (
            data.get("name")
            and MailTemplate.objects.filter(
                owner=self.instance.owner, name=data["name"]
            )
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            self.add_error("name", _("Ce nom existe déjà."))
        return data


@login_required
@require_http_methods(["GET", "POST"])
def templates(request, pk=None):
    item = (
        get_object_or_404(MailTemplate, pk=pk, owner=request.user)
        if pk
        else MailTemplate(owner=request.user)
    )
    form = TemplateForm(
        request.POST if request.method == "POST" else None, instance=item
    )
    if request.method == "POST":
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            if pk and request.POST.get("action") == "delete":
                item.delete()
                return redirect("mail_templates")
            if form.is_valid():
                if (
                    not pk
                    and MailTemplate.objects.filter(owner=request.user).count() >= 100
                ):
                    form.add_error(None, _("Limite de 100 modèles atteinte."))
                else:
                    form.save()
                    return redirect("mail_templates")
    return render(
        request,
        "mail/templates.html",
        {
            "nav": "mail",
            "form": form,
            "item": item if pk else None,
            "templates": MailTemplate.objects.filter(owner=request.user).order_by(
                "name"
            ),
        },
    )
