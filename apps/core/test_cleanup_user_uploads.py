import datetime
import tempfile
from decimal import Decimal
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.dk.models import TransactionDetail
from apps.documents.models import DocumentDriveLink
from apps.paket_spm.models import PaketSPMUpload


class CleanupUserUploadsTests(TestCase):
    def make_transaction(self, number):
        return TransactionDetail.objects.create(
            satker_code="1300",
            nomor_spm=number,
            tanggal_spm=datetime.date(2026, 6, 30),
            akun="521111",
            nilai_bruto=Decimal("100"),
            nilai_netto=Decimal("100"),
        )

    def test_cleanup_removes_created_row_but_preserves_existing_dk(self):
        existing = self.make_transaction("00777T")
        TransactionDetail.objects.filter(pk=existing.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=10)
        )
        paket = PaketSPMUpload.objects.create(
            zip_file="uploads/paket_spm/test.pdf",
            original_filename="SPM NOMOR 00777T.pdf",
            nomor_spm="00777T",
            satker_code="1300",
            tahun=2026,
        )
        created = self.make_transaction("00777T")
        for transaction_row in (existing, created):
            DocumentDriveLink.objects.create(
                transaction_detail=transaction_row,
                satker_code="1300",
                nomor_spm="00777T",
                jenis_dokumen="SPM",
                nama_file=paket.original_filename,
                google_drive_url="http://localhost/archive.pdf",
                catatan=f"source=Paket SPM; paket_spm_id={paket.id}; document_status=Lengkap",
            )

        call_command(
            "cleanup_user_uploads",
            feature="paket_spm",
            include_doclinks=True,
            commit=True,
        )

        self.assertTrue(TransactionDetail.objects.filter(pk=existing.pk).exists())
        self.assertFalse(TransactionDetail.objects.filter(pk=created.pk).exists())
        self.assertFalse(PaketSPMUpload.objects.exists())

    def test_cleanup_can_remove_regenerable_ocr_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir, override_settings(MEDIA_ROOT=temp_dir):
            cache_dir = Path(temp_dir) / "tmp" / ".ocr_cache"
            cache_dir.mkdir(parents=True)
            (cache_dir / "cached.json").write_text("{}", encoding="utf-8")

            call_command(
                "cleanup_user_uploads",
                feature="paket_spm",
                include_ocr_cache=True,
                commit=True,
            )

            self.assertFalse(cache_dir.exists())
