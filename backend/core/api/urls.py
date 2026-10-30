from django.urls import path

from core.api import views

urlpatterns = [
    path("accounts", views.AccountsView.as_view(), name="accounts"),
    path("accounts/<uuid:account_id>", views.AccountDetailView.as_view(), name="account-detail"),
    path(
        "accounts/<uuid:account_id>/ledger",
        views.AccountLedgerView.as_view(),
        name="account-ledger",
    ),
    path(
        "accounts/<uuid:account_id>/repayments",
        views.RepaymentsView.as_view(),
        name="account-repayments",
    ),
    path(
        "accounts/<uuid:account_id>/close", views.CloseAccountView.as_view(), name="account-close"
    ),
    path(
        "cards/<uuid:card_id>/authorizations",
        views.CardAuthorizationsView.as_view(),
        name="card-authorizations",
    ),
    path("cards/<uuid:card_id>/freeze", views.CardFreezeView.as_view(), name="card-freeze"),
    path("cards/<uuid:card_id>/unfreeze", views.CardUnfreezeView.as_view(), name="card-unfreeze"),
    path("webhooks/payments", views.ProviderWebhookView.as_view(), name="webhook-payments"),
]
