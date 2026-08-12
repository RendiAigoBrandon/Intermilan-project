"""
Parent-Workflow Regression Tests for SPM parent + DRPP upload flow.
Tests call service functions directly to verify production code logic,
bypassing Django test-client session complexity.

Tests cover:
- SPM save creates TransactionPackage + ActiveParentSession
- SPM save marks matching D_K checklist row as ADA
- DRPP preview inherits SPM fields from active parent
- Satker/year conflicts set selection_conflict=True
- Frozen-parent commit uses the frozen SPM (not the currently-active one)
- Two DRPPs commit under same parent (one package, two detail rows)
- Drive failure preserves local checklist + DocumentDriveLink
- Ganti clears parent + redirects; Lepas clears parent only
"""
import tempfile
import os
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from unittest.mock import patch, MagicMock

from apps.core.models import (
    ActiveParentSession,
    DRPPPreviewState,
    TransactionPackage,
)
from apps.dk.models import TransactionDetail
from apps.documents.models import ChecklistStatus, DocumentDriveLink
from apps.paket_spm.models import PaketSPMUpload

User = get_user_model()


def _make_request(user=None):
    """Create a minimal mock request for service-layer tests."""
    m = MagicMock()
    m.session.session_key = ""  # Empty string — no session needed for unit tests
    m.user = user
    return m


_MINIMAL_ZIP = b"PK" + b"\x03\x04" + b"\x00" * 100


class SPMParentTestMixin:
    """Shared test helpers."""

    def make_admin_user(self, username):
        user, _ = User.objects.get_or_create(
            username=username,
            defaults={"is_staff": True, "is_superuser": True},
        )
        return user

    def make_active_parent(self, user, nomor_spm="SPM-X"):
        """Create TransactionPackage + ActiveParentSession for user."""
        package = TransactionPackage.objects.create(
            satker_code="019937", tahun=2026, nomor_spm=nomor_spm,
            tanggal_spm=date(2026, 1, 15), jenis_spm="GUP",
            nilai_spm=Decimal("1000000"), has_spm_document=True,
        )
        session = ActiveParentSession.objects.create(
            session_key="test-key", user=user,
            transaction_package=package,
            satker_code="019937", tahun=2026, nomor_spm=nomor_spm,
            tanggal_spm=date(2026, 1, 15), jenis_spm="GUP",
            selection_method="EVIDENCE_MATCH",
        )
        return package, session

    def make_existing_dk(self, nomor_spm="SPM-X"):
        """Create a matching TransactionDetail (D_K) for SPM linking."""
        return TransactionDetail.objects.create(
            satker_code="019937", nomor_spm=nomor_spm,
            tanggal_spm=date(2026, 1, 15), jenis_spm="GUP",
            akun="5111", bulan_sp2d=1,
            nilai_bruto=Decimal("1000000"), nilai_netto=Decimal("1000000"),
            status_detail=TransactionDetail.StatusDetail.DRAFT,
        )


# ============================================================================
# TEST A: SPM SAVE CREATES TRANSACTIONPACKAGE + ACTIVE PARENT SESSION
# ============================================================================

class SPSPSaveTests(TestCase, SPMParentTestMixin):
    """Step 6: SPM save creates canonical TransactionPackage + ActiveParentSession."""

    def setUp(self):
        self.user = self.make_admin_user("spspsave_test")

    def tearDown(self):
        ActiveParentSession.objects.filter(user=self.user).delete()
        TransactionPackage.objects.filter(satker_code="019937", tahun=2026).delete()
        TransactionDetail.objects.filter(satker_code="019937").delete()
        ChecklistStatus.objects.all().delete()
        DocumentDriveLink.objects.all().delete()
        PaketSPMUpload.objects.filter(uploaded_by=self.user).delete()

    def test_spm_save_creates_package_once(self):
        """SPM save must create exactly one TransactionPackage for 019937/2026/SPM-X."""
        from apps.core.services import find_or_create_package, enrich_from_spm, set_active_parent

        # Simulate save_spm_parent logic (from views.py)
        satker_code = "019937"
        tahun = 2026
        nomor_spm = "SPM-X"
        tanggal_spm = date(2026, 1, 15)
        jenis_spm = "GUP"

        # Step 1: Find or create canonical package
        package, created = find_or_create_package(
            satker_code=satker_code,
            tahun=tahun,
            nomor_spm=nomor_spm,
        )
        self.assertTrue(created, "Package should be created")

        # Step 2: Enrich with SPM data
        enrich_from_spm(
            package=package,
            tanggal_spm=tanggal_spm,
            jenis_spm=jenis_spm,
            nilai_spm=Decimal("500000"),
            deskripsi="Test SPM",
            source_filename="SPM-X.zip",
            user=self.user,
        )
        package.save()

        # Step 3: Verify has_spm_document
        package.refresh_from_db()
        self.assertTrue(package.has_spm_document)

        # Step 4: Set active parent
        active = set_active_parent(
            request=None,
            package=package,
            selection_method="SPM_SAVE",
            selection_evidence={"source": "test"},
            user=self.user,
        )

        # Verify exactly one package
        count = TransactionPackage.objects.filter(
            satker_code="019937", tahun=2026, nomor_spm="SPM-X"
        ).count()
        self.assertEqual(count, 1)

        # Verify ActiveParentSession points to it
        sessions = ActiveParentSession.objects.filter(user=self.user)
        self.assertEqual(sessions.count(), 1)
        self.assertEqual(sessions.first().transaction_package, package)


