from django.conf import settings
from django.core.checks import Error, register, Tags


@register(Tags.security, deploy=True)
def production_checks(app_configs, **kwargs):
    errors = []
    if not settings.ANTIVIRUS_REQUIRED or not settings.CLAMAV_HOST:
        errors.append(Error("Production requires ANTIVIRUS_REQUIRED=true and CLAMAV_HOST.", id="vault.E001"))
    if settings.DEBUG:
        errors.append(Error("Production requires DEBUG=false.", id="vault.E002"))
    if not settings.TRUST_PROXY:
        errors.append(Error("Production expects the isolated reverse proxy; set TRUST_PROXY=true.", id="vault.E003"))
    return errors
