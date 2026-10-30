"""Role permissions (SEC-ROLE-1). Object ownership is enforced again in the flows (SEC-ROLE-2)."""

from rest_framework.permissions import BasePermission

from core.auth.principals import Principal, Role


class _HasRole(BasePermission):
    role: Role

    def has_permission(self, request, view):
        principal = request.user
        return isinstance(principal, Principal) and principal.role == self.role


class IsCustomer(_HasRole):
    role = Role.CUSTOMER


class IsPaymentProvider(_HasRole):
    role = Role.PAYMENT_PROVIDER
