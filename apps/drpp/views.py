import os
import uuid
import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import FileSystemStorage
from django.core.paginator import Paginator
from django.shortcuts import redirect, render

from apps.accounts.access import (
    can_upload_document,
    filter_by_satker,
    get_user_satker_code,
    is_admin,
    permission_context,
)
from apps.core.views import build_pagination_window, normalize_page_size
from apps.documents.services.google_drive import archive_file_link
from apps.documents.models import DocumentDriveLink

from .models import DRPPItem, DRPPUpload, DRPPImportBatch
from .services import prepare_drpp_rows, classify_drpp_rows, commit_drpp_rows


def _validate_drpp_upload(upload_file, upload_files):
    """Validate uploaded files for size, count, and basic type checks."""
    MAX_SIZE = 50 * 1024 * 1024
    MAX_FILES = 200
    ALLOWED_ZIP_TYPES = {"application/zip", "application/x-zip-compressed", "application/octet-stream", "application/x-zip"}
    ALLOWED_PDF_TYPES = {"application/pdf", "application/octet-stream"}
    
    if upload_file and upload_files:
        return "Jangan mencampur input ZIP/PDF dari dua pemilih file."
    files = [upload_file] if upload_file else list(upload_files or [])
    total_size = sum(item.size for item in files)
    total_count = len(files)
    extensions = {os.path.splitext(item.name)[1].lower() for item in files}
    if ".zip" in extensions and (len(files) != 1 or extensions != {".zip"}):
        return "ZIP tidak boleh dicampur dengan PDF atau ZIP lain."
    for item in files:
        name_lower = item.name.lower()
        content_type = (item.content_type or "").lower()
        if name_lower.endswith(".zip"):
            if content_type and content_type not in ALLOWED_ZIP_TYPES:
                return "MIME zip tidak valid."
        elif name_lower.endswith(".pdf"):
            if content_type and content_type not in ALLOWED_PDF_TYPES:
                return f"MIME PDF tidak valid untuk {item.name}."
        else:
            return f"File {item.name} tidak didukung (harus PDF/ZIP)."
                
    if total_count > MAX_FILES:
        return f"Maksimal {MAX_FILES} file per upload."
    if total_size > MAX_SIZE:
        return "Total ukuran upload melebihi 50MB."
        
    return None

def _save_many_files_as_zip(fs, upload_files):
    import zipfile
    from io import BytesIO
    zip_buffer = BytesIO()
    used_names = set()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in upload_files:
            safe_name = os.path.basename(f.name)
            stem, suffix = os.path.splitext(safe_name)
            candidate = safe_name
            counter = 2
            while candidate.casefold() in used_names:
                candidate = f"{stem}_{counter}{suffix}"
                counter += 1
            used_names.add(candidate.casefold())
            safe_name = candidate
            zf.writestr(safe_name, f.read())
    filename = f"multi_{uuid.uuid4().hex[:8]}.zip"
    fs.save(filename, zip_buffer)
    return filename


def _remove_file(file_path):
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
    except OSError:
        pass


def _discard_preview(request, preview_state):
    _remove_file((preview_state or {}).get("file_path"))
    request.session.pop("drpp_preview", None)


