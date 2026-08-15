"""
Reset all operational data for fresh deployment of INTERMILAN.

WARNING: This command deletes ALL transactional data!
WARNING: Use only for resetting to clean state!

Usage:
    # Preview what will be deleted
    python manage.py reset_operational_data --dry-run

    # Execute with confirmation
    python manage.py reset_operational_data

    # Execute without confirmation
    python manage.py reset_operational_data --force

DATA THAT WILL BE DELETED:
    DK Models:
        - dk_transactiondetail
        - dk_transactionchangelog

    SP2D Models:
        - sp2d_sp2dimportbatch
        - sp2d_sp2draw

    DRPP Models:
        - drpp_drppimportbatch
        - drpp_drppitem
        - drpp_drppmatch
        - drpp_drppupload

    Documents Models:
        - documents_checkliststatus
        - documents_checklisttemplate
        - documents_documentdrivelink
        - documents_documentupload

    Paket SPM Models:
        - paket_spm_paketspmupload
        - paket_spm_paketspmpreviewitem

    Core Models:
        - core_activeparentsession
        - core_drpppreviewstate
        - core_monitoringsummary
        - core_transactionpackage
        - core_transactionprovenance

    Sessions:
        - django_session

DATA THAT WILL BE PROTECTED:
    User & Profile:
        - auth_user (all users)
        - accounts_profile
        - auth_group
        - auth_permission
        - auth_group_permissions

    Master Data:
        - core_satkermaster
        - dk_masterakun

CAUTION:
    - This is destructive! All transactional data will be deleted.
    - Use --dry-run first to preview.
    - Not for regular use - only for deployment reset.
"""

from django.core.management.base import BaseCommand
from django.db import connection, transaction