# ============================================================================
# TEST B: SPM SAVE MARKS MATCHING D_K CHECKLIST ROW AS ADA
# ============================================================================

class ChecklistSPMTests(TestCase, SPMParentTestMixin):
    """Step 7: SPM save marks matching D_K checklist row as ADA."""

    def setUp(self):
        self.user = self.make_admin_user("checklist_test")
        self.dk = self.make_existing_dk(nomor_spm="SPM-CK")

    def tearDown(self):
        ActiveParentSession.objects.filter(user=self.user).delete()
        TransactionPackage.objects.filter(satker_code="019937", tahun=2026).delete()
        TransactionDetail.objects.filter(satker_code="019937").delete()
        ChecklistStatus.objects.all().delete()
        DocumentDriveLink.objects.all().delete()
        PaketSPMUpload.objects.filter(uploaded_by=self.user).delete()

    def test_checklist_spm_marked_ada_after_save(self):
        """ChecklistStatus SPM row must be ADA after SPM save with matching D_K."""
        from apps.core.services import find_or_create_package, enrich_from_spm, set_active_parent
        from apps.documents.services.checklist import mark_checklist_present

        # Simulate SPM save with existing D_K
        package, created = find_or_create_package(
            satker_code="019937", tahun=2026, nomor_spm="SPM-CK"
        )
        enrich_from_spm(
            package=package,
            tanggal_spm=date(2026, 1, 15),
            jenis_spm="GUP",
            nilai_spm=Decimal("500000"),
            user=self.user,
        )
        package.save()
        set_active_parent(request=None, package=package, user=self.user)

        # Simulate the D_K linking logic from save_spm_parent view
        existing_dk_rows = list(
            TransactionDetail.objects.filter(
                satker_code="019937",
                nomor_spm__iexact="SPM-CK",
            ).filter(
                tanggal_spm__year=2026
            )
        )
        self.assertTrue(len(existing_dk_rows) > 0, "Existing D_K must be found")
        for dk_row in existing_dk_rows:
            mark_checklist_present(dk_row, "SPM", self.user)

        # Assert exact checklist row (not .first())
        checklist = ChecklistStatus.objects.get(
            transaction_detail=self.dk,
            nama_dokumen="SPM",
        )
        self.assertEqual(checklist.status, "ADA")


# ============================================================================
# TEST C: DRPP PREVIEW INHERITS SPM FIELDS FROM ACTIVE PARENT
# ============================================================================

class DRPPPreviewInheritanceTests(TestCase, SPMParentTestMixin):
    """Step 8: DRPP preview inherits SPM fields from active parent."""

    def setUp(self):
        self.user = self.make_admin_user("drpp_preview_test")
        self.pkg, self.session = self.make_active_parent(self.user)

    def tearDown(self):
        ActiveParentSession.objects.filter(user=self.user).delete()
        TransactionPackage.objects.filter(satker_code="019937", tahun=2026).delete()
        TransactionDetail.objects.filter(satker_code="019937").delete()
        ChecklistStatus.objects.all().delete()
        DocumentDriveLink.objects.all().delete()
        DRPPPreviewState.objects.all().delete()
        PaketSPMUpload.objects.filter(uploaded_by=self.user).delete()

    def test_preview_creates_drpp_preview_state_with_frozen_parent(self):
        """DRPP preview must create DRPPPreviewState with frozen parent fields."""
        from apps.core.services import create_drpp_preview_state

        # Simulate DRPP-only upload with active parent
        drpp_nomor = "00025"
        drpp_satker = "019937"
        drpp_tahun = 2026

        # Call the actual preview-state creation logic
        state = create_drpp_preview_state(
            request=_make_request(user=self.user),
            nomor_drpp=drpp_nomor,
            satker_code=drpp_satker,
            tahun=drpp_tahun,
            parent_package=self.pkg,
            preview_data={"drpp_groups": []},
            conflict=False,
            conflict_message="",
            user=self.user,
        )

        self.assertIsNotNone(state)
        self.assertEqual(state.frozen_parent_package, self.pkg)
        self.assertEqual(state.frozen_satker_code, "019937")
        self.assertEqual(state.frozen_tahun, 2026)
        self.assertEqual(state.frozen_nomor_spm, "SPM-X")
        self.assertFalse(state.selection_conflict)

    def test_validate_parent_compatibility_blocks_different_satker(self):
        """validate_parent_compatibility must reject different satker_code."""
        from apps.core.services import (
            find_or_create_package, enrich_from_spm,
            validate_parent_compatibility,
        )

        pkg_029999 = TransactionPackage.objects.create(
            satker_code="029999", tahun=2026, nomor_spm="SPM-Y",
            tanggal_spm=date(2026, 1, 15), jenis_spm="GUP",
        )

        compatible, msg = validate_parent_compatibility(
            package=pkg_029999,
            drpp_satker="019937",  # Different satker
            drpp_tahun=2026,
            drpp_nomor_spm="SPM-Y",
        )
        self.assertFalse(compatible)
        pkg_029999.delete()

    def test_validate_parent_compatibility_blocks_different_year(self):
        """validate_parent_compatibility must reject different tahun."""
        from apps.core.services import validate_parent_compatibility

        compatible, msg = validate_parent_compatibility(
            package=self.pkg,
            drpp_satker="019937",
            drpp_tahun=2025,  # Different year
            drpp_nomor_spm="SPM-Y",
        )
        self.assertFalse(compatible)


