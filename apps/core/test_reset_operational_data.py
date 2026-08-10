import io
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from apps.accounts.models import Profile
from apps.core.management.commands.reset_operational_data import Command
from apps.core.models import MonitoringSummary
from apps.dk.models import MasterAkun, TransactionChangeLog, TransactionDetail
from apps.documents.models import ChecklistStatus, ChecklistTemplate, DocumentDriveLink, DocumentUpload
from apps.drpp.models import DRPPImportBatch, DRPPItem, DRPPMatch, DRPPUpload
from apps.paket_spm.models import PaketSPMPreviewItem, PaketSPMUpload
from apps.sp2d.models import SP2DImportBatch, SP2DRaw


User = get_user_model()


class ResetOperationalDataTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.tempdir.name)
        self.media_root = self.base_dir / "media"
        self.override = override_settings(BASE_DIR=self.base_dir, MEDIA_ROOT=self.media_root)
        self.override.enable()
        self.user = User.objects.create_superuser("admin-reset", "admin@example.test", "password")
        Profile.objects.filter(user=self.user).update(satker_code="1300")
        MasterAkun.objects.create(kode="521111", nama_akun="Belanja")
        ChecklistTemplate.objects.create(nama_dokumen="SPM", urutan=1)
        self._make_operational_data()

    def tearDown(self):
        self.override.disable()
        self.tempdir.cleanup()

    def _touch_media(self, relative, content=b"x"):
        path = self.media_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def _make_operational_data(self):
        paket_path = self._touch_media("uploads/paket_spm/2026/07/paket.pdf")
        doc_path = self._touch_media("uploads/documents/2026/07/doc.pdf")
        self._touch_media("tmp/upload.tmp")
        self._touch_media("ocr_cache/drpp_batch/page.json")
        self._touch_media("archive/documents/2026/07/archive.pdf")

        sp2d_batch = SP2DImportBatch.objects.create(filename="sp2d.xlsx", original_filename="sp2d.xlsx")
        sp2d = SP2DRaw.objects.create(import_batch=sp2d_batch, satker_code="1300", no_sp2d="SP2D001")
        tx = TransactionDetail.objects.create(
            sp2d_raw=sp2d,
            satker_code="1300",
            akun="521111",
            nomor_spm="00001A",
            nilai_bruto=Decimal("100"),
            nilai_netto=Decimal("100"),
        )
        TransactionChangeLog.objects.create(transaction=tx, field_name="nilai_netto", old_value="0", new_value="100")
        paket = PaketSPMUpload.objects.create(
            zip_file=str(paket_path.relative_to(self.media_root)).replace("\\", "/"),
            original_filename="paket.pdf",
            satker_code="1300",
            uploaded_by=self.user,
        )
        PaketSPMPreviewItem.objects.create(paket=paket, akun="521111", matched_transaction=tx)
        upload = DocumentUpload.objects.create(
            transaction_detail=tx,
            document_type="SPM",
            original_filename="doc.pdf",
            stored_filename="doc.pdf",
            file=str(doc_path.relative_to(self.media_root)).replace("\\", "/"),
            uploaded_by=self.user,
        )
        DocumentDriveLink.objects.create(
            transaction_detail=tx,
            satker_code="1300",
            nomor_spm="00001A",
            nama_file="drive.pdf",
            google_drive_url="https://drive.google.com/file/d/abc",
        )
        ChecklistStatus.objects.create(transaction_detail=tx, nama_dokumen="SPM", dokumen_upload=upload)
        drpp_batch = DRPPImportBatch.objects.create(filename="drpp.pdf", original_filename="drpp.pdf", uploaded_by=self.user)
        drpp_upload = DRPPUpload.objects.create(import_batch=drpp_batch, nomor_drpp="00001", transaction_detail=tx)
        drpp_item = DRPPItem.objects.create(drpp_upload=drpp_upload, import_batch=drpp_batch, no_bukti="KW001", akun="521111")
        DRPPMatch.objects.create(drpp_upload=drpp_upload, drpp_item=drpp_item, transaction_detail=tx)
        MonitoringSummary.objects.create(
            satker_code="1300",
            satker_label="BPS 1300",
            bulan="Juli",
            bulan_number=7,
            tahun=2026,
        )

    def _operational_total(self):
        return sum(
            model.objects.count()
            for model in (
                ChecklistStatus, DRPPMatch, DRPPItem, PaketSPMPreviewItem, DocumentUpload,
                DocumentDriveLink, DRPPUpload, DRPPImportBatch, PaketSPMUpload,
                TransactionChangeLog, TransactionDetail, SP2DRaw, SP2DImportBatch, MonitoringSummary,
            )
        )

    def test_dry_run_does_not_change_records(self):
        before = self._operational_total()
        stdout = io.StringIO()
        call_command("reset_operational_data", dry_run=True, stdout=stdout)
        self.assertEqual(self._operational_total(), before)
        self.assertIn("Mode: DRY-RUN", stdout.getvalue())
        self.assertIn("TransactionDetail", stdout.getvalue())

    def test_execute_without_token_or_wrong_token_is_rejected(self):
        with self.assertRaises(CommandError):
            call_command("reset_operational_data", execute=True)
        with self.assertRaises(CommandError):
            call_command("reset_operational_data", confirm="SALAH")

    def test_confirm_deletes_only_operational_and_retains_master_auth(self):
        call_command("reset_operational_data", confirm="RESET_INTERMILAN")
        self.assertEqual(self._operational_total(), 0)
        self.assertTrue(User.objects.filter(username="admin-reset").exists())
        self.assertTrue(Profile.objects.filter(user=self.user).exists())
        self.assertTrue(MasterAkun.objects.filter(kode="521111").exists())
        self.assertTrue(ChecklistTemplate.objects.filter(nama_dokumen="SPM").exists())

    def test_files_deleted_only_with_include_files_and_command_is_idempotent(self):
        media_file = self.media_root / "uploads/paket_spm/2026/07/paket.pdf"
        call_command("reset_operational_data", confirm="RESET_INTERMILAN")
        self.assertTrue(media_file.exists())

        self._make_operational_data()
        call_command("reset_operational_data", confirm="RESET_INTERMILAN", include_files=True)
        self.assertFalse(media_file.exists())
        self.assertFalse(any((self.media_root / "ocr_cache").rglob("*.*")))
        call_command("reset_operational_data", confirm="RESET_INTERMILAN", include_files=True)
        self.assertEqual(self._operational_total(), 0)

    def test_file_outside_media_is_not_deleted(self):
        outside = self.base_dir / "source.pdf"
        outside.write_bytes(b"private")
        call_command("reset_operational_data", confirm="RESET_INTERMILAN", include_files=True)
        self.assertTrue(outside.exists())

    def test_backup_failure_aborts_delete(self):
        before = self._operational_total()
        with patch.object(Command, "_backup_operational_data", side_effect=CommandError("backup gagal")):
            with self.assertRaises(CommandError):
                call_command("reset_operational_data", confirm="RESET_INTERMILAN")
        self.assertEqual(self._operational_total(), before)
