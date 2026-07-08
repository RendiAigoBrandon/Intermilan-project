import io
import json
import mimetypes
import os
import shutil
from pathlib import Path

from django.utils import timezone

from django.conf import settings

from apps.documents.models import DocumentDriveLink


def drive_enabled():
    return os.environ.get("GOOGLE_DRIVE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def upload_file_to_drive(file_path, display_name=None, mime_type=None):
    if not drive_enabled():
        archive = archive_file_locally(file_path, display_name=display_name)
        return {
            "status": "local_archived" if archive["path"] else "disabled",
            "file_id": "",
            "web_view_link": archive["url"],
            "local_path": archive["path"],
            "mime_type": mime_type or mimetypes.guess_type(file_path)[0] or "",
            "size": os.path.getsize(file_path) if os.path.exists(file_path) else 0,
            "error_message": "Google Drive belum aktif; file disimpan ke local archive." if archive["path"] else "Google Drive belum dikonfigurasi. File belum diarsipkan ke Drive.",
        }

    root_folder_id = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "").strip()
    service_account_file = os.environ.get("GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE", "").strip()
    credentials_json = os.environ.get("GOOGLE_DRIVE_CREDENTIALS_JSON", "").strip()
    if not service_account_file and not credentials_json:
        return {
            "status": "missing_credentials",
            "file_id": "",
            "web_view_link": "",
            "local_path": "",
            "mime_type": mime_type or mimetypes.guess_type(file_path)[0] or "",
            "size": os.path.getsize(file_path) if os.path.exists(file_path) else 0,
            "error_message": "Credential Google Drive belum tersedia.",
        }

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except Exception as exc:
        return {
            "status": "missing_credentials",
            "file_id": "",
            "web_view_link": "",
            "local_path": "",
            "mime_type": mime_type or mimetypes.guess_type(file_path)[0] or "",
            "size": os.path.getsize(file_path) if os.path.exists(file_path) else 0,
            "error_message": f"Google API client belum tersedia: {exc}",
        }

    try:
        scopes = ["https://www.googleapis.com/auth/drive.file"]
        if credentials_json:
            info = json.loads(credentials_json)
            credentials = service_account.Credentials.from_service_account_info(info, scopes=scopes)
        else:
            credentials = service_account.Credentials.from_service_account_file(service_account_file, scopes=scopes)
        service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        guessed_mime = mime_type or mimetypes.guess_type(file_path)[0] or "application/octet-stream"
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
        }
    except Exception as exc:
        return {
            "status": "failed",
            "file_id": "",
            "web_view_link": "",
            "local_path": "",
            "mime_type": mime_type or mimetypes.guess_type(file_path)[0] or "",
            "size": os.path.getsize(file_path) if os.path.exists(file_path) else 0,
            "error_message": str(exc),
        }


def archive_file_locally(file_path, display_name=None):
    if not file_path or not os.path.exists(file_path):
        return {"path": "", "url": ""}
    now = timezone.localtime()
    archive_dir = Path(settings.MEDIA_ROOT) / "archive" / "documents" / f"{now:%Y}" / f"{now:%m}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    safe_name = os.path.basename(display_name or file_path)
    target = archive_dir / safe_name
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


def archive_file_link(file_path, user=None, jenis_dokumen="", nama_file="", satker_code="", nomor_spm="", no_drpp="", no_kuitansi="", catatan_extra="", transaction_detail=None):
    result = upload_file_to_drive(file_path, display_name=nama_file or os.path.basename(file_path))
    status = DocumentDriveLink.Status.AKTIF if result["status"] == "uploaded" else DocumentDriveLink.Status.PERLU_DICEK
    catatan = f"drive_status={result['status']}; file_id={result['file_id']}; local_path={result.get('local_path', '')}; size={result['size']}; {result['error_message']}"
    if catatan_extra:
        catatan = f"{catatan}; {catatan_extra}"
    link = DocumentDriveLink.objects.create(
        transaction_detail=transaction_detail,
        satker_code=satker_code or "",
        nomor_spm=nomor_spm or "",
        no_kuitansi=no_kuitansi or "",
        no_drpp=no_drpp or "",
        jenis_dokumen=jenis_dokumen or "",
        nama_file=nama_file or os.path.basename(file_path),
        google_drive_url=result["web_view_link"] or "",
        status=status,
        catatan=catatan[:2000],
        created_by=user,
    )
    return result, link
