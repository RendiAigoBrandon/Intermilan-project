"""
Tests for prepare_presentation_db management command.

Tests:
1. Successful execute clears operational data
2. Structural data remains after execute
3. Simulated deletion failure rolls back the entire reset
4. Dry-run shows correct counts
"""

from io import StringIO
from unittest.mock import patch, MagicMock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.core.models import SatkerMaster
from apps.dk.models import MasterAkun, TransactionDetail


class PreparePresentationDBTests(TestCase):
    """Test prepare_presentation_db command atomic behavior."""

    def setUp(self):
        """Create minimal test data: structural + operational."""
        # Structural data (should be preserved)
        self.satker = SatkerMaster.objects.create(
            unit_code="1300",
            satker_code="019937",
            nama_satker="BPS Provinsi Sumatera Barat",
        )
        self.master_akun = MasterAkun.objects.create(
            kode="521111",
            nama_akun="Belanja Barang Operasional",
        )

        # Operational data (should be cleared)
        self.tx = TransactionDetail.objects.create(
            satker_code="019937",
            nomor_spm="00001T",
            tanggal_spm="2026-01-15",
            akun="521111",
            bulan_sp2d=1,
            jenis_spm="GUP",
            nilai_bruto=1000000,
            nilai_netto=900000,
        )

    def test_dry_run_shows_correct_counts(self):
        """Dry-run should show what would be cleared without making changes."""
        out = StringIO()
        call_command("prepare_presentation_db", "--dry-run", stdout=out)
        output = out.getvalue()

        # Should show structural data preserved
        self.assertIn("SatkerMaster: 1 rows", output)
        self.assertIn("MasterAkun: 1 rows", output)

        # Should show operational data that would be cleared
        self.assertIn("TransactionDetail", output)

        # Structural data should still exist after dry-run
        self.assertEqual(SatkerMaster.objects.count(), 1)
        self.assertEqual(MasterAkun.objects.count(), 1)
        self.assertEqual(TransactionDetail.objects.count(), 1)

    def test_successful_execute_clears_operational_data(self):
        """Execute should clear all operational data."""
        out = StringIO()
        call_command("prepare_presentation_db", "--execute", stdout=out)
        output = out.getvalue()

        # Should show atomic commitment
        self.assertIn("atomic", output.lower())
        self.assertIn("committed atomically", output.lower())

        # Operational data should be cleared
        self.assertEqual(TransactionDetail.objects.count(), 0)

        # Verification should show zero remaining
        self.assertIn("All operational data cleared successfully", output)

    def test_structural_data_preserved_after_execute(self):
        """Execute should preserve all structural data."""
        call_command("prepare_presentation_db", "--execute")

        # Structural data should be preserved
        self.assertEqual(SatkerMaster.objects.count(), 1)
        self.assertEqual(MasterAkun.objects.count(), 1)

    def test_execute_succeeds_without_error(self):
        """Execute should complete without raising exception on success."""
        out = StringIO()
        # Should not raise
        call_command("prepare_presentation_db", "--execute", stdout=out)
        # Verify data was cleared
        self.assertEqual(TransactionDetail.objects.count(), 0)

    def test_execute_raises_command_error_on_failure(self):
        """Execute should raise CommandError if any deletion fails."""
        # Patch the all() method to return a mock queryset whose delete fails
        mock_qs = MagicMock()
        mock_qs.all.return_value = mock_qs
        mock_qs.delete.side_effect = RuntimeError("Simulated deletion failure")

        with patch.object(TransactionDetail.objects, "all", return_value=mock_qs):
            with self.assertRaises(CommandError) as ctx:
                call_command("prepare_presentation_db", "--execute")

        # Should raise CommandError about atomic failure
        self.assertIn("Atomic reset failed", str(ctx.exception))
        self.assertIn("Simulated deletion failure", str(ctx.exception))

    def test_failure_rolls_back_all_changes(self):
        """If any deletion fails, ALL operational data should be unchanged."""
        # Count before
        tx_count_before = TransactionDetail.objects.count()

        # Simulate failure during deletion
        mock_qs = MagicMock()
        mock_qs.all.return_value = mock_qs
        mock_qs.delete.side_effect = ValueError("Intentional test failure")

        with patch.object(TransactionDetail.objects, "all", return_value=mock_qs):
            with self.assertRaises(CommandError):
                call_command("prepare_presentation_db", "--execute")

        # ALL operational data should be unchanged (not partially deleted)
        self.assertEqual(TransactionDetail.objects.count(), tx_count_before)

    def test_already_clean_database_returns_early(self):
        """If database is already clean, command should return without error."""
        # Clear operational data first
        TransactionDetail.objects.all().delete()

        out = StringIO()
        # Should not raise
        call_command("prepare_presentation_db", "--execute", stdout=out)

        # Should show already clean message
        output = out.getvalue()
        self.assertIn("already clean", output)
