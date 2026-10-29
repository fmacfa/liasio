"""Explicit collection grants; administration never implies file access.

Writes lock both accounts in primary-key order, then the collection. Grant
changes use the same order so revocation and suspension cannot race a write.
The collection owner remains the storage/quota/webhook/branding owner; the
authenticated actor is kept separately for credentials and audit attribution.
"""

from django.db.models import Q, F
from django.http import Http404
from django.shortcuts import get_object_or_404

from .models import Collection, CollectionAccess, GroupAccess, Workspace, User


def collections_for(user, *, write=False):
    if not user.is_authenticated or not user.is_active:
        return Collection.objects.none()
    grants = CollectionAccess.objects.filter(user_id=user.pk)
    group_grants = GroupAccess.objects.filter(
        group__members=user,
        group__workspace=F("collection__workspace"),
        group__workspace__owner__is_active=True,
    )
    if write:
        grants = grants.filter(role="collaborator")
        group_grants = group_grants.filter(role="collaborator")
    return Collection.objects.filter(owner__is_active=True).filter(
        Q(owner_id=user.pk)
        | Q(pk__in=grants.values("collection_id"))
        | Q(pk__in=group_grants.values("collection_id"))
    )


def collection_for(user, pk, *, write=False):
    return get_object_or_404(collections_for(user, write=write), pk=pk)


def lock_accounts(actor, owner_id, *, request=None):
    accounts = {
        u.pk: u
        for u in User.objects.select_for_update()
        .filter(pk__in={actor.pk, owner_id})
        .order_by("pk")
    }
    if len(accounts) != len({actor.pk, owner_id}) or any(
        not u.is_active for u in accounts.values()
    ):
        raise Http404
    from .sessions import requires_mfa

    if not accounts[actor.pk].mfa_secret and requires_mfa(accounts[actor.pk]):
        raise Http404
    if request is not None:
        from .api_auth import authorize_current

        authorize_current(request, accounts[actor.pk])
    return accounts[owner_id]


def locked_collection(actor, pk, *, write=True, request=None):
    initial = collection_for(actor, pk, write=write)
    lock_workspace(initial)
    owner = lock_accounts(actor, initial.owner_id, request=request)
    collection = get_object_or_404(
        collections_for(actor, write=write).select_for_update(of=("self",)),
        pk=pk,
        workspace_id=initial.workspace_id,
        owner_id=initial.owner_id,
    )
    return collection, owner


def lock_workspace(collection):
    # Group changes and collection writes serialize within their workspace.
    # Lock order: workspace(s), ordered accounts, collection, dependent rows.
    if collection.workspace_id:
        get_object_or_404(
            Workspace.objects.select_for_update(), pk=collection.workspace_id
        )


def role_for(user, collection):
    if collection.owner_id == user.pk:
        return "owner"
    if collections_for(user, write=True).filter(pk=collection.pk).exists():
        return "collaborator"
    return "reader" if collections_for(user).filter(pk=collection.pk).exists() else None
