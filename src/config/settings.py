"""Django settings for melrater.

A small multi-reviewer tool: SQLite in production (WAL, IMMEDIATE
transactions — see https://alldjango.com/articles/definitive-guide-to-using-django-sqlite-in-production),
server-rendered templates with htmx, media = pre-rendered component montages.

Configuration is 12-factor: every deployment-specific value is a MELRATER_*
environment variable read through django-environ, and every default is the one
that is safe to run in public.
"""

from pathlib import Path

import environ
from django.utils.csp import CSP

SRC_DIR = Path(__file__).resolve().parent.parent
BASE_DIR = SRC_DIR.parent  # repo root: holds runtime state (db/, media/)

env = environ.Env()

# Convenience for running outside `pixi run` (which supplies the dev values from
# pixi.toml's [activation.env]). Absent in the container, and read_env never
# overwrites a variable that is already set, so the real environment always wins
# and "which config is deployed" stays a question with one answer.
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)


def _csv(name: str, default: str = "") -> list[str]:
    """Comma-separated environment value -> list, tolerant of stray whitespace.

    Neither `str.split(",")` nor django-environ's own list cast strips, and a
    single " 127.0.0.1" in ALLOWED_HOSTS 400s every request while logging
    nothing that says why.
    """
    return [part.strip() for part in env.str(name, default).split(",") if part.strip()]


# Off unless something says otherwise. The reverse — defaulting on — makes
# `docker run` of this image with no configuration a debug-traceback server
# signing sessions with the throwaway key below, which is committed to this
# repository. A source checkout opts in through pixi.toml's [activation.env],
# which cannot reach the runtime image; MELRATER_DEBUG still overrides that in
# either direction.
DEBUG = env.bool("MELRATER_DEBUG", default=env.bool("MELRATER_DEV", default=False))

SECRET_KEY = env.str("MELRATER_SECRET_KEY", default="")
if not SECRET_KEY:
    if not DEBUG:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(
            "MELRATER_SECRET_KEY must be set when MELRATER_DEBUG=0"
        )
    # keeps the `django-insecure-` prefix so `check --deploy` (W009) still
    # objects if this ever escapes into a real deployment
    SECRET_KEY = "django-insecure-dev-only-do-not-use-in-a-public-deployment"

ALLOWED_HOSTS = _csv("MELRATER_ALLOWED_HOSTS", "localhost,127.0.0.1")

# Behind a TLS-terminating proxy Django sees plain http, so its CSRF origin check
# compares the browser's https Origin against an http one and rejects every POST
# — rating included. CSRF_TRUSTED_ORIGINS is the fix; SECURE_PROXY_SSL_HEADER
# additionally makes request.is_secure() tell the truth, so the secure cookies
# are actually set. Both are inert unless the deployment sets the variables.
# Comma-separated, scheme included.
CSRF_TRUSTED_ORIGINS = _csv("MELRATER_CSRF_TRUSTED_ORIGINS")

BEHIND_TLS_PROXY = env.bool("MELRATER_BEHIND_TLS_PROXY", default=False)
if BEHIND_TLS_PROXY:
    # Trustworthy only because the app port is never published: the proxy always
    # overwrites X-Forwarded-Proto, and nothing else can reach the app server.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Nothing reads the CSRF cookie from JavaScript — base.html injects the token
# server-side into htmx's hx-headers — so the browser can keep it from scripts.
CSRF_COOKIE_HTTPONLY = True

# W004 (HSTS): browsers do not apply HSTS to IP literals, and this deployment is
# addressed by IP (deploy.md §1.2). W008 (SSL redirect): Caddy already redirects
# http->https, and doing it again in Django would only add a hop. Silenced
# deliberately, so that a *new* warning from `check --deploy` is visible rather
# than lost in two that will never be actioned.
# mail.E001: the dummy backend is the point — no view sends mail, and the
# alternative default dials SMTP on localhost:25 (see MAILERS below).
SILENCED_SYSTEM_CHECKS = ["security.W004", "security.W008", "mail.E001"]

# No mail is sent and no mail is wanted: the password-reset views are not
# routed (see config/urls.py) and accounts are issued by `create_rater`. The
# default backend would dial SMTP on localhost:25 and 500. MAILERS rather than
# EMAIL_BACKEND, which Django 6.1 deprecates and 7.0 removes — and which
# Django refuses to read at all once MAILERS is defined.
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.dummy.EmailBackend"}}

