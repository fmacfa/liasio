"""Instance health, quota administration and deduplicated operational alerts."""

from django.utils.translation import gettext_lazy as _
import hmac
import shutil
import smtplib
import socket
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.db import DatabaseError, transaction
from django.db.models import Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

from .models import Asset, Audit, Instance, OperationalAlert, ServiceState, User
from .views import staff_required


def antivirus_ready():
    if not settings.CLAMAV_HOST:
        return not settings.ANTIVIRUS_REQUIRED
    try:
        with socket.create_connection(
            (settings.CLAMAV_HOST, settings.CLAMAV_PORT), timeout=3
        ) as client:
            client.settimeout(3)
            client.sendall(b"zPING\0")
            return client.recv(64) == b"PONG\0"
    except OSError:
        return False


def snapshot():
    disk = shutil.disk_usage(settings.DATA_DIR)
    now = timezone.now()
    try:
        database = Instance.objects.filter(pk=1).exists()
        states = {state.key: state for state in ServiceState.objects.all()}
        heartbeat = states.get("maintenance")
        backup = states.get("backup_verified")
        maintenance = bool(
            heartbeat and (now - heartbeat.updated_at).total_seconds() < 900
        )
        backup_date = (
            parse_datetime(backup.data.get("snapshot_created_at", ""))
            if backup
            else None
        )
        backup_recent = bool(
            backup_date
            and timezone.is_aware(backup_date)
            and 0
            <= (now - backup_date).total_seconds()
            < settings.BACKUP_MAX_AGE_HOURS * 3600
        )
        totals = User.objects.aggregate(
            stored=Sum("used_bytes"), reserved=Sum("reserved_bytes")
        )
    except DatabaseError:
        database, maintenance, backup_recent, totals = False, False, False, {}
    av = antivirus_ready()
    return {
        "version": settings.APP_VERSION,
        "database": database,
        "antivirus": av,
        "maintenance": maintenance,
        "backup_verified_recently": backup_recent,
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
        "disk_used_percent": round(disk.used * 100 / disk.total, 1),
        "stored_bytes": totals.get("stored") or 0,
        "reserved_bytes": totals.get("reserved") or 0,
        "ready": database and av and disk.free >= settings.MIN_FREE_DISK_BYTES,
    }


def monitor():
    status = snapshot()
    wanted = {}
    if (
        status["disk_used_percent"] >= 85
        or status["disk_free_bytes"] < settings.MIN_FREE_DISK_BYTES
    ):
        wanted["disk"] = _(
            "Le disque dépasse 85 % d’utilisation ou sa réserve minimale. Libérez de l’espace."
        )
    if not status["antivirus"]:
        wanted["antivirus"] = _(
            "L’antivirus ne répond plus. Les nouveaux fichiers ne peuvent pas être validés."
        )
    if not status["maintenance"]:
        wanted["maintenance"] = _(
            "La maintenance ne signale plus son activité depuis plus de quinze minutes."
        )
    if not status["backup_verified_recently"]:
        wanted["backup"] = _(
            "Aucune restauration de sauvegarde vérifiée récemment. Consultez le guide d’exploitation."
        )
    for user in User.objects.filter(is_active=True):
        if user.used_bytes + user.reserved_bytes >= user.quota_bytes * 0.9:
            wanted[f"quota-{user.pk}"] = _(
                "Le compte n° %(id)s atteint au moins 90 %% de son quota (envois réservés compris)."
            ) % {"id": user.pk}
    now = timezone.now()
    for key, message in wanted.items():
        alert, created = OperationalAlert.objects.get_or_create(
            key=key, defaults={"message": message}
        )
        if not alert.active:
            alert.notified_at = None
        alert.active, alert.message = True, message
        if not alert.notified_at and settings.MONITOR_EMAIL and settings.EMAIL_HOST:
            try:
                send_mail(
                    _("[Liasio] Intervention nécessaire"),
                    message + "\n" + settings.APP_URL + "/operations/",
                    settings.DEFAULT_FROM_EMAIL,
                    [settings.MONITOR_EMAIL],
                )
                alert.notified_at = now
            except (OSError, smtplib.SMTPException):
                pass  # Retry next cycle; no credentials or recipient in logs.
        alert.save()
    OperationalAlert.objects.exclude(pk__in=wanted).filter(active=True).update(
        active=False, updated_at=now
    )
    ServiceState.objects.update_or_create(key="health", defaults={"data": status})
    return status


@staff_required
@require_GET
def index(request):
    health = ServiceState.objects.filter(key="health").first()
    disk = shutil.disk_usage(settings.DATA_DIR)
    return render(
        request,
        "operations.html",
        {
            "nav": "operations",
            "health": health,
            "alerts": OperationalAlert.objects.filter(active=True).order_by("key"),
            "disk": disk,
            "members": User.objects.order_by("-used_bytes"),
            "file_count": Asset.all_objects.count(),
            "trash_bytes": Asset.all_objects.filter(deleted_at__isnull=False).aggregate(
                total=Sum("size")
            )["total"]
            or 0,
            "backup": ServiceState.objects.filter(key="backup_verified").first(),
            "mail_enabled": bool(settings.MONITOR_EMAIL and settings.EMAIL_HOST),
        },
    )


@staff_required
@require_POST
def quota(request, pk):
    try:
        value = Decimal(request.POST.get("quota_gib", ""))
        if not value.is_finite() or value < 0 or value > 1048576:
            raise ValueError
        quota_bytes = int(value * 1024**3)
    except (InvalidOperation, ValueError):
        messages.error(
            request, _("Indiquez un quota valide, entre 0 et 1 048 576 Gio.")
        )
        return redirect("operations")
    with transaction.atomic():
        user = get_object_or_404(User.objects.select_for_update(), pk=pk)
        if quota_bytes < user.used_bytes + user.reserved_bytes:
            messages.error(
                request,
                _(
                    "Le quota doit couvrir les fichiers, la corbeille et les envois en attente."
                ),
            )
        else:
            user.quota_bytes = quota_bytes
            user.save(update_fields=["quota_bytes"])
            Audit.objects.create(
                actor=request.user, action="quota_changed", target=str(pk)
            )
            messages.success(request, _("Quota mis à jour."))
    return redirect("operations")


@require_GET
def readiness(request):
    token = settings.MONITORING_TOKEN
    supplied = request.headers.get("Authorization", "")
    if len(token) < 32 or not hmac.compare_digest(
        supplied.encode(), ("Bearer " + token).encode()
    ):
        raise Http404
    status = snapshot()
    return JsonResponse(status, status=200 if status["ready"] else 503)
