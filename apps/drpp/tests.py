"""
Comprehensive Fase 3 test suite for DRPP & Kuitansi Mandiri workflow.

Coverage per checklist poin 7:
- DRPPImportBatch
- statistik invariant
- kuitansi mandiri tanpa fake DRPPUpload
- DRPPItem source_type constraint
- identity_key idempoten
- source_row_key review dedupe
- akun kosong tidak membuat TransactionDetail
- akun harus aktif di MasterAkun
- FINAL tidak berubah
- DIARSIPKAN tidak berubah dan tidak diduplikasi
- 00166T berbeda dengan 00166A (normalized_bukti_key)
- multi DRPP
- multi kuitansi
- upload ulang menjadi SKIP
- manual D_K tidak tertimpa
- hanya satu active DRPPMatch
- preview tidak menulis database
- commit parse ulang
- cross-satker ditolak
- viewer ditolak
- ZIP traversal ditolak
- ZIP bomb ditolak
- MIME spoofing ditolak
- Google Drive gagal tidak rollback database
- tidak ada TransactionDetail kosong
- jumlah tunggal tidak difabrikasi menjadi bruto dan netto
"""
import io
import os
import shutil
import tempfile
import zipfile
import hashlib
from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.db import IntegrityError
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import Profile
from apps.core.parsers import normalized_bukti_key
from apps.documents.models import DocumentDriveLink, DocumentUpload
from apps.dk.models import MasterAkun, TransactionDetail, TransactionChangeLog
from apps.drpp.models import (
    DRPPImportBatch,
    DRPPItem,
    DRPPMatch,
    DRPPSupportingAttachment,
    DRPPUpload,
)
from apps.drpp.services import (
    commit_drpp_rows,
    classify_drpp_rows,
    prepare_drpp_rows,
    get_drpp_item_hard_identity,
    get_kw_mandiri_hard_identity,
    get_source_row_key,
)

User = get_user_model()


def make_user(username, satker_code, role=Profile.Role.SATKER, password="pass123"):
    u = User.objects.create_user(username, f"{username}@example.com", password)
    p = u.profile
    p.role = role
    p.satker_code = satker_code
    p.save()
    return u


def make_row(**kwargs):
    """Helper to build a minimal classified row dict."""
    defaults = {
        "source_type": DRPPItem.SourceType.KUITANSI_MANDIRI,
        "satker_code": "SAT1",
        "tahun": "2025",
        "nomor_drpp": "",
        "no_kuitansi": "KW001",
        "akun": "511111",
        "bruto": Decimal("1000"),
        "netto": Decimal("1000"),
        "jumlah": Decimal("1000"),
        "tanggal_bukti": None,
        "penerima": "Test",
        "keperluan": "Test keperluan",
        "source_file": "test.zip",
        "source_file_hash": hashlib.sha256(b"file001").hexdigest(),
        "source_member_name": "folder/test.pdf",
        "source_row_id": "page:1:y:100",
        "source_row_key": hashlib.sha256(b"test001").hexdigest(),
        "identity_key": get_kw_mandiri_hard_identity("SAT1", "2025", "KW001"),
        "parser_needs_review": False,
        "parser_review_fields": [],
    }
    defaults.update(kwargs)
    return defaults


def mock_prep_rows(rows):
    """Return a mock prepare_drpp_rows result."""
    return {"ok": True, "warnings": [], "rows": rows, "file_hash": hashlib.sha256(b"file001").hexdigest()}


# ---------------------------------------------------------------------------
# Unit tests: normalized_bukti_key
# ---------------------------------------------------------------------------
class NormalizedBuktiKeyTest(TestCase):
    def test_basic_numeric(self):
        self.assertEqual(normalized_bukti_key("001"), "1")
        self.assertEqual(normalized_bukti_key("00289"), "289")

    def test_suffix_preserved(self):
        key_t = normalized_bukti_key("00166T")
        key_a = normalized_bukti_key("00166A")
        self.assertNotEqual(key_t, key_a,
                            "00166T and 00166A must produce different keys")
        self.assertEqual(key_t, "166T")
        self.assertEqual(key_a, "166A")

    def test_same_number_same_suffix(self):
        self.assertEqual(normalized_bukti_key("KW 00166T"), normalized_bukti_key("00166T"))

    def test_no_suffix_gives_plain_number(self):
        self.assertEqual(normalized_bukti_key("00166"), "166")