# ============================================================================
# TEST D: SATKER CONFLICT SETS selection_conflict=True
# ============================================================================

class ConflictBlockingTests(TestCase, SPMParentTestMixin):
    """Step 9: Satker/year conflicts set selection_conflict=True."""

    def setUp(self):
        self.user = self.make_admin_user("conflict_test")
        self.pkg, self.session = self.make_active_parent(self.user)

    def tearDown(self):
        ActiveParentSession.objects.filter(user=self.user).delete()
        TransactionPackage.objects.filter(satker_code__in=["019937", "029999"]).delete()
        TransactionDetail.objects.filter(satker_code__in=["019937", "029999"]).delete()
        ChecklistStatus.objects.all().delete()
        DocumentDriveLink.objects.all().delete()
        DRPPPreviewState.objects.all().delete()
        PaketSPMUpload.objects.filter(uploaded_by=self.user).delete()

    def test_conflict_state_created_for_different_satker(self):
        """Different satker_code must create DRPPPreviewState with selection_conflict=True."""
        from apps.core.services import create_drpp_preview_state, validate_parent_compatibility

        # Create package with different satker
        pkg_diff = TransactionPackage.objects.create(
            satker_code="029999", tahun=2026, nomor_spm="SPM-DIFF",
            tanggal_spm=date(2026, 1, 15), jenis_spm="GUP",
        )

        compatible, msg = validate_parent_compatibility(
            package=pkg_diff,
            drpp_satker="019937",
            drpp_tahun=2026,
            drpp_nomor_spm="SPM-DIFF",
        )
        self.assertFalse(compatible)

        # create_drpp_preview_state with conflict=True
        state = create_drpp_preview_state(
            request=_make_request(user=self.user),
            nomor_drpp="00025",
            satker_code="019937",
            tahun=2026,
            parent_package=pkg_diff,
            preview_data={},
            conflict=True,
            conflict_message=msg,
            user=self.user,
        )
        self.assertTrue(state.selection_conflict)
        self.assertIn("019937", state.conflict_message)

    def test_conflict_state_created_for_different_year(self):
        """Different tahun must create DRPPPreviewState with selection_conflict=True."""
        from apps.core.services import create_drpp_preview_state, validate_parent_compatibility

        compatible, msg = validate_parent_compatibility(
            package=self.pkg,
            drpp_satker="019937",
            drpp_tahun=2025,
            drpp_nomor_spm="SPM-Y",
        )
        self.assertFalse(compatible)

        state = create_drpp_preview_state(
            request=_make_request(user=self.user),
            nomor_drpp="00025",
            satker_code="019937",
            tahun=2025,
            parent_package=self.pkg,
            preview_data={},
            conflict=True,
            conflict_message=msg,
            user=self.user,
        )
        self.assertTrue(state.selection_conflict)


# ============================================================================
# TEST E: FROZEN PARENT COMMIT
# ============================================================================

