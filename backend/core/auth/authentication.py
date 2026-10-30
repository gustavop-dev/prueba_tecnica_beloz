from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header

from core.auth.principals import resolve

KEYWORD = b"bearer"


class StaticTokenAuthentication(BaseAuthentication):
    """`Authorization: Bearer <token>` → `Principal` (SEC-AUTH-1..4)."""

    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if not header:
            return None
        if len(header) != 2 or header[0].lower() != KEYWORD:
            raise exceptions.AuthenticationFailed()
        try:
            token = header[1].decode()
        except UnicodeDecodeError:
            raise exceptions.AuthenticationFailed() from None
        principal = resolve(token)
        if principal is None:
            raise exceptions.AuthenticationFailed()
        return principal, None

    def authenticate_header(self, request):
        # A WWW-Authenticate value makes DRF answer 401 instead of 403 for missing credentials.
        return "Bearer"
