import os

import dj_database_url

from .base import *


DEBUG = False

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATABASE_ENGINE = os.environ.get("DATABASE_ENGINE", "postgresql").strip().lower()

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            ssl_require=os.environ.get("DB_SSL_REQUIRE", "false").lower() == "true",
        )
    }
elif DATABASE_ENGINE in {"postgres", "postgresql"}:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["DATABASE_NAME"],
            "USER": os.environ["DATABASE_USER"],
            "PASSWORD": os.environ["DATABASE_PASSWORD"],
            "HOST": os.environ.get("DATABASE_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DATABASE_PORT", "5432"),
            "CONN_MAX_AGE": 600,
        }
    }
elif DATABASE_ENGINE == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.environ["DATABASE_NAME"],
            "USER": os.environ["DATABASE_USER"],
            "PASSWORD": os.environ["DATABASE_PASSWORD"],
            "HOST": os.environ.get("DATABASE_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DATABASE_PORT", "3306"),
            "CONN_MAX_AGE": 600,
        }
    }
else:
    raise RuntimeError("DATABASE_ENGINE production harus postgresql/mysql atau DATABASE_URL harus disetel.")

CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
