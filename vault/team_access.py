"""Session-only, owner-managed grants for existing team accounts."""

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from .access import lock_accounts
from .models import Audit, Collection, CollectionAccess, UploadSession, User
from .security import limited

CHOICES = [("", _("Aucun accès")), *CollectionAccess.ROLES]


def set_access(owner, collection_id, member, role):
    if role not in {"", "reader", "collaborator"} or member.pk == owner.pk:
        raise Http404
    with transaction.atomic():
        lock_accounts(owner, member.pk)
        collection = get_object_or_404(
            Collection.objects.select_for_update(), pk=collection_id, owner=owner
        )
        current = CollectionAccess.objects.filter(collection=collection, user=member)
        if current.values_list("role", flat=True).first() == (role or None):
            return
        if role:
            CollectionAccess.objects.update_or_create(
                collection=collection, user=member, defaults={"role": role}
            )
        else:
            current.delete()
        if role != "collaborator":
            # Release the owner's reserved quota immediately on downgrade/removal.
            from .resumable import cancel_session

            for pk in UploadSession.objects.filter(
                collection=collection, initiated_by=member, state="pending"
            ).values_list("pk", flat=True):
                cancel_session(owner, pk)
        Audit.objects.create(
            actor=owner,
            action="collection_access_changed",
            target=f"{collection.pk}:{member.pk}",
        )


class GrantForm(forms.Form):
    email = forms.EmailField(label=_("Adresse e-mail du membre"), max_length=254)
    role = forms.ChoiceField(
        label=_("Niveau d’accès"), choices=CHOICES, initial="reader", required=False
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        self.member = User.objects.filter(email__iexact=email, is_active=True).first()
        if self.member is None:
            raise forms.ValidationError(
                _(
                    "Ce membre doit d’abord accepter son invitation et disposer d’un compte actif."
                )
            )
        return email


@login_required
@require_http_methods(["GET", "POST"])
def collection_access(request, pk):
    collection = get_object_or_404(Collection, pk=pk, owner=request.user)
    form = GrantForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        if form.member.pk == request.user.pk:
            form.add_error(
                "email", _("Le propriétaire conserve tous les droits sur ce dossier.")
            )
        elif limited(request, "collection-access", 60, 3600):
            return render(
                request,
                "error.html",
                {
                    "title": _("Un instant…"),
                    "detail": _("Trop de demandes. Réessayez plus tard."),
                },
                status=429,
            )
        else:
            set_access(request.user, pk, form.member, form.cleaned_data["role"])
            messages.success(request, _("Accès au dossier mis à jour."))
            return redirect("collection_access", pk=pk)
    return render(
        request,
        "collections/access.html",
        {
            "nav": "collections",
            "collection": collection,
            "form": form,
            "grants": collection.access_grants.select_related("user").order_by(
                "user__email"
            ),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def member_access(request, pk):
    # This shortcut is for team administrators; it still exposes only their own folders.
    if not request.user.is_staff or request.user.pk == pk:
        raise Http404
    member = get_object_or_404(User, pk=pk, is_active=True)
    collections = list(Collection.objects.filter(owner=request.user))
    existing = dict(
        CollectionAccess.objects.filter(
            user=member, collection__owner=request.user
        ).values_list("collection_id", "role")
    )
    form = forms.Form(request.POST if request.method == "POST" else None)
    for collection in collections:
        form.fields[str(collection.pk)] = forms.ChoiceField(
            label=collection.title,
            choices=CHOICES,
            initial=existing.get(collection.pk, ""),
            required=False,
        )
    if request.method == "POST" and form.is_valid():
        if limited(request, "collection-access", 60, 3600):
            return render(
                request,
                "error.html",
                {
                    "title": _("Un instant…"),
                    "detail": _("Trop de demandes. Réessayez plus tard."),
                },
                status=429,
            )
        with transaction.atomic():
            lock_accounts(request.user, member.pk)
            for collection in sorted(collections, key=lambda c: c.pk):
                set_access(
                    request.user,
                    collection.pk,
                    member,
                    form.cleaned_data[str(collection.pk)],
                )
        messages.success(request, _("Accès au dossier mis à jour."))
        return redirect("member_access", pk=pk)
    return render(
        request,
        "collections/member_access.html",
        {"nav": "team", "member": member, "form": form},
    )
