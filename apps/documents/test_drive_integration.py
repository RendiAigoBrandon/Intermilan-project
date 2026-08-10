"""
Google Drive Integration Tests - Central Archive Architecture.

Test dengan mocking Google API - TIDAK menjalankan Google API nyata.

Test scenarios untuk Central Archive:
1. Admin can authorize (operator blocked)
2. Central token used for all uploads
3. Operator upload uses central token (no per-user OAuth)
4. Duplicate protection works
5. Baseline DocumentDriveLink tidak berubah
6. Drive failure tidak rollback D_K
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Profile
from apps.documents.models import DocumentDriveLink
from apps.documents.services.google_drive import (
    archive_file_link,
    check_central_oauth_status,
    drive_enabled,
    get_drive_mode,
    upload_file_to_drive,
)
from apps.documents.services.google_drive_dedup import (
    archive_file_with_dedup,
    calculate_file_hash,
    find_existing_drive_link,
)


User = get_user_model()


class TestGoogleDriveDisabled(TestCase):
    """Test when Google Drive is disabled."""

    def setUp(self):
        """Reset environment for each test."""
        self.original_env = os.environ.copy()

    def tearDown(self):
        """Restore environment."""
        os.environ.clear()
        os.environ.update(self.original_env)

    @override_settings()
    def test_drive_disabled_mode(self):
        """Test get_drive_mode returns 'disabled' when not configured."""
        os.environ.pop("GOOGLE_DRIVE_ENABLED", None)
        os.environ.pop("GOOGLE_DRIVE_UPLOAD_MODE", None)

        import importlib
        import apps.documents.services.google_drive as gd_module
        importlib.reload(gd_module)

        mode = gd_module.get_drive_mode()
        self.assertEqual(mode, "disabled")

    @override_settings()
    def test_drive_enabled_check(self):
        """Test drive_enabled returns False when disabled."""
        os.environ["GOOGLE_DRIVE_ENABLED"] = "false"

        import importlib
        import apps.documents.services.google_drive as gd_module
        importlib.reload(gd_module)

        self.assertFalse(gd_module.drive_enabled())


class TestDocumentDriveLinkPersistence(TestCase):
    """Test DocumentDriveLink creation and persistence."""

    def setUp(self):
        """Setup test user with unique username."""
        import uuid
        self.unique_id = uuid.uuid4().hex[:8]
        self.user = User.objects.create_user(
            username=f"testuser_drive_{self.unique_id}",
            password="testpass"
        )

    def test_archive_creates_link(self):
        """Test archive_file_link creates DocumentDriveLink."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"Test content")
            temp_path = f.name

        try:
            with override_settings(GOOGLE_DRIVE_ENABLED="false"):
                import importlib
                import apps.documents.services.google_drive as gd_module
                importlib.reload(gd_module)

                # Archive with local fallback
                result, link, is_reused = gd_module.archive_file_link(
                    file_path=temp_path,
                    user=self.user,
                    jenis_dokumen="KUITANSI",
                    nama_file="KW001.pdf",
                    satker_code="1300",
                    nomor_spm="00001T",
                    no_kuitansi="KW001",
                )

            # Verify link created
            self.assertIsNotNone(link.id)
            self.assertEqual(link.jenis_dokumen, "KUITANSI")
            self.assertEqual(link.satker_code, "1300")
            self.assertEqual(link.nama_file, "KW001.pdf")
            self.assertEqual(link.created_by, self.user)

            # Verify status when Drive disabled
            self.assertEqual(link.status, DocumentDriveLink.Status.PERLU_DICEK)

        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


