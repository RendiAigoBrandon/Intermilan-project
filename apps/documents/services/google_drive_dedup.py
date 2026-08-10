"""
Google Drive Archive Service - Duplicate Protection.

Mendukung deteksi file duplikat berdasarkan:
1. File hash (SHA256)
2. Existing Google Drive file ID

Flow duplicate protection:
1. Hitung hash file
2. Cek apakah sudah ada DocumentDriveLink dengan hash sama
3. Jika ada dan Drive URL valid, reuse existing link
4. Jika tidak ada atau link invalid, upload baru

Keamanan:
- Hash dihitung dari file content
- Tidak ada upload duplikat ke Drive
- DocumentDriveLink tidak di-duplicate
"""

import hashlib
import os
from typing import Optional, Tuple

from django.conf import settings

from apps.documents.models import DocumentDriveLink


def calculate_file_hash(file_path: str) -> str:
    """
    Hitung SHA256 hash dari file.

    Args:
        file_path: Path ke file

    Returns:
        Hex string dari SHA256 hash
    """
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def find_existing_drive_link(
    file_hash: str = None,
    file_path: str = None,
    satker_code: str = None,
    nomor_spm: str = None,
    no_drpp: str = None,
    no_kuitansi: str = None,
) -> Optional[DocumentDriveLink]:
    """
    Cari DocumentDriveLink yang sudah ada untuk file yang sama.

    Prioritas pencarian:
    1. Match by file_hash + satker_code (exact match)
    2. Match by no_drpp + satker_code (for DRPP)
    3. Match by no_kuitansi + satker_code (for Kuitansi)

    Args:
        file_hash: SHA256 hash file
        file_path: Path ke file (akan di-hash jika file_hash tidak ada)
        satker_code: Kode satker
        nomor_spm: Nomor SPM
        no_drpp: Nomor DRPP
        no_kuitansi: Nomor Kuitansi

    Returns:
        DocumentDriveLink jika ditemukan, None jika tidak
    """
    # Calculate hash if not provided
    if not file_hash and file_path:
        file_hash = calculate_file_hash(file_path)

    # Build query
    queryset = DocumentDriveLink.objects.filter(
        google_drive_url__startswith="https://drive.google.com"
    )

    # Filter by satker
    if satker_code:
        queryset = queryset.filter(satker_code=satker_code)

    # Priority 1: Match by hash
    if file_hash:
        existing = queryset.filter(catatan__icontains=f"hash={file_hash}").first()
        if existing:
            return existing

    # Priority 2: Match by DRPP number
    if no_drpp:
        existing = queryset.filter(no_drpp=no_drpp).first()
        if existing and existing.google_drive_url:
            return existing

    # Priority 3: Match by Kuitansi number
    if no_kuitansi:
        existing = queryset.filter(no_kuitansi=no_kuitansi).first()
        if existing and existing.google_drive_url:
            return existing

    return None


def archive_file_with_dedup(
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
) -> Tuple[dict, DocumentDriveLink, bool]:
    """
    Archive file dengan duplicate protection.

    Args:
        file_path: Path ke file
        user: Django user (unused for central archive, kept for API compatibility)
        jenis_dokumen: Jenis dokumen
        nama_file: Nama file
        satker_code: Kode satker
        nomor_spm: Nomor SPM
        no_drpp: Nomor DRPP
        no_kuitansi: Nomor Kuitansi
        catatan_extra: Catatan tambahan
        transaction_detail: TransactionDetail object
        force_upload: Jika True, abaikan duplicate dan upload ulang

    Returns:
        Tuple (result_dict, DocumentDriveLink, is_reused)
        - result_dict: Hasil upload/drive operation
        - DocumentDriveLink: Link object
        - is_reused: True jika reuse existing link
    """
    from apps.documents.services.google_drive import (
        archive_file_link,
        upload_file_to_drive,
    )

    # Calculate hash
    file_hash = calculate_file_hash(file_path)

    # Check for existing link
    is_reused = False
    existing_link = None

    if not force_upload:
        existing_link = find_existing_drive_link(
            file_hash=file_hash,
            file_path=file_path,
            satker_code=satker_code,
            nomor_spm=nomor_spm,
            no_drpp=no_drpp,
            no_kuitansi=no_kuitansi,
        )

    if existing_link:
        # Reuse existing link
        is_reused = True

        # Update transaction_detail if provided
        if transaction_detail and not existing_link.transaction_detail:
            existing_link.transaction_detail = transaction_detail
            existing_link.save(update_fields=["transaction_detail", "updated_at"])

        result = {
            "status": "reused",
            "file_id": "",
            "web_view_link": existing_link.google_drive_url,
            "local_path": "",
            "mime_type": "",
            "size": 0,
            "error_message": "",
            "is_duplicate": True,
            "existing_link_id": existing_link.id,
        }

        return result, existing_link, is_reused

    # No existing link - archive new
    result, link, _ = archive_file_link(
        file_path=file_path,
        user=user,
        jenis_dokumen=jenis_dokumen,
        nama_file=nama_file,
        satker_code=satker_code,
        nomor_spm=nomor_spm,
        no_drpp=no_drpp,
        no_kuitansi=no_kuitansi,
        catatan_extra=f"hash={file_hash}; {catatan_extra}" if catatan_extra else f"hash={file_hash}",
        transaction_detail=transaction_detail,
    )

    result["is_duplicate"] = False
    result["file_hash"] = file_hash

    return result, link, is_reused
