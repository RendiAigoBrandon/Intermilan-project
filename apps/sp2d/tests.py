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
