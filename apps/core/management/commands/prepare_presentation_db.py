"""
Management command to prepare the database for presentation/demo.

This command clears operational transaction data while preserving structural/master data.

Usage:
    python manage.py prepare_presentation_db --dry-run
    python manage.py prepare_presentation_db --execute

ATOMIC EXECUTION:
    All operational deletions are wrapped in a single database transaction.
    If ANY deletion fails, ALL changes are rolled back.
    Returns exit code 1 on failure.

OPERATIONAL DATA (cleared):
- TransactionDetail: transaction records
- MonitoringSummary: budget/FA16 data
- DocumentDriveLink: document references
- PaketSPMUpload: SPM package uploads
- DRPPUpload: DRPP uploads
- DRPPItem: DRPP line items
- DRPPMatch: DRPP matching records
- SP2DRaw: SP2D records
- SP2DImportBatch: SP2D import batches
- TransactionPackage: transaction packages
- ActiveParentSession: active parent sessions
- DRPPPreviewState: DRPP preview state
- TransactionProvenance: transaction provenance records
- ChecklistStatus: document checklist status
- AuditLog: audit log entries

STRUCTURAL DATA (PRESERVED):
- SatkerMaster: unit/satker mapping
- MasterAkun: account codes
- User: user accounts
- Profile: user profiles
- Permission: system permissions
- ContentType: Django content types
- Group: permission groups
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Clear operational transaction data for presentation/demo database"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be cleared without making changes",
        )
        parser.add_argument(
            "--clear-sessions",
            action="store_true",
            help="Also clear Django sessions",
        )
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Execute the clear operation",
        )

    def handle(self, *args, **options):
        dry_run = not options.get("execute", False)
        clear_sessions = options.get("clear_sessions", False)

        # Define operational tables and their counts
        operational_tables = [
            ("dk", "TransactionDetail", "transaction details"),
            ("core", "MonitoringSummary", "monitoring summaries"),
            ("documents", "DocumentDriveLink", "document links"),
            ("documents", "DocumentUpload", "document uploads"),
            ("paket_spm", "PaketSPMUpload", "SPM uploads"),
            ("paket_spm", "PaketSPMPreviewItem", "SPM preview items"),
            ("drpp", "DRPPUpload", "DRPP uploads"),
            ("drpp", "DRPPItem", "DRPP items"),
            ("drpp", "DRPPMatch", "DRPP matches"),
            ("drpp", "DRPPImportBatch", "DRPP import batches"),
            ("sp2d", "SP2DRaw", "SP2D records"),
            ("sp2d", "SP2DImportBatch", "SP2D import batches"),
            ("core", "TransactionPackage", "transaction packages"),
            ("core", "ActiveParentSession", "active parent sessions"),
            ("core", "DRPPPreviewState", "DRPP preview states"),
            ("core", "TransactionProvenance", "transaction provenance records"),
            ("documents", "ChecklistStatus", "checklist status records"),
            ("auditlog", "AuditLog", "audit log entries"),
        ]

        # Tables to always preserve (structural/master)
        structural_tables = [
            ("core", "SatkerMaster"),
            ("dk", "MasterAkun"),
            ("auth", "User"),
            ("accounts", "Profile"),
            ("auth", "Permission"),
            ("auth", "Group"),
            ("contenttypes", "ContentType"),
        ]

        # Sessions table (optional)
        if clear_sessions:
            operational_tables.append(("sessions", "Session", "user sessions"))

        self.stdout.write("=" * 60)
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes will be made"))
        self.stdout.write("=" * 60)

        # Show structural data that will be preserved
        self.stdout.write("\nSTRUCTURAL DATA (will be preserved):")
        for app, model in structural_tables:
            try:
                from django.apps import apps
                m = apps.get_model(app, model)
                count = m.objects.count()
                self.stdout.write("  {}.{}: {} rows".format(app, model, count))
            except Exception as e:
                self.stdout.write("  {}.{}: error - {}".format(app, model, e))

        # Count operational data
        self.stdout.write("\nOPERATIONAL DATA (will be cleared):")
        total_operational = 0
        for app, model, label in operational_tables:
            try:
                from django.apps import apps
                m = apps.get_model(app, model)
                count = m.objects.count()
                if count > 0:
                    self.stdout.write("  {}.{}: {} {}".format(app, model, count, label))
                    total_operational += count
            except Exception as e:
                self.stdout.write("  {}.{}: error - {}".format(app, model, e))

        if total_operational == 0:
            self.stdout.write(self.style.SUCCESS("\nDatabase is already clean for presentation."))
            return

        if dry_run:
            self.stdout.write("\nTotal operational rows that would be cleared: {}".format(total_operational))
            self.stdout.write("\nRun with --execute to clear these records.")
            return

        # Execute the clear
        self.stdout.write("\nClearing operational data...")
        self.stdout.write("(All deletions are wrapped in a single atomic transaction)")

        deletion_summary = []

        try:
            with transaction.atomic():
                # Delete in dependency order (children before parents)
                # Reverse the order so FK-referenced tables are cleared first
                for app, model, label in reversed(operational_tables):
                    from django.apps import apps
                    m = apps.get_model(app, model)
                    count = m.objects.count()
                    if count > 0:
                        deleted, _ = m.objects.all().delete()
                        deletion_summary.append((app, model, label, deleted))
                        self.stdout.write(
                            "  Deleted {} {}.{} rows".format(deleted, app, model)
                        )

                self.stdout.write(self.style.SUCCESS(
                    "\nAll {} operational deletions committed atomically.".format(len(deletion_summary))
                ))

        except Exception as e:
            error_type = type(e).__name__
            self.stdout.write(
                "\nATOMIC ROLLBACK: Deletion failed: {} - {}".format(error_type, e)
            )
            self.stdout.write(
                "ALL changes have been rolled back. Database is unchanged."
            )
            self.stdout.write(
                "Fix the issue and re-run with --execute."
            )
            raise CommandError("Atomic reset failed: {} - {}".format(error_type, e))

        # Verify counts after clearing
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("VERIFICATION (after clearing):")
        self.stdout.write("=" * 60)

        remaining_operational = 0
        for app, model, label in operational_tables:
            try:
                from django.apps import apps
                m = apps.get_model(app, model)
                count = m.objects.count()
                if count > 0:
                    self.stdout.write(self.style.WARNING("  WARNING: {}.{} still has {} rows".format(app, model, count)))
                    remaining_operational += count
            except Exception:
                pass

        if remaining_operational == 0:
            self.stdout.write(self.style.SUCCESS("\nAll operational data cleared successfully."))
            self.stdout.write("\nStructural data preserved:")
            for app, model in structural_tables:
                try:
                    from django.apps import apps
                    m = apps.get_model(app, model)
                    count = m.objects.count()
                    self.stdout.write("  {}.{}: {} rows preserved".format(app, model, count))
                except Exception:
                    pass
            return  # Success
        else:
            self.stdout.write(self.style.ERROR("\nWARNING: {} operational rows remain".format(remaining_operational)))
            raise CommandError("Some operational rows remain after reset.")
            self.stdout.write("Some tables may have circular FK references - manual cleanup needed.")
