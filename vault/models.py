from django.utils.translation import gettext_lazy as _
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import MinValueValidator, MaxValueValidator, RegexValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


def default_quota():
    return settings.DEFAULT_QUOTA_BYTES


def invitation_expiry():
    return timezone.now() + timedelta(days=7)


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra):
        user = self.model(email=email.strip().lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True)
        return self.create_user(email, password, **extra)


class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=80)
    quota_bytes = models.PositiveBigIntegerField(default=default_quota)
    used_bytes = models.PositiveBigIntegerField(default=0)
    reserved_bytes = models.PositiveBigIntegerField(default=0)
    mfa_secret = models.TextField(blank=True)
    mfa_version = models.UUIDField(default=uuid.uuid4, editable=False)
    mfa_last_step = models.BigIntegerField(default=-1)
    recovery_codes = models.JSONField(default=list, blank=True)
    mail_lease = models.UUIDField(null=True, editable=False)
    mail_lease_until = models.DateTimeField(null=True, editable=False)
    mail_last_claimed = models.DateTimeField(null=True, editable=False)
    notify_downloads = models.BooleanField(default=False)
    notify_uploads = models.BooleanField(default=False)
    # Shared by all dispatch processes: one in-flight webhook per owner.
    webhook_lease = models.UUIDField(null=True, editable=False)
    webhook_lease_until = models.DateTimeField(null=True, editable=False)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["display_name"]
    objects = UserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("email"), name="unique_email_casefold")
        ]

    def __str__(self):
        return self.display_name or self.email

    @property
    def storage_percent(self):
        return min(100, round(self.used_bytes * 100 / max(1, self.quota_bytes)))


class Instance(models.Model):
    # A seeded singleton provides a lock for concurrent first-time setup requests.
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    initialized = models.BooleanField(default=False)


class Branding(models.Model):
    THEMES = [
        ("forest", _("Forêt")),
        ("ocean", _("Océan")),
        ("plum", _("Prune")),
        ("ink", _("Encre")),
    ]
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="branding"
    )
    name = models.CharField(max_length=80, default=_("Mon espace"))
    tagline = models.CharField(max_length=180, blank=True)
    theme = models.CharField(max_length=10, choices=THEMES, default="forest")
    footer = models.CharField(max_length=240, blank=True)
    accent_color = models.CharField(
        default="",
        db_default="",
        max_length=7,
        blank=True,
        validators=[
            RegexValidator(
                r"\A#[0-9a-fA-F]{6}\Z", _("Utilisez une couleur au format #123abc.")
            )
        ],
    )
    background_color = models.CharField(
        default="",
        db_default="",
        max_length=7,
        blank=True,
        validators=[
            RegexValidator(
                r"\A#[0-9a-fA-F]{6}\Z", _("Utilisez une couleur au format #123abc.")
            )
        ],
    )
    # Small, canonical PNGs live in the backed-up database, never at a public URL.
    logo_data = models.BinaryField(default=bytes, db_default=b"", blank=True)
    background_data = models.BinaryField(default=bytes, db_default=b"", blank=True)

    @property
    def image_bytes(self):
        return len(self.logo_data) + len(self.background_data)


class LiveManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class Collection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="collections"
    )
    title = models.CharField(max_length=120)
    description = models.TextField(max_length=4000, blank=True)
    recipient_note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    workspace = models.ForeignKey(
        "Workspace",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="collections",
    )
    file_request = models.ForeignKey(
        "FileRequest",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="submissions",
    )
    objects = LiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-updated_at"]
        base_manager_name = "all_objects"
        default_manager_name = "objects"

    @property
    def total_bytes(self):
        return sum(f.size for f in self.files.all())


class CollectionAccess(models.Model):
    ROLES = [("reader", _("Lecteur")), ("collaborator", _("Collaborateur"))]
    collection = models.ForeignKey(
        Collection, on_delete=models.CASCADE, related_name="access_grants"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="collection_access",
    )
    role = models.CharField(max_length=16, choices=ROLES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["collection", "user"], name="unique_collection_access"
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=["reader", "collaborator"]),
                name="valid_collection_access_role",
            ),
        ]


class CurrentAssetManager(LiveManager):
    def get_queryset(self):
        return super().get_queryset().filter(superseded_at__isnull=True)


class Asset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    collection = models.ForeignKey(
        Collection, on_delete=models.CASCADE, related_name="files"
    )
    path = models.CharField(max_length=500)
    blob = models.FileField(max_length=120)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    scanned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    superseded_at = models.DateTimeField(null=True, blank=True, db_index=True)
    objects = CurrentAssetManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["created_at", "pk"]
        base_manager_name = "all_objects"
        default_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(
                fields=["collection", "path"],
                condition=models.Q(superseded_at__isnull=True),
                name="unique_current_collection_path",
            )
        ]

    @property
    def extension(self):
        return self.path.rsplit(".", 1)[-1].upper()[:5]

    @property
    def preview_kind(self):
        from .previews import preview_kind

        return preview_kind(self)


