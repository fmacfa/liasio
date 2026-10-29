"""Private drafts and a bounded, durable SMTP outbox. No network inside DB locks."""

import json
import smtplib
import uuid
from datetime import timedelta
from string import Template

from cryptography.fernet import InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from .access import collections_for
from .mfa import cipher
from .models import MailDelivery, User

ACTIVE_STATES = ("draft", "pending", "sending")


def render_message(subject, body, values):
    if not subject or len(subject) > 160 or any(c in subject for c in "\r\n"):
        raise ValidationError(
            _("Indiquez un objet sur une seule ligne (160 caractères maximum).")
        )
    if not body or len(body) > 10000:
        raise ValidationError(_("Le message doit contenir de 1 à 10 000 caractères."))
    try:
        return Template(subject).substitute(values), Template(body).substitute(values)
    except (KeyError, ValueError):
        raise ValidationError(
            _(
                "Variables disponibles : $name, $link, $collection. Utilisez $$ pour un dollar."
            )
        ) from None


def enqueue(
    sender,
    recipient,
    subject,
    body,
    *,
    state="pending",
    kind="share",
    link=None,
    invitation=None,
    batch=None,
    expires_at=None,
):
    """Caller holds sender's account lock; payload includes private capabilities."""
    validate_email(recipient)
    if (
        state not in ("draft", "pending")
        or not subject
        or "\n" in subject
        or "\r" in subject
    ):
        raise ValidationError(_("Message invalide."))
    if len(body) > 16000 or len(subject) > 300:
        raise ValidationError(_("Message trop long."))
    if (
        MailDelivery.objects.filter(sender=sender, state__in=ACTIVE_STATES).count()
        >= 1000
    ):
        raise ValidationError(
            _("La file contient déjà 1 000 messages. Réessayez après leur traitement.")
        )
    return MailDelivery.objects.create(
        sender=sender,
        recipient=recipient.strip().lower(),
        payload=cipher()
        .encrypt(json.dumps({"subject": subject, "body": body}).encode())
        .decode(),
        state=state,
        kind=kind,
        link=link,
        invitation=invitation,
        batch=batch,
        expires_at=expires_at or timezone.now() + timedelta(days=1),
    )


def payload(delivery):
    return json.loads(cipher().decrypt(delivery.payload.encode()))


def permitted(delivery):
    from .sessions import requires_mfa

    if (
        delivery.kind in ("share", "invitation")
        and not delivery.sender.mfa_secret
        and requires_mfa(delivery.sender)
    ):
        return False
    if not delivery.sender.is_active or delivery.expires_at <= timezone.now():
        return False
    if delivery.kind in ("share", "otp"):
        link = delivery.link
        if (
            not link
            or link.status_code != "active"
            or not link.collection.owner.is_active
        ):
            return False
        if delivery.kind == "otp":
            return (
                delivery.recipient in link.allowed_emails
                and link.collection.owner_id == delivery.sender_id
            )
        return (
            collections_for(delivery.sender, write=True)
            .filter(pk=link.collection_id)
            .exists()
        )
    if delivery.kind == "invitation":
        return bool(
            delivery.invitation
            and delivery.invitation.active
            and delivery.sender.is_staff
            and delivery.invitation.invited_by_id == delivery.sender_id
            and delivery.invitation.email.lower() == delivery.recipient
        )
    return (
        delivery.kind in ("test", "notification")
        and delivery.recipient == delivery.sender.email.lower()
    )


