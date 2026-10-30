from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"

    def ready(self):
        from core.auth.principals import validate_configuration

        # Fail closed at startup when the API tokens are malformed (SEC-SECRET-3).
        validate_configuration()
