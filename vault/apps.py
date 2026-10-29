from django.apps import AppConfig


class VaultConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "vault"

    def ready(self):
        from . import checks, webhooks  # noqa: F401
        from django.db.models.signals import post_save
        from .mail import on_event, on_audit
        from .models import Event, Audit

        post_save.connect(
            on_event, sender=Event, dispatch_uid="mail_download_notification"
        )
        post_save.connect(
            on_audit, sender=Audit, dispatch_uid="mail_upload_notification"
        )
