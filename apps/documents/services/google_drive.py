"""
Google Drive Archive Service - Central Archive with Auto Root Folder.

Mendukung dua mode authentication:
1. SERVICE_ACCOUNT - Upload dengan Service Account (application-level)
2. OAUTH - Upload dengan SATU akun pusat INTERMILAN (admin authorize)

ROOT FOLDER STRATEGY:
1. GOOGLE_DRIVE_ROOT_FOLDER_ID diset → gunakan langsung
2. Kosong → app buat folder otomatis via Drive API
3. Folder ID disimpan di media/drive_tokens/archive_folder_id.json
4. Folder creation idempotent (cari dulu, baru buat jika tidak ada)

ARCHITECTURE: Central Archive
- HANYA satu koneksi Drive untuk seluruh aplikasi
- HANYA admin/superuser boleh authorize
- Operator cukup upload tanpa perlu login Google
- Token central: media/drive_tokens/archive_oauth.json
"""

import httplib2
import io
import json
import logging
import mimetypes
import os
import shutil
import socket
from pathlib import Path
from typing import Optional

from django.conf import settings
from django.utils import timezone

from apps.documents.models import DocumentDriveLink

logger = logging.getLogger("documents.drive")


# =============================================================================
# MODE DETECTION
# =============================================================================

def get_drive_mode() -> str:
    """
    Deteksi mode Google Drive yang aktif.

    Returns:
        'service_account', 'oauth', atau 'disabled'
    """
    enabled = os.environ.get("GOOGLE_DRIVE_ENABLED", "false").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return "disabled"

    mode = os.environ.get("GOOGLE_DRIVE_UPLOAD_MODE", "service_account").strip().lower()
    return mode


def drive_enabled() -> bool:
    """Apakah Google Drive integration aktif."""
    return get_drive_mode() != "disabled"


def oauth_enabled() -> bool:
    """Apakah OAuth mode aktif."""
    return get_drive_mode() == "oauth"


def service_account_enabled() -> bool:
    """Apakah Service Account mode aktif."""
    return get_drive_mode() == "service_account"


# =============================================================================
# ROOT FOLDER MANAGEMENT
# =============================================================================

def _get_root_folder_id_from_config() -> Optional[str]:
    """
    Ambil root folder ID dari environment variable.

    Returns:
        Folder ID jika diset, None jika kosong
    """
    folder_id = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "").strip()
    return folder_id if folder_id else None


def _get_root_folder_id_path() -> Path:
    """Path ke file folder ID central."""
    from apps.documents.services.google_oauth import get_root_folder_id_path as get_path
    return get_path()


def _get_root_folder_name() -> str:
    """Nama folder root archive."""
    from apps.documents.services.google_oauth import get_root_folder_name as get_name
    return get_name()


def _save_root_folder_id(folder_id: str) -> None:
    """Simpan folder ID ke file."""
    folder_path = _get_root_folder_id_path()
    with open(folder_path, "w") as f:
        json.dump({"folder_id": folder_id}, f)


def _get_saved_root_folder_id() -> Optional[str]:
    """Ambil folder ID dari file yang sudah disimpan."""
    folder_path = _get_root_folder_id_path()
    if folder_path.exists():
        try:
            with open(folder_path, "r") as f:
                data = json.load(f)
            return data.get("folder_id")
        except (json.JSONDecodeError, IOError):
            return None
    return None


def _get_or_create_root_folder(service, timeout: int = None) -> str:
    """
    Dapatkan atau buat root folder archive.

    Idempotent: cari dulu, buat jika tidak ada.

    Args:
        service: Google Drive API service

    Returns:
        Folder ID
    """
    folder_name = _get_root_folder_name()

    # Check if already saved
    saved_id = _get_saved_root_folder_id()
    if saved_id:
        # Verify folder still exists
        try:
            service.files().get(fileId=saved_id, fields="id").execute(timeout=timeout)
            return saved_id  # Folder exists
        except Exception:
            # Folder no longer exists, recreate
            pass

    # Search for existing folder
    results = service.files().list(
        q=f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        spaces="drive",
        fields="files(id, name)",
        pageSize=10,
    ).execute(timeout=timeout)

    folders = results.get("files", [])

    if folders:
        folder_id = folders[0]["id"]
        _save_root_folder_id(folder_id)
        return folder_id

    # Create new folder
    folder_metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }

    folder = service.files().create(
        body=folder_metadata,
        fields="id",
    ).execute(timeout=timeout)

    folder_id = folder.get("id")
    _save_root_folder_id(folder_id)

    return folder_id


