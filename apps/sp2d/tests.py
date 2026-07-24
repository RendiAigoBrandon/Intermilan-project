from django.test import TestCase
from apps.sp2d.services import generate_identity_key
from apps.sp2d.models import SP2DRaw, SP2DImportBatch
from django.contrib.auth.models import User
import hashlib

class SP2DServiceTests(TestCase):
    def test_generate_identity_key_with_sp2d(self):
        key = generate_identity_key("1234", "SP2D-001", "INV-01", "SPM-01", "2026-01-01", "2026-01-01", "1000", "2026")
        expected_base = "1234|SP2D-001|2026"
        expected_key = hashlib.sha256(expected_base.encode("utf-8")).hexdigest()
        self.assertEqual(key, expected_key)

    def test_generate_identity_key_without_sp2d(self):
        key = generate_identity_key("1234", "", "INV-01", "SPM-01", "2026-01-01", "2026-01-01", "1000", "2026")
        expected_base = "1234|INV-01|2026-01-01|1000|2026"
        expected_key = hashlib.sha256(expected_base.encode("utf-8")).hexdigest()
        self.assertEqual(key, expected_key)

    def test_sp2d_raw_creation(self):
        user = User.objects.create(username="testuser", email="test@test.com")
        batch = SP2DImportBatch.objects.create(tahun=2026, bulan=1, filename="test.xlsx", original_filename="test.xlsx", uploaded_by=user)
        sp2d = SP2DRaw.objects.create(
            import_batch=batch,
            identity_key="test_key",
            satker_code="1234",
            nomor_spm_extracted="SPM-001",
            tahun=2026,
            nilai_spm=1000,
            nilai_sp2d=1000,
            created_by=user
        )
        self.assertEqual(sp2d.status, SP2DRaw.Status.PERLU_DETAIL)
        self.assertEqual(sp2d.identity_key, "test_key")

import openpyxl
from io import BytesIO
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from decimal import Decimal
import hashlib

