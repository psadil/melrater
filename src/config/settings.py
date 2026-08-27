"""Django settings for melrater.

A small multi-reviewer tool: SQLite in production (WAL, IMMEDIATE
transactions — see https://alldjango.com/articles/definitive-guide-to-using-django-sqlite-in-production),
server-rendered templates with htmx, media = pre-rendered component montages.
"""

import os
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent
BASE_DIR = SRC_DIR.parent  # repo root: holds runtime state (db/, media/)

DEBUG = os.environ.get("MELRATER_DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("MELRATER_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(
            "MELRATER_SECRET_KEY must be set when MELRATER_DEBUG=0"
        )
    SECRET_KEY = "django-insecure-dev-only-do-not-use-in-a-public-deployment"
ALLOWED_HOSTS = os.environ.get("MELRATER_ALLOWED_HOSTS", "localhost,127.0.0.1").split(
    ","
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "melrater.core",
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

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [SRC_DIR / "templates"],
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

WSGI_APPLICATION = "config.wsgi.application"

DB_DIR = BASE_DIR / "db"
DB_DIR.mkdir(exist_ok=True)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DB_DIR / "db.sqlite3",
        "OPTIONS": {
            "transaction_mode": "IMMEDIATE",
            "timeout": 5,
            "init_command": (
                "PRAGMA journal_mode=WAL;"
                "PRAGMA synchronous=NORMAL;"
                "PRAGMA mmap_size=134217728;"
                "PRAGMA journal_size_limit=27103364;"
                "PRAGMA cache_size=2000;"
            ),
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# leading slash matches granian's --static-path-route (see the serve task)
STATIC_URL = "/static/"
STATICFILES_DIRS = [SRC_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "run-list"
LOGOUT_REDIRECT_URL = "login"