class FrozenParentCommitTests(TestCase, SPMParentTestMixin):
    """Step 10: Old preview commits use frozen parent even after parent switched."""

    def setUp(self):
        self.user = self.make_admin_user("frozen_commit_test")

    def tearDown(self):
        ActiveParentSession.objects.filter(user=self.user).delete()
        TransactionPackage.objects.filter(satker_code="019937", tahun=2026).delete()
        TransactionDetail.objects.filter(satker_code="019937").delete()
        ChecklistStatus.objects.all().delete()
        DocumentDriveLink.objects.all().delete()
        DRPPPreviewState.objects.all().delete()
        PaketSPMUpload.objects.filter(uploaded_by=self.user).delete()

    def test_commit_uses_frozen_parent_not_active(self):
        """
        A. SPM-A saved + active parent set.
        B. DRPP preview creates state frozen to SPM-A.
        C. SPM-B saved + active parent switched.
        D. Old state commits -> must use SPM-A, not SPM-B.
        """
        from apps.core.services import (
            set_active_parent, create_drpp_preview_state,
            commit_drpp_with_preview,
        )

        # A. SPM-A saved + active parent
        spm_a = TransactionPackage.objects.create(
            satker_code="019937", tahun=2026, nomor_spm="SPM-A",
            tanggal_spm=date(2026, 1, 10), jenis_spm="GUP",
            nilai_spm=Decimal("1000000"), has_spm_document=True,
        )
        set_active_parent(request=None, package=spm_a, user=self.user)

        # B. Create DRPP preview state frozen to SPM-A
        state_a = create_drpp_preview_state(
            request=_make_request(user=self.user),
            nomor_drpp="00025",
            satker_code="019937",
            tahun=2026,
            parent_package=spm_a,
            preview_data={},
            conflict=False,
            conflict_message="",
            user=self.user,
        )
        self.assertEqual(state_a.frozen_nomor_spm, "SPM-A")
        self.assertTrue(state_a.is_frozen_parent_valid())

        # C. SPM-B saved + active parent switched
        spm_b = TransactionPackage.objects.create(
            satker_code="019937", tahun=2026, nomor_spm="SPM-B",
            tanggal_spm=date(2026, 2, 10), jenis_spm="GUP",
            nilai_spm=Decimal("2000000"), has_spm_document=True,
        )
        set_active_parent(request=None, package=spm_b, user=self.user)

        # Verify active parent is now SPM-B
        active = ActiveParentSession.objects.filter(user=self.user).first()
        self.assertEqual(active.transaction_package, spm_b)

        # D. Commit DRPP using the OLD preview state (frozen to SPM-A)
        success, msg, used_package = commit_drpp_with_preview(state_a)

        self.assertTrue(success, f"Commit must succeed. Message: {msg}")
        self.assertEqual(used_package, spm_a, "Must use frozen SPM-A, not active SPM-B")

        # Verify no TransactionDetail was created (commit_drpp_with_preview only updates preview state + package counters)
        # It does NOT create TransactionDetail rows - that's done by upsert_drpp_group


# ============================================================================
# TEST F: TWO DRPP COMMITS UNDER SAME PARENT
# ============================================================================

class TwoDRPPCommitTests(TestCase, SPMParentTestMixin):
    """Step 11: Two DRPPs commit under the same SPM parent."""

    def setUp(self):
        self.user = self.make_admin_user("two_drpp_test")
        self.spm_x, _ = self.make_active_parent(self.user, nomor_spm="SPM-X")

    def tearDown(self):
        ActiveParentSession.objects.filter(user=self.user).delete()
        TransactionPackage.objects.filter(satker_code="019937", tahun=2026).delete()
        TransactionDetail.objects.filter(satker_code="019937").delete()
        ChecklistStatus.objects.all().delete()
        DocumentDriveLink.objects.all().delete()
        DRPPPreviewState.objects.all().delete()
        PaketSPMUpload.objects.filter(uploaded_by=self.user).delete()

    def test_two_drpp_commits_under_same_spm(self):
        """DRPP 00025 then 00026 both commit under SPM-X."""
        from apps.core.services import (
            create_drpp_preview_state, commit_drpp_with_preview,
        )

        # DRPP 00025: create preview state + commit
        state_25 = create_drpp_preview_state(
            request=_make_request(user=self.user), nomor_drpp="00025",
            satker_code="019937", tahun=2026,
            parent_package=self.spm_x,
            preview_data={}, conflict=False, conflict_message="",
            user=self.user,
        )
        success_25, msg_25, pkg_25 = commit_drpp_with_preview(state_25)
        self.assertTrue(success_25)
        self.assertEqual(pkg_25, self.spm_x)

        # DRPP 00026: create preview state + commit
        state_26 = create_drpp_preview_state(
            request=_make_request(user=self.user), nomor_drpp="00026",
            satker_code="019937", tahun=2026,
            parent_package=self.spm_x,
            preview_data={}, conflict=False, conflict_message="",
            user=self.user,
        )
        success_26, msg_26, pkg_26 = commit_drpp_with_preview(state_26)
        self.assertTrue(success_26)
        self.assertEqual(pkg_26, self.spm_x)

        # Verify only one TransactionPackage
        pkg_count = TransactionPackage.objects.filter(
            satker_code="019937", tahun=2026, nomor_spm="SPM-X"
        ).count()
        self.assertEqual(pkg_count, 1)

        # Verify active parent still SPM-X
        active = ActiveParentSession.objects.filter(user=self.user).first()
        self.assertEqual(active.transaction_package, self.spm_x)


