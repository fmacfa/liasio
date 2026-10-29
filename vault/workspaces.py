"""Explicit team organization, group grants and accountable ownership transfers."""

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from .access import collections_for, lock_accounts, lock_workspace
from .models import (
    Asset,
    Audit,
    Collection,
    CollectionAccess,
    GroupAccess,
    TeamGroup,
    UploadSession,
    User,
    Workspace,
    WorkspaceMember,
)
from .security import limited


def workspaces_for(user):
    return (
        Workspace.objects.filter(owner__is_active=True)
        .filter(Q(owner=user) | Q(members__user=user))
        .distinct()
    )


def lock_workspace_accounts(workspace):
    ids = set(workspace.members.values_list("user_id", flat=True))
    ids.update(workspace.collections.values_list("owner_id", flat=True))
    ids.add(workspace.owner_id)
    accounts = {
        u.pk: u
        for u in User.objects.select_for_update().filter(pk__in=ids).order_by("pk")
    }
    if not accounts[workspace.owner_id].is_active:
        raise Http404


def cancel_inaccessible_uploads(workspace_id, collection_id=None):
    from .resumable import cancel_session

    queryset = UploadSession.objects.filter(
        collection__workspace_id=workspace_id,
        state="pending",
        initiated_by__isnull=False,
    )
    if collection_id:
        queryset = queryset.filter(collection_id=collection_id)
    pending = list(queryset.select_related("initiated_by").order_by("owner_id", "pk"))
    owners = {
        u.pk: u
        for u in User.objects.select_for_update()
        .filter(pk__in={s.owner_id for s in pending})
        .order_by("pk")
    }
    for session in pending:
        if (
            not collections_for(session.initiated_by, write=True)
            .filter(pk=session.collection_id)
            .exists()
        ):
            cancel_session(owners[session.owner_id], session.pk)


def attach_collection(actor, collection_id, workspace_id):
    with transaction.atomic():
        initial = get_object_or_404(Collection, pk=collection_id, owner=actor)
        ids = {pk for pk in [initial.workspace_id, workspace_id] if pk}
        list(Workspace.objects.select_for_update().filter(pk__in=ids).order_by("pk"))
        lock_accounts(actor, actor.pk)
        workspace = (
            get_object_or_404(workspaces_for(actor), pk=workspace_id)
            if workspace_id
            else None
        )
        collection = get_object_or_404(
            Collection.objects.select_for_update(),
            pk=collection_id,
            owner=actor,
            workspace_id=initial.workspace_id,
        )
        if collection.workspace_id != (workspace.pk if workspace else None):
            collection.group_grants.all().delete()
            collection.workspace = workspace
            collection.save(update_fields=["workspace", "updated_at"])
            # Cancel former group members' reservations after removing the grants.
            from .resumable import cancel_session

            for session in UploadSession.objects.filter(
                collection=collection, state="pending"
            ).select_related("initiated_by"):
                if (
                    session.initiated_by_id
                    and not collections_for(session.initiated_by, write=True)
                    .filter(pk=collection.pk)
                    .exists()
                ):
                    cancel_session(actor, session.pk)
            Audit.objects.create(
                actor=actor,
                action="collection_workspace_changed",
                target=str(collection.pk),
            )
        return collection


