import os
from pathlib import Path
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
SECRET_KEY = os.environ.get("SECRET_KEY", "")
if len(SECRET_KEY) < 50:
    raise ImproperlyConfigured("Set SECRET_KEY to at least 50 random characters.")
APP_URL = os.environ.get("APP_URL", "http://127.0.0.1:8000").rstrip("/")
origin = urlparse(APP_URL)
if not origin.hostname or origin.path or origin.query or origin.fragment or origin.username:
    raise ImproperlyConfigured("APP_URL must be an origin, e.g. https://files.example.com.")
if origin.scheme not in (["http", "https"] if DEBUG else ["https"]):
    raise ImproperlyConfigured("HTTPS is required outside development.")
ALLOWED_HOSTS = [origin.hostname, "localhost", "127.0.0.1"] if DEBUG else [origin.hostname]
CSRF_TRUSTED_ORIGINS = [APP_URL]
SETUP_TOKEN = os.environ.get("SETUP_TOKEN", "")
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
MEDIA_ROOT = DATA_DIR / "files"
MEDIA_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
FILE_UPLOAD_TEMP_DIR = str(DATA_DIR / "tmp")
Path(FILE_UPLOAD_TEMP_DIR).mkdir(mode=0o700, parents=True, exist_ok=True)

INSTALLED_APPS = [
    "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
    "django.contrib.messages", "django.contrib.staticfiles", "vault.apps.VaultConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "vault.middleware.RequestBoundaryMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "linkvault.urls"
WSGI_APPLICATION = "linkvault.wsgi.application"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "vault.context_processors.brand",
    ]},
}]
if os.environ.get("POSTGRES_HOST"):
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ["POSTGRES_HOST"],
        "NAME": os.environ.get("POSTGRES_DB", "linkvault"),
        "USER": os.environ.get("POSTGRES_USER", "linkvault"),
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "CONN_MAX_AGE": 60, "CONN_HEALTH_CHECKS": True,
    }}
else:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3", "NAME": DATA_DIR / "db.sqlite3",
        "OPTIONS": {"timeout": 30, "transaction_mode": "IMMEDIATE"},
        "TEST": {"NAME": str(DATA_DIR / "test.sqlite3")},
    }}

AUTH_USER_MODEL = "vault.User"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
TRUST_PROXY = os.environ.get("TRUST_PROXY", "false").lower() == "true"
if TRUST_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
LANGUAGE_CODE = "fr-fr"
TIME_ZONE = os.environ.get("TIME_ZONE", "Europe/Berlin")
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
FILE_UPLOAD_PERMISSIONS = 0o600
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o700
FILE_UPLOAD_HANDLERS = ["vault.uploads.BoundedUploadHandler", "django.core.files.uploadhandler.TemporaryFileUploadHandler"]
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES = 100
DATA_UPLOAD_MAX_NUMBER_FIELDS = 300
MAX_FILE_BYTES = int(os.environ.get("MAX_FILE_BYTES", 100 * 1024 * 1024))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 500 * 1024 * 1024))
DEFAULT_QUOTA_BYTES = int(os.environ.get("DEFAULT_QUOTA_BYTES", 5 * 1024**3))
ALLOWED_EXTENSIONS = set(os.environ.get("ALLOWED_EXTENSIONS", "pdf,txt,csv,md,json,jpg,jpeg,png,webp,gif,zip,docx,xlsx,pptx,odt,ods,mp3,mp4,mov").lower().split(","))
ANTIVIRUS_REQUIRED = os.environ.get("ANTIVIRUS_REQUIRED", "true").lower() == "true"
CLAMAV_HOST = os.environ.get("CLAMAV_HOST", "")
CLAMAV_PORT = int(os.environ.get("CLAMAV_PORT", 3310))
EVENT_RETENTION_DAYS = int(os.environ.get("EVENT_RETENTION_DAYS", 90))
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "true").lower() == "true"
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "false").lower() == "true"
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "LinkVault <noreply@example.com>")
PASSWORD_RESET_TIMEOUT = 3600
# Never log capability URLs, passwords, request bodies or cookies.
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "filters": {"redact": {"()": "vault.middleware.RedactURLs"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "filters": ["redact"]}},
    "loggers": {
        "django": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "gunicorn.error": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