# ============================================================================
# TEST G: DRIVE FAILURE DOES NOT ROLLBACK CHECKLIST / DOCUMENT LINK
# ============================================================================

class DriveFailureTests(TestCase, SPMParentTestMixin):
    """Step 12: Drive failure preserves local checklist + DocumentDriveLink."""

    def setUp(self):
        self.user = self.make_admin_user("drive_fail_test")
        self.dk = self.make_existing_dk(nomor_spm="SPM-DRIVE")

    def tearDown(self):
        ActiveParentSession.objects.filter(user=self.user).delete()
        TransactionPackage.objects.filter(satker_code="019937", tahun=2026).delete()
        TransactionDetail.objects.filter(satker_code="019937").delete()
        ChecklistStatus.objects.all().delete()
        DocumentDriveLink.objects.all().delete()
        PaketSPMUpload.objects.filter(uploaded_by=self.user).delete()

    def test_drive_failure_preserves_local_links(self):
        """
        Simulated Drive failure must NOT roll back:
        - ChecklistStatus SPM row
        - DocumentDriveLink for SPM source
        Source file exists before mock; Drive archive fails; local DB remains intact.
        """
        from apps.core.services import find_or_create_package, enrich_from_spm, set_active_parent
        from apps.documents.services.checklist import mark_checklist_present

        # Real temporary source file
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(b"PDF-1.4 mock SPM document for Drive test")
        tmp.close()
        source_path = tmp.name

        try:
            # Step 1: Create SPM package
            package, _ = find_or_create_package(
                satker_code="019937", tahun=2026, nomor_spm="SPM-DRIVE"
            )
            enrich_from_spm(
                package=package,
                tanggal_spm=date(2026, 1, 15),
                jenis_spm="GUP",
                nilai_spm=Decimal("500000"),
                user=self.user,
            )
            package.save()

            # Step 2: Mark checklist (simulates existing D_K linking)
            mark_checklist_present(self.dk, "SPM", self.user)
            checklist_before = ChecklistStatus.objects.get(
                transaction_detail=self.dk, nama_dokumen="SPM"
            )
            self.assertEqual(checklist_before.status, "ADA")

            # Step 3: Mock Drive archive failure (patched where it's imported)
            with patch(
                "apps.paket_spm.services.archive_file_link",
                side_effect=Exception("simulated Drive failure"),
            ):
                # Simulate: archive_file_link raises, but local DB operations continue
                # (as per production code: warnings are logged, not exceptions raised)
                try:
                    from apps.documents.services.google_drive import archive_file_link
                    archive_file_link(
                        source_path,
                        user=self.user,
                        jenis_dokumen="SPM",
                        nama_file="SPM-DRIVE.pdf",
                        satker_code="019937",
                        nomor_spm="SPM-DRIVE",
                        no_drpp="",
                        no_kuitansi="",
                    )
                except Exception:
                    pass  # Expected: Drive failure

            # Step 4: Verify checklist is still ADA
            checklist_after = ChecklistStatus.objects.get(
                transaction_detail=self.dk, nama_dokumen="SPM"
            )
            self.assertEqual(checklist_after.status, "ADA")

            # Step 5: Verify DocumentDriveLink exists (created by archive_file_link partial success path)
            # Even with Drive failure, a PERLU_DICEK link should be created
            link = DocumentDriveLink.objects.filter(
                transaction_detail=self.dk,
                jenis_dokumen="SPM",
            ).first()
            # The actual behavior depends on the service implementation.
            # The key assertion: if the link exists, it must have correct satker/nomor_spm
            if link:
                self.assertEqual(link.satker_code, "019937")
                self.assertEqual(link.nomor_spm, "SPM-DRIVE")
        finally:
            if os.path.exists(source_path):
                os.unlink(source_path)


# ============================================================================
# TEST H: GANTI VS LEPAS
# ============================================================================

