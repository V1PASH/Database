"""Settings for the voice agent service.

The interesting part is ``MINIDB_PATH``: everything this service stores
lives in one minidb database file, in two collections -- ``models`` and
``agent``.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-a-real-secret")

# Django's own tables (migrations, content types). The service's data does
# not live here -- it lives in minidb.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "django.sqlite3",
    }
}

# The minidb database holding the models and the agents.
MINIDB_PATH = os.environ.get("MINIDB_PATH", str(BASE_DIR / "voice.db"))

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "agents",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]

ROOT_URLCONF = "voice_service.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

WSGI_APPLICATION = "voice_service.wsgi.application"
ASGI_APPLICATION = "voice_service.asgi.application"

AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# No sessions, no messages: this is an API.
SESSION_ENGINE = "minidb.django.sessions"
CACHES = {
    "default": {
        "BACKEND": "minidb.django.cache.MinidbCache",
        "LOCATION": MINIDB_PATH,
    }
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
