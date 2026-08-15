"""
Management command to cleanup test/dummy data from INTERMILAN production database.

Usage:
    # Preview only (dry-run)
    python manage.py cleanup_production_test_data --dry-run

    # Execute cleanup (with confirmation)
    python manage.py cleanup_production_test_data

    # Execute cleanup (skip confirmation)
    python manage.py cleanup_production_test_data --force

This command removes test/dummy data while protecting:
    - auth_user (production users)
    - accounts_profile
    - core_satkermaster (master data)
    - Google Drive configuration

Data that WILL be deleted:
    - dk_transactiondetail with test SPM numbers
    - sp2d_sp2draw with test SAT codes
    - dk_masterakun with dummy account codes
    - core_activeparentsession orphan records

Data that will ONLY be DISPLAYED (not deleted):
    - core_transactionpackage (info only)
"""

from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Cleanup test/dummy data from INTERMILAN production database"

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
        self.stdout.write(self.style.WARNING("=" * 70))
        self.stdout.write(self.style.WARNING("  INTERMILAN - PRODUCTION TEST DATA CLEANUP"))
        self.stdout.write(self.style.WARNING("=" * 70))
        self.stdout.write("")

        if dry_run:
            self.stdout.write(self.style.WARNING("  MODE: DRY RUN - No changes will be made"))
        else:
            self.stdout.write(self.style.ERROR("  MODE: LIVE - Data will be deleted!"))
        self.stdout.write("")

        # Import models
        from apps.dk.models import TransactionDetail, MasterAkun
        from apps.sp2d.models import SP2DRaw
        from apps.core.models import TransactionPackage, ActiveParentSession

        deletion_plan = {}

        # =====================================================================
        # 1. DK TransactionDetail - Test data
        # =====================================================================
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("-" * 70))
        self.stdout.write(self.style.WARNING("  1. DK TransactionDetail (Test Data)"))
        self.stdout.write(self.style.WARNING("-" * 70))

        test_td = TransactionDetail.objects.filter(
            nomor_spm__icontains="DBG"
        ) | TransactionDetail.objects.filter(
            satker_code="SAT1"
        ) | TransactionDetail.objects.filter(
            id=428
        )
        test_td = test_td.distinct()

        self.stdout.write(f"  Records found: {test_td.count()}")
        for td in test_td:
            self.stdout.write(f"    - ID={td.id}: SPM={td.nomor_spm}, satker={td.satker_code}, kuitansi={td.no_kuitansi}")

        deletion_plan["dk.TransactionDetail"] = {
            "count": test_td.count(),
            "queryset": test_td,
        }

        # =====================================================================
        # 2. SP2D Raw - Test data
        # =====================================================================
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("-" * 70))
        self.stdout.write(self.style.WARNING("  2. SP2D Raw (Test Data)"))
        self.stdout.write(self.style.WARNING("-" * 70))

        test_sp2d = SP2DRaw.objects.filter(
            no_sp2d__icontains="DBG"
        ) | SP2DRaw.objects.filter(
            satker_code="SAT1"
        ) | SP2DRaw.objects.filter(
            satker_code="SAT2"
        )
        test_sp2d = test_sp2d.distinct()

        self.stdout.write(f"  Records found: {test_sp2d.count()}")
        for sp in test_sp2d:
            self.stdout.write(f"    - ID={sp.id}: satker={sp.satker_code}, SPM={sp.nomor_spm_extracted}, SP2D={sp.no_sp2d}")

        deletion_plan["sp2d.SP2DRaw"] = {
            "count": test_sp2d.count(),
            "queryset": test_sp2d,
        }

        # =====================================================================
        # 3. MasterAkun - Dummy accounts
        # =====================================================================
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("-" * 70))
        self.stdout.write(self.style.WARNING("  3. DK MasterAkun (Dummy Accounts)"))
        self.stdout.write(self.style.WARNING("-" * 70))

        dummy_codes = ["12345", "51xxx", "51XXXX", "51xxx", "51XXXX"]
        test_akun = MasterAkun.objects.filter(kode__in=dummy_codes)

        self.stdout.write(f"  Records found: {test_akun.count()}")
        for akun in test_akun:
            self.stdout.write(f"    - {akun.kode}: {akun.nama_akun}")

        deletion_plan["dk.MasterAkun"] = {
            "count": test_akun.count(),
            "queryset": test_akun,
        }

        # =====================================================================
        # 4. ActiveParentSession - Orphan records
        # =====================================================================
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("-" * 70))
        self.stdout.write(self.style.WARNING("  4. Core ActiveParentSession (Orphan)"))
        self.stdout.write(self.style.WARNING("-" * 70))

        from django.contrib.auth import get_user_model
        User = get_user_model()
        valid_user_ids = list(User.objects.values_list("id", flat=True))

        orphan_sessions = ActiveParentSession.objects.exclude(user_id__in=valid_user_ids)

        self.stdout.write(f"  Records found: {orphan_sessions.count()}")
        for session in orphan_sessions:
            self.stdout.write(f"    - ID={session.id}: session_key={session.session_key[:20] if session.session_key else 'None'}..., user_id={session.user_id}")

        deletion_plan["core.ActiveParentSession"] = {
            "count": orphan_sessions.count(),
            "queryset": orphan_sessions,
        }

        # =====================================================================
        # 5. TransactionPackage - INFO ONLY (will NOT be deleted)
        # =====================================================================
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("-" * 70))
        self.stdout.write(self.style.WARNING("  5. Core TransactionPackage (INFO ONLY - Will NOT be deleted)"))
        self.stdout.write(self.style.WARNING("-" * 70))

        all_packages = TransactionPackage.objects.all()
        self.stdout.write(f"  Total records: {all_packages.count()}")

        for pkg in all_packages:
            # Check if it has related transaction details
            related_count = TransactionDetail.objects.filter(transaction_package=pkg).count()
            self.stdout.write(f"    - ID={pkg.id}: satker={pkg.satker_code}, SPM={pkg.nomor_spm}")
            self.stdout.write(f"      tahun={pkg.tahun}, bulan={pkg.tanggal_sp2d}, related_transactions={related_count}")
            if related_count == 0:
                self.stdout.write(self.style.WARNING(f"      WARNING: No related transactions - safe to delete manually"))

        self.stdout.write("")
        self.stdout.write(self.style.WARNING("  NOTE: TransactionPackage records will NOT be deleted automatically"))
        self.stdout.write(self.style.WARNING("  Review above and delete manually if needed"))

        # =====================================================================
        # SUMMARY
        # =====================================================================
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("=" * 70))
        self.stdout.write(self.style.WARNING("  SUMMARY"))
        self.stdout.write(self.style.WARNING("=" * 70))

        total_to_delete = sum(d["count"] for d in deletion_plan.values())
        self.stdout.write(f"  Total records to DELETE: {total_to_delete}")
        for name, info in deletion_plan.items():
            if info["count"] > 0:
                self.stdout.write(f"    - {name}: {info['count']} record(s)")

        self.stdout.write("")
        self.stdout.write(self.style.WARNING("  Records INFO ONLY (will NOT be deleted):"))
        self.stdout.write(f"    - core.TransactionPackage: {all_packages.count()} record(s)")

        self.stdout.write("")

        # =====================================================================
        # PROTECTED DATA (will NOT be touched)
        # =====================================================================
        self.stdout.write(self.style.SUCCESS("-" * 70))
        self.stdout.write(self.style.SUCCESS("  PROTECTED DATA (Will NOT be touched):"))
        self.stdout.write(self.style.SUCCESS("-" * 70))
        self.stdout.write(self.style.SUCCESS("    - auth_user (production users)"))
        self.stdout.write(self.style.SUCCESS("    - accounts_profile"))
        self.stdout.write(self.style.SUCCESS("    - core_satkermaster (master data)"))
        self.stdout.write(self.style.SUCCESS("    - Google Drive configuration"))
        self.stdout.write(self.style.SUCCESS("    - All other production data"))

        self.stdout.write("")

        # =====================================================================
        # EXECUTE OR DRY-RUN
        # =====================================================================
        if dry_run:
            self.stdout.write(self.style.WARNING("  >>> DRY RUN: No data has been deleted <<<"))
            self.stdout.write("")
            self.stdout.write("  Run without --dry-run to execute cleanup:")
            self.stdout.write("  python manage.py cleanup_production_test_data")
            self.stdout.write("")
            return

        if not force:
            self.stdout.write("")
            self.stdout.write(self.style.ERROR("  PERINGATAN: Data will be permanently deleted!"))
            self.stdout.write("")
            confirm = input("  Type 'yes' to continue: ")
            if confirm.lower() != "yes":
                self.stdout.write("")
                self.stdout.write(self.style.WARNING("  Cancelled. No changes made."))
                return

        self.stdout.write("")
        self.stdout.write(self.style.WARNING("  Starting cleanup..."))
        self.stdout.write("")

        try:
            with transaction.atomic():
                deleted_counts = {}

                for model_name, info in deletion_plan.items():
                    if info["count"] > 0:
                        deleted, _ = info["queryset"].delete()
                        deleted_counts[model_name] = deleted
                        self.stdout.write(
                            self.style.SUCCESS(f"  [DELETED] {model_name}: {deleted} record(s)")
                        )

            # =====================================================================
            # SUCCESS
            # =====================================================================
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS("=" * 70))
            self.stdout.write(self.style.SUCCESS("  CLEANUP COMPLETED SUCCESSFULLY!"))
            self.stdout.write(self.style.SUCCESS("=" * 70))
            self.stdout.write("")
            self.stdout.write("  Deleted records:")
            for name, count in deleted_counts.items():
                self.stdout.write(f"    - {name}: {count}")
            self.stdout.write("")

        except Exception as e:
            self.stdout.write("")
            self.stdout.write(self.style.ERROR("=" * 70))
            self.stdout.write(self.style.ERROR("  ERROR: Cleanup failed!"))
            self.stdout.write(self.style.ERROR("=" * 70))
            self.stdout.write("")
            self.stdout.write(f"  {str(e)}")
            self.stdout.write("")
            raise
