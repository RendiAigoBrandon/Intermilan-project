from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from apps.core.models import MonitoringSummary
from apps.dk.models import TransactionDetail
from apps.documents.models import DocumentDriveLink
from apps.paket_spm.models import PaketSPMPreviewItem, PaketSPMUpload


class Command(BaseCommand):
    help = "Cleanup Paket SPM upload testing data. Default dry-run, menjaga baseline D_K/Monitoring/Document."

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true")
        parser.add_argument("--include-doclinks", action="store_true")

    def handle(self, *args, **options):
        commit = options["commit"]
        include_doclinks = options["include_doclinks"]
        paket_qs = PaketSPMUpload.objects.all()
        preview_qs = PaketSPMPreviewItem.objects.filter(paket__in=paket_qs)
        transaction_ids = list(
            preview_qs.filter(
                matched_transaction__isnull=False,
                matched_transaction__sp2d_raw__isnull=True,
                matched_transaction__pembebanan="Paket SPM OCR",
            ).values_list("matched_transaction_id", flat=True).distinct()
        )
        transaction_qs = TransactionDetail.objects.filter(id__in=transaction_ids)
        doclink_qs = DocumentDriveLink.objects.none()
        if include_doclinks:
            doclink_qs = DocumentDriveLink.objects.filter(
                Q(catatan__icontains="source=Paket SPM") | Q(jenis_dokumen__in=["PAKET_SPM_ZIP", "SPM", "DRPP", "KW"])
            )

        self.stdout.write("=== Paket SPM cleanup preview ===")
        self.stdout.write(f"PaketSPMUpload: {paket_qs.count()}")
        self.stdout.write(f"PaketSPMPreviewItem: {preview_qs.count()}")
        self.stdout.write(f"TransactionDetail dari Paket SPM OCR: {transaction_qs.count()}")
        self.stdout.write(f"DocumentDriveLink Paket SPM: {doclink_qs.count() if include_doclinks else 0}")

        dk_before = TransactionDetail.objects.count()
        monitoring_before = MonitoringSummary.objects.count()
        doc_before = DocumentDriveLink.objects.count()
        self.stdout.write(f"Baseline sebelum cleanup: D_K={dk_before}, MonitoringSummary={monitoring_before}, DocumentDriveLink={doc_before}")

        if not commit:
            self.stdout.write(self.style.WARNING("Dry-run. Tambahkan --commit untuk menghapus data Paket SPM testing."))
            return

        with transaction.atomic():
            deleted_preview, _ = preview_qs.delete()
            deleted_paket, _ = paket_qs.delete()
            deleted_transactions, _ = transaction_qs.delete()
            deleted_doclinks = 0
            if include_doclinks:
                deleted_doclinks, _ = doclink_qs.delete()

        dk_after = TransactionDetail.objects.count()
        monitoring_after = MonitoringSummary.objects.count()
        doc_after = DocumentDriveLink.objects.count()
        expected_dk_after = dk_before - deleted_transactions
        expected_doc_after = doc_before - deleted_doclinks

        self.stdout.write(self.style.SUCCESS(
            f"Terhapus: paket={deleted_paket}, preview={deleted_preview}, D_K Paket SPM={deleted_transactions}, doclinks={deleted_doclinks}"
        ))
        self.stdout.write(f"Baseline setelah cleanup: D_K={dk_after}, MonitoringSummary={monitoring_after}, DocumentDriveLink={doc_after}")
        if dk_after != expected_dk_after:
            self.stdout.write(self.style.ERROR(f"D_K berubah di luar target: expected {expected_dk_after}, actual {dk_after}"))
        if monitoring_after != monitoring_before:
            self.stdout.write(self.style.ERROR("MonitoringSummary berubah, ini tidak aman."))
        if doc_after != expected_doc_after:
            self.stdout.write(self.style.ERROR(f"DocumentDriveLink berubah di luar target: expected {expected_doc_after}, actual {doc_after}"))
