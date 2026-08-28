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

# Behind a TLS-terminating proxy Django sees plain http, so its CSRF origin check
# compares the browser's https Origin against an http one and rejects every POST
# — rating included. CSRF_TRUSTED_ORIGINS is the fix; SECURE_PROXY_SSL_HEADER
# additionally makes request.is_secure() tell the truth, so the secure cookies
# are actually set. Both are inert unless the deployment sets the variables.
# Comma-separated, scheme included, no spaces (nothing here strips).
CSRF_TRUSTED_ORIGINS = [
    origin
    for origin in os.environ.get("MELRATER_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin
]
if os.environ.get("MELRATER_BEHIND_TLS_PROXY") == "1":
    # Trustworthy only because the app port is never published: the proxy always
    # overwrites X-Forwarded-Proto, and nothing else can reach the app server.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_typer",
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

# With DEBUG off, Django's default `django` logger keeps its console handler
# behind RequireDebugTrue and its only other handler mails ADMINS, which is
# empty — so a 500 in a container renders a bare error page and leaves nothing
# whatsoever in `docker logs`. Send tracebacks to stderr instead.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}
    },
    "handlers": {"stderr": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "loggers": {
        "django.request": {
            "handlers": ["stderr"],
            "level": "ERROR",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["stderr"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "run-list"
LOGOUT_REDIRECT_URL = "login"
