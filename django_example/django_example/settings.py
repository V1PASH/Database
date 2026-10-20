"""A small Django project that keeps its data in minidb.

    python manage.py migrate          # Django's own tables
    python manage.py runserver         # http://127.0.0.1:8000/

The models that inherit from ``MinidbModel`` are stored in minidb; the rest
are ordinary Django models stored by Django itself.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Development settings. Turn DEBUG off and set real hosts before deploying.
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]

# In development this is fine; use a real secret in production.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-a-real-secret")

# Django's own tables still need a relational database -- for migrations,
# the content types table and the admin site.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "django.sqlite3",
    }
}

# The minidb database, holding the application's own documents.
MINIDB_PATH = os.environ.get("MINIDB_PATH", str(BASE_DIR / "app.db"))

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "blog",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "django_example.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "django_example.wsgi.application"

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# The session and the cache live in minidb too.
SESSION_ENGINE = "minidb.django.sessions"
CACHES = {
    "default": {
        "BACKEND": "minidb.django.cache.MinidbCache",
        "LOCATION": MINIDB_PATH,
    }
}

LOGIN_URL = "login"
