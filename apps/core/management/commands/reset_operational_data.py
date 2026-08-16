import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import BaseCommand, CommandError, call_command
from django.core.management.color import no_style
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import Profile
from apps.auditlog.models import AuditLog
from apps.core.models import (
    ActiveParentSession,
    DRPPPreviewState,
    MonitoringSummary,
    SatkerMaster,
    TransactionPackage,
    TransactionProvenance,
)
from apps.dk.models import MasterAkun, TransactionChangeLog, TransactionDetail
from apps.documents.models import (
    ChecklistStatus,
    ChecklistTemplate,
    DocumentDriveLink,
    DocumentUpload,
)
from apps.drpp.models import (
    DRPPImportBatch,
    DRPPItem,
    DRPPMatch,
    DRPPUpload,
    DRPPSupportingAttachment,
)
from apps.paket_spm.models import PaketSPMPreviewItem, PaketSPMUpload
from apps.sp2d.models import SP2DImportBatch, SP2DRaw


CONFIRM_TOKEN = "RESET_INTERMILAN"

# All operational models - these will be deleted
# Format: (app_label.ModelName, ModelClass)
OPERATIONAL_MODELS = [
    # DK
    ("dk.TransactionDetail", TransactionDetail),
    ("dk.TransactionChangeLog", TransactionChangeLog),
    # SP2D
    ("sp2d.SP2DRaw", SP2DRaw),
    ("sp2d.SP2DImportBatch", SP2DImportBatch),
    # DRPP
    ("drpp.DRPPUpload", DRPPUpload),
    ("drpp.DRPPItem", DRPPItem),
    ("drpp.DRPPMatch", DRPPMatch),
    ("drpp.DRPPImportBatch", DRPPImportBatch),
    ("drpp.DRPPSupportingAttachment", DRPPSupportingAttachment),
    # Documents
    ("documents.DocumentUpload", DocumentUpload),
    ("documents.DocumentDriveLink", DocumentDriveLink),
    ("documents.ChecklistStatus", ChecklistStatus),
    ("documents.ChecklistTemplate", ChecklistTemplate),
    # Paket SPM
    ("paket_spm.PaketSPMUpload", PaketSPMUpload),
    ("paket_spm.PaketSPMPreviewItem", PaketSPMPreviewItem),
    # Core
    ("core.TransactionPackage", TransactionPackage),
    ("core.ActiveParentSession", ActiveParentSession),
    ("core.DRPPPreviewState", DRPPPreviewState),
    ("core.MonitoringSummary", MonitoringSummary),
    ("core.TransactionProvenance", TransactionProvenance),
    # Audit
    ("audit.AuditLog", AuditLog),
]

# Protected models - these will NOT be deleted
PROTECTED_MODELS = [
    ("auth.User", get_user_model()),
    ("accounts.Profile", Profile),
    ("core.SatkerMaster", SatkerMaster),
    ("dk.MasterAkun", MasterAkun),
]


