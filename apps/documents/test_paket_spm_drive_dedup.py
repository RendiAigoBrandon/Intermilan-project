"""
Google Drive Paket SPM Flow Tests - Central Archive Architecture.

Test INTEGRASI AKTIF: Upload DRPP dan Kuitansi (paket_spm:list)
dengan duplicate protection.

Tests use MOCK - no real Google API calls.
"""

import json
import os
import tempfile
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.documents.models import DocumentDriveLink
from apps.documents.services.google_drive import archive_file_link


User = get_user_model()


class TestPaketSPMArchiveDedupIntegration(TestCase):
    """
    Test active paket_spm flow uses archive_file_link with DUPLICATE PROTECTION.

    ACTIVE FLOW: paket_spm:list → services → archive_file_link()
    """

    def setUp(self):
        """Create unique user for each test."""
        self.unique_id = uuid.uuid4().hex[:8]
        self.admin = User.objects.create_user(
            username=f"admin_test_{self.unique_id}",
            password="testpass",
            is_superuser=True,
            is_staff=True,
        )
        self.original_env = os.environ.copy()

    def tearDown(self):
        """Restore environment."""
        os.environ.clear()
        os.environ.update(self.original_env)

    def test_first_upload_creates_drive_link(self):
        """First upload creates new DocumentDriveLink with hash."""
        os.environ["GOOGLE_DRIVE_ENABLED"] = "false"

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"First test PDF content")
            temp_path = f.name

        try:
            # Patch dedup to return no existing link
            with patch("apps.documents.services.google_drive_dedup.find_existing_drive_link") as mock_find:
                mock_find.return_value = None

                result, link, is_reused = archive_file_link(
                    file_path=temp_path,
                    user=self.admin,
                    jenis_dokumen="DRPP/KW",
                    nama_file="TEST001.pdf",
                    satker_code="1300",
                    nomor_spm="00001T",
                    no_drpp="DRPP001",
                    no_kuitansi="KW001",
                    transaction_detail=None,
                )

            self.assertFalse(is_reused)
            self.assertEqual(result["status"], "disabled")
            self.assertIsNotNone(link.id)
            self.assertEqual(link.satker_code, "1300")
            self.assertIn("hash=", link.catatan)

        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def test_second_upload_reuses_existing_link(self):
        """Second upload of identical file reuses existing DocumentDriveLink."""
        os.environ["GOOGLE_DRIVE_ENABLED"] = "false"

        test_id = uuid.uuid4().hex[:8]
        test_hash = f"hash_{test_id}"

        # Create existing link with known hash
        existing_link = DocumentDriveLink.objects.create(
            satker_code="1300",
            nomor_spm="00001T",
            no_drpp=f"DRPP_{test_id}",
            no_kuitansi=f"KW_{test_id}",
            jenis_dokumen="DRPP/KW",
            nama_file=f"TEST_{test_id}.pdf",
            google_drive_url=f"https://drive.google.com/file/d/existing_{test_id}/view",
            status=DocumentDriveLink.Status.AKTIF,
            created_by=self.admin,
            catatan=f"hash={test_hash}; drive_status=uploaded",
        )

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"Test content")
            temp_path = f.name

        try:
            # Patch dedup to return existing link by hash
            with patch("apps.documents.services.google_drive_dedup.find_existing_drive_link") as mock_find:
                mock_find.return_value = existing_link

                result, link, is_reused = archive_file_link(
                    file_path=temp_path,
                    user=self.admin,
                    jenis_dokumen="DRPP/KW",
                    nama_file=f"TEST_{test_id}.pdf",
                    satker_code="1300",
                    nomor_spm="00001T",
                    no_drpp=f"DRPP_{test_id}",
                    no_kuitansi=f"KW_{test_id}",
                    transaction_detail=None,
                )

            self.assertTrue(is_reused)
            self.assertEqual(link.id, existing_link.id)
            self.assertEqual(result["status"], "reused")
            self.assertTrue(result.get("is_duplicate"))

        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def test_drive_failure_saves_local(self):
        """Drive failure → D_K saved with local fallback (no rollback)."""
        os.environ["GOOGLE_DRIVE_ENABLED"] = "true"
        os.environ["GOOGLE_DRIVE_UPLOAD_MODE"] = "oauth"

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"Test content")
            temp_path = f.name

        try:
            # No existing link
            with patch("apps.documents.services.google_drive_dedup.find_existing_drive_link") as mock_find:
                mock_find.return_value = None

                # Mock Drive failure
                with patch("apps.documents.services.google_drive._upload_oauth_central") as mock_upload:
                    mock_upload.side_effect = Exception("Drive API error")

                    result, link, is_reused = archive_file_link(
                        file_path=temp_path,
                        user=self.admin,
                        jenis_dokumen="DRPP/KW",
                        nama_file="FAILURE_TEST.pdf",
                        satker_code="1300",
                        nomor_spm="00002T",
                        transaction_detail=None,
                    )

            # D_K still created with local fallback
            self.assertIsNotNone(link.id)
            self.assertEqual(result["status"], "failed")
            self.assertIn("Drive API error", result["error_message"])
            self.assertTrue(result.get("local_path"))

        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def test_baseline_links_unchanged(self):
        """Existing baseline DocumentDriveLink records not modified."""
        os.environ["GOOGLE_DRIVE_ENABLED"] = "false"

        test_id = uuid.uuid4().hex[:8]

        baseline_link = DocumentDriveLink.objects.create(
            satker_code="1300",
            nomor_spm=f"BASELINE_{test_id}",
            google_drive_url=f"https://drive.google.com/file/d/baseline_{test_id}/view",
            status=DocumentDriveLink.Status.AKTIF,
            catatan="Imported from baseline",
        )

        original_url = baseline_link.google_drive_url
        original_catatan = baseline_link.catatan

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"New content")
            temp_path = f.name

        try:
            with patch("apps.documents.services.google_drive_dedup.find_existing_drive_link") as mock_find:
                mock_find.return_value = None

                archive_file_link(
                    file_path=temp_path,
                    user=self.admin,
                    jenis_dokumen="DRPP/KW",
                    nama_file=f"NEW_{test_id}.pdf",
                    satker_code="1300",
                    nomor_spm=f"NEW_{test_id}",
                    transaction_detail=None,
                )

            baseline_link.refresh_from_db()
            self.assertEqual(baseline_link.google_drive_url, original_url)
            self.assertEqual(baseline_link.catatan, original_catatan)

        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def test_central_oauth_used_for_upload(self):
        """Upload uses central OAuth token, not per-user."""
        os.environ["GOOGLE_DRIVE_ENABLED"] = "true"
        os.environ["GOOGLE_DRIVE_UPLOAD_MODE"] = "oauth"

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"Central archive test")
            temp_path = f.name

        try:
            # Mock central token exists
            import pathlib
            from django.conf import settings
            central_path = pathlib.Path(settings.MEDIA_ROOT) / "drive_tokens" / "archive_oauth.json"
            central_path.parent.mkdir(parents=True, exist_ok=True)
            central_path.write_text(json.dumps({
                "token": "central_token",
                "refresh_token": "central_refresh",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "central_client",
                "client_secret": "central_secret",
                "scopes": ["https://www.googleapis.com/auth/drive.file"],
            }))

            with patch("apps.documents.services.google_drive_dedup.find_existing_drive_link") as mock_find:
                mock_find.return_value = None

                with patch("apps.documents.services.google_drive._upload_oauth_central") as mock_upload:
                    mock_upload.return_value = {
                        "status": "uploaded",
                        "file_id": "central_file_id",
                        "web_view_link": "https://drive.google.com/file/d/central_file_id/view",
                        "local_path": "",
                        "mime_type": "application/pdf",
                        "size": 100,
                        "error_message": "",
                        "upload_mode": "oauth",
                        "folder_id": "test_folder",
                    }

                    result, link, is_reused = archive_file_link(
                        file_path=temp_path,
                        user=self.admin,
                        jenis_dokumen="DRPP/KW",
                        nama_file="CENTRAL_TEST.pdf",
                        satker_code="1300",
                        transaction_detail=None,
                    )

                    # Central OAuth called
                    mock_upload.assert_called_once()
                    self.assertEqual(result["status"], "uploaded")
                    # Note: archive_file_link doesn't return upload_mode, it returns processed result
                    self.assertEqual(result["web_view_link"], "https://drive.google.com/file/d/central_file_id/view")

            central_path.unlink(missing_ok=True)

        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
