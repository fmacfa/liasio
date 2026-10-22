from django.utils.translation import gettext_lazy as _
import csv
import io
import json
import logging
import smtplib
import shutil
import uuid
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.views import PasswordResetConfirmView
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.text import slugify
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import (
    require_GET,
    require_POST,
    require_http_methods,
    require_safe,
)

from .forms import (
    AccountForm,
    BoundedPasswordChangeForm,
    BoundedSetPasswordForm,
    BrandingForm,
    CollectionForm,
    InvitationForm,
    LinkForm,
    LoginForm,
    SetupForm,
    UnlockForm,
)
from .capacity import CapacityUnavailable
from .models import (
    Asset,
    Audit,
    Branding,
    Collection,
    Event,
    Instance,
    Invitation,
    ShareLink,
    User,
)
from .security import limited, rate_allowed, verify_capability
from .mfa import begin_login, complete_login
from .storage import (
    add_files,
    available_links,
    delete_assets,
    delete_collection,
    download_bundle,
)


def too_many(request):
    response = render(
        request,
        "error.html",
        {
            "title": _("Un instant…"),
            "detail": _("Trop de tentatives. Réessayez dans quelques minutes."),
        },
        status=429,
    )
    response["Retry-After"] = "900"
    return response


def unavailable(request):
    return render(
        request,
        "error.html",
        {
            "title": _("Ce lien n’est plus disponible"),
            "detail": _(
                "Il a expiré, a été révoqué ou a atteint sa limite de téléchargements. Contactez la personne qui vous l’a envoyé."
            ),
        },
        status=410,
    )