class Command(BaseCommand):
    help = "Reset all operational data for fresh deployment"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what will be deleted without making changes",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Skip confirmation prompt",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force = options["force"]

        self.stdout.write("")
        self.stdout.write(self.style.ERROR("=" * 70))
        self.stdout.write(self.style.ERROR("  INTERMILAN - RESET OPERATIONAL DATA"))
        self.stdout.write(self.style.ERROR("=" * 70))
        self.stdout.write("")
        self.stdout.write(self.style.ERROR("  WARNING: All transactional data will be deleted!"))
        self.stdout.write(self.style.ERROR("  WARNING: User accounts will be kept."))
        self.stdout.write("")

        if dry_run:
            self.stdout.write(self.style.WARNING("  MODE: DRY RUN - No changes will be made"))
        else:
            self.stdout.write(self.style.ERROR("  MODE: LIVE - Data will be deleted!"))
        self.stdout.write("")

        # Tables to delete (operational data)
        # Order matters for foreign key constraints - delete child tables first
        tables_to_delete = [
            ("documents_checkliststatus", "documents", "ChecklistStatus"),
            ("paket_spm_paketspmpreviewitem", "paket_spm", "PaketSPMPreviewItem"),
            ("drpp_drppmatch", "drpp", "DRPPMatch"),
            ("drpp_drppitem", "drpp", "DRPPItem"),
            ("documents_documentupload", "documents", "DocumentUpload"),
            ("drpp_drppupload", "drpp", "DRPPUpload"),
            ("paket_spm_paketspmupload", "paket_spm", "PaketSPMUpload"),
            ("documents_documentdrivelink", "documents", "DocumentDriveLink"),
            ("drpp_drppimportbatch", "drpp", "DRPPImportBatch"),
            ("sp2d_sp2dimportbatch", "sp2d", "SP2DImportBatch"),
            ("sp2d_sp2draw", "sp2d", "SP2DRaw"),
            ("dk_transactionchangelog", "dk", "TransactionChangeLog"),
            ("dk_transactiondetail", "dk", "TransactionDetail"),
            ("core_drpppreviewstate", "core", "DRPPPreviewState"),
            ("core_monitoringsummary", "core", "MonitoringSummary"),
            ("core_transactionprovenance", "core", "TransactionProvenance"),
            ("core_transactionpackage", "core", "TransactionPackage"),
            ("core_activeparentsession", "core", "ActiveParentSession"),
            ("documents_checklisttemplate", "documents", "ChecklistTemplate"),
        ]

        # Protected tables
        protected_tables = [
            ("auth_user", "auth", "User"),
            ("accounts_profile", "accounts", "Profile"),
            ("auth_group", "auth", "Group"),
            ("auth_permission", "auth", "Permission"),
            ("auth_group_permissions", "auth", "GroupPermission"),
            ("core_satkermaster", "core", "SatkerMaster"),
            ("dk_masterakun", "dk", "MasterAkun"),
            ("django_session", "django.contrib.sessions", "Session"),
        ]

        # Count records to delete
        deletion_plan = []
        total_to_delete = 0

        with connection.cursor() as cursor:
            for table, app, model in tables_to_delete:
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    count = cursor.fetchone()[0]
                    deletion_plan.append({
                        "table": table,
                        "app": app,
                        "model": model,
                        "count": count,
                    })
                    total_to_delete += count
                except Exception as e:
                    deletion_plan.append({
                        "table": table,
                        "app": app,
                        "model": model,
                        "count": 0,
                        "error": str(e),
                    })

        # Count protected records
        protected_counts = {}
        total_protected = 0

        with connection.cursor() as cursor:
            for table, app, model in protected_tables:
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    count = cursor.fetchone()[0]
                    protected_counts[f"{app}.{model}"] = count
                    total_protected += count
                except Exception as e:
                    protected_counts[f"{app}.{model}"] = f"Error: {e}"

        # =====================================================================
        # DISPLAY PREVIEW
        # =====================================================================
        self.stdout.write("")
        self.stdout.write(self.style.ERROR("-" * 70))
        self.stdout.write(self.style.ERROR("  DATA TO BE DELETED:"))
        self.stdout.write(self.style.ERROR("-" * 70))
        self.stdout.write("")

        for item in deletion_plan:
            model_name = f"{item['app']}.{item['model']}"
            if "error" in item:
                self.stdout.write(f"  {model_name:<45} ERROR")
            else:
                self.stdout.write(f"  {model_name:<45} {item['count']:>6} records")

        self.stdout.write("")
        self.stdout.write(self.style.WARNING("-" * 70))
        self.stdout.write(self.style.WARNING("  PROTECTED DATA (Will NOT be deleted):"))
        self.stdout.write(self.style.WARNING("-" * 70))
        self.stdout.write("")

        for name, count in protected_counts.items():
            self.stdout.write(self.style.SUCCESS(f"  {name:<45} {count} records"))

        # =====================================================================
        # SUMMARY
        # =====================================================================
        self.stdout.write("")
        self.stdout.write(self.style.ERROR("=" * 70))
        self.stdout.write(self.style.ERROR("  RESET SUMMARY"))
        self.stdout.write(self.style.ERROR("=" * 70))
        self.stdout.write("")
        self.stdout.write(f"  Total records to DELETE: {total_to_delete}")
        self.stdout.write(f"  Total records to KEEP: {total_protected}")
        self.stdout.write("")

        if dry_run:
            self.stdout.write(self.style.WARNING("=" * 70))
            self.stdout.write(self.style.WARNING("  >>> DRY RUN: No data has been deleted <<<"))
            self.stdout.write(self.style.WARNING("=" * 70))
            self.stdout.write("")
            return

        # =====================================================================
        # CONFIRMATION
        # =====================================================================
        if not force:
            self.stdout.write("")
            self.stdout.write(self.style.ERROR("  WARNING: All operational data will be permanently deleted!"))
            self.stdout.write("")
            confirm = input("  Type 'yes' to continue: ")
            if confirm.lower() != "yes":
                self.stdout.write("")
                self.stdout.write(self.style.WARNING("  Cancelled. No changes made."))
                return

        # =====================================================================
        # EXECUTE DELETE
        # =====================================================================
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("  Starting cleanup..."))
        self.stdout.write("")

        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    deleted_counts = {}

                    for item in deletion_plan:
                        table = item["table"]
                        model_name = f"{item['app']}.{item['model']}"
                        count = item["count"]

                        if count > 0 and "error" not in item:
                            try:
                                cursor.execute(f"DELETE FROM {table}")
                                deleted_counts[model_name] = count
                                self.stdout.write(
                                    self.style.SUCCESS(f"  [DELETED] {model_name}: {count} record(s)")
                                )
                            except Exception as e:
                                self.stdout.write(
                                    self.style.ERROR(f"  [ERROR] {model_name}: {e}")
                                )
                        else:
                            if "error" not in item:
                                self.stdout.write(f"  [SKIP] {model_name}: 0 records")

        except Exception as e:
            self.stdout.write("")
            self.stdout.write(self.style.ERROR(f"  ERROR: {e}"))
            return

        # =====================================================================
        # SUCCESS
        # =====================================================================
        total_deleted = sum(deleted_counts.values())
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 70))
        self.stdout.write(self.style.SUCCESS("  RESET COMPLETED SUCCESSFULLY!"))
        self.stdout.write(self.style.SUCCESS("=" * 70))
        self.stdout.write("")
        self.stdout.write(f"  Total deleted: {total_deleted} record(s)")
        self.stdout.write(f"  Total protected: {total_protected} record(s)")
        self.stdout.write("")