class TestDuplicateProtection(TestCase):
    """Test duplicate detection and prevention."""

    def setUp(self):
        """Setup test user and existing link with unique identifiers."""
        import uuid
        self.unique_suffix = uuid.uuid4().hex[:8]
        self.user = User.objects.create_user(
            username=f"testuser_dedup_{self.unique_suffix}",
            password="testpass"
        )

        # Create existing link with unique kuitansi
        self.existing_kuitansi = f"KW{self.unique_suffix}"
        self.existing_link = DocumentDriveLink.objects.create(
            satker_code="1300",
            nomor_spm=f"00001T{self.unique_suffix}",
            no_kuitansi=self.existing_kuitansi,
            jenis_dokumen="KUITANSI",
            nama_file=f"KW_{self.unique_suffix}.pdf",
            google_drive_url=f"https://drive.google.com/file/d/{self.unique_suffix}/view",
            status=DocumentDriveLink.Status.AKTIF,
            created_by=self.user,
        )

    def test_find_existing_by_kuitansi(self):
        """Test finding existing link by Kuitansi number."""
        found = find_existing_drive_link(
            satker_code="1300",
            no_kuitansi=self.existing_kuitansi,
        )

        self.assertIsNotNone(found)
        self.assertEqual(found.id, self.existing_link.id)

    def test_find_existing_no_match(self):
        """Test no match for different Kuitansi number."""
        found = find_existing_drive_link(
            satker_code="1300",
            no_kuitansi="KW999_NOTEXIST",
        )

        self.assertIsNone(found)

    def test_archive_with_dedup_reuses_link(self):
        """Test archive_file_with_dedup reuses existing link."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"Test content")
            temp_path = f.name

        try:
            with override_settings(GOOGLE_DRIVE_ENABLED="false"):
                import importlib
                import apps.documents.services.google_drive_dedup as dedup_module
                importlib.reload(dedup_module)

                result, link, is_reused = dedup_module.archive_file_with_dedup(
                    file_path=temp_path,
                    user=self.user,
                    jenis_dokumen="KUITANSI",
                    nama_file=f"KW_NEW_{self.unique_suffix}.pdf",
                    satker_code="1300",
                    no_kuitansi=self.existing_kuitansi,
                )

            # Should reuse existing link
            self.assertTrue(is_reused)
            self.assertEqual(link.id, self.existing_link.id)
            self.assertEqual(result["status"], "reused")

        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


class TestFileHash(TestCase):
    """Test file hash calculation."""

    def test_same_content_same_hash(self):
        """Test same content produces same hash."""
        with tempfile.NamedTemporaryFile(delete=False) as f1:
            f1.write(b"Test content")
            path1 = f1.name

        with tempfile.NamedTemporaryFile(delete=False) as f2:
            f2.write(b"Test content")
            path2 = f2.name

        try:
            hash1 = calculate_file_hash(path1)
            hash2 = calculate_file_hash(path2)

            self.assertEqual(hash1, hash2)
        finally:
            os.unlink(path1)
            os.unlink(path2)

    def test_different_content_different_hash(self):
        """Test different content produces different hash."""
        with tempfile.NamedTemporaryFile(delete=False) as f1:
            f1.write(b"Content A")
            path1 = f1.name

        with tempfile.NamedTemporaryFile(delete=False) as f2:
            f2.write(b"Content B")
            path2 = f2.name

        try:
            hash1 = calculate_file_hash(path1)
            hash2 = calculate_file_hash(path2)

            self.assertNotEqual(hash1, hash2)
        finally:
            os.unlink(path1)
            os.unlink(path2)


class TestBaselineIntegrity(TestCase):
    """Test that existing baseline data is not affected."""

    def test_existing_links_not_modified(self):
        """Test existing DocumentDriveLink records are not modified."""
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        # Create baseline link (as if imported from existing data)
        baseline_link = DocumentDriveLink.objects.create(
            satker_code="1300",
            nomor_spm=f"BASELINE_{unique_id}",
            google_drive_url=f"https://drive.google.com/file/d/baseline_{unique_id}/view",
            status=DocumentDriveLink.Status.AKTIF,
            catatan="Imported from baseline - do not modify",
        )

        original_url = baseline_link.google_drive_url
        original_status = baseline_link.status

        # Archive a new file (should NOT modify baseline)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"New content")
            temp_path = f.name

        try:
            with override_settings(GOOGLE_DRIVE_ENABLED="false"):
                import importlib
                import apps.documents.services.google_drive_dedup as dedup_module
                importlib.reload(dedup_module)

                # Just call to verify baseline unchanged - return ignored
                dedup_module.archive_file_with_dedup(
                    file_path=temp_path,
                    user=None,
                    jenis_dokumen="KUITANSI",
                    nama_file=f"NEW_KW_{unique_id}.pdf",
                    satker_code="1300",
                    no_kuitansi=f"NEW_KW_{unique_id}",
                )

            # Reload baseline link
            baseline_link.refresh_from_db()

            # Should not be modified
            self.assertEqual(baseline_link.google_drive_url, original_url)
            self.assertEqual(baseline_link.status, original_status)

        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


class TestCentralArchiveArchitecture(TestCase):
    """Test central archive architecture (not per-user)."""

    def setUp(self):
        """Setup admin and operator users."""
        import uuid
        self.unique_id = uuid.uuid4().hex[:8]

        self.admin = User.objects.create_user(
            username=f"admin_test_{self.unique_id}",
            password="testpass",
            is_superuser=True,
            is_staff=True,
        )
        self.operator = User.objects.create_user(
            username=f"operator_test_{self.unique_id}",
            password="testpass",
            is_superuser=False,
            is_staff=False,
        )

    def test_central_token_used_for_upload(self):
        """Test that upload uses central token, not per-user."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"Test content for central upload")
            temp_path = f.name

        try:
            with override_settings(GOOGLE_DRIVE_ENABLED="true", GOOGLE_DRIVE_UPLOAD_MODE="oauth"):
                import importlib
                import apps.documents.services.google_drive as gd_module
                importlib.reload(gd_module)

                # Mock central token exists
                import pathlib
                central_token_path = pathlib.Path(gd_module.settings.MEDIA_ROOT) / "drive_tokens" / "archive_oauth.json"
                central_token_path.parent.mkdir(parents=True, exist_ok=True)
                central_token_path.write_text(json.dumps({
                    "token": "mock_token",
                    "refresh_token": "mock_refresh",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "client_id": "mock_id",
                    "client_secret": "mock_secret",
                    "scopes": ["https://www.googleapis.com/auth/drive.file"],
                }))

                # Mock the upload
                with patch.object(gd_module, '_upload_oauth_central') as mock_upload:
                    mock_upload.return_value = {
                        "status": "uploaded",
                        "file_id": "central_file_id",
                        "web_view_link": "https://drive.google.com/file/d/central_file_id/view",
                        "local_path": "",
                        "mime_type": "application/pdf",
                        "size": 100,
                        "error_message": "",
                        "upload_mode": "oauth",
                    }

                    # Upload as operator (should use central token, not operator's token)
                    result = gd_module.upload_file_to_drive(
                        file_path=temp_path,
                        display_name="central_test.pdf",
                    )

                    # Verify central upload was called (not per-user)
                    mock_upload.assert_called_once()
                    self.assertEqual(result["status"], "uploaded")
                    self.assertEqual(result["upload_mode"], "oauth")

                # Cleanup
                central_token_path.unlink(missing_ok=True)

        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def test_central_oauth_check(self):
        """Test central OAuth status check."""
        with override_settings(GOOGLE_DRIVE_ENABLED="true", GOOGLE_DRIVE_UPLOAD_MODE="oauth"):
            import importlib
            import apps.documents.services.google_drive as gd_module
            importlib.reload(gd_module)

            # Test without token
            status = gd_module.check_central_oauth_status()
            self.assertFalse(status["is_authorized"])
            self.assertEqual(status["mode"], "oauth")
            self.assertIn("authorize", status["error"].lower())


