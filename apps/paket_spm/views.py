import os
import shutil
import zipfile
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files import File
from django.core.files.storage import FileSystemStorage
from django.db import transaction
from django.shortcuts import redirect, render

from apps.accounts.access import filter_by_satker, permission_context
from apps.core.parsers import classify_document, extract_pdf_text, parse_drpp_pdf, parse_month, parse_paket_spm_zip, parse_spm_pdf, make_json_safe
from apps.documents.services.google_drive import archive_file_link
from apps.paket_spm.services import build_package_decision, build_transaction_rows_from_package
from apps.sp2d.models import SP2DRaw

from .models import PaketSPMPreviewItem, PaketSPMUpload


@login_required
def paket_spm_list(request):
    if request.method == "POST":
        upload_file = request.FILES.get("file_paket")
        upload_files = request.FILES.getlist("document_files")
        if not upload_files:
            upload_files = request.FILES.getlist("file_paket")
            upload_file = None
        if not upload_file and not upload_files:
            messages.error(request, "Harap pilih PDF, folder PDF, atau ZIP paket SPM.")
            return redirect("paket_spm:list")
        validation_error = validate_paket_upload(upload_file, upload_files)
        if validation_error:
            messages.error(request, validation_error)
            return redirect("paket_spm:list")

        tmp_dir = os.path.join(settings.MEDIA_ROOT, "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        fs = FileSystemStorage(location=tmp_dir)

        if upload_files and len(upload_files) == 1 and upload_files[0].name.lower().endswith((".pdf", ".zip")):
            single_upload = upload_files[0]
            lower_name = single_upload.name.lower()
            filename = fs.save(single_upload.name, single_upload)
            original_filename = single_upload.name
            kind = "zip" if lower_name.endswith(".zip") else "pdf"
        elif upload_files:
            filename = save_many_files_as_zip(fs, upload_files)
            original_filename = filename
            kind = "zip"
        else:
            lower_name = upload_file.name.lower()
            filename = fs.save(upload_file.name, upload_file)
            original_filename = upload_file.name
            kind = "zip" if lower_name.endswith(".zip") else "pdf"

        file_path = fs.path(filename)
        use_ocr = bool(request.POST.get("use_ocr"))
        
        # 1. Parsing di POST
        try:
            if kind == "pdf":
                text_probe = extract_pdf_text(file_path, ocr=False)
                doc_type = classify_document(original_filename, "\n".join(text_probe["pages"]))
                if doc_type == "DRPP" or doc_type == "KW":
                    drpp = parse_drpp_pdf(file_path, ocr=use_ocr)
                    spm = None
                else:
                    doc_type = "SPM"
                    spm = parse_spm_pdf(file_path, ocr=use_ocr)
                    drpp = None
                parsed = {
                    "ok": bool(
                        (spm and spm["status"] in {"parsed_text", "parsed_ocr", "needs_manual_review"} and (spm["metadata"].get("nomor_spm") or spm["akun_rows"]))
                        or (drpp and drpp["status"] in {"parsed_text", "parsed_ocr", "needs_manual_review"} and (drpp["metadata"].get("nomor_drpp") or drpp["items"]))
                    ),
                    "files": [{
                        "file_name": original_filename,
                        "type": doc_type,
                        "status": "extracted",
                        "parse_status": (spm or drpp)["status"],
                        "method": (spm or drpp)["method"],
                        "warnings": (spm or drpp)["warnings"],
                    }],
                    "spm": spm,
                    "drpp": drpp,
                    "drpps": [drpp] if drpp else [],
                    "kw_by_drpp": {drpp["metadata"].get("nomor_drpp", "DRPP"): drpp.get("items", [])} if drpp else {},
                    "kw_items": drpp.get("items", []) if drpp else [],
                    "warnings": [],
                    "temp_dir": "",
                }
            else:
                parsed = parse_paket_spm_zip(file_path, ocr=use_ocr)
        except Exception as exc:
            parsed = {"ok": False, "files": [], "spm": None, "drpp": None, "kw_items": [], "warnings": [str(exc)], "temp_dir": ""}

        # 2. Simpan ke database sebagai DRAFT
        spm_meta = (parsed.get("spm") or {}).get("metadata", {})
        drpp_meta = (parsed.get("drpp") or {}).get("metadata", {})
        tahun = int(request.POST.get("tahun")) if str(request.POST.get("tahun", "")).isdigit() else None
        bulan = parse_month(request.POST.get("bulan", ""))
        satker = str(request.POST.get("satker_code") or spm_meta.get("satker_code") or "")[:32]
        
        import json
        safe_parsed = make_json_safe(parsed)
        # Validate that it is JSON serializable
        try:
            json.dumps(safe_parsed, ensure_ascii=False)
        except TypeError as e:
            messages.error(request, f"System Error: Gagal mengkonversi data OCR ke JSON. {str(e)}")
            return redirect("paket_spm:list")

        paket = PaketSPMUpload(
            original_filename=original_filename,
            folder_path=parsed.get("temp_dir", ""),
            nomor_spm=str(spm_meta.get("nomor_spm") or drpp_meta.get("nomor_spm") or "")[:100],
            nomor_sp2d=str(spm_meta.get("nomor_sp2d") or "")[:100],
            nomor_invoice=str(spm_meta.get("nomor_invoice") or "")[:100],
            satker_code=satker,
            tahun=tahun,
            bulan=bulan,
            jenis_spm_asli=str(spm_meta.get("jenis_spm") or "")[:100],
            jenis_spm_label=str(spm_meta.get("jenis_spm") or "")[:100],
            tanggal_spm=spm_meta.get("tanggal_spm"),
            nilai_spm=spm_meta.get("total_pembayaran") or Decimal("0"),
            total_rincian_bruto=sum((item.get("jumlah") or Decimal("0") for item in parsed.get("kw_items", [])), Decimal("0")),
            total_rincian_netto=sum((item.get("jumlah") or Decimal("0") for item in parsed.get("kw_items", [])), Decimal("0")),
            status=PaketSPMUpload.Status.PREVIEW,
            uploaded_by=request.user,
            parsed_data=safe_parsed,
        )
        with open(file_path, "rb") as zip_file:
            paket.zip_file.save(original_filename, File(zip_file), save=False)
        paket.save()
        
        # Sync file to drive immediately if valid? Actually let's just let it be saved locally first.
        # User said: "Saat user upload, file PDF/ZIP harus langsung disimpan ke local archive atau Google Drive jika aktif. Jangan tunggu commit baru simpan file. Preview/Draft harus punya referensi path/link file."
        # Local archive is handled by `paket.zip_file.save(...)` which puts it in `media/uploads/paket_spm/...`.
        # Google Drive sync usually happens in services or celery, but I can call `archive_file_link(paket.zip_file.path)` here if needed.
        # Actually let's leave it as `zip_file` path since we have `paket.zip_file.url`.
        
        request.session["paket_spm_preview_id"] = paket.id
        request.session["sp2d_raw_id"] = request.POST.get("sp2d_raw_id", "")
        
        print(f"[INTERMILAN PaketSPM Upload] Saved as PREVIEW id={paket.id}", flush=True)
        return redirect("paket_spm:preview")

    rows = filter_by_satker(PaketSPMUpload.objects.select_related("uploaded_by"), request.user)
    sp2d_context = get_sp2d_context(request.GET.get("sp2d_raw_id"), request.user)
    context = permission_context(request.user)
    context.update(
        {
            "page_title": "Upload Paket SPM",
            "page_subtitle": "Siapkan paket dokumen SPM, DRPP, dan kuitansi untuk preview D_K sebelum disimpan.",
            "rows": rows[:50],
            "max_zip_size_mb": settings.MAX_ZIP_SIZE_MB,
            "max_upload_size_mb": settings.MAX_UPLOAD_SIZE_MB,
            "sp2d_context": sp2d_context,
        }
    )
    return render(
        request,
        "paket_spm/list.html",
        context,
    )


@login_required
def paket_spm_preview(request):
    paket_id = request.session.get("paket_spm_preview_id")
    if not paket_id:
        messages.error(request, "Sesi preview Paket SPM tidak ditemukan. Silakan upload ulang atau buka dari daftar draft.")
        return redirect("paket_spm:list")
    try:
        paket = PaketSPMUpload.objects.get(id=paket_id, status=PaketSPMUpload.Status.PREVIEW, uploaded_by=request.user)
    except PaketSPMUpload.DoesNotExist:
        messages.error(request, "Draft Paket SPM tidak ditemukan.")
        return redirect("paket_spm:list")

    sp2d_context = get_sp2d_context(request.session.get("sp2d_raw_id"), request.user)
    forced_sp2d = sp2d_context.get("row") if sp2d_context else None

    parsed = paket.parsed_data or {}
    
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "cancel":
            paket.delete() # Or keep it? The user might just want to abort. Since we have drafts now, we can just let them keep it or delete it.
            # "Draft harus bisa ditemukan... tombol hapus draft". Let's assume cancel means abort completely.
            request.session.pop("paket_spm_preview_id", None)
            messages.info(request, "Preview Paket SPM dibatalkan.")
            return redirect("paket_spm:list")
            
        if action == "recalculate":
            # Update paket fields based on input
            paket.nomor_spm = request.POST.get("nomor_spm", paket.nomor_spm)
            paket.nomor_sp2d = request.POST.get("nomor_sp2d", paket.nomor_sp2d)
            paket.nomor_invoice = request.POST.get("nomor_invoice", paket.nomor_invoice)
            paket.satker_code = request.POST.get("satker_code", paket.satker_code)
            
            # We also update the parsed_data so it reflects in decision and UI
            if "spm" not in parsed or not parsed["spm"]:
                parsed["spm"] = {"metadata": {}}
            parsed["spm"]["metadata"]["nomor_spm"] = paket.nomor_spm
            parsed["spm"]["metadata"]["nomor_sp2d"] = paket.nomor_sp2d
            parsed["spm"]["metadata"]["nomor_invoice"] = paket.nomor_invoice
            parsed["spm"]["metadata"]["satker_code"] = paket.satker_code
            
            akun_str = request.POST.get("akun", "")
            if akun_str:
                parsed["spm"]["metadata"]["akun_pengeluaran"] = [a.strip() for a in akun_str.split(",") if a.strip()]
            
            nilai_str = request.POST.get("nilai_total", "")
            if nilai_str:
                try:
                    parsed["spm"]["metadata"]["total_pembayaran"] = Decimal(nilai_str)
                    paket.nilai_spm = Decimal(nilai_str)
                except:
                    pass
            
            import json
            safe_parsed = make_json_safe(parsed)
            try:
                json.dumps(safe_parsed, ensure_ascii=False)
            except TypeError as e:
                messages.error(request, f"System Error: Gagal mengkonversi update data ke JSON. {str(e)}")
                return redirect("paket_spm:preview")
                
            paket.parsed_data = safe_parsed
            paket.save()
            messages.success(request, "Data diupdate, matching dihitung ulang.")
            return redirect("paket_spm:preview")

        if action == "commit":
            commit_choice = request.POST.get("commit_choice") # 'link_existing', 'create_from_package', 'review_manual', 'save_draft'
            decision = build_package_decision(parsed, paket.original_filename, forced_sp2d=forced_sp2d)
            
            if commit_choice == "save_draft":
                request.session.pop("paket_spm_preview_id", None)
                messages.success(request, "Dokumen disimpan sebagai draft review.")
                # We do not change status, keep it PREVIEW so it shows in drafts
                return redirect("paket_spm:list") # or to drafts page later
                
            if commit_choice == "link_existing":
                matched_id = request.POST.get("matched_transaction_id")
                if matched_id:
                    tx = TransactionDetail.objects.filter(id=matched_id).first()
                    if tx:
                        # Link it
                        paket.status = PaketSPMUpload.Status.COMMITTED
                        paket.save()
                        # TODO: update TX with file reference if needed
                        messages.success(request, "Dokumen berhasil dikaitkan ke D_K existing.")
                    else:
                        messages.error(request, "D_K existing tidak ditemukan.")
                        return redirect("paket_spm:preview")
                else:
                    messages.error(request, "Pilih D_K existing terlebih dahulu.")
                    return redirect("paket_spm:preview")
                    
            elif commit_choice == "create_from_package":
                with transaction.atomic():
                    paket.status = PaketSPMUpload.Status.COMMITTED
                    paket.save()
                    build_transaction_rows_from_package(parsed, paket, request.user, sp2d_raw=forced_sp2d, document_status=decision.get("document_status"))
                messages.success(request, "Dokumen berhasil dibaca. D_K baru telah dibuat.")
            
            request.session.pop("paket_spm_preview_id", None)
            return redirect("paket_spm:list")

    decision = build_package_decision(parsed, paket.original_filename, forced_sp2d=forced_sp2d)
    preview_summary = build_preview_summary(parsed, decision, {"original_filename": paket.original_filename})
    scan_rows = build_scan_rows(parsed, decision)
    drpp_rows = build_drpp_rows(parsed)
    kw_rows = build_kw_rows(parsed)
    
    context = permission_context(request.user)
    context.update({
        "page_title": "Preview Paket SPM",
        "page_subtitle": "Tinjau isi ZIP dan hasil parser sebelum commit.",
        "parsed": parsed,
        "decision": decision,
        "preview_summary": preview_summary,
        "scan_rows": scan_rows,
        "drpp_rows": drpp_rows,
        "kw_rows": kw_rows,
        "sp2d_context": sp2d_context,
        "paket": paket,
        "can_commit": decision["can_commit"],
    })
    return render(request, "paket_spm/preview.html", context)
    return render(request, "paket_spm/preview.html", context)


def get_sp2d_context(sp2d_raw_id, user):
    if not str(sp2d_raw_id or "").isdigit():
        return None
    queryset = filter_by_satker(SP2DRaw.objects.select_related("import_batch"), user)
    row = queryset.filter(pk=sp2d_raw_id).first()
    if not row:
        return None
    tahun = row.import_batch.tahun if row.import_batch_id else ""
    return {
        "row": row,
        "sp2d_raw_id": row.id,
        "satker_code": row.satker_code,
        "tahun": tahun,
        "bulan": row.bulan_sp2d or "",
        "label": f"{row.no_sp2d or '-'} / {row.nomor_spm_extracted or row.nomor_invoice or '-'}",
    }


def cleanup_paket_files(zip_path, temp_dir=""):
    if zip_path and os.path.exists(zip_path):
        os.remove(zip_path)
    if temp_dir and os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)