def transfer_collection(actor, collection_id, recipient):
    with transaction.atomic():
        initial = get_object_or_404(Collection, pk=collection_id, owner=actor)
        lock_workspace(initial)
        new_owner = lock_accounts(actor, recipient.pk)
        if recipient.pk == actor.pk:
            raise ValidationError(_("Choisissez un autre propriétaire."))
        collection = get_object_or_404(
            Collection.objects.select_for_update(),
            pk=collection_id,
            owner=actor,
            workspace_id=initial.workspace_id,
        )
        if (
            collection.workspace_id
            and not workspaces_for(new_owner)
            .filter(pk=collection.workspace_id)
            .exists()
        ):
            raise ValidationError(
                _("Le nouveau propriétaire doit appartenir à cet espace d’équipe.")
            )
        size = (
            Asset.all_objects.filter(collection=collection).aggregate(n=Sum("size"))[
                "n"
            ]
            or 0
        )
        if (
            new_owner.used_bytes + new_owner.reserved_bytes + size
            > new_owner.quota_bytes
        ):
            raise ValidationError(
                _("Le quota du nouveau propriétaire est insuffisant.")
            )
        from .resumable import cancel_session

        for session in UploadSession.objects.filter(
            collection=collection, state="pending"
        ):
            cancel_session(actor, session.pk)
        old_owner = User.objects.get(pk=actor.pk)
        old_owner.used_bytes -= size
        old_owner.save(update_fields=["used_bytes"])
        new_owner.used_bytes += size
        new_owner.save(update_fields=["used_bytes"])
        collection.owner = new_owner
        collection.save(update_fields=["owner", "updated_at"])
        CollectionAccess.objects.filter(collection=collection, user=new_owner).delete()
        Audit.objects.create(
            actor=actor, action="collection_transferred", target=str(collection.pk)
        )
        return collection


class WorkspaceForm(forms.ModelForm):
    class Meta:
        model = Workspace
        fields = ["name", "require_mfa"]
        labels = {
            "name": _("Nom de l’espace"),
            "require_mfa": _("Exiger la double authentification"),
        }


class MemberForm(forms.Form):
    email = forms.EmailField(label=_("Adresse e-mail du membre"), max_length=254)


