import logging
import mimetypes
import os
import re
import shutil
import socket
from decimal import Decimal
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.core.files import File
from django.core.files.storage import FileSystemStorage
from django.db import transaction as db_transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date

from apps.accounts.access import (
    can_edit_satker,
    can_upload_document,
    filter_by_satker,
    get_user_satker_code,
    permission_context,
)
from apps.core.parsers import (
    classify_document,
    extract_pdf_text,
    normalized_bukti_key,
    parse_drpp_pdf,
    parse_paket_spm_zip,
    parse_spm_pdf,
)
from apps.core.drpp_batch_parser import _normalize_drpp as normalize_drpp_number
from apps.core.satker import get_satker_name_map
from apps.core.document_policy import (
    get_required_documents_for_akun_family,
    normalize_akun_family,
)
from apps.core.views import UPLOAD_COLUMNS, build_pagination_window, build_satker_options
from apps.dk.models import TransactionDetail
from apps.dk.services import refresh_transaction_document_status
from apps.drpp.models import DRPPItem, DRPPSupportingAttachment, DRPPUpload
from apps.sp2d.models import SP2DRaw
from apps.documents.services.checklist import mark_checklist_present as mark_checklist_present_service
from apps.documents.services.google_drive import archive_file_link, drive_enabled
from apps.documents.services.google_drive_dedup import calculate_file_hash

from .models import ChecklistStatus, ChecklistTemplate, DocumentDriveLink, DocumentUpload

logger = logging.getLogger("documents.views")

RECEIPT_DOCUMENT_TYPE = "Kuitansi"
RECEIPT_UPLOAD_FIELD = "receipt_files"
BLOCKED_UPLOAD_MIME_TYPES = {
    "application/x-msdownload",
    "application/x-msdos-program",
    "application/x-executable",
    "application/x-sh",
    "application/x-bat",
    "text/x-python",
}


def _is_valid_google_drive_url(value):
    try:
        parsed = urlsplit((value or "").strip())
        hostname = parsed.hostname
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and hostname in {
        "drive.google.com",
        "docs.google.com",
    }


def _extract_year(value):
    matches = re.findall(r"\b(19\d{2}|20\d{2})\b", str(value or ""))
    return int(matches[-1]) if matches else None


def _transaction_matches_drpp_year(transaction, tahun):
    if not tahun:
        return True
    if transaction.tanggal_spm:
        return transaction.tanggal_spm.year == tahun
    return str(tahun) in (transaction.no_drpp or "")


def _drpp_lookup_key(value):
    return normalize_drpp_number(value)


def _drpp_lookup_variants(value):
    return {
        item
        for item in {
            str(value or "").strip(),
            _drpp_lookup_key(value),
            normalized_bukti_key(value),
        }
        if item
    }


def _drpp_upload_matches_number(drpp_upload, nomor_drpp_norm):
    upload_keys = _drpp_lookup_variants(drpp_upload.nomor_drpp_norm) | _drpp_lookup_variants(drpp_upload.nomor_drpp)
    return _drpp_lookup_key(nomor_drpp_norm) in upload_keys


def _transactions_for_drpp_identity(user, satker_code, nomor_drpp_norm, tahun=None):
    if not satker_code or not nomor_drpp_norm:
        return []
    candidates = filter_by_satker(
        TransactionDetail.objects.select_related("sp2d_raw").filter(
            satker_code=satker_code,
        ).exclude(no_drpp=""),
        user,
    ).order_by("id")
    return [
        transaction
        for transaction in candidates
        if _drpp_lookup_key(transaction.no_drpp) == nomor_drpp_norm
        and _transaction_matches_drpp_year(transaction, tahun)
    ]


def _matching_drpp_uploads(user, satker_code, nomor_drpp_norm, tahun=None):
    queryset = filter_by_satker(
        DRPPUpload.objects.filter(satker_code=satker_code),
        user,
    )
    if tahun:
        queryset = queryset.filter(tahun=tahun)
    return [
        item
        for item in queryset.order_by("-uploaded_at", "-id")[:200]
        if _drpp_upload_matches_number(item, nomor_drpp_norm)
    ]


def _drpp_identity_for_transaction(transaction):
    nomor_drpp_norm = _drpp_lookup_key(transaction.no_drpp)
    if not nomor_drpp_norm or not transaction.satker_code:
        return "", None, ""
    tahun = (
        transaction.tanggal_spm.year
        if transaction.tanggal_spm
        else _extract_year(transaction.no_drpp)
    )
    return transaction.satker_code, tahun, nomor_drpp_norm


def _supporting_attachments_for_identity(user, drpp_upload=None, satker_code="", tahun=None, nomor_drpp_norm=""):
    query = Q(pk__in=[])
    if drpp_upload:
        query |= Q(drpp_upload=drpp_upload)
    if satker_code and tahun and nomor_drpp_norm:
        query |= Q(
            satker_code=satker_code,
            tahun=tahun,
            nomor_drpp_norm__in=_drpp_lookup_variants(nomor_drpp_norm),
        )
    if not query:
        return DRPPSupportingAttachment.objects.none()
    return filter_by_satker(
        DRPPSupportingAttachment.objects.filter(query),
        user,
    ).select_related(
        "document_upload",
        "uploaded_by",
        "archive_link",
    ).distinct()


def _supporting_attachments_for_transaction(transaction, user):
    satker_code, tahun, nomor_drpp_norm = _drpp_identity_for_transaction(transaction)
    matches = _matching_drpp_uploads(user, satker_code, nomor_drpp_norm, tahun)[:1]
    drpp_upload = matches[0] if matches else None
    return _supporting_attachments_for_identity(
        user,
        drpp_upload=drpp_upload,
        satker_code=satker_code,
        tahun=tahun,
        nomor_drpp_norm=nomor_drpp_norm,
    )


def _drpp_context(user, transactions, drpp_upload=None, satker_code="", tahun=None, nomor_drpp="", nomor_drpp_norm=""):
    transaction_years = sorted(
        {
            item.tanggal_spm.year if item.tanggal_spm else _extract_year(item.no_drpp)
            for item in transactions
            if item.tanggal_spm or _extract_year(item.no_drpp)
        }
    )
    if not tahun and len(transaction_years) > 1:
        return None, "DRPP ditemukan pada lebih dari satu tahun. Gunakan nomor DRPP lengkap beserta tahun."
    spm_numbers = sorted({(item.nomor_spm or "").strip() for item in transactions if item.nomor_spm})
    if len(spm_numbers) > 1:
        return None, "DRPP memiliki lebih dari satu Nomor SPM. Upload kuitansi dibatalkan agar tidak salah kait."
    satker_code = satker_code or (drpp_upload.satker_code if drpp_upload else "") or transactions[0].satker_code
    tahun = tahun or (drpp_upload.tahun if drpp_upload else None) or (transaction_years[0] if transaction_years else None)
    nomor_drpp_norm = _drpp_lookup_key(
        nomor_drpp_norm
        or (drpp_upload.nomor_drpp_norm if drpp_upload else "")
        or nomor_drpp
    )
    transaction_drpp_values = [(item.no_drpp or "").strip() for item in transactions if item.no_drpp]
    full_transaction_drpp = next((value for value in transaction_drpp_values if "/" in value), "")
    nomor_drpp = (
        nomor_drpp
        or full_transaction_drpp
        or (drpp_upload.nomor_drpp if drpp_upload else "")
        or (transaction_drpp_values[0] if transaction_drpp_values else "")
    )
    satker_name = get_satker_name_map([satker_code]).get(satker_code, "")
    attachments = _supporting_attachments_for_identity(
        user,
        drpp_upload=drpp_upload,
        satker_code=satker_code,
        tahun=tahun,
        nomor_drpp_norm=nomor_drpp_norm,
    )
    return {
        "drpp_upload": drpp_upload,
        "satker_code": satker_code,
        "satker_name": satker_name,
        "tahun": tahun,
        "nomor_drpp": nomor_drpp,
        "nomor_drpp_norm": nomor_drpp_norm,
        "nomor_spm": spm_numbers[0] if spm_numbers else (drpp_upload.nomor_spm if drpp_upload else ""),
        "transaction_count": len(transactions),
        "attachment_count": attachments.count(),
        "attachments": attachments,
    }, ""


