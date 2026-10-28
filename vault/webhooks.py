"""Transactional webhook outbox, bounded retries and pinned public HTTPS delivery."""

import hashlib
import hmac
import http.client
import ipaddress
import json
import re
import secrets
import signal
import socket
import ssl
import time
import uuid
from contextlib import contextmanager
from datetime import timedelta
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Exists, OuterRef, Q
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .mfa import cipher
from .models import (
    Audit,
    Collection,
    Event,
    ServiceState,
    User,
    Webhook,
    WebhookDelivery,
)

EVENTS = [
    ("collection.created", _("Dossier créé")),
    ("files.uploaded", _("Fichiers enregistrés")),
    ("link.created", _("Lien créé")),
    ("link.revoked", _("Lien révoqué")),
    ("link.opened", _("Page ouverte")),
    ("download.started", _("Téléchargement autorisé")),
]
MAX_ATTEMPTS = 6
RETRY_SECONDS = [60, 300, 1800, 7200, 21600]


def parse_destination(url):
    """No credentials, fragments, query secrets, custom ports or ambiguous hosts."""
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        if (
            len(url) > 2000
            or not url.isascii()
            or re.search(r"[\s\\\x00-\x1f\x7f]", url)
            or parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.query
            or parsed.port not in (None, 443)
            or not re.fullmatch(r"[a-zA-Z0-9.-]+", hostname)
            or "." not in hostname
            or hostname.endswith(".")
            or ".." in hostname
        ):
            raise ValueError
        return hostname.lower(), parsed.path or "/"
    except ValueError:
        raise ValidationError(
            _(
                "Utilisez une URL HTTPS publique sur le port 443, sans identifiants, paramètres ni fragment."
            )
        ) from None


def public_addresses(hostname):
    addresses = list(
        dict.fromkeys(
            info[4][0]
            for info in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        )
    )
    if not addresses:
        raise ValueError("destination_blocked")
    for value in addresses:
        address = ipaddress.ip_address(value)
        if (
            not address.is_global
            or address.is_multicast
            or (
                address.version == 6
                and (
                    address.ipv4_mapped
                    or address.sixtofour
                    or address.teredo
                    or address in ipaddress.ip_network("64:ff9b::/96")
                    or address in ipaddress.ip_network("64:ff9b:1::/48")
                )
            )
        ):
            raise ValueError("destination_blocked")
    return addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(
            host, port=443, timeout=5, context=ssl.create_default_context()
        )
        self.address = address

    def connect(self):
        # Resolve exactly once; TLS still authenticates the original hostname.
        sock = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


@contextmanager
def deadline():
    # The command's main thread runs on Linux. This also bounds DNS and slow headers.
    def expired(*_args):
        raise TimeoutError("delivery_timeout")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 20)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def send(delivery, hook):
    with deadline():
        url = cipher().decrypt(hook.destination.encode()).decode()
        hostname, path = parse_destination(url)
        addresses = public_addresses(hostname)  # Repeat validation on every attempt.
        body = json.dumps(
            delivery.payload, separators=(",", ":"), ensure_ascii=True
        ).encode()
        timestamp = str(int(time.time()))
        secret = cipher().decrypt(hook.secret.encode())
        signature = hmac.new(
            secret, timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        connection = PinnedHTTPS(hostname, addresses[0])
        try:
            connection.request(
                "POST",
                path,
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Liasio-Webhooks/1",
                    "X-LinkVault-Event": delivery.kind,
                    "X-LinkVault-Delivery": str(delivery.pk),
                    "X-LinkVault-Timestamp": timestamp,
                    "X-LinkVault-Signature": "v1=" + signature,
                },
            )
            # No redirects, proxy environment, cookies or response-body storage.
            response = connection.getresponse()
            return response.status
        finally:
            connection.close()


def pause_hook(hook, *, error="endpoint_disabled"):
    """Caller holds the hook lock; pause and cancellation commit together."""
    hook.active = False
    hook.last_error = error if error == "queue_full" else ""
    hook.save(update_fields=["active", "last_error"])
    hook.deliveries.filter(state="pending").update(
        state="canceled", error=error, lease=None
    )


def enqueue(owner_id, kind, data, *, only=None):
    with transaction.atomic():
        hooks = (
            Webhook.objects.select_for_update(of=("self",))
            .filter(owner_id=owner_id, active=True, owner__is_active=True)
            .order_by("pk")
        )
        if only:
            hooks = hooks.filter(pk=only)
        event_id = uuid.uuid4()
        payload = {
            "id": str(event_id),
            "type": kind,
            "created_at": timezone.now().isoformat(),
            "data": data,
        }
        for hook in hooks:
            if kind not in hook.events and kind != "webhook.test":
                continue
            if hook.deliveries.filter(state="pending").count() >= 1000:
                pause_hook(hook, error="queue_full")
                Audit.objects.create(
                    actor_id=owner_id, action="webhook_queue_full", target=str(hook.pk)
                )
                continue
            WebhookDelivery.objects.create(
                webhook=hook, event_id=event_id, kind=kind, payload=payload
            )


