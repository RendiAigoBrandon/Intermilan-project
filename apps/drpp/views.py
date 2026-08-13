import logging
import os
import uuid
import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
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
from apps.core.services import get_active_parent_for_user, clear_active_parent as _clear_parent_svc
from apps.documents.services.google_drive_dedup import archive_file_with_dedup
from apps.documents.models import DocumentDriveLink

from .models import DRPPItem, DRPPUpload, DRPPImportBatch
from .services import prepare_drpp_rows, classify_drpp_rows, commit_drpp_rows

logger = logging.getLogger("drpp.views")


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
    active_parent = get_active_parent_for_user(request=request, user=request.user)
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
            "active_parent": active_parent,
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
        # Compute classified_rows here so it's available to all POST actions
        classified_rows = classify_drpp_rows(prep["rows"], user_corrections)
        active_parent = get_active_parent_for_user(request=request, user=request.user)
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

        elif action == "inherit_spm":
            # User manually entered a Nomor SPM — look up the exact parent package
            # by current DRPP context (satker + tahun + nomor_spm) and inherit SPM
            # fields into the classified rows.  This works for any DRPP/SPM combination.
            manual_nomor_spm = (request.POST.get("nomor_spm") or "").strip()
            if not manual_nomor_spm:
                messages.warning(request, "Masukkan Nomor SPM untuk mencari parent.")
                return redirect("drpp:preview")

            from apps.core.services import get_package_by_identity, validate_parent_compatibility

            manual_pkg = get_package_by_identity(
                satker_code=satker_code or "",
                tahun=int(tahun) if tahun else 0,
                nomor_spm=manual_nomor_spm,
            )
            if not manual_pkg:
                messages.warning(
                    request,
                    f"SPM {manual_nomor_spm} tidak ditemukan untuk satker {satker_code or '(kosong)'} "
                    f"tahun {tahun or '(kosong)'}. Pastikan Nomor SPM dan satker benar."
                )
                return redirect("drpp:preview")

            is_compatible, conflict_msg = validate_parent_compatibility(
                package=manual_pkg,
                drpp_satker=satker_code or None,
                drpp_tahun=int(tahun) if tahun else None,
                drpp_nomor_spm=None,
            )
            if not is_compatible:
                messages.warning(request, f"SPM {manual_nomor_spm} tidak cocok dengan DRPP ini: {conflict_msg}")
                return redirect("drpp:preview")

            # Inherit SPM fields into classified rows
            inherited = _inherit_spm_fields(classified_rows, manual_pkg)
            if inherited:
                messages.info(request, f"Field SPM diwariskan dari {manual_nomor_spm}: {', '.join(inherited)}")
                # Re-classify with inherited fields so status reflects the new data
                classified_rows = classify_drpp_rows(prep["rows"], user_corrections)
                _inherit_spm_fields(classified_rows, manual_pkg)
            else:
                messages.info(request, f"SPM {manual_nomor_spm} ditemukan tetapi tidak ada field baru untuk diwariskan.")

            # Store inheritance in session so it persists across re-renders
            preview_state["inherited_spm_package_id"] = manual_pkg.pk
            preview_state["inherited_spm_fields"] = inherited
            request.session["drpp_preview"] = preview_state

            context = permission_context(request.user)
            context.update({
                "page_title": "Preview DRPP",
                "page_subtitle": "Nomor SPM berhasil diset. Periksa hasil di bawah sebelum commit.",
                "rows": classified_rows,
                "satker_code": satker_code,
                "tahun": tahun,
                "can_commit": bool(classified_rows),
                "warnings": preview_state.get("warnings", []),
                "active_parent": active_parent,
            })
            return render(request, "drpp/preview.html", context)

        elif action == "commit":
            # Load inherited SPM package from session (if user selected one)
            inherited_spm_package = None
            inherited_pkg_id = preview_state.get("inherited_spm_package_id")
            if inherited_pkg_id:
                from apps.core.models import TransactionPackage
                inherited_spm_package = TransactionPackage.objects.filter(pk=inherited_pkg_id).first()

            # Parse and classify again
            try:
                result = commit_drpp_rows(
                    zip_path=file_path,
                    ocr=ocr,
                    satker_code=satker_code,
                    tahun=tahun,
                    user=request.user,
                    filename=preview_state["original_filename"],
                    original_filename=preview_state["original_filename"],
                    user_corrections=user_corrections,
                    inherited_spm_package=inherited_spm_package,
                )
            except Exception as exc:
                logger.exception("[DRPP COMMIT] Unexpected error during commit: %s", exc)
                messages.error(request, f"Gagal menyimpan DRPP: {exc}")
                _discard_preview(request, preview_state)
                return redirect("drpp:list")

            if not result["ok"]:
                messages.error(request, f"Gagal saat parsing/commit: {', '.join(result.get('error', []))}")
                _discard_preview(request, preview_state)
                return redirect("drpp:list")
                
            batch = result["batch"]

            # Archive File with Duplicate Protection
            try:
                drive_result, _, is_reused = archive_file_with_dedup(
                    result["document_upload"].file.path,
                    user=request.user,
                    jenis_dokumen="DRPP_BATCH",
                    nama_file=preview_state["original_filename"],
                    satker_code=batch.satker_code,
                    catatan_extra=f"Batch={batch.pk}",
                )
            except Exception as exc:
                drive_result = {"status": "failed", "error_message": str(exc), "is_duplicate": False}
                is_reused = False
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
                messages.success(request, msg + ". File diarsipkan ke Google Drive.")
            elif drive_result.get("is_duplicate") or is_reused:
                messages.info(request, msg + ". File sudah ada di Google Drive (tidak di-upload ulang).")
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

    # Store warnings in session so POST actions can access them without re-parsing
    preview_state["warnings"] = prep["warnings"]
    request.session["drpp_preview"] = preview_state

    from apps.core.services import (
        get_active_parent_for_user,
        validate_parent_compatibility,
        get_package_by_identity,
    )

    # ================================================================
    # STEP 1: Classify rows first (with raw OCR data)
    # ================================================================
    classified_rows = classify_drpp_rows(prep["rows"], user_corrections)

    # ================================================================
    # STEP 2: ACTIVE PARENT SPM INHERITANCE
    # Inherit SPM fields from active parent BEFORE row-level warnings are computed.
    # Inheritance must happen BEFORE the second classify call.
    # ================================================================
    active_parent = get_active_parent_for_user(request=request, user=request.user)
    parent_inherited_fields = []
    manual_inherited_fields = []
    parent_conflict = None

    def _inherit_spm_fields(rows, parent_pkg):
        """Inherit SPM fields into classified DRPP rows where fields are blank.

        Does NOT force status/message changes — the existing classifier will
        recompute correct status when called after inheritance.
        """
        if not rows or not parent_pkg:
            return []
        inherited = []
        for row in rows:
            changed = False
            if not row.get("nomor_spm") and parent_pkg.nomor_spm:
                row["nomor_spm"] = parent_pkg.nomor_spm
                inherited.append("nomor_spm")
                changed = True
            if not row.get("tanggal_spm") and parent_pkg.tanggal_spm:
                if hasattr(parent_pkg.tanggal_spm, "isoformat"):
                    row["tanggal_spm"] = parent_pkg.tanggal_spm.isoformat()
                else:
                    row["tanggal_spm"] = str(parent_pkg.tanggal_spm)
                inherited.append("tanggal_spm")
                changed = True
            if not row.get("jenis_spm") and parent_pkg.jenis_spm:
                row["jenis_spm"] = parent_pkg.jenis_spm
                inherited.append("jenis_spm")
                changed = True
            # Do NOT force row["message"] = "" or row["status"] = "OK" here.
            # The classifier handles status correctly after re-classify.
            # Truthful warnings (kuitansi missing, akun missing) are preserved naturally.
            # Conditional "SPM utama belum ada di D_K" is handled in the template via active_parent context.
        return inherited

    # Try active parent first
    if active_parent and active_parent.transaction_package:
        parent_pkg = active_parent.transaction_package
        is_compatible, conflict_msg = validate_parent_compatibility(
            package=parent_pkg,
            drpp_satker=satker_code or None,
            drpp_tahun=int(tahun) if tahun else None,
            drpp_nomor_spm=None,
        )
        if is_compatible:
            parent_inherited_fields = _inherit_spm_fields(classified_rows, parent_pkg)
            if parent_inherited_fields:
                messages.info(request, f"Field SPM diwariskan dari SPM parent aktif: {', '.join(parent_inherited_fields)}")
        else:
            parent_conflict = conflict_msg
            messages.warning(request, conflict_msg)

    # Manual SPM fallback: user typed Nomor SPM in the form
    manual_nomor_spm = (request.POST.get("nomor_spm") or "").strip()
    if manual_nomor_spm and not parent_inherited_fields:
        manual_pkg = get_package_by_identity(
            satker_code=satker_code or "",
            tahun=int(tahun) if tahun else 0,
            nomor_spm=manual_nomor_spm,
        )
        if manual_pkg:
            is_compatible, conflict_msg = validate_parent_compatibility(
                package=manual_pkg,
                drpp_satker=satker_code or None,
                drpp_tahun=int(tahun) if tahun else None,
                drpp_nomor_spm=None,
            )
            if is_compatible:
                manual_inherited_fields = _inherit_spm_fields(classified_rows, manual_pkg)
                if manual_inherited_fields:
                    messages.info(request, f"Field SPM diisi dari pencarian manual ({manual_nomor_spm}): {', '.join(manual_inherited_fields)}")
            else:
                messages.warning(request, f"SPM {manual_nomor_spm} tidak cocok dengan DRPP ini: {conflict_msg}")
        else:
            messages.warning(request, f"SPM {manual_nomor_spm} tidak ditemukan untuk satker {satker_code or '(kosong)'} tahun {tahun or '(kosong)'}. Pastikan Nomor SPM dan satker benar.")

    # ================================================================
    # STEP 3: Re-classify AFTER inheritance so status/warnings reflect inherited fields
    # ================================================================
    if parent_inherited_fields or manual_inherited_fields:
        classified_rows = classify_drpp_rows(prep["rows"], user_corrections)
        # Re-apply inheritance to the freshly classified rows
        if parent_inherited_fields:
            _inherit_spm_fields(classified_rows, active_parent.transaction_package)
        elif manual_inherited_fields and manual_nomor_spm:
            manual_pkg = get_package_by_identity(
                satker_code=satker_code or "",
                tahun=int(tahun) if tahun else 0,
                nomor_spm=manual_nomor_spm,
            )
            if manual_pkg:
                _inherit_spm_fields(classified_rows, manual_pkg)

    can_commit = bool(classified_rows)

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
        "warnings": prep["warnings"],
        "active_parent": active_parent,
    })
    return render(request, "drpp/preview.html", context)


@require_POST
@login_required
def change_active_parent(request):
    """Ganti SPM: clear current parent and redirect to SPM upload workflow."""
    cleared = _clear_parent_svc(request=request, user=request.user)
    if cleared:
        messages.info(request, "SPM Parent sebelumnya telah dilepas. Silakan pilih atau upload SPM baru.")
    else:
        messages.info(request, "Silakan pilih atau upload SPM baru.")
    return redirect("paket_spm:list")


@require_POST
@login_required
def clear_active_parent(request):
    """Clear the active SPM parent (Lepas SPM Parent)."""
    cleared = _clear_parent_svc(request=request, user=request.user)
    if cleared:
        messages.info(request, "SPM Parent aktif telah dilepas.")
    else:
        messages.info(request, "Tidak ada SPM Parent aktif yang perlu dilepas.")
    return redirect("drpp:list")
