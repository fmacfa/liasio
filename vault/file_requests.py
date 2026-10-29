"""Upload-only capabilities. A separate private collection is created per submission."""

from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods

from . import resumable
from .models import Audit, Collection, FileRequest, UploadSession, User
from .recipients import browser_nonce, digest
from .security import limited, rate_allowed, verify_capability


def active_request(token):
    item = get_object_or_404(
        FileRequest.objects.select_related("owner"),
        pk=verify_capability("file-request", token),
    )
    if not item.active:
        raise Http404
    return item


def start_collection(request, size):
    """Called inside the upload admission transaction, after the capacity/instance lock."""
    owner = User.objects.select_for_update().get(pk=request.file_request.owner_id)
    item = FileRequest.objects.select_for_update().get(
        pk=request.file_request.pk, owner=owner
    )
    pending = UploadSession.objects.filter(file_request=item, state="pending")
    reserved = pending.aggregate(total=Sum("size"))["total"] or 0
    if (
        not item.active
        or item.received_count + pending.count() >= item.max_submissions
        or item.received_bytes + reserved + size > item.max_bytes
    ):
        raise ValidationError(
            _("Cette demande a expiré ou sa limite de réception est atteinte.")
        )
    if (
        not owner.is_active
        or owner.used_bytes + owner.reserved_bytes + size > owner.quota_bytes
    ):
        raise ValidationError(
            _("L’espace de réception est insuffisant. Contactez l’expéditeur.")
        )
    folder = Collection.objects.create(
        owner=owner,
        title=item.title[:90] + " · " + timezone.now().strftime("%Y-%m-%d %H:%M"),
        file_request=item,
    )
    return folder.pk


def validate_session(session):
    if session.file_request_id:
        item = session.file_request
        if (
            not item.active
            or item.owner_id != session.owner_id
            or session.collection.owner_id != item.owner_id
        ):
            raise ValidationError(_("Cette demande de fichiers n’est plus disponible."))


def complete(session):
    if not session.file_request_id:
        return
    item = FileRequest.objects.select_for_update().get(pk=session.file_request_id)
    item.received_bytes += session.size
    item.received_count += 1
    item.save(update_fields=["received_bytes", "received_count"])
    Audit.objects.create(action="file_request_received", target=str(item.pk))


@require_GET
def public(request, token):
    item = active_request(token)
    if limited(request, "file-request-page", 120, 60):
        from .views import too_many

        return too_many(request)
    browser_nonce(request)
    return render(
        request,
        "requests/public.html",
        {
            "file_request": item,
            "token": token,
            "done": request.GET.get("done") == "1",
            "max_file_mb": settings.MAX_FILE_BYTES // 1024**2,
            "max_upload_mb": min(settings.MAX_UPLOAD_BYTES, item.max_bytes) // 1024**2,
        },
    )


@require_http_methods(["GET", "POST"])
@resumable.json_errors
def upload(request, token, action, pk=None, part_id=None):
    item = active_request(token)
    if action in ("start", "chunk", "finish", "cancel") and request.method != "POST":
        raise Http404
    if action in ("pending", "status") and request.method != "GET":
        raise Http404
    browser = digest(browser_nonce(request))
    # No account login is created. Authority is scoped before invoking the shared ingestion pipeline.
    if action != "chunk" and not rate_allowed(
        "request-" + action, str(item.pk), 60, 3600
    ):
        return JsonResponse(
            {"error": _("Trop de demandes. Réessayez plus tard.")}, status=429
        )
    scoped = UploadSession.objects.filter(
        file_request=item, browser_digest=browser, owner=item.owner
    )
    if pk:
        get_object_or_404(scoped, pk=pk)
    if action == "pending":
        return JsonResponse(
            {
                "sessions": [
                    resumable.describe(s)
                    for s in scoped.filter(
                        state="pending", expires_at__gt=timezone.now()
                    )
                ]
            }
        )
    original = request.user
    request.user = item.owner
    request.file_request = item
    request.file_request_browser = browser
    try:
        if action == "start":
            response = resumable.start(request, None)
        elif action == "chunk":
            response = resumable.chunk(request, pk, part_id)
        elif action in ("status", "finish", "cancel"):
            response = getattr(resumable, action)(request, pk)
        else:
            raise Http404
        if action == "finish" and response.status_code == 200:
            return JsonResponse(
                {"state": "complete", "redirect": item.public_url + "?done=1"}
            )
        return response
    finally:
        request.user = original
        del request.file_request
        del request.file_request_browser


class RequestForm(forms.Form):
    title = forms.CharField(label=_("Titre"), max_length=120)
    description = forms.CharField(
        label=_("Instructions pour le client"),
        max_length=2000,
        required=False,
        widget=forms.Textarea,
    )
    days = forms.IntegerField(
        label=_("Validité en jours"),
        min_value=1,
        max_value=365,
        initial=7,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "pattern": "[0-9]*"}),
    )
    max_mb = forms.IntegerField(
        label=_("Volume total maximal en Mio"),
        min_value=1,
        initial=1024,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "pattern": "[0-9]*"}),
    )
    max_submissions = forms.IntegerField(
        label=_("Nombre maximal de dépôts"),
        min_value=1,
        max_value=200,
        initial=20,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "pattern": "[0-9]*"}),
    )


@login_required
@require_http_methods(["GET", "POST"])
def index(request):
    form = RequestForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        if limited(request, "request-create", 10, 3600):
            from .views import too_many

            return too_many(request)
        with transaction.atomic():
            owner = User.objects.select_for_update().get(
                pk=request.user.pk, is_active=True
            )
            if form.cleaned_data["max_mb"] * 1024**2 > owner.quota_bytes:
                form.add_error(
                    "max_mb", _("La limite dépasse le quota de votre compte.")
                )
            elif FileRequest.objects.filter(owner=owner).count() >= 100:
                form.add_error(
                    None,
                    _(
                        "Limite de 100 demandes atteinte. Supprimez une ancienne demande."
                    ),
                )
            else:
                item = FileRequest.objects.create(
                    owner=owner,
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data["description"],
                    max_bytes=form.cleaned_data["max_mb"] * 1024**2,
                    max_submissions=form.cleaned_data["max_submissions"],
                    expires_at=timezone.now()
                    + timedelta(days=form.cleaned_data["days"]),
                )
                Audit.objects.create(
                    actor=owner, action="file_request_created", target=str(item.pk)
                )
                return redirect("file_request", pk=item.pk)
    return render(
        request,
        "requests/index.html",
        {
            "nav": "requests",
            "form": form,
            "requests": FileRequest.objects.filter(owner=request.user).order_by(
                "-created_at"
            ),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def detail(request, pk):
    item = get_object_or_404(FileRequest, owner=request.user, pk=pk)
    if request.method == "POST":
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            item = FileRequest.objects.select_for_update().get(pk=pk)
            item.revoked_at = timezone.now()
            item.save(update_fields=["revoked_at"])
            for session in UploadSession.objects.filter(
                file_request=item, state="pending"
            ):
                resumable.cancel_session(request.user, session.pk)
            if request.POST.get("action") == "delete":
                item.delete()  # Submitted collections remain owned by their current owner.
            Audit.objects.create(
                actor=request.user, action="file_request_revoked", target=str(pk)
            )
        return redirect("file_requests")
    return render(
        request,
        "requests/detail.html",
        {
            "nav": "requests",
            "file_request": item,
            "submissions": item.submissions.filter(owner=request.user),
        },
    )