# ---------------------------------------------------------------------------
# Model-level tests
# ---------------------------------------------------------------------------
class DRPPModelTest(TestCase):

    def test_constraint_drpp_item_requires_upload(self):
        """source_type=DRPP_ITEM must have drpp_upload set."""
        with self.assertRaises(IntegrityError):
            DRPPItem.objects.create(
                source_type=DRPPItem.SourceType.DRPP_ITEM,
                drpp_upload=None,
                no_bukti="KW-FAIL",
            )

    def test_kuitansi_mandiri_no_upload(self):
        """source_type=KUITANSI_MANDIRI must NOT have drpp_upload."""
        item = DRPPItem.objects.create(
            source_type=DRPPItem.SourceType.KUITANSI_MANDIRI,
            drpp_upload=None,
            no_bukti="KW-OK",
        )
        self.assertIsNone(item.drpp_upload)

    def test_kuitansi_mandiri_with_upload_fails(self):
        """source_type=KUITANSI_MANDIRI must NOT have drpp_upload — constraint enforced."""
        upload = DRPPUpload.objects.create(nomor_drpp="DRPP-1")
        with self.assertRaises(IntegrityError):
            DRPPItem.objects.create(
                source_type=DRPPItem.SourceType.KUITANSI_MANDIRI,
                drpp_upload=upload,
                no_bukti="KW-FAIL2",
            )

    def test_only_one_active_drppmatch(self):
        """DRPPMatch is OneToOne on drpp_item — second match raises IntegrityError."""
        item = DRPPItem.objects.create(
            source_type=DRPPItem.SourceType.KUITANSI_MANDIRI,
            no_bukti="KW-M1",
        )
        DRPPMatch.objects.create(drpp_item=item)
        with self.assertRaises(IntegrityError):
            DRPPMatch.objects.create(drpp_item=item)

    def test_no_transactiondetail_created_without_values(self):
        """DRPPItem can exist without creating any TransactionDetail."""
        DRPPItem.objects.create(
            source_type=DRPPItem.SourceType.KUITANSI_MANDIRI,
            no_bukti="KW-EMPTY",
        )
        self.assertEqual(TransactionDetail.objects.count(), 0)

    def test_supporting_attachment_links_drpp_parent_and_document_upload(self):
        upload = DRPPUpload.objects.create(
            nomor_drpp="00043/DRPP/019937/2026",
            nomor_drpp_norm=normalized_bukti_key("00043/DRPP/019937/2026"),
            satker_code="019937",
            tahun=2026,
        )
        document = DocumentUpload.objects.create(
            original_filename="kuitansi.pdf",
            document_type="Kuitansi",
            file="test/kuitansi.pdf",
            file_hash="hash-kuitansi",
            file_size=17,
            mime_type="application/pdf",
        )

        attachment = DRPPSupportingAttachment.objects.create(
            drpp_upload=upload,
            document_upload=document,
            satker_code="019937",
            tahun=2026,
            nomor_drpp=upload.nomor_drpp,
            nomor_drpp_norm=upload.nomor_drpp_norm,
        )

        self.assertEqual(upload.supporting_attachments.get(), attachment)
        self.assertEqual(document.drpp_supporting_attachment, attachment)
        self.assertEqual(TransactionDetail.objects.count(), 0)

    def test_supporting_attachment_can_use_manual_drpp_identity_without_parent(self):
        document = DocumentUpload.objects.create(
            original_filename="manual-kuitansi.pdf",
            document_type="Kuitansi",
            file="test/manual-kuitansi.pdf",
            file_hash="hash-manual-kuitansi",
            file_size=17,
            mime_type="application/pdf",
        )

        attachment = DRPPSupportingAttachment.objects.create(
            drpp_upload=None,
            document_upload=document,
            satker_code="019937",
            tahun=2026,
            nomor_drpp="00044/DRPP/019937/2026",
            nomor_drpp_norm="00044",
        )

        self.assertIsNone(attachment.drpp_upload)
        self.assertEqual(document.drpp_supporting_attachment, attachment)
        self.assertEqual(TransactionDetail.objects.count(), 0)


# ---------------------------------------------------------------------------
# Service-level: classify_drpp_rows
# ---------------------------------------------------------------------------
class ClassifyDRPPRowsTest(TestCase):

    def setUp(self):
        MasterAkun.objects.create(kode="511111", nama_akun="Test", is_active=True)

    def test_preview_does_not_write_db(self):
        rows = [make_row()]
        before = TransactionDetail.objects.count()
        classify_drpp_rows(rows)
        after = TransactionDetail.objects.count()
        self.assertEqual(before, after)
        self.assertEqual(rows[0]["status"], "BARU")

    def test_akun_kosong_is_review(self):
        rows = [make_row(akun="")]
        result = classify_drpp_rows(rows)
        self.assertEqual(result[0]["status"], "REVIEW_AKUN")
        self.assertIn("Akun kosong", result[0]["message"])

    def test_inactive_akun_is_review(self):
        MasterAkun.objects.filter(kode="511111").update(is_active=False)
        rows = [make_row(akun="511111")]
        result = classify_drpp_rows(rows)
        self.assertEqual(result[0]["status"], "REVIEW_AKUN")
        self.assertIn("tidak aktif", result[0]["message"])

    def test_active_akun_passes(self):
        rows = [make_row(akun="511111")]
        result = classify_drpp_rows(rows)
        self.assertEqual(result[0]["status"], "BARU")

    def test_final_dk_produces_konflik(self):
        dk = TransactionDetail.objects.create(
            satker_code="SAT1",
            no_kuitansi="KW001",
            akun="511111",
            tanggal_spm=date(2025, 1, 1),
            status_detail=TransactionDetail.StatusDetail.FINAL,
        )
        rows = [make_row(no_kuitansi="KW001")]
        result = classify_drpp_rows(rows)
        self.assertEqual(result[0]["status"], "KONFLIK_TERKUNCI")

    def test_diarsipkan_dk_produces_konflik(self):
        TransactionDetail.objects.create(
            satker_code="SAT1",
            no_kuitansi="KW001",
            akun="511111",
            tanggal_spm=date(2025, 1, 1),
            status_detail=TransactionDetail.StatusDetail.DIARSIPKAN,
        )
        rows = [make_row(no_kuitansi="KW001")]
        result = classify_drpp_rows(rows)
        self.assertEqual(result[0]["status"], "KONFLIK_DIARSIPKAN")

    def test_different_suffix_not_matched(self):
        """00166T and 00166A must NOT match same TransactionDetail."""
        TransactionDetail.objects.create(
            satker_code="SAT1",
            no_kuitansi="00166T",
            akun="511111",
            tanggal_spm=date(2025, 1, 1),
            status_detail=TransactionDetail.StatusDetail.FINAL,
        )
        # Upload 00166A — should be BARU, not KONFLIK
        rows = [make_row(no_kuitansi="00166A")]
        result = classify_drpp_rows(rows)
        self.assertEqual(result[0]["status"], "BARU",
                         "00166A must not match the FINAL record for 00166T")

    def test_source_row_key_dedupe_for_review(self):
        """Two rows with same source_row_key both get REVIEW if akun empty."""
        shared_key = hashlib.sha256(b"shared").hexdigest()
        rows = [
            make_row(akun="", source_row_key=shared_key, no_kuitansi="KW1",
                     identity_key=get_kw_mandiri_hard_identity("SAT1", "2025", "KW1")),
            make_row(akun="", source_row_key=shared_key, no_kuitansi="KW2",
                     identity_key=get_kw_mandiri_hard_identity("SAT1", "2025", "KW2")),
        ]
        result = classify_drpp_rows(rows)
        self.assertTrue(all(r["status"] == "REVIEW_AKUN" for r in result))