def _resolve_drpp_context(user, satker_code="", nomor_drpp="", drpp_upload_id=None, for_upload=False):
    if drpp_upload_id:
        drpp_upload = DRPPUpload.objects.filter(pk=drpp_upload_id).first()
        if not drpp_upload:
            return None, "DRPP tidak ditemukan pada data D_K."
        if for_upload and not can_edit_satker(user, drpp_upload.satker_code):
            raise PermissionDenied("Anda tidak memiliki akses ke DRPP satker ini.")
        scoped = filter_by_satker(DRPPUpload.objects.all(), user).filter(pk=drpp_upload_id).first()
        if not scoped:
            return None, "DRPP tidak ditemukan pada data D_K."
        satker_code = drpp_upload.satker_code
        tahun = drpp_upload.tahun or _extract_year(drpp_upload.nomor_drpp)
        nomor_drpp = drpp_upload.nomor_drpp
        nomor_drpp_norm = _drpp_lookup_key(drpp_upload.nomor_drpp_norm or drpp_upload.nomor_drpp)
    else:
        nomor_drpp = (nomor_drpp or "").strip()
        satker_code = (satker_code or get_user_satker_code(user) or "").strip()
        if not satker_code:
            return None, "Pilih Satker terlebih dahulu."
        if not nomor_drpp:
            return None, "Masukkan No. DRPP terlebih dahulu."
        if for_upload and not can_edit_satker(user, satker_code):
            raise PermissionDenied("Anda tidak memiliki akses ke satker ini.")

        nomor_drpp_norm = _drpp_lookup_key(nomor_drpp)
        tahun = _extract_year(nomor_drpp)
        matches = _matching_drpp_uploads(user, satker_code, nomor_drpp_norm, tahun)
        drpp_upload = matches[0] if len(matches) == 1 else None

    if for_upload and not can_upload_document(user):
        raise PermissionDenied("Akun ini tidak memiliki akses upload dokumen.")

    transactions = _transactions_for_drpp_identity(user, satker_code, nomor_drpp_norm, tahun)
    if not transactions:
        return None, "DRPP tidak ditemukan pada data D_K."
    return _drpp_context(
        user,
        transactions,
        drpp_upload=drpp_upload,
        satker_code=satker_code,
        tahun=tahun,
        nomor_drpp=nomor_drpp,
        nomor_drpp_norm=nomor_drpp_norm,
    )


def _supporting_receipt_satker_options(user):
    scoped = filter_by_satker(TransactionDetail.objects.exclude(satker_code=""), user)
    return [
        {
            "code": item["satker_code"],
            "label": (
                f"{item['satker_code']} - {item['satker_name']}"
                if item.get("satker_name")
                else item["satker_code"]
            ),
        }
        for item in build_satker_options(scoped)
    ]


def _validate_receipt_uploads(upload_files):
    upload_error = validate_upload_batch(upload_files)
    if upload_error:
        return upload_error
    for upload_file in upload_files:
        content_type = (getattr(upload_file, "content_type", "") or "").lower()
        if content_type in BLOCKED_UPLOAD_MIME_TYPES:
            return f"Format file tidak didukung: {upload_file.name}"
    return ""


def _save_supporting_receipt_file(request, drpp_context, upload_file, tmp_path, file_hash):
    drpp_upload = drpp_context["drpp_upload"]
    if DRPPSupportingAttachment.objects.filter(
        satker_code=drpp_context["satker_code"],
        tahun=drpp_context["tahun"],
        nomor_drpp_norm__in=_drpp_lookup_variants(drpp_context["nomor_drpp_norm"]),
        document_upload__file_hash=file_hash,
    ).exists():
        return None, True

    document_upload = None
    attachment = None
    drive_result = {"status": "pending", "error_message": ""}
    try:
        with db_transaction.atomic():
            with open(tmp_path, "rb") as handle:
                document_upload = DocumentUpload(
                    transaction_detail=None,
                    document_type=RECEIPT_DOCUMENT_TYPE,
                    original_filename=upload_file.name,
                    stored_filename=upload_file.name,
                    file_hash=file_hash,
                    file_size=upload_file.size,
                    mime_type=upload_file.content_type or mimetypes.guess_type(upload_file.name)[0] or "",
                    uploaded_by=request.user,
                )
                document_upload.file.save(upload_file.name, File(handle), save=False)
                document_upload.stored_filename = document_upload.file.name
                document_upload.save()

            # Create DocumentDriveLink placeholder with empty google_drive_url
            # Drive URL will be filled after this atomic block
            archive_link = DocumentDriveLink.objects.create(
                transaction_detail=None,
                satker_code=drpp_context["satker_code"],
                nomor_spm=drpp_context["nomor_spm"] or "",
                no_kuitansi="",
                no_drpp=drpp_context["nomor_drpp"],
                jenis_dokumen=RECEIPT_DOCUMENT_TYPE,
                nama_file=upload_file.name,
                google_drive_url="",
                status=DocumentDriveLink.Status.PERLU_DICEK,
                catatan=(
                    f"source=DRPP supporting receipt; drpp_upload_id={drpp_upload.id if drpp_upload else 'None'}; "
                    f"document_upload_id={document_upload.id}; hash={file_hash}"
                )[:2000],
                created_by=request.user,
            )
            attachment = DRPPSupportingAttachment.objects.create(
                drpp_upload=drpp_upload,
                document_upload=document_upload,
                archive_link=archive_link,
                satker_code=drpp_context["satker_code"],
                tahun=drpp_context["tahun"],
                nomor_drpp=drpp_context["nomor_drpp"],
                nomor_drpp_norm=drpp_context["nomor_drpp_norm"],
                uploaded_by=request.user,
            )

        # ================================================================
        # OUTSIDE ATOMIC: Google Drive upload (network call — may be slow/fail)
        # Local save is already committed, so Drive failure is non-fatal.
        # ================================================================
        drive_result = _archive_receipt_to_drive(
            tmp_path,
            document_upload=document_upload,
            archive_link=archive_link,
            drpp_context=drpp_context,
            file_hash=file_hash,
            user=request.user,
        )

        if drive_result["status"] == "uploaded":
            logger.info(
                "[RECEIPT DRIVE] uploaded satker=%s drpp=%s file=%s",
                drpp_context["satker_code"], drpp_context["nomor_drpp"], upload_file.name,
            )
        elif drive_result["status"] in {"failed", "timeout", "missing_credentials"}:
            logger.warning(
                "[RECEIPT DRIVE] %s satker=%s drpp=%s file=%s: %s",
                drive_result["status"], drpp_context["satker_code"],
                drpp_context["nomor_drpp"], upload_file.name,
                drive_result.get("error_message", ""),
            )

        return attachment, False
    except Exception:
        if document_upload and document_upload.file:
            document_upload.file.delete(save=False)
        raise


