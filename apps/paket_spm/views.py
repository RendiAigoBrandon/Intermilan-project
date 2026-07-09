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
from apps.core.parsers import classify_document, extract_pdf_text, parse_drpp_pdf, parse_month, parse_paket_spm_zip, parse_spm_pdf
from apps.documents.services.google_drive import archive_file_link
from apps.paket_spm.services import build_package_decision, build_transaction_rows_from_package
from apps.sp2d.models import SP2DRaw

from .models import PaketSPMPreviewItem, PaketSPMUpload


@login_required
def paket_spm_list(request):
    if request.method == "POST":
        old_preview = request.session.pop("paket_spm_preview", None)
        if old_preview:
            cleanup_paket_files(old_preview.get("file_path"), "")
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
        request.session["paket_spm_preview"] = {
            "file_path": fs.path(filename),
            "original_filename": original_filename,
            "satker_code": request.POST.get("satker_code", ""),
            "tahun": request.POST.get("tahun", ""),
            "bulan": request.POST.get("bulan", ""),
            "sp2d_raw_id": request.POST.get("sp2d_raw_id", ""),
            "ocr": bool(request.POST.get("use_ocr")),
            "kind": kind,
        }
        print(
            "[INTERMILAN PaketSPM Upload] "
            f"original_filename={original_filename}; temp_path={fs.path(filename)}; kind={kind}",
            flush=True,
        )
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
    preview_state = request.session.get("paket_spm_preview")
    if not preview_state:
        messages.error(request, "Sesi preview Paket SPM tidak ditemukan.")
        return redirect("paket_spm:list")
    file_path = preview_state["file_path"]
    if not os.path.exists(file_path):
        messages.error(request, "File ZIP sementara hilang. Silakan upload ulang.")
        request.session.pop("paket_spm_preview", None)
        return redirect("paket_spm:list")
    sp2d_context = get_sp2d_context(preview_state.get("sp2d_raw_id"), request.user)
    forced_sp2d = sp2d_context.get("row") if sp2d_context else None

    try:
        if preview_state.get("kind") == "pdf":
            text_probe = extract_pdf_text(file_path, ocr=False)
            doc_type = classify_document(preview_state["original_filename"], "\n".join(text_probe["pages"]))
            if doc_type == "DRPP":
                drpp = parse_drpp_pdf(file_path, ocr=preview_state.get("ocr", False))
                spm = None
            elif doc_type == "KW":
                drpp = parse_drpp_pdf(file_path, ocr=preview_state.get("ocr", False))
                spm = None
            else:
                doc_type = "SPM"
                spm = parse_spm_pdf(file_path, ocr=preview_state.get("ocr", False))
                drpp = None
            parsed = {
                "ok": bool(
                    (spm and spm["status"] in {"parsed_text", "parsed_ocr", "needs_manual_review"} and (spm["metadata"].get("nomor_spm") or spm["akun_rows"]))
                    or (drpp and drpp["status"] in {"parsed_text", "parsed_ocr", "needs_manual_review"} and (drpp["metadata"].get("nomor_drpp") or drpp["items"]))
                ),
                "files": [{
                    "file_name": preview_state["original_filename"],
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
            parsed = parse_paket_spm_zip(file_path, ocr=preview_state.get("ocr", False))
    except Exception as exc:
        parsed = {"ok": False, "files": [], "spm": None, "drpp": None, "kw_items": [], "warnings": [str(exc)], "temp_dir": ""}

    decision = build_package_decision(parsed, preview_state.get("original_filename", ""), forced_sp2d=forced_sp2d)
    log_number_resolution(preview_state, parsed, decision)
    preview_summary = build_preview_summary(parsed, decision, preview_state)
    scan_rows = build_scan_rows(parsed, decision)
    drpp_rows = build_drpp_rows(parsed)
    kw_rows = build_kw_rows(parsed)
    can_commit = decision["can_commit"]
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "cancel":
            cleanup_paket_files(file_path, parsed.get("temp_dir"))
            request.session.pop("paket_spm_preview", None)
            messages.info(request, "Preview Paket SPM dibatalkan.")
            return redirect("paket_spm:list")
        if action == "commit":
            if not can_commit:
                messages.error(request, decision["decision_text"])
                return redirect("paket_spm:preview")
            spm_meta = (parsed.get("spm") or {}).get("metadata", {})
            drpp_meta = (parsed.get("drpp") or {}).get("metadata", {})
            decision_meta = decision["meta"]
            tahun = int(preview_state["tahun"]) if str(preview_state.get("tahun", "")).isdigit() else None
            bulan = parse_month(preview_state.get("bulan", ""))
            with transaction.atomic():
                paket = PaketSPMUpload(
                    original_filename=preview_state["original_filename"],
                    folder_path=parsed.get("temp_dir", ""),
                    nomor_spm=str(decision_meta.get("nomor_spm") or spm_meta.get("nomor_spm") or drpp_meta.get("nomor_spm") or "")[:100],
                    satker_code=str(preview_state.get("satker_code") or decision_meta.get("satker_code") or spm_meta.get("satker_code") or "")[:32],
                    tahun=tahun,
                    bulan=bulan,
                    jenis_spm_asli=str(decision_meta.get("jenis_spm") or spm_meta.get("jenis_spm") or "")[:100],
                    jenis_spm_label=str(decision_meta.get("jenis_spm") or spm_meta.get("jenis_spm") or "")[:100],
                    tanggal_spm=decision_meta.get("tanggal_spm") or spm_meta.get("tanggal_spm"),
                    nilai_spm=decision_meta.get("total") or spm_meta.get("total_pembayaran") or Decimal("0"),
                    total_rincian_bruto=sum((item.get("jumlah") or Decimal("0") for item in parsed.get("kw_items", [])), Decimal("0")),
                    total_rincian_netto=sum((item.get("jumlah") or Decimal("0") for item in parsed.get("kw_items", [])), Decimal("0")),
                    status=PaketSPMUpload.Status.COMMITTED,
                    uploaded_by=request.user,
                )
                with open(file_path, "rb") as zip_file:
                    paket.zip_file.save(preview_state["original_filename"], File(zip_file), save=False)
                paket.save()
                matched_transaction = decision.get("matched_transaction")
                created_transactions = []
                if decision["commit_action"] in {"create_from_package", "link_sp2d"}:
                    created_transactions = build_transaction_rows_from_package(
                        parsed,
                        paket,
                        request.user,
                        sp2d_raw=decision.get("matched_sp2d"),
                        document_status=decision["document_status"],
                    )
                    matched_transaction = created_transactions[0] if created_transactions else None
                    if decision.get("matched_sp2d") and created_transactions:
                        decision["matched_sp2d"].status = SP2DRaw.Status.COCOK
                        decision["matched_sp2d"].save(update_fields=["status", "updated_at"])
                preview_items = parsed.get("kw_items", [])
                if not preview_items and parsed.get("spm"):
                    preview_items = [
                        {"akun": row.get("akun", ""), "jumlah": Decimal("0"), "no_bukti": "", "keperluan": row.get("uraian", "")}
                        for row in parsed["spm"].get("akun_rows", [])
                    ]
                if not preview_items:
                    preview_items = [
                        {
                            "akun": "",
                            "jumlah": decision_meta.get("total") or Decimal("0"),
                            "no_bukti": "",
                            "no_drpp": decision_meta.get("nomor_drpp", ""),
                            "keperluan": "Dokumen Paket SPM tersimpan; rincian perlu review manual.",
                        }
                    ]
                preview_objects = []
                for index, item in enumerate(preview_items):
                    item_transaction = matched_transaction
                    if created_transactions and index < len(created_transactions):
                        item_transaction = created_transactions[index]
                    preview_objects.append(PaketSPMPreviewItem(
                        paket=paket,
                        helper=f"ZIP:{preview_state['original_filename']}",
                        akun=str(item.get("akun", ""))[:32],
                        bulan_sp2d=bulan,
                        nomor_spm=paket.nomor_spm,
                        tanggal_spm=paket.tanggal_spm,
                        jenis_spm=paket.jenis_spm_label,
                        no_kuitansi=str(item.get("no_bukti", ""))[:100],
                        no_drpp=str(item.get("no_drpp") or decision_meta.get("nomor_drpp") or drpp_meta.get("nomor_drpp") or "")[:100],
                        deskripsi=str(item.get("keperluan", "")),
                        nilai_bruto=item.get("jumlah") or Decimal("0"),
                        nilai_netto=item.get("jumlah") or Decimal("0"),
                        status=PaketSPMPreviewItem.Status.MATCHED if item_transaction else PaketSPMPreviewItem.Status.PERLU_DICEK,
                        catatan=f"Sumber Data: Paket SPM; Status Dokumen={decision['document_status']}; Status Rekonsiliasi={decision['reconciliation_status']}",
                        matched_transaction=item_transaction,
                    ))
                PaketSPMPreviewItem.objects.bulk_create(preview_objects)
            drive_result, _ = archive_file_link(
                file_path,
                user=request.user,
                jenis_dokumen="PAKET_SPM_ZIP" if preview_state.get("kind") == "zip" else "SPM",
                nama_file=preview_state["original_filename"],
                satker_code=paket.satker_code,
                nomor_spm=paket.nomor_spm,
                no_drpp=str(decision_meta.get("nomor_drpp") or drpp_meta.get("nomor_drpp", ""))[:100],
                catatan_extra=f"source=Paket SPM; parser_status={'ok' if parsed.get('ok') else 'needs_review'}; document_status={decision['document_status']}; reconciliation_status={decision['reconciliation_status']}; files={len(parsed.get('files', []))}",
                transaction_detail=matched_transaction,
            )
            for parsed_file in parsed.get("files", []):
                pdf_path = parsed_file.get("path")
                if pdf_path and os.path.exists(pdf_path):
                    archive_file_link(
                        pdf_path,
                        user=request.user,
                        jenis_dokumen=parsed_file.get("type", "UNKNOWN"),
                        nama_file=parsed_file.get("file_name", ""),
                        satker_code=paket.satker_code,
                        nomor_spm=paket.nomor_spm,
                        no_drpp=str(drpp_meta.get("nomor_drpp", "")),
                        no_kuitansi=parsed_file.get("file_name", "") if parsed_file.get("type") == "KW" else "",
                        catatan_extra=f"source=Paket SPM; parser_status={parsed_file.get('parse_status')}; method={parsed_file.get('method')}; document_status={decision['document_status']}; reconciliation_status={decision['reconciliation_status']}",
                        transaction_detail=matched_transaction,
                    )
            cleanup_paket_files(file_path, parsed.get("temp_dir"))
            request.session.pop("paket_spm_preview", None)
            if drive_result["status"] == "uploaded":
                messages.success(request, f"Paket SPM disimpan sebagai preview #{paket.pk} dan ZIP diarsipkan ke Google Drive.")
            elif drive_result["status"] == "local_archived":
                messages.warning(request, f"Paket SPM disimpan sebagai preview #{paket.pk}. Google Drive belum aktif; file disimpan ke local archive.")
            else:
                messages.warning(request, f"Paket SPM disimpan sebagai preview #{paket.pk}. Arsip Google Drive belum aktif, file belum diunggah ke Drive.")
            if preview_state.get("sp2d_raw_id"):
                return redirect("sp2d:inbox_detail", pk=preview_state["sp2d_raw_id"])
            return redirect("paket_spm:list")

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
        "preview_state": preview_state,
        "can_commit": can_commit,
    })
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
                "akun": ", ".join(parsed["spm"].get("akun_rows") and [r.get("akun", "") for r in parsed["spm"]["akun_rows"]] or []) or "-",
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
