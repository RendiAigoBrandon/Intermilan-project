import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=None):
    raw = os.environ.get(name, "")
    if not raw:
        return default or []
    return [item.strip() for item in raw.split(",") if item.strip()]


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or os.environ.get("SECRET_KEY", "intermilan-dev-only-change-me")
DEBUG = env_bool("DJANGO_DEBUG", env_bool("DEBUG", False))
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", env_list("ALLOWED_HOSTS", ["127.0.0.1", "localhost"]))

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.accounts",
    "apps.core",
    "apps.sp2d",
    "apps.dk",
    "apps.documents",
    "apps.drpp",
    "apps.paket_spm",
    "apps.reports",
    "apps.auditlog",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "intermilan_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "intermilan_project.wsgi.application"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "id-id"
TIME_ZONE = "Asia/Jakarta"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = Path(os.environ.get("STATIC_ROOT", BASE_DIR / "staticfiles"))
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))

MAX_UPLOAD_SIZE_MB = env_int("MAX_UPLOAD_SIZE_MB", 2048)
MAX_FOLDER_UPLOAD_SIZE_MB = env_int("MAX_FOLDER_UPLOAD_SIZE_MB", 2048)
MAX_ZIP_SIZE_MB = env_int("MAX_ZIP_SIZE_MB", 2048)
MAX_ZIP_TOTAL_UNCOMPRESSED_MB = env_int("MAX_ZIP_TOTAL_UNCOMPRESSED_MB", 5000)
MAX_ZIP_FILES = env_int("MAX_ZIP_FILES", 1000)
MAX_UPLOAD_FILES = env_int("MAX_UPLOAD_FILES", 1000)
MAX_PDF_PAGES_PREVIEW = env_int("MAX_PDF_PAGES_PREVIEW", 3)
MAX_OCR_SECONDS_PER_FILE = env_int("MAX_OCR_SECONDS_PER_FILE", 90)

DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_SIZE_MB * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 0
FILE_UPLOAD_TEMP_DIR = str(MEDIA_ROOT / "tmp")
FILE_UPLOAD_HANDLERS = [
    "django.core.files.uploadhandler.TemporaryFileUploadHandler",
]

GOOGLE_DRIVE_MODE = os.environ.get("GOOGLE_DRIVE_MODE", "metadata_only")
OCR_ENABLED = env_bool("OCR_ENABLED", False)
OCR_SERVER_MODE = os.environ.get("OCR_SERVER_MODE", "disabled")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
