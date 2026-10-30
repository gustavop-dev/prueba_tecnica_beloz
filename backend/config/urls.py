from django.urls import path

from core.observability.health import health

urlpatterns = [
    path("health", health, name="health"),
]