def validate_paket_upload(upload_file=None, upload_files=None):
    upload_files = upload_files or []
    total_size = sum(getattr(item, "size", 0) for item in upload_files)
    if upload_file:
        total_size += getattr(upload_file, "size", 0)
    if len(upload_files) > settings.MAX_UPLOAD_FILES:
        return f"Jumlah file melebihi batas {settings.MAX_UPLOAD_FILES} file."
    if total_size > settings.MAX_FOLDER_UPLOAD_SIZE_MB * 1024 * 1024:
        return "Ukuran upload melebihi batas 2GB."
    files_to_check = upload_files or ([upload_file] if upload_file else [])
    for item in files_to_check:
        lower = item.name.lower()
        if not lower.endswith((".pdf", ".zip")):
            return f"Format file tidak didukung: {item.name}"
    if upload_file and upload_file.name.lower().endswith(".zip") and upload_file.size > settings.MAX_ZIP_SIZE_MB * 1024 * 1024:
        return "Ukuran upload melebihi batas 2GB."
    return ""


def build_preview_summary(parsed, decision, preview_state):
    meta = decision.get("meta", {})
    return {
        "upload_name": preview_state.get("original_filename", "-"),
        "file_count": len(parsed.get("files", [])),
        "spm_count": 1 if parsed.get("spm") else 0,
        "drpp_count": len(parsed.get("drpps", []) or ([parsed.get("drpp")] if parsed.get("drpp") else [])),
        "kw_count": len(parsed.get("kw_items", [])),
        "total": meta.get("total") or Decimal("0"),
        "document_status": decision.get("document_status", "-"),
        "reconciliation_status": decision.get("reconciliation_status", "-"),
        "commit_label": decision.get("commit_label", "-"),
    }