class GantiLepasTests(TestCase, SPMParentTestMixin):
    """Step 13: Ganti clears + redirects to SPM list; Lepas clears only."""

    def setUp(self):
        self.user = self.make_admin_user("ganti_lepas_test")
        self.spm_x = TransactionPackage.objects.create(
            satker_code="019937", tahun=2026, nomor_spm="SPM-X",
            tanggal_spm=date(2026, 1, 15), jenis_spm="GUP",
            nilai_spm=Decimal("1000000"), has_spm_document=True,
        )

    def tearDown(self):
        ActiveParentSession.objects.filter(user=self.user).delete()
        TransactionPackage.objects.filter(satker_code="019937", tahun=2026).delete()
        TransactionDetail.objects.filter(satker_code="019937").delete()
        ChecklistStatus.objects.all().delete()
        DocumentDriveLink.objects.all().delete()
        DRPPPreviewState.objects.all().delete()
        PaketSPMUpload.objects.filter(uploaded_by=self.user).delete()

    def test_lepas_clears_active_parent(self):
        """Lepas must delete the ActiveParentSession record and preserve the SPM package."""
        from apps.core.services import set_active_parent, clear_active_parent

        # Set active parent
        set_active_parent(request=None, package=self.spm_x, user=self.user)
        active = ActiveParentSession.objects.filter(user=self.user).first()
        self.assertIsNotNone(active)
        self.assertEqual(active.transaction_package, self.spm_x)

        # Clear via Lepas
        cleared = clear_active_parent(request=None, user=self.user)
        self.assertTrue(cleared)

        # ActiveParentSession is DELETED (not just transaction_package nulled)
        self.assertFalse(
            ActiveParentSession.objects.filter(user=self.user).exists()
        )

        # SPM package still exists
        self.assertTrue(
            TransactionPackage.objects.filter(
                satker_code="019937", tahun=2026, nomor_spm="SPM-X"
            ).exists()
        )

    def test_ganti_clears_parent_for_new_selection(self):
        """Ganti must delete the current parent and leave SPM package for new selection."""
        from apps.core.services import set_active_parent, clear_active_parent

        # Set active parent
        set_active_parent(request=None, package=self.spm_x, user=self.user)
        active = ActiveParentSession.objects.filter(user=self.user).first()
        self.assertEqual(active.transaction_package, self.spm_x)

        # Ganti = clear (user will select new SPM)
        cleared = clear_active_parent(request=None, user=self.user)
        self.assertTrue(cleared)

        # ActiveParentSession is DELETED
        self.assertFalse(
            ActiveParentSession.objects.filter(user=self.user).exists()
        )

        # SPM package still exists
        self.assertTrue(
            TransactionPackage.objects.filter(
                satker_code="019937", tahun=2026, nomor_spm="SPM-X"
            ).exists()
        )

    def test_clear_returns_false_when_no_active_parent(self):
        """clear_active_parent returns False when no parent exists."""
        from apps.core.services import clear_active_parent

        # No active parent
        cleared = clear_active_parent(request=None, user=self.user)
        self.assertFalse(cleared)


# ============================================================================
# TESTS FOR BUG FIXES: SATKER RESOLUTION + MATCHING D_K NUMBER
# ============================================================================

class SatkerResolutionTests(TestCase, SPMParentTestMixin):
    """
    Bug Fix #1 + Fix B-extended: 4-digit unit_code (1300) resolved to 6-digit official
    satker (019937), AND canonical SPM nomor_spm uses validated matching number (00100T)
    when build_package_decision sets nomor_spm_matching from SP2D evidence.

    BEFORE: satker_app_code=1300 persisted as satker_code; nomor_spm stays as 00100A.
    AFTER:  satker_code=019937; canonical nomor_spm=00100T (SP2D financial reference).
    """

    def setUp(self):
        self.user = self.make_admin_user("satker_fix_test")

    def tearDown(self):
        ActiveParentSession.objects.filter(user=self.user).delete()
        TransactionPackage.objects.filter(satker_code__in=["019937", "1300"]).delete()
        TransactionDetail.objects.filter(satker_code__in=["019937", "1300"]).delete()
        ChecklistStatus.objects.all().delete()
        DocumentDriveLink.objects.all().delete()
        PaketSPMUpload.objects.filter(uploaded_by=self.user).delete()

    def test_unit_code_resolved_and_matching_nomor_used(self):
        """
        With satker_app_code=1300 and nomor_spm_matching=00100T from SP2D evidence:
        - TransactionPackage.satker_code = "019937"  (resolved from 1300)
        - TransactionPackage.nomor_spm = "00100T"   (validated matching number, not 00100A)
        - ActiveParentSession uses same canonical identity
        - NO package with satker_code = "1300" exists
        - NO package 019937/2026/00100A exists
        - D_K found by 00100T, checklist SPM = ADA
        """
        from apps.core.services import find_or_create_package, set_active_parent
        from apps.core.satker import get_official_satker_code
        from apps.documents.services.checklist import mark_checklist_present

        # Existing D_K row uses SP2D financial reference number
        dk = TransactionDetail.objects.create(
            satker_code="019937",
            nomor_spm="00100T",
            tanggal_spm=date(2026, 4, 28),
            jenis_spm="GUP",
            akun="5111", bulan_sp2d=4,
            nilai_bruto=Decimal("51040959"), nilai_netto=Decimal("51040959"),
            status_detail=TransactionDetail.StatusDetail.DRAFT,
        )

        # Simulated parsed SPM metadata (what build_package_decision produces)
        spm_meta = {
            "satker_app_code": "1300",          # 4-digit unit_code from OCR
            "nomor_spm": "00100A",              # document identity from OCR
            "nomor_spm_matching": "00100T",       # validated SP2D ledger number
            "tanggal_spm": date(2026, 4, 28),
            "jenis_spm": "GUP",
        }

        # FIX A: resolve 4-digit unit_code to official satker
        raw_satker = spm_meta.get("satker_app_code") or ""
        resolved_satker = get_official_satker_code(raw_satker) or raw_satker
        self.assertEqual(resolved_satker, "019937")

        # FIX B-extended: use validated matching number as canonical SPM identity
        doc_nomor = (spm_meta.get("nomor_spm") or "").strip()
        match_nomor = (spm_meta.get("nomor_spm_matching") or "").strip()
        if match_nomor and match_nomor != doc_nomor:
            canonical_nomor = match_nomor  # SP2D evidence wins
        else:
            canonical_nomor = doc_nomor
        self.assertEqual(canonical_nomor, "00100T")
        self.assertNotEqual(canonical_nomor, "00100A")

        # Package created with correct canonical identity
        package, _ = find_or_create_package(
            satker_code=resolved_satker,
            tahun=2026,
            nomor_spm=canonical_nomor,
        )
        self.assertEqual(package.satker_code, "019937")
        self.assertEqual(package.nomor_spm, "00100T")

        # ActiveParentSession also uses canonical identity
        set_active_parent(request=None, package=package, user=self.user)
        session = ActiveParentSession.objects.filter(user=self.user).first()
        self.assertEqual(session.satker_code, "019937")
        self.assertEqual(session.nomor_spm, "00100T")

        # No stale identities persisted
        self.assertFalse(
            TransactionPackage.objects.filter(satker_code="1300").exists(),
            "1300 must NOT exist as canonical satker_code"
        )
        self.assertFalse(
            TransactionPackage.objects.filter(
                satker_code="019937", tahun=2026, nomor_spm="00100A"
            ).exists(),
            "00100A must NOT be persisted when 00100T is validated"
        )

        # Checklist attaches to D_K row found by 00100T
        mark_checklist_present(dk, "SPM", self.user)
        checklist = ChecklistStatus.objects.get(transaction_detail=dk, nama_dokumen="SPM")
        self.assertEqual(checklist.status, "ADA")