class SP2DHardeningTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="test_upload", password="password", is_superuser=True)
        from apps.accounts.models import Profile
        profile = self.user.profile
        profile.role = Profile.Role.ADMIN_PUSAT
        profile.save()

    def _create_mock_excel(self, data_rows):
        """Create XLSX with SP2D-compatible header matching SP2D_COLUMN_MAP and SP2D_HEADER_KEYWORDS."""
        wb = openpyxl.Workbook()
        ws = wb.active
        # Headers must match SP2D_HEADER_KEYWORDS: "no sp2d", "nilai sp2d", "nomor invoice", "jenis spm", "deskripsi"
        headers = [
            "Kode Satker",       # -> satker_code
            "Nama Satker",       # -> satker_name
            "No SP2D",           # -> no_sp2d  (KEYWORD: "no sp2d")
            "Tgl SP2D",          # -> tgl_sp2d
            "Nilai SPM",         # -> nilai_spm
            "Potongan",          # -> potongan
            "Nilai SP2D",        # -> nilai_sp2d (KEYWORD: "nilai sp2d")
            "Nomor Invoice",     # -> nomor_invoice (KEYWORD: "nomor invoice")
            "Jenis SPM",         # -> jenis_spm (KEYWORD: "jenis spm")
            "Deskripsi",         # -> deskripsi (KEYWORD: "deskripsi")
        ]
        ws.append(headers)
        for row in data_rows:
            ws.append(row)

        mem = BytesIO()
        wb.save(mem)
        mem.seek(0)
        return mem.read()

    def test_service_classify_baru(self):
        """Test classify_sp2d_rows returns BARU for new row."""
        from apps.sp2d.services import classify_sp2d_rows
        rows = [{"satker_code": "999999", "satker_name": "Test Satker", "no_sp2d": "SP2D-TEST-01",
                 "tgl_sp2d": None, "nilai_spm": Decimal("1000"), "potongan": Decimal("0"),
                 "nilai_sp2d": Decimal("1000"), "nomor_invoice": "INV/001/2026",
                 "jenis_spm": "LS", "deskripsi": "Test", "nomor_spm_extracted": "SPM-001"}]
        result = classify_sp2d_rows(2026, rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["preview_status"], "BARU")

    def test_service_classify_identik_setelah_commit(self):
        """Test classify_sp2d_rows returns IDENTIK_DILEWATI setelah record ada di DB."""
        from apps.sp2d.services import classify_sp2d_rows, commit_sp2d_rows
        batch = SP2DImportBatch.objects.create(
            filename="test.xlsx", original_filename="test.xlsx",
            tahun=2026, bulan=1, total_rows=1,
            status=SP2DImportBatch.Status.PROCESSING,
            uploaded_by=self.user
        )
        rows = [{"satker_code": "888888", "satker_name": "Satker Identik", "no_sp2d": "SP2D-IDENTIK-01",
                 "tgl_sp2d": None, "nilai_spm": Decimal("5000"), "potongan": Decimal("0"),
                 "nilai_sp2d": Decimal("5000"), "nomor_invoice": "",
                 "jenis_spm": "LS", "deskripsi": "Test", "nomor_spm_extracted": ""}]
        commit_sp2d_rows(batch, rows, self.user, filename="test.xlsx")
        self.assertEqual(batch.created_rows, 1)

        # Classify again → IDENTIK_DILEWATI
        result = classify_sp2d_rows(2026, rows)
        self.assertEqual(result[0]["preview_status"], "IDENTIK_DILEWATI")

    def test_service_idempotensi_commit_dua_kali(self):
        """Commit dua kali dengan data identik → created=1, skipped=1, total SP2DRaw=1."""
        from apps.sp2d.services import commit_sp2d_rows
        rows = [{"satker_code": "777777", "satker_name": "Satker Idempoten",
                 "no_sp2d": "SP2D-IDEMPOTEN-01", "tgl_sp2d": None,
                 "nilai_spm": Decimal("2000"), "potongan": Decimal("0"), "nilai_sp2d": Decimal("2000"),
                 "nomor_invoice": "", "jenis_spm": "LS", "deskripsi": "Idempoten", "nomor_spm_extracted": ""}]

        batch1 = SP2DImportBatch.objects.create(
            filename="t1.xlsx", original_filename="t1.xlsx", tahun=2026, bulan=1,
            total_rows=1, status=SP2DImportBatch.Status.PROCESSING, uploaded_by=self.user)
        commit_sp2d_rows(batch1, rows, self.user, filename="t1.xlsx")
        self.assertEqual(batch1.created_rows, 1)
        self.assertEqual(batch1.skipped_rows, 0)

        batch2 = SP2DImportBatch.objects.create(
            filename="t2.xlsx", original_filename="t2.xlsx", tahun=2026, bulan=1,
            total_rows=1, status=SP2DImportBatch.Status.PROCESSING, uploaded_by=self.user)
        commit_sp2d_rows(batch2, rows, self.user, filename="t2.xlsx")
        self.assertEqual(batch2.created_rows, 0)
        self.assertEqual(batch2.skipped_rows, 1)
        self.assertEqual(SP2DRaw.objects.filter(satker_code="777777").count(), 1)

    def test_service_gagal_tanpa_satker(self):
        """Row tanpa satker_code → identity GAGAL, failed_rows bertambah."""
        from apps.sp2d.services import classify_sp2d_rows
        rows = [{"satker_code": "", "satker_name": "", "no_sp2d": "SP2D-NOSATKER",
                 "tgl_sp2d": None, "nilai_spm": Decimal("3000"), "potongan": Decimal("0"),
                 "nilai_sp2d": Decimal("3000"), "nomor_invoice": "", "jenis_spm": "",
                 "deskripsi": "Tanpa Satker", "nomor_spm_extracted": ""}]
        result = classify_sp2d_rows(2026, rows)
        self.assertEqual(result[0]["preview_status"], "GAGAL")

    def test_upload_excel_via_http(self):
        """Upload XLSX via HTTP POST, check redirect ke preview."""
        self.client.login(username="test_upload", password="password")
        excel_data = self._create_mock_excel([
            ["123456", "Satker HTTP", "SP2D-HTTP-01", "2026-01-15", 1000, 0, 1000, "INV/001/2026", "LS", "Test HTTP"],
        ])
        uploaded = SimpleUploadedFile(
            "test_http.xlsx", excel_data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response = self.client.post(reverse("sp2d:list"), {
            "tahun": "2026", "bulan": "1", "file_sp2d": uploaded
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn("preview", response.url)

    def test_identity_key_formula(self):
        """Verifikasi formula identity_key untuk data dengan no_sp2d."""
        from apps.sp2d.services import build_identity_result
        result = build_identity_result(
            satker="123456", sp2d_no="001A", invoice_no="", spm_no="",
            tgl_sp2d=None, tgl_invoice=None, nilai=1000, tahun=2026
        )
        expected_base = "123456|001A|2026"
        expected_key = hashlib.sha256(expected_base.encode("utf-8")).hexdigest()
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["identity_key"], expected_key)


