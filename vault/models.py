import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import MinValueValidator, MaxValueValidator
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
        ("forest", "Forêt"),
        ("ocean", "Océan"),
        ("plum", "Prune"),
        ("ink", "Encre"),
    ]
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="branding"
    )
    name = models.CharField(max_length=80, default="Mon espace")
    tagline = models.CharField(max_length=180, blank=True)
    theme = models.CharField(max_length=10, choices=THEMES, default="forest")
    footer = models.CharField(max_length=240, blank=True)


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
    objects = LiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-updated_at"]
        base_manager_name = "all_objects"
        default_manager_name = "objects"

    @property
    def total_bytes(self):
        return sum(f.size for f in self.files.all())


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
    objects = LiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["path"]
        base_manager_name = "all_objects"
        default_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(
                fields=["collection", "path"], name="unique_collection_path"
            )
        ]

    @property
    def extension(self):
        return self.path.rsplit(".", 1)[-1].upper()[:5]


class ShareLink(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    collection = models.ForeignKey(
        Collection, on_delete=models.CASCADE, related_name="links"
    )
    label = models.CharField(max_length=80)
    password_hash = models.CharField(max_length=256, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    max_downloads = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(100000)],
    )
    downloads = models.PositiveIntegerField(default=0)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def status(self):
        if self.revoked_at or self.collection.deleted_at:
            return "Révoqué"
        if self.expires_at and self.expires_at <= timezone.now():
            return "Expiré"
        if self.max_downloads is not None and self.downloads >= self.max_downloads:
            return "Épuisé"
        return "Actif"

    @property
    def public_url(self):
        from .security import capability

        return f"{settings.APP_URL}/s/{capability('share', self.id)}/"


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
        ("opened", "Page ouverte"),
        ("download_started", "Téléchargement autorisé"),
        ("created", "Lien créé"),
        ("revoked", "Lien révoqué"),
    ]
    link = models.ForeignKey(ShareLink, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=24, choices=KINDS)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class Audit(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )
    action = models.CharField(max_length=40)
    # An object UUID or numeric id only, never an email, password or capability.
    target = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class RateBucket(models.Model):
    key = models.CharField(primary_key=True, max_length=64)
    hits = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)


def upload_expiry():
    return timezone.now() + timedelta(hours=24)


class UploadSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    collection = models.ForeignKey(Collection, on_delete=models.CASCADE)
    size = models.PositiveBigIntegerField()
    state = models.CharField(max_length=12, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=upload_expiry, db_index=True)


class UploadPart(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        UploadSession, on_delete=models.CASCADE, related_name="parts"
    )
    path = models.CharField(max_length=500)
    size = models.PositiveBigIntegerField()
    offset = models.PositiveBigIntegerField(default=0)
    fingerprint = models.CharField(max_length=64)

    class Meta:
        ordering = ["path"]


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