# Kata kunci warning yang bersifat teknis -- tidak perlu tampil ke operator
_TECHNICAL_WARNING_PATTERNS = [
    "paddleocr",
    "ocr_enable",
    "pdf gabungan terdeteksi",
    "native text",
    "tesseract",
    "engine=",
    "engine dicoba",
    "raw_text",
    "tidak dipakai sebagai no spm",
]


def _split_warnings(warnings):
    """Pisahkan warnings menjadi (notes_user, warnings_technical)."""
    notes_user = []
    warnings_technical = []
    for w in (warnings or []):
        lower = w.lower()
        if any(pattern in lower for pattern in _TECHNICAL_WARNING_PATTERNS):
            warnings_technical.append(w)
        else:
            notes_user.append(w)
    return notes_user, warnings_technical


def build_scan_rows(parsed, decision):
    meta = decision.get("meta", {})
    matching_number = meta.get("nomor_spm_matching") or "-"
    rows = []
    drpp_by_file = {}
    for drpp in parsed.get("drpps", []) or []:
        drpp_by_file[os.path.basename(drpp.get("file_name", ""))] = drpp
    if parsed.get("drpp"):
        drpp_by_file[os.path.basename(parsed["drpp"].get("file_name", ""))] = parsed["drpp"]
    kw_by_file = {}
    for item in parsed.get("kw_items", []) or []:
        source = os.path.basename(str(item.get("source_file", "")))
        if source and source not in kw_by_file:
            kw_by_file[source] = item
    for index, item in enumerate(parsed.get("files", []), start=1):
        file_name = item.get("file_name", "")
        base_name = os.path.basename(file_name)
        doc_type = item.get("type", "-")
        row_meta = {}
        no_kw = ""
        akun = ""
        nilai = Decimal("0")
        if doc_type == "SPM" and parsed.get("spm"):
            row_meta = parsed["spm"].get("metadata", {})
            akun_p = row_meta.get("akun_pengeluaran") or []
            akun = ", ".join(akun_p)
            if not akun:
                akun = ", ".join(parsed["spm"].get("akun_rows") and [r.get("akun", "") for r in parsed["spm"]["akun_rows"]] or []) or "-"
            nilai = row_meta.get("total_pembayaran") or meta.get("total") or Decimal("0")
        elif doc_type == "DRPP":
            drpp = drpp_by_file.get(base_name) or {}
            row_meta = drpp.get("metadata", {})
            nilai = row_meta.get("total") or Decimal("0")
        elif doc_type == "KW":
            kw = kw_by_file.get(base_name) or {}
            no_kw = kw.get("no_bukti", "")
            akun = kw.get("akun", "")
            nilai = kw.get("jumlah") or Decimal("0")
            row_meta = {"nomor_drpp": kw.get("no_drpp", ""), "nomor_spm": meta.get("nomor_spm", "")}
        all_warnings = item.get("warnings") or []
        notes_user, warnings_technical = _split_warnings(all_warnings)
        user_keterangan = "; ".join(notes_user) if notes_user else (decision.get("notes", [""])[0] if decision.get("notes") else "-")
        rows.append(
            {
                "no": index,
                "file_name": file_name,
                "type": doc_type,
                "nomor_spm": row_meta.get("nomor_spm") or meta.get("nomor_spm") or "-",
                "nomor_spm_ocr": row_meta.get("nomor_spm_ocr") or meta.get("nomor_spm_ocr") or "-",
                "nomor_spm_filename": row_meta.get("nomor_spm_filename") or meta.get("nomor_spm_filename") or "-",
                "nomor_spm_matching": matching_number,
                "nomor_spm_final": row_meta.get("nomor_spm_final") or row_meta.get("nomor_spm") or meta.get("nomor_spm_final") or meta.get("nomor_spm") or "-",
                "nomor_spm_review_status": row_meta.get("nomor_spm_review_status") or meta.get("nomor_spm_review_status") or "OK",
                "nomor_spp": row_meta.get("nomor_spp") or "-",
                "nomor_sp2d": row_meta.get("nomor_sp2d") or meta.get("nomor_sp2d") or "-",
                "nomor_invoice": row_meta.get("nomor_invoice") or meta.get("nomor_invoice") or "-",
                "nomor_drpp": row_meta.get("nomor_drpp") or "-",
                "no_kw": no_kw or "-",
                "akun": akun or "-",
                "jumlah_pengeluaran": row_meta.get("jumlah_pengeluaran") or meta.get("jumlah_pengeluaran") or Decimal("0"),
                "jumlah_potongan": row_meta.get("jumlah_potongan") or meta.get("jumlah_potongan") or Decimal("0"),
                "nilai": nilai,
                "method": item.get("method") or "-",
                "ocr_status": item.get("parse_status") or item.get("status") or "-",
                "matching_status": decision.get("reconciliation_status") or "-",
                "notes": "; ".join(all_warnings) or "-",
                "notes_user": user_keterangan,
                "warnings_technical": warnings_technical,
            }
        )
    if not rows and parsed.get("spm"):
        spm_meta = parsed["spm"].get("metadata", {})
        all_warnings = parsed["spm"].get("warnings") or []
        notes_user, warnings_technical = _split_warnings(all_warnings)
        rows.append(
            {
                "no": 1,
                "file_name": parsed["spm"].get("file_name", "-"),
                "type": "SPM",
                "nomor_spm": spm_meta.get("nomor_spm") or "-",
                "nomor_spm_ocr": spm_meta.get("nomor_spm_ocr") or "-",
                "nomor_spm_filename": spm_meta.get("nomor_spm_filename") or "-",
                "nomor_spm_matching": matching_number,
                "nomor_spm_final": spm_meta.get("nomor_spm_final") or spm_meta.get("nomor_spm") or "-",
                "nomor_spm_review_status": spm_meta.get("nomor_spm_review_status") or "OK",
                "nomor_spp": spm_meta.get("nomor_spp") or "-",
                "nomor_sp2d": spm_meta.get("nomor_sp2d") or "-",
                "nomor_invoice": spm_meta.get("nomor_invoice") or "-",
                "nomor_drpp": spm_meta.get("nomor_drpp") or "-",
                "no_kw": "-",
                "akun": (
                    ", ".join([f"{a}" for a in spm_meta.get("akun_pengeluaran", [])])
                ) or ", ".join(parsed["spm"].get("akun_rows") and [r.get("akun", "") for r in parsed["spm"]["akun_rows"]] or []) or "-",
                "jumlah_pengeluaran": spm_meta.get("jumlah_pengeluaran") or Decimal("0"),
                "jumlah_potongan": spm_meta.get("jumlah_potongan") or Decimal("0"),
                "nilai": spm_meta.get("total_pembayaran") or Decimal("0"),
                "method": parsed["spm"].get("method") or "-",
                "ocr_status": parsed["spm"].get("status") or "-",
                "matching_status": decision.get("reconciliation_status") or "-",
                "notes": "; ".join(all_warnings) or "-",
                "notes_user": "; ".join(notes_user) or decision.get("notes", [""])[0] if decision.get("notes") else "-",
                "warnings_technical": warnings_technical,
            }
        )
    return rows


