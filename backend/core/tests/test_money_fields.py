from django.apps import apps
from django.db import models

DOMAIN_APPS = ["accounts", "collateral", "cards", "ledger", "idempotency", "audit"]


def _domain_fields():
    return [
        field
        for label in DOMAIN_APPS
        for model in apps.get_app_config(label).get_models()
        for field in model._meta.get_fields()
        if isinstance(field, models.Field)
    ]


def _label(field):
    return f"{field.model.__name__}.{field.name}"


def _float_or_decimal_fields():
    return [
        _label(field)
        for field in _domain_fields()
        if isinstance(field, models.FloatField | models.DecimalField)
    ]


def _minor_fields_that_are_not_big_integers():
    return [
        _label(field)
        for field in _domain_fields()
        if field.name.endswith("_minor") and type(field) is not models.BigIntegerField
    ]


def test_no_model_declares_float_or_decimal_fields():
    """Fails if any model stores values as float or decimal (BR-MONEY-3)."""
    assert _float_or_decimal_fields() == []


def test_every_minor_amount_field_is_a_big_integer():
    """Fails if a `*_minor` money field uses a type other than BigIntegerField (BR-MONEY-2)."""
    assert _minor_fields_that_are_not_big_integers() == []