def staff_required(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            raise Http404
        return view(request, *args, **kwargs)

    return wrapped


@require_http_methods(["GET", "POST"])
@sensitive_post_parameters()
def setup(request):
    if (
        Instance.objects.filter(pk=1, initialized=True).exists()
        or User.objects.exists()
    ):
        return redirect("login")
    form = SetupForm(request.POST or None)
    if request.method == "POST":
        if limited(request, "setup", 5):
            return too_many(request)
        if form.is_valid():
            with transaction.atomic():
                instance = Instance.objects.select_for_update().get(pk=1)
                if instance.initialized or User.objects.exists():
                    return redirect("login")
                user = User.objects.create_superuser(
                    form.cleaned_data["email"],
                    form.cleaned_data["password"],
                    display_name=form.cleaned_data["display_name"],
                )
                Branding.objects.create(user=user, name=user.display_name)
                instance.initialized = True
                instance.save(update_fields=["initialized"])
                Audit.objects.create(actor=user, action="instance_initialized")
            login(request, user)
            return redirect("dashboard")
    return render(request, "account/setup.html", {"form": form})


@require_http_methods(["GET", "POST"])
@sensitive_post_parameters()
def sign_in(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if (
        not User.objects.exists()
        and not Instance.objects.filter(pk=1, initialized=True).exists()
    ):
        return redirect("setup")
    form = LoginForm(request.POST or None)
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()[:254]
        if limited(request, "login", 20) or not rate_allowed(
            "login-account", email, 10
        ):
            return too_many(request)
        if form.is_valid():
            user = authenticate(
                request,
                email=form.cleaned_data["email"].strip().lower(),
                password=form.cleaned_data["password"],
            )
            if user:
                if user.mfa_secret:
                    begin_login(request, user)
                    return redirect("mfa_challenge")
                complete_login(request, user)
                return redirect("dashboard")
            form.add_error(None, _("Adresse e-mail ou mot de passe incorrect."))
    return render(request, "account/login.html", {"form": form})


@require_POST
@login_required
def sign_out(request):
    logout(request)
    return redirect("login")


@require_http_methods(["GET", "POST"])
@sensitive_post_parameters()
def accept_invitation(request, token):
    identifier = verify_capability("invite", token)
    invitation = get_object_or_404(Invitation, pk=identifier)
    if not invitation.active:
        return unavailable(request)
    form = AccountForm(request.POST or None, email=invitation.email)
    if request.method == "POST":
        if limited(request, "invitation", 10):
            return too_many(request)
        if form.is_valid():
            with transaction.atomic():
                invitation = Invitation.objects.select_for_update().get(pk=identifier)
                if (
                    not invitation.active
                    or User.objects.filter(email__iexact=invitation.email).exists()
                ):
                    return unavailable(request)
                try:
                    with transaction.atomic():
                        user = User.objects.create_user(
                            invitation.email,
                            form.cleaned_data["password"],
                            display_name=form.cleaned_data["display_name"],
                        )
                except IntegrityError:
                    return unavailable(request)
                Branding.objects.create(user=user, name=user.display_name)
                invitation.accepted_at = timezone.now()
                invitation.save(update_fields=["accepted_at"])
                Audit.objects.create(
                    actor=user, action="invitation_accepted", target=str(invitation.pk)
                )
            login(request, user)
            return redirect("dashboard")
    return render(
        request, "account/invitation.html", {"form": form, "invitation": invitation}
    )


@require_http_methods(["GET", "POST"])
@sensitive_post_parameters()
def password_reset(request):
    form = PasswordResetForm(request.POST or None)
    if request.method == "POST":
        if limited(request, "password-reset", 5, 3600):
            return too_many(request)
        if form.is_valid():
            if settings.EMAIL_HOST:
                try:
                    form.save(
                        request=request,
                        use_https=settings.APP_URL.startswith("https://"),
                        domain_override=settings.APP_URL.split("://", 1)[1],
                        email_template_name="account/reset_email.txt",
                        subject_template_name="account/reset_subject.txt",
                        from_email=settings.DEFAULT_FROM_EMAIL,
                    )
                except (OSError, smtplib.SMTPException):
                    logging.getLogger("django").error(
                        "Password reset SMTP delivery failed; no account details logged."
                    )
            return render(request, "account/reset_sent.html")
    return render(
        request,
        "account/reset.html",
        {"form": form, "smtp_enabled": bool(settings.EMAIL_HOST)},
    )


@method_decorator(sensitive_post_parameters(), name="dispatch")
class ResetConfirmView(PasswordResetConfirmView):
    template_name = "account/reset_confirm.html"
    form_class = BoundedSetPasswordForm
    success_url = "/login/"

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST" and limited(request, "reset-confirm", 10):
            return too_many(request)
        return super().dispatch(request, *args, **kwargs)


@login_required
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters()
def account(request):
    form = BoundedPasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST":
        if limited(request, "password-change", 10):
            return too_many(request)
        if form.is_valid():
            try:
                user = form.save()
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                update_session_auth_hash(request, user)
                Audit.objects.create(actor=user, action="password_changed")
                messages.success(
                    request,
                    _("Mot de passe modifié. Les autres sessions seront déconnectées."),
                )
                return redirect("account")
    return render(request, "account/settings.html", {"form": form, "nav": "account"})


@login_required
@require_GET
def dashboard(request):
    collections = (
        request.user.collections.prefetch_related("files")
        .annotate(link_count=Count("links", distinct=True))
        .order_by("-updated_at", "-pk")
    )
    links = ShareLink.objects.filter(collection__owner=request.user)
    events = Event.objects.filter(link__collection__owner=request.user)
    return render(
        request,
        "dashboard.html",
        {
            "nav": "dashboard",
            "collections": collections[:4],
            "collection_count": collections.count(),
            "active_links": available_links(links).count(),
            "download_count": links.aggregate(total=Sum("downloads"))["total"] or 0,
            "opened_count": events.filter(kind="opened").count(),
            "events": events.select_related("link__collection")[:6],
        },
    )


@login_required
@require_GET
def collections(request):
    query = request.GET.get("q", "")[:120]
    items = (
        request.user.collections.prefetch_related("files")
        .annotate(link_count=Count("links", distinct=True))
        .order_by("-updated_at", "-pk")
    )
    if query:
        items = items.filter(
            Q(title__icontains=query) | Q(description__icontains=query)
        )
    page = Paginator(items, 24).get_page(request.GET.get("page"))
    return render(
        request,
        "collections/list.html",
        {"nav": "collections", "collections": page, "page": page, "query": query},
    )


@login_required
@require_http_methods(["GET", "POST"])
def collection_create(request):
    form = CollectionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if limited(request, "create-collection", 60, 3600):
            return too_many(request)
        with transaction.atomic():
            owner = User.objects.select_for_update().get(pk=request.user.pk)
            if not owner.is_active:
                raise Http404
            item = form.save(commit=False)
            item.owner = owner
            item.save()
        messages.success(request, _("Dossier créé. Ajoutez maintenant vos fichiers."))
        return redirect("collection", pk=item.pk)
    return render(
        request,
        "collections/form.html",
        {"nav": "collections", "form": form, "creating": True},
    )


@login_required
@require_GET
def collection_detail(request, pk):
    item = get_object_or_404(
        Collection.objects.prefetch_related("files", "links"), pk=pk, owner=request.user
    )
    return render(
        request,
        "collections/detail.html",
        {
            "nav": "collections",
            "collection": item,
            "max_file_mb": settings.MAX_FILE_BYTES // 1024**2,
            "max_upload_mb": settings.MAX_UPLOAD_BYTES // 1024**2,
        },
    )


@login_required
@require_GET
def upload_page(request):
    selected = request.GET.get("collection", "")
    item = None
    if selected:
        try:
            identifier = uuid.UUID(selected)
        except ValueError:
            raise Http404 from None
        item = get_object_or_404(Collection, pk=identifier, owner=request.user)
    return render(
        request,
        "upload.html",
        {
            "nav": "upload",
            "destinations": request.user.collections.all(),
            "collection": item,
            "max_file_mb": settings.MAX_FILE_BYTES // 1024**2,
            "max_upload_mb": settings.MAX_UPLOAD_BYTES // 1024**2,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def collection_edit(request, pk):
    item = get_object_or_404(Collection, pk=pk, owner=request.user)
    form = CollectionForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Présentation mise à jour."))
        return redirect("collection", pk=pk)
    return render(
        request,
        "collections/form.html",
        {"nav": "collections", "form": form, "collection": item},
    )


@login_required
@require_POST
def collection_upload(request, pk):
    item = get_object_or_404(Collection, pk=pk, owner=request.user)
    try:
        # Multipart parsing happens after authentication and ownership checks
        # when CSRF is supplied in a header by the enhanced upload UI.
        uploads = request.FILES.getlist("files")
        if getattr(request, "upload_limit_exceeded", False):
            raise ValidationError(
                _("Envoi trop volumineux. Aucun fichier n’a été conservé.")
            )
        paths_json = request.POST.get("paths", "")
        paths = json.loads(paths_json) if paths_json else [f.name for f in uploads]
        if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
            raise ValidationError(_("Liste des chemins invalide."))
        add_files(request.user, item.pk, uploads, paths, capacity_reserved=True)
        messages.success(
            request,
            _("%(count)s fichier(s) ajouté(s) au dossier.") % {"count": len(uploads)},
        )
    except (ValidationError, ValueError) as exc:
        messages.error(
            request,
            " ".join(exc.messages)
            if isinstance(exc, ValidationError)
            else _("Envoi invalide."),
        )
    return redirect("collection", pk=pk)


@login_required
@require_http_methods(["GET", "POST"])
def collection_delete(request, pk):
    item = get_object_or_404(Collection, pk=pk, owner=request.user)
    if request.method == "POST":
        delete_collection(request.user, pk)
        messages.success(
            request,
            _("Dossier placé dans la corbeille. Tous ses liens ont été révoqués."),
        )
        return redirect("collections")
    return render(
        request, "collections/delete.html", {"nav": "collections", "collection": item}
    )


@login_required
@require_http_methods(["GET", "POST"])
def link_create(request, pk):
    item = get_object_or_404(Collection, pk=pk, owner=request.user)
    form = LinkForm(
        request.POST or None,
        initial={"expires_at": timezone.localtime() + timedelta(days=7)},
    )
    if request.method == "POST" and form.is_valid():
        if limited(request, "link-create", 60, 3600):
            return too_many(request)
        with transaction.atomic():
            # Lock the collection so upload and link creation cannot race.
            owner = User.objects.select_for_update().get(pk=request.user.pk)
            if not owner.is_active:
                raise Http404
            locked = Collection.objects.select_for_update().get(
                pk=pk, owner=request.user
            )
            if not locked.files.exists():
                form.add_error(
                    None, _("Ajoutez au moins un fichier avant de créer un lien.")
                )
            elif (
                settings.ANTIVIRUS_REQUIRED
                and locked.files.filter(scanned=False).exists()
            ):
                form.add_error(
                    None,
                    _(
                        "Des fichiers n’ont pas été analysés par l’antivirus. Importez-les à nouveau dans un dossier avec l’antivirus activé."
                    ),
                )
            else:
                link = form.save(commit=False)
                link.collection = locked
                if form.cleaned_data["password"]:
                    link.password_hash = make_password(form.cleaned_data["password"])
                link.save()
                Event.objects.create(link=link, kind="created")
                Audit.objects.create(
                    actor=request.user, action="link_created", target=str(link.pk)
                )
                messages.success(
                    request, _("Lien créé. Vous pouvez le copier et le partager.")
                )
                return redirect("links")
    return render(
        request, "links/form.html", {"nav": "links", "form": form, "collection": item}
    )


@login_required
@require_GET
def links(request):
    query = request.GET.get("q", "")[:120]
    items = (
        ShareLink.objects.filter(collection__owner=request.user)
        .select_related("collection")
        .annotate(open_count=Count("events", filter=Q(events__kind="opened")))
        .order_by("-created_at", "-pk")
    )
    if query:
        items = items.filter(
            Q(label__icontains=query) | Q(collection__title__icontains=query)
        )
    if request.GET.get("status") == "active":
        items = available_links(items)
    page = Paginator(items, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "links/list.html",
        {
            "nav": "links",
            "links": page,
            "page": page,
            "query": query,
            "status": request.GET.get("status", ""),
        },
    )


@login_required
@require_POST
def link_revoke(request, pk):
    link = get_object_or_404(ShareLink, pk=pk, collection__owner=request.user)
    with transaction.atomic():
        changed = ShareLink.objects.filter(pk=pk, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
        if changed:
            Event.objects.create(link=link, kind="revoked")
            Audit.objects.create(
                actor=request.user, action="link_revoked", target=str(pk)
            )
    messages.success(
        request, _("Lien révoqué. Aucun nouveau téléchargement ne sera autorisé.")
    )
    return redirect("links")


@login_required
@require_GET
def activity(request):
    events = Event.objects.filter(link__collection__owner=request.user).select_related(
        "link__collection"
    )
    link_id = request.GET.get("link", "")
    if link_id:
        try:
            events = events.filter(link_id=link_id)
        except ValidationError:
            raise Http404 from None
    if request.GET.get("export") == "csv":
        # Cap a single export; the UI always uses pagination.
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [_("Date (ISO 8601 UTC)"), _("Événement"), _("Lien"), _("Dossier")]
        )

        def safe_csv(value):
            value = str(value)
            return (
                "'" + value
                if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n"))
                else value
            )

        for event in events[:10000]:
            writer.writerow(
                [
                    event.created_at.isoformat(),
                    event.get_kind_display(),
                    safe_csv(event.link.label),
                    safe_csv(event.link.collection.title),
                ]
            )
        response = HttpResponse(
            "\ufeff" + output.getvalue(), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = (
            'attachment; filename="linkvault-activity.csv"'
        )
        return response
    page = Paginator(events, 30).get_page(request.GET.get("page"))
    return render(
        request,
        "activity.html",
        {
            "nav": "activity",
            "events": page,
            "page": page,
            "link_id": link_id,
            "retention_days": settings.EVENT_RETENTION_DAYS,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def branding(request):
    item, created = Branding.objects.get_or_create(
        user=request.user, defaults={"name": request.user.display_name}
    )
    form = BrandingForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Votre page de partage a été personnalisée."))
        return redirect("branding")
    return render(
        request, "branding.html", {"nav": "branding", "form": form, "branding": item}
    )


@login_required
@require_GET
def storage(request):
    query = request.GET.get("q", "")[:120]
    order = request.GET.get("sort", "largest")
    ordering = {
        "largest": "-size",
        "smallest": "size",
        "newest": "-created_at",
        "name": "path",
    }.get(order, "-size")
    owned = Asset.objects.filter(collection__owner=request.user)
    items = owned.select_related("collection")
    if query:
        items = items.filter(
            Q(path__icontains=query) | Q(collection__title__icontains=query)
        )
    page = Paginator(items.order_by(ordering, "pk"), 30).get_page(
        request.GET.get("page")
    )
    context = {
        "nav": "storage",
        "files": page,
        "page": page,
        "query": query,
        "sort": order,
        "file_count": owned.count(),
        "collection_count": request.user.collections.count(),
        "remaining": max(
            0,
            request.user.quota_bytes
            - request.user.used_bytes
            - request.user.reserved_bytes,
        ),
    }
    if request.user.is_staff:
        disk = shutil.disk_usage(settings.MEDIA_ROOT)
        context.update(
            disk=disk,
            disk_percent=round(disk.used * 100 / disk.total),
            server_file_count=Asset.objects.count(),
            server_bytes=Asset.objects.aggregate(total=Sum("size"))["total"] or 0,
            storage_members=User.objects.order_by("-used_bytes"),
        )
    return render(request, "storage.html", context)


@login_required
@require_POST
def storage_delete(request):
    raw = request.POST.getlist("selected")
    try:
        if not raw or len(raw) > 100:
            raise ValueError
        identifiers = list({uuid.UUID(value) for value in raw})
    except ValueError:
        messages.error(request, _("Sélectionnez entre 1 et 100 fichiers."))
        return redirect("storage")
    files = list(
        Asset.objects.filter(
            pk__in=identifiers, collection__owner=request.user
        ).select_related("collection")
    )
    if len(files) != len(identifiers):
        raise Http404
    if request.POST.get("confirm") == "delete":
        try:
            count, deleted_bytes = delete_assets(request.user, identifiers)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(
                request,
                _(
                    "%(count)s fichier(s) placé(s) dans la corbeille. Les liens des dossiers concernés ont été révoqués."
                )
                % {"count": count},
            )
        return redirect("storage")
    affected = available_links(
        ShareLink.objects.filter(collection_id__in={f.collection_id for f in files})
    ).count()
    return render(
        request,
        "storage_delete.html",
        {
            "nav": "storage",
            "files": files,
            "freed": sum(f.size for f in files),
            "affected": affected,
        },
    )


@staff_required
@require_http_methods(["GET", "POST"])
def team(request):
    form = InvitationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if limited(request, "invite-create", 30, 3600):
            return too_many(request)
        with transaction.atomic():
            Invitation.objects.filter(
                email=form.cleaned_data["email"],
                accepted_at__isnull=True,
                revoked_at__isnull=True,
            ).update(revoked_at=timezone.now())
            invitation = Invitation.objects.create(
                email=form.cleaned_data["email"], invited_by=request.user
            )
            Audit.objects.create(
                actor=request.user,
                action="invitation_created",
                target=str(invitation.pk),
            )
        messages.success(
            request,
            _("Invitation créée pour 7 jours. Copiez le lien et envoyez-le au membre."),
        )
        return redirect("team")
    return render(
        request,
        "team.html",
        {
            "nav": "team",
            "form": form,
            "members": User.objects.order_by("date_joined"),
            "invitations": Invitation.objects.filter(
                accepted_at__isnull=True,
                revoked_at__isnull=True,
                expires_at__gt=timezone.now(),
            )[:50],
            "audits": Audit.objects.select_related("actor")[:10],
        },
    )


@staff_required
@require_POST
def invitation_revoke(request, pk):
    changed = Invitation.objects.filter(
        pk=pk, accepted_at__isnull=True, revoked_at__isnull=True
    ).update(revoked_at=timezone.now())
    if changed:
        Audit.objects.create(
            actor=request.user, action="invitation_revoked", target=str(pk)
        )
    messages.success(request, _("Invitation révoquée."))
    return redirect("team")


@staff_required
@require_POST
def member_toggle(request, pk):
    with transaction.atomic():
        member = get_object_or_404(User.objects.select_for_update(), pk=pk)
        if member.is_staff or member.pk == request.user.pk:
            raise Http404
        member.is_active = not member.is_active
        # Also invalidate password-verified sessions awaiting a second factor.
        member.mfa_version = uuid.uuid4()
        member.save(update_fields=["is_active", "mfa_version"])
        # Existing sessions must not become valid again after reactivation.
        from django.contrib.sessions.models import Session

        for session in Session.objects.filter(
            expire_date__gt=timezone.now()
        ).iterator():
            if str(session.get_decoded().get("_auth_user_id", "")) == str(member.pk):
                session.delete()
        if not member.is_active:
            from .models import APIToken, Webhook
            from .webhooks import pause_hook

            APIToken.objects.filter(owner=member, revoked_at__isnull=True).update(
                revoked_at=timezone.now()
            )
            for hook in (
                Webhook.objects.select_for_update().filter(owner=member).order_by("pk")
            ):
                pause_hook(hook)
            ShareLink.objects.filter(
                collection__owner=member, revoked_at__isnull=True
            ).update(revoked_at=timezone.now())
        Audit.objects.create(
            actor=request.user,
            action="member_activated" if member.is_active else "member_suspended",
            target=str(pk),
        )
    messages.success(
        request,
        _("Compte mis à jour. Une suspension révoque aussi les liens du membre."),
    )
    return redirect("team")


def public_link(token):
    identifier = verify_capability("share", token)
    return get_object_or_404(
        ShareLink.objects.select_related(
            "collection__owner__branding"
        ).prefetch_related("collection__files"),
        pk=identifier,
    )


def can_open(request, link):
    grant = request.session.get("grants", {}).get(str(link.pk), 0)
    return not link.password_hash or grant > timezone.now().timestamp()


@require_safe
def share(request, token):
    if limited(request, "share-page", 120, 60):
        return too_many(request)
    link = public_link(token)
    if link.status_code != "active" or not link.collection.owner.is_active:
        return unavailable(request)
    if not can_open(request, link):
        return render(
            request, "share/unlock.html", {"form": UnlockForm(), "token": token}
        )
    now = timezone.now().timestamp()
    seen = request.session.get("seen_links", {})
    if request.method == "GET" and seen.get(str(link.pk), 0) < now - 1800:
        with transaction.atomic():
            Event.objects.create(link=link, kind="opened")
        seen = {k: v for k, v in seen.items() if v > now - 1800}
        seen[str(link.pk)] = now
        request.session["seen_links"] = dict(list(seen.items())[-100:])
    return render(
        request,
        "share/page.html",
        {
            "link": link,
            "collection": link.collection,
            "branding": getattr(link.collection.owner, "branding", None),
            "token": token,
        },
    )


@require_POST
@sensitive_post_parameters()
def unlock(request, token):
    link = public_link(token)
    if link.status_code != "active" or not link.collection.owner.is_active:
        return unavailable(request)
    if limited(request, "unlock", 20) or not rate_allowed(
        "unlock-link", str(link.pk), 50
    ):
        return too_many(request)
    form = UnlockForm(request.POST)
    if form.is_valid() and (
        not link.password_hash
        or check_password(form.cleaned_data["password"], link.password_hash)
    ):
        now = timezone.now().timestamp()
        grants = {k: v for k, v in request.session.get("grants", {}).items() if v > now}
        grants[str(link.pk)] = now + 3600
        request.session["grants"] = dict(list(grants.items())[-50:])
        request.session.cycle_key()
        return redirect("share", token=token)
    form.add_error(None, _("Mot de passe incorrect."))
    return render(
        request, "share/unlock.html", {"form": form, "token": token}, status=403
    )


@require_POST
def download(request, token):
    if limited(request, "download", 20, 900):
        return too_many(request)
    link = public_link(token)
    if link.status_code != "active" or not link.collection.owner.is_active:
        return unavailable(request)
    if not can_open(request, link):
        return render(
            request,
            "share/unlock.html",
            {"form": UnlockForm(), "token": token},
            status=403,
        )
    try:
        bundle = download_bundle(link)
    except CapacityUnavailable:
        return render(
            request,
            "error.html",
            {
                "title": _("Transfert en attente"),
                "detail": _(
                    "Le serveur manque d’espace temporaire. Réessayez plus tard ou contactez l’expéditeur."
                ),
            },
            status=503,
        )
    except ValidationError:
        return unavailable(request)
    except OSError:
        return render(
            request,
            "error.html",
            {
                "title": _("Transfert indisponible"),
                "detail": _(
                    "Le fichier n’a pas pu être préparé. Réessayez plus tard ou contactez l’expéditeur."
                ),
            },
            status=503,
        )
    return FileResponse(
        bundle,
        as_attachment=True,
        filename=(slugify(link.collection.title) or "dossier") + ".zip",
        content_type="application/octet-stream",
    )


@require_GET
def health(request):
    Instance.objects.filter(pk=1).exists()
    return HttpResponse("ok", content_type="text/plain")


def error404(request, exception=None):
    return render(
        request,
        "error.html",
        {
            "title": _("Page introuvable"),
            "detail": _("Cette adresse ne correspond à aucune page disponible."),
        },
        status=404,
    )


def error500(request):
    return render(
        request,
        "error.html",
        {
            "title": _("Une erreur est survenue"),
            "detail": _("Réessayez dans quelques instants."),
        },
        status=500,
    )