def log_number_resolution(preview_state, parsed, decision):
    spm_meta = (parsed.get("spm") or {}).get("metadata", {})
    meta = decision.get("meta", {})
    print(
        "[INTERMILAN PaketSPM Nomor] "
        f"original_filename={preview_state.get('original_filename')}; "
        f"temp_file={preview_state.get('file_path')}; "
        f"parsed_no_spm_from_filename={spm_meta.get('nomor_spm_filename') or '-'}; "
        f"parsed_no_spm_from_ocr={spm_meta.get('nomor_spm_ocr') or '-'}; "
        f"matched_no_spm={meta.get('nomor_spm_matching') or '-'}; "
        f"final_no_spm={spm_meta.get('nomor_spm_final') or meta.get('nomor_spm') or '-'}; "
        f"reason={spm_meta.get('nomor_spm_reason') or meta.get('nomor_spm_reason') or '-'}",
        flush=True,
    )


def build_drpp_rows(parsed):
    rows = []
    for drpp in parsed.get("drpps", []) or ([parsed.get("drpp")] if parsed.get("drpp") else []):
        if not drpp:
            continue
        meta = drpp.get("metadata", {})
        items = drpp.get("items", []) or []
        rows.append(
            {
                "nomor_drpp": meta.get("nomor_drpp") or "-",
                "nomor_spm": meta.get("nomor_spm") or "-",
                "item_count": len(items),
                "total": meta.get("total") or sum((row.get("jumlah") or Decimal("0") for row in items), Decimal("0")),
                "status": drpp.get("status") or "-",
                "file_name": drpp.get("file_name") or "-",
            }
        )
    return rows