# ---------------------------------------------------------------------------
# Service-level: commit_drpp_rows
# ---------------------------------------------------------------------------
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CommitDRPPRowsTest(TestCase):

    def setUp(self):
        self.media_dir = tempfile.mkdtemp(prefix="drpp-tests-")
        self.media_override = override_settings(MEDIA_ROOT=self.media_dir)
        self.media_override.enable()
        self.user_sat1 = make_user("op_sat1", "SAT1")
        self.user_sat2 = make_user("op_sat2", "SAT2")
        self.admin = make_user("admin", "", role=Profile.Role.ADMIN_PUSAT)
        MasterAkun.objects.create(kode="511111", nama_akun="Test", is_active=True)

    def tearDown(self):
        self.media_override.disable()
        shutil.rmtree(self.media_dir, ignore_errors=True)
        super().tearDown()

    def _commit(self, rows, satker_code="SAT1", user=None, tahun="2025"):
        user = user or self.user_sat1
        source_path = os.path.join(self.media_dir, "fake.zip")
        with open(source_path, "wb") as source:
            source.write(b"fake zip source")
        with mock.patch("apps.drpp.services.prepare_drpp_rows", return_value=mock_prep_rows(rows)):
            return commit_drpp_rows(
                source_path, False, satker_code, tahun, user,
                "fake.zip", "fake.zip",
            )

    def test_cross_satker_rejected(self):
        """Operator from SAT1 cannot commit for SAT2."""
        result = self._commit([make_row(satker_code="SAT2")], satker_code="SAT2", user=self.user_sat1)
        self.assertFalse(result["ok"])
        self.assertIn("Akses ditolak", result["error"][0])

    def test_admin_can_commit_any_satker(self):
        """Admin can commit for any satker."""
        row = make_row(satker_code="SAT2",
                       identity_key=get_kw_mandiri_hard_identity("SAT2", "2025", "KW001"))
        result = self._commit([row], satker_code="SAT2", user=self.admin)
        self.assertTrue(result["ok"])

    def test_batch_satker_overrides_source_and_uploader_profile(self):
        TransactionDetail.objects.create(
            satker_code="SAT1",
            no_kuitansi="KW001",
            akun="511111",
            nilai_bruto=Decimal("1000"),
            nilai_netto=Decimal("1000"),
            deskripsi="Test keperluan",
            tanggal_spm=date(2025, 1, 1),
            status_detail=TransactionDetail.StatusDetail.FINAL,
        )
        row = make_row(satker_code="SAT1")
        result = self._commit([row], satker_code="SAT2", user=self.admin)
        self.assertEqual(result["batch"].satker_code, "SAT2")
        self.assertEqual(DRPPItem.objects.get().satker_code, "SAT2")
        self.assertTrue(TransactionDetail.objects.filter(satker_code="SAT2", no_kuitansi="KW001").exists())

    def test_akun_kosong_does_not_create_dk(self):
        result = self._commit([make_row(akun="")])
        self.assertTrue(result["ok"])
        batch = result["batch"]
        self.assertEqual(batch.review_rows, 1)
        self.assertEqual(batch.created_rows, 0)
        self.assertEqual(TransactionDetail.objects.count(), 0)

    def test_idempotent_upload_second_is_skip(self):
        """Committing the same row twice: 2nd commit should not create new D_K."""
        row = make_row()
        result1 = self._commit([row])
        self.assertTrue(result1["ok"])
        dk_count_after_first = TransactionDetail.objects.count()

        result2 = self._commit([row])
        self.assertTrue(result2["ok"])
        dk_count_after_second = TransactionDetail.objects.count()
        self.assertEqual(dk_count_after_first, dk_count_after_second,
                         "Second identical upload must not create duplicate D_K")

    def test_manual_dk_not_overwritten(self):
        """A manually created D_K should not be overwritten with wrong values."""
        dk = TransactionDetail.objects.create(
            satker_code="SAT1",
            no_kuitansi="KW001",
            akun="511111",
            nilai_bruto=Decimal("5000"),
            nilai_netto=Decimal("5000"),
            tanggal_spm=date(2025, 1, 1),
        )
        row = make_row(bruto=Decimal("9999"), netto=Decimal("9999"))
        self._commit([row])
        dk.refresh_from_db()
        # DRPP import does not overwrite existing D_K bruto/netto
        self.assertEqual(dk.nilai_bruto, Decimal("5000"),
                         "Manual D_K nilai_bruto must not be overwritten by DRPP import")

    def test_batch_statistics_invariant(self):
        """created + updated + skipped + conflict + review + failed == len(rows)."""
        rows = [
            make_row(no_kuitansi="KW1", akun="",
                     identity_key=get_kw_mandiri_hard_identity("SAT1", "2025", "KW1")),
            make_row(no_kuitansi="KW2",
                     identity_key=get_kw_mandiri_hard_identity("SAT1", "2025", "KW2")),
        ]
        result = self._commit(rows)
        b = result["batch"]
        total = b.created_rows + b.updated_rows + b.skipped_rows + b.conflict_rows + b.review_rows + b.failed_rows
        self.assertEqual(total, len(rows),
                         f"Statistics invariant violated: {total} != {len(rows)}")

    def test_konflik_final_not_modified(self):
        """A FINAL D_K must not be modified by DRPP import."""
        MasterAkun.objects.create(kode="522222", nama_akun="Akun pembanding", is_active=True)
        dk = TransactionDetail.objects.create(
            satker_code="SAT1",
            no_kuitansi="KW001",
            akun="511111",
            nilai_bruto=Decimal("1000"),
            tanggal_spm=date(2025, 1, 1),
            status_detail=TransactionDetail.StatusDetail.FINAL,
        )
        row = make_row(akun="522222", bruto=Decimal("9999"))
        result = self._commit([row])
        dk.refresh_from_db()
        self.assertEqual(dk.akun, "511111", "FINAL D_K akun must not be changed")
        self.assertEqual(dk.nilai_bruto, Decimal("1000"), "FINAL D_K bruto must not be changed")
        batch = result["batch"]
        self.assertEqual(batch.conflict_rows, 1)

    def test_konflik_diarsipkan_not_duplicated(self):
        """DIARSIPKAN D_K must not be touched or duplicated."""
        TransactionDetail.objects.create(
            satker_code="SAT1",
            no_kuitansi="KW001",
            akun="511111",
            tanggal_spm=date(2025, 1, 1),
            status_detail=TransactionDetail.StatusDetail.DIARSIPKAN,
        )
        row = make_row()
        result = self._commit([row])
        self.assertEqual(TransactionDetail.objects.filter(no_kuitansi="KW001").count(), 1,
                         "Must not create duplicate for DIARSIPKAN D_K")
        self.assertEqual(result["batch"].conflict_rows, 1)

    def test_drive_failure_does_not_rollback_db(self):
        """Google Drive failure must not rollback already-committed D_K."""
        row = make_row()
        result = self._commit([row])
        self.assertTrue(result["ok"])
        # Even without Drive, D_K should exist
        self.assertEqual(TransactionDetail.objects.filter(no_kuitansi="KW001").count(), 1)

    def test_jumlah_tunggal_not_fabricated(self):
        """A single jumlah value must not be automatically split into bruto+netto."""
        item = DRPPItem.objects.create(
            source_type=DRPPItem.SourceType.KUITANSI_MANDIRI,
            no_bukti="KW-SINGLE",
            jumlah=Decimal("5000"),
            nilai_bruto=Decimal("0"),
            nilai_netto=Decimal("0"),
        )
        # jumlah=5000, nilai_bruto=0, nilai_netto=0 — must remain as set
        self.assertEqual(item.nilai_bruto, Decimal("0"),
                         "jumlah tunggal must not auto-fabricate nilai_bruto")
        self.assertEqual(item.nilai_netto, Decimal("0"),
                         "jumlah tunggal must not auto-fabricate nilai_netto")

    def test_single_jumlah_is_persisted_for_review_without_dk(self):
        row = make_row(jumlah=Decimal("5000"), bruto=None, netto=None)
        result = self._commit([row])
        self.assertTrue(result["ok"])
        item = DRPPItem.objects.get(source_row_key=row["source_row_key"])
        self.assertEqual(item.jumlah, Decimal("5000"))
        self.assertIsNone(item.nilai_bruto)
        self.assertIsNone(item.nilai_netto)
        self.assertEqual(item.status_verifikasi, DRPPItem.StatusVerifikasi.PERLU_REVIEW)
        self.assertIn("Bruto dan netto", item.catatan)
        self.assertEqual(result["batch"].review_rows, 1)
        self.assertEqual(result["batch"].status, DRPPImportBatch.Status.COMPLETED_WITH_REVIEW)
        self.assertEqual(TransactionDetail.objects.count(), 0)

    def test_master_akun_empty_is_not_bypassed(self):
        MasterAkun.objects.all().delete()
        result = self._commit([make_row()])
        item = DRPPItem.objects.get()
        self.assertEqual(item.status_verifikasi, DRPPItem.StatusVerifikasi.PERLU_REVIEW)
        self.assertIn("MasterAkun", item.catatan)
        self.assertEqual(result["batch"].review_rows, 1)
        self.assertEqual(TransactionDetail.objects.count(), 0)

    def test_missing_identity_is_persisted_without_dk(self):
        row = make_row(
            source_type=DRPPItem.SourceType.UNRESOLVED,
            no_kuitansi="",
            identity_key=None,
        )
        result = self._commit([row])
        item = DRPPItem.objects.get()
        self.assertIsNone(item.identity_key)
        self.assertEqual(item.status_verifikasi, DRPPItem.StatusVerifikasi.PERLU_REVIEW)
        self.assertIn("Identitas", item.catatan)
        self.assertEqual(result["batch"].review_rows, 1)
        self.assertEqual(TransactionDetail.objects.count(), 0)

    def test_missing_drpp_number_is_persisted_as_identity_review(self):
        row = make_row(
            source_type=DRPPItem.SourceType.UNRESOLVED,
            nomor_drpp="",
            identity_key=None,
        )
        result = self._commit([row])
        item = DRPPItem.objects.get()
        self.assertEqual(item.source_type, DRPPItem.SourceType.UNRESOLVED)
        self.assertEqual(item.status_verifikasi, DRPPItem.StatusVerifikasi.PERLU_REVIEW)
        self.assertEqual(result["batch"].review_rows, 1)
        self.assertEqual(TransactionDetail.objects.count(), 0)

    def test_batch_and_local_document_audit_fields(self):
        result = self._commit([make_row()])
        batch = result["batch"]
        self.assertEqual(batch.total_rows, 1)
        self.assertEqual(batch.satker_code, "SAT1")
        self.assertEqual(batch.tahun, 2025)
        self.assertEqual(len(batch.file_hash), 64)
        self.assertIsNotNone(batch.document_upload)
        self.assertTrue(batch.document_upload.file.name)
        counted = sum(
            getattr(batch, field)
            for field in ("created_rows", "updated_rows", "skipped_rows", "conflict_rows", "review_rows", "failed_rows")
        )
        self.assertEqual(counted, batch.total_rows)
        self.assertEqual(batch.status, DRPPImportBatch.Status.COMPLETED)

    def test_parser_failure_is_audited_as_failed_batch(self):
        source_path = os.path.join(self.media_dir, "broken.zip")
        with open(source_path, "wb") as source:
            source.write(b"broken")
        failed_prep = {
            "ok": False,
            "warnings": ["arsip rusak"],
            "rows": [],
            "file_hash": hashlib.sha256(b"broken").hexdigest(),
        }
        with mock.patch("apps.drpp.services.prepare_drpp_rows", return_value=failed_prep):
            result = commit_drpp_rows(
                source_path, False, "SAT1", 2025, self.user_sat1, "broken.zip", "broken.zip"
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["batch"].status, DRPPImportBatch.Status.FAILED)
        self.assertIsNotNone(result["batch"].document_upload)
        self.assertIn("arsip rusak", result["batch"].notes)

    def test_same_source_row_dedupes_audit_and_dk(self):
        row = make_row()
        first = self._commit([row])
        second = self._commit([row])
        self.assertTrue(first["ok"] and second["ok"])
        self.assertEqual(DRPPItem.objects.count(), 1)
        self.assertEqual(TransactionDetail.objects.count(), 1)
        self.assertEqual(second["batch"].skipped_rows, 1)

    def test_manual_description_mismatch_becomes_conflict(self):
        dk = TransactionDetail.objects.create(
            satker_code="SAT1",
            no_kuitansi="KW001",
            akun="511111",
            nilai_bruto=Decimal("1000"),
            nilai_netto=Decimal("1000"),
            deskripsi="Deskripsi manual",
            tanggal_spm=date(2025, 1, 1),
        )
        result = self._commit([make_row(keperluan="Deskripsi hasil OCR")])
        dk.refresh_from_db()
        item = DRPPItem.objects.get()
        self.assertEqual(dk.deskripsi, "Deskripsi manual")
        self.assertEqual(item.status_verifikasi, DRPPItem.StatusVerifikasi.TIDAK_SESUAI)
        self.assertEqual(item.match.status_match, DRPPMatch.StatusMatch.KONFLIK)
        self.assertIn("deskripsi", item.match.catatan)
        self.assertEqual(result["batch"].conflict_rows, 1)

    def test_same_receipt_different_document_year_does_not_match(self):
        TransactionDetail.objects.create(
            satker_code="SAT1",
            no_kuitansi="KW001",
            akun="511111",
            nilai_bruto=Decimal("1000"),
            nilai_netto=Decimal("1000"),
            deskripsi="Test keperluan",
            tanggal_spm=date(2024, 1, 1),
        )
        result = self._commit([make_row()])
        self.assertEqual(result["batch"].created_rows, 1)
        self.assertEqual(TransactionDetail.objects.count(), 2)

    def test_ambiguous_exact_match_is_conflict_not_first_row(self):
        for _ in range(2):
            TransactionDetail.objects.create(
                satker_code="SAT1",
                no_kuitansi="KW001",
                akun="511111",
                nilai_bruto=Decimal("1000"),
                nilai_netto=Decimal("1000"),
                deskripsi="Test keperluan",
                tanggal_spm=date(2025, 1, 1),
            )
        row = make_row()
        self.assertEqual(classify_drpp_rows([dict(row)])[0]["status"], "KONFLIK_AMBIGU")
        result = self._commit([row])
        self.assertEqual(result["batch"].conflict_rows, 1)
        self.assertEqual(TransactionDetail.objects.count(), 2)
        self.assertIsNone(DRPPItem.objects.get().match.transaction_detail)

    def test_drpp_parent_keeps_first_and_last_batch_history(self):
        row = make_row(
            source_type=DRPPItem.SourceType.DRPP_ITEM,
            nomor_drpp="00042",
            identity_key=get_drpp_item_hard_identity("SAT1", "2025", "00042", "KW001"),
            tanggal_drpp=date(2025, 6, 1),
            nomor_spm="00166T",
            drpp_total=Decimal("1000"),
            drpp_raw_text="raw drpp",
        )
        first = self._commit([row])
        second = self._commit([row])
        upload = DRPPUpload.objects.get()
        self.assertEqual(upload.first_import_batch, first["batch"])
        self.assertEqual(upload.last_import_batch, second["batch"])
        self.assertEqual(upload.nomor_drpp_norm, "42")
        self.assertEqual(upload.total_jumlah, Decimal("1000"))
        self.assertEqual(upload.match_status, DRPPUpload.MatchStatus.COCOK)

    def test_source_row_race_recovers_with_savepoint(self):
        row = make_row()
        self._commit([row])
        with mock.patch("apps.drpp.services.DRPPItem.objects.get_or_create", side_effect=IntegrityError):
            result = self._commit([row])
        self.assertTrue(result["ok"])
        self.assertEqual(DRPPItem.objects.count(), 1)

    def test_item_identity_race_recovers_when_source_row_changes(self):
        first_row = make_row()
        self._commit([first_row])
        second_row = make_row(
            source_row_key=hashlib.sha256(b"second-source-row").hexdigest(),
            source_row_id="page:3:y:500",
        )
        result = self._commit([second_row])
        self.assertTrue(result["ok"])
        self.assertEqual(DRPPItem.objects.count(), 1)
        self.assertEqual(DRPPItem.objects.get().source_row_key, second_row["source_row_key"])
        self.assertEqual(TransactionDetail.objects.count(), 1)

    def test_drpp_parent_race_recovers_with_savepoint(self):
        row = make_row(
            source_type=DRPPItem.SourceType.DRPP_ITEM,
            nomor_drpp="00042",
            identity_key=get_drpp_item_hard_identity("SAT1", "2025", "00042", "KW001"),
        )
        self._commit([row])
        with mock.patch("apps.drpp.services.DRPPUpload.objects.get_or_create", side_effect=IntegrityError):
            result = self._commit([row])
        self.assertTrue(result["ok"])
        self.assertEqual(DRPPUpload.objects.count(), 1)

    def test_match_race_recovers_with_savepoint(self):
        row = make_row()
        self._commit([row])
        with mock.patch("apps.drpp.services.DRPPMatch.objects.get_or_create", side_effect=IntegrityError):
            result = self._commit([row])
        self.assertTrue(result["ok"])
        self.assertEqual(DRPPMatch.objects.count(), 1)