def _ensure_root_folder(service, timeout: int = None) -> Optional[str]:
    """
    Ensure root folder exists, return folder ID or None.

    Handles both config-based and auto-created folders.

    Args:
        service: Google Drive API service
        timeout: Optional socket timeout in seconds.

    Returns:
        Folder ID or None if error
    """
    # Check if configured
    config_folder_id = _get_root_folder_id_from_config()
    if config_folder_id:
        return config_folder_id

    # Auto-create/retrieve
    try:
        return _get_or_create_root_folder(service, timeout=timeout)
    except Exception:
        return None


# =============================================================================
# SERVICE ACCOUNT UPLOAD
# =============================================================================

def _get_service_account_credentials():
    """
    Ambil Service Account credentials.

    Returns:
        google.oauth2.service_account.Credentials

    Raises:
        Exception: Jika credential tidak tersedia
    """
    from google.oauth2 import service_account

    service_account_file = os.environ.get("GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE", "").strip()
    credentials_json = os.environ.get("GOOGLE_DRIVE_CREDENTIALS_JSON", "").strip()

    if not service_account_file and not credentials_json:
        raise ValueError("Service Account credential not configured")

    scopes = ["https://www.googleapis.com/auth/drive.file"]

    if credentials_json:
        info = json.loads(credentials_json)
        credentials = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    else:
        credentials = service_account.Credentials.from_service_account_file(
            service_account_file, scopes=scopes
        )

    return credentials


def build_drive_service(credentials, timeout=None):
    """
    Build Google Drive service with given credentials.

    Args:
        credentials: google-auth credentials object
        timeout: Optional socket timeout in seconds. If provided, the underlying HTTP
            client will raise socket.timeout after this many seconds.  This bounds
            the Drive API call for user-facing requests and prevents Cloudflare 524
            timeouts from being triggered by a slow Drive upload.
    """
    from googleapiclient.discovery import build

    # Modern google-auth: pass credentials directly, don't use deprecated authorize()
    http = None
    if timeout:
        import httplib2
        http = httplib2.Http(timeout=timeout)
        http = credentials.authorize(http)
    return build("drive", "v3", credentials=credentials, http=http, cache_discovery=False)


def _upload_service_account(file_path: str, display_name: str = None,
                            mime_type: str = None, timeout: int = None):
    """
    Upload file menggunakan Service Account.

    Args:
        file_path: Path ke file yang diupload
        display_name: Nama file di Drive
        mime_type: MIME type file
        timeout: Optional socket timeout in seconds.

    Returns:
        Result dict dengan status, file_id, web_view_link, etc.
    """
    from googleapiclient.http import MediaFileUpload

    credentials = _get_service_account_credentials()
    service = build_drive_service(credentials, timeout=timeout)

    guessed_mime = mime_type or mimetypes.guess_type(file_path)[0] or "application/octet-stream"

    # Get or create root folder
    root_folder_id = _ensure_root_folder(service, timeout=timeout)

    metadata = {"name": display_name or os.path.basename(file_path)}
    if root_folder_id:
        metadata["parents"] = [root_folder_id]

    media = MediaFileUpload(file_path, mimetype=guessed_mime, resumable=False)

    created = service.files().create(
        body=metadata,
        media_body=media,
        fields="id, webViewLink, mimeType, size",
    ).execute()

    return {
        "status": "uploaded",
        "file_id": created.get("id", ""),
        "web_view_link": created.get("webViewLink", ""),
        "local_path": "",
        "mime_type": created.get("mimeType", guessed_mime),
        "size": int(created.get("size") or os.path.getsize(file_path)),
        "error_message": "",
        "upload_mode": "service_account",
        "folder_id": root_folder_id,
    }


