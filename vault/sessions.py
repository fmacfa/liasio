"""Session visibility without IP tracking, and workspace MFA enforcement."""

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.sessions.models import Session
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from .models import Audit, LoginSession, Workspace
from .security import limited


def requires_mfa(user):
    return (
        Workspace.objects.filter(require_mfa=True, owner__is_active=True)
        .filter(Q(owner=user) | Q(members__user=user))
        .exists()
    )


class SessionInventoryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.user.is_authenticated
            and not request.user.mfa_secret
            and requires_mfa(request.user)
        ):
            permitted = ("/account/", "/logout/", "/language/", "/jsi18n/", "/help/")
            if not request.path.startswith(permitted) and not request.path.startswith(
                ("/api/", "/s/", "/file-requests/", "/health/", "/ready/")
            ):
                return redirect("mfa_settings")
        response = self.get_response(request)
        key = request.session.session_key
        if (
            request.user.is_authenticated
            and not getattr(request, "api_token", None)
            and key
        ):
            with transaction.atomic():
                if Session.objects.select_for_update().filter(pk=key).exists():
                    item, created = LoginSession.objects.get_or_create(
                        session_id=key,
                        defaults={
                            "user": request.user,
                            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:240],
                        },
                    )
                    if (
                        item.user_id == request.user.pk
                        and not created
                        and item.last_seen < timezone.now() - timedelta(minutes=5)
                    ):
                        LoginSession.objects.filter(pk=item.pk).update(
                            last_seen=timezone.now()
                        )
        return response


@login_required
@require_http_methods(["GET", "POST"])
def index(request):
    if request.method == "POST":
        if limited(request, "session-revoke", 30, 3600):
            from .views import too_many

            return too_many(request)
        with transaction.atomic():
            rows = LoginSession.objects.filter(user=request.user).exclude(
                session_id=request.session.session_key
            )
            if request.POST.get("action") == "revoke":
                row = get_object_or_404(rows, pk=request.POST.get("session"))
                Session.objects.filter(pk=row.session_id).delete()
            elif request.POST.get("action") == "revoke_others":
                Session.objects.filter(pk__in=rows.values("session_id")).delete()
            else:
                from django.http import Http404

                raise Http404
            Audit.objects.create(actor=request.user, action="sessions_revoked")
        messages.success(request, _("Sessions déconnectées."))
        return redirect("sessions")
    items = list(
        LoginSession.objects.filter(
            user=request.user, session__expire_date__gt=timezone.now()
        ).select_related("session")
    )
    for item in items:
        item.is_current = item.session_id == request.session.session_key
    return render(
        request,
        "account/sessions.html",
        {
            "nav": "account",
            "sessions": items,
            "mfa_required": requires_mfa(request.user),
        },
    )