def _archive_receipt_to_drive(file_path, document_upload, archive_link, drpp_context, file_hash, user):
    """
    Upload a receipt file to Google Drive and update the DocumentDriveLink in-place.

    Returns drive_result dict with status, web_view_link, etc.
    """
    try:
        drive_result, updated_link, is_reused = archive_file_link(
            file_path,
            user=user,
            jenis_dokumen=RECEIPT_DOCUMENT_TYPE,
            nama_file=document_upload.original_filename,
            satker_code=drpp_context["satker_code"],
            nomor_spm=drpp_context["nomor_spm"] or "",
            no_drpp=drpp_context["nomor_drpp"],
            no_kuitansi="",
            catatan_extra=(
                f"source=DRPP supporting receipt; "
                f"drpp_upload_id={drpp_context['drpp_upload'].id if drpp_context.get('drpp_upload') else 'None'}; "
                f"document_upload_id={document_upload.id}"
            ),
            transaction_detail=None,
            existing_link=archive_link,
            timeout=15,  # seconds — fail fast to prevent Cloudflare 524
        )

        # If archive_file_link returned an updated link, refresh from DB to get the saved URL
        if updated_link:
            archive_link.refresh_from_db()

        return drive_result
    except socket.timeout:
        logger.warning(
            "[RECEIPT DRIVE] socket timeout for file=%s — local link preserved.",
            document_upload.original_filename,
        )
        return {
            "status": "timeout",
            "error_message": "Google Drive tidak merespon dalam 15 detik.",
            "web_view_link": archive_link.google_drive_url if archive_link else "",
        }
    except Exception as exc:
        logger.warning(
            "[RECEIPT DRIVE] Failed for file=%s: %s — local link remains.",
            document_upload.original_filename, exc,
        )
        return {
            "status": "failed",
            "error_message": str(exc),
            "web_view_link": archive_link.google_drive_url if archive_link else "",
        }


