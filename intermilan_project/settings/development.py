import os

import dj_database_url

from .base import BASE_DIR
from .base import *


DEBUG = True

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATABASE_ENGINE = os.environ.get("DATABASE_ENGINE", "sqlite").strip().lower()

if DATABASE_URL:
    DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=60)}
elif DATABASE_ENGINE in {"postgres", "postgresql"}:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DATABASE_NAME", "intermilan"),
            "USER": os.environ.get("DATABASE_USER", "intermilan_user"),
            "PASSWORD": os.environ.get("DATABASE_PASSWORD", ""),
            "HOST": os.environ.get("DATABASE_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DATABASE_PORT", "5432"),
        }
    }
elif DATABASE_ENGINE == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.environ.get("DATABASE_NAME", "intermilan"),
            "USER": os.environ.get("DATABASE_USER", "intermilan_user"),
            "PASSWORD": os.environ.get("DATABASE_PASSWORD", ""),
            "HOST": os.environ.get("DATABASE_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DATABASE_PORT", "3306"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / os.environ.get("DATABASE_NAME", "db.sqlite3"),
        }
    }

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
