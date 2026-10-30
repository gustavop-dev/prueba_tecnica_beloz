"""HTTP endpoints of docs/api.md §2. Views validate input, call one flow and present the result."""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core import flows
from core.api import presenters
from core.api.serializers import (
    AmountRequest,
    EmptyRequest,
    OpenAccountRequest,
    ProviderReportRequest,
)
from core.auth.permissions import IsCustomer, IsPaymentProvider


def _validated(serializer_class, request) -> dict:
    serializer = serializer_class(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class AccountsView(APIView):
    """POST /accounts — request an account opening (creates the PENDING deposit)."""

    permission_classes = [IsCustomer]

    def post(self, request):
        data = _validated(OpenAccountRequest, request)
        deposit = flows.request_account_opening(
            owner_id=request.user.id, deposit_amount_minor=data["deposit_amount_minor"]
        )
        return Response(presenters.opening(deposit), status=status.HTTP_201_CREATED)


class ProviderWebhookView(APIView):
    """POST /webhooks/payments — the provider reports the deposit outcome."""

    permission_classes = [IsPaymentProvider]

    def post(self, request):
        data = _validated(ProviderReportRequest, request)
        result = flows.apply_provider_report(
            deposit_id=data["deposit_id"], succeeded=data["outcome"] == "succeeded"
        )
        return Response(presenters.provider_report(result))


class CardAuthorizationsView(APIView):
    """POST /cards/{id}/authorizations — purchase."""

    permission_classes = [IsCustomer]

    def post(self, request, card_id):
        data = _validated(AmountRequest, request)
        result = flows.authorize_purchase(
            owner_id=request.user.id, card_id=card_id, amount_minor=data["amount_minor"]
        )
        return Response(presenters.authorization(result), status=status.HTTP_201_CREATED)


class RepaymentsView(APIView):
    """POST /accounts/{id}/repayments — payment."""

    permission_classes = [IsCustomer]

    def post(self, request, account_id):
        data = _validated(AmountRequest, request)
        result = flows.repay(
            owner_id=request.user.id, account_id=account_id, amount_minor=data["amount_minor"]
        )
        return Response(presenters.repayment(result), status=status.HTTP_201_CREATED)


class CloseAccountView(APIView):
    """POST /accounts/{id}/close — closure."""

    permission_classes = [IsCustomer]

    def post(self, request, account_id):
        _validated(EmptyRequest, request)
        result = flows.close(owner_id=request.user.id, account_id=account_id)
        return Response(presenters.closure(result))


class AccountDetailView(APIView):
    """GET /accounts/{id} — status, limit, debt, available credit, deposit and card."""

    permission_classes = [IsCustomer]

    def get(self, request, account_id):
        view = flows.get_account_view(owner_id=request.user.id, account_id=account_id)
        return Response(presenters.account_view(view))


class AccountLedgerView(APIView):
    """GET /accounts/{id}/ledger — the account's journal entries."""

    permission_classes = [IsCustomer]

    def get(self, request, account_id):
        entries = flows.get_ledger_entries(owner_id=request.user.id, account_id=account_id)
        return Response(presenters.ledger_entries(account_id, entries))
