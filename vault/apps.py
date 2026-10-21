from django.apps import AppConfig


class VaultConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "vault"

    def ready(self):
        from . import checks  # noqa: F401