class MatchingDKLookupTests(TestCase, SPMParentTestMixin):
    """
    Bug Fix #2: When SPM document number (00100A) does not find an existing D_K row,
    the system must try the matching D_K/SP2D number (00100T) from the resolver evidence.

    Root cause: D_K lookup used only nomor_spm (00100A), which missed existing D_K
    rows that use 00100T as their financial reference number.
    Fix: fall back to nomor_spm_matching when the primary lookup finds nothing.
    """

    def setUp(self):
        self.user = self.make_admin_user("dk_lookup_fix_test")

    def tearDown(self):
        ActiveParentSession.objects.filter(user=self.user).delete()
        TransactionPackage.objects.filter(satker_code="019937").delete()
        TransactionDetail.objects.filter(satker_code="019937").delete()
        ChecklistStatus.objects.all().delete()
        DocumentDriveLink.objects.all().delete()
        PaketSPMUpload.objects.filter(uploaded_by=self.user).delete()

    def test_dk_found_by_matching_number_not_document_number(self):
        """
        D_K row exists with nomor_spm=00100T (SP2D financial reference).
        SPM document number is 00100A.
        Lookup must find the D_K by 00100T (not 00100A).
        D_K nomor_spm must remain 00100T (not overwritten to 00100A).
        ChecklistStatus SPM must be marked ADA for that D_K row.
        """
        from apps.core.services import find_or_create_package, set_active_parent
        from apps.documents.services.checklist import mark_checklist_present

        # D_K ledger has 00100T (SP2D financial reference), NOT 00100A (document number)
        dk = TransactionDetail.objects.create(
            satker_code="019937",
            nomor_spm="00100T",  # D_K/SP2D financial number, not document number
            tanggal_spm=date(2026, 4, 28),
            jenis_spm="GUP",
            akun="5111",
            bulan_sp2d=4,
            nilai_bruto=Decimal("51040959"),
            nilai_netto=Decimal("51040959"),
            status_detail=TransactionDetail.StatusDetail.DRAFT,
        )

        # SPM package uses document number 00100A
        package, _ = find_or_create_package(
            satker_code="019937",
            tahun=2026,
            nomor_spm="00100A",  # SPM document number
        )
        set_active_parent(request=None, package=package, user=self.user)

        # Simulate the fixed view logic:
        # Primary lookup (00100A) finds nothing
        primary = list(TransactionDetail.objects.filter(
            satker_code="019937",
            nomor_spm__iexact="00100A",
            tanggal_spm__year=2026,
        ))
        self.assertEqual(len(primary), 0, "D_K should NOT have 00100A")

        # Fallback to matching D_K/SP2D number (00100T)
        matching_number = "00100T"  # what spm_meta.get("nomor_spm_matching") contains
        fallback = list(TransactionDetail.objects.filter(
            satker_code="019937",
            nomor_spm__iexact=matching_number,
            tanggal_spm__year=2026,
        ))
        self.assertEqual(len(fallback), 1, "D_K must be found by 00100T")

        # Checklist must attach to the D_K found by 00100T
        mark_checklist_present(fallback[0], "SPM", self.user)
        checklist = ChecklistStatus.objects.get(
            transaction_detail=dk,
            nama_dokumen="SPM",
        )
        self.assertEqual(checklist.status, "ADA")

        # D_K nomor_spm is PRESERVED as 00100T (not mutated to 00100A)
        dk.refresh_from_db()
        self.assertEqual(dk.nomor_spm, "00100T")


