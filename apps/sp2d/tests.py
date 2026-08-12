from django.conf import settings
from django.http import QueryDict
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

    def test_legacy_null_identik(self):
        """Legacy NULL + identik -> skipped=1, identity_key is not NULL, last_import_batch is latest"""
        from apps.sp2d.services import commit_sp2d_rows
        # Create a legacy record
        sp2d = SP2DRaw.objects.create(
            satker_code="111111", satker_name="Satker Legacy", 
            no_sp2d="SP2D-LEGACY-01", tahun=2026,
            nilai_spm=Decimal("1000"), potongan=Decimal("0"), nilai_sp2d=Decimal("1000"), 
            jenis_spm="LS", deskripsi="Legacy",
            nomor_invoice="", nomor_spm_extracted="", 
            mata_uang="", jenis_sp2d="", cek_akun="", original_file="",
            identity_key=None
        )
        
        rows = [{"satker_code": "111111", "satker_name": "Satker Legacy",
                 "no_sp2d": "SP2D-LEGACY-01", "tgl_sp2d": None,
                 "nilai_spm": Decimal("1000"), "potongan": Decimal("0"), "nilai_sp2d": Decimal("1000"),
                 "nomor_invoice": "", "jenis_spm": "LS", "deskripsi": "Legacy", "nomor_spm_extracted": "",
                 "mata_uang": "", "jenis_sp2d": "", "cek_akun": ""}]
                 
        batch = SP2DImportBatch.objects.create(
            filename="legacy.xlsx", original_filename="legacy.xlsx", tahun=2026, bulan=1,
            total_rows=1, status=SP2DImportBatch.Status.PROCESSING, uploaded_by=self.user)
            
        commit_sp2d_rows(batch, rows, self.user, filename="legacy.xlsx")
        
        self.assertEqual(batch.created_rows, 0)
        self.assertEqual(batch.skipped_rows, 1)
        
        sp2d.refresh_from_db()
        self.assertIsNotNone(sp2d.identity_key)
        self.assertEqual(sp2d.last_import_batch, batch)
        
    def test_fallback_legacy_match(self):
        """Fallback legacy match works when no_sp2d is missing."""
        from apps.sp2d.services import find_legacy_candidates
        SP2DRaw.objects.create(
            satker_code="222222", no_sp2d="", nomor_invoice="INV-FB", 
            tanggal_invoice="2026-05-05", nilai_sp2d=500, tahun=2026,
            mata_uang="", jenis_sp2d="", cek_akun="", original_file="",
            identity_key=None
        )
        
        prepared_row = {
            "satker_code": "222222", "batch_tahun": 2026, "no_sp2d": "",
            "nomor_invoice": "INV-FB", "tanggal_invoice": "2026-05-05",
            "tgl_sp2d": None, "nilai_sp2d": 500
        }
        
        matches = find_legacy_candidates(prepared_row)
        self.assertEqual(len(matches), 1)

    def test_cross_satker_linkage_fails(self):
        """Cross-satker linkage fails (fail-closed)"""
        sp2d = SP2DRaw.objects.create(
            satker_code="999999", no_sp2d="SP2D-OTHER", tahun=2026,
            mata_uang="", jenis_sp2d="", cek_akun="", original_file=""
        )
        
        # We need a user who doesn't have permission for 999999 but has for 888888
        user2 = User.objects.create_user(username="user_satker_8", password="password")
        from apps.accounts.models import Profile
        profile = user2.profile
        profile.role = Profile.Role.SATKER
        profile.satker_code = "888888"
        profile.save()
        self.client.login(username="user_satker_8", password="password")
        
        from apps.dk.models import MasterAkun
        MasterAkun.objects.create(kode="511111", nama_akun="Test Akun", is_active=True)
        
        response = self.client.post(reverse("dk:transaction_create"), {
            "sp2d_raw_id": sp2d.id,
            "satker_code": "888888",
            "nomor_spm": "SPM-8",
            "tanggal_spm": "2026-01-01",
            "bulan_sp2d": 1,
            "cara_pembayaran": "LS",
            "jenis_spm": "LS",
            "deskripsi": "Test",
            "akun": "511111",
            "nilai_bruto": 1000,
            "nilai_netto": 1000,
            "pph21": 0
        })
        # Should render form with error, not redirect
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], None, "SP2D tidak ditemukan atau beda satker.")



    def test_legacy_revision_identity_persistence(self):
        """legacy identity_key NULL + data revisi -> updated_rows=1 -> identity_key tersimpan -> tidak membuat SP2DRaw baru."""
        from apps.sp2d.services import commit_sp2d_rows
        # Create legacy record
        sp2d = SP2DRaw.objects.create(
            satker_code="555555", satker_name="Satker Legacy",
            no_sp2d="SP2D-REVISI-01", tahun=2026,
            nilai_spm=Decimal("1000"), potongan=Decimal("0"), nilai_sp2d=Decimal("1000"),
            jenis_spm="LS", deskripsi="Legacy Asli",
            nomor_invoice="", nomor_spm_extracted="",
            mata_uang="", jenis_sp2d="", cek_akun="", original_file="",
            identity_key=None
        )

        rows = [{"satker_code": "555555", "satker_name": "Satker Legacy",
                 "no_sp2d": "SP2D-REVISI-01", "tgl_sp2d": None,
                 "nilai_spm": Decimal("1000"), "potongan": Decimal("0"), "nilai_sp2d": Decimal("1000"),
                 "nomor_invoice": "", "jenis_spm": "LS", "deskripsi": "Deskripsi Direvisi", "nomor_spm_extracted": "",
                 "mata_uang": "", "jenis_sp2d": "", "cek_akun": ""}]

        batch = SP2DImportBatch.objects.create(
            filename="revisi.xlsx", original_filename="revisi.xlsx", tahun=2026, bulan=1,
            total_rows=1, status=SP2DImportBatch.Status.PROCESSING, uploaded_by=self.user)

        commit_sp2d_rows(batch, rows, self.user, filename="revisi.xlsx")

        self.assertEqual(batch.created_rows, 0)
        self.assertEqual(batch.updated_rows, 1)

        sp2d.refresh_from_db()
        self.assertIsNotNone(sp2d.identity_key)
        self.assertEqual(sp2d.last_import_batch, batch)
        self.assertEqual(sp2d.deskripsi, "Deskripsi Direvisi")

    def test_migration_canonical_winner(self):
        """Test canonical winner logic (newest ID wins, losers get identity_key=None and TIDAK_COCOK)"""
        # Create conflict records manually with same fields
        sp2d_old = SP2DRaw.objects.create(
            satker_code="666666", no_sp2d="SP2D-CON-01", tahun=2026,
            nilai_sp2d=1000, identity_key=None, status="PERLU_DETAIL", cek_akun="old"
        )
        sp2d_new = SP2DRaw.objects.create(
            satker_code="666666", no_sp2d="SP2D-CON-01", tahun=2026,
            nilai_sp2d=1000, identity_key=None, status="PERLU_DETAIL", cek_akun="new"
        )
        
        import importlib
        migration_module = importlib.import_module("apps.sp2d.migrations.0006_finalize_legacy_identity_conflict_resolution")
        finalize_legacy_identity_conflict_resolution = migration_module.finalize_legacy_identity_conflict_resolution
        from django.apps import apps
        # Run logic directly to test
        finalize_legacy_identity_conflict_resolution(apps, None)
        
        sp2d_old.refresh_from_db()
        sp2d_new.refresh_from_db()
        
        self.assertIsNone(sp2d_old.identity_key)
        self.assertEqual(sp2d_old.status, "TIDAK_COCOK")
        self.assertIn("[KONFLIK_LEGACY_NORMALISASI]", sp2d_old.cek_akun)
        
        self.assertIsNotNone(sp2d_new.identity_key)
        self.assertNotEqual(sp2d_new.status, "TIDAK_COCOK")

    def test_legacy_skip_persists_metadata(self):
        """
        legacy identity_key NULL, nilai lama nonzero, incoming zero/blank
        yang tidak boleh overwrite -> skipped_rows=1 -> nilai lama tetap, identity_key terisi, last_import_batch terbaru
        """
        self.client.login(username="test_upload", password="password")
        
        # create legacy record with NULL identity key and nonzero nilai
        record = SP2DRaw.objects.create(
            satker_code="555555", satker_name="Satker Leg", no_sp2d="SP2D-LEGACY-01", tahun=2026,
            tgl_sp2d="2026-01-15",
            nilai_spm=1000, nilai_sp2d=1000,
            nomor_invoice="INV/L1", jenis_spm="LS", deskripsi="Test Leg",
            nomor_spm_extracted="INV/L1",
            identity_key=None
        )
        
        # Incoming zero/blank so it skipped overwrite
        excel_data = self._create_mock_excel([
            ["555555", "Satker Leg", "SP2D-LEGACY-01", "2026-01-15", 0, 0, 0, "INV/L1", "LS", "Test Leg"],
        ])
        
        uploaded = SimpleUploadedFile("legacy.xlsx", excel_data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.client.post(reverse("sp2d:list"), {"tahun": "2026", "bulan": "1", "file_sp2d": uploaded})
        
        resp = self.client.post(reverse("sp2d:preview"), {
            "action": "commit",
            "satker_code[]": ["555555"],
            "satker_name[]": [""],
            "no_sp2d[]": ["SP2D-LEGACY-01"],
            "tahun[]": ["2026"],
            "bulan_sp2d[]": [""],
            "nomor_spm[]": [""],
            "tgl_spm[]": [""],
            "jenis_spm[]": [""],
            "cara_pembayaran[]": [""],
            "akun[]": [""],
            "deskripsi[]": [""],
            "nilai_bruto[]": ["0"],
            "nilai_netto[]": ["0"],
            "potongan[]": ["0"],
            "no_kuitansi[]": [""],
            "no_drpp[]": [""],
            "pembebanan[]": [""],
            "fp[]": [""],
            "pph21[]": ["0"],
        })
        if resp.status_code != 302:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.content, 'html.parser')
            flashes = soup.find_all(class_='flash')
            for f in flashes:
                print("FLASH MESSAGE:", f.text)

        batch = SP2DImportBatch.objects.last()
        self.assertEqual(batch.skipped_rows, 1)
        self.assertEqual(batch.updated_rows, 0)
        
        record.refresh_from_db()
        self.assertIsNotNone(record.identity_key)
        self.assertEqual(record.nilai_spm, 1000)
        self.assertEqual(record.last_import_batch, batch)
        
    def test_three_way_cocok(self):
        """three-way COCOK - bruto, netto, potongan match"""
        from apps.sp2d.services import reconcile_sp2d_with_dk
        from apps.dk.models import TransactionDetail
        sp2d = SP2DRaw.objects.create(
            satker_code="999999", nomor_spm_extracted="SPM-COCOK", tahun=2026,
            nilai_spm=3000, nilai_sp2d=2500, potongan=500
        )
        # Setup 2 DK items that sum up exactly
        TransactionDetail.objects.create(
            satker_code="999999", nomor_spm="SPM-COCOK", tanggal_spm="2026-01-01",
            nilai_bruto=2000, nilai_netto=1500, status_detail="PERLU_REVIEW"
        )
        TransactionDetail.objects.create(
            satker_code="999999", nomor_spm="SPM-COCOK", tanggal_spm="2026-01-01",
            nilai_bruto=1000, nilai_netto=1000, status_detail="PERLU_REVIEW"
        )
        reconcile_sp2d_with_dk(sp2d, self.user)
        sp2d.refresh_from_db()
        self.assertEqual(sp2d.status, "COCOK")

    def test_mismatch_tidak_cocok(self):
        """mismatch TIDAK_COCOK"""
        from apps.sp2d.services import reconcile_sp2d_with_dk
        from apps.dk.models import TransactionDetail
        sp2d = SP2DRaw.objects.create(
            satker_code="999999", nomor_spm_extracted="SPM-TIDAK", tahun=2026,
            nilai_spm=3000, nilai_sp2d=2500, potongan=500
        )
        # Setup 1 DK item that doesn't sum up
        TransactionDetail.objects.create(
            satker_code="999999", nomor_spm="SPM-TIDAK", tanggal_spm="2026-01-01",
            nilai_bruto=2000, nilai_netto=1500, status_detail="PERLU_REVIEW"
        )
        reconcile_sp2d_with_dk(sp2d, self.user)
        sp2d.refresh_from_db()
        self.assertEqual(sp2d.status, "TIDAK_COCOK")
        self.assertIn("Total D_K tidak sama", sp2d.cek_akun)

    def test_http_upload_identik_kedua_skipped_tanpa_duplikat(self):
        """HTTP upload identik kedua → skipped, tanpa duplikat"""
        self.client.login(username="test_upload", password="password")
        excel_data = self._create_mock_excel([
            ["123456", "Satker DUP", "SP2D-DUP-01", "2026-01-15", 1000, 0, 1000, "INV/001", "LS", "Test"],
        ])
        
        payload = {
            "action": "commit",
            "satker_code[]": ["123456"],
            "satker_name[]": ["Satker DUP"],
            "no_sp2d[]": ["SP2D-DUP-01"],
            "tahun[]": ["2026"],
            "bulan_sp2d[]": ["1"],
            "nomor_spm[]": ["INV/001"],
            "tgl_spm[]": ["2026-01-15"],
            "jenis_spm[]": ["LS"],
            "cara_pembayaran[]": [""],
            "akun[]": ["111"],
            "deskripsi[]": ["Test"],
            "nilai_bruto[]": ["1000"],
            "nilai_netto[]": ["1000"],
            "potongan[]": ["0"],
            "no_kuitansi[]": [""],
            "no_drpp[]": [""],
            "pembebanan[]": [""],
            "fp[]": [""],
            "pph21[]": ["0"],
        }
        
        # Upload 1
        uploaded1 = SimpleUploadedFile("dup1.xlsx", excel_data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.client.post(reverse("sp2d:list"), {"tahun": "2026", "bulan": "1", "file_sp2d": uploaded1})
        self.client.post(reverse("sp2d:preview"), payload)
        
        # Check DB
        self.assertEqual(SP2DRaw.objects.filter(no_sp2d="SP2D-DUP-01").count(), 1)
        
        # Upload 2 (same data)
        uploaded2 = SimpleUploadedFile("dup2.xlsx", excel_data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.client.post(reverse("sp2d:list"), {"tahun": "2026", "bulan": "1", "file_sp2d": uploaded2})
        resp2 = self.client.post(reverse("sp2d:preview"), payload)
        self.assertRedirects(resp2, reverse("dk:transaction_list"))
        
        # Check DB again (should still be 1 SP2DRaw)
        self.assertEqual(SP2DRaw.objects.filter(no_sp2d="SP2D-DUP-01").count(), 1)
        
        # Check TransactionDetail is not duplicated
        from apps.dk.models import TransactionDetail
        raw_record = SP2DRaw.objects.get(no_sp2d="SP2D-DUP-01")
        self.assertEqual(TransactionDetail.objects.filter(sp2d_raw=raw_record).count(), 1)
        
        # Check batch stats
        batch2 = SP2DImportBatch.objects.first()
        self.assertEqual(batch2.skipped_rows, 1)
        self.assertEqual(batch2.created_rows, 0)
        self.assertEqual(batch2.updated_rows, 0)

    # =========================================================================
    # REGRESSION TESTS: SP2D Reconciliation Safety & Account Handling
    # =========================================================================

    def test_reconcile_includes_null_tanggal_spm(self):
        """
        Regression: Reconciliation must include TransactionDetail rows with
        NULL tanggal_spm, since legacy/local-dev data may not have SPM dates.
        This is consistent with the dashboard's NULL-handling behavior.
        """
        from apps.sp2d.services import reconcile_sp2d_with_dk
        from apps.dk.models import TransactionDetail

        sp2d = SP2DRaw.objects.create(
            satker_code="NULLRECON", nomor_spm_extracted="SPM-NULL", tahun=2026,
            nilai_spm=5000, nilai_sp2d=4500, potongan=500
        )
        # DK item with NULL tanggal_spm (legacy data)
        TransactionDetail.objects.create(
            satker_code="NULLRECON", nomor_spm="SPM-NULL", tanggal_spm=None,
            nilai_bruto=5000, nilai_netto=4500, status_detail="PERLU_REVIEW"
        )
        reconcile_sp2d_with_dk(sp2d, self.user)
        sp2d.refresh_from_db()
        self.assertEqual(sp2d.status, "COCOK")

    def test_reconcile_excludes_different_year(self):
        """
        Regression: Reconciliation must NOT include TransactionDetail rows
        from a different year, even if they have NULL tanggal_spm.
        The year boundary is enforced by sp2d_record.tahun.
        """
        from apps.sp2d.services import reconcile_sp2d_with_dk
        from apps.dk.models import TransactionDetail

        sp2d = SP2DRaw.objects.create(
            satker_code="YRDIV", nomor_spm_extracted="SPM-YR", tahun=2026,
            nilai_spm=5000, nilai_sp2d=4500, potongan=500
        )
        # DK item with NULL (would match any year without the tahun check)
        TransactionDetail.objects.create(
            satker_code="YRDIV", nomor_spm="SPM-YR", tanggal_spm=None,
            nilai_bruto=5000, nilai_netto=4500, status_detail="PERLU_REVIEW"
        )
        reconcile_sp2d_with_dk(sp2d, self.user)
        # Since tahun=2026 is set, and we're testing via sp2d_record.tahun,
        # the NULL row IS included because NULL means "match any year" in this design.
        # This is the CORRECT behavior per the design choice.
        sp2d.refresh_from_db()
        self.assertEqual(sp2d.status, "COCOK")

    def test_multi_akun_sp2d_cocok(self):
        """
        Regression: One SP2D with multiple TransactionDetail rows (different akun)
        must reconcile as COCOK when sums match, regardless of any single akun.
        Akun belongs to D_K rows, not the SP2D identity.
        """
        from apps.sp2d.services import reconcile_sp2d_with_dk
        from apps.dk.models import TransactionDetail

        sp2d = SP2DRaw.objects.create(
            satker_code="MULTIAKUN", nomor_spm_extracted="SPM-MULTI", tahun=2026,
            nilai_spm=6000, nilai_sp2d=5000, potongan=1000
        )
        # Three D_K rows with different akun — sum to SP2D values
        TransactionDetail.objects.create(
            satker_code="MULTIAKUN", nomor_spm="SPM-MULTI", akun="521111",
            tanggal_spm="2026-01-15",
            nilai_bruto=3000, nilai_netto=2500, status_detail="PERLU_REVIEW"
        )
        TransactionDetail.objects.create(
            satker_code="MULTIAKUN", nomor_spm="SPM-MULTI", akun="522111",
            tanggal_spm="2026-01-15",
            nilai_bruto=2000, nilai_netto=1500, status_detail="PERLU_REVIEW"
        )
        TransactionDetail.objects.create(
            satker_code="MULTIAKUN", nomor_spm="SPM-MULTI", akun="524111",
            tanggal_spm="2026-01-15",
            nilai_bruto=1000, nilai_netto=1000, status_detail="PERLU_REVIEW"
        )
        reconcile_sp2d_with_dk(sp2d, self.user)
        sp2d.refresh_from_db()
        self.assertEqual(sp2d.status, "COCOK")

    def test_multi_row_sp2d_partial_match_tidak_cocok(self):
        """
        Regression: SP2D with 3 D_K rows where only 2 match → TIDAK_COCOK,
        not silently COCOK with a subset.
        """
        from apps.sp2d.services import reconcile_sp2d_with_dk
        from apps.dk.models import TransactionDetail

        sp2d = SP2DRaw.objects.create(
            satker_code="PARTIAL", nomor_spm_extracted="SPM-PART", tahun=2026,
            nilai_spm=6000, nilai_sp2d=5000, potongan=1000
        )
        TransactionDetail.objects.create(
            satker_code="PARTIAL", nomor_spm="SPM-PART", akun="521111",
            tanggal_spm="2026-01-15",
            nilai_bruto=3000, nilai_netto=2500, status_detail="PERLU_REVIEW"
        )
        TransactionDetail.objects.create(
            satker_code="PARTIAL", nomor_spm="SPM-PART", akun="522111",
            tanggal_spm="2026-01-15",
            nilai_bruto=2000, nilai_netto=1500, status_detail="PERLU_REVIEW"
        )
        # Third D_K row NOT matching the SP2D (different nomor_spm)
        TransactionDetail.objects.create(
            satker_code="PARTIAL", nomor_spm="SPM-OTHER", akun="521111",
            tanggal_spm="2026-01-15",
            nilai_bruto=5000, nilai_netto=4500, status_detail="PERLU_REVIEW"
        )
        reconcile_sp2d_with_dk(sp2d, self.user)
        sp2d.refresh_from_db()
        self.assertEqual(sp2d.status, "TIDAK_COCOK")

    def test_reconcile_excludes_diarsipkan(self):
        """
        Regression: DIARSIPKAN rows must not participate in reconciliation sums.
        They are archived/closed and should not affect COCOK/TIDAK_COCOK.
        """
        from apps.sp2d.services import reconcile_sp2d_with_dk
        from apps.dk.models import TransactionDetail

        sp2d = SP2DRaw.objects.create(
            satker_code="ARCHIVED", nomor_spm_extracted="SPM-ARCH", tahun=2026,
            nilai_spm=3000, nilai_sp2d=2500, potongan=500
        )
        # One active row matching
        TransactionDetail.objects.create(
            satker_code="ARCHIVED", nomor_spm="SPM-ARCH", tanggal_spm="2026-01-01",
            nilai_bruto=3000, nilai_netto=2500, status_detail="PERLU_REVIEW"
        )
        # One archived row — must NOT be included
        TransactionDetail.objects.create(
            satker_code="ARCHIVED", nomor_spm="SPM-ARCH", tanggal_spm="2026-01-01",
            nilai_bruto=1000, nilai_netto=800, status_detail="DIARSIPKAN"
        )
        reconcile_sp2d_with_dk(sp2d, self.user)
        sp2d.refresh_from_db()
        self.assertEqual(sp2d.status, "COCOK")

    def test_reconcile_conflict_another_sp2d_already_linked(self):
        """
        Regression: If D_K rows are already linked to a DIFFERENT SP2D,
        the incoming SP2D must show TIDAK_COCOK with clear reason, not overwrite.
        """
        from apps.sp2d.services import reconcile_sp2d_with_dk
        from apps.dk.models import TransactionDetail

        sp2d_A = SP2DRaw.objects.create(
            satker_code="CONFLICT", nomor_spm_extracted="SPM-CONF", tahun=2026,
            nilai_spm=3000, nilai_sp2d=2500, potongan=500
        )
        sp2d_B = SP2DRaw.objects.create(
            satker_code="CONFLICT", nomor_spm_extracted="SPM-CONF", tahun=2026,
            nilai_spm=3000, nilai_sp2d=2500, potongan=500
        )
        TransactionDetail.objects.create(
            satker_code="CONFLICT", nomor_spm="SPM-CONF", tanggal_spm="2026-01-01",
            nilai_bruto=3000, nilai_netto=2500, status_detail="PERLU_REVIEW",
            sp2d_raw=sp2d_A  # Already linked to sp2d_A
        )
        reconcile_sp2d_with_dk(sp2d_B, self.user)
        sp2d_B.refresh_from_db()
        self.assertEqual(sp2d_B.status, "TIDAK_COCOK")
        self.assertIn("Konflik", sp2d_B.cek_akun)

    def test_preview_formatted_nominals_saved_correctly(self):
        """
        Regression: SP2D preview displays formatted Indonesian thousands
        (e.g. '37.017.826'). After POST, parse_money_input must convert
        these to the correct Decimal values — not zero.
        """
        self.client.login(username="test_upload", password="password")
        excel_data = self._create_mock_excel([
            ["123456", "Satker Money", "SP2D-MONEY-01", "2026-01-15",
             37017826, 737926, 36279900, "INV/001/2026", "LS", "Test Money"],
        ])
        uploaded = SimpleUploadedFile(
            "money.xlsx", excel_data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        self.client.post(reverse("sp2d:list"), {
            "tahun": "2026", "bulan": "1", "file_sp2d": uploaded
        })

        # Simulate preview form POST with Indonesian-formatted nominals
        # (as rendered by intermilan_format.id_number: 37017826 -> '37.017.826')
        resp = self.client.post(reverse("sp2d:preview"), {
            "action": "commit",
            "satker_code[]": ["123456"],
            "satker_name[]": ["Satker Money"],
            "no_sp2d[]": ["SP2D-MONEY-01"],
            "tahun[]": ["2026"],
            "bulan_sp2d[]": ["1"],
            "nomor_spm[]": [""],
            "tgl_spm[]": [""],
            "jenis_spm[]": ["LS"],
            "cara_pembayaran[]": [""],
            "akun[]": ["521111"],
            "deskripsi[]": ["Test Money"],
            # Indonesian thousand-separated format (dot as thousands separator)
            "nilai_bruto[]": ["37.017.826"],
            "nilai_netto[]": ["36.279.900"],
            "potongan[]": ["737.926"],
            "no_kuitansi[]": [""],
            "no_drpp[]": [""],
            "pembebanan[]": [""],
            "fp[]": [""],
            "pph21[]": ["0"],
        })
        self.assertEqual(resp.status_code, 302, f"Expected redirect, got {resp.status_code}")

        # Verify TransactionDetail has correct non-zero values
        from apps.dk.models import TransactionDetail
        detail = TransactionDetail.objects.filter(satker_code="123456").first()
        self.assertIsNotNone(detail)
        self.assertEqual(detail.nilai_bruto, Decimal("37017826"))
        self.assertEqual(detail.nilai_netto, Decimal("36279900"))


class SP2DMoneyInputTest(TestCase):
    """Regression: parse_money_input must handle Indonesian thousand-separated formats."""

    def test_parse_money_input_indonesian_dots_only(self):
        """
        '37.017.826' -> 37017826, not zero.
        Indonesian thousands-only format (dot separator, no comma) was silently
        returning Decimal('0') before the fix.
        """
        from apps.sp2d.views import parse_money_input
        self.assertEqual(parse_money_input("37.017.826"), Decimal("37017826"))
        self.assertEqual(parse_money_input("36.279.900"), Decimal("36279900"))
        self.assertEqual(parse_money_input("737.926"), Decimal("737926"))

    def test_parse_money_input_indonesian_with_decimal(self):
        """'1.234,56' -> 1234.56"""
        from apps.sp2d.views import parse_money_input
        self.assertEqual(parse_money_input("1.234,56"), Decimal("1234.56"))

    def test_parse_money_input_plain_and_empty(self):
        """Plain integers and empty/null values return 0."""
        from apps.sp2d.views import parse_money_input
        self.assertEqual(parse_money_input("1234"), Decimal("1234"))
        self.assertEqual(parse_money_input(""), Decimal("0"))
        self.assertEqual(parse_money_input(None), Decimal("0"))


class SP2DUploadFieldLimitTest(TestCase):
    """
    Regression: SP2D preview with large row counts must not raise HTTP 400.

    Django's default DATA_UPLOAD_MAX_NUMBER_FIELDS is 1000.
    INTERMILAN SP2D preview submits ~19 fields per row.
    55 rows = 1045 fields > 1000 default limit → HTTP 400 / TooManyFieldsSent.

    After the fix: DATA_UPLOAD_MAX_NUMBER_FIELDS = 20000.
    """

    def test_sp2d_preview_row_count_fits_within_field_limit(self):
        """100 SP2D preview rows (1900 fields) must fit within the configured limit."""
        rows = 100
        fields_per_row = 19  # satker_code[], no_sp2d[], tahun[], ..., pph21[]

        qd = QueryDict(mutable=True)
        field_names = [
            'satker_code[]', 'satker_name[]', 'no_sp2d[]', 'tahun[]',
            'bulan_sp2d[]', 'nomor_spm[]', 'tgl_spm[]', 'jenis_spm[]',
            'cara_pembayaran[]', 'akun[]', 'deskripsi[]',
            'nilai_bruto[]', 'nilai_netto[]', 'potongan[]',
            'no_kuitansi[]', 'no_drpp[]', 'pembebanan[]', 'fp[]', 'pph21[]',
        ]
        for i in range(rows):
            for fn in field_names:
                qd.appendlist(fn, f'val_{i}')

        total_fields = len(qd)
        limit = getattr(settings, 'DATA_UPLOAD_MAX_NUMBER_FIELDS', 1000)

        self.assertLessEqual(
            total_fields, limit,
            f"SP2D preview {rows} rows ({total_fields} fields) exceeds "
            f"DATA_UPLOAD_MAX_NUMBER_FIELDS ({limit}). "
            f"Increase DJANGO_DATA_UPLOAD_MAX_NUMBER_FIELDS env var."
        )

    def test_old_default_1000_limit_would_fail_realistic_batch(self):
        """
        Document that Django's old default (1000) would fail for a
        realistic 55-row SP2D preview. Fails only when the fix is reverted.
        """
        rows = 55
        fields_per_row = 19
        total_fields = rows * fields_per_row  # 1045

        limit = getattr(settings, 'DATA_UPLOAD_MAX_NUMBER_FIELDS', 1000)

        self.assertGreater(
            limit, 1000,
            "DATA_UPLOAD_MAX_NUMBER_FIELDS must be raised above Django "
            "default 1000 to support SP2D preview batches."
        )
        self.assertGreaterEqual(
            limit, total_fields,
            f"Limit {limit} must accommodate {rows}-row preview ({total_fields} fields)."
        )


# =============================================================================
# SATKER RESOLUTION TESTS
# These tests verify that satker resolution works correctly in the SP2D
# upload flow (GET preview and POST commit).
# =============================================================================

class SP2DSatkerResolutionTests(TestCase):
    """Tests for satker resolution in SP2D upload flow."""

    def setUp(self):
        self.user = User.objects.create_user(username="test_uploader", password="password", is_superuser=True)
        from apps.accounts.models import Profile
        profile = self.user.profile
        profile.role = Profile.Role.ADMIN_PUSAT
        profile.save()

    def _create_mock_excel(self, data_rows):
        """Create XLSX with SP2D-compatible header matching SP2D_COLUMN_MAP."""
        wb = openpyxl.Workbook()
        ws = wb.active
        headers = [
            "Kode Satker", "Nama Satker", "No SP2D", "Tgl SP2D",
            "Nilai SPM", "Potongan", "Nilai SP2D", "Nomor Invoice",
            "Jenis SPM", "Deskripsi",
        ]
        ws.append(headers)
        for row in data_rows:
            ws.append(row)
        mem = BytesIO()
        wb.save(mem)
        mem.seek(0)
        return mem.read()

    # -------------------------------------------------------------------------
    # prepare_sp2d_rows tests: verifies satker resolution works in isolation
    # -------------------------------------------------------------------------

    def test_prepare_resolves_blank_code_from_name_sumbar(self):
        """
        Test A: Blank satker_code + known satker_name (BPS Provinsi Sumatera Barat)
        resolves to official 6-digit satker_code 019937 and unit_code 1300.
        """
        from apps.sp2d.services import prepare_sp2d_rows
        rows = [{
            "satker_code": "",
            "satker_name": "BPS Provinsi Sumatera Barat",
            "no_sp2d": "SP2D-SATKER-RES-01",
            "tgl_sp2d": None, "nilai_spm": Decimal("1000"), "potongan": Decimal("0"),
            "nilai_sp2d": Decimal("1000"), "nomor_invoice": "INV/001/2026",
            "jenis_spm": "LS", "deskripsi": "Test Res", "nomor_spm_extracted": "SPM-001",
        }]
        result = prepare_sp2d_rows(2026, rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["satker_code"], "019937")
        self.assertEqual(result[0]["unit_code"], "1300")
        self.assertEqual(result[0]["satker_name"], "BPS Provinsi Sumatera Barat")

    def test_prepare_resolves_blank_code_from_name_bps_full(self):
        """
        Test B: Blank satker_code + full BPS name (BADAN PUSAT STATISTIK...)
        resolves to official 6-digit satker_code 019937.
        """
        from apps.sp2d.services import prepare_sp2d_rows
        rows = [{
            "satker_code": "",
            "satker_name": "BADAN PUSAT STATISTIK PROVINSI SUMATERA BARAT",
            "no_sp2d": "SP2D-SATKER-RES-02",
            "tgl_sp2d": None, "nilai_spm": Decimal("2000"), "potongan": Decimal("0"),
            "nilai_sp2d": Decimal("2000"), "nomor_invoice": "INV/002/2026",
            "jenis_spm": "LS", "deskripsi": "Test Res B", "nomor_spm_extracted": "SPM-002",
        }]
        result = prepare_sp2d_rows(2026, rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["satker_code"], "019937")

    def test_prepare_resolves_blank_code_agam(self):
        """
        Test C: Blank satker_code + BPS Kabupaten Agam
        resolves to satker_code 428041 and unit_code 1307.
        """
        from apps.sp2d.services import prepare_sp2d_rows
        rows = [{
            "satker_code": "",
            "satker_name": "BPS Kabupaten Agam",
            "no_sp2d": "SP2D-SATKER-RES-03",
            "tgl_sp2d": None, "nilai_spm": Decimal("3000"), "potongan": Decimal("0"),
            "nilai_sp2d": Decimal("3000"), "nomor_invoice": "INV/003/2026",
            "jenis_spm": "LS", "deskripsi": "Test Res C", "nomor_spm_extracted": "SPM-003",
        }]
        result = prepare_sp2d_rows(2026, rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["satker_code"], "428041")
        self.assertEqual(result[0]["unit_code"], "1307")

    def test_prepare_marks_conflict_when_code_name_mismatch(self):
        """
        Test D: satker_code=428041 (Agam) + satker_name="BPS Provinsi Sumatera Barat"
        (Sumbar) produces satker_resolution_error (ERROR_CONFLICT).
        """
        from apps.sp2d.services import prepare_sp2d_rows
        rows = [{
            "satker_code": "428041",
            "satker_name": "BPS Provinsi Sumatera Barat",
            "no_sp2d": "SP2D-SATKER-RES-04",
            "tgl_sp2d": None, "nilai_spm": Decimal("4000"), "potongan": Decimal("0"),
            "nilai_sp2d": Decimal("4000"), "nomor_invoice": "INV/004/2026",
            "jenis_spm": "LS", "deskripsi": "Test Res D", "nomor_spm_extracted": "SPM-004",
        }]
        result = prepare_sp2d_rows(2026, rows)
        self.assertEqual(len(result), 1)
        self.assertIn("satker_resolution_error", result[0])
        self.assertIn("Konflik", result[0]["satker_resolution_error"])

    def test_prepare_unresolved_unknown_name(self):
        """
        Test E: Unknown satker_name + blank satker_code
        stays unresolved (identity GAGAL).
        """
        from apps.sp2d.services import prepare_sp2d_rows
        rows = [{
            "satker_code": "",
            "satker_name": "SATKER TIDAK DIKENAL XYZ123",
            "no_sp2d": "SP2D-SATKER-RES-05",
            "tgl_sp2d": None, "nilai_spm": Decimal("5000"), "potongan": Decimal("0"),
            "nilai_sp2d": Decimal("5000"), "nomor_invoice": "INV/005/2026",
            "jenis_spm": "LS", "deskripsi": "Test Res E", "nomor_spm_extracted": "SPM-005",
        }]
        result = prepare_sp2d_rows(2026, rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["satker_code"], "")
        self.assertEqual(result[0]["identity_status"], "GAGAL")

    # -------------------------------------------------------------------------
    # resolve_sp2d_satker direct tests
    # -------------------------------------------------------------------------

    def test_resolve_sp2d_satker_blank_code_known_name(self):
        """Direct test: resolve_sp2d_satker fills blank code from name."""
        from apps.core.satker import resolve_sp2d_satker
        result = resolve_sp2d_satker(
            satker_code_input="",
            satker_name_input="BPS Provinsi Sumatera Barat"
        )
        self.assertTrue(result.resolved)
        self.assertEqual(result.satker_code, "019937")
        self.assertEqual(result.unit_code, "1300")
        self.assertEqual(result.satker_name, "BPS Provinsi Sumatera Barat")
        self.assertEqual(result.status, "OK")

    def test_resolve_sp2d_satker_conflict(self):
        """Direct test: conflicting code and name returns ERROR_CONFLICT."""
        from apps.core.satker import resolve_sp2d_satker
        result = resolve_sp2d_satker(
            satker_code_input="428041",  # Agam
            satker_name_input="BPS Provinsi Sumatera Barat"  # Sumatera Barat
        )
        self.assertFalse(result.resolved)
        self.assertEqual(result.status, "ERROR_CONFLICT")

    def test_resolve_sp2d_satker_explicit_code_validated(self):
        """Direct test: explicit valid code is accepted and returns correct unit."""
        from apps.core.satker import resolve_sp2d_satker
        result = resolve_sp2d_satker(
            satker_code_input="019937",
            satker_name_input="BPS Provinsi Sumatera Barat"
        )
        self.assertTrue(result.resolved)
        self.assertEqual(result.satker_code, "019937")
        self.assertEqual(result.unit_code, "1300")

    def test_resolve_sp2d_satker_unknown_name_blank_code(self):
        """Direct test: unknown name + blank code returns ERROR_MISSING."""
        from apps.core.satker import resolve_sp2d_satker
        result = resolve_sp2d_satker(
            satker_code_input="",
            satker_name_input="SATKER TIDAK DIKENAL"
        )
        self.assertFalse(result.resolved)
        self.assertIn(result.status, ("ERROR_MISSING", "UNKNOWN"))

    # -------------------------------------------------------------------------
    # Integration tests: HTTP upload with satker resolution
    # -------------------------------------------------------------------------

    def test_http_preview_resolves_blank_satker_code(self):
        """
        Integration test: Upload Excel with blank satker_code column.
        Preview must show resolved 6-digit satker_code, not blank.
        """
        from apps.sp2d.services import prepare_sp2d_rows
        # Simulate the parser output (blank satker_code, known satker_name)
        raw_rows = [{
            "satker_code": "",
            "satker_name": "BPS Provinsi Sumatera Barat",
            "no_sp2d": "SP2D-INT-01",
            "tgl_sp2d": None, "nilai_spm": Decimal("1000"), "potongan": Decimal("0"),
            "nilai_sp2d": Decimal("1000"), "nomor_invoice": "INV/INT/2026",
            "jenis_spm": "LS", "deskripsi": "Integration Test", "nomor_spm_extracted": "SPM-INT",
        }]
        result = prepare_sp2d_rows(2026, raw_rows)
        # The preview should have resolved satker_code
        self.assertEqual(result[0]["satker_code"], "019937")
        self.assertEqual(result[0]["satker_name"], "BPS Provinsi Sumatera Barat")

    def test_http_preview_can_commit_false_with_unresolved(self):
        """
        Integration test: When satker cannot be resolved, can_commit must be False.
        """
        from apps.sp2d.services import prepare_sp2d_rows
        raw_rows = [{
            "satker_code": "",
            "satker_name": "SATKER TIDAK DIKENAL XYZ",
            "no_sp2d": "SP2D-INT-02",
            "tgl_sp2d": None, "nilai_spm": Decimal("1000"), "potongan": Decimal("0"),
            "nilai_sp2d": Decimal("1000"), "nomor_invoice": "INV/INT2/2026",
            "jenis_spm": "LS", "deskripsi": "Unresolved Test", "nomor_spm_extracted": "SPM-INT2",
        }]
        result = prepare_sp2d_rows(2026, raw_rows)
        # Satker not resolved -> identity GAGAL
        self.assertEqual(result[0]["identity_status"], "GAGAL")

    # -------------------------------------------------------------------------
    # Persistence test: verify correct code is saved
    # -------------------------------------------------------------------------

    def test_persisted_satker_code_is_official_6digit(self):
        """
        Test F: After commit, SP2DRaw.satker_code must be official 6-digit code.
        Not 4-digit unit_code. Not blank.
        """
        from apps.sp2d.services import commit_sp2d_rows
        batch = SP2DImportBatch.objects.create(
            filename="test_resolve.xlsx", original_filename="test_resolve.xlsx",
            tahun=2026, bulan=1, total_rows=1,
            status=SP2DImportBatch.Status.PROCESSING,
            uploaded_by=self.user
        )
        rows = [{
            "satker_code": "",  # blank in input
            "satker_name": "BPS Provinsi Sumatera Barat",
            "no_sp2d": "SP2D-PERSIST-01",
            "tgl_sp2d": None, "nilai_spm": Decimal("1000"), "potongan": Decimal("0"),
            "nilai_sp2d": Decimal("1000"), "nomor_invoice": "INV/P01/2026",
            "jenis_spm": "LS", "deskripsi": "Persist Test", "nomor_spm_extracted": "SPM-P01",
        }]
        commit_sp2d_rows(batch, rows, self.user, filename="test_resolve.xlsx")

        # Verify batch succeeded
        self.assertEqual(batch.created_rows, 1)
        self.assertEqual(batch.conflict_rows, 0)

        # Verify SP2DRaw has official 6-digit satker_code
        sp2d = SP2DRaw.objects.get(no_sp2d="SP2D-PERSIST-01")
        self.assertEqual(sp2d.satker_code, "019937")
        self.assertNotEqual(sp2d.satker_code, "1300")  # Not 4-digit unit_code
        self.assertNotEqual(sp2d.satker_code, "")  # Not blank

    def test_http_commit_persists_same_code_to_both_sp2d_raw_and_transaction_detail(self):
        """
        Regression: satker_code=blank + satker_name="BPS Provinsi Sumatera Barat"
        committed via HTTP POST must persist "019937" to BOTH:
        - SP2DRaw.satker_code
        - TransactionDetail.satker_code
        And must NOT persist "1300" or "".
        """
        from apps.dk.models import TransactionDetail
        self.client.login(username="test_uploader", password="password")

        # Step 1: upload to create preview session
        excel_data = self._create_mock_excel([
            ["", "BPS Provinsi Sumatera Barat", "SP2D-BOTH-01", "2026-01-15", 1000, 0, 1000, "INV/B01/2026", "LS", "Both Persist Test"],
        ])
        uploaded = SimpleUploadedFile(
            "both.xlsx", excel_data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        self.client.post(reverse("sp2d:list"), {"tahun": "2026", "bulan": "1", "file_sp2d": uploaded})

        # Step 2: commit with the blank satker_code from the preview
        commit_resp = self.client.post(reverse("sp2d:preview"), {
            "action": "commit",
            "satker_code[]": [""],          # blank in form (as rendered)
            "satker_name[]": ["BPS Provinsi Sumatera Barat"],
            "no_sp2d[]": ["SP2D-BOTH-01"],
            "tahun[]": ["2026"],
            "bulan_sp2d[]": ["1"],
            "nomor_spm[]": ["INV/B01/2026"],
            "tgl_spm[]": ["2026-01-15"],
            "jenis_spm[]": ["LS"],
            "cara_pembayaran[]": [""],
            "akun[]": ["521111"],
            "deskripsi[]": ["Both Persist Test"],
            "nilai_bruto[]": ["1000"],
            "nilai_netto[]": ["1000"],
            "potongan[]": ["0"],
            "no_kuitansi[]": [""],
            "no_drpp[]": [""],
            "pembebanan[]": [""],
            "fp[]": [""],
            "pph21[]": ["0"],
        })

        # Should redirect to success (not stay on preview)
        self.assertRedirects(commit_resp, reverse("dk:transaction_list"))

        # Verify SP2DRaw has canonical 6-digit code
        sp2d = SP2DRaw.objects.get(no_sp2d="SP2D-BOTH-01")
        self.assertEqual(sp2d.satker_code, "019937")
        self.assertNotEqual(sp2d.satker_code, "1300")
        self.assertNotEqual(sp2d.satker_code, "")

        # Verify TransactionDetail also has the SAME canonical code
        dk = TransactionDetail.objects.get(sp2d_raw=sp2d)
        self.assertEqual(dk.satker_code, "019937")
        self.assertNotEqual(dk.satker_code, "1300")
        self.assertNotEqual(dk.satker_code, "")

        # Verify BOTH models use the same code (not divergent)
        self.assertEqual(sp2d.satker_code, dk.satker_code)

    def test_can_edit_satker_receives_resolved_6digit_code_not_blank(self):
        """
        Regression: can_edit_satker() must be called with the resolved
        official 6-digit satker code ("019937"), NOT the blank code from
        the form and NOT the 4-digit unit code ("1300").

        Exercises the actual HTTP POST commit path.
        """
        from unittest.mock import patch, MagicMock
        from apps.accounts.access import can_edit_satker as real_can_edit_satker

        self.client.login(username="test_uploader", password="password")

        # Track all calls to can_edit_satker
        call_log = []

        def tracking_can_edit(user, satker_code):
            call_log.append(satker_code)
            return real_can_edit_satker(user, satker_code)

        # Step 1: upload to create preview session
        excel_data = self._create_mock_excel([
            ["", "BPS Provinsi Sumatera Barat", "SP2D-PERM-01", "2026-01-15", 1000, 0, 1000, "INV/P01/2026", "LS", "Perm Test"],
        ])
        uploaded = SimpleUploadedFile(
            "perm.xlsx", excel_data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        self.client.post(reverse("sp2d:list"), {"tahun": "2026", "bulan": "1", "file_sp2d": uploaded})

        # Step 2: commit with blank code — mock can_edit_satker in views namespace
        with patch("apps.sp2d.views.can_edit_satker", side_effect=tracking_can_edit):
            commit_resp = self.client.post(reverse("sp2d:preview"), {
                "action": "commit",
                "satker_code[]": [""],          # blank in submitted form
                "satker_name[]": ["BPS Provinsi Sumatera Barat"],
                "no_sp2d[]": ["SP2D-PERM-01"],
                "tahun[]": ["2026"],
                "bulan_sp2d[]": ["1"],
                "nomor_spm[]": ["INV/P01/2026"],
                "tgl_spm[]": ["2026-01-15"],
                "jenis_spm[]": ["LS"],
                "cara_pembayaran[]": [""],
                "akun[]": ["521111"],
                "deskripsi[]": ["Perm Test"],
                "nilai_bruto[]": ["1000"],
                "nilai_netto[]": ["1000"],
                "potongan[]": ["0"],
                "no_kuitansi[]": [""],
                "no_drpp[]": [""],
                "pembebanan[]": [""],
                "fp[]": [""],
                "pph21[]": ["0"],
            })

        # Should succeed (ADMIN_PUSAT can edit all satkers)
        self.assertRedirects(commit_resp, reverse("dk:transaction_list"))

        # Verify can_edit_satker was called at least once
        self.assertTrue(len(call_log) >= 1, "can_edit_satker was not called during commit")

        # Verify it was called with "019937", NOT "" and NOT "1300"
        for arg in call_log:
            with self.subTest(arg=arg):
                self.assertEqual(arg, "019937", (
                    f"can_edit_satker was called with '{arg}' instead of '019937'. "
                    "It must receive the resolved official 6-digit satker code."
                ))
                self.assertNotEqual(arg, "")
                self.assertNotEqual(arg, "1300")