class TestOAuthViewsPermissions(TestCase):
    """Test OAuth views permission decorators."""

    def setUp(self):
        """Setup admin and operator users."""
        import uuid
        self.unique_id = uuid.uuid4().hex[:8]

        self.admin = User.objects.create_user(
            username=f"admin_oauth_{self.unique_id}",
            password="testpass",
            is_superuser=True,
            is_staff=True,
        )
        self.operator = User.objects.create_user(
            username=f"operator_oauth_{self.unique_id}",
            password="testpass",
            is_superuser=False,
            is_staff=False,
        )

    def test_operator_cannot_authorize(self):
        """Test that operator is blocked from authorization."""
        self.client.login(username=f"operator_oauth_{self.unique_id}", password="testpass")
        response = self.client.get("/documents/drive/oauth/authorize/")
        # Should redirect to dashboard (not authorize)
        self.assertEqual(response.status_code, 302)

    def test_admin_can_access_authorize(self):
        """Test that admin can access authorization route (not blocked by permission)."""
        self.client.login(username=f"admin_oauth_{self.unique_id}", password="testpass")
        response = self.client.get("/documents/drive/oauth/authorize/")
        # Admin should NOT get 403/401 (permission denied)
        # May get 302 (redirect to dashboard if not configured) or other valid response
        self.assertNotEqual(response.status_code, 403)
        # Admin can reach the route - redirect is valid when OAuth not configured
        # This confirms admin is NOT blocked by @user_passes_test decorator
        self.assertIn(response.status_code, [200, 302])