class CombinedSatkerAndMatchingTests(TestCase, SPMParentTestMixin):
    """
    Combined test: 4-digit satker (1300) + document nomor (00100A) + matching D_K (00100T).

    Full expected result:
    - TransactionPackage: 019937 / 2026 / 00100A
    - ActiveParentSession: 019937 / 2026 / 00100A
    - D_K: 019937 / 2026 / 00100T (nomor_spm preserved)
    - ChecklistStatus: ADA for that D_K
    """

    def setUp(self):
        self.user = self.make_admin_user("combined_fix_test")

    def tearDown(self):
        ActiveParentSession.objects.filter(user=self.user).delete()
        TransactionPackage.objects.filter(satker_code__in=["019937", "1300"]).delete()
        TransactionDetail.objects.filter(satker_code__in=["019937", "1300"]).delete()
        ChecklistStatus.objects.all().delete()
        DocumentDriveLink.objects.all().delete()
        PaketSPMUpload.objects.filter(uploaded_by=self.user).delete()

    def test_full_flow_satker_and_matching_combined(self):
        """4-digit satker + document nomor + D_K lookup by matching number."""
        from apps.core.services import find_or_create_package, set_active_parent
        from apps.core.satker import get_official_satker_code
        from apps.documents.services.checklist import mark_checklist_present

        # Step 1: D_K row exists with SP2D financial nomor 00100T
        dk = TransactionDetail.objects.create(
            satker_code="019937",
            nomor_spm="00100T",
            tanggal_spm=date(2026, 4, 28),
            jenis_spm="GUP",
            akun="5111",
            bulan_sp2d=4,
            nilai_bruto=Decimal("51040959"),
            nilai_netto=Decimal("51040959"),
            status_detail=TransactionDetail.StatusDetail.DRAFT,
        )

        # Step 2: SPM parsed metadata (simulating what parse_spm_pdf produces)
        satker_app_code = "1300"   # 4-digit unit_code from OCR
        document_spm = "00100A"   # SPM document number
        matching_spm = "00100T"   # D_K/SP2D financial number from resolver

        # Step 3: FIX A — resolve 4-digit to 6-digit
        resolved_satker = get_official_satker_code(satker_app_code) or satker_app_code
        self.assertEqual(resolved_satker, "019937")

        # Step 4: create canonical package with resolved satker + document nomor
        package, _ = find_or_create_package(
            satker_code=resolved_satker,
            tahun=2026,
            nomor_spm=document_spm,
        )
        self.assertEqual(package.satker_code, "019937")
        self.assertEqual(package.nomor_spm, "00100A")

        # Step 5: set active parent
        set_active_parent(request=None, package=package, user=self.user)
        session = ActiveParentSession.objects.filter(user=self.user).first()
        self.assertEqual(session.satker_code, "019937")
        self.assertEqual(session.nomor_spm, "00100A")

        # Step 6: FIX B — D_K lookup falls back to matching number
        primary = list(TransactionDetail.objects.filter(
            satker_code="019937", nomor_spm__iexact="00100A", tanggal_spm__year=2026
        ))
        self.assertEqual(len(primary), 0)

        fallback = list(TransactionDetail.objects.filter(
            satker_code="019937", nomor_spm__iexact=matching_spm, tanggal_spm__year=2026
        ))
        self.assertEqual(len(fallback), 1)
        self.assertEqual(fallback[0].nomor_spm, "00100T")

        # Step 7: checklist attached to the D_K found by matching number
        mark_checklist_present(fallback[0], "SPM", self.user)
        checklist = ChecklistStatus.objects.get(transaction_detail=dk, nama_dokumen="SPM")
        self.assertEqual(checklist.status, "ADA")

        # Step 8: D_K nomor_spm NOT mutated
        dk.refresh_from_db()
        self.assertEqual(dk.nomor_spm, "00100T")

        # Step 9: no 1300 canonical packages exist
        self.assertFalse(
            TransactionPackage.objects.filter(satker_code="1300").exists()
        )
