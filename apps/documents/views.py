import mimetypes
import os
import shutil
from decimal import Decimal
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.files import File
from django.core.files.storage import FileSystemStorage
from django.db import transaction as db_transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date

from apps.accounts.access import can_upload_document, filter_by_satker, permission_context
from apps.core.parsers import (
    classify_document,
    extract_pdf_text,
    parse_drpp_pdf,
    parse_paket_spm_zip,
    parse_spm_pdf,
)
from apps.core.document_policy import (
    get_required_documents_for_akun_family,
    normalize_akun_family,
)
from apps.core.views import UPLOAD_COLUMNS, build_pagination_window
from apps.dk.models import TransactionDetail
from apps.dk.services import refresh_transaction_document_status
from apps.drpp.models import DRPPItem, DRPPUpload
from apps.sp2d.models import SP2DRaw
from apps.documents.services.checklist import mark_checklist_present as mark_checklist_present_service
from apps.documents.services.google_drive import archive_file_link

from .models import ChecklistStatus, ChecklistTemplate, DocumentDriveLink, DocumentUpload


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


@login_required
def archive(request):
    q = request.GET.get("q", "").strip()
    satker = request.GET.get("satker", "").strip()
    status = request.GET.get("status", "").strip()

    scoped_links = filter_by_satker(
        DocumentDriveLink.objects.select_related("created_by"),
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
    try:
        if DocumentDriveLink.objects.filter(
            transaction_detail=transaction,
            jenis_dokumen=document_type,
            nama_file=upload_file.name,
        ).exists():
            messages.warning(request, "Dokumen sudah pernah diupload untuk transaksi ini. Commit ulang dibatalkan agar tidak duplikat.")
            return {"skipped": True}
        with db_transaction.atomic():
            document_upload = create_document_upload(transaction, upload_file, tmp_path, document_type, request.user)
            parsed = parse_uploaded_document(tmp_path, upload_file.name, document_type, use_ocr)
            extracted_temp_dir = parsed.get("temp_dir", "")
            metadata = collect_metadata(parsed)
            update_transaction_from_metadata(transaction, metadata)
            if not transaction.sp2d_raw_id:
                matched_sp2d = match_sp2d_from_metadata(transaction, metadata)
                if matched_sp2d:
                    transaction.sp2d_raw = matched_sp2d
                    transaction.save(update_fields=["sp2d_raw", "updated_at"])

            drive_result, main_link, is_reused = archive_file_link(
                tmp_path,
                user=request.user,
                jenis_dokumen=document_type,
                nama_file=upload_file.name,
                satker_code=transaction.satker_code,
                nomor_spm=transaction.nomor_spm,
                no_drpp=transaction.no_drpp,
                no_kuitansi=transaction.no_kuitansi,
                catatan_extra=build_archive_note(parsed, metadata, transaction),
                transaction_detail=transaction,
            )
            archive_extracted_files(parsed, request.user, transaction)
            persist_drpp_groups(parsed, transaction, document_upload, request.user)
            update_checklist_from_parsed(transaction, document_type, parsed, request.user)
            refresh_transaction_document_status(transaction, verified_document_type=document_type)

        if drive_result["status"] not in {"uploaded", "local_archived"}:
            messages.warning(request, drive_result["error_message"] or "Dokumen tersimpan, tetapi arsip Drive perlu dicek.")

        if metadata.get("updated_fields"):
            messages.success(request, "OCR berhasil mengisi field yang kosong: " + ", ".join(metadata["updated_fields"]))
        if not transaction.sp2d_raw_id or not getattr(transaction.sp2d_raw, "no_sp2d", ""):
            messages.warning(request, "Ringkasan transaksi belum lengkap karena No SP2D belum tersedia.")
        if metadata.get("ocr_review"):
            messages.warning(request, "OCR belum yakin membaca dokumen; status Perlu Review OCR.")
        if metadata.get("missing_note"):
            messages.warning(request, metadata["missing_note"])
        return {"archive_status": drive_result["status"], "needs_review": bool(metadata.get("ocr_review") or metadata.get("missing_note"))}
    finally:
        try:
            fs.delete(tmp_name)
        except Exception:
            pass
        if extracted_temp_dir and os.path.exists(extracted_temp_dir):
            shutil.rmtree(extracted_temp_dir, ignore_errors=True)


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