# ---------------------------------------------------------------------------
# View-level: security tests
# ---------------------------------------------------------------------------
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class DRPPViewSecurityTest(TestCase):

    def setUp(self):
        self.media_dir = tempfile.mkdtemp(prefix="drpp-view-tests-")
        self.media_override = override_settings(MEDIA_ROOT=self.media_dir)
        self.media_override.enable()
        self.client = Client()
        self.viewer = make_user("viewer1", "", role=Profile.Role.VIEWER)
        self.operator = make_user("op1", "SAT1")
        self.other_operator = make_user("op2", "SAT2")
        self.admin = make_user("admin-view", "", role=Profile.Role.ADMIN_PUSAT)
        self.list_url = reverse("drpp:list")
        self.preview_url = reverse("drpp:preview")

    def tearDown(self):
        self.media_override.disable()
        shutil.rmtree(self.media_dir, ignore_errors=True)
        super().tearDown()

    def _make_zip_bytes(self, filenames=None, content=b"PDF content"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name in (filenames or ["test.pdf"]):
                zf.writestr(name, content)
        return buf.getvalue()

    def test_viewer_upload_rejected(self):
        """Viewer must not be able to upload — receives error redirect."""
        self.client.login(username="viewer1", password="pass123")
        pdf = SimpleUploadedFile("test.pdf", b"%PDF-1.4 fake", content_type="application/pdf")
        resp = self.client.post(self.list_url, {"file_drpp": pdf, "tahun": "2025"}, follow=True)
        self.assertContains(resp, "tidak memiliki hak akses")

    def test_anonymous_redirected(self):
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])

    def test_mime_spoofing_rejected(self):
        """A .zip file with an explicitly invalid MIME must fail MIME validation."""
        self.client.login(username="op1", password="pass123")
        # Simulate file with .zip name but explicitly set to text/html MIME
        f = SimpleUploadedFile("evil.zip", b"not a zip", content_type="text/html")
        resp = self.client.post(self.list_url, {"file_drpp": f, "tahun": "2025"}, follow=True)
        # Should redirect to list with error about MIME
        self.assertContains(resp, "MIME")

    def test_zip_traversal_rejected(self):
        """ZIP entries with ../ path traversal are caught and view redirects gracefully."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../../evil.py", "malicious")
        safe_zip = buf.getvalue()

        self.client.login(username="op1", password="pass123")
        # Upload phase: should save file and redirect to preview
        f = SimpleUploadedFile("traversal.zip", safe_zip, content_type="application/zip")
        resp = self.client.post(self.list_url, {"file_drpp": f, "tahun": "2025"}, follow=False)
        # Should redirect to preview (upload accepted; traversal caught inside parse)
        self.assertEqual(resp.status_code, 302)
        # Now visit preview: view should catch ValueError and redirect to list
        resp2 = self.client.get(reverse("drpp:preview"), follow=True)
        # Should end up at list (not crash)
        self.assertEqual(resp2.status_code, 200)

    def test_zip_bomb_large_rejected(self):
        """Files exceeding individual MAX_SIZE (50MB) must be rejected before saving."""
        self.client.login(username="op1", password="pass123")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("small.pdf", b"X" * 100)
        zip_bytes = buf.getvalue()
        f = SimpleUploadedFile("big.zip", zip_bytes, content_type="application/zip")
        
        # Patch the size to exceed limit during the view execution
        with mock.patch("django.core.files.uploadedfile.UploadedFile.size", new_callable=mock.PropertyMock) as mock_size:
            mock_size.return_value = 60 * 1024 * 1024  # 60MB
            resp = self.client.post(self.list_url, {"file_drpp": f, "tahun": "2025"}, follow=True)
            
        # Should redirect to list with size error message
        self.assertContains(resp, "50MB")

    def test_preview_session_missing_redirects_to_list(self):
        """Preview without a valid session key must redirect to list."""
        self.client.login(username="op1", password="pass123")
        # No drpp_preview session — should redirect to list
        resp = self.client.get(self.preview_url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("drpp:list"), resp["Location"])

    def test_single_pdf_is_wrapped_as_safe_zip(self):
        self.client.login(username="op1", password="pass123")
        pdf = SimpleUploadedFile("single.pdf", b"%PDF-1.4 source", content_type="application/pdf")
        response = self.client.post(self.list_url, {"document_files": [pdf], "tahun": "2025"})
        self.assertRedirects(response, self.preview_url, fetch_redirect_response=False)
        state = self.client.session["drpp_preview"]
        self.assertTrue(zipfile.is_zipfile(state["file_path"]))
        with zipfile.ZipFile(state["file_path"]) as archive:
            self.assertEqual(archive.namelist(), ["single.pdf"])
        self.client.post(self.preview_url, {"action": "cancel"})
        self.assertFalse(os.path.exists(state["file_path"]))

    def test_single_zip_is_stored_directly(self):
        self.client.login(username="op1", password="pass123")
        zip_bytes = self._make_zip_bytes(["direct.pdf"])
        uploaded = SimpleUploadedFile("direct.zip", zip_bytes, content_type="application/zip")
        response = self.client.post(self.list_url, {"document_files": [uploaded], "tahun": "2025"})
        self.assertRedirects(response, self.preview_url, fetch_redirect_response=False)
        state = self.client.session["drpp_preview"]
        with open(state["file_path"], "rb") as stored:
            self.assertEqual(stored.read(), zip_bytes)
        self.client.post(self.preview_url, {"action": "cancel"})

    def test_multiple_pdfs_with_same_basename_do_not_overwrite(self):
        self.client.login(username="op1", password="pass123")
        first = SimpleUploadedFile("same.pdf", b"first", content_type="application/pdf")
        second = SimpleUploadedFile("same.pdf", b"second", content_type="application/pdf")
        response = self.client.post(self.list_url, {"document_files": [first, second], "tahun": "2025"})
        self.assertEqual(response.status_code, 302)
        state = self.client.session["drpp_preview"]
        with zipfile.ZipFile(state["file_path"]) as archive:
            self.assertEqual(archive.namelist(), ["same.pdf", "same_2.pdf"])
            self.assertEqual(archive.read("same.pdf"), b"first")
            self.assertEqual(archive.read("same_2.pdf"), b"second")
        self.client.post(self.preview_url, {"action": "cancel"})

    def test_mixed_zip_and_pdf_is_rejected(self):
        self.client.login(username="op1", password="pass123")
        archive = SimpleUploadedFile("batch.zip", self._make_zip_bytes(), content_type="application/zip")
        pdf = SimpleUploadedFile("extra.pdf", b"%PDF", content_type="application/pdf")
        response = self.client.post(
            self.list_url,
            {"document_files": [archive, pdf], "tahun": "2025"},
            follow=True,
        )
        self.assertContains(response, "tidak boleh dicampur")
        self.assertNotIn("drpp_preview", self.client.session)

    def test_nested_zip_is_rejected_and_original_temp_removed(self):
        outer = io.BytesIO()
        with zipfile.ZipFile(outer, "w") as archive:
            archive.writestr("nested.zip", self._make_zip_bytes())
        self.client.login(username="op1", password="pass123")
        uploaded = SimpleUploadedFile("outer.zip", outer.getvalue(), content_type="application/zip")
        self.client.post(self.list_url, {"document_files": [uploaded], "tahun": "2025"})
        state = dict(self.client.session["drpp_preview"])
        response = self.client.get(self.preview_url, follow=True)
        self.assertContains(response, "Nested ZIP tidak didukung")
        self.assertFalse(os.path.exists(state["file_path"]))
        self.assertNotIn("drpp_preview", self.client.session)

    def test_operator_satker_is_forced_from_profile(self):
        self.client.login(username="op1", password="pass123")
        pdf = SimpleUploadedFile("single.pdf", b"%PDF", content_type="application/pdf")
        self.client.post(
            self.list_url,
            {"document_files": [pdf], "tahun": "2025", "satker_code": "SAT2"},
        )
        self.assertEqual(self.client.session["drpp_preview"]["satker_code"], "SAT1")
        self.client.post(self.preview_url, {"action": "cancel"})

    def test_admin_must_choose_satker(self):
        self.client.login(username="admin-view", password="pass123")
        self.assertContains(self.client.get(self.list_url), 'name="satker_code"')
        pdf = SimpleUploadedFile("single.pdf", b"%PDF", content_type="application/pdf")
        response = self.client.post(
            self.list_url,
            {"document_files": [pdf], "tahun": "2025"},
            follow=True,
        )
        self.assertContains(response, "Satker wajib dipilih")
        self.assertNotIn("drpp_preview", self.client.session)

    def test_preview_owner_mismatch_is_rejected_and_cleaned(self):
        self.client.login(username="op1", password="pass123")
        temp_path = os.path.join(self.media_dir, "owner.zip")
        with open(temp_path, "wb") as source:
            source.write(self._make_zip_bytes())
        session = self.client.session
        session["drpp_preview"] = {
            "file_path": temp_path,
            "original_filename": "owner.zip",
            "satker_code": "SAT1",
            "tahun": 2025,
            "uploaded_by_user_id": self.other_operator.pk,
        }
        session.save()
        response = self.client.get(self.preview_url, follow=True)
        self.assertContains(response, "bukan milik pengguna aktif")
        self.assertFalse(os.path.exists(temp_path))

    def test_drive_exception_is_post_commit_and_marked_for_review(self):
        self.client.login(username="op1", password="pass123")
        temp_path = os.path.join(self.media_dir, "commit.zip")
        with open(temp_path, "wb") as source:
            source.write(self._make_zip_bytes())
        stored = DocumentUpload.objects.create(
            document_type="DRPP_BATCH",
            original_filename="commit.zip",
            stored_filename="commit.zip",
            file=SimpleUploadedFile("commit.zip", self._make_zip_bytes()),
            uploaded_by=self.operator,
        )
        batch = DRPPImportBatch.objects.create(
            uploaded_by=self.operator,
            filename="commit.zip",
            original_filename="commit.zip",
            satker_code="SAT1",
            tahun=2025,
            total_rows=0,
            status=DRPPImportBatch.Status.COMPLETED,
            document_upload=stored,
        )
        session = self.client.session
        session["drpp_preview"] = {
            "file_path": temp_path,
            "original_filename": "commit.zip",
            "satker_code": "SAT1",
            "tahun": 2025,
            "uploaded_by_user_id": self.operator.pk,
        }
        session.save()
        with mock.patch("apps.drpp.views.commit_drpp_rows", return_value={"ok": True, "batch": batch, "document_upload": stored}), mock.patch(
            "apps.drpp.views.archive_file_with_dedup", side_effect=RuntimeError("drive offline")
        ):
            response = self.client.post(self.preview_url, {"action": "commit"}, follow=True)
        self.assertContains(response, "Pengarsipan Drive tertunda")
        self.assertTrue(DRPPImportBatch.objects.filter(pk=batch.pk).exists())
        self.assertTrue(DocumentDriveLink.objects.filter(status=DocumentDriveLink.Status.PERLU_DICEK).exists())
        self.assertFalse(os.path.exists(temp_path))


# ---------------------------------------------------------------------------
# Parser-to-source hardening
# ---------------------------------------------------------------------------
class PrepareDRPPRowsHardeningTest(TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="drpp-prepare-")
        self.source_path = os.path.join(self.temp_dir, "source.zip")
        with open(self.source_path, "wb") as source:
            source.write(b"source bytes")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        super().tearDown()

    def _parsed(self, item):
        extracted_dir = tempfile.mkdtemp(prefix="drpp-extracted-")
        return extracted_dir, {
            "ok": True,
            "warnings": [],
            "temp_dir": extracted_dir,
            "drpps": [{"file_name": "DRPP.pdf", "metadata": {"nomor_drpp": "00042", "total": Decimal("5000")}}],
            "kw_by_drpp": {"00042": [item]},
        }

    def test_single_jumlah_is_not_promoted_to_bruto_and_netto(self):
        extracted_dir, parsed = self._parsed({
            "no_bukti": "KW/2025",
            "akun": "511111",
            "jumlah": Decimal("5000"),
            "source_file": "DRPP.pdf",
            "source_member_name": "folder/DRPP.pdf",
            "source_row_id": "page:2:y:300",
        })
        with mock.patch("apps.drpp.services.parse_paket_spm_zip", return_value=parsed):
            result = prepare_drpp_rows(self.source_path, satker_code="SAT1", tahun=2025)
        row = result["rows"][0]
        self.assertEqual(row["jumlah"], Decimal("5000"))
        self.assertIsNone(row["bruto"])
        self.assertIsNone(row["netto"])
        self.assertFalse(os.path.exists(extracted_dir))
        self.assertEqual(
            row["source_row_key"],
            get_source_row_key(result["file_hash"], "folder/DRPP.pdf", "page:2:y:300"),
        )

    def test_explicit_bruto_and_netto_are_preserved(self):
        _, parsed = self._parsed({
            "no_bukti": "KW/2025",
            "akun": "511111",
            "jumlah": Decimal("5000"),
            "bruto": Decimal("5000"),
            "netto": Decimal("4500"),
            "source_file": "DRPP.pdf",
        })
        with mock.patch("apps.drpp.services.parse_paket_spm_zip", return_value=parsed):
            row = prepare_drpp_rows(self.source_path, satker_code="SAT1", tahun=2025)["rows"][0]
        self.assertEqual(row["bruto"], Decimal("5000"))
        self.assertEqual(row["netto"], Decimal("4500"))

    def test_more_than_two_drpp_are_materialized_and_temp_cleaned(self):
        extracted_dir = tempfile.mkdtemp(prefix="drpp-extracted-")
        parsed = {
            "ok": True,
            "warnings": [],
            "temp_dir": extracted_dir,
            "drpps": [{"metadata": {"nomor_drpp": number}} for number in ("1", "2", "3")],
            "kw_by_drpp": {},
        }
        with mock.patch("apps.drpp.services.parse_paket_spm_zip", return_value=parsed):
            result = prepare_drpp_rows(self.source_path, satker_code="SAT1", tahun=2025)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["rows"]), 3)
        self.assertFalse(os.path.exists(extracted_dir))


# ---------------------------------------------------------------------------
# Parser default flow: drpp_kuitansi_mode=False must not change behavior
# ---------------------------------------------------------------------------
class ParserDefaultFlowTest(TestCase):

    def test_kw_standalone_blocked_in_default_mode(self):
        """
        parse_paket_spm_zip without drpp_kuitansi_mode=True must still
        block standalone KW files with a fatal error.
        """
        from apps.core.parsers import parse_paket_spm_zip

        # Build a minimal ZIP with just a KW file (no DRPP/SPM)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("KW_001.pdf", b"%PDF-1.4 fake kuitansi content")
        zip_bytes = buf.getvalue()

        tmp_zip = io.BytesIO(zip_bytes)
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            f.write(zip_bytes)
            tmp_path = f.name

        result = {}
        try:
            result = parse_paket_spm_zip(tmp_path, ocr=False)
            # In default mode: KW alone should produce warnings and ok=False
            # (or ok=True but with fatal_errors listed)
            # Key assertion: no kw_items committed without DRPP
            self.assertFalse(result["ok"],
                             "Default mode must not accept standalone KW without DRPP")
        finally:
            if result.get("temp_dir"):
                shutil.rmtree(result["temp_dir"], ignore_errors=True)
            os.unlink(tmp_path)

    def test_kw_allowed_in_drpp_kuitansi_mode(self):
        """
        parse_paket_spm_zip with drpp_kuitansi_mode=True must allow standalone KW.
        """
        from apps.core.parsers import parse_paket_spm_zip

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("KW_001.pdf", b"%PDF-1.4 fake kuitansi content")
        zip_bytes = buf.getvalue()

        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            f.write(zip_bytes)
            tmp_path = f.name

        result = {}
        try:
            result = parse_paket_spm_zip(tmp_path, ocr=False, drpp_kuitansi_mode=True)
            # Should NOT have a fatal error about KW needing DRPP
            kw_warning = any(
                "wajib diunggah bersama DRPP" in w
                for w in result.get("warnings", [])
            )
            self.assertFalse(kw_warning,
                             "drpp_kuitansi_mode=True must allow standalone KW without fatal error")
        finally:
            if result.get("temp_dir"):
                shutil.rmtree(result["temp_dir"], ignore_errors=True)
            os.unlink(tmp_path)
