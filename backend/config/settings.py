"""Django settings. Every value comes from the environment; see `.env.example`."""

from pathlib import Path

from config import env

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = env.required("DJANGO_SECRET_KEY")
DEBUG = env.flag("DJANGO_DEBUG")
ALLOWED_HOSTS = env.csv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

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
    # Authentication and permissions are provided by core.auth (F2).
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
}