@login_required
def drpp_list(request):
    if request.method == "POST":
        if not can_upload_document(request.user):
            messages.error(request, "Anda tidak memiliki hak akses untuk mengunggah dokumen.")
            return redirect("drpp:list")
            
        upload_file = request.FILES.get("file_drpp")
        upload_files = request.FILES.getlist("document_files")
        if not upload_file and not upload_files:
            messages.error(request, "Harap pilih PDF DRPP, banyak PDF, folder, atau ZIP.")
            return redirect("drpp:list")
            
        validation_error = _validate_drpp_upload(upload_file, upload_files)
        if validation_error:
            messages.error(request, validation_error)
            return redirect("drpp:list")
            
        tmp_dir = os.path.join(settings.MEDIA_ROOT, "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        fs = FileSystemStorage(location=tmp_dir)
        
        selected_files = [upload_file] if upload_file else list(upload_files)
        if len(selected_files) == 1 and selected_files[0].name.lower().endswith(".zip"):
            selected = selected_files[0]
            filename = fs.save(os.path.basename(selected.name), selected)
            original_filename = selected.name
        else:
            filename = _save_many_files_as_zip(fs, selected_files)
            original_filename = selected_files[0].name if len(selected_files) == 1 else filename
            
        # Parse satker from request (only for admin, operators are forced to their own satker)
        input_satker = request.POST.get("satker_code", "").strip()
        user_satker = get_user_satker_code(request.user)
        satker_code = input_satker if is_admin(request.user) else user_satker
        if not satker_code:
            _remove_file(fs.path(filename))
            messages.error(request, "Satker wajib dipilih sebelum preview.")
            return redirect("drpp:list")
        
        # Tahun is explicitly required or defaults to current
        try:
            tahun = int(request.POST.get("tahun") or datetime.datetime.now().year)
        except (TypeError, ValueError):
            _remove_file(fs.path(filename))
            messages.error(request, "Tahun dokumen tidak valid.")
            return redirect("drpp:list")
            
        request.session["drpp_preview"] = {
            "file_path": fs.path(filename),
            "original_filename": original_filename,
            "ocr": bool(request.POST.get("use_ocr")),
            "satker_code": satker_code,
            "tahun": tahun,
            "uploaded_by_user_id": request.user.pk,
        }
        return redirect("drpp:preview")

    rows = filter_by_satker(
        DRPPImportBatch.objects.select_related("uploaded_by"),
        request.user,
        field_name="satker_code"
    )
    
    page_size = normalize_page_size(request.GET.get("page_size"))
    paginator = Paginator(rows.order_by("-created_at", "id"), page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    base_query = request.GET.copy()
    base_query.pop("page", None)
    context = permission_context(request.user)
    context.update(
        {
            "page_title": "DRPP",
            "page_subtitle": "Daftar DRPP & Kuitansi yang sudah diunggah.",
            "rows": page_obj.object_list,
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
    if not can_upload_document(request.user):
        _discard_preview(request, preview_state)
        messages.error(request, "Anda tidak memiliki hak akses untuk mengubah preview DRPP.")
        return redirect("drpp:list")
    if preview_state.get("uploaded_by_user_id") != request.user.pk:
        _discard_preview(request, preview_state)
        messages.error(request, "Sesi preview ini bukan milik pengguna aktif.")
        return redirect("drpp:list")
    user_satker = get_user_satker_code(request.user)
    if not is_admin(request.user) and preview_state.get("satker_code") != user_satker:
        _discard_preview(request, preview_state)
        messages.error(request, "Sesi preview berada di luar ruang lingkup satker Anda.")
        return redirect("drpp:list")
        
    file_path = preview_state["file_path"]
    if not os.path.exists(file_path):
        messages.error(request, "File sementara DRPP hilang. Silakan upload ulang.")
        request.session.pop("drpp_preview", None)
        return redirect("drpp:list")

    ocr = preview_state.get("ocr", False)
    satker_code = preview_state.get("satker_code")
    tahun = preview_state.get("tahun")
    
    user_corrections = preview_state.get("user_corrections", {})

    if request.method == "POST":
        action = request.POST.get("action")
        
        if action == "cancel":
            _discard_preview(request, preview_state)
            messages.info(request, "Preview DRPP dibatalkan.")
            return redirect("drpp:list")
            
        elif action == "update_corrections":
            # Save the corrections in session
            for key, val in request.POST.items():
                for prefix, field in (("akun_", "akun"), ("bruto_", "bruto"), ("netto_", "netto"), ("kuitansi_", "no_kuitansi")):
                    if not key.startswith(prefix):
                        continue
                    row_key = key[len(prefix):]
                    if row_key not in user_corrections:
                        user_corrections[row_key] = {}
                    user_corrections[row_key][field] = val
            preview_state["user_corrections"] = user_corrections
            request.session["drpp_preview"] = preview_state
            messages.success(request, "Koreksi disimpan, silakan verifikasi tabel di bawah.")
            return redirect("drpp:preview")
            
        elif action == "commit":
            # Parse and classify again
            result = commit_drpp_rows(
                zip_path=file_path, 
                ocr=ocr, 
                satker_code=satker_code, 
                tahun=tahun, 
                user=request.user, 
                filename=preview_state["original_filename"],
                original_filename=preview_state["original_filename"],
                user_corrections=user_corrections
            )
            
            if not result["ok"]:
                messages.error(request, f"Gagal saat parsing/commit: {', '.join(result.get('error', []))}")
                _discard_preview(request, preview_state)
                return redirect("drpp:list")
                
            batch = result["batch"]
            
            # Archive File
            try:
                drive_result, _ = archive_file_link(
                    result["document_upload"].file.path,
                    user=request.user,
                    jenis_dokumen="DRPP_BATCH",
                    nama_file=preview_state["original_filename"],
                    satker_code=batch.satker_code,
                    catatan_extra=f"Batch={batch.pk}",
                )
            except Exception as exc:
                drive_result = {"status": "failed", "error_message": str(exc)}
                DocumentDriveLink.objects.create(
                    satker_code=batch.satker_code,
                    jenis_dokumen="DRPP_BATCH",
                    nama_file=preview_state["original_filename"],
                    google_drive_url="",
                    status=DocumentDriveLink.Status.PERLU_DICEK,
                    catatan=f"drive_status=failed; Batch={batch.pk}; {exc}"[:2000],
                    created_by=request.user,
                )
            finally:
                _discard_preview(request, preview_state)
            
            msg = (f"Berhasil commit. Baru: {batch.created_rows}, "
                   f"Update: {batch.updated_rows}, Skip: {batch.skipped_rows}, "
                   f"Review/Conflict/Failed: {batch.review_rows+batch.conflict_rows+batch.failed_rows}")
            if drive_result["status"] == "uploaded":
                messages.success(request, msg + ". File diarsipkan ke Drive.")
            else:
                messages.warning(request, msg + f". Pengarsipan Drive tertunda: {drive_result.get('error_message')}")
                
            return redirect("drpp:list")

    # Read-only parse & classify for GET preview
    try:
        prep = prepare_drpp_rows(file_path, ocr=ocr, satker_code=satker_code, tahun=tahun)
    except Exception as exc:
        import zipfile as _zipfile
        if isinstance(exc, _zipfile.BadZipFile):
            messages.error(request, "File tidak bisa dibuka sebagai ZIP yang valid.")
        else:
            messages.error(request, f"Error saat memproses file: {exc}")
        _discard_preview(request, preview_state)
        return redirect("drpp:list")
    
    if not prep["ok"]:
        _discard_preview(request, preview_state)
        messages.error(request, "; ".join(prep["warnings"]))
        return redirect("drpp:list")
        
    classified_rows = classify_drpp_rows(prep["rows"], user_corrections)
    
    can_commit = bool(classified_rows)

    context = permission_context(request.user)
    context.update({
        "page_title": "Preview DRPP",
        "page_subtitle": "Tinjau hasil parser DRPP sebelum commit.",
        "rows": classified_rows,
        "satker_code": satker_code,
        "tahun": tahun,
        "can_commit": can_commit,
        "warnings": prep["warnings"]
    })
    return render(request, "drpp/preview.html", context)
