"""Interactive bulk links, private list exports and stable-URL renaming."""

from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.validators import RegexValidator
from django.db import transaction
from django.db.models import Count
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import LinkForm
from .link_lists import MAX_LINKS, read_list, spreadsheet_response
from .models import Audit, Collection, Event, LinkBatch, LinkBatchItem, ShareLink, User
from .security import limited, rate_allowed

MAX_SAVED_BATCHES = 100
SINGLE_LINE = RegexValidator(
    r"[\x00-\x1f\x7f\ud800-\udfff\ufffe\uffff]",
    _("Utilisez un nom sur une seule ligne, sans caractères de contrôle."),
    inverse_match=True,
)


class SourceForm(forms.Form):
    name = forms.CharField(
        label=_("Nom du lot"), max_length=80, validators=[SINGLE_LINE]
    )
    mode = forms.ChoiceField(
        label=_("Comment préparer les liens ?"),
        choices=[
            ("count", _("Indiquer un nombre")),
            ("file", _("Importer une liste Excel ou CSV")),
        ],
    )
    count = forms.IntegerField(
        label=_("Nombre de liens"),
        required=False,
        min_value=1,
        max_value=MAX_LINKS,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "pattern": "[0-9]*"}),
        help_text=_(
            "De 1 à 200 liens par lot. Vous pourrez nommer chaque lien à l’étape suivante."
        ),
    )
    source = forms.FileField(
        label=_("Votre liste"),
        required=False,
        widget=forms.FileInput(attrs={"accept": ".xlsx,.csv"}),
        help_text=_(
            "Excel .xlsx ou CSV UTF-8 · 1 Mio · 200 lignes · 20 colonnes. Première feuille, avec une ligne d’en-tête."
        ),
    )

    def clean(self):
        data = super().clean()
        if data.get("mode") == "count" and data.get("count") is None:
            self.add_error("count", _("Indiquez le nombre de liens."))
        if data.get("mode") == "file" and not data.get("source"):
            self.add_error("source", _("Choisissez votre liste."))
        return data