def claim():
    now = timezone.now()
    # A crashed worker's lease can be reclaimed; a fixed Message-ID makes retries identifiable.
    candidates = (
        User.objects.filter(
            Q(mail_lease_until__isnull=True) | Q(mail_lease_until__lt=now),
            pk__in=MailDelivery.objects.filter(
                Q(state="pending") | Q(state="sending", lease_until__lt=now),
                next_attempt__lte=now,
            ).values("sender_id"),
        )
        .order_by(F("mail_last_claimed").asc(nulls_first=True), "pk")
        .values_list("pk", flat=True)[:50]
    )
    for pk in list(candidates):
        with transaction.atomic():
            sender = User.objects.select_for_update().get(pk=pk)
            if sender.mail_lease_until and sender.mail_lease_until > now:
                continue
            delivery = (
                MailDelivery.objects.select_for_update(of=("self",))
                .filter(
                    Q(state="pending") | Q(state="sending", lease_until__lt=now),
                    sender=sender,
                    next_attempt__lte=now,
                )
                .select_related("sender", "link__collection__owner", "invitation")
                .order_by("created_at", "pk")
                .first()
            )
            if not delivery:
                continue
            if delivery.attempts >= 5 or not permitted(delivery):
                delivery.state = "canceled"
                delivery.payload = ""
                delivery.save(update_fields=["state", "payload"])
                continue
            lease = uuid.uuid4()
            until = now + timedelta(minutes=5)
            sender.mail_lease, sender.mail_lease_until, sender.mail_last_claimed = (
                lease,
                until,
                now,
            )
            sender.save(
                update_fields=["mail_lease", "mail_lease_until", "mail_last_claimed"]
            )
            delivery.state, delivery.lease, delivery.lease_until = (
                "sending",
                lease,
                until,
            )
            delivery.attempts += 1
            delivery.save(update_fields=["state", "lease", "lease_until", "attempts"])
            return delivery
    return None


def deliver_one():
    delivery = claim()
    if delivery is None:
        return False
    error = ""
    try:
        content = payload(delivery)
        message = EmailMessage(
            content["subject"],
            content["body"],
            settings.DEFAULT_FROM_EMAIL,
            [delivery.recipient],
            headers={"Message-ID": f"<{delivery.pk}@liasio.local>"},
        )
        if message.send(fail_silently=False) != 1:
            error = "smtp"
    except (OSError, smtplib.SMTPException, InvalidToken, ValueError, KeyError):
        error = "smtp"
    with transaction.atomic():
        sender = (
            User.objects.select_for_update()
            .filter(pk=delivery.sender_id, mail_lease=delivery.lease)
            .first()
        )
        if sender is None:
            return True
        current = MailDelivery.objects.select_for_update().get(pk=delivery.pk)
        if current.lease != delivery.lease:
            return True
        current.error = error
        current.lease = current.lease_until = None
        if not error:
            current.state, current.sent_at, current.payload = "sent", timezone.now(), ""
        elif current.attempts >= 5:
            current.state, current.payload = "failed", ""
        else:
            current.state = "pending"
            current.next_attempt = timezone.now() + timedelta(
                seconds=min(3600, 30 * 2**current.attempts)
            )
        current.save()
        sender.mail_lease = sender.mail_lease_until = None
        sender.save(update_fields=["mail_lease", "mail_lease_until"])
    return True


def cleanup():
    now = timezone.now()
    MailDelivery.objects.filter(
        state__in=("draft", "pending"), expires_at__lte=now
    ).update(state="canceled", payload="")
    MailDelivery.objects.filter(created_at__lt=now - timedelta(days=30)).exclude(
        state="sending"
    ).delete()


def notify(user_id, kind, title):
    """Optional self-notifications, throttled per account; no bearer links in them."""
    from .security import rate_allowed

    with transaction.atomic():
        user = (
            User.objects.select_for_update().filter(pk=user_id, is_active=True).first()
        )
        if not user or not getattr(user, "notify_" + kind, False):
            return
        if not rate_allowed("mail-notification", str(user.pk), 10, 3600):
            return
        try:
            enqueue(
                user,
                user.email,
                _("Activité Liasio"),
                title + "\n\n" + settings.APP_URL + "/activity/",
                kind="notification",
            )
        except ValidationError:
            pass  # A full mailbox queue must not interrupt an upload/download.


def on_event(sender, instance, created, **kwargs):
    if created and instance.kind == "download_started":
        user_id = instance.link.collection.owner_id
        transaction.on_commit(
            lambda: notify(
                user_id, "downloads", str(_("Un téléchargement a été autorisé."))
            )
        )


def on_audit(sender, instance, created, **kwargs):
    if created and instance.action == "files_uploaded":
        from .models import Collection

        owner_id = (
            Collection.objects.filter(pk=instance.target)
            .values_list("owner_id", flat=True)
            .first()
        )
        if owner_id:
            transaction.on_commit(
                lambda: notify(
                    owner_id,
                    "uploads",
                    str(_("De nouveaux fichiers ont été enregistrés.")),
                )
            )