class GroupForm(forms.ModelForm):
    class Meta:
        model = TeamGroup
        fields = ["name", "members"]
        labels = {"name": _("Nom du groupe"), "members": _("Membres du groupe")}
        widgets = {"members": forms.CheckboxSelectMultiple}

    def __init__(self, *args, workspace, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.workspace = workspace
        self.fields["members"].queryset = User.objects.filter(
            workspace_memberships__workspace=workspace, is_active=True
        ).order_by("email")

    def clean_name(self):
        name = self.cleaned_data["name"]
        if (
            TeamGroup.objects.filter(workspace=self.instance.workspace, name=name)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise ValidationError(_("Ce nom existe déjà."))
        return name


@login_required
@require_http_methods(["GET", "POST"])
def index(request):
    form = WorkspaceForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        if limited(request, "workspace-create", 10, 3600):
            from .views import too_many

            return too_many(request)
        if form.cleaned_data["require_mfa"] and not request.user.mfa_secret:
            form.add_error(
                "require_mfa",
                _("Activez d’abord votre double authentification dans Mon compte."),
            )
        else:
            with transaction.atomic():
                current = lock_accounts(request.user, request.user.pk)
                if form.cleaned_data["require_mfa"] and not current.mfa_secret:
                    form.add_error(
                        "require_mfa",
                        _(
                            "Activez d’abord votre double authentification dans Mon compte."
                        ),
                    )
                elif Workspace.objects.filter(owner=request.user).count() >= 20:
                    form.add_error(
                        None, _("La limite de 20 espaces par compte est atteinte.")
                    )
                else:
                    workspace = form.save(commit=False)
                    workspace.owner = request.user
                    workspace.save()
                    WorkspaceMember.objects.create(
                        workspace=workspace, user=request.user
                    )
                    Audit.objects.create(
                        actor=request.user,
                        action="workspace_created",
                        target=str(workspace.pk),
                    )
                    return redirect("workspace", pk=workspace.pk)
    return render(
        request,
        "workspaces/index.html",
        {
            "nav": "workspaces",
            "workspaces": workspaces_for(request.user),
            "form": form,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def detail(request, pk):
    workspace = get_object_or_404(workspaces_for(request.user), pk=pk)
    is_owner = workspace.owner_id == request.user.pk
    member_form = MemberForm()
    settings_form = WorkspaceForm(instance=workspace)
    if request.method == "POST":
        if not is_owner or limited(request, "workspace-change", 60, 3600):
            raise Http404
        try:
            with transaction.atomic():
                workspace = get_object_or_404(
                    Workspace.objects.select_for_update(), pk=pk, owner=request.user
                )
                lock_workspace_accounts(workspace)
                action = request.POST.get("action")
                if action == "add":
                    member_form = MemberForm(request.POST)
                    if member_form.is_valid():
                        member = get_object_or_404(
                            User,
                            email__iexact=member_form.cleaned_data["email"],
                            is_active=True,
                        )
                        if workspace.members.count() >= 200:
                            raise ValidationError(
                                _("La limite de 200 membres par espace est atteinte.")
                            )
                        WorkspaceMember.objects.get_or_create(
                            workspace=workspace, user=member
                        )
                elif action == "remove":
                    member = get_object_or_404(
                        workspace.members, pk=request.POST.get("member")
                    )
                    if member.user_id == workspace.owner_id:
                        raise ValidationError(
                            _(
                                "Transférez la responsabilité de l’espace avant de retirer son propriétaire."
                            )
                        )
                    for group in workspace.groups.all():
                        group.members.remove(member.user_id)
                    member.delete()
                    cancel_inaccessible_uploads(workspace.pk)
                elif action == "settings":
                    settings_form = WorkspaceForm(request.POST, instance=workspace)
                    if settings_form.is_valid():
                        if (
                            settings_form.cleaned_data["require_mfa"]
                            and not User.objects.get(pk=request.user.pk).mfa_secret
                        ):
                            raise ValidationError(
                                _(
                                    "Activez d’abord votre double authentification dans Mon compte."
                                )
                            )
                        settings_form.save()
                elif action == "transfer" and request.POST.get("confirm") == "transfer":
                    member = get_object_or_404(
                        workspace.members.select_related("user"),
                        user_id=request.POST.get("user"),
                        user__is_active=True,
                    )
                    workspace.owner = member.user
                    workspace.save(update_fields=["owner"])
                else:
                    raise Http404
                Audit.objects.create(
                    actor=request.user, action="workspace_changed", target=str(pk)
                )
            if (
                member_form.is_bound
                and member_form.errors
                or settings_form.is_bound
                and settings_form.errors
            ):
                pass
            else:
                messages.success(request, _("Espace d’équipe mis à jour."))
                return redirect("workspace", pk=pk)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    return render(
        request,
        "workspaces/detail.html",
        {
            "nav": "workspaces",
            "workspace": workspace,
            "is_owner": is_owner,
            "members": workspace.members.select_related("user"),
            "groups": workspace.groups.prefetch_related("members"),
            "collections": collections_for(request.user)
            .filter(workspace=workspace)
            .annotate(link_count=Count("links", distinct=True))
            .prefetch_related("files")
            .select_related("owner"),
            "member_form": member_form,
            "settings_form": settings_form,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def group(request, pk, group_id=None):
    workspace = get_object_or_404(Workspace, pk=pk, owner=request.user)
    item = (
        get_object_or_404(TeamGroup, pk=group_id, workspace=workspace)
        if group_id
        else None
    )
    if request.method == "POST" and request.POST.get("action") == "delete":
        if item is None or request.POST.get("confirm") != "delete":
            raise Http404
        with transaction.atomic():
            workspace = get_object_or_404(
                Workspace.objects.select_for_update(), pk=pk, owner=request.user
            )
            lock_workspace_accounts(workspace)
            get_object_or_404(TeamGroup, pk=group_id, workspace=workspace).delete()
            cancel_inaccessible_uploads(pk)
            Audit.objects.create(
                actor=request.user, action="group_changed", target=str(group_id)
            )
        return redirect("workspace", pk=pk)
    form = GroupForm(
        request.POST if request.method == "POST" else None,
        workspace=workspace,
        instance=item,
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            workspace = get_object_or_404(
                Workspace.objects.select_for_update(), pk=pk, owner=request.user
            )
            lock_workspace_accounts(workspace)
            item = (
                get_object_or_404(TeamGroup, pk=group_id, workspace=workspace)
                if group_id
                else None
            )
            form = GroupForm(request.POST, workspace=workspace, instance=item)
            if not form.is_valid():
                return render(
                    request,
                    "workspaces/form.html",
                    {"nav": "workspaces", "workspace": workspace, "form": form},
                )
            if item is None and workspace.groups.count() >= 100:
                form.add_error(
                    None, _("La limite de 100 groupes par espace est atteinte.")
                )
            else:
                form.save()
                cancel_inaccessible_uploads(pk)
                Audit.objects.create(
                    actor=request.user,
                    action="group_changed",
                    target=str(form.instance.pk),
                )
                return redirect("workspace", pk=pk)
    return render(
        request,
        "workspaces/form.html",
        {"nav": "workspaces", "workspace": workspace, "form": form},
    )


@login_required
@require_http_methods(["GET", "POST"])
def collection_settings(request, pk):
    collection = get_object_or_404(Collection, pk=pk, owner=request.user)
    workspace_form = forms.Form()
    workspace_form.fields["workspace"] = forms.ModelChoiceField(
        label=_("Espace d’équipe"),
        queryset=workspaces_for(request.user),
        required=False,
        initial=collection.workspace_id,
        empty_label=_("Espace personnel"),
    )
    transfer_form = MemberForm()
    group_form = forms.Form()
    for group in (
        TeamGroup.objects.filter(workspace_id=collection.workspace_id)
        if collection.workspace_id
        else []
    ):
        grant = collection.group_grants.filter(group=group).first()
        group_form.fields[str(group.pk)] = forms.ChoiceField(
            label=group.name,
            required=False,
            choices=[("", _("Aucun accès")), *CollectionAccess.ROLES],
            initial=grant.role if grant else "",
        )
    if request.method == "POST":
        try:
            action = request.POST.get("action")
            if action == "workspace":
                workspace_form.data, workspace_form.is_bound = request.POST, True
                if workspace_form.is_valid():
                    workspace = workspace_form.cleaned_data["workspace"]
                    attach_collection(
                        request.user, pk, workspace.pk if workspace else None
                    )
                    return redirect("collection_workspace", pk=pk)
            elif action == "transfer" and request.POST.get("confirm") == "transfer":
                transfer_form = MemberForm(request.POST)
                if transfer_form.is_valid():
                    recipient = get_object_or_404(
                        User,
                        email__iexact=transfer_form.cleaned_data["email"],
                        is_active=True,
                    )
                    transfer_collection(request.user, pk, recipient)
                    messages.success(
                        request,
                        _(
                            "Propriété transférée. Les fichiers et liens existants sont conservés."
                        ),
                    )
                    return redirect("collections")
            elif action == "groups":
                group_form.data, group_form.is_bound = request.POST, True
                if group_form.is_valid():
                    with transaction.atomic():
                        lock_workspace(collection)
                        lock_accounts(request.user, request.user.pk)
                        current = get_object_or_404(
                            Collection.objects.select_for_update(),
                            pk=pk,
                            owner=request.user,
                            workspace_id=collection.workspace_id,
                        )
                        for key, role in group_form.cleaned_data.items():
                            group = get_object_or_404(
                                TeamGroup, pk=key, workspace=current.workspace
                            )
                            if role:
                                GroupAccess.objects.update_or_create(
                                    group=group,
                                    collection=current,
                                    defaults={"role": role},
                                )
                            else:
                                GroupAccess.objects.filter(
                                    group=group, collection=current
                                ).delete()
                        cancel_inaccessible_uploads(current.workspace_id, current.pk)
                        Audit.objects.create(
                            actor=request.user,
                            action="collection_access_changed",
                            target=str(pk),
                        )
                    return redirect("collection_workspace", pk=pk)
            else:
                raise Http404
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    return render(
        request,
        "workspaces/collection.html",
        {
            "nav": "collections",
            "collection": collection,
            "workspace_form": workspace_form,
            "group_form": group_form,
            "transfer_form": transfer_form,
        },
    )
