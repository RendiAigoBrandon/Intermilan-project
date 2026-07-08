from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import BaseCommand, call_command
from django.core.management.color import no_style
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import Profile
from apps.core.models import MonitoringSummary
from apps.dk.models import MasterAkun, TransactionDetail
from apps.documents.models import ChecklistStatus, ChecklistTemplate, DocumentDriveLink, DocumentUpload
from apps.drpp.models import DRPPItem, DRPPMatch, DRPPUpload
from apps.paket_spm.models import PaketSPMPreviewItem, PaketSPMUpload
from apps.sp2d.models import SP2DImportBatch, SP2DRaw


DELETE_MODELS = [
    ChecklistStatus,
    DRPPMatch,
    DRPPItem,
    DRPPUpload,
    DocumentUpload,
    DocumentDriveLink,
    PaketSPMPreviewItem,
    PaketSPMUpload,
    MonitoringSummary,
    TransactionDetail,
    SP2DRaw,
    SP2DImportBatch,
]

RETAINED_MODELS = [
    ("User", get_user_model()),
    ("Profile", Profile),
    ("MasterAkun", MasterAkun),
    ("ChecklistTemplate", ChecklistTemplate),
]


class Command(BaseCommand):
    help = "Reset data transaksi/import testing tanpa menghapus user, profile, MasterAkun, dan ChecklistTemplate."

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Benar-benar menghapus data testing. Tanpa opsi ini command hanya dry-run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Tampilkan rencana reset tanpa menghapus data. Ini adalah perilaku default.",
        )
        parser.add_argument(
            "--skip-backup",
            action="store_true",
            help="Lewati dumpdata backup sebelum commit. Tidak disarankan.",
        )

    def handle(self, *args, **options):
        commit = options["commit"]
        before_counts = self._counts(DELETE_MODELS)
        retained_before = self._named_counts(RETAINED_MODELS)

        self.stdout.write(self.style.WARNING("Mode: COMMIT") if commit else self.style.WARNING("Mode: DRY-RUN"))
        self.stdout.write("Data yang akan dikosongkan:")
        self._print_counts(before_counts)
        self.stdout.write("")
        self.stdout.write("Data referensi yang dipertahankan:")
        self._print_counts(retained_before)

        if not commit:
            self.stdout.write(self.style.SUCCESS("Dry-run selesai. Tidak ada data yang dihapus."))
            self.stdout.write("Jalankan dengan --commit untuk reset testing data.")
            return

        backup_path = None
        if not options["skip_backup"]:
            backup_path = self._backup_database()
            self.stdout.write(self.style.SUCCESS(f"Backup dibuat: {backup_path}"))
        else:
            self.stdout.write(self.style.WARNING("Backup dilewati karena --skip-backup dipakai."))

        with transaction.atomic():
            for model in DELETE_MODELS:
                model.objects.all().delete()
            self._reset_sequences(DELETE_MODELS)

        after_counts = self._counts(DELETE_MODELS)
        retained_after = self._named_counts(RETAINED_MODELS)

        self.stdout.write("")
        self.stdout.write("Data setelah reset:")
        self._print_counts(after_counts)
        self.stdout.write("")
        self.stdout.write("Data referensi tetap ada:")
        self._print_counts(retained_after)

        if backup_path:
            self.stdout.write(f"Backup: {backup_path}")
        self.stdout.write(self.style.SUCCESS("Reset testing data selesai."))

    def _backup_database(self):
        timestamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
        backup_dir = Path(settings.BASE_DIR) / "backups" / "postgres"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"postgres_before_clean_reset_{timestamp}.json"
        with backup_path.open("w", encoding="utf-8") as backup_file:
            call_command(
                "dumpdata",
                exclude=["contenttypes", "auth.permission", "sessions", "admin.logentry"],
                indent=2,
                stdout=backup_file,
                verbosity=0,
            )
        return backup_path

    def _counts(self, models):
        return {model.__name__: model.objects.count() for model in models}

    def _named_counts(self, named_models):
        return {name: model.objects.count() for name, model in named_models}

    def _print_counts(self, counts):
        for name, total in counts.items():
            self.stdout.write(f"- {name}: {total}")

    def _reset_sequences(self, models):
        statements = connection.ops.sequence_reset_sql(no_style(), models)
        if not statements:
            return
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
