"""
Management command to migrate legacy TransactionDetail rows from 4-digit unit_code to 6-digit official satker_code.

This command safely migrates TransactionDetail rows where satker_code is a known 4-digit
unit code to the corresponding official 6-digit satker code.

Usage:
    python manage.py migrate_legacy_satker_codes --dry-run
    python manage.py migrate_legacy_satker_codes

This is idempotent - running it twice is safe.
"""

from django.core.management.base import BaseCommand

from apps.core.models import SatkerMaster
from apps.dk.models import TransactionDetail


class Command(BaseCommand):
    help = "Migrate legacy TransactionDetail rows from 4-digit unit_code to 6-digit official satker_code"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be migrated without making changes",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)

        # Get known unit codes
        unit_codes = set(SatkerMaster.objects.values_list('unit_code', flat=True))

        # Find TransactionDetail rows with 4-digit satker codes
        four_digit_rows = TransactionDetail.objects.filter(satker_code__in=unit_codes)

        # Count before migration
        total_count = four_digit_rows.count()
        if total_count == 0:
            self.stdout.write("No TransactionDetail rows with 4-digit satker_code found.")
            return

        self.stdout.write(f"Found {total_count} TransactionDetail rows with 4-digit satker_code.")

        # Build mapping
        unit_to_satker = {
            sm.unit_code: sm.satker_code
            for sm in SatkerMaster.objects.all()
        }

        # Group by unit_code
        migrated = 0
        skipped = 0
        unchanged = 0

        for row in four_digit_rows:
            old_code = row.satker_code
            new_code = unit_to_satker.get(old_code)

            if not new_code:
                skipped += 1
                self.stdout.write(
                    f"  SKIP: Row {row.id} has unknown unit_code {old_code}"
                )
                continue

            if old_code == new_code:
                unchanged += 1
                continue

            if dry_run:
                self.stdout.write(
                    f"  [DRY-RUN] Would migrate row {row.id}: {old_code} -> {new_code}"
                )
            else:
                row.satker_code = new_code
                row.save(update_fields=['satker_code', 'updated_at'])
                migrated += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  Migrated row {row.id}: {old_code} -> {new_code}"
                    )
                )

        # Summary
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 60))

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes were made"))
            self.stdout.write(f"  Would migrate: {migrated}")
            self.stdout.write(f"  Would skip: {skipped}")
            self.stdout.write(f"  Already correct: {unchanged}")
        else:
            self.stdout.write("Migration complete!")
            self.stdout.write(f"  Migrated: {migrated}")
            self.stdout.write(f"  Skipped: {skipped}")
            self.stdout.write(f"  Already correct: {unchanged}")
            self.stdout.write(f"  Remaining with 4-digit codes: {TransactionDetail.objects.filter(satker_code__in=unit_codes).count()}")

        self.stdout.write("=" * 60)
