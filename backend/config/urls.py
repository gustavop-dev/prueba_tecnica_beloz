from django.urls import include, path

from core.observability.health import health

urlpatterns = [
    path("health", health, name="health"),
    path("", include("core.api.urls")),
]

handler404 = "core.api.errors.not_found"
handler500 = "core.api.errors.server_error"
