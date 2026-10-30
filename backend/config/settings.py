"""Django settings. Every value comes from the environment; see `.env.example`."""

from pathlib import Path

from config import env

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = env.required("DJANGO_SECRET_KEY")
DEBUG = env.flag("DJANGO_DEBUG")
ALLOWED_HOSTS = env.csv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

# Static API tokens: comma-separated `role:principal_id:token` (SEC-AUTH-1..3, SEC-SECRET-1).
# Parsed and validated at startup by core.auth (fail closed).
AUTH_TOKENS = env.required("AUTH_TOKENS")

# Provider webhook (SEC-HOOK-1..5): shared HMAC-SHA256 secret and the provider's principal id.
WEBHOOK_SIGNING_SECRET = env.required("WEBHOOK_SIGNING_SECRET")
PAYMENT_PROVIDER_ID = env.required("PAYMENT_PROVIDER_ID")
WEBHOOK_TOLERANCE_SECONDS = 300

# Longest wait for a row lock before a money operation gives up with LOCK_TIMEOUT (CON-9).
DB_LOCK_TIMEOUT_MS = int(env.optional("DB_LOCK_TIMEOUT_MS", "5000"))

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "rest_framework",
    # Operations core: owns no models; registered to host management commands.
    "core",
    "accounts",
    "collateral",
    "cards",
    "ledger",
    "idempotency",
    "audit",
]

MIDDLEWARE = [
    "core.observability.request_id.RequestIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# PostgreSQL in every environment, tests included (STACK-3). There is no fallback engine.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env.required("POSTGRES_DB"),
        "USER": env.required("POSTGRES_USER"),
        "PASSWORD": env.required("POSTGRES_PASSWORD"),
        "HOST": env.required("POSTGRES_HOST"),
        "PORT": env.optional("POSTGRES_PORT", "5432"),
        "OPTIONS": {"connect_timeout": 3},
        "TEST": {"NAME": env.optional("TEST_DB_NAME", f"test_{env.required('POSTGRES_DB')}")},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": ["core.auth.authentication.StaticTokenAuthentication"],
    # Every view declares its role explicitly; without one, nothing is allowed.
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "UNAUTHENTICATED_USER": None,
    "EXCEPTION_HANDLER": "core.api.errors.exception_handler",
}

# Structured JSON logs on stdout, one object per line with the request id (OBS-1..3).
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "core.observability.json_logging.JsonFormatter"}},
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json",
        }
    },
    "root": {"handlers": ["stdout"], "level": env.optional("LOG_LEVEL", "INFO")},
    "loggers": {"django": {"handlers": ["stdout"], "level": "WARNING", "propagate": False}},
}