class ShareLinkQuerySet(models.QuerySet):
    def bulk_create(self, objs, *args, **kwargs):
        objs = list(objs)
        for obj in objs:
            obj._set_short_code()
        return super().bulk_create(objs, *args, **kwargs)


class ShareLink(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    collection = models.ForeignKey(
        Collection, on_delete=models.CASCADE, related_name="links"
    )
    label = models.CharField(max_length=80)
    password_hash = models.CharField(max_length=256, blank=True)
    allowed_emails = models.JSONField(default=list, blank=True)
    allow_previews = models.BooleanField(default=False)
    follow_updates = models.BooleanField(default=False)
    revision = models.ForeignKey(
        "CollectionRevision",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="links",
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    max_downloads = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(100000)],
    )
    downloads = models.PositiveIntegerField(default=0)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    short_code_digest = models.CharField(
        max_length=64, unique=True, null=True, blank=True, editable=False
    )
    objects = ShareLinkQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def _set_short_code(self):
        from .share_urls import code_digest, short_code

        if self.pk is None:
            self.pk = uuid.uuid4()
        self.short_code_digest = code_digest(short_code(self.pk))

    def save(self, *args, **kwargs):
        if self._state.adding:
            self._set_short_code()
        return super().save(*args, **kwargs)

    @property
    def status_code(self):
        if self.revoked_at or self.collection.deleted_at:
            return "revoked"
        if self.expires_at and self.expires_at <= timezone.now():
            return "expired"
        if self.max_downloads is not None and self.downloads >= self.max_downloads:
            return "exhausted"
        return "active"

    @property
    def status(self):
        return {
            "revoked": _("Révoqué"),
            "expired": _("Expiré"),
            "exhausted": _("Épuisé"),
            "active": _("Actif"),
        }[self.status_code]

    @property
    def public_url(self):
        from .share_urls import preferred_share_token

        return f"{settings.APP_URL}/s/{preferred_share_token(self)}/"


