from django.core.management.base import BaseCommand, CommandError

from core.reconciliation import find_mismatches


class Command(BaseCommand):
    help = "Verify that the balance cache and the deposits match the ledger. Read-only."

    def handle(self, *args, **options):
        mismatches = find_mismatches()
        for mismatch in mismatches:
            target = f"account={mismatch.account_id}" if mismatch.account_id else ""
            target = target or f"entry={mismatch.entry_id}"
            self.stdout.write(
                f"MISMATCH {mismatch.check} {target} "
                f"expected={mismatch.expected} actual={mismatch.actual}"
            )
        if mismatches:
            raise CommandError(f"{len(mismatches)} mismatch(es) found.")
        self.stdout.write("OK: cache, deposits and ledger are consistent.")