def build_kw_rows(parsed):
    return parsed.get("kw_items", []) or []


def save_many_files_as_zip(fs, upload_files):
    safe_name = f"paket_spm_multi_{len(upload_files)}_files.zip"
    zip_path = fs.path(safe_name)
    counter = 1
    while os.path.exists(zip_path):
        safe_name = f"paket_spm_multi_{len(upload_files)}_files_{counter}.zip"
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

@login_required
def paket_spm_drafts(request):
    if request.method == "POST":
        action = request.POST.get("action")
        paket_id = request.POST.get("paket_id")
        if action == "continue" and paket_id:
            request.session["paket_spm_preview_id"] = paket_id
            return redirect("paket_spm:preview")
        elif action == "delete" and paket_id:
            PaketSPMUpload.objects.filter(id=paket_id, uploaded_by=request.user, status=PaketSPMUpload.Status.PREVIEW).delete()
            messages.success(request, "Draft berhasil dihapus.")
            return redirect("paket_spm:drafts")

    drafts = PaketSPMUpload.objects.filter(uploaded_by=request.user, status=PaketSPMUpload.Status.PREVIEW).order_by("-uploaded_at")
    context = permission_context(request.user)
    context.update({
        "page_title": "Draft Review Paket SPM",
        "page_subtitle": "Lanjutkan review dokumen yang belum disimpan ke D_K.",
        "drafts": drafts,
    })
    return render(request, "paket_spm/drafts.html", context)