class LinkBatch(models.Model):
    """A private, reviewable list. Completing the same UUID twice is harmless."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    collection = models.ForeignKey(
        Collection, on_delete=models.CASCADE, related_name="link_batches"
    )
    name = models.CharField(max_length=80)
    headers = models.JSONField(default=list)
    source_rows = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]


class LinkBatchItem(models.Model):
    batch = models.ForeignKey(LinkBatch, on_delete=models.CASCADE, related_name="items")
    link = models.OneToOneField(
        ShareLink, on_delete=models.CASCADE, related_name="batch_item"
    )
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "position"], name="unique_batch_position"
            )
        ]


class Invitation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField()
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    expires_at = models.DateTimeField(default=invitation_expiry)
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def active(self):
        return (
            not self.accepted_at
            and not self.revoked_at
            and self.expires_at > timezone.now()
        )

    @property
    def public_url(self):
        from .security import capability

        return f"{settings.APP_URL}/invite/{capability('invite', self.id)}/"


class Event(models.Model):
    KINDS = [
        ("opened", _("Page ouverte")),
        ("download_started", _("Téléchargement autorisé")),
        ("created", _("Lien créé")),
        ("revoked", _("Lien révoqué")),
    ]
    link = models.ForeignKey(ShareLink, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=24, choices=KINDS)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class Audit(models.Model):
    # Keep stable audit identifiers in storage; translate only their presentation.
    ACTION_LABELS = {
        "instance_initialized": _("Instance initialisée"),
        "invitation_accepted": _("Invitation acceptée"),
        "invitation_created": _("Invitation créée"),
        "invitation_revoked": _("Invitation révoquée"),
        "login": _("Connexion réussie"),
        "password_changed": _("Mot de passe modifié"),
        "member_activated": _("Membre réactivé"),
        "member_suspended": _("Membre suspendu"),
        "quota_changed": _("Quota modifié"),
        "quota_updated": _("Quota modifié"),
        "collection_created": _("Dossier créé"),
        "collection_access_changed": _("Accès au dossier modifiés"),
        "collection_downloaded": _("Dossier téléchargé par un membre"),
        "workspace_created": _("Espace d’équipe créé"),
        "workspace_changed": _("Espace d’équipe modifié"),
        "group_changed": _("Groupe modifié"),
        "collection_workspace_changed": _("Espace du dossier modifié"),
        "collection_transferred": _("Propriété du dossier transférée"),
        "collection_trashed": _("Dossier placé dans la corbeille"),
        "files_uploaded": _("Fichiers enregistrés"),
        "files_trashed": _("Fichiers placés dans la corbeille"),
        "upload_started": _("Envoi démarré"),
        "upload_canceled": _("Envoi annulé"),
        "trash_restored": _("Élément restauré"),
        "trash_purged": _("Élément supprimé définitivement"),
        "link_created": _("Lien créé"),
        "link_revoked": _("Lien révoqué"),
        "link_renamed": _("Lien renommé"),
        "link_batch_renamed": _("Lot renommé"),
        "link_batch_deleted": _("Liste de liens supprimée"),
        "link_previews_changed": _("Aperçus du lien modifiés"),
        "api_key_created": _("Clé API créée"),
        "api_key_revoked": _("Clé API révoquée"),
        "webhook_created": _("Webhook créé"),
        "webhook_paused": _("Webhook mis en pause"),
        "webhook_deleted": _("Webhook supprimé"),
        "webhook_resume": _("Webhook réactivé"),
        "webhook_rotate": _("Secret du webhook renouvelé"),
        "webhook_queue_full": _("Webhook mis en pause : file pleine"),
        "mfa_enabled": _("Double authentification activée"),
        "mfa_disable": _("Double authentification désactivée"),
        "mfa_regenerate": _("Codes de récupération renouvelés"),
        "mfa_recovery_used": _("Code de récupération utilisé"),
        "backup_restore_verified": _("Restauration de sauvegarde vérifiée"),
        "revision_created": _("Version enregistrée"),
        "revision_deleted": _("Version supprimée"),
        "revision_restored": _("Version restaurée"),
        "sessions_revoked": _("Sessions déconnectées"),
        "mail_queued": _("E-mails confirmés pour envoi"),
        "file_request_created": _("Demande de fichiers créée"),
        "file_request_received": _("Dépôt client reçu"),
        "file_request_revoked": _("Demande de fichiers révoquée"),
        "oidc_linked": _("Identité d’entreprise reliée"),
        "oidc_unlinked": _("Identité d’entreprise dissociée"),
    }

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )
    action = models.CharField(max_length=40)
    # An object UUID or numeric id only, never an email, password or capability.
    target = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    @property
    def action_label(self):
        return self.ACTION_LABELS.get(self.action, _("Autre action"))

    class Meta:
        ordering = ["-created_at", "-id"]


class MailBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True)


class OIDCIdentity(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="oidc_identities"
    )
    issuer = models.URLField(max_length=500)
    subject = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["issuer", "subject"], name="unique_oidc_subject"
            ),
            models.UniqueConstraint(
                fields=["user", "issuer"], name="unique_user_oidc_issuer"
            ),
        ]


class MailDelivery(models.Model):
    STATES = [
        ("draft", _("Brouillon")),
        ("pending", _("En attente")),
        ("sending", _("En cours")),
        ("sent", _("Transmis au serveur mail")),
        ("failed", _("Échec")),
        ("canceled", _("Annulé")),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sender = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="mail_deliveries"
    )
    batch = models.ForeignKey(
        MailBatch, null=True, on_delete=models.CASCADE, related_name="deliveries"
    )
    recipient = models.EmailField()
    payload = models.TextField()
    kind = models.CharField(max_length=16)
    state = models.CharField(
        max_length=16, choices=STATES, default="draft", db_index=True
    )
    link = models.ForeignKey(ShareLink, null=True, on_delete=models.SET_NULL)
    invitation = models.ForeignKey(Invitation, null=True, on_delete=models.SET_NULL)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt = models.DateTimeField(default=timezone.now)
    lease = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True)
    error = models.CharField(max_length=24, blank=True)


class MailTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=80)
    subject = models.CharField(max_length=160)
    body = models.TextField(max_length=10000)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name"], name="unique_mail_template"
            )
        ]


class RecipientChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    link = models.ForeignKey(ShareLink, on_delete=models.CASCADE)
    email = models.EmailField()
    code_hash = models.CharField(max_length=64)
    session_digest = models.CharField(max_length=64)
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class RateBucket(models.Model):
    key = models.CharField(primary_key=True, max_length=64)
    hits = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)


def upload_expiry():
    return timezone.now() + timedelta(hours=24)


class UploadSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="initiated_uploads",
    )
    collection = models.ForeignKey(Collection, on_delete=models.CASCADE)
    size = models.PositiveBigIntegerField()
    state = models.CharField(max_length=12, default="pending")
    replace_files = models.BooleanField(default=False)
    file_request = models.ForeignKey(
        "FileRequest", null=True, on_delete=models.SET_NULL
    )
    browser_digest = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=upload_expiry, db_index=True)


class FileRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=120)
    description = models.TextField(max_length=2000, blank=True)
    max_bytes = models.PositiveBigIntegerField()
    max_submissions = models.PositiveIntegerField(default=20)
    received_bytes = models.PositiveBigIntegerField(default=0)
    received_count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def active(self):
        return (
            not self.revoked_at
            and self.expires_at > timezone.now()
            and self.owner.is_active
        )

    @property
    def public_url(self):
        from .security import capability

        return (
            settings.APP_URL
            + "/file-requests/"
            + capability("file-request", self.pk)
            + "/"
        )


class UploadPart(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        UploadSession, on_delete=models.CASCADE, related_name="parts"
    )
    path = models.CharField(max_length=500)
    size = models.PositiveBigIntegerField()
    offset = models.PositiveBigIntegerField(default=0)
    fingerprint = models.CharField(max_length=64)
    position = models.PositiveSmallIntegerField(default=0, db_default=0)

    class Meta:
        ordering = ["position", "path"]


class ServiceState(models.Model):
    key = models.CharField(primary_key=True, max_length=40)
    data = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)


class OperationalAlert(models.Model):
    key = models.CharField(primary_key=True, max_length=80)
    message = models.CharField(max_length=240)
    active = models.BooleanField(default=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def display_message(self):
        # Quota messages were interpolated before storage. Translate their
        # template again using the stable account identifier, not the old text.
        if self.key.startswith("quota-") and self.key[6:].isdigit():
            return _(
                "Le compte n° %(id)s atteint au moins 90 %% de son quota (envois réservés compris)."
            ) % {"id": self.key[6:]}
        return _(self.message)


class APIToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=80)
    digest = models.CharField(max_length=64)
    credential_version = models.CharField(max_length=64)
    scopes = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)


class APIReceipt(models.Model):
    token = models.ForeignKey(APIToken, on_delete=models.CASCADE)
    key = models.CharField(max_length=64)
    fingerprint = models.CharField(max_length=64)
    response = (
        models.TextField()
    )  # Encrypted: responses may contain share capabilities.
    status = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["token", "key"], name="api_receipt_key")
        ]


class Webhook(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=80)
    destination = models.TextField()  # Encrypted; paths can contain receiver secrets.
    hostname = models.CharField(max_length=253)
    secret = (
        models.TextField()
    )  # Encrypted signing key, shown only on creation/rotation.
    events = models.JSONField(default=list)
    active = models.BooleanField(default=True)
    last_error = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class WebhookDelivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    webhook = models.ForeignKey(
        Webhook, on_delete=models.CASCADE, related_name="deliveries"
    )
    event_id = models.UUIDField(default=uuid.uuid4)
    kind = models.CharField(max_length=40)
    payload = models.JSONField()
    state = models.CharField(max_length=12, default="pending")
    attempts = models.PositiveSmallIntegerField(default=0)
    next_at = models.DateTimeField(default=timezone.now, db_index=True)
    lease = models.UUIDField(null=True)
    http_status = models.PositiveSmallIntegerField(null=True)
    error = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    delivered_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=["state", "next_at"], name="webhook_due_idx")]


class Workspace(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=80)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="managed_workspaces",
    )
    require_mfa = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "pk"]

    def __str__(self):
        return self.name


class WorkspaceMember(models.Model):
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="members"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workspace_memberships",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "user"], name="workspace_member_unique"
            )
        ]


class TeamGroup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="groups"
    )
    name = models.CharField(max_length=80)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="team_groups"
    )

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"], name="workspace_group_name_unique"
            )
        ]

    def __str__(self):
        return self.name


class GroupAccess(models.Model):
    group = models.ForeignKey(
        TeamGroup, on_delete=models.CASCADE, related_name="grants"
    )
    collection = models.ForeignKey(
        Collection, on_delete=models.CASCADE, related_name="group_grants"
    )
    role = models.CharField(max_length=16, choices=CollectionAccess.ROLES)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["group", "collection"], name="group_collection_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=["reader", "collaborator"]),
                name="group_access_valid_role",
            ),
        ]


class SharingPreset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sharing_presets",
    )
    name = models.CharField(max_length=80)
    expires_in_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(3650)],
    )
    max_downloads = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(100000)],
    )
    password_required = models.BooleanField(default=False)
    allow_previews = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name"], name="owner_preset_name_unique"
            )
        ]

    def __str__(self):
        return self.name


class CollectionRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    collection = models.ForeignKey(
        Collection, on_delete=models.CASCADE, related_name="revisions"
    )
    number = models.PositiveIntegerField()
    name = models.CharField(max_length=80, blank=True)
    assets = models.ManyToManyField(Asset, related_name="revisions")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["-number"]
        constraints = [
            models.UniqueConstraint(
                fields=["collection", "number"],
                name="collection_revision_number_unique",
            )
        ]

    def __str__(self):
        return f"v{self.number}" + (f" · {self.name}" if self.name else "")


class LoginSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.OneToOneField(
        "sessions.Session", on_delete=models.CASCADE, related_name="login_info"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="login_sessions",
    )
    user_agent = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-last_seen"]
