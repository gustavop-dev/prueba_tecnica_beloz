from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"

    def ready(self):
        from core.auth import principals, webhook

        # Fail closed at startup when tokens or webhook settings are malformed (SEC-SECRET-3).
        principals.validate_configuration()
        webhook.validate_configuration()
