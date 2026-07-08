import os
import zipfile
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import FileSystemStorage
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import redirect, render

from apps.accounts.access import filter_by_satker, permission_context
from apps.core.parsers import parse_drpp_pdf, parse_paket_spm_zip
from apps.core.views import build_pagination_window, normalize_page_size
from apps.documents.services.google_drive import archive_file_link

from .models import DRPPItem, DRPPUpload


@login_required
def drpp_list(request):
    if request.method == "POST":
        upload_file = request.FILES.get("file_drpp")
        upload_files = request.FILES.getlist("document_files")
        if not upload_file and not upload_files:
            messages.error(request, "Harap pilih PDF DRPP, banyak PDF, folder, atau ZIP.")
            return redirect("drpp:list")
        validation_error = validate_drpp_upload(upload_file, upload_files)
        if validation_error:
            messages.error(request, validation_error)
            return redirect("drpp:list")
        tmp_dir = os.path.join(settings.MEDIA_ROOT, "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        fs = FileSystemStorage(location=tmp_dir)
        if upload_files:
            filename = save_many_files_as_zip(fs, upload_files)
            original_filename = filename
            kind = "zip"
        else:
            filename = fs.save(upload_file.name, upload_file)
            original_filename = upload_file.name
            kind = "zip" if upload_file.name.lower().endswith(".zip") else "pdf"
        request.session["drpp_preview"] = {
            "file_path": fs.path(filename),
            "original_filename": original_filename,
            "ocr": bool(request.POST.get("use_ocr")),
            "kind": kind,
        }
        return redirect("drpp:preview")

    rows = filter_by_satker(DRPPUpload.objects.select_related("transaction_detail", "document_upload", "uploaded_by"), request.user)
    transaction_id = request.GET.get("transaction_id")
    if transaction_id:
        rows = rows.filter(transaction_detail_id=transaction_id)
    page_size = normalize_page_size(request.GET.get("page_size"))
    paginator = Paginator(rows.order_by("-uploaded_at", "id"), page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    base_query = request.GET.copy()
    base_query.pop("page", None)
    context = permission_context(request.user)
    context.update(
        {
            "page_title": "DRPP",
            "page_subtitle": "Daftar DRPP yang sudah masuk. Upload/parser penuh belum diaktifkan pada tahap ini.",
            "rows": page_obj.object_list,
            "transaction_id": transaction_id,
            "page_obj": page_obj,
            "paginator": paginator,
            "page_size": page_size,
            "page_size_options": (20, 50, 100),
            "page_start": page_obj.start_index() if paginator.count else 0,
            "page_end": page_obj.end_index() if paginator.count else 0,
            "base_querystring": base_query.urlencode(),
            "pagination_window": build_pagination_window(page_obj),
        }
    )
    return render(
        request,
        "drpp/list.html",
        context,
    )


@login_required
def drpp_preview(request):
    preview_state = request.session.get("drpp_preview")
    if not preview_state:
        messages.error(request, "Sesi preview DRPP tidak ditemukan.")
        return redirect("drpp:list")
    file_path = preview_state["file_path"]
    if not os.path.exists(file_path):
        messages.error(request, "File sementara DRPP hilang. Silakan upload ulang.")
        request.session.pop("drpp_preview", None)
        return redirect("drpp:list")

    if preview_state.get("kind") == "zip":
        parsed_package = parse_paket_spm_zip(file_path, ocr=preview_state.get("ocr", False))
        parsed_drpps = parsed_package.get("drpps") or ([parsed_package.get("drpp")] if parsed_package.get("drpp") else [])
        parsed = parsed_drpps[0] if parsed_drpps else {"file_name": preview_state["original_filename"], "page_count": 0, "method": "zip", "best_engine": "zip", "status": "needs_manual_review", "warnings": parsed_package.get("warnings", []), "metadata": {}, "items": [], "page_details": []}
        can_commit = bool(parsed_drpps)
    else:
        parsed_package = None
        parsed_drpps = []
        parsed = parse_drpp_pdf(file_path, ocr=preview_state.get("ocr", False))
        can_commit = parsed["status"] in {"parsed_text", "parsed_ocr", "needs_manual_review"} and bool(parsed["metadata"].get("nomor_drpp") or parsed["items"])

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "cancel":
            os.remove(file_path)
            if parsed_package and parsed_package.get("temp_dir"):
                import shutil
                shutil.rmtree(parsed_package["temp_dir"], ignore_errors=True)
            request.session.pop("drpp_preview", None)
            messages.info(request, "Preview DRPP dibatalkan.")
            return redirect("drpp:list")
        if action == "commit":
            if not can_commit:
                messages.error(request, "Preview DRPP belum valid untuk commit.")
                return redirect("drpp:preview")
            with transaction.atomic():
                uploads = [create_drpp_upload_from_parsed(item, request.user) for item in (parsed_drpps or [parsed])]
                upload = uploads[0]
            drive_result, _ = archive_file_link(
                file_path,
                user=request.user,
                jenis_dokumen="DRPP",
                nama_file=preview_state["original_filename"],
                nomor_spm=str(parsed["metadata"].get("nomor_spm", "")),
                no_drpp=str(parsed["metadata"].get("nomor_drpp", "")),
                catatan_extra=f"parser_status={parsed['status']}; method={parsed['method']}",
            )
            os.remove(file_path)
            if parsed_package and parsed_package.get("temp_dir"):
                import shutil
                shutil.rmtree(parsed_package["temp_dir"], ignore_errors=True)
            request.session.pop("drpp_preview", None)
            if drive_result["status"] == "uploaded":
                messages.success(request, f"DRPP disimpan dan diarsipkan ke Google Drive: {upload.nomor_drpp or upload.pk}.")
            else:
                messages.warning(request, f"DRPP disimpan sebagai preview/review: {upload.nomor_drpp or upload.pk}. {drive_result['error_message']}")
            return redirect("drpp:list")

    context = permission_context(request.user)
    context.update({
        "page_title": "Preview DRPP",
        "page_subtitle": "Tinjau hasil parser DRPP sebelum commit.",
        "parsed": parsed,
        "parsed_package": parsed_package,
        "parsed_drpps": parsed_drpps,
        "preview_state": preview_state,
        "can_commit": can_commit,
    })
    return render(request, "drpp/preview.html", context)


def create_drpp_upload_from_parsed(parsed, user):
    upload = DRPPUpload.objects.create(
        nomor_drpp=str(parsed["metadata"].get("nomor_drpp", ""))[:100],
        nomor_drpp_norm=str(parsed["metadata"].get("nomor_drpp", "")).upper()[:100],
        nomor_spm=str(parsed["metadata"].get("nomor_spm", ""))[:100],
        total_jumlah=parsed["metadata"].get("total") or Decimal("0"),
        raw_text=parsed.get("text_sample", ""),
        match_status=DRPPUpload.MatchStatus.PERLU_DICEK,
        uploaded_by=user,
    )
    DRPPItem.objects.bulk_create([
        DRPPItem(
            drpp_upload=upload,
            no_urut=item.get("no_urut"),
            no_bukti=str(item.get("no_bukti", ""))[:100],
            no_bukti_norm=str(item.get("no_bukti", "")).upper()[:100],
            akun=str(item.get("akun", ""))[:32],
            jumlah=item.get("jumlah") or Decimal("0"),
            keperluan=str(item.get("keperluan", "")),
            status_verifikasi=DRPPItem.StatusVerifikasi.PERLU_REVIEW,
        )
        for item in parsed["items"]
    ])
    return upload


def validate_drpp_upload(upload_file=None, upload_files=None):
    upload_files = upload_files or []
    total_size = sum(getattr(item, "size", 0) for item in upload_files)
    if upload_file:
        total_size += getattr(upload_file, "size", 0)
    if len(upload_files) > settings.MAX_UPLOAD_FILES:
        return f"Jumlah file melebihi batas {settings.MAX_UPLOAD_FILES} file."
    if total_size > settings.MAX_FOLDER_UPLOAD_SIZE_MB * 1024 * 1024:
        return "Ukuran upload melebihi batas 2GB."
    for item in upload_files or ([upload_file] if upload_file else []):
        if not item.name.lower().endswith((".pdf", ".zip")):
            return f"Format file tidak didukung: {item.name}"
    return ""


def save_many_files_as_zip(fs, upload_files):
    safe_name = f"drpp_multi_{len(upload_files)}_files.zip"
    zip_path = fs.path(safe_name)
    counter = 1
    while os.path.exists(zip_path):
        safe_name = f"drpp_multi_{len(upload_files)}_files_{counter}.zip"
        zip_path = fs.path(safe_name)
        counter += 1
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for upload_file in upload_files:
            arcname = str(upload_file.name).replace("\\", "/").lstrip("/")
            if not arcname.lower().endswith(".pdf"):
                continue
            with archive.open(arcname, "w") as target:
                for chunk in upload_file.chunks():
                    target.write(chunk)
    return safe_name