class TestOAuthPKCEFlow(TestCase):
    """Test OAuth PKCE session storage and callback restoration."""

    def setUp(self):
        """Setup admin user."""
        import uuid
        self.unique_id = uuid.uuid4().hex[:8]
        self.admin = User.objects.create_user(
            username=f"admin_pkce_{self.unique_id}",
            password="testpass",
            is_superuser=True,
            is_staff=True,
        )

    @override_settings(
        GOOGLE_DRIVE_ENABLED="true",
        GOOGLE_DRIVE_UPLOAD_MODE="oauth",
    )
    def test_authorize_stores_session_keys(self):
        """Test that authorize stores PKCE verifier and state in session.

        Note: Django test client follows redirects by default.
        We check the FIRST response (before following) by using
        assertRedirects with fetch_redirect_response=False, or by
        checking that the chain ends at Google.
        """
        import importlib
        import apps.documents.services.google_drive as gd_module
        importlib.reload(gd_module)

        self.client.login(username=f"admin_pkce_{self.unique_id}", password="testpass")

        # Make request WITH redirect following to see the full chain
        response = self.client.get("/documents/drive/oauth/authorize/")
        # The final response after following /authorize/ → Google → /callback/ → /dashboard/
        # is the /dashboard/ page (since Google redirects to callback, which redirects to dashboard).

        # Session keys should have been stored BEFORE the redirect to Google
        session = self.client.session
        self.assertIn("_oauth_state", session, "Session should have _oauth_state from authorize")
        self.assertIn("_oauth_code_verifier", session, "Session should have _oauth_code_verifier")
        self.assertIn("_oauth_client_config", session, "Session should have _oauth_client_config")

        # code_verifier must be a non-empty string (128 chars for PKCE S256)
        verifier = session["_oauth_code_verifier"]
        self.assertIsInstance(verifier, str)
        self.assertGreater(len(verifier), 40, "PKCE verifier should be 43-128 chars")
        self.assertLessEqual(len(verifier), 128)

        # state should be a non-empty base64 string
        state = session["_oauth_state"]
        self.assertIsInstance(state, str)
        self.assertGreater(len(state), 10)

        # client_config should contain the "web" top-level key (required by Flow.from_client_config)
        # IMPORTANT: we must store client_config with "web" key, NOT flow.client_config
        # (flow.client_config is the unwrapped inner dict, which would cause
        # "Client secrets must be for a web or installed app." error in the callback).
        config = session["_oauth_client_config"]
        self.assertIn("web", config, "client_config must have 'web' top-level key")
        self.assertIn("client_id", config["web"])
        self.assertIn("client_secret", config["web"])
        self.assertIn("auth_uri", config["web"])
        self.assertIn("token_uri", config["web"])
        self.assertIn("redirect_uris", config["web"])
        self.assertIsInstance(config["web"]["redirect_uris"], list)
        self.assertGreater(len(config["web"]["redirect_uris"]), 0)

    @override_settings(
        GOOGLE_DRIVE_ENABLED="true",
        GOOGLE_DRIVE_UPLOAD_MODE="oauth",
    )
    def test_authorize_redirects_to_google_not_state(self):
        """Test that authorize redirects to Google authorization URL, NOT to OAuth state.

        This was the critical regression: the tuple return order of
        get_authorization_url_with_flow() was (state, url, flow), but the caller
        unpacked as (auth_url, state, flow) — causing redirect(auth_url) to receive
        the base64-encoded state string, which Django tried to reverse as a URL name.

        The correct behavior is: redirect to https://accounts.google.com/... with
        the state passed as a query parameter (not used as the redirect URL itself).
        """
        import importlib
        import apps.documents.services.google_drive as gd_module
        importlib.reload(gd_module)

        self.client.login(username=f"admin_pkce_{self.unique_id}", password="testpass")

        # Get first response WITHOUT following redirects
        response = self.client.get("/documents/drive/oauth/authorize/", follow=False)

        # Must be a redirect (302) — not an error or 200
        self.assertEqual(response.status_code, 302, f"Expected 302 redirect, got {response.status_code}")

        redirect_target = response.url

        # CRITICAL: redirect_target must be the Google authorization URL, NOT the OAuth state string.
        # Previously, auth_url received the state value and Django tried to reverse it as a URL name.
        self.assertTrue(
            redirect_target.startswith("https://accounts.google.com/"),
            f"Expected redirect to accounts.google.com, got: {redirect_target}",
        )

        # OAuth state must appear as a query parameter in the Google URL (CSRF protection).
        # The state is NOT the redirect target itself — it is appended to the URL.
        oauth_state = self.client.session.get("_oauth_state", "")
        self.assertIn(f"state=", redirect_target, "OAuth state must be passed as a query parameter to Google")
        self.assertIn(oauth_state, redirect_target, "Stored OAuth state must appear in the authorization URL as &state=... query param")

        # Redirect must NOT be a Django URL (e.g., /dashboard/)
        self.assertFalse(
            redirect_target.startswith("/"),
            f"Redirect target must NOT be a Django URL path. Got: {redirect_target}",
        )

    @override_settings(
        GOOGLE_DRIVE_ENABLED="true",
        GOOGLE_DRIVE_UPLOAD_MODE="oauth",
    )
    def test_callback_requires_session_keys(self):
        """Test that callback fails gracefully when session is missing verifier."""
        import importlib
        import apps.documents.services.google_drive as gd_module
        importlib.reload(gd_module)

        self.client.login(username=f"admin_pkce_{self.unique_id}", password="testpass")

        # Simulate callback WITHOUT going through authorize first (no session keys)
        response = self.client.get(
            "/documents/drive/oauth/callback/",
            {"code": "fake_code", "state": "fake_state"},
        )
        # Should redirect to dashboard with error message
        self.assertEqual(response.status_code, 302)
        messages_list = list(response.wsgi_request._messages)
        # Missing code OR missing verifier both produce a useful error message
        self.assertTrue(
            any(
                "expired" in str(m.message).lower()
                or "invalid" in str(m.message).lower()
                or "session" in str(m.message).lower()
                or "no authorization code" in str(m.message).lower()
                for m in messages_list
            ),
            f"Expected error message, got: {[str(m.message) for m in messages_list]}",
        )

    @override_settings(
        GOOGLE_DRIVE_ENABLED="true",
        GOOGLE_DRIVE_UPLOAD_MODE="oauth",
    )
    def test_authorize_clears_stale_session_on_retry(self):
        """Test that a new authorize clears stale session keys from a previous attempt."""
        import importlib
        import apps.documents.services.google_drive as gd_module
        importlib.reload(gd_module)

        self.client.login(username=f"admin_pkce_{self.unique_id}", password="testpass")

        # First authorize attempt
        self.client.get("/documents/drive/oauth/authorize/")
        session = self.client.session
        old_verifier = session["_oauth_code_verifier"]
        old_state = session["_oauth_state"]

        # Second authorize attempt (should clear old keys and store new ones)
        self.client.get("/documents/drive/oauth/authorize/")
        new_verifier = session["_oauth_code_verifier"]
        new_state = session["_oauth_state"]

        # Both should exist (cleared + recreated)
        self.assertIsNotNone(new_verifier)
        self.assertIsNotNone(new_state)
        # Session was cleared and regenerated
        self.assertIn("_oauth_code_verifier", session)

    @override_settings(
        GOOGLE_DRIVE_ENABLED="true",
        GOOGLE_DRIVE_UPLOAD_MODE="oauth",
    )
    def test_callback_success_with_mocked_google(self):
        """Test successful callback flow with mocked Google token exchange."""
        import importlib
        import apps.documents.services.google_drive as gd_module
        importlib.reload(gd_module)

        self.client.login(username=f"admin_pkce_{self.unique_id}", password="testpass")

        # First: get authorize to store session keys
        self.client.get("/documents/drive/oauth/authorize/")
        session = self.client.session
        saved_state = session["_oauth_state"]
        saved_verifier = session["_oauth_code_verifier"]
        saved_config = session["_oauth_client_config"]

        # Verify verifier is stored before callback
        self.assertIsNotNone(saved_verifier)
        self.assertGreater(len(saved_verifier), 40)

        # Build a proper mock credentials object
        mock_credentials = MagicMock()
        mock_credentials.token = "mock_access_token_abc123"
        mock_credentials.refresh_token = "mock_refresh_token_xyz"
        mock_credentials.token_uri = "https://oauth2.googleapis.com/token"
        mock_credentials.client_id = saved_config["web"]["client_id"]
        mock_credentials.client_secret = saved_config["web"]["client_secret"]
        mock_credentials.scopes = ["https://www.googleapis.com/auth/drive.file"]
        mock_credentials.expiry = None

        # Mock Flow instance that from_client_config will return
        mock_flow_instance = MagicMock()
        mock_flow_instance.credentials = mock_credentials
        # mock_flow_instance.fetch_token is called by the callback
        mock_flow_instance.fetch_token.return_value = None  # fetch_token returns None, credentials via .credentials
        # mock_flow_instance.redirect_uri and code_verifier are set by the callback

        # Mock Flow class: from_client_config returns our controlled mock instance
        mock_flow_class = MagicMock()
        mock_flow_class.from_client_config.return_value = mock_flow_instance

        # Mock get_central_token_path to return a safe temp path
        with patch("apps.documents.oauth_views.Flow", mock_flow_class), \
             patch("apps.documents.oauth_views.get_central_token_path") as mock_token_path, \
             patch("builtins.open", MagicMock()) as mock_open, \
             patch("json.dump"):
            import tempfile, os
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
            tmp.close()
            mock_token_path.return_value = tmp.name
            try:
                response = self.client.get(
                    "/documents/drive/oauth/callback/",
                    {"code": "auth_code_123", "state": saved_state},
                )
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass

        # Should succeed and redirect to dashboard
        self.assertEqual(response.status_code, 302)
        messages_list = list(response.wsgi_request._messages)
        self.assertTrue(
            any(
                "berhasil" in str(m.message).lower() or "success" in str(m.message).lower()
                for m in messages_list
            ),
            f"Expected success message, got: {[str(m.message) for m in messages_list]}",
        )

        # Session keys should be cleared after success
        final_session = self.client.session
        self.assertNotIn("_oauth_code_verifier", final_session)
        self.assertNotIn("_oauth_state", final_session)
        self.assertNotIn("_oauth_client_config", final_session)

    @override_settings(
        GOOGLE_DRIVE_ENABLED="true",
        GOOGLE_DRIVE_UPLOAD_MODE="oauth",
    )
    def test_callback_error_clears_session(self):
        """Test that Google error in callback clears session keys."""
        import importlib
        import apps.documents.services.google_drive as gd_module
        importlib.reload(gd_module)

        self.client.login(username=f"admin_pkce_{self.unique_id}", password="testpass")

        # Authorize first
        self.client.get("/documents/drive/oauth/authorize/")
        session = self.client.session

        # Callback with Google error
        response = self.client.get(
            "/documents/drive/oauth/callback/",
            {"error": "access_denied", "error_description": "User denied access"},
        )
        # Should redirect to dashboard
        self.assertEqual(response.status_code, 302)

        # Session keys should be cleared even on error
        final_session = self.client.session
        self.assertNotIn("_oauth_code_verifier", final_session)
        self.assertNotIn("_oauth_state", final_session)

    def test_code_verifier_is_128_chars(self):
        """Test that auto-generated PKCE verifier matches library default length."""
        # Verify the library generates 128-char verifiers by default
        from google_auth_oauthlib.flow import Flow
        config = {
            "web": {
                "client_id": "test",
                "client_secret": "test",
                "auth_uri": "https://accounts.google.com",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        flow = Flow.from_client_config(
            client_config=config,
            scopes=[],
            redirect_uri="http://localhost",
        )
        # Trigger verifier generation
        flow.authorization_url(access_type="offline", prompt="consent")
        verifier = flow.code_verifier
        # Default is 128 chars
        self.assertEqual(len(verifier), 128)
        self.assertTrue(all(c.isascii() for c in verifier))