# 'unsafe-inline' for styles only: the templates carry style="color:{{ ... }}"
# attributes (core/partials/metric_row.html, core/component_detail.html), and a
# nonce cannot cover a style *attribute*. Scripts get 'self' for the two
# vendored/local files plus the nonce Django's admin templates emit.
SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF, CSP.NONCE],
    "style-src": [CSP.SELF, CSP.UNSAFE_INLINE],
    "img-src": [CSP.SELF],
    "connect-src": [CSP.SELF],
    "form-action": [CSP.SELF],
    "base-uri": [CSP.SELF],
    "object-src": [CSP.NONE],
    "frame-ancestors": [CSP.NONE],
}

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_typer",
    "axes",
    "melrater.core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # must be last: it wraps authentication to observe the outcome
    "axes.middleware.AxesMiddleware",
]

# ---------------------------------------------------------------------------
# Login throttling (django-axes).
# ---------------------------------------------------------------------------
# AxesStandaloneBackend goes first and short-circuits a locked-out attempt
# before ModelBackend ever hashes a password.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 0.25  # hours
AXES_RESET_ON_SUCCESS = True
# Lock the (address, username) *pair*, not either alone. Locking on username
# alone lets anyone freeze a known reviewer out; locking on address alone would,
# behind one shared proxy, freeze out everybody at once.
AXES_LOCKOUT_PARAMETERS = [["ip_address", "username"]]

if BEHIND_TLS_PROXY:
    # REMOTE_ADDR is Caddy's container address, so the client has to come from
    # X-Forwarded-For — but only in a way a client cannot forge. Caddy does not
    # append *itself* to that header, it appends the peer it heard from, so a
    # request that arrives with no XFF reaches Django with exactly one entry.
    # Hence 0, not the intuitive 1. Measured against python-ipware:
    #
    #   proxy_count | honest client | client sends its own X-Forwarded-For
    #   ------------+---------------+-------------------------------------
    #   None        | real IP       | forged IP, trusted
    #   0           | real IP       | falls back to Caddy's IP (no forgery)
    #   1           | None at all   | forged IP, trusted
    AXES_IPWARE_META_PRECEDENCE_ORDER = ("HTTP_X_FORWARDED_FOR", "REMOTE_ADDR")
    AXES_IPWARE_PROXY_COUNT = 0

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [SRC_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                # feeds {{ csp_nonce }}; the admin's templates use it, and
                # without it SECURE_CSP's CSP.NONCE would name a nonce in the
                # header that no <script> ever carries
                "django.template.context_processors.csp",
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

# Montages are ordinary Django media: written through `default_storage` (see
# melrater/core/storage.py for their layout) and served from MEDIA_ROOT by the
# login-required view in config/urls.py. No STORAGES dict — Django's own
# defaults are exactly FileSystemStorage + StaticFilesStorage, and a named
# alias for montages alone bought a seam only half of the code went through.
#
# Deliberately left relative and unpinned: `settings.MEDIA_URL` is a property
# that prepends the script prefix, and a storage backend configured with an
# explicit base_url would capture the bare "media/" literal instead — the
# difference between /media/runs/... and a relative URL that resolves under
# /runs/<id>/ic/<n>/ and 404s.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# ---------------------------------------------------------------------------
# Run ingest API (melrater/core/api.py).
# ---------------------------------------------------------------------------
# Follows DEBUG, and for the same reason it exists: a source checkout gets the
# endpoint for free, while `docker run` of this image with no configuration
# does not expose a write API onto the montage store. compose.yaml opts in.
INGEST_ENABLED = env.bool("MELRATER_INGEST_ENABLED", default=DEBUG)

# The API's own ceilings, sized against the deployed box (2 vCPU / 4 GB, the
# container capped at 1500m) and a real run: 96 components -> 288 montages,
# ~20 MB of tar and ~2.5 MB of JSON. Note that none of these raises a *Django*
# limit. A pushed run arrives as two multipart FILE parts, and
# DATA_UPLOAD_MAX_MEMORY_SIZE is calculated excluding file upload data, so its
# 2.5 MB default keeps guarding /accounts/login/ and the rating POST untouched
# while a 2.5 MB run payload sails past it.
INGEST_MAX_TAR_BYTES = env.int(
    "MELRATER_INGEST_MAX_TAR_BYTES", default=64 * 1024 * 1024
)
INGEST_MAX_MONTAGE_BYTES = env.int(
    "MELRATER_INGEST_MAX_MONTAGE_BYTES", default=1024 * 1024
)
INGEST_MAX_COMPONENTS = env.int("MELRATER_INGEST_MAX_COMPONENTS", default=256)

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
        # failed logins and lockouts, which nothing else records
        "axes": {"handlers": ["stderr"], "level": "WARNING", "propagate": False},
        # every accepted push, so that runs arriving is a thing the logs show
        # rather than a thing you infer from the run list having grown
        "melrater.ingest": {
            "handlers": ["stderr"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "run-list"
LOGOUT_REDIRECT_URL = "login"
