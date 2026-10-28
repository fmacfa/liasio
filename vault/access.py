"""Explicit collection grants; administration never implies file access.

Writes lock both accounts in primary-key order, then the collection. Grant
changes use the same order so revocation and suspension cannot race a write.
The collection owner remains the storage/quota/webhook/branding owner; the
authenticated actor is kept separately for credentials and audit attribution.
"""

from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404

from .models import Collection, CollectionAccess, User


def collections_for(user, *, write=False):
    if not user.is_authenticated or not user.is_active:
        return Collection.objects.none()
    grants = CollectionAccess.objects.filter(user_id=user.pk)
    if write:
        grants = grants.filter(role="collaborator")
    return Collection.objects.filter(owner__is_active=True).filter(
        Q(owner_id=user.pk) | Q(pk__in=grants.values("collection_id"))
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
    if request is not None:
        from .api_auth import authorize_current

        authorize_current(request, accounts[actor.pk])
    return accounts[owner_id]


def locked_collection(actor, pk, *, write=True, request=None):
    initial = collection_for(actor, pk, write=write)
    owner = lock_accounts(actor, initial.owner_id, request=request)
    collection = get_object_or_404(
        collections_for(actor, write=write).select_for_update(of=("self",)), pk=pk
    )
    return collection, owner


def role_for(user, collection):
    if collection.owner_id == user.pk:
        return "owner"
    return (
        CollectionAccess.objects.filter(collection=collection, user=user)
        .values_list("role", flat=True)
        .first()
    )
