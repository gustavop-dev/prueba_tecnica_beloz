"""Strict request serializers (SEC-INPUT-1..4, BR-MONEY-4/5).

Unknown fields are rejected, amounts must be JSON integers in cents, identifiers must be UUIDs.
"""

from collections.abc import Mapping

from rest_framework import serializers

from core.money import InvalidAmount, parse_amount_minor


class AmountMinorField(serializers.Field):
    """A strictly positive JSON integer. Floats (even `10.0`), strings and booleans are rejected."""

    def to_internal_value(self, data):
        try:
            return parse_amount_minor(data)
        except InvalidAmount as exc:
            raise serializers.ValidationError(str(exc)) from None

    def to_representation(self, value):
        return value


class StrictSerializer(serializers.Serializer):
    """Rejects unknown fields instead of ignoring them (SEC-INPUT-3)."""

    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            unknown = sorted(set(data) - set(self.fields))
            if unknown:
                raise serializers.ValidationError({name: ["Unknown field."] for name in unknown})
        return super().to_internal_value(data)


class OpenAccountRequest(StrictSerializer):
    deposit_amount_minor = AmountMinorField()


class ProviderReportRequest(StrictSerializer):
    OUTCOMES = ("succeeded", "failed")

    deposit_id = serializers.UUIDField(format="hex_verbose")
    outcome = serializers.ChoiceField(choices=OUTCOMES)


class AmountRequest(StrictSerializer):
    amount_minor = AmountMinorField()


class EmptyRequest(StrictSerializer):
    """Body for operations that take no parameters: `{}` or no body at all."""