class ConditionsForm(LinkForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("label")


class NamesForm(forms.Form):
    def __init__(self, labels, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for position, label in enumerate(labels):
            self.fields[f"label_{position}"] = forms.CharField(
                label=_("Nom du lien %(number)s") % {"number": position + 1},
                max_length=80,
                validators=[SINGLE_LINE],
                initial=label,
                widget=forms.TextInput(attrs={"data-batch-label": position}),
            )


def owner_batches(user):
    return LinkBatch.objects.filter(
        collection__owner=user, collection__deleted_at__isnull=True
    )


def owned_batch(request, pk):
    return get_object_or_404(
        owner_batches(request.user).select_related("collection"), pk=pk
    )


def check_batch_capacity(owner):
    if LinkBatch.objects.filter(collection__owner=owner).count() >= MAX_SAVED_BATCHES:
        raise ValidationError(
            _(
                "Vous avez atteint 100 lots. Supprimez une ancienne liste pour en préparer une autre ; ses liens seront conservés."
            )
        )


def new_batch(owner, collection, name, headers, rows):
    """Caller holds the owner and collection locks in a transaction."""
    check_batch_capacity(owner)
    if not collection.files.exists():
        raise ValidationError(_("Ajoutez au moins un fichier avant de créer un lien."))
    return LinkBatch.objects.create(
        collection=collection, name=name, headers=headers, source_rows=rows
    )


def rate_limited(request, scope, maximum):
    return limited(request, scope, maximum * 2, 3600) or not rate_allowed(
        scope + "-owner", str(request.user.pk), maximum, 3600
    )


def throttled():
    return HttpResponse(_("Trop de demandes. Réessayez plus tard."), status=429)


def cleanup_drafts():
    cutoff = timezone.now() - timedelta(hours=24)
    # Lock just this draft, then recheck its state. Never delete a batch completed
    # between the maintenance query and the final deletion.
    candidates = LinkBatch.objects.filter(
        completed_at__isnull=True, created_at__lt=cutoff
    ).values_list("pk", flat=True)[:200]
    for pk in list(candidates):
        with transaction.atomic():
            batch = (
                LinkBatch.objects.select_for_update()
                .filter(pk=pk, completed_at__isnull=True, created_at__lt=cutoff)
                .first()
            )
            if batch:
                batch.delete()


@login_required
@require_GET
def index(request):
    collection_id = request.GET.get("collection")
    if collection_id:
        try:
            collection = get_object_or_404(
                Collection, pk=collection_id, owner=request.user
            )
        except ValidationError:
            raise Http404 from None
        return redirect("link_batch_prepare", pk=collection.pk)
    batches = (
        owner_batches(request.user)
        .select_related("collection")
        .annotate(link_count=Count("items"))
    )
    page = Paginator(batches, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "links/batches.html",
        {
            "nav": "links",
            "batches": page,
            "page": page,
            "collections": Collection.objects.filter(owner=request.user),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def prepare(request, pk):
    collection = get_object_or_404(Collection, pk=pk, owner=request.user)
    form = SourceForm(
        request.POST if request.method == "POST" else None,
        request.FILES or None,
        initial={"name": collection.title[:80], "mode": "count", "count": 10},
    )
    if request.method == "POST":
        if rate_limited(request, "batch-prepare", 10):
            return throttled()
        if getattr(request, "upload_limit_exceeded", False):
            form.add_error(None, _("La liste ne doit pas dépasser 1 Mio."))
        elif form.is_valid():
            try:
                if form.cleaned_data["mode"] == "file":
                    headers, rows = read_list(form.cleaned_data["source"])
                else:
                    headers, rows = (
                        [],
                        [[] for _number in range(form.cleaned_data["count"])],
                    )
                with transaction.atomic():
                    owner = get_object_or_404(
                        User.objects.select_for_update(),
                        pk=request.user.pk,
                        is_active=True,
                    )
                    locked = get_object_or_404(
                        Collection.objects.select_for_update(), pk=pk, owner=owner
                    )
                    batch = new_batch(
                        owner, locked, form.cleaned_data["name"], headers, rows
                    )
                return redirect("link_batch", pk=batch.pk)
            except ValidationError as exc:
                form.add_error(None, exc)
    return render(
        request,
        "links/batch_prepare.html",
        {"nav": "links", "collection": collection, "form": form},
    )


def selected_column(batch, supplied=None):
    if not batch.headers:
        return None
    if supplied is not None:
        try:
            column = int(supplied)
            if 0 <= column < len(batch.headers):
                return column
        except (TypeError, ValueError):
            pass
        return 0
    for wanted in ("nom", "name", "label", "libellé", "nom du lien", "email", "e-mail"):
        for index, value in enumerate(batch.headers):
            if value.casefold() == wanted:
                return index
    return 0


def initial_labels(batch, column):
    return [
        (row[column].strip()[:80] if column is not None else "")
        or (_("Lien %(number)s") % {"number": f"{number + 1:03d}"})
        for number, row in enumerate(batch.source_rows)
    ]


def create_links(request, batch, names, conditions):
    # One hash for a shared batch password, outside database locks. Never persist
    # the clear-text password in the draft, list, audit, URL or exported workbook.
    password = conditions.cleaned_data["password"]
    password_hash = make_password(password) if password else ""
    with transaction.atomic():
        owner = get_object_or_404(
            User.objects.select_for_update(), pk=request.user.pk, is_active=True
        )
        collection = get_object_or_404(
            Collection.objects.select_for_update(), pk=batch.collection_id, owner=owner
        )
        batch = get_object_or_404(
            LinkBatch.objects.select_for_update(), pk=batch.pk, collection=collection
        )
        complete_batch(
            batch,
            owner,
            [names.cleaned_data[f"label_{i}"] for i in range(len(batch.source_rows))],
            conditions.cleaned_data,
            password_hash,
        )


def complete_batch(batch, owner, labels, conditions, password_hash):
    """Shared browser/API write path; caller holds owner, collection and batch locks.

    A newly inserted batch is already private to the caller's transaction. Hashing
    and input validation happen before these locks, on both entry points.
    """
    if batch.completed_at:
        return
    if batch.created_at <= timezone.now() - timedelta(hours=24):
        raise ValidationError(_("Ce brouillon a expiré. Préparez un nouveau lot."))
    collection = batch.collection
    if not collection.files.exists():
        raise ValidationError(_("Ajoutez au moins un fichier avant de créer un lien."))
    if settings.ANTIVIRUS_REQUIRED and collection.files.filter(scanned=False).exists():
        raise ValidationError(
            _(
                "Des fichiers n’ont pas été analysés par l’antivirus. Importez-les à nouveau dans un dossier avec l’antivirus activé."
            )
        )
    links = [
        ShareLink(
            collection=collection,
            label=label,
            password_hash=password_hash,
            expires_at=conditions["expires_at"],
            max_downloads=conditions["max_downloads"],
            allow_previews=conditions["allow_previews"],
        )
        for label in labels
    ]
    ShareLink.objects.bulk_create(links)
    LinkBatchItem.objects.bulk_create(
        [
            LinkBatchItem(batch=batch, link=link, position=position)
            for position, link in enumerate(links)
        ]
    )
    for link in links:
        Event.objects.create(link=link, kind="created")  # Transactional webhook outbox.
    Audit.objects.bulk_create(
        [
            Audit(actor=owner, action="link_created", target=str(link.pk))
            for link in links
        ]
    )
    batch.completed_at = timezone.now()
    batch.save(update_fields=["completed_at"])


@login_required
@require_http_methods(["GET", "POST"])
def detail(request, pk):
    batch = owned_batch(request, pk)
    column = selected_column(batch, request.POST.get("label_column"))
    items = (
        list(batch.items.select_related("link__collection"))
        if batch.completed_at
        else []
    )
    labels = (
        [item.link.label for item in items]
        if batch.completed_at
        else initial_labels(batch, column)
    )
    action = request.POST.get("action", "")
    if request.method == "POST" and action == "create" and batch.completed_at:
        return redirect("link_batch", pk=pk)
    posted = request.POST if request.method == "POST" and action != "map" else None
    names = NamesForm(labels, posted)
    conditions = ConditionsForm(
        request.POST if request.method == "POST" else None,
        initial={"expiry_mode": "days", "expires_in_days": 7},
    )
    if request.method == "POST" and action not in ("map", "create", "rename"):
        return HttpResponse(_("Action invalide."), status=400)
    if request.method == "POST" and (
        (action == "rename" and not batch.completed_at)
        or (action == "map" and batch.completed_at)
    ):
        return HttpResponse(_("Action invalide."), status=400)
    if request.method == "POST" and action in ("create", "rename"):
        if rate_limited(request, "batch-change", 30):
            return throttled()
        if action == "create" and names.is_valid() and conditions.is_valid():
            try:
                create_links(request, batch, names, conditions)
            except ValidationError as exc:
                conditions.add_error(None, exc)
            else:
                messages.success(
                    request,
                    _(
                        "Les liens sont créés. Copiez-les ou téléchargez la liste complétée."
                    ),
                )
                return redirect("link_batch", pk=pk)
        elif action == "rename" and batch.completed_at and names.is_valid():
            with transaction.atomic():
                owner = get_object_or_404(
                    User.objects.select_for_update(), pk=request.user.pk, is_active=True
                )
                collection = get_object_or_404(
                    Collection.objects.select_for_update(),
                    pk=batch.collection_id,
                    owner=owner,
                )
                locked = get_object_or_404(
                    LinkBatch.objects.select_for_update(),
                    pk=pk,
                    collection=collection,
                    completed_at__isnull=False,
                )
                current = list(locked.items.select_related("link"))
                for position, item in enumerate(current):
                    item.link.label = names.cleaned_data[f"label_{position}"]
                ShareLink.objects.bulk_update(
                    [item.link for item in current], ["label"]
                )
                Audit.objects.create(
                    actor=owner, action="link_batch_renamed", target=str(pk)
                )
            messages.success(
                request, _("Noms enregistrés. Les URL restent identiques.")
            )
            return redirect("link_batch", pk=pk)
    fields = list(names)
    rows = [
        {
            "field": field,
            "values": batch.source_rows[position],
            "cells": list(zip(batch.headers, batch.source_rows[position])),
            "item": items[position] if items else None,
            "position": position + 1,
        }
        for position, field in enumerate(fields)
    ]
    return render(
        request,
        "links/batch_detail.html",
        {
            "nav": "links",
            "batch": batch,
            "rows": rows,
            "names": names,
            "form": conditions,
            "column": column,
            "columns": list(enumerate(batch.headers)),
            "expired": not batch.completed_at
            and batch.created_at <= timezone.now() - timedelta(hours=24),
        },
    )


@login_required
@require_GET
def export(request, pk, format):
    if format not in ("csv", "xlsx"):
        raise Http404
    batch = owned_batch(request, pk)
    if not batch.completed_at:
        raise Http404
    if rate_limited(request, "batch-export", 60):
        return throttled()
    return export_response(batch, format)


def export_response(batch, format):
    """Export values after the caller has checked ownership, readiness and limits."""
    headers = list(batch.headers)
    for header in (
        "Liasio name",
        "Liasio URL",
        "Liasio status",
        "Liasio created at (UTC)",
        "Liasio expires at (UTC)",
        "Liasio max downloads",
        "Liasio downloads",
    ):
        unique, suffix = header, 2
        while unique.casefold() in {h.casefold() for h in headers}:
            unique = f"{header} ({suffix})"
            suffix += 1
        headers.append(unique)
    rows = []
    for item in batch.items.select_related("link__collection"):
        link = item.link
        rows.append(
            batch.source_rows[item.position]
            + [
                link.label,
                link.public_url,
                link.status_code,
                link.created_at.isoformat(),
                link.expires_at.isoformat() if link.expires_at else "",
                str(link.max_downloads) if link.max_downloads is not None else "",
                str(link.downloads),
            ]
        )
    return spreadsheet_response(
        headers,
        rows,
        format,
        filename="liasio-links-" + batch.pk.hex[:12],
        url_column=len(batch.headers) + 1,
    )


@login_required
@require_GET
def template(request, format):
    if format not in ("csv", "xlsx"):
        raise Http404
    if rate_limited(request, "batch-export", 60):
        return throttled()
    return spreadsheet_response(
        [_("Nom"), "Email", _("Référence")],
        [
            ["Camille", "camille@example.com", "CLIENT-001"],
            ["Alex", "alex@example.com", "CLIENT-002"],
        ],
        format,
        filename="liasio-list-template",
    )


@login_required
@require_POST
def delete(request, pk):
    batch = owned_batch(request, pk)
    with transaction.atomic():
        owner = get_object_or_404(
            User.objects.select_for_update(), pk=request.user.pk, is_active=True
        )
        collection = get_object_or_404(
            Collection.objects.select_for_update(), pk=batch.collection_id, owner=owner
        )
        locked = get_object_or_404(
            LinkBatch.objects.select_for_update(), pk=pk, collection=collection
        )
        locked.delete()  # Only the list and its associations; share links survive.
        Audit.objects.create(actor=owner, action="link_batch_deleted", target=str(pk))
    messages.success(
        request,
        _("Liste supprimée. Les liens déjà créés restent disponibles dans Mes liens."),
    )
    return redirect("link_batches")


@login_required
@require_http_methods(["GET", "POST"])
def rename(request, pk):
    link = get_object_or_404(
        ShareLink,
        pk=pk,
        collection__owner=request.user,
        collection__deleted_at__isnull=True,
    )
    form = NamesForm([link.label], request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        if rate_limited(request, "link-rename", 60):
            return throttled()
        with transaction.atomic():
            owner = get_object_or_404(
                User.objects.select_for_update(), pk=request.user.pk, is_active=True
            )
            collection = get_object_or_404(
                Collection.objects.select_for_update(),
                pk=link.collection_id,
                owner=owner,
            )
            locked = get_object_or_404(
                ShareLink.objects.select_for_update(), pk=pk, collection=collection
            )
            locked.label = form.cleaned_data["label_0"]
            locked.save(update_fields=["label"])
            Audit.objects.create(actor=owner, action="link_renamed", target=str(pk))
        messages.success(request, _("Noms enregistrés. Les URL restent identiques."))
        return redirect("links")
    return render(
        request, "links/rename.html", {"nav": "links", "form": form, "link": link}
    )
