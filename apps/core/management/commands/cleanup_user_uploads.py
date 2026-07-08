from datetime import datetime
from pathlib import Path
import re

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import MonitoringSummary
from apps.dk.models import TransactionDetail
from apps.documents.models import DocumentDriveLink, DocumentUpload
from apps.drpp.models import DRPPItem, DRPPMatch, DRPPUpload
from apps.paket_spm.models import PaketSPMPreviewItem, PaketSPMUpload
from apps.sp2d.models import SP2DImportBatch, SP2DRaw


FEATURES = {"all", "sp2d", "paket_spm", "drpp"}


class Command(BaseCommand):
    help = "Cleanup semua hasil upload testing user secara aman. Default dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Tampilkan rencana cleanup tanpa menghapus data.")
        parser.add_argument("--commit", action="store_true", help="Benar-benar hapus data upload testing.")
        parser.add_argument("--filename", default="", help="Filter filename/metadata upload.")
        parser.add_argument("--feature", default="all", choices=sorted(FEATURES), help="Batasi cleanup ke fitur tertentu.")
        parser.add_argument("--include-doclinks", action="store_true", help="Ikut hapus DocumentDriveLink yang jelas berasal dari upload test.")
        parser.add_argument("--include-archive-files", action="store_true", help="Ikut hapus file upload/archive lokal yang terkait data upload test.")
        parser.add_argument("--include-temp-files", action="store_true", help="Ikut hapus file sementara di media/tmp.")
        parser.add_argument("--uploaded-after", default="", help="Filter data upload setelah tanggal YYYY-MM-DD.")

    def handle(self, *args, **options):
        commit = options["commit"]
        filename = options["filename"].strip()
        feature = options["feature"]
        include_doclinks = options["include_doclinks"]
        include_archive_files = options["include_archive_files"]
        include_temp_files = options["include_temp_files"]
        uploaded_after = self.parse_uploaded_after(options["uploaded_after"])

        before = self.baseline_counts()
        self.stdout.write(self.style.WARNING("Mode: COMMIT") if commit else self.style.WARNING("Mode: DRY-RUN"))
        self.stdout.write(f"Filter feature       : {feature}")
        self.stdout.write(f"Filter filename      : {filename or '-'}")
        self.stdout.write(f"Filter uploaded-after: {uploaded_after.date().isoformat() if uploaded_after else '-'}")
        self.stdout.write(f"Include doclinks     : {include_doclinks}")
        self.stdout.write(f"Include archive files: {include_archive_files}")
        self.stdout.write(f"Include temp files   : {include_temp_files}")
        self.print_baseline("Sebelum cleanup", before)
        self.stdout.write("Tidak akan menghapus baseline D_K 5684")
        self.stdout.write("Tidak akan menghapus MonitoringSummary 480")
        self.stdout.write("Tidak akan menghapus DocumentDriveLink baseline lama")

        targets = self.build_targets(feature, filename, uploaded_after, include_doclinks)
        temp_paths = self.collect_temp_paths(filename) if include_temp_files else []
        managed_file_paths = self.collect_managed_file_paths(targets, include_doclinks) if include_archive_files else []

        self.stdout.write("")
        self.stdout.write("=" * 64)
        self.stdout.write(f"SP2DImportBatch yang akan dihapus      : {targets['sp2d_batches'].count()}")
        self.stdout.write(f"SP2DRaw yang akan dihapus              : {targets['sp2d_raw'].count()}")
        self.stdout.write(f"PaketSPMUpload yang akan dihapus       : {targets['paket_uploads'].count()}")
        self.stdout.write(f"PaketSPMPreviewItem yang akan dihapus  : {targets['paket_items'].count()}")
        self.stdout.write(f"DRPPUpload yang akan dihapus           : {targets['drpp_uploads'].count()}")
        self.stdout.write(f"DRPPItem yang akan dihapus             : {targets['drpp_items'].count()}")
        self.stdout.write(f"DRPPMatch yang akan dihapus            : {targets['drpp_matches'].count()}")
        self.stdout.write(f"DocumentUpload test yang dihapus       : {targets['document_uploads'].count()}")
        self.stdout.write(f"DocumentDriveLink test yang dihapus    : {targets['doclinks'].count() if include_doclinks else 0}")
        self.stdout.write(f"TransactionDetail Paket SPM test       : {targets['package_transactions'].count()}")
        self.stdout.write(f"File upload/archive terkait test       : {len(managed_file_paths)}")
        self.stdout.write(f"File temporary yang akan dihapus       : {len(temp_paths)}")
        self.stdout.write("=" * 64)

        self.print_samples("SP2D batch", targets["sp2d_batches"], "original_filename")
        self.print_samples("Paket SPM", targets["paket_uploads"], "original_filename")
        self.print_samples("DRPP", targets["drpp_uploads"], "nomor_drpp")
        self.print_samples("DocumentDriveLink", targets["doclinks"], "nama_file")
        self.print_temp_samples(managed_file_paths, title="Contoh upload/archive files")
        self.print_temp_samples(temp_paths)

        if not commit:
            self.stdout.write(self.style.WARNING("\nDry-run selesai. Tidak ada data atau file yang dihapus."))
            return

        deleted = {}
        with transaction.atomic():
            deleted["DRPPMatch"], _ = targets["drpp_matches"].delete()
            deleted["DRPPItem"], _ = targets["drpp_items"].delete()
            deleted["DRPPUpload"], _ = targets["drpp_uploads"].delete()
            deleted["PaketSPMPreviewItem"], _ = targets["paket_items"].delete()
            deleted["PaketSPMUpload"], _ = targets["paket_uploads"].delete()
            deleted["TransactionDetail"], _ = targets["package_transactions"].delete()
            deleted["DocumentUpload"], _ = targets["document_uploads"].delete()
            if include_doclinks:
                deleted["DocumentDriveLink"], _ = targets["doclinks"].delete()
            else:
                deleted["DocumentDriveLink"] = 0
            deleted["SP2DRaw"], _ = targets["sp2d_raw"].delete()
            deleted["SP2DImportBatch"], _ = targets["sp2d_batches"].delete()

        deleted_files = 0
        deleted_files += self.delete_temp_paths(managed_file_paths)
        if include_temp_files:
            deleted_files += self.delete_temp_paths(temp_paths)

        after = self.baseline_counts()
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Cleanup commit selesai."))
        for name, total in deleted.items():
            self.stdout.write(f"- {name}: {total}")
        self.stdout.write(f"- Files/dirs lokal: {deleted_files}")
        self.print_baseline("Sesudah cleanup", after)
        self.verify_baseline(before, after, deleted.get("TransactionDetail", 0), deleted.get("DocumentDriveLink", 0))

    def build_targets(self, feature, filename, uploaded_after, include_doclinks):
        sp2d_batches = SP2DImportBatch.objects.all() if feature in {"all", "sp2d"} else SP2DImportBatch.objects.none()
        paket_uploads = PaketSPMUpload.objects.all() if feature in {"all", "paket_spm"} else PaketSPMUpload.objects.none()
        drpp_uploads = DRPPUpload.objects.all() if feature in {"all", "drpp"} else DRPPUpload.objects.none()

        if filename:
            sp2d_batches = sp2d_batches.filter(Q(original_filename__icontains=filename) | Q(filename__icontains=filename) | Q(notes__icontains=filename))
            paket_uploads = paket_uploads.filter(Q(original_filename__icontains=filename) | Q(nomor_spm__icontains=filename))
            drpp_uploads = drpp_uploads.filter(Q(nomor_drpp__icontains=filename) | Q(nomor_spm__icontains=filename) | Q(raw_text__icontains=filename))
        if uploaded_after:
            sp2d_batches = sp2d_batches.filter(uploaded_at__gte=uploaded_after)
            paket_uploads = paket_uploads.filter(uploaded_at__gte=uploaded_after)
            drpp_uploads = drpp_uploads.filter(uploaded_at__gte=uploaded_after)

        sp2d_raw = SP2DRaw.objects.filter(import_batch__in=sp2d_batches)
        paket_items = PaketSPMPreviewItem.objects.filter(paket__in=paket_uploads)
        package_transaction_ids = list(
            PaketSPMPreviewItem.objects.filter(
                paket__in=paket_uploads,
                matched_transaction__isnull=False,
                matched_transaction__sp2d_raw__isnull=True,
                matched_transaction__pembebanan="Paket SPM OCR",
            ).values_list("matched_transaction_id", flat=True).distinct()
        )
        package_transactions = TransactionDetail.objects.filter(id__in=package_transaction_ids)
        drpp_items = DRPPItem.objects.filter(drpp_upload__in=drpp_uploads)
        drpp_matches = DRPPMatch.objects.filter(Q(drpp_upload__in=drpp_uploads) | Q(drpp_item__in=drpp_items))
        document_uploads = self.build_document_upload_queryset(filename, feature, uploaded_after, drpp_uploads)

        doclinks = DocumentDriveLink.objects.none()
        if include_doclinks:
            doclinks = self.build_doclink_queryset(filename, feature, uploaded_after, sp2d_batches, paket_uploads, drpp_uploads)

        return {
            "sp2d_batches": sp2d_batches,
            "sp2d_raw": sp2d_raw,
            "paket_uploads": paket_uploads,
            "paket_items": paket_items,
            "package_transactions": package_transactions,
            "drpp_uploads": drpp_uploads,
            "drpp_items": drpp_items,
            "drpp_matches": drpp_matches,
            "document_uploads": document_uploads,
            "doclinks": doclinks,
        }

    def build_document_upload_queryset(self, filename, feature, uploaded_after, drpp_uploads):
        qs = DocumentUpload.objects.none()
        if feature in {"all", "drpp"}:
            qs = qs | DocumentUpload.objects.filter(drpp_uploads__in=drpp_uploads)
        if feature in {"all", "paket_spm", "drpp"}:
            upload_filter = (
                Q(notes__icontains="source=Paket SPM")
                | Q(notes__icontains="source=DRPP")
                | Q(notes__icontains="source=checklist_dk")
                | Q(original_filename__icontains="SPM NOMOR")
                | Q(original_filename__icontains="DRPP NOMOR")
                | Q(original_filename__icontains="KW")
            )
            if filename:
                upload_filter |= Q(original_filename__icontains=filename) | Q(stored_filename__icontains=filename) | Q(notes__icontains=filename)
            qs = qs | DocumentUpload.objects.filter(upload_filter)
        if uploaded_after:
            qs = qs.filter(uploaded_at__gte=uploaded_after)
        return qs.distinct()

    def build_doclink_queryset(self, filename, feature, uploaded_after, sp2d_batches, paket_uploads, drpp_uploads):
        safe_filter = (
            Q(catatan__icontains="source=Paket SPM")
            | Q(catatan__icontains="source=SP2D")
            | Q(catatan__icontains="source=DRPP")
            | Q(catatan__icontains="source=checklist_dk")
            | Q(catatan__icontains="source=checklist_dk_extracted")
            | Q(catatan__icontains="upload_test=true")
            | Q(catatan__icontains="parser_status=")
        )
        if filename:
            filename_filter = (
                Q(nama_file__icontains=filename)
                | Q(catatan__icontains=filename)
                | Q(nomor_spm__icontains=filename)
                | Q(no_drpp__icontains=filename)
                | Q(no_kuitansi__icontains=filename)
            )
            safe_filter = safe_filter & filename_filter
        for name in sp2d_batches.values_list("original_filename", flat=True):
            safe_filter |= Q(nama_file=name) | Q(catatan__icontains=name)
        for upload in paket_uploads:
            safe_filter |= Q(nama_file=upload.original_filename) | Q(catatan__icontains=upload.original_filename) | Q(nomor_spm__iexact=upload.nomor_spm)
        for upload in drpp_uploads:
            safe_filter |= Q(no_drpp__iexact=upload.nomor_drpp) | Q(nomor_spm__iexact=upload.nomor_spm)
        qs = DocumentDriveLink.objects.filter(safe_filter).distinct()
        if uploaded_after:
            qs = qs.filter(created_at__gte=uploaded_after)
        if feature == "sp2d":
            qs = qs.filter(Q(jenis_dokumen="SP2D_EXCEL") | Q(catatan__icontains="source=SP2D"))
        elif feature == "paket_spm":
            qs = qs.filter(Q(catatan__icontains="source=Paket SPM") | Q(catatan__icontains="parser_status=") | Q(catatan__icontains="upload_test=true"))
        elif feature == "drpp":
            qs = qs.filter(Q(catatan__icontains="source=DRPP") | Q(catatan__icontains="source=checklist_dk") | Q(catatan__icontains="source=checklist_dk_extracted") | Q(catatan__icontains="parser_status=") | Q(catatan__icontains="upload_test=true"))
        return qs

    def collect_temp_paths(self, filename=""):
        tmp_dir = Path(settings.MEDIA_ROOT) / "tmp"
        if not tmp_dir.exists():
            return []
        paths = [path for path in tmp_dir.iterdir()]
        if filename:
            lowered = filename.lower()
            paths = [path for path in paths if lowered in path.name.lower()]
        return self.unique_safe_media_paths(paths)

    def collect_managed_file_paths(self, targets, include_doclinks):
        paths = []
        for upload in targets["paket_uploads"]:
            try:
                if upload.zip_file and upload.zip_file.path:
                    paths.append(Path(upload.zip_file.path))
            except Exception:
                pass
        for upload in targets["document_uploads"]:
            try:
                if upload.file and upload.file.path:
                    paths.append(Path(upload.file.path))
            except Exception:
                pass
        if include_doclinks:
            for link in targets["doclinks"]:
                match = re.search(r"local_path=([^;]+)", link.catatan or "")
                if match:
                    paths.append(Path(match.group(1).strip()))
        return self.unique_safe_media_paths(paths)

    def unique_safe_media_paths(self, paths):
        media_root = Path(settings.MEDIA_ROOT).resolve()
        safe_paths = []
        seen = set()
        for path in paths:
            try:
                resolved = Path(path).resolve()
            except Exception:
                continue
            if not str(resolved).startswith(str(media_root)):
                continue
            if str(resolved) in seen:
                continue
            seen.add(str(resolved))
            if resolved.exists():
                safe_paths.append(resolved)
        return safe_paths

    def delete_temp_paths(self, paths):
        deleted = 0
        for path in paths:
            try:
                if path.is_dir():
                    import shutil
                    shutil.rmtree(path, ignore_errors=True)
                elif path.exists():
                    path.unlink()
                deleted += 1
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"Gagal hapus temp {path}: {exc}"))
        return deleted

    def baseline_counts(self):
        return {
            "TransactionDetail": TransactionDetail.objects.count(),
            "MonitoringSummary": MonitoringSummary.objects.count(),
            "DocumentDriveLink": DocumentDriveLink.objects.count(),
            "DocumentUpload": DocumentUpload.objects.count(),
            "SP2DImportBatch": SP2DImportBatch.objects.count(),
            "SP2DRaw": SP2DRaw.objects.count(),
            "PaketSPMUpload": PaketSPMUpload.objects.count(),
            "PaketSPMPreviewItem": PaketSPMPreviewItem.objects.count(),
            "DRPPUpload": DRPPUpload.objects.count(),
            "DRPPItem": DRPPItem.objects.count(),
            "DRPPMatch": DRPPMatch.objects.count(),
        }

    def print_baseline(self, title, counts):
        self.stdout.write("")
        self.stdout.write(title)
        for key, value in counts.items():
            self.stdout.write(f"- {key}: {value}")

    def verify_baseline(self, before, after, deleted_transactions, deleted_doclinks):
        expected_dk = before["TransactionDetail"] - deleted_transactions
        expected_doclinks = before["DocumentDriveLink"] - deleted_doclinks
        if after["TransactionDetail"] != expected_dk:
            self.stdout.write(self.style.ERROR(f"D_K berubah di luar target: expected {expected_dk}, actual {after['TransactionDetail']}"))
        if after["MonitoringSummary"] != before["MonitoringSummary"]:
            self.stdout.write(self.style.ERROR("MonitoringSummary berubah. Ini tidak aman."))
        if after["DocumentDriveLink"] != expected_doclinks:
            self.stdout.write(self.style.ERROR(f"DocumentDriveLink berubah di luar target: expected {expected_doclinks}, actual {after['DocumentDriveLink']}"))
        if after["TransactionDetail"] == 5684 and after["MonitoringSummary"] == 480 and after["DocumentDriveLink"] == 3081 and after["SP2DImportBatch"] == 0 and after["SP2DRaw"] == 0:
            self.stdout.write(self.style.SUCCESS("Baseline target aman: 5684 480 3081 0 0"))

    def print_samples(self, label, queryset, field):
        rows = list(queryset[:10])
        if not rows:
            return
        self.stdout.write(f"\nContoh {label}:")
        for row in rows:
            self.stdout.write(f"- #{row.pk}: {getattr(row, field, '')}")

    def print_temp_samples(self, paths, title="Contoh temp files"):
        if not paths:
            return
        self.stdout.write(f"\n{title}:")
        for path in paths[:10]:
            self.stdout.write(f"- {path}")

    def parse_uploaded_after(self, value):
        if not value:
            return None
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise SystemExit("--uploaded-after harus format YYYY-MM-DD")
        return timezone.make_aware(parsed)
