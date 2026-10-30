"""Gunicorn settings: the application logs every request itself, so gunicorn only logs its own
lifecycle, in the same JSON format (OBS-1)."""

bind = "0.0.0.0:8000"
workers = 2
accesslog = None

logconfig_dict = {
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
    # Gunicorn merges this dict over its defaults, whose root logger points at a "console" handler
    # that no longer exists here: the root must be redefined.
    "root": {"handlers": ["stdout"], "level": "INFO"},
    "loggers": {
        "gunicorn.error": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
        "gunicorn.access": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
    },
}
