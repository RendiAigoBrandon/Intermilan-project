import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import BaseCommand, CommandError, call_command
from django.core.management.color import no_style
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import Profile
from apps.core.models import MonitoringSummary
from apps.dk.models import MasterAkun, TransactionChangeLog, TransactionDetail
from apps.documents.models import ChecklistStatus, ChecklistTemplate, DocumentDriveLink, DocumentUpload
from apps.drpp.models import DRPPImportBatch, DRPPItem, DRPPMatch, DRPPUpload
from apps.paket_spm.models import PaketSPMPreviewItem, PaketSPMUpload
from apps.sp2d.models import SP2DImportBatch, SP2DRaw


CONFIRM_TOKEN = "RESET_INTERMILAN"

OPERATIONAL_MODELS = [
    ChecklistStatus,
    DRPPMatch,
    DRPPItem,
    PaketSPMPreviewItem,
    DocumentUpload,
    DocumentDriveLink,
    DRPPUpload,
    DRPPImportBatch,
    PaketSPMUpload,
    TransactionChangeLog,
    TransactionDetail,
    SP2DRaw,
    SP2DImportBatch,
    MonitoringSummary,
]

RETAINED_MODELS = [
    ("User", get_user_model()),
    ("Profile", Profile),
    ("MasterAkun", MasterAkun),
    ("ChecklistTemplate", ChecklistTemplate),
]


class Command(BaseCommand):
    help = "Dry-run/reset data operasional INTERMILAN tanpa menghapus auth, migration, dan master/reference."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Tampilkan rencana tanpa menghapus. Ini default.")
        parser.add_argument("--execute", action="store_true", help="Minta penghapusan nyata; wajib pakai --confirm.")
        parser.add_argument("--confirm", default="", help=f"Token penghapusan nyata: {CONFIRM_TOKEN}")
        parser.add_argument("--include-files", action="store_true", help="Saat execute, hapus file media/cache terkait.")

    def handle(self, *args, **options):
        execute = bool(options["execute"] or options["confirm"])
        if execute and options["confirm"] != CONFIRM_TOKEN:
            raise CommandError(f"Penghapusan nyata ditolak. Token harus persis {CONFIRM_TOKEN}.")

        counts_before = self._model_counts(OPERATIONAL_MODELS)
        retained_counts = self._named_counts(RETAINED_MODELS)
        file_paths = self._collect_file_paths()
        file_total = self._file_size(file_paths)

        self.stdout.write(self.style.WARNING("Mode: EXECUTE") if execute else self.style.WARNING("Mode: DRY-RUN"))
        self.stdout.write("Data operasional yang akan dihapus:")
        self._print_counts(counts_before)
        self.stdout.write("")
        self.stdout.write("Urutan penghapusan:")
        for index, model in enumerate(OPERATIONAL_MODELS, start=1):
            self.stdout.write(f"{index}. {model._meta.label} ({model._meta.db_table})")
        self.stdout.write("")
        self.stdout.write("Master/system yang dipertahankan:")
        self._print_counts(retained_counts)
        self.stdout.write(f"File/cache/temp kandidat: {len(file_paths)} file, {file_total} byte")

        manifest = {
            "timestamp": timezone.localtime().isoformat(),
            "mode": "execute" if execute else "dry-run",
            "models": counts_before,
            "retained": retained_counts,
            "file_count": len(file_paths),
            "file_total_bytes": file_total,
            "files": [str(path) for path in file_paths],
        }

        if not execute:
            self.stdout.write(self.style.SUCCESS("Dry-run selesai. Tidak ada database/file yang dihapus."))
            return

        backup_file, manifest_file = self._backup_operational_data(manifest)
        deleted_counts = {}
        with transaction.atomic():
            for model in OPERATIONAL_MODELS:
                deleted_counts[model.__name__] = model.objects.count()
                model.objects.all().delete()
            self._reset_sequences(OPERATIONAL_MODELS)

        deleted_files, failed_files = [], []
        if options["include_files"]:
            for path in file_paths:
                try:
                    if path.exists() and self._is_under_media(path):
                        path.unlink()
                        deleted_files.append(str(path))
                except OSError as exc:
                    failed_files.append({"path": str(path), "error": str(exc)})

        counts_after = self._model_counts(OPERATIONAL_MODELS)
        self.stdout.write("")
        self.stdout.write("Data setelah reset:")
        self._print_counts(counts_after)
        self.stdout.write(f"Record terhapus: {sum(deleted_counts.values())}")
        self.stdout.write(f"File berhasil dihapus: {len(deleted_files)}")
        self.stdout.write(f"File gagal dihapus: {len(failed_files)}")
        self.stdout.write(f"Backup: {backup_file}")
        self.stdout.write(f"Manifest: {manifest_file}")
        self.stdout.write(self.style.SUCCESS("Reset operasional selesai."))

    def _model_counts(self, models):
        return {model.__name__: model.objects.count() for model in models}

    def _named_counts(self, named_models):
        return {name: model.objects.count() for name, model in named_models}

    def _print_counts(self, counts):
        for name, count in counts.items():
            self.stdout.write(f"- {name}: {count}")

    def _media_root(self):
        return Path(settings.MEDIA_ROOT).resolve()

    def _is_under_media(self, path):
        try:
            path.resolve().relative_to(self._media_root())
            return True
        except ValueError:
            return False

    def _collect_file_paths(self):
        paths = []
        for obj in list(PaketSPMUpload.objects.all()) + list(DocumentUpload.objects.all()):
            for field_name in ("zip_file", "file"):
                field = getattr(obj, field_name, None)
                if not field:
                    continue
                try:
                    path = Path(field.path)
                except (NotImplementedError, ValueError):
                    continue
                if path.exists() and self._is_under_media(path):
                    paths.append(path.resolve())

        for rel in ("tmp", "ocr_cache", "archive/documents"):
            root = self._media_root() / rel
            if root.exists():
                paths.extend(path.resolve() for path in root.rglob("*") if path.is_file())
        for cache_dir in self._media_root().rglob(".ocr_cache") if self._media_root().exists() else []:
            paths.extend(path.resolve() for path in cache_dir.rglob("*") if path.is_file())

        seen = set()
        safe_paths = []
        for path in paths:
            if path in seen or not self._is_under_media(path):
                continue
            seen.add(path)
            safe_paths.append(path)
        return safe_paths

    def _file_size(self, paths):
        total = 0
        for path in paths:
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total

    def _backup_operational_data(self, manifest):
        timestamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
        backup_dir = Path(settings.BASE_DIR) / "backups" / "operational_reset"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = backup_dir / f"operational_before_reset_{timestamp}.json"
        manifest_file = backup_dir / f"operational_before_reset_{timestamp}.manifest.json"
        labels = [model._meta.label for model in OPERATIONAL_MODELS]
        with backup_file.open("w", encoding="utf-8") as handle:
            call_command("dumpdata", *labels, indent=2, stdout=handle, verbosity=0)
        manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return backup_file, manifest_file

    def _reset_sequences(self, models):
        statements = connection.ops.sequence_reset_sql(no_style(), models)
        if not statements:
            return
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
