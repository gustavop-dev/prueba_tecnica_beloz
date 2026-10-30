"""Data migration: create the two global ledger accounts (docs/ledger.md §3)."""

from django.db import migrations

GLOBAL_ACCOUNTS = (
    ("CASH", "ASSET"),
    ("MERCHANT_PAYABLE", "LIABILITY"),
)


def create_global_accounts(apps, schema_editor):
    LedgerAccount = apps.get_model("ledger", "LedgerAccount")
    for code, kind in GLOBAL_ACCOUNTS:
        LedgerAccount.objects.get_or_create(
            code=code, credit_account_id=None, defaults={"kind": kind}
        )


def delete_global_accounts(apps, schema_editor):
    LedgerAccount = apps.get_model("ledger", "LedgerAccount")
    LedgerAccount.objects.filter(
        code__in=[code for code, _ in GLOBAL_ACCOUNTS], credit_account_id=None
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_global_accounts, delete_global_accounts),
    ]
