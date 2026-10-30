"""Error envelope and the error glossary of docs/api.md §3–4.

Every error leaves the API as `{"error": {"code", "message", "request_id", "details"}}`. Domain
exceptions from the apps are mapped here; anything unexpected becomes `INTERNAL_ERROR` without
internal details.
"""

import logging

from django.http import JsonResponse
from rest_framework import exceptions as drf
from rest_framework.response import Response

from accounts import services as accounts
from cards import services as cards
from collateral import services as collateral
from core.auth.webhook import InvalidSignature, SignatureExpired
from core.db import is_lock_timeout
from core.money import InvalidAmount
from idempotency import services as idempotency

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """An error the API layer itself raises, already carrying its glossary code."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


# (exception type, HTTP status, code, message). The first matching entry wins, so subclasses
# (DepositNotPending ⊂ collateral.InvalidTransition) come before their parents.
DOMAIN_ERRORS = [
    (collateral.DepositNotPending, 409, "DEPOSIT_NOT_PENDING", "The deposit is no longer pending."),
    (accounts.AccountNotFound, 404, "NOT_FOUND", "Resource not found."),
    (cards.CardNotFound, 404, "NOT_FOUND", "Resource not found."),
    (collateral.DepositNotFound, 404, "NOT_FOUND", "Resource not found."),
    (accounts.AccountClosed, 409, "ACCOUNT_CLOSED", "The account is closed."),
    (cards.CardNotActive, 409, "CARD_NOT_ACTIVE", "The card is not active."),
    (
        accounts.InsufficientCredit,
        422,
        "INSUFFICIENT_CREDIT",
        "The purchase amount exceeds the available credit.",
    ),
    (
        accounts.PaymentExceedsDebt,
        422,
        "PAYMENT_EXCEEDS_DEBT",
        "The payment amount exceeds the outstanding debt.",
    ),
    (
        accounts.InvalidTransition | cards.InvalidTransition | collateral.InvalidTransition,
        409,
        "INVALID_STATE_TRANSITION",
        "The operation is not allowed in the current state.",
    ),
    (InvalidAmount, 400, "VALIDATION_ERROR", "The request is invalid."),
    (
        idempotency.KeyReusedWithDifferentRequest,
        422,
        "IDEMPOTENCY_KEY_REUSED",
        "This Idempotency-Key was already used for a different request.",
    ),
]

DRF_ERRORS = [
    (InvalidSignature, "INVALID_SIGNATURE", "The webhook signature is missing or does not match."),
    (SignatureExpired, "SIGNATURE_EXPIRED", "The webhook timestamp is outside the allowed window."),
    (drf.NotAuthenticated, "UNAUTHENTICATED", "Authentication credentials are missing or invalid."),
    (
        drf.AuthenticationFailed,
        "UNAUTHENTICATED",
        "Authentication credentials are missing or invalid.",
    ),
    (drf.PermissionDenied, "FORBIDDEN", "This role may not use this endpoint."),
    (drf.NotFound, "NOT_FOUND", "Resource not found."),
    (drf.ParseError, "VALIDATION_ERROR", "The request body is not valid JSON."),
    (drf.ValidationError, "VALIDATION_ERROR", "The request is invalid."),
    (drf.MethodNotAllowed, "METHOD_NOT_ALLOWED", "Method not allowed."),
    (drf.UnsupportedMediaType, "UNSUPPORTED_MEDIA_TYPE", "Use application/json."),
]


def envelope(request, *, code: str, message: str, details=None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(request, "request_id", None),
            "details": details if details is not None else {},
        }
    }


def exception_handler(exc, context):
    """DRF `EXCEPTION_HANDLER`: maps every exception to the envelope."""
    request = context["request"]
    if isinstance(exc, ApiError):
        return Response(envelope(request, code=exc.code, message=exc.message), status=exc.status)
    if is_lock_timeout(exc):
        return Response(
            envelope(
                request,
                code="LOCK_TIMEOUT",
                message="The account is busy. Retry with the same Idempotency-Key.",
            ),
            status=503,
        )
    for exc_type, status, code, message in DOMAIN_ERRORS:
        if isinstance(exc, exc_type):
            return Response(envelope(request, code=code, message=message), status=status)
    for exc_type, code, message in DRF_ERRORS:
        if isinstance(exc, exc_type):
            details = exc.detail if isinstance(exc, drf.ValidationError) else None
            headers = {}
            if isinstance(exc, drf.NotAuthenticated | drf.AuthenticationFailed):
                view = context.get("view")
                scheme = view.get_authenticate_header(request) if view is not None else None
                headers["WWW-Authenticate"] = scheme or "Bearer"
            return Response(
                envelope(request, code=code, message=message, details=details),
                status=exc.status_code,
                headers=headers,
            )
    logger.exception("request.unhandled_error")
    return Response(
        envelope(request, code="INTERNAL_ERROR", message="Unexpected error."), status=500
    )


def not_found(request, exception=None):
    """`handler404`: URLs that match no route, e.g. a malformed UUID in the path."""
    return JsonResponse(
        envelope(request, code="NOT_FOUND", message="Resource not found."), status=404
    )


def server_error(request):
    """`handler500`: errors raised outside DRF views."""
    return JsonResponse(
        envelope(request, code="INTERNAL_ERROR", message="Unexpected error."), status=500
    )