@login_required
def archive(request):
    q = request.GET.get("q", "").strip()
    satker = request.GET.get("satker", "").strip()
    status = request.GET.get("status", "").strip()

    scoped_links = filter_by_satker(
        DocumentDriveLink.objects.select_related(
            "created_by",
            "drpp_supporting_attachment__document_upload",
            "drpp_supporting_attachment__uploaded_by",
        ),
        request.user,
    )
    satker_options = list(
        scoped_links.exclude(satker_code="")
        .values_list("satker_code", flat=True)
        .distinct()
        .order_by("satker_code")
    )
    links = scoped_links
    if q:
        links = links.filter(
            Q(nomor_spm__icontains=q)
            | Q(no_kuitansi__icontains=q)
            | Q(no_drpp__icontains=q)
            | Q(nama_file__icontains=q)
        )
    if satker:
        links = links.filter(satker_code=satker)
    valid_url_query = Q(
        google_drive_url__iregex=r"^https?://(drive|docs)\.google\.com(?:[/?#]|$)"
    )
    if status == DocumentDriveLink.Status.PERLU_DICEK:
        links = links.filter(Q(status=status) | ~valid_url_query)
    elif status:
        links = links.filter(status=status).filter(valid_url_query)

    paginator = Paginator(links.order_by("-created_at", "-id"), 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    for link in page_obj.object_list:
        link.archive_number = link.no_kuitansi or link.nomor_spm
        link.archive_url_valid = _is_valid_google_drive_url(link.google_drive_url)
        link.archive_status = link.status if link.archive_url_valid else DocumentDriveLink.Status.PERLU_DICEK
        link.supporting_attachment = getattr(link, "drpp_supporting_attachment", None)
        link.archive_uploaded_by = (
            link.supporting_attachment.uploaded_by
            if link.supporting_attachment
            else link.created_by
        )
        link.archive_uploaded_at = (
            link.supporting_attachment.created_at
            if link.supporting_attachment
            else link.created_at
        )

    query_params = request.GET.copy()
    query_params.pop("page", None)
    context = permission_context(request.user)
    context.update(
        {
            "page_title": "Arsip",
            "page_subtitle": "Daftar tautan arsip Google Drive dari data UPLOAD KK_1300.",
            "drive_links": page_obj,
            "page_obj": page_obj,
            "paginator": paginator,
            "pagination_window": build_pagination_window(page_obj),
            "base_querystring": query_params.urlencode(),
            "filters": {"q": q, "satker": satker, "status": status},
            "satker_options": satker_options,
            "status_options": DocumentDriveLink.Status.choices,
        }
    )
    return render(request, "documents/archive.html", context)


@login_required
def upload_kuitansi(request):
    selected_satker = (
        request.POST.get("satker")
        or request.GET.get("satker")
        or get_user_satker_code(request.user)
        or ""
    ).strip()
    nomor_drpp = (request.POST.get("no_drpp") or request.GET.get("no_drpp") or "").strip()
    drpp_upload_id = request.POST.get("drpp_upload_id") or request.GET.get("drpp_upload_id")
    drpp_context = None
    lookup_error = ""

    if request.method == "POST" and request.POST.get("action") == "upload_receipts":
        drpp_context, lookup_error = _resolve_drpp_context(
            request.user,
            satker_code=selected_satker,
            nomor_drpp=nomor_drpp,
            drpp_upload_id=drpp_upload_id,
            for_upload=True,
        )
        if drpp_context:
            upload_files = request.FILES.getlist(RECEIPT_UPLOAD_FIELD)
            upload_error = _validate_receipt_uploads(upload_files)
            if upload_error:
                messages.error(request, upload_error)
            else:
                tmp_dir = os.path.join(settings.MEDIA_ROOT, "tmp", "drpp_receipts")
                os.makedirs(tmp_dir, exist_ok=True)
                storage = FileSystemStorage(location=tmp_dir)
                processed = 0
                duplicates = 0
                drive_uploaded = 0
                drive_pending = 0
                for upload_file in upload_files:
                    tmp_name = storage.save(upload_file.name, upload_file)
                    tmp_path = storage.path(tmp_name)
                    try:
                        file_hash = calculate_file_hash(tmp_path)
                        _, duplicate = _save_supporting_receipt_file(
                            request,
                            drpp_context,
                            upload_file,
                            tmp_path,
                            file_hash,
                        )
                        if duplicate:
                            duplicates += 1
                        else:
                            processed += 1
                            # Check if Drive upload succeeded by examining the archive_link
                            # This is a simple heuristic - if Drive is enabled and no error, it worked
                            if drive_enabled():
                                drive_uploaded += 1
                            else:
                                drive_pending += 1
                    finally:
                        storage.delete(tmp_name)
                if processed:
                    messages.success(request, f"{processed} file kuitansi pendukung tersimpan.")
                if duplicates:
                    messages.info(request, f"{duplicates} file duplikat konten dilewati.")
                if drive_uploaded and drive_enabled():
                    messages.success(request, f"{drive_uploaded} file berhasil diarsipkan ke Google Drive.")
                elif drive_pending and not drive_enabled():
                    messages.warning(request, f"{drive_pending} file tersimpan lokal. Aktifkan Google Drive untuk arsip otomatis.")
                return redirect(
                    f"{reverse('documents:upload_kuitansi')}?{urlencode({'satker': drpp_context['satker_code'], 'no_drpp': drpp_context['nomor_drpp']})}"
                )
        else:
            messages.error(request, lookup_error or "DRPP tidak ditemukan pada data D_K.")

    elif request.GET:
        drpp_context, lookup_error = _resolve_drpp_context(
            request.user,
            satker_code=selected_satker,
            nomor_drpp=nomor_drpp,
            drpp_upload_id=drpp_upload_id,
        )

    context = permission_context(request.user)
    context.update(
        {
            "page_title": "Upload Kuitansi Pendukung",
            "page_subtitle": "Upload bukti kuitansi level DRPP tanpa OCR atau perubahan D_K.",
            "satker_options": _supporting_receipt_satker_options(request.user),
            "selected_satker": selected_satker,
            "no_drpp": nomor_drpp,
            "drpp_context": drpp_context,
            "lookup_error": lookup_error,
            "can_upload_receipt": can_upload_document(request.user),
            "receipt_upload_field": RECEIPT_UPLOAD_FIELD,
        }
    )
    return render(request, "documents/upload_kuitansi.html", context)


@login_required
def drpp_attachment_download(request, attachment_id):
    attachments = filter_by_satker(
        DRPPSupportingAttachment.objects.select_related("document_upload"),
        request.user,
    )
    attachment = get_object_or_404(attachments, pk=attachment_id)
    if not attachment.document_upload.file:
        raise Http404("File tidak ditemukan.")
    return FileResponse(
        attachment.document_upload.file.open("rb"),
        as_attachment=False,
        filename=attachment.document_upload.original_filename,
    )


@require_POST
@login_required
def sync_attachment_drive(request, attachment_id):
    """
    Sync an existing DRPP supporting attachment to Google Drive.

    Only syncs if:
    - Attachment has a local file
    - DocumentDriveLink has empty google_drive_url
    - File is not already in Drive (idempotent)
    """
    attachments = filter_by_satker(
        DRPPSupportingAttachment.objects.select_related(
            "document_upload", "archive_link", "drpp_upload"
        ),
        request.user,
    )
    attachment = get_object_or_404(attachments, pk=attachment_id)

    # Check if already synced
    archive_link = attachment.archive_link
    if not archive_link:
        messages.error(request, "Attachment tidak memiliki tautan arsip.")
        return redirect("documents:archive")

    if _is_valid_google_drive_url(archive_link.google_drive_url):
        messages.info(request, "File sudah tersinkron ke Google Drive. Tidak perlu sinkron ulang.")
        return redirect("documents:archive")

    # Check if local file exists
    if not attachment.document_upload.file:
        messages.error(request, "File lokal tidak ditemukan.")
        return redirect("documents:archive")

    file_path = attachment.document_upload.file.path
    if not os.path.exists(file_path):
        messages.error(request, "File fisik tidak ditemukan di server.")
        return redirect("documents:archive")

    # Check if already synced by another process (idempotency)
    # Re-fetch the link to get the latest state
    archive_link.refresh_from_db()
    if _is_valid_google_drive_url(archive_link.google_drive_url):
        messages.info(request, "File sudah tersinkron ke Google Drive.")
        return redirect("documents:archive")

    try:
        # Calculate file hash for dedup
        file_hash = calculate_file_hash(file_path)

        # Build drpp_context from attachment data
        drpp_context = {
            "drpp_upload": attachment.drpp_upload,
            "satker_code": attachment.satker_code,
            "tahun": attachment.tahun,
            "nomor_drpp": attachment.nomor_drpp,
            "nomor_drpp_norm": attachment.nomor_drpp_norm,
            "nomor_spm": archive_link.nomor_spm or "",
        }

        # Upload to Drive
        drive_result = _archive_receipt_to_drive(
            file_path=file_path,
            document_upload=attachment.document_upload,
            archive_link=archive_link,
            drpp_context=drpp_context,
            file_hash=file_hash,
            user=request.user,
        )

        if drive_result["status"] == "uploaded":
            messages.success(request, f"File '{attachment.document_upload.original_filename}' berhasil diarsipkan ke Google Drive.")
        elif drive_result["status"] == "reused":
            messages.info(request, f"File sudah ada di Google Drive (tidak di-upload ulang).")
        elif drive_result["status"] == "failed":
            messages.error(request, f"Sinkronisasi gagal: {drive_result.get('error_message', 'Unknown error')}. File tetap tersimpan lokal.")
        elif drive_result["status"] == "timeout":
            messages.warning(request, f"Google Drive tidak merespon dalam 15 detik. File tersimpan lokal. Coba lagi nanti.")
        elif drive_result["status"] == "disabled":
            messages.warning(request, "Google Drive belum aktif. File tersimpan lokal.")
        elif drive_result["status"] == "missing_credentials":
            messages.warning(request, "Credential Google Drive belum dikonfigurasi. File tersimpan lokal.")
        else:
            messages.warning(request, f"Status tidak diketahui: {drive_result['status']}. {drive_result.get('error_message', '')}")

    except Exception as exc:
        logger.exception("[SYNC DRIVE] Failed for attachment=%s: %s", attachment_id, exc)
        messages.error(request, f"Terjadi kesalahan saat sinkronisasi: {exc}. File tetap tersimpan lokal.")

    return redirect("documents:archive")


@login_required
def checklist_list(request):
    q = request.GET.get("q", "").strip()
    jenis = request.GET.get("jenis", "").strip()
    satker = request.GET.get("satker", "").strip()
    scoped_links = filter_by_satker(
        DocumentDriveLink.objects.select_related("created_by"),
        request.user,
    )
    links = scoped_links.order_by("-created_at")
    if q:
        links = links.filter(
            Q(nama_file__icontains=q)
            | Q(jenis_dokumen__icontains=q)
            | Q(nomor_spm__icontains=q)
            | Q(no_drpp__icontains=q)
            | Q(no_kuitansi__icontains=q)
            | Q(satker_code__icontains=q)
            | Q(catatan__icontains=q)
        )
    if jenis:
        links = links.filter(jenis_dokumen__iexact=jenis)
    if satker:
        links = links.filter(satker_code=satker)
    links = links[:100]
    uploads = filter_by_satker(
        DocumentUpload.objects.select_related("uploaded_by", "transaction_detail"),
        request.user,
        field_name="transaction_detail__satker_code",
    ).order_by("-uploaded_at")[:50]
    jenis_options = (
        scoped_links.exclude(jenis_dokumen="")
        .values_list("jenis_dokumen", flat=True)
        .distinct()
        .order_by("jenis_dokumen")[:50]
    )
    context = permission_context(request.user)
    context.update(
        {
            "page_title": "Checklist Dokumen & DRPP",
            "page_subtitle": "Cari arsip dokumen, link Google Drive, dan buka checklist dari halaman D_K.",
            "templates_count": ChecklistTemplate.objects.filter(is_active=True).count(),
            "filters": {"q": q, "jenis": jenis, "satker": satker},
            "drive_links": links,
            "uploads": uploads,
            "jenis_options": jenis_options,
        }
    )
    return render(request, "documents/checklist_entry.html", context)


@login_required
def checklist_detail(request, transaction_id):
    transactions = filter_by_satker(
        TransactionDetail.objects.select_related("sp2d_raw"),
        request.user,
    )
    transaction = get_object_or_404(transactions, pk=transaction_id)
    context_can_upload = can_upload_document(request.user, transaction)

    if request.method == "POST":
        if not context_can_upload:
            messages.error(request, "Akun ini tidak memiliki akses upload dokumen untuk transaksi ini.")
            return redirect("documents:checklist_detail", transaction_id=transaction.pk)
        action = request.POST.get("action", "")
        if action == "upload_document":
            handle_document_upload(request, transaction)
            return redirect("documents:checklist_detail", transaction_id=transaction.pk)
        if action == "save_checklist":
            update_checklist_manual(request, transaction)
            refresh_transaction_document_status(transaction)
            messages.success(request, "Checklist berhasil diperbarui.")
            return redirect("documents:checklist_detail", transaction_id=transaction.pk)

    statuses = list(ChecklistStatus.objects.filter(transaction_detail=transaction).order_by("nama_dokumen"))

    if not statuses:
        # Generate and persist account-family-specific checklist rows
        # This ensures completion% is accurate and save works correctly
        akun_family = normalize_akun_family(transaction.akun, transaction.jenis_spm)
        required_docs = get_required_documents_for_akun_family(akun_family)
        if required_docs:
            ChecklistStatus.objects.bulk_create([
                ChecklistStatus(
                    transaction_detail=transaction,
                    nama_dokumen=name,
                    wajib=True,
                    status=ChecklistStatus.Status.BELUM,
                )
                for name in required_docs
            ], ignore_conflicts=True)
            statuses = list(ChecklistStatus.objects.filter(transaction_detail=transaction).order_by("nama_dokumen"))
        else:
            # Fallback: use ChecklistTemplate
            templates = list(ChecklistTemplate.objects.filter(is_active=True).order_by("urutan", "nama_dokumen")[:100])
            if templates:
                ChecklistStatus.objects.bulk_create([
                    ChecklistStatus(
                        transaction_detail=transaction,
                        nama_dokumen=t.nama_dokumen,
                        wajib=t.wajib,
                        status=ChecklistStatus.Status.BELUM,
                    )
                    for t in templates
                ], ignore_conflicts=True)
                statuses = list(ChecklistStatus.objects.filter(transaction_detail=transaction).order_by("nama_dokumen"))

    checklist_rows = statuses
    uploads = DocumentUpload.objects.filter(transaction_detail=transaction).select_related("uploaded_by")[:20]
    drive_links = DocumentDriveLink.objects.filter(transaction_detail=transaction).select_related("created_by")[:50]
    drpp_supporting_attachments = _supporting_attachments_for_transaction(transaction, request.user)
    attach_satker_names([transaction])
    total = len(statuses)
    ada = sum(1 for item in statuses if item.status == ChecklistStatus.Status.ADA)
    completion_percent = round((ada / total) * 100, 2) if total else 0
    reconciliation_status = "Cocok dengan SP2D" if transaction.sp2d_raw_id and transaction.sp2d_raw.no_sp2d else "Belum ada SP2D pembanding"

    context = permission_context(request.user)
    context.update(
        {
            "page_title": "Checklist Dokumen & DRPP",
            "page_subtitle": "Status dokumen, upload DRPP, dan edit rincian bukti pengeluaran.",
            "transaction": transaction,
            "completion_percent": completion_percent,
            "upload_columns": UPLOAD_COLUMNS,
            "checklist_rows": checklist_rows,
            "uploads": uploads,
            "drive_links": drive_links,
            "drpp_supporting_attachments": drpp_supporting_attachments,
            "drpp_supporting_count": drpp_supporting_attachments.count(),
            "can_upload_document": context_can_upload,
            "reconciliation_status": reconciliation_status,
            "document_type_options": ["SP2D", "SPM", "DRPP", "KW", "Paket SPM ZIP", "LAMPIRAN"],
        }
    )
    return render(request, "documents/checklist_overview.html", context)


def handle_document_upload(request, transaction):
    document_type = request.POST.get("document_type", "").strip() or "DOKUMEN"
    manual_link = request.POST.get("manual_link", "").strip()
    upload_files = request.FILES.getlist("document_files") or request.FILES.getlist("document_file")
    use_ocr = bool(request.POST.get("use_ocr"))

    if not upload_files and not manual_link:
        messages.error(request, "Pilih file dokumen atau isi link Google Drive manual.")
        return

    if manual_link and not upload_files:
        link, created = DocumentDriveLink.objects.get_or_create(
            transaction_detail=transaction,
            jenis_dokumen=document_type,
            google_drive_url=manual_link,
            defaults={
                "satker_code": transaction.satker_code,
                "nomor_spm": transaction.nomor_spm,
                "no_drpp": transaction.no_drpp,
                "no_kuitansi": transaction.no_kuitansi,
                "nama_file": manual_link.rsplit("/", 1)[-1] or manual_link,
                "status": DocumentDriveLink.Status.PERLU_DICEK,
                "catatan": "source=manual_link; status_rekonsiliasi=Perlu Review Matching",
                "created_by": request.user,
            },
        )
        mark_checklist_present(transaction, document_type, request.user)
        messages.success(request, "Link dokumen manual tersimpan dan checklist diperbarui." if created else "Link dokumen sudah pernah tersimpan.")
        return

    upload_error = validate_upload_batch(upload_files)
    if upload_error:
        messages.error(request, upload_error)
        return

    processed = 0
    needs_review = 0
    archived_local = 0
    uploaded_drive = 0
    for upload_file in upload_files:
        result = process_single_document_file(request, transaction, document_type, upload_file, use_ocr)
        if result.get("skipped"):
            continue
        processed += 1
        needs_review += 1 if result.get("needs_review") else 0
        archived_local += 1 if result.get("archive_status") == "local_archived" else 0
        uploaded_drive += 1 if result.get("archive_status") == "uploaded" else 0

    if processed:
        messages.success(request, f"Upload selesai, {processed} file diterima dan checklist diperbarui.")
    if uploaded_drive:
        messages.success(request, f"{uploaded_drive} file berhasil disimpan ke Google Drive.")
    if archived_local:
        messages.warning(request, f"{archived_local} file disimpan ke local archive karena Google Drive belum aktif.")
    if needs_review:
        messages.warning(request, f"{needs_review} dokumen perlu review OCR. File tetap disimpan.")


def validate_upload_batch(upload_files):
    if len(upload_files) > settings.MAX_UPLOAD_FILES:
        return f"Jumlah file melebihi batas {settings.MAX_UPLOAD_FILES} file."
    total_size = sum(getattr(file, "size", 0) for file in upload_files)
    limit = settings.MAX_FOLDER_UPLOAD_SIZE_MB * 1024 * 1024
    if total_size > limit:
        return "Ukuran upload melebihi batas 2GB."
    for upload_file in upload_files:
        lower_name = upload_file.name.lower()
        if not lower_name.endswith((".pdf", ".zip", ".jpg", ".jpeg", ".png")):
            return f"Format file tidak didukung: {upload_file.name}"
    return ""



def process_single_document_file(request, transaction, document_type, upload_file, use_ocr=False):
    tmp_dir = os.path.join(settings.MEDIA_ROOT, "tmp", "checklist_uploads")
    os.makedirs(tmp_dir, exist_ok=True)
    fs = FileSystemStorage(location=tmp_dir)
    tmp_name = fs.save(upload_file.name, upload_file)
    tmp_path = fs.path(tmp_name)
    extracted_temp_dir = ""

    # Track objects created inside the atomic block so they can be updated
    # (or retried) after the transaction commits without re-opening the DB
    # connection.  This is the same pattern as link_followup_document in
    # paket_spm/services.py: Drive work stays outside the transaction so a
    # timeout/failure never erases the local save.
    drive_result = {"status": "pending", "error_message": ""}
    main_link = None
    is_reused = False
    document_upload = None

    try:
        if DocumentDriveLink.objects.filter(
            transaction_detail=transaction,
            jenis_dokumen=document_type,
            nama_file=upload_file.name,
        ).exists():
            messages.warning(request, "Dokumen sudah pernah diupload untuk transaksi ini. Commit ulang dibatalkan agar tidak duplikat.")
            return {"skipped": True}

        # ================================================================
        # ATOMIC BLOCK: all DB work — fast, no network calls
        # Google Drive upload happens AFTER this block so a Drive timeout
        # (Cloudflare 524) never rolls back the local save.
        # ================================================================
        with db_transaction.atomic():
            document_upload = create_document_upload(
                transaction, upload_file, tmp_path, document_type, request.user
            )
            parsed = parse_uploaded_document(tmp_path, upload_file.name, document_type, use_ocr)
            extracted_temp_dir = parsed.get("temp_dir", "")
            metadata = collect_metadata(parsed)
            update_transaction_from_metadata(transaction, metadata)
            if not transaction.sp2d_raw_id:
                matched_sp2d = match_sp2d_from_metadata(transaction, metadata)
                if matched_sp2d:
                    transaction.sp2d_raw = matched_sp2d
                    transaction.save(update_fields=["sp2d_raw", "updated_at"])

            # Create a placeholder DocumentDriveLink with the local archive path.
            # Drive URL is filled in after the atomic block.
            # This mirrors the link_followup_document pattern: placeholder first,
            # Drive outside, then update-in-place.
            main_link = _create_drive_link_placeholder(
                file_path=tmp_path,
                transaction=transaction,
                document_type=document_type,
                upload_file=upload_file,
                parsed=parsed,
                metadata=metadata,
                user=request.user,
            )

            # Persist DRPP groups and mark checklist inside atomic — fast local work.
            persist_drpp_groups(parsed, transaction, document_upload, request.user)
            update_checklist_from_parsed(transaction, document_type, parsed, request.user)
            refresh_transaction_document_status(transaction, verified_document_type=document_type)

        # ================================================================
        # OUTSIDE ATOMIC: Google Drive work (network call — may be slow/fail)
        # If Drive times out the transaction is already committed.
        # ================================================================
        drive_result, main_link, is_reused = _archive_document_to_drive(
            tmp_path,
            transaction=transaction,
            document_type=document_type,
            upload_file=upload_file,
            parsed=parsed,
            metadata=metadata,
            user=request.user,
            existing_link=main_link,
        )

        if drive_result["status"] not in {"uploaded", "reused", "local_archived"}:
            messages.warning(request, drive_result["error_message"] or "Dokumen tersimpan, tetapi arsip Drive perlu dicek.")

        if metadata.get("updated_fields"):
            messages.success(request, "OCR berhasil mengisi field yang kosong: " + ", ".join(metadata["updated_fields"]))
        if not transaction.sp2d_raw_id or not getattr(transaction.sp2d_raw, "no_sp2d", ""):
            messages.warning(request, "Ringkasan transaksi belum lengkap karena No SP2D belum tersedia.")
        if metadata.get("ocr_review"):
            messages.warning(request, "OCR belum yakin membaca dokumen; status Perlu Review OCR.")
        if metadata.get("missing_note"):
            messages.warning(request, metadata["missing_note"])

        return {
            "archive_status": drive_result["status"],
            "needs_review": bool(metadata.get("ocr_review") or metadata.get("missing_note")),
        }

    finally:
        # Clean up temp file — happens AFTER Drive work so the file is still
        # available if Drive is still in progress (or in the background retry path).
        try:
            fs.delete(tmp_name)
        except Exception:
            pass
        if extracted_temp_dir and os.path.exists(extracted_temp_dir):
            shutil.rmtree(extracted_temp_dir, ignore_errors=True)


def _create_drive_link_placeholder(file_path, transaction, document_type, upload_file, parsed, metadata, user):
    """
    Create a DocumentDriveLink placeholder with the local archive path.
    The google_drive_url is intentionally left empty — it will be filled
    by _archive_document_to_drive after this function returns.

    This function is always called INSIDE a db_transaction.atomic() block.
    """
    from apps.documents.services.google_drive import archive_file_locally

    # Archive to local storage immediately so the file is accessible even if
    # Drive is unavailable.  This ensures DocumentDriveLink has a valid URL
    # even when Drive upload fails.
    local_archive = archive_file_locally(file_path, display_name=upload_file.name)
    local_url = local_archive.get("url", "") or ""

    catatan = build_archive_note(parsed, metadata, transaction)

    link = DocumentDriveLink.objects.create(
        transaction_detail=transaction,
        satker_code=transaction.satker_code or "",
        nomor_spm=transaction.nomor_spm or "",
        no_kuitansi=transaction.no_kuitansi or "",
        no_drpp=transaction.no_drpp or "",
        jenis_dokumen=document_type or "",
        nama_file=upload_file.name,
        google_drive_url=local_url,
        status=(
            DocumentDriveLink.Status.PERLU_DICEK
            if not local_url
            else DocumentDriveLink.Status.AKTIF
        ),
        catatan=f"{catatan}; [DRIVE ARCHIVE] placeholder created",
        created_by=user,
    )
    return link


def _archive_document_to_drive(file_path, transaction, document_type, upload_file, parsed, metadata, user, existing_link):
    """
    Attempt to upload a document to Google Drive and update the existing
    DocumentDriveLink placeholder in-place.

    This function is always called OUTSIDE any db_transaction.atomic() block.
    A Drive timeout (Cloudflare 524) will NOT roll back any DB work.

    Returns (drive_result, link, is_reused) — same shape as archive_file_link.
    """
    try:
        # Bounded timeout: if Drive is slow/fails within 15s, we fail fast with
        # status='timeout' and the DB state (already committed) remains intact.
        # This prevents a Cloudflare 524 on the user-facing request.
        drive_result, updated_link, is_reused = archive_file_link(
            file_path,
            user=user,
            jenis_dokumen=document_type,
            nama_file=upload_file.name,
            satker_code=transaction.satker_code,
            nomor_spm=transaction.nomor_spm,
            no_drpp=transaction.no_drpp,
            no_kuitansi=transaction.no_kuitansi,
            catatan_extra=build_archive_note(parsed, metadata, transaction),
            transaction_detail=transaction,
            existing_link=existing_link,
            timeout=15,  # seconds — fail fast, don't wait for Cloudflare timeout
        )
        return drive_result, updated_link, is_reused
    except socket.timeout:
        # Raised directly if httplib2 socket timeout fires before our catch in
        # upload_file_to_drive.  Placeholder link (with local URL) is already saved.
        logger.warning("[DOCUMENT DRIVE] socket timeout for transaction=%s file=%s — local link preserved.", transaction.pk, upload_file.name)
        return {
            "status": "timeout",
            "error_message": "Google Drive tidak merespon dalam 15 detik. File tersimpan lokal.",
            "web_view_link": existing_link.google_drive_url if existing_link else "",
        }, existing_link, False
    except Exception as exc:
        logger.warning(
            "[DOCUMENT DRIVE] Failed for transaction=%s file=%s — local link remains. "
            "Exception: %s",
            transaction.pk, upload_file.name, exc,
        )
        # The placeholder link (created inside the atomic block) already has
        # the local archive URL.  Drive failure is non-fatal.
        return {
            "status": "failed",
            "error_message": f"Drive upload gagal: {exc}",
            "web_view_link": existing_link.google_drive_url if existing_link else "",
        }, existing_link, False


def create_document_upload(transaction, upload_file, tmp_path, document_type, user):
    with open(tmp_path, "rb") as handle:
        document_upload = DocumentUpload(
            transaction_detail=transaction,
            document_type=document_type,
            original_filename=upload_file.name,
            stored_filename=upload_file.name,
            file_size=upload_file.size,
            mime_type=upload_file.content_type or mimetypes.guess_type(upload_file.name)[0] or "",
            uploaded_by=user,
        )
        document_upload.file.save(upload_file.name, File(handle), save=True)
    return document_upload


def parse_uploaded_document(file_path, filename, document_type, use_ocr=False):
    lower_name = filename.lower()
    normalized_type = document_type.upper()
    if lower_name.endswith(".zip"):
        return parse_paket_spm_zip(file_path, ocr=use_ocr)
    if not lower_name.endswith(".pdf"):
        return {
            "ok": False,
            "files": [{"file_name": filename, "type": normalized_type, "status": "uploaded", "warnings": ["File non-PDF disimpan tanpa OCR."]}],
            "spm": None,
            "drpp": None,
            "drpps": [],
            "kw_items": [],
            "warnings": ["File non-PDF disimpan tanpa OCR."],
        }
    text_probe = extract_pdf_text(file_path, ocr=False)
    classified_type = classify_document(filename, "\n".join(text_probe["pages"]))
    detected_type = classified_type if classified_type != "UNKNOWN" else normalized_type
    if detected_type == "SPM":
        spm = parse_spm_pdf(file_path, ocr=use_ocr)
        return {"ok": True, "files": [{"file_name": filename, "type": "SPM", "parse_status": spm["status"], "method": spm["method"], "warnings": spm["warnings"]}], "spm": spm, "drpp": None, "drpps": [], "kw_items": [], "warnings": []}
    if detected_type in {"DRPP", "KW"}:
        drpp = parse_drpp_pdf(file_path, ocr=use_ocr)
        kw_items = [{**item, "no_drpp": drpp.get("metadata", {}).get("nomor_drpp", ""), "source_file": filename} for item in drpp.get("items", [])]
        return {"ok": True, "files": [{"file_name": filename, "type": detected_type, "parse_status": drpp["status"], "method": drpp["method"], "warnings": drpp["warnings"]}], "spm": None, "drpp": drpp, "drpps": [drpp], "kw_by_drpp": {drpp.get("metadata", {}).get("nomor_drpp", "DRPP"): kw_items}, "kw_items": kw_items, "warnings": []}
    return {"ok": False, "files": [{"file_name": filename, "type": detected_type, "parse_status": "needs_manual_review", "method": text_probe["method"], "warnings": text_probe["warnings"]}], "spm": None, "drpp": None, "drpps": [], "kw_items": [], "warnings": text_probe["warnings"]}


def collect_metadata(parsed):
    spm_meta = (parsed.get("spm") or {}).get("metadata", {})
    drpps = parsed.get("drpps") or ([parsed.get("drpp")] if parsed.get("drpp") else [])
    drpp_meta = (drpps[0] or {}).get("metadata", {}) if drpps else {}
    kw_items = parsed.get("kw_items") or []
    ocr_statuses = [
        item.get("status")
        for item in [parsed.get("spm"), *drpps]
        if item
    ]
    missing_note = ""
    jenis_spm = str(spm_meta.get("jenis_spm") or "").upper()
    if "GUP" in jenis_spm and (not drpps or not kw_items):
        missing_note = "Dokumen belum lengkap: DRPP/KW belum ditemukan."
    return {
        "nomor_spm": spm_meta.get("nomor_spm") or drpp_meta.get("nomor_spm") or "",
        "nomor_drpp": drpp_meta.get("nomor_drpp") or spm_meta.get("nomor_drpp") or "",
        "tanggal_spm": spm_meta.get("tanggal_spm"),
        "jenis_spm": spm_meta.get("jenis_spm") or "",
        "akun": next((item.get("akun") for item in kw_items if item.get("akun")), ""),
        "nilai": spm_meta.get("total_pembayaran") or drpp_meta.get("total") or sum((item.get("jumlah") or Decimal("0") for item in kw_items), Decimal("0")),
        "uraian": spm_meta.get("uraian") or next((item.get("keperluan") for item in kw_items if item.get("keperluan")), ""),
        "kw": next((item.get("no_bukti") for item in kw_items if item.get("no_bukti")), ""),
        "ocr_review": any(status in {"needs_manual_review", "failed"} for status in ocr_statuses) or not parsed.get("ok"),
        "missing_note": missing_note,
        "updated_fields": [],
    }


def update_transaction_from_metadata(transaction, metadata):
    changed = []
    set_if_empty(transaction, "nomor_spm", metadata.get("nomor_spm"), changed)
    set_if_empty(transaction, "no_drpp", metadata.get("nomor_drpp"), changed)
    set_if_empty(transaction, "no_kuitansi", metadata.get("kw"), changed)
    set_if_empty(transaction, "tanggal_spm", metadata.get("tanggal_spm"), changed)
    set_if_empty(transaction, "jenis_spm", metadata.get("jenis_spm"), changed)
    set_if_empty(transaction, "cara_pembayaran", metadata.get("jenis_spm"), changed)
    set_if_empty(transaction, "akun", metadata.get("akun"), changed)
    set_if_empty(transaction, "deskripsi", metadata.get("uraian"), changed)
    if metadata.get("nilai") and transaction.nilai_netto in (None, Decimal("0")):
        transaction.nilai_netto = metadata["nilai"]
        changed.append("Nilai Netto")
    if metadata.get("nilai") and transaction.nilai_bruto in (None, Decimal("0")):
        transaction.nilai_bruto = metadata["nilai"]
        changed.append("Nilai Bruto")
    if metadata.get("nomor_drpp") and transaction.drpp_status == TransactionDetail.DRPPStatus.BELUM_ADA:
        transaction.drpp_status = TransactionDetail.DRPPStatus.ADA
        changed.append("Status DRPP")
    if changed:
        transaction.save()
    metadata["updated_fields"] = changed


def set_if_empty(instance, field_name, value, changed):
    if value in (None, ""):
        return
    if field_name.startswith("tanggal") and isinstance(value, str):
        value = parse_date(value)
        if value is None:
            return
    current = getattr(instance, field_name)
    if current in (None, ""):
        setattr(instance, field_name, value)
        changed.append(field_name.replace("_", " ").title())


def match_sp2d_from_metadata(transaction, metadata):
    conditions = Q()
    if metadata.get("nomor_spm"):
        conditions |= Q(nomor_spm_extracted__iexact=metadata["nomor_spm"]) | Q(nomor_invoice__icontains=metadata["nomor_spm"])
    if metadata.get("nilai"):
        conditions |= Q(nilai_spm=metadata["nilai"]) | Q(nilai_sp2d=metadata["nilai"])
    if not conditions:
        return None
    queryset = SP2DRaw.objects.filter(conditions)
    if transaction.satker_code:
        satker_match = queryset.filter(satker_code=transaction.satker_code).first()
        if satker_match:
            return satker_match
    return queryset.first()


def build_archive_note(parsed, metadata, transaction):
    status_rekon = "Cocok dengan SP2D" if transaction.sp2d_raw_id else "Belum ada SP2D pembanding"
    status_doc = "Perlu Review OCR" if metadata.get("ocr_review") else "Lengkap"
    if metadata.get("missing_note"):
        status_doc = "Belum Lengkap"
    return f"source=checklist_dk; status_dokumen={status_doc}; status_rekonsiliasi={status_rekon}; files={len(parsed.get('files', []))}"


def archive_extracted_files(parsed, user, transaction):
    for parsed_file in parsed.get("files", []):
        file_path = parsed_file.get("path")
        if not file_path or not os.path.exists(file_path):
            continue
        if DocumentDriveLink.objects.filter(transaction_detail=transaction, nama_file=parsed_file.get("file_name", ""), jenis_dokumen=parsed_file.get("type", "")).exists():
            continue
        archive_file_link(
            file_path,
            user=user,
            jenis_dokumen=parsed_file.get("type", ""),
            nama_file=parsed_file.get("file_name", ""),
            satker_code=transaction.satker_code,
            nomor_spm=transaction.nomor_spm,
            no_drpp=transaction.no_drpp,
            no_kuitansi=transaction.no_kuitansi,
            catatan_extra=f"source=checklist_dk_extracted; parser_status={parsed_file.get('parse_status')}; method={parsed_file.get('method')}",
            transaction_detail=transaction,
        )


def persist_drpp_groups(parsed, transaction, document_upload, user):
    drpps = parsed.get("drpps") or ([parsed.get("drpp")] if parsed.get("drpp") else [])
    for drpp in drpps:
        if not drpp:
            continue
        meta = drpp.get("metadata", {})
        nomor_drpp = meta.get("nomor_drpp", "")
        drpp_upload, _ = DRPPUpload.objects.get_or_create(
            transaction_detail=transaction,
            nomor_drpp_norm=(nomor_drpp or "").upper(),
            defaults={
                "document_upload": document_upload,
                "nomor_drpp": nomor_drpp,
                "satker_code": transaction.satker_code,
                "nomor_spm": meta.get("nomor_spm") or transaction.nomor_spm,
                "total_jumlah": meta.get("total") or Decimal("0"),
                "raw_text": drpp.get("text_sample", ""),
                "match_status": DRPPUpload.MatchStatus.COCOK if transaction.nomor_spm else DRPPUpload.MatchStatus.PERLU_DICEK,
                "uploaded_by": user,
            },
        )
        for item in drpp.get("items", []):
            no_bukti = item.get("no_bukti", "")
            if no_bukti and DRPPItem.objects.filter(drpp_upload=drpp_upload, no_bukti_norm=no_bukti.upper()).exists():
                continue
            DRPPItem.objects.create(
                drpp_upload=drpp_upload,
                no_urut=item.get("no_urut"),
                no_bukti=no_bukti,
                no_bukti_norm=no_bukti.upper(),
                tanggal_bukti=parse_date(str(item.get("tanggal_bukti") or "")) if item.get("tanggal_bukti") else None,
                penerima=item.get("penerima", ""),
                keperluan=item.get("keperluan", ""),
                npwp=item.get("npwp", ""),
                akun=item.get("akun", ""),
                jumlah=item.get("jumlah") or Decimal("0"),
                status_verifikasi=DRPPItem.StatusVerifikasi.PERLU_REVIEW,
            )


def update_checklist_from_parsed(transaction, document_type, parsed, user):
    mark_checklist_present(transaction, document_type, user)
    detected_types = {item.get("type", "") for item in parsed.get("files", [])}
    if parsed.get("spm") or "SPM" in detected_types:
        mark_checklist_present(transaction, "SPM", user)
    if parsed.get("drpp") or parsed.get("drpps") or "DRPP" in detected_types:
        mark_checklist_present(transaction, "DRPP", user)
    if parsed.get("kw_items") or "KW" in detected_types:
        mark_checklist_present(transaction, "Kuitansi/Bukti Pembayaran", user)


def mark_checklist_present(transaction, document_type, user):
    mark_checklist_present_service(transaction, document_type, user)


def update_checklist_manual(request, transaction):
    for key, value in request.POST.items():
        if not key.startswith("checklist_status_"):
            continue
        status_id = key.replace("checklist_status_", "")
        ChecklistStatus.objects.filter(pk=status_id, transaction_detail=transaction).update(status=value, updated_by=request.user)


def attach_satker_names(rows):
    codes = {row.satker_code for row in rows if row.satker_code}
    names = {
        item["satker_code"]: item["satker_name"]
        for item in SP2DRaw.objects.filter(satker_code__in=codes)
        .exclude(satker_name="")
        .values("satker_code", "satker_name")
        .distinct()
    }
    for row in rows:
        row.display_satker_name = getattr(row.sp2d_raw, "satker_name", "") or names.get(row.satker_code, "")


def get_satker_options():
    return (
        SP2DRaw.objects.exclude(satker_code="")
        .values("satker_code", "satker_name")
        .order_by("satker_code")
        .distinct()[:200]
    )