# =============================================================================
# OAUTH UPLOAD (CENTRAL ARCHIVE)
# =============================================================================

def _get_central_oauth_credentials():
    """
    Ambil OAuth credentials dari central token.

    Returns:
        google.oauth2.credentials.Credentials

    Raises:
        FileNotFoundError: Jika token tidak ditemukan
        OAuthRefreshFailed: Jika refresh gagal
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    from apps.documents.services.google_oauth import (
        OAuthTokenNotFound,
        OAuthRefreshFailed,
        get_central_token_path,
    )

    token_path = get_central_token_path()

    if not token_path.exists():
        raise FileNotFoundError("Central OAuth token not found. Admin perlu authorize terlebih dahulu.")

    with open(token_path, "r") as f:
        token_data = json.load(f)

    credentials = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes"),
    )

    # Refresh if expired
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        # Save refreshed token
        token_data["token"] = credentials.token
        token_data["expiry"] = credentials.expiry.isoformat() if credentials.expiry else None
        with open(token_path, "w") as f:
            json.dump(token_data, f, indent=2)

    return credentials


def _upload_oauth_central(file_path: str, display_name: str = None,
                           mime_type: str = None, timeout: int = None):
    """
    Upload file menggunakan central OAuth credentials.

    Args:
        file_path: Path ke file yang diupload
        display_name: Nama file di Drive
        mime_type: MIME type file
        timeout: Optional socket timeout in seconds.

    Returns:
        Result dict dengan status, file_id, web_view_link, etc.
    """
    from googleapiclient.http import MediaFileUpload

    credentials = _get_central_oauth_credentials()
    service = build_drive_service(credentials, timeout=timeout)

    guessed_mime = mime_type or mimetypes.guess_type(file_path)[0] or "application/octet-stream"

    # Get or create root folder
    root_folder_id = _ensure_root_folder(service, timeout=timeout)

    metadata = {"name": display_name or os.path.basename(file_path)}
    if root_folder_id:
        metadata["parents"] = [root_folder_id]

    media = MediaFileUpload(file_path, mimetype=guessed_mime, resumable=False)

    created = service.files().create(
        body=metadata,
        media_body=media,
        fields="id, webViewLink, mimeType, size",
    ).execute()

    return {
        "status": "uploaded",
        "file_id": created.get("id", ""),
        "web_view_link": created.get("webViewLink", ""),
        "local_path": "",
        "mime_type": created.get("mimeType", guessed_mime),
        "size": int(created.get("size") or os.path.getsize(file_path)),
        "error_message": "",
        "upload_mode": "oauth",
        "folder_id": root_folder_id,
    }


# =============================================================================
# MAIN UPLOAD FUNCTION
# =============================================================================

def upload_file_to_drive(file_path: str, display_name: str = None,
                        mime_type: str = None, timeout: int = None) -> dict:
    """
    Upload file ke Google Drive central archive.

    Mode upload ditentukan oleh GOOGLE_DRIVE_UPLOAD_MODE:
    - 'oauth': Upload dengan central OAuth credentials (admin authorize)
    - 'service_account': Upload dengan Service Account

    ROOT FOLDER STRATEGY:
    1. GOOGLE_DRIVE_ROOT_FOLDER_ID diset → gunakan langsung
    2. Kosong → app buat folder otomatis

    Jika mode tidak aktif atau gagal:
    - Archive ke local storage (media/archive/documents/)
    - Tetap return dict dengan status yang sesuai

    Args:
        file_path: Path ke file yang diupload
        display_name: Nama file di Drive
        mime_type: MIME type file
        timeout: Optional socket timeout in seconds for the Drive API call.
            If set, the upload fails fast with status 'timeout' instead of
            blocking for the Cloudflare/gunicorn timeout.  Recommended for
            user-facing requests (e.g. manual document upload).

    Returns:
        Result dict:
        - status: 'uploaded', 'local_archived', 'disabled', 'missing_credentials', 'failed', 'timeout'
        - file_id: Google Drive file ID (jika uploaded)
        - web_view_link: Google Drive URL
        - local_path: Local archive path (jika local_archived)
        - folder_id: Root folder ID yang digunakan
        - error_message: Error message jika gagal
    """
    logger.info("[DRIVE] upload_file_to_drive path=%s display=%s mime=%s", file_path, display_name, mime_type)
    if not drive_enabled():
        archive = archive_file_locally(file_path, display_name=display_name)
        logger.info("[DRIVE] disabled — local archived to=%s", archive["path"])
        return {
            "status": "disabled",
            "file_id": "",
            "web_view_link": archive["url"],
            "local_path": archive["path"],
            "mime_type": mime_type or mimetypes.guess_type(file_path)[0] or "",
            "size": os.path.getsize(file_path) if os.path.exists(file_path) else 0,
            "folder_id": None,
            "error_message": "Google Drive belum aktif. File disimpan ke local archive.",
        }

    mode = get_drive_mode()
    logger.info("[DRIVE] mode=%s", mode)

    try:
        if mode == "oauth":
            result = _upload_oauth_central(file_path, display_name, mime_type, timeout=timeout)
        else:
            # Default to service_account
            result = _upload_service_account(file_path, display_name, mime_type, timeout=timeout)
        logger.info(
            "[DRIVE] uploaded status=%s file_id=%s folder=%s",
            result["status"], result.get("file_id", "")[:20], result.get("folder_id", ""),
        )
        return result

    except socket.timeout:
        # Drive was too slow — local archive is already saved by _create_drive_link_placeholder.
        # Mark status truthfully so the UI shows "Drive pending/failed".
        logger.warning("[DRIVE] socket timeout after %ss — Drive upload timed out, local link preserved.", timeout)
        return {
            "status": "timeout",
            "file_id": "",
            "web_view_link": "",
            "local_path": "",
            "mime_type": mime_type or mimetypes.guess_type(file_path)[0] or "",
            "size": os.path.getsize(file_path) if os.path.exists(file_path) else 0,
            "folder_id": None,
            "error_message": f"Google Drive tidak merespon dalam {timeout} detik. File tersimpan lokal.",
        }
    except FileNotFoundError as e:
        # Central OAuth token not found
        logger.warning("[DRIVE] FileNotFoundError (missing_credentials): %s", e)
        archive = archive_file_locally(file_path, display_name=display_name)
        return {
            "status": "missing_credentials",
            "file_id": "",
            "web_view_link": "",
            "local_path": archive["path"],
            "mime_type": mime_type or mimetypes.guess_type(file_path)[0] or "",
            "size": os.path.getsize(file_path) if os.path.exists(file_path) else 0,
            "folder_id": None,
            "error_message": f"Central OAuth token not found. Admin perlu authorize Drive terlebih dahulu. ({e})",
        }

    except Exception as exc:
        # General error - fallback to local
        logger.warning("[DRIVE] upload failed: %s — falling back to local archive", exc)
        archive = archive_file_locally(file_path, display_name=display_name)
        return {
            "status": "failed",
            "file_id": "",
            "web_view_link": "",
            "local_path": archive["path"],
            "mime_type": mime_type or mimetypes.guess_type(file_path)[0] or "",
            "size": os.path.getsize(file_path) if os.path.exists(file_path) else 0,
            "folder_id": None,
            "error_message": str(exc),
        }


# =============================================================================
# LOCAL ARCHIVE FALLBACK
# =============================================================================

def archive_file_locally(file_path: str, display_name: str = None) -> dict:
    """
    Archive file ke local storage sebagai fallback.

    Args:
        file_path: Path ke file
        display_name: Nama file untuk archive

    Returns:
        dict dengan path dan url
    """
    if not file_path or not os.path.exists(file_path):
        return {"path": "", "url": ""}

    now = timezone.localtime()
    archive_dir = Path(settings.MEDIA_ROOT) / "archive" / "documents" / f"{now:%Y}" / f"{now:%m}"
    archive_dir.mkdir(parents=True, exist_ok=True)

    safe_name = os.path.basename(display_name or file_path)
    target = archive_dir / safe_name

    # Handle duplicate names
    counter = 1
    while target.exists():
        target = archive_dir / f"{target.stem}_{counter}{target.suffix}"
        counter += 1

    shutil.copy2(file_path, target)

    try:
        relative = target.relative_to(settings.MEDIA_ROOT).as_posix()
    except ValueError:
        relative = target.name

    return {"path": str(target), "url": f"{settings.MEDIA_URL}{relative}"}


# =============================================================================
# MAIN ARCHIVE FUNCTION (for DocumentDriveLink)
# =============================================================================

def archive_file_link(
    file_path: str,
    user=None,
    jenis_dokumen: str = "",
    nama_file: str = "",
    satker_code: str = "",
    nomor_spm: str = "",
    no_drpp: str = "",
    no_kuitansi: str = "",
    catatan_extra: str = "",
    transaction_detail=None,
    force_upload: bool = False,
    existing_link=None,
    timeout: int = None,
) -> tuple:
    """
    Upload file ke Google Drive dan buat DocumentDriveLink dengan DUPLICATE PROTECTION.

    DUPLICATE PROTECTION ENABLED (unless existing_link is provided):
    1. Calculate SHA256 hash of file
    2. Search existing DocumentDriveLink by hash, no_drpp, or no_kuitansi
    3. If found → reuse existing link (no upload)
    4. If not found → upload new

    Args:
        file_path: Path ke file yang diupload
        user: Django user object (unused for central archive, kept for API compatibility)
        jenis_dokumen: Jenis dokumen (DRPP, KUITANSI, etc)
        nama_file: Nama file
        satker_code: Kode satker
        nomor_spm: Nomor SPM
        no_drpp: Nomor DRPP
        no_kuitansi: Nomor Kuitansi
        catatan_extra: Catatan tambahan
        transaction_detail: TransactionDetail object (optional)
        force_upload: Jika True, abaikan duplicate dan upload ulang

    Returns:
        Tuple (result_dict, DocumentDriveLink_object, is_reused)
    """
    logger.info(
        "[DRIVE ARCHIVE] start file=%s jenis=%s satker=%s spm=%s drpp=%s",
        file_path, jenis_dokumen, satker_code, nomor_spm, no_drpp,
    )
    # Step 1: Dedup — skip if caller already provided existing_link (placeholder) or force_upload
    from apps.documents.services.google_drive_dedup import (
        calculate_file_hash,
        find_existing_drive_link,
    )

    file_hash = calculate_file_hash(file_path)
    is_reused = False

    if not existing_link and not force_upload:
        existing_link = find_existing_drive_link(
            file_hash=file_hash,
            file_path=file_path,
            satker_code=satker_code,
            nomor_spm=nomor_spm,
            no_drpp=no_drpp,
            no_kuitansi=no_kuitansi,
        )
        if existing_link:
            is_reused = True
            if transaction_detail and not existing_link.transaction_detail:
                existing_link.transaction_detail = transaction_detail
                existing_link.save(update_fields=["transaction_detail", "updated_at"])
            return (
                {"status": "reused", "web_view_link": existing_link.google_drive_url,
                 "file_id": "", "local_path": "", "mime_type": "", "size": 0,
                 "folder_id": None, "error_message": "", "is_duplicate": True,
                 "existing_link_id": existing_link.id, "file_hash": file_hash},
                existing_link, True,
            )

    # Step 2: Upload to Drive or archive locally
    result = upload_file_to_drive(
        file_path=file_path,
        display_name=nama_file or os.path.basename(file_path),
        timeout=timeout,
    )

    # Step 3: Determine status
    status = DocumentDriveLink.Status.AKTIF if result["status"] == "uploaded" else DocumentDriveLink.Status.PERLU_DICEK

    # Step 4: Build catatan
    folder_note = f"folder_id={result.get('folder_id', '')}; " if result.get('folder_id') else ""
    catatan = (
        f"drive_status={result['status']}; "
        f"file_id={result['file_id']}; "
        f"hash={file_hash}; "
        f"{folder_note}"
        f"local_path={result.get('local_path', '')}; "
        f"size={result['size']}; "
        f"{result['error_message']}"
    )
    if catatan_extra:
        catatan = f"{catatan}; {catatan_extra}"

    # Step 5: If caller provided an existing placeholder, update it instead of creating a new link.
    # This handles the retry-after-failure scenario: the placeholder URL is empty, Drive now succeeds,
    # we find the placeholder by the new Drive URL and update it rather than creating a duplicate.
    new_url = result["web_view_link"] or result.get("local_path", "") or ""
    if existing_link is not None:
        # existing_link here is the placeholder passed by link_followup_document (not found by dedup)
        # Check if a Drive URL already exists for this Drive file to avoid duplicates on retry
        preexisting = (
            DocumentDriveLink.objects.filter(google_drive_url=new_url).exclude(pk=existing_link.pk).first()
            if new_url.startswith("https://drive.google.com") else None
        )
        if preexisting:
            logger.info("[DRIVE ARCHIVE] found preexisting Drive link id=%s for url=%s", preexisting.id, new_url[:60])
            link = preexisting
            link.google_drive_url = new_url
        else:
            link = existing_link
            link.google_drive_url = new_url
        link.status = status
        link.catatan = catatan[:2000]
        link.save(update_fields=["google_drive_url", "status", "catatan", "updated_at"])
        if transaction_detail and not link.transaction_detail_id:
            link.transaction_detail = transaction_detail
            link.save(update_fields=["transaction_detail", "updated_at"])
        logger.info("[DRIVE ARCHIVE] updated placeholder id=%s url=%s status=%s", link.id, new_url[:60] if new_url else "(empty)", status)
        return (
            {"status": result["status"], "web_view_link": new_url, "file_id": result.get("file_id", ""),
             "local_path": result.get("local_path", ""), "mime_type": result.get("mime_type", ""),
             "size": result.get("size", 0), "folder_id": result.get("folder_id"), "error_message": result.get("error_message", ""),
             "is_duplicate": False, "file_hash": file_hash},
            link, False,
        )

    # Step 5b: No placeholder — create new link normally
    link = DocumentDriveLink.objects.create(
        transaction_detail=transaction_detail,
        satker_code=satker_code or "",
        nomor_spm=nomor_spm or "",
        no_kuitansi=no_kuitansi or "",
        no_drpp=no_drpp or "",
        jenis_dokumen=jenis_dokumen or "",
        nama_file=nama_file or os.path.basename(file_path),
        google_drive_url=new_url,
        status=status,
        catatan=catatan[:2000],
        created_by=user,
    )
    logger.info(
        "[DRIVE ARCHIVE] done status=%s link_id=%s drive_url=%s is_duplicate=%s",
        result["status"], link.id, new_url[:60] if new_url else "(empty)", False,
    )
    return (
        {"status": result["status"], "web_view_link": new_url, "file_id": result.get("file_id", ""),
         "local_path": result.get("local_path", ""), "mime_type": result.get("mime_type", ""),
         "size": result.get("size", 0), "folder_id": result.get("folder_id"),
         "error_message": result.get("error_message", ""), "is_duplicate": False, "file_hash": file_hash},
        link, False,
    )


# =============================================================================
# HELPER: CHECK OAUTH STATUS
# =============================================================================

def check_central_oauth_status() -> dict:
    """
    Cek status central OAuth.

    Returns:
        dict dengan is_authorized, mode, error
    """
    if not oauth_enabled():
        return {
            "is_authorized": False,
            "mode": "service_account" if service_account_enabled() else "disabled",
            "error": None,
        }

    try:
        _get_central_oauth_credentials()
        return {
            "is_authorized": True,
            "mode": "oauth",
            "error": None,
        }
    except FileNotFoundError:
        return {
            "is_authorized": False,
            "mode": "oauth",
            "error": "Admin belum authorize Google Drive",
        }
    except Exception as e:
        return {
            "is_authorized": False,
            "mode": "oauth",
            "error": str(e),
        }
