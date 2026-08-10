import os

import dj_database_url

from .base import BASE_DIR
from .base import *


DEBUG = True

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE CONFIGURATION
#
# Strategy: PostgreSQL only, configured via environment variables.
#
# Priority:
#   1. DATABASE_URL  — full connection string (e.g. postgresql://user:pass@host/db)
#   2. Individual DATABASE_* vars below
#   3. (fallback only) SQLite — ONLY for emergency local dev when no PostgreSQL
#      is available.  Do NOT use SQLite for normal development; all features
#      require PostgreSQL (JSONB, arrays, tsvector, window functions, etc.).
#
# For local development with PostgreSQL, set these environment variables:
#   DATABASE_ENGINE=postgresql       ← explicitly required
#   DATABASE_NAME=intermilan_dev
#   DATABASE_USER=intermilan_user
#   DATABASE_PASSWORD=<password>
#   DATABASE_HOST=127.0.0.1
#   DATABASE_PORT=5432
# ─────────────────────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATABASE_ENGINE = os.environ.get("DATABASE_ENGINE", "").strip().lower()

if DATABASE_URL:
    DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=60)}
elif DATABASE_ENGINE in ("postgres", "postgresql"):
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
else:
    # Emergency fallback only — do NOT use for normal development.
    # Requires DATABASE_NAME to be set to a local .sqlite3 path.
    _db_name = os.environ.get("DATABASE_NAME", "")
    if _db_name:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": BASE_DIR / _db_name,
            }
        }
    else:
        raise RuntimeError(
            "No PostgreSQL configuration found.  Set DATABASE_URL or "
            "DATABASE_ENGINE=postgresql with DATABASE_NAME/DATABASE_USER/"
            "DATABASE_PASSWORD variables.  SQLite is not supported for "
            "normal INTERMILAN development."
        )

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
