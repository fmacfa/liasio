from django.conf import settings


def brand(request):
    return {"development": settings.DEBUG, "antivirus_enabled": bool(settings.CLAMAV_HOST), "app_url": settings.APP_URL}
