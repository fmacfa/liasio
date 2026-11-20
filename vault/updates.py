"""Administrator controls and the limited job mailbox polled by the host."""

from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.http import HttpResponse
from django.utils import timezone
from django.utils.timezone import now as utcnow
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from . import release_channel
from .models import ServiceState
from .views import staff_required

CHECK = "update_check"
HOST = "update_host"
POLICY = "update_policy"
JOB = "update_job"


def current():
    return "v" + settings.APP_VERSION


def data(key):
    return (
        ServiceState.objects.filter(pk=key).values_list("data", flat=True).first() or {}
    )


def locked(key):
    return ServiceState.objects.select_for_update().get_or_create(pk=key)[0]


def fresh(timestamp, age):
    stamp = parse_datetime(timestamp) if isinstance(timestamp, str) else None
    return bool(
        stamp and timezone.is_aware(stamp) and timedelta(0) <= utcnow() - stamp < age
    )


def check():
    now = utcnow().isoformat()
    with transaction.atomic():
        row = locked(CHECK)
        if fresh(row.data.get("attempt"), timedelta(minutes=1)):
            return
        row.data = {**row.data, "attempt": now}
        row.save()
    try:
        result = {
            "tag": release_channel.latest_release()["tag_name"],
            "checked": now,
            "error": False,
        }
    except ValueError:
        result = {"error": True}
    with transaction.atomic():
        row = locked(CHECK)
        if row.data.get("attempt") == now:
            row.data.update(result)
            row.save()


def available(info):
    return bool(
        info.get("tag")
        and release_channel.version(info["tag"]) > release_channel.version(current())
    )


def connected():
    return fresh(data(HOST).get("seen"), timedelta(minutes=5))


def pending(job):
    return job.get("state") in {"queued", "installing"}


def make_job(tag, automatic):
    return {"id": uuid4().hex, "tag": tag, "state": "queued", "automatic": automatic}


@staff_required
@require_GET
def index(request):
    info, job = data(CHECK), data(JOB)
    online = connected()
    return render(
        request,
        "updates.html",
        {
            "nav": "operations",
            "current": current(),
            "info": info,
            "checked": parse_datetime(info["checked"]) if info.get("checked") else None,
            "newer": available(info),
            "online": online,
            "job": job,
            "can_install": online
            and available(info)
            and not info.get("error")
            and not pending(job),
            "busy": pending(job),
            "auto": data(POLICY).get("enabled", False),
        },
    )


@staff_required
@require_POST
def action(request):
    action = request.POST.get("action")
    if action == "check":
        check()
    elif action in {"enable", "disable"} and (connected() or action == "disable"):
        with transaction.atomic():
            row = locked(POLICY)
            row.data["enabled"] = action == "enable"
            row.save()
            if action == "disable":
                job = locked(JOB)
                if job.data.get("automatic") and job.data.get("state") == "queued":
                    job.data["state"] = "cancelled"
                    job.save()
    elif action == "install":
        with transaction.atomic():
            job = locked(JOB)
            info = data(CHECK)
            if (
                connected()
                and not pending(job.data)
                and available(info)
                and not info.get("error")
                and fresh(info.get("checked"), timedelta(days=1))
                and request.POST.get("tag") == info["tag"]
            ):
                job.data = make_job(info["tag"], False)
                job.save()
            else:
                messages.error(
                    request,
                    _(
                        "Vérifiez la version disponible et l’état de l’agent avant de relancer l’installation."
                    ),
                )
    return redirect("updates")


def poll():
    """The host calls once a minute. A daily check is due after 03:00 UTC."""
    now = utcnow()
    today = now.date().isoformat()
    with transaction.atomic():
        host = locked(HOST)
        due = now.hour >= 3 and host.data.get("daily") != today
        host.data["seen"] = now.isoformat()
        if due:
            host.data["daily"] = today
        host.save()
    if due:
        check()
    with transaction.atomic():
        policy = locked(POLICY)
        job = locked(JOB)
        info = data(CHECK)
        if (
            due
            and policy.data.get("enabled")
            and not pending(job.data)
            and info.get("tag")
            and not info.get("error")
            and release_channel.automatic_patch(current(), info["tag"])
            and not (
                job.data.get("tag") == info["tag"] and job.data.get("state") == "failed"
            )
        ):
            job.data = make_job(info["tag"], True)
        if not pending(job.data):
            return {"action": "idle"}
        if job.data.get("state") == "queued":
            job.data["state"] = "installing"
            job.save()
        return {
            "action": "install",
            "id": job.data["id"],
            "tag": job.data["tag"],
            "automatic": job.data["automatic"],
        }


def finish(job_id, state):
    with transaction.atomic():
        job = locked(JOB)
        if job.data.get("id") == job_id and pending(job.data):
            if (
                state == "queued"
                and job.data.get("automatic")
                and not data(POLICY).get("enabled")
            ):
                state = "cancelled"
            job.data["state"] = state
            job.save()


class MaintenanceGate:
    """Keep public traffic out of the brief database cutover, including on restart."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.path_info not in ("/health/", "/ready/")
            and (settings.DATA_DIR / ".update-maintenance").exists()
        ):
            return HttpResponse(
                _("Mise à jour en cours. Réessayez dans un instant."),
                status=503,
                headers={"Retry-After": "60", "Cache-Control": "no-store"},
            )
        return self.get_response(request)