@receiver(post_save, sender=Collection)
def collection_created(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        enqueue(
            instance.owner_id, "collection.created", {"collection_id": str(instance.pk)}
        )


@receiver(post_save, sender=Audit)
def files_uploaded(sender, instance, created, raw=False, **kwargs):
    if created and not raw and instance.action == "files_uploaded":
        owner_id = (
            Collection.all_objects.filter(pk=instance.target)
            .values_list("owner_id", flat=True)
            .first()
        )
        if owner_id is not None:
            enqueue(owner_id, "files.uploaded", {"collection_id": instance.target})


@receiver(post_save, sender=Event)
def link_event(sender, instance, created, raw=False, **kwargs):
    if not created or raw:
        return
    kind = {
        "created": "link.created",
        "revoked": "link.revoked",
        "opened": "link.opened",
        "download_started": "download.started",
    }.get(instance.kind)
    if kind:
        enqueue(
            instance.link.collection.owner_id,
            kind,
            {
                "collection_id": str(instance.link.collection_id),
                "link_id": str(instance.link_id),
            },
        )


def claim_delivery():
    """Rotate owners durably; lease only one delivery per owner across workers.

    Lock order: scheduler -> user -> hook -> delivery. Never hold these locks
    during I/O. SQLite IMMEDIATE transactions serialize the same short section.
    """
    now = timezone.now()
    with transaction.atomic():
        ServiceState.objects.get_or_create(key="webhook_scheduler")
        scheduler = ServiceState.objects.select_for_update().get(
            key="webhook_scheduler"
        )
        due = WebhookDelivery.objects.filter(
            webhook__owner_id=OuterRef("pk"), state="pending", next_at__lte=now
        )
        owners = (
            User.objects.filter(Exists(due))
            .filter(
                Q(webhook_lease_until__isnull=True) | Q(webhook_lease_until__lte=now)
            )
            .order_by("pk")
            .select_for_update(
                skip_locked=connection.features.has_select_for_update_skip_locked,
                # Event producers may already hold Hook and insert an Audit FK
                # to User. Do not block that key-share check while awaiting Hook.
                no_key=connection.vendor == "postgresql",
            )
        )
        cursor = scheduler.data.get("last_owner_id", 0)
        owner = owners.filter(pk__gt=cursor).first() or owners.first()
        if owner is None:
            return None
        scheduler.data = {"last_owner_id": owner.pk}
        scheduler.save(update_fields=["data", "updated_at"])
        delivery = (
            WebhookDelivery.objects.filter(
                webhook__owner=owner, state="pending", next_at__lte=now
            )
            .order_by("next_at", "pk")
            .first()
        )
        if delivery is None:
            return ()  # Maintenance may remove expired history without an owner lock.
        hook = Webhook.objects.select_for_update().get(pk=delivery.webhook_id)
        delivery = (
            WebhookDelivery.objects.select_for_update()
            .filter(pk=delivery.pk, state="pending", next_at__lte=now)
            .first()
        )
        if delivery is None:
            return ()
        if not hook.active or not owner.is_active:
            pause_hook(hook)
            return ()
        if delivery.attempts >= MAX_ATTEMPTS:
            delivery.state, delivery.error = "failed", "attempt_limit"
            delivery.lease = None
            delivery.save(update_fields=["state", "error", "lease"])
            return ()
        delivery.lease = uuid.uuid4()
        delivery.attempts += 1
        delivery.next_at = now + timedelta(minutes=5)
        delivery.save(update_fields=["lease", "attempts", "next_at"])
        owner.webhook_lease = delivery.lease
        owner.webhook_lease_until = delivery.next_at
        owner.save(update_fields=["webhook_lease", "webhook_lease_until"])
        return delivery, hook, owner.pk


def deliver_one():
    """Fair, at-least-once delivery. Crashed workers release their slot in 5 min."""
    claimed = claim_delivery()
    if claimed is None:
        return False
    if not claimed:
        return True
    delivery, hook, owner_id = claimed
    status, error = None, ""
    try:
        status = send(delivery, hook)
        if not 200 <= status < 300:
            error = "http_error"
    except (OSError, ValueError, ValidationError, http.client.HTTPException):
        # Never persist receiver URLs, response contents or exception text.
        error = "connection_failed_or_blocked"
    except Exception:
        error = "delivery_failed"
    success = status is not None and 200 <= status < 300
    retry = (
        not success
        and (status is None or status in (408, 425, 429) or status >= 500)
        and delivery.attempts < MAX_ATTEMPTS
    )
    fields = {
        "state": "delivered" if success else "pending" if retry else "failed",
        "http_status": status,
        "error": error,
        "lease": None,
        "delivered_at": timezone.now() if success else None,
    }
    if retry:
        fields["next_at"] = timezone.now() + timedelta(
            seconds=RETRY_SECONDS[delivery.attempts - 1] + secrets.randbelow(20)
        )
    with transaction.atomic():
        # Same order as claim/pause; an expired worker cannot release a new lease.
        User.objects.filter(pk=owner_id, webhook_lease=delivery.lease).update(
            webhook_lease=None, webhook_lease_until=None
        )
        WebhookDelivery.objects.filter(
            pk=delivery.pk, lease=delivery.lease, state="pending"
        ).update(**fields)
    return True
