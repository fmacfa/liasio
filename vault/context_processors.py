from django.conf import settings


def brand(request):
    return {
        "app_version": settings.APP_VERSION,
        "trash_retention": settings.TRASH_RETENTION_DAYS,
        "development": settings.DEBUG,
        "antivirus_enabled": bool(settings.CLAMAV_HOST),
        "app_url": settings.APP_URL,
    }