class Command(BaseCommand):
    help = "Reset ALL operational data to simulate fresh deployment. Deletes data from dk, sp2d, drpp, documents, paket_spm, core, and audit apps."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show plan without deleting. This is the default.")
        parser.add_argument("--execute", action="store_true", help="Actually delete data; requires --confirm.")
        parser.add_argument("--confirm", default="", help=f"Confirmation token for actual deletion: {CONFIRM_TOKEN}")
        parser.add_argument("--include-files", action="store_true", help="When executing, also delete related media/cache files.")

    def handle(self, *args, **options):
        execute = bool(options["execute"] or options["confirm"])
        if execute and options["confirm"] != CONFIRM_TOKEN:
            raise CommandError(f"Actual deletion denied. Token must be exactly: {CONFIRM_TOKEN}")

        # Gather counts
        operational_counts = self._model_counts(OPERATIONAL_MODELS)
        protected_counts = self._model_counts(PROTECTED_MODELS)
        file_paths = self._collect_file_paths()
        file_total = self._file_size(file_paths)

        self.stdout.write(self.style.WARNING("\n=== MODE: EXECUTE ===\n") if execute else self.style.WARNING("\n=== MODE: DRY-RUN ===\n"))

        # Split into "to delete" (has records) and "empty" (no records)
        to_delete = {k: v for k, v in operational_counts.items() if v > 0}
        empty = {k: v for k, v in operational_counts.items() if v == 0}

        # Print DATA TO DELETE section
        self.stdout.write(self.style.WARNING("DATA TO DELETE:"))
        if to_delete:
            for label, count in sorted(to_delete.items()):
                self.stdout.write(f"  {label}: {count}")
        else:
            self.stdout.write("  (none - database is already empty)")
        self.stdout.write("")

        # Print EMPTY section
        self.stdout.write(self.style.WARNING("EMPTY:"))
        if empty:
            for label, count in sorted(empty.items()):
                self.stdout.write(f"  {label}: {count}")
        else:
            self.stdout.write("  (none - all tables have data)")
        self.stdout.write("")

        # Print PROTECTED section
        self.stdout.write(self.style.WARNING("PROTECTED:"))
        for label, count in sorted(protected_counts.items()):
            self.stdout.write(f"  {label}: {count}")
        self.stdout.write("")

        # Summary
        total_to_delete = sum(to_delete.values())
        self.stdout.write(f"Total records to delete: {total_to_delete}")
        self.stdout.write(f"File candidates for deletion: {len(file_paths)} ({file_total:,} bytes)")
        self.stdout.write("")

        # Build manifest
        manifest = {
            "timestamp": timezone.localtime().isoformat(),
            "mode": "execute" if execute else "dry-run",
            "to_delete": to_delete,
            "empty": empty,
            "protected": protected_counts,
            "total_to_delete": total_to_delete,
            "file_count": len(file_paths),
            "file_total_bytes": file_total,
            "files": [str(path) for path in file_paths],
        }

        if not execute:
            self.stdout.write(self.style.SUCCESS("Dry-run complete. No data was deleted."))
            self._save_manifest(manifest)
            return

        # Execute deletion
        self.stdout.write(self.style.WARNING("Starting deletion..."))
        backup_file = self._backup_operational_data(manifest)

        deleted_counts = {}
        with transaction.atomic():
            for label, model in reversed(OPERATIONAL_MODELS):
                count = model.objects.count()
                if count > 0:
                    deleted_counts[label] = count
                    model.objects.all().delete()
                    self.stdout.write(f"  Deleted {count} from {label}")
            self._reset_sequences([m for _, m in OPERATIONAL_MODELS])

        # Delete files if requested
        deleted_files, failed_files = [], []
        if options["include_files"]:
            self.stdout.write("")
            self.stdout.write("Deleting files...")
            for path in file_paths:
                try:
                    if path.exists() and self._is_under_media(path):
                        path.unlink()
                        deleted_files.append(str(path))
                        self.stdout.write(f"  Deleted file: {path}")
                except OSError as exc:
                    failed_files.append({"path": str(path), "error": str(exc)})
                    self.stdout.write(self.style.ERROR(f"  Failed to delete: {path} - {exc}"))

        # Final summary
        counts_after = self._model_counts(OPERATIONAL_MODELS)
        remaining = sum(counts_after.values())

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 50))
        self.stdout.write(self.style.SUCCESS("RESET COMPLETE"))
        self.stdout.write(self.style.SUCCESS("=" * 50))
        self.stdout.write(f"Records deleted: {total_to_delete}")
        self.stdout.write(f"Files deleted: {len(deleted_files)}")
        self.stdout.write(f"Files failed: {len(failed_files)}")
        self.stdout.write(f"Remaining operational records: {remaining}")
        self.stdout.write(f"Backup: {backup_file}")
        self.stdout.write(self.style.SUCCESS("All operational data has been reset."))

    def _model_counts(self, models):
        """Get counts for models specified as (label, model) tuples.

        Handles missing tables gracefully by returning 0.
        """
        counts = {}
        for label, model in models:
            try:
                counts[label] = model.objects.count()
            except Exception:
                # Table doesn't exist yet - treat as empty
                counts[label] = 0
        return counts

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

        labels = [model._meta.label for _, model in OPERATIONAL_MODELS]
        with backup_file.open("w", encoding="utf-8") as handle:
            call_command("dumpdata", *labels, indent=2, stdout=handle, verbosity=0)

        return backup_file

    def _save_manifest(self, manifest):
        """Save manifest file for dry-run."""
        timestamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
        manifest_dir = Path(settings.BASE_DIR) / "backups" / "operational_reset"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_file = manifest_dir / f"dry_run_manifest_{timestamp}.json"
        manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def _reset_sequences(self, models):
        statements = connection.ops.sequence_reset_sql(no_style(), models)
        if not statements:
            return
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
