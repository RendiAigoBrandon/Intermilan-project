import logging
import os
import shutil
import zipfile
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.core.exceptions import PermissionDenied
from django.core.files import File
from django.core.files.storage import FileSystemStorage
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.core.exceptions import UploadTechnicalError, UploadBusinessLimitError
from apps.accounts.access import filter_by_satker, permission_context
from apps.core.drpp_batch_parser import (
    PARSER_VERSION as DRPP_BATCH_VERSION,
    evaluate_drpp_group_commitability,
    evaluate_kkp_group_commitability,
    parse_drpp_upload_batch,
)
from apps.core.dk_draft_adapter import (
    DraftSource,
    DraftStatus,
    build_dk_drafts_from_parsed_data,
)
from apps.core.document_policy import SPMFamily, normalize_spm_family
from apps.core.ocr import check_ocr_environment
from apps.core.parsers import classify_document, extract_pdf_text, parse_date, parse_drpp_pdf, parse_month, parse_paket_spm_zip, parse_spm_pdf, make_json_safe
from apps.core.satker import get_official_satker_code, get_unit_code_from_satker
from apps.core.services import (
    find_or_create_package,
    enrich_from_spm,
    set_active_parent,
    get_active_parent_for_user,
    find_compatible_parent,
    validate_parent_compatibility,
    clear_active_parent as _clear_active_parent_service,
    create_drpp_preview_state,
    get_drpp_preview_state_by_session,
    commit_drpp_with_preview,
)
from apps.documents.services.checklist import mark_checklist_present
from apps.dk.services import refresh_transaction_document_status
from apps.dk.models import TransactionDetail
from apps.paket_spm.services import build_drpp_batch_rows, build_package_decision, build_transaction_rows_from_package, clean_optional, exact_transactions_for_package, lampiran_warnings, link_existing_package_documents, link_followup_document, link_paket_spm_source_document, merge_followup_into_existing_dk, normalize_key, parse_user_decimal, parsed_from_identity_probe, preview_blank_fields, preview_item_value, preview_review_fields, probe_package_identity, resolve_satker_from_existing_dk, short_document_number, upsert_drpp_group
from apps.sp2d.models import SP2DRaw

from .models import PaketSPMUpload

logger = logging.getLogger('paket_spm')


@login_required
def paket_spm_list(request):
    access_context = permission_context(request.user)
    if request.method == "POST":
        if not access_context["can_upload_document"]:
            raise PermissionDenied("Akun ini hanya memiliki akses baca.")
        if access_context["is_role_operator"] and not access_context["user_satker_code"]:
            raise PermissionDenied("Scope satker operator belum dikonfigurasi.")
        upload_file = request.FILES.get("file_paket")
        upload_files = request.FILES.getlist("document_files")
        if not upload_files:
            upload_files = request.FILES.getlist("file_paket")
            upload_file = None
        if not upload_file and not upload_files:
            messages.error(request, "Harap pilih PDF DRPP/kuitansi, folder PDF, atau ZIP.")
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
        elif upload_file is not None:
            lower_name = upload_file.name.lower()
            filename = fs.save(upload_file.name, upload_file)
            original_filename = upload_file.name
            kind = "zip" if lower_name.endswith(".zip") else "pdf"
        else:
            messages.error(request, "Harap pilih PDF DRPP/kuitansi, folder PDF, atau ZIP.")
            return redirect("paket_spm:list")

        file_path = fs.path(filename)
        use_ocr = True
        parsed = None

        # 1. Identity probe dulu. Jika D_K existing aman ditemukan, jangan jalankan full parser/OCR.
        sp2d_context = get_sp2d_context(request.POST.get("sp2d_raw_id"), request.user)
        sp2d_row = sp2d_context.get("row") if sp2d_context else None
        input_tahun = str(request.POST.get("tahun") or getattr(sp2d_row, "tahun", "") or "")
        requested_satker = str(
            request.POST.get("satker_code") or getattr(sp2d_row, "satker_code", "") or ""
        ).split(" - ")[0].strip()
        operator_satker = (
            access_context.get("user_satker_code") or ""
            if access_context.get("is_role_operator")
            else ""
        )
        input_satker = operator_satker or requested_satker
        identity_probe = {}
        try:
            parsed = parse_drpp_upload_batch(file_path, ocr=use_ocr)
        except UploadBusinessLimitError as exc:
            # Untuk pelanggaran batas bisnis, tolak dan bersihkan file upload.
            cleanup_paket_files(file_path)
            messages.error(request, str(exc))
            return redirect("paket_spm:list")
        except UploadTechnicalError as exc:
            cleanup_paket_files(file_path)
            messages.error(request, str(exc))
            return redirect("paket_spm:list")
        except Exception as exc:
            cleanup_paket_files(file_path)
            messages.error(request, f"Upload DRPP gagal diproses: {exc}")
            return redirect("paket_spm:list")

        # Jalur lama dipertahankan di source untuk fitur Paket SPM lain, tetapi
        # fitur pengguna ini selalu selesai melalui parser batch DRPP di atas.
        if parsed is not None:
            pass
        elif kind == "zip":
            try:
                parsed = parse_paket_spm_zip(file_path, ocr=use_ocr)
                parsed["identity_probe"] = identity_probe
                if identity_probe.get("warnings"):
                    parsed.setdefault("warnings", []).extend(identity_probe["warnings"])
            except Exception as exc:
                parsed = {"ok": False, "files": [], "spm": None, "drpp": None, "kw_items": [], "warnings": [str(exc)], "temp_dir": ""}
        elif identity_probe.get("exact_transaction_ids") and not identity_probe.get("needs_review"):
            parsed = parsed_from_identity_probe(identity_probe, original_filename)
        elif identity_probe.get("needs_review"):
            parsed = parsed_from_identity_probe(identity_probe, original_filename)
        else:
            try:
                if kind == "pdf":
                    text_probe = extract_pdf_text(file_path, ocr=False)
                    doc_type = classify_document(original_filename, "\n".join(text_probe["pages"]))
                    if doc_type == "DRPP":
                        drpp = parse_drpp_pdf(file_path, ocr=use_ocr)
                        spm = None
                    elif doc_type == "KW":
                        spm = None
                        drpp = None
                        parsed = {
                            "ok": False,
                            "files": [{
                                "file_name": original_filename,
                                "type": "KW",
                                "status": "needs_manual_review",
                                "parse_status": "needs_manual_review",
                                "method": "classifier",
                                "warnings": ["KW/Bukti wajib diunggah bersama DRPP."],
                            }],
                            "spm": None,
                            "drpp": None,
                            "drpps": [],
                            "kw_by_drpp": {},
                            "kw_items": [],
                            "warnings": ["KW/Bukti wajib diunggah bersama DRPP."],
                            "temp_dir": "",
                        }
                    elif doc_type in {"INVOICE", "FAKTUR", "BAST", "SSP", "SP2D", "LAMPIRAN_COA", "UNKNOWN"}:
                        spm = None
                        drpp = None
                        parsed = {
                            "ok": False,
                            "files": [{
                                "file_name": original_filename,
                                "type": doc_type,
                                "status": "needs_manual_review",
                                "parse_status": "needs_manual_review",
                                "method": "classifier",
                                "warnings": ["Dokumen pendukung tidak boleh otomatis menjadi transaksi baru."],
                            }],
                            "spm": None,
                            "drpp": None,
                            "drpps": [],
                            "kw_by_drpp": {},
                            "kw_items": [],
                            "warnings": ["Dokumen pendukung tidak boleh otomatis menjadi transaksi baru."],
                            "temp_dir": "",
                        }
                    else:
                        doc_type = "SPM"
                        spm = parse_spm_pdf(file_path, ocr=use_ocr)
                        drpp = None
                        active_doc = spm or {}
                        parsed = {
                            "ok": bool(
                                (spm and spm.get("status") in {"parsed_text", "parsed_ocr", "needs_manual_review"} and (spm.get("metadata", {}).get("nomor_spm") or spm.get("akun_rows")))
                            ),
                            "files": [{
                                "file_name": original_filename,
                                "type": doc_type,
                                "status": "extracted",
                                "parse_status": active_doc.get("status", "needs_manual_review"),
                                "method": active_doc.get("method", "parser"),
                                "warnings": active_doc.get("warnings", []),
                            }],
                            "spm": spm,
                            "drpp": drpp,
                            "drpps": [],
                            "kw_by_drpp": {},
                            "kw_items": [],
                            "warnings": [],
                            "temp_dir": "",
                        }
                    if doc_type == "DRPP":
                        active_doc = drpp or {}
                        parsed = {
                            "ok": bool(
                                (drpp and drpp.get("status") in {"parsed_text", "parsed_ocr", "needs_manual_review"} and (drpp.get("metadata", {}).get("nomor_drpp") or drpp.get("items")))
                            ),
                            "files": [{
                                "file_name": original_filename,
                                "type": doc_type,
                                "status": "extracted",
                                "parse_status": active_doc.get("status", "needs_manual_review"),
                                "method": active_doc.get("method", "parser"),
                                "warnings": active_doc.get("warnings", []),
                            }],
                            "spm": drpp,
                            "drpp": drpp,
                            "drpps": [drpp] if drpp else [],
                            "kw_by_drpp": {drpp.get("metadata", {}).get("nomor_drpp", "DRPP"): drpp.get("items", [])} if drpp else {},
                            "kw_items": drpp.get("items", []) if drpp else [],
                            "warnings": [],
                            "temp_dir": "",
                        }
                else:
                    parsed = parse_paket_spm_zip(file_path, ocr=use_ocr)
            except Exception as exc:
                parsed = {"ok": False, "files": [], "spm": None, "drpp": None, "kw_items": [], "warnings": [str(exc)], "temp_dir": ""}

        # Generate 15-column D_K drafts using adapter
        # This populates parsed_data["dk_drafts"] without modifying existing keys
        try:
            parsed["dk_drafts"] = build_dk_drafts_from_parsed_data(
                parsed,
                satker=satker,
                tahun=tahun,
                sp2d_match={"bulan": bulan, "cara_pembayaran": spm_meta.get("cara_pembayaran")} if bulan else None,
            )
        except Exception:
            # Adapter failure should not block upload - draft generation can be deferred
            parsed["dk_drafts"] = []

        # attach_shadow disabled — ollama_shadow module removed
        # Re-enable only after restoring apps.core.ollama_shadow

        # 2. Simpan ke database sebagai DRAFT
        spm_meta = (parsed.get("spm") or {}).get("metadata", {})
        if sp2d_row:
            if not parsed.get("spm"):
                parsed["spm"] = {"metadata": {}, "status": "parsed_text", "method": "selected_sp2d", "warnings": [], "detail_items": [], "akun_rows": []}
                spm_meta = parsed["spm"]["metadata"]
            selected_date = sp2d_row.tgl_sp2d or sp2d_row.tanggal_selesai_sp2d
            spm_meta["nomor_spm"] = spm_meta.get("nomor_spm") or sp2d_row.nomor_spm_extracted
            spm_meta["tanggal_spm"] = spm_meta.get("tanggal_spm") or selected_date
            spm_meta["tanggal_sp2d"] = spm_meta.get("tanggal_sp2d") or selected_date
            spm_meta["jenis_spm"] = spm_meta.get("jenis_spm") or sp2d_row.jenis_spm
            spm_meta["satker_code"] = spm_meta.get("satker_code") or sp2d_row.satker_code
            spm_meta["satker_app_code"] = spm_meta.get("satker_app_code") or sp2d_row.satker_code
            spm_meta["jumlah_pengeluaran"] = spm_meta.get("jumlah_pengeluaran") or sp2d_row.nilai_spm
            spm_meta["jumlah_potongan"] = spm_meta.get("jumlah_potongan") or sp2d_row.potongan
            spm_meta["total_pembayaran"] = spm_meta.get("total_pembayaran") or sp2d_row.nilai_sp2d
            spm_meta["bulan_sp2d"] = spm_meta.get("bulan_sp2d") or sp2d_row.bulan_sp2d
        drpp_list = parsed.get("drpps") or ([parsed.get("drpp")] if parsed.get("drpp") else [])
        drpp_meta = ((parsed.get("drpp") or (drpp_list[0] if drpp_list else {})) or {}).get("metadata", {})
        tanggal_spm = spm_meta.get("tanggal_spm")
        tanggal_sp2d = spm_meta.get("tanggal_sp2d")
        tahun = (
            (int(request.POST.get("tahun")) if str(request.POST.get("tahun", "")).isdigit() else None)
            or
            getattr(tanggal_spm, "year", None)
            or spm_meta.get("tahun")
            or getattr(sp2d_row, "tahun", None)
        )
        bulan = (
            getattr(tanggal_sp2d, "month", None)
            or getattr(sp2d_row, "bulan", None)
            or parse_month(str(getattr(sp2d_row, "bulan_nama", "") or ""))
        )
        document_satker = str(
            spm_meta.get("satker_app_code")
            or spm_meta.get("satker_code")
            or ""
        )[:32]
        if operator_satker and document_satker and document_satker != operator_satker:
            cleanup_paket_files(file_path, parsed.get("temp_dir", ""))
            messages.error(
                request,
                f"Satker dokumen {document_satker} berbeda dengan scope operator {operator_satker}. "
                "Upload tidak disimpan dan perlu diperiksa.",
            )
            return redirect("paket_spm:list")
        satker = (operator_satker or requested_satker or document_satker)[:32]
        parsed["paket_context"] = {"tahun": tahun, "bulan": bulan, "satker_code": satker}

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
            tanggal_spm=tanggal_spm,
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
    active_parent = get_active_parent_for_user(request=request, user=request.user)
    context = access_context
    context.update(
        {
            "page_title": "Upload DRPP",
            "page_subtitle": "Unggah satu paket DRPP beserta seluruh kuitansi yang terkait. Sistem akan mencocokkan data dengan SP2D dan menampilkan hasil sebelum disimpan ke D_K.",
            "rows": rows[:50],
            "max_zip_size_mb": settings.MAX_ZIP_SIZE_MB,
            "max_upload_size_mb": settings.MAX_UPLOAD_SIZE_MB,
            "sp2d_context": sp2d_context,
            "ocr_environment": check_ocr_environment(),
            "active_parent": active_parent,
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
    commit_choice = request.POST.get("commit_choice")
    if not paket_id:
        messages.error(request, "Sesi preview Upload DRPP tidak ditemukan. Silakan unggah ulang atau buka dari daftar draft.")
        return redirect("paket_spm:list")
    try:
        paket = PaketSPMUpload.objects.get(id=paket_id, status=PaketSPMUpload.Status.PREVIEW, uploaded_by=request.user)
    except PaketSPMUpload.DoesNotExist:
        messages.error(request, "Draft Upload DRPP tidak ditemukan.")
        return redirect("paket_spm:list")

    sp2d_context = get_sp2d_context(request.session.get("sp2d_raw_id"), request.user)
    forced_sp2d = sp2d_context.get("row") if sp2d_context else None

    parsed = paket.parsed_data or {}

    if request.method == "POST":
        access_context = permission_context(request.user)
        if not access_context["can_upload_document"]:
            raise PermissionDenied("Akun ini hanya memiliki akses baca.")
        if access_context["is_role_operator"] and not access_context["user_satker_code"]:
            raise PermissionDenied("Scope satker operator belum dikonfigurasi.")
        action = request.POST.get("action")
        if action == "cancel":
            zip_path = paket.zip_file.path if paket.zip_file else ""
            temp_dir = paket.folder_path
            paket.delete()
            cleanup_paket_files(zip_path, temp_dir)
            request.session.pop("paket_spm_preview_id", None)
            messages.info(request, "Preview Upload DRPP dibatalkan.")
            return redirect("paket_spm:list")

        # Form preview juga dikirim saat commit supaya edit manual pada baris
        # tidak hilang ketika pengguna langsung menekan Simpan ke D_K.
        if action in {"recalculate", "commit"}:
            def clean_text(val):
                v = str(val or "").strip()
                return "" if v == "-" else v

            # Update paket fields based on input
            paket.nomor_spm = clean_text(request.POST.get("nomor_spm", paket.nomor_spm))
            paket.nomor_sp2d = clean_text(request.POST.get("nomor_sp2d", paket.nomor_sp2d))
            paket.nomor_invoice = clean_text(request.POST.get("nomor_invoice", paket.nomor_invoice))

            raw_satker = (
                access_context.get("user_satker_code") or ""
                if access_context.get("is_role_operator")
                else clean_text(request.POST.get("satker_code", ""))
            )
            # Cascade satker: operator scope → top-level POST → existing paket satker
            # → forced_sp2d satker → paket_context satker → existing D_K by SPM+tahun
            if not raw_satker:
                raw_satker = paket.satker_code or (
                    (forced_sp2d.satker_code if forced_sp2d else None)
                    or parsed.get("paket_context", {}).get("satker_code") or ""
                )
            _dk_satker_ambiguous = False
            # Extract tahun from SPM date in parsed metadata as fallback when paket.tahun is None
            _spm_tahun = getattr(
                (parsed.get("spm") or {}).get("metadata", {}).get("tanggal_spm"), "year", None
            ) or (parsed.get("spm") or {}).get("metadata", {}).get("tahun")
            _tahun = paket.tahun or _spm_tahun
            if not raw_satker and paket.nomor_spm and _tahun:
                # Final fallback: look up satker from existing D_K records using the
                # normalized SPM body number and tahun. Returns None (not found),
                # '' (ambiguous/multiple satkers), or a single satker_code string.
                nomor_body = short_document_number(paket.nomor_spm) if paket.nomor_spm else ""
                dk_satker = resolve_satker_from_existing_dk(nomor_body, _tahun)
                if dk_satker == "":
                    # Ambiguous — multiple distinct satkers exist for this SPM body in D_K
                    _dk_satker_ambiguous = True
                elif dk_satker:
                    raw_satker = dk_satker
            paket.satker_code = raw_satker.split(" - ")[0].strip()[:32]

            # We also update the parsed_data so it reflects in decision and UI
            if "spm" not in parsed or not isinstance(parsed["spm"], dict):
                parsed["spm"] = {"metadata": {}}
            if "metadata" not in parsed["spm"] or not isinstance(parsed["spm"]["metadata"], dict):
                parsed["spm"]["metadata"] = {}
                
            _meta = parsed["spm"]["metadata"]
            if paket.nomor_spm: _meta["nomor_spm"] = paket.nomor_spm
            if paket.nomor_sp2d: _meta["nomor_sp2d"] = paket.nomor_sp2d
            if paket.nomor_invoice: _meta["nomor_invoice"] = paket.nomor_invoice
            if raw_satker: _meta["satker_code"] = paket.satker_code
            
            post_drpp = clean_text(request.POST.get("nomor_drpp"))
            if post_drpp: _meta["nomor_drpp"] = post_drpp

            # Remove premature serialization and decision building

            akun_str = request.POST.get("akun", "")
            if akun_str:
                parsed["spm"]["metadata"]["akun_pengeluaran"] = [a.strip() for a in akun_str.split(",") if a.strip()]

            nilai_str = clean_text(request.POST.get("nilai_total", "")).replace(".", "").replace(",", ".")
            if nilai_str:
                try:
                    parsed["spm"]["metadata"]["total_pembayaran"] = Decimal(nilai_str)
                    paket.nilai_spm = Decimal(nilai_str)
                except:
                    pass

            pengeluaran_str = clean_text(request.POST.get("jumlah_pengeluaran", "")).replace(".", "").replace(",", ".")
            if pengeluaran_str:
                try:
                    parsed["spm"]["metadata"]["jumlah_pengeluaran"] = Decimal(pengeluaran_str)
                except:
                    pass

            potongan_str = clean_text(request.POST.get("jumlah_potongan", "")).replace(".", "").replace(",", ".")
            if potongan_str:
                try:
                    parsed["spm"]["metadata"]["jumlah_potongan"] = Decimal(potongan_str)
                except:
                    pass

            row_count = int(request.POST.get("preview_row_count") or 0)
            if row_count:
                source_preview_rows = parsed.get("preview_rows") or parsed.get("kw_items") or []
                preview_rows = []
                for index in range(row_count):
                    row = {
                        "akun": clean_text(request.POST.get(f"rows-{index}-akun")),
                        "bulan_sp2d": clean_text(request.POST.get(f"rows-{index}-bulan_sp2d")),
                        "cara_pembayaran": clean_text(request.POST.get(f"rows-{index}-cara_pembayaran")),
                        "nomor_spm": clean_text(request.POST.get(f"rows-{index}-nomor_spm")),
                        "tanggal_spm": clean_text(request.POST.get(f"rows-{index}-tanggal_spm")),
                        "jenis_spm": clean_text(request.POST.get(f"rows-{index}-jenis_spm")),
                        "no_kuitansi": clean_text(request.POST.get(f"rows-{index}-no_kuitansi")),
                        "no_drpp": clean_text(request.POST.get(f"rows-{index}-no_drpp")),
                        "deskripsi": clean_text(request.POST.get(f"rows-{index}-deskripsi")),
                        "nilai_bruto": clean_text(request.POST.get(f"rows-{index}-nilai_bruto")),
                        "nilai_netto": clean_text(request.POST.get(f"rows-{index}-nilai_netto")),
                        "pembebanan": clean_text(request.POST.get(f"rows-{index}-pembebanan")),
                        "fp": clean_text(request.POST.get(f"rows-{index}-fp")),
                        "pph21": clean_text(request.POST.get(f"rows-{index}-pph21")),
                    }
                    if any(row.values()):
                        source_row = source_preview_rows[index] if index < len(source_preview_rows) else {}
                        review_fields = set(preview_review_fields(source_row))
                        blank_fields = set(preview_blank_fields(source_row))
                        for field in tuple(review_fields):
                            original = clean_text(preview_item_value(source_row, field))
                            if row.get(field) and row.get(field) != original:
                                review_fields.discard(field)
                        for field in tuple(blank_fields):
                            if row.get(field):
                                blank_fields.discard(field)
                        row["_preview_review_fields"] = sorted(review_fields)
                        row["_preview_blank_fields"] = sorted(blank_fields)
                        row["warnings"] = list(source_row.get("warnings") or [])
                        row["group_key"] = source_row.get("group_key") or ""
                        row["receipt_policy"] = source_row.get("receipt_policy") or ""
                        row["receipt_not_available_from_source"] = (
                            source_row.get("receipt_not_available_from_source") is True
                        )
                        row["field_provenance"] = dict(source_row.get("field_provenance") or {})
                        row["status_detail"] = "PERLU_REVIEW" if review_fields else "LENGKAP"
                        preview_rows.append(row)
                parsed["preview_rows"] = preview_rows

                # Update dk_drafts with manual edits (manual_confirmed source)
                # This preserves manual values when draft is saved
                _update_dk_drafts_with_manual_edits(parsed, preview_rows)

                # ================================================================
                # DRPP PARENT INHERITANCE: Wire active SPM parent to DRPP preview
                # Uses DRPPPreviewState as canonical frozen parent store.
                # ================================================================
                # Get active parent for this user
                active_parent = get_active_parent_for_user(request=request, user=request.user)
                parent_warning = None
                parent_conflict = None

                # Extract DRPP identity from parsed data
                drpp_satker = (
                    parsed.get("spm", {}).get("metadata", {}).get("satker_code")
                    or parsed.get("spm", {}).get("metadata", {}).get("satker_app_code")
                    or paket.satker_code
                    or ""
                ).strip()
                drpp_tahun = (
                    parsed.get("spm", {}).get("metadata", {}).get("tahun")
                    or paket.tahun
                    or None
                )
                if drpp_tahun:
                    try:
                        drpp_tahun = int(drpp_tahun)
                    except (ValueError, TypeError):
                        drpp_tahun = None
                drpp_nomor_spm = (
                    parsed.get("spm", {}).get("metadata", {}).get("nomor_spm")
                    or ""
                ).strip()

                # Check for SPM metadata presence (not just drpp_groups)
                has_own_spm = bool(
                    parsed.get("spm")
                    and parsed.get("spm", {}).get("metadata", {}).get("nomor_spm")
                )

                # Get the first DRPP number for preview state identification
                first_drpp = None
                if parsed.get("drpps"):
                    first_drpp = (parsed.get("drpps")[0].get("metadata") or {}).get("nomor_drpp") or "UNKNOWN"
                elif parsed.get("drpp_groups"):
                    first_drpp = (parsed.get("drpp_groups")[0].get("group_key") or parsed.get("drpp_groups")[0].get("no_drpp") or "UNKNOWN")
                else:
                    first_drpp = paket.original_filename or "UNKNOWN"

                if active_parent and active_parent.transaction_package and not has_own_spm:
                    # DRPP-only upload: inherit SPM fields from active parent
                    parent_package = active_parent.transaction_package

                    # Validate compatibility with DRPP evidence
                    is_compatible, conflict_msg = validate_parent_compatibility(
                        package=parent_package,
                        drpp_satker=drpp_satker or None,
                        drpp_tahun=drpp_tahun,
                        drpp_nomor_spm=drpp_nomor_spm or None,
                    )

                    if not is_compatible:
                        # Block preview if parent is incompatible
                        parent_conflict = conflict_msg
                        messages.warning(request, conflict_msg)
                        # Mark conflict in DRPPPreviewState so commit is blocked
                        create_drpp_preview_state(
                            request=request,
                            nomor_drpp=first_drpp,
                            satker_code=drpp_satker or paket.satker_code or "",
                            tahun=drpp_tahun or paket.tahun or 0,
                            parent_package=parent_package,
                            preview_data=parsed,
                            conflict=True,
                            conflict_message=conflict_msg,
                            user=request.user,
                        )
                    else:
                        # Inherit SPM fields where blank in preview_rows
                        inherited_fields = []
                        if preview_rows:
                            first = preview_rows[0]
                            if not first.get("nomor_spm") and parent_package.nomor_spm:
                                first["nomor_spm"] = parent_package.nomor_spm
                                inherited_fields.append("nomor_spm")
                            if not first.get("tanggal_spm") and parent_package.tanggal_spm:
                                first["tanggal_spm"] = parent_package.tanggal_spm
                                inherited_fields.append("tanggal_spm")
                            if not first.get("jenis_spm") and parent_package.jenis_spm:
                                first["jenis_spm"] = parent_package.jenis_spm
                                inherited_fields.append("jenis_spm")
                            if not first.get("cara_pembayaran") and getattr(parent_package, "cara_pembayaran", None):
                                first["cara_pembayaran"] = parent_package.cara_pembayaran
                                inherited_fields.append("cara_pembayaran")

                            # Also populate parsed["spm"]["metadata"] so that
                            # build_transaction_rows_from_package() (called on recalculate)
                            # reads the inherited SPM fields.  The above updates to
                            # preview_rows[0] are for the initial preview display;
                            # this update makes the inheritance persist through form
                            # recalculation.
                            if "metadata" not in parsed["spm"]:
                                parsed["spm"]["metadata"] = {}
                            _meta = parsed["spm"]["metadata"]
                            if not _meta.get("nomor_spm") and parent_package.nomor_spm:
                                _meta["nomor_spm"] = parent_package.nomor_spm
                            if not _meta.get("tanggal_spm") and parent_package.tanggal_spm:
                                _meta["tanggal_spm"] = parent_package.tanggal_spm
                            if not _meta.get("jenis_spm") and parent_package.jenis_spm:
                                _meta["jenis_spm"] = parent_package.jenis_spm
                            if not _meta.get("cara_pembayaran") and getattr(parent_package, "cara_pembayaran", None):
                                _meta["cara_pembayaran"] = parent_package.cara_pembayaran

                            # Update all KW items in drpp_groups
                            if parsed.get("drpp_groups"):
                                for group in parsed.get("drpp_groups"):
                                    if not group.get("items"):
                                        continue
                                    for item in group["items"]:
                                        if "nomor_spm" not in item and parent_package.nomor_spm:
                                            item["nomor_spm"] = parent_package.nomor_spm
                                        if "tanggal_spm" not in item and parent_package.tanggal_spm:
                                            item["tanggal_spm"] = parent_package.tanggal_spm
                                        if "jenis_spm" not in item and parent_package.jenis_spm:
                                            item["jenis_spm"] = parent_package.jenis_spm

                            # Update _meta from inherited first row
                            paket.nomor_spm = first.get("nomor_spm") or paket.nomor_spm

                            if inherited_fields:
                                parent_warning = f"Field SPM diwariskan dari parent aktif: {', '.join(inherited_fields)}"
                                messages.info(request, parent_warning)

                        # Create DRPPPreviewState as canonical frozen parent (DB-backed)
                        preview_state = create_drpp_preview_state(
                            request=request,
                            nomor_drpp=first_drpp,
                            satker_code=drpp_satker or paket.satker_code or "",
                            tahun=drpp_tahun or paket.tahun or 0,
                            parent_package=parent_package,
                            preview_data=parsed,
                            conflict=False,
                            conflict_message="",
                            user=request.user,
                        )

                        # Store preview_state_id in session for browser flow compatibility
                        request.session["drpp_preview_state_id"] = preview_state.pk
                        request.session.modified = True

                if preview_rows:
                    first = preview_rows[0]
                    _meta["nomor_spm"] = first.get("nomor_spm") or _meta.get("nomor_spm") or paket.nomor_spm
                    _meta["tanggal_spm"] = first.get("tanggal_spm") or _meta.get("tanggal_spm")
                    _meta["jenis_spm"] = first.get("jenis_spm") or _meta.get("jenis_spm")
                    _meta["cara_pembayaran"] = first.get("cara_pembayaran") or _meta.get("cara_pembayaran")
                    
                    # Update all KW items in parsed["drpp_groups"] if they exist
                    # to keep them in sync with preview_rows editing, ONLY if it's a single SPM package
                    if parsed.get("drpp_groups") and parsed.get("parser_version") != DRPP_BATCH_VERSION:
                        for group in parsed["drpp_groups"]:
                            if not group.get("items"): continue
                            for item in group["items"]:
                                if first.get("nomor_spm"): item["nomor_spm"] = first.get("nomor_spm")
                                if first.get("tanggal_spm"): item["tanggal_spm"] = first.get("tanggal_spm")
                                if first.get("jenis_spm"): item["jenis_spm"] = first.get("jenis_spm")
                                if first.get("cara_pembayaran"): item["cara_pembayaran"] = first.get("cara_pembayaran")
                    total_bruto = sum((parse_user_decimal(row.get("nilai_bruto")) for row in preview_rows), Decimal("0"))
                    total_netto = sum((parse_user_decimal(row.get("nilai_netto")) for row in preview_rows), Decimal("0"))
                    if parsed.get("parser_version") != DRPP_BATCH_VERSION:
                        parsed["spm"]["metadata"]["jumlah_pengeluaran"] = total_bruto
                        parsed["spm"]["metadata"]["total_pembayaran"] = total_netto
                        parsed["spm"]["metadata"]["jumlah_potongan"] = max(total_bruto - total_netto, Decimal("0"))
                    paket.nomor_spm = first.get("nomor_spm") or paket.nomor_spm
                    paket.nilai_spm = total_netto
                    if parsed.get("parser_version") == DRPP_BATCH_VERSION:
                        for group in parsed.get("drpp_groups") or []:
                            group_number = clean_optional(group.get("group_key") or group.get("no_drpp"))
                            edited_items = [
                                row
                                for row in preview_rows
                                if clean_optional(row.get("group_key") or row.get("no_drpp")) == group_number
                            ]
                            validator = (
                                evaluate_kkp_group_commitability
                                if group.get("is_kkp") else evaluate_drpp_group_commitability
                            )
                            validation = validator(
                                group.get("drpp") or {},
                                edited_items,
                            )
                            group["validation"] = validation
                            group["status"] = validation["status"]

            drpp_count = int(request.POST.get("drpp_row_count") or 0)
            if drpp_count:
                drpps = parsed.get("drpps") or ([parsed.get("drpp")] if parsed.get("drpp") else [])
                updated_drpps = []
                for index in range(drpp_count):
                    raw_item = drpps[index] if index < len(drpps) else None
                    current = raw_item if isinstance(raw_item, dict) else {"metadata": {}, "items": []}
                    if not isinstance(current.get("metadata"), dict):
                        current["metadata"] = {}
                    meta = current["metadata"]
                    meta["nomor_drpp"] = clean_text(request.POST.get(f"drpp-{index}-nomor_drpp", meta.get("nomor_drpp", "")))
                    meta["satker_code"] = clean_text(request.POST.get(f"drpp-{index}-satker")) or (
                        meta.get("satker_app_code") or meta.get("satker_code") or ""
                    )
                    meta["satker_app_code"] = meta["satker_code"]
                    raw_tahun = clean_text(request.POST.get(f"drpp-{index}-tahun", meta.get("tahun", "")))
                    meta["tahun"] = int(raw_tahun) if str(raw_tahun).isdigit() else raw_tahun
                    meta["tanggal_drpp"] = clean_text(request.POST.get(f"drpp-{index}-tanggal_drpp", meta.get("tanggal_drpp", "")))
                    meta["nomor_spm"] = clean_text(request.POST.get(f"drpp-{index}-nomor_spm", meta.get("nomor_spm", "")))
                    updated_drpps.append(current)
                parsed["drpps"] = updated_drpps
                parsed["drpp"] = updated_drpps[0] if updated_drpps else None

            kw_count = int(request.POST.get("kw_row_count") or 0)
            if kw_count:
                kw_items = []
                for index in range(kw_count):
                    row = {
                        "no_drpp": clean_text(request.POST.get(f"kw-{index}-no_drpp")),
                        "no_bukti": clean_text(request.POST.get(f"kw-{index}-no_bukti")),
                        "tanggal_bukti": clean_text(request.POST.get(f"kw-{index}-tanggal_bukti")),
                        "penerima": clean_text(request.POST.get(f"kw-{index}-penerima")),
                        "npwp": clean_text(request.POST.get(f"kw-{index}-npwp")),
                        "akun": clean_text(request.POST.get(f"kw-{index}-akun")),
                        "jumlah": parse_user_decimal(request.POST.get(f"kw-{index}-jumlah")),
                        "keperluan": clean_text(request.POST.get(f"kw-{index}-keperluan")),
                        "pembebanan": clean_text(request.POST.get(f"kw-{index}-pembebanan")),
                    }
                    if any(v not in ("", Decimal("0")) for v in row.values()):
                        kw_items.append(row)
                parsed["kw_items"] = kw_items
                for drpp in parsed.get("drpps") or []:
                    nomor_drpp = (drpp.get("metadata") or {}).get("nomor_drpp", "")
                    drpp["items"] = [{**item, "no_drpp": item.get("no_drpp") or nomor_drpp} for item in kw_items if (item.get("no_drpp") or nomor_drpp) == nomor_drpp]
                parsed["kw_by_drpp"] = {}
                for item in kw_items:
                    parsed["kw_by_drpp"].setdefault(item.get("no_drpp") or "TANPA_DRPP", []).append(item)

            keterangan = request.POST.get("keterangan", "")
            if keterangan and "spm" in parsed:
                if "warnings" not in parsed["spm"] or not isinstance(parsed["spm"]["warnings"], list):
                    parsed["spm"]["warnings"] = []
                if keterangan not in parsed["spm"]["warnings"]:
                    parsed["spm"]["warnings"].insert(0, keterangan)

            import json
            safe_parsed = make_json_safe(parsed)
            try:
                json.dumps(safe_parsed, ensure_ascii=False)
            except TypeError as e:
                messages.error(request, f"System Error: Gagal mengkonversi update data ke JSON. {str(e)}")
                return redirect("paket_spm:preview")

            paket.parsed_data = safe_parsed
            paket.save()

            if action == "recalculate":
                messages.success(request, "Data diupdate, matching dihitung ulang.")
                return redirect("paket_spm:preview")

        if action == "commit":
            # ================================================================
            # COMMIT REVALIDATION: Use DRPPPreviewState as canonical frozen parent
            # ================================================================
            preview_state_id = request.session.get("drpp_preview_state_id")
            commit_parent_package = None

            if preview_state_id:
                # Load DRPPPreviewState from DB
                preview_state = get_drpp_preview_state_by_session(request, user=request.user)
                if preview_state and preview_state.pk == preview_state_id:
                    # Verify frozen parent is still valid
                    if preview_state.selection_conflict:
                        messages.error(request, preview_state.conflict_message or "Terjadi konflik Seleksi. Buat preview ulang.")
                        return redirect("paket_spm:preview")

                    if not preview_state.is_frozen_parent_valid():
                        messages.error(request, "SPM parent yang dipilih di preview sudah tidak valid. Buat preview ulang.")
                        return redirect("paket_spm:preview")

                    commit_parent_package = preview_state.get_frozen_parent_for_commit()
                    if not commit_parent_package:
                        messages.error(request, "SPM parent tidak ditemukan. Buat preview ulang.")
                        return redirect("paket_spm:preview")

            if parsed.get("parser_version") == DRPP_BATCH_VERSION:
                commit_drpp = clean_optional(request.POST.get("commit_drpp"))
                if not commit_drpp:
                    messages.error(request, "Pilih kelompok DRPP yang akan disimpan.")
                    return redirect("paket_spm:preview")

                # GUP Reguler Validation
                spm_meta = parsed.get("spm", {}).get("metadata", {}) if parsed.get("spm") else {}
                family = normalize_spm_family(
                    spm_meta.get("jenis_spm") or spm_meta.get("jenis_tagihan")
                )
                is_gup_reguler = family == SPMFamily.GUP_REGULAR
                
                if is_gup_reguler:
                    groups = parsed.get("drpp_groups") or []
                    commit_group = next(
                        (
                            g for g in groups
                            if clean_optional(g.get("group_key") or g.get("no_drpp")) == commit_drpp
                        ),
                        None,
                    )
                    
                    if commit_drpp == "TANPA_DRPP":
                        messages.error(request, "Dokumen GUP Reguler diwajibkan memiliki DRPP (TANPA_DRPP tidak diizinkan).")
                        return redirect("paket_spm:preview")
                        
                    if commit_group:
                        items = [
                            row for row in build_drpp_batch_rows(parsed, paket, user=request.user)
                            if clean_optional(row.no_drpp) == commit_drpp
                        ]
                        extra_errors = []
                        if not spm_meta.get("nomor_spm") or not spm_meta.get("tanggal_spm") or not spm_meta.get("jenis_spm") or not spm_meta.get("cara_pembayaran"):
                            extra_errors.append("Atribut metadata parent (Nomor SPM, Tanggal, Jenis, Cara Pembayaran) belum lengkap.")
                        kw_numbers = [item.no_kuitansi for item in items if item.no_kuitansi]
                        if len(kw_numbers) != len(set(kw_numbers)):
                            extra_errors.append("Terdapat nomor kuitansi duplikat.")
                        if not spm_meta.get("nomor_spm"):
                            extra_errors.append("Parent SPM belum ditentukan.")
                        # Ambiguity check (before "Satker belum ditentukan") so it appears first
                        if _dk_satker_ambiguous:
                            extra_errors.append(
                                "Satker ambigu: nomor SPM ditemukan di beberapa satker berbeda dalam D_K. "
                                "Tetapkan satker secara manual atau hubungi administrator."
                            )
                        elif not spm_meta.get("satker_code") and not spm_meta.get("satker_app_code") and not spm_meta.get("satker_djpb_code"):
                            extra_errors.append("Satker belum ditentukan.")
                        # Safe conflict check: SP2D satker must agree with resolved document satker
                        if forced_sp2d and spm_meta.get("satker_code"):
                            sp2d_satker = (forced_sp2d.satker_code or "").strip()
                            doc_satker = (spm_meta.get("satker_code") or "").strip()
                            if sp2d_satker and doc_satker and sp2d_satker != doc_satker:
                                extra_errors.append(
                                    f"Satker dokumen ({doc_satker}) berbeda dari Satker SP2D ({sp2d_satker}). "
                                    "Satukan sebelum menyimpan."
                                )
                        validation = evaluate_drpp_group_commitability(
                            commit_group.get("drpp") or {},
                            items,
                            parser_validation=commit_group.get("validation") or {},
                            extra_errors=extra_errors,
                        )
                        errors = validation["errors"]
                        if errors:
                            for error in errors:
                                messages.error(request, error)
                            return redirect("paket_spm:preview")

                parser_sp2d = None
                if parsed.get("sp2d_parent_id"):
                    parser_sp2d = filter_by_satker(SP2DRaw.objects.all(), request.user).filter(pk=parsed["sp2d_parent_id"]).first()
                try:
                    with transaction.atomic():
                        rows = upsert_drpp_group(
                            parsed,
                            paket,
                            commit_drpp,
                            user=request.user,
                            sp2d_raw=forced_sp2d or parser_sp2d,
                            document_status="Lengkap",
                        )
                        link_followup_document(
                            paket,
                            rows,
                            user=request.user,
                            parsed=parsed,
                            document_status="Lengkap",
                        )
                        committed = parsed.setdefault("committed_drpps", [])
                        if commit_drpp not in committed:
                            committed.append(commit_drpp)
                        all_numbers = [
                            group.get("group_key") or group.get("no_drpp")
                            for group in parsed.get("drpp_groups") or []
                        ]
                        paket.status = (
                            PaketSPMUpload.Status.COMMITTED
                            if all(number in committed for number in all_numbers)
                            else PaketSPMUpload.Status.PREVIEW
                        )
                        paket.parsed_data = make_json_safe(parsed)
                        paket.save(update_fields=["status", "parsed_data"])
                except Exception as exc:
                    messages.error(request, str(exc))
                    return redirect("paket_spm:preview")

                if family == SPMFamily.GUP_KKP:
                    messages.success(request, "Paket KKP berhasil di-upsert ke D_K tanpa duplikasi.")
                else:
                    messages.success(request, f"DRPP {commit_drpp} berhasil di-upsert ke D_K tanpa duplikasi.")
                if paket.status == PaketSPMUpload.Status.COMMITTED:
                    request.session.pop("paket_spm_preview_id", None)
                    return redirect("paket_spm:list")
                return redirect("paket_spm:preview")

        commit_choice = request.POST.get("commit_choice") # 'link_existing', 'create_from_package', 'review_manual', 'save_draft', 'save_spm_parent'

        decision = build_package_decision(parsed, paket.original_filename, forced_sp2d=forced_sp2d, current_paket_id=paket.id)

        if commit_choice == "save_draft":
            request.session.pop("paket_spm_preview_id", None)
            messages.success(request, "Draft Upload DRPP berhasil disimpan dan dapat dibuka kembali dari daftar draft.")
            # We do not change status, keep it PREVIEW so it shows in drafts
            return redirect("paket_spm:drafts")

        if commit_choice == "save_spm_parent":
            # SPM-only: save/link the SPM parent document without creating TransactionDetail rows.
            # The parsed_data already contains SPM body (00100A), SPP (00100T), date, jenis, satker.
            # We mark the paket as COMMITTED so it's audited/archived.
            # Later DRPP uploads can resolve the parent context via satker+tahun+nomor_spm matching.
            if not parsed.get("spm"):
                messages.error(request, "SPM parent tidak ditemukan pada dokumen.")
                return redirect("paket_spm:preview")
            if parsed.get("drpps") or (parsed.get("kw_items") or []):
                messages.error(request, "Dokumen memiliki DRPP/Kuitansi. Gunakan alur simpan DRPP normal.")
                return redirect("paket_spm:preview")
            try:
                spm_meta = (parsed.get("spm") or {}).get("metadata") or {}

                # 1. Resolve satker and tahun
                raw_satker = (
                    spm_meta.get("satker_app_code")
                    or spm_meta.get("satker_code")
                    or paket.satker_code
                    or ""
                ).strip()
                _tanggal_spm = spm_meta.get("tanggal_spm")
                tahun = (
                    getattr(_tanggal_spm, "year", None)
                    or (int(_tanggal_spm[:4]) if isinstance(_tanggal_spm, str) and len(_tanggal_spm) >= 4 else None)
                    or spm_meta.get("tahun")
                    or paket.tahun
                    or None
                )

                # ================================================================
                # FIX C: Call build_package_decision to get matched_transaction.
                # build_package_decision does NOT mutate parsed["matched_transaction"],
                # so we must call it here to extract matched_transaction from the
                # decision dict.  This restores the validated D_K/SP2D reference
                # after make_json_safe serialization (which converted the Django model
                # instance to str(id)).
                #
                # The matched D_K may have a unit_code satker (e.g. "1300") while
                # the canonical satker is the official 6-digit code (e.g. "019937").
                # Use the matched D_K's satker for D_K queries, and the canonical
                # satker for TransactionPackage / ActiveParentSession.
                # ================================================================
                _decision = build_package_decision(
                    parsed, paket.original_filename, forced_sp2d=forced_sp2d, current_paket_id=paket.id
                )
                # Extract matched_transaction and metadata from decision
                _matched_tx = _decision.get("matched_transaction")  # may be a dict
                _matched_meta = _decision.get("meta", {})
                _matched_nomor = normalize_key(_matched_meta.get("nomor_spm_matching") or "")

                # 2. Canonical satker: use official 6-digit code for TransactionPackage/ActiveParentSession
                canonical_satker = get_official_satker_code(raw_satker)
                if canonical_satker:
                    satker_code = canonical_satker
                else:
                    satker_code = raw_satker  # already 6-digit or unknown

                # 3. Canonical nomor_spm: prefer matched D_K number over document number
                doc_nomor = (spm_meta.get("nomor_spm") or "").strip()
                nomor_spm = _matched_nomor if _matched_nomor else doc_nomor

                if not satker_code or not tahun or not nomor_spm:
                    messages.error(request, "Satker, tahun, atau nomor SPM belum lengkap untuk disimpan sebagai parent.")
                    return redirect("paket_spm:preview")

                # Retrieve matched D_K by PK from the decision dict, then validate against canonical identity.
                # PK-based retrieval guarantees we get the exact matched row.  Canonicalize both sides
                # using get_official_satker_code() so the comparison is safe whether the D_K row stores
                # unit_code ("1300") or canonical satker ("019937").  Do NOT mutate the D_K row.
                _mt_pk = _matched_tx.get("id") if isinstance(_matched_tx, dict) else None

                package, package_created = find_or_create_package(
                    satker_code=satker_code,
                    tahun=int(tahun),
                    nomor_spm=nomor_spm,
                    user=request.user,
                )

                # 3. Enrich package with SPM data
                enrich_from_spm(
                    package=package,
                    tanggal_spm=spm_meta.get("tanggal_spm"),
                    jenis_spm=spm_meta.get("jenis_spm"),
                    nilai_spm=spm_meta.get("total_pembayaran"),
                    deskripsi=spm_meta.get("uraian", ""),
                    source_filename=paket.original_filename,
                    user=request.user,
                )

                # 4. Find and enrich existing TransactionDetail rows for this package.
                #
                # Strategy: always populate existing_dk_rows with the correct D_K rows
                # for the canonical identity (satker_code + tahun + nomor_spm).
                # First, try the PK-based lookup from build_package_decision result.
                # Then, as a fallback, query by canonical identity using the validated
                # nomor_spm (from D_K/SP2D evidence) to handle satker-code variants
                # (e.g. D_K stores "1300", document shows "019937").
                existing_dk_rows = []
                _mt_pk = _matched_tx.get("id") if isinstance(_matched_tx, dict) else None
                if _mt_pk:
                    try:
                        existing_dk_rows = list(
                            TransactionDetail.objects.filter(id=_mt_pk).order_by("id")
                        )
                    except Exception:
                        pass

                # Fallback: query by canonical identity (satker + tahun + validated nomor_spm).
                # This catches D_K rows even when the satker stored in D_K differs
                # from the document satker (e.g. "1300" vs "019937").
                # Only run when satker_code is populated.  An empty satker must not
                # trigger cross-satker matching.
                if not existing_dk_rows and satker_code and tahun and nomor_spm:
                    try:
                        # Resolve canonical satker for the D_K query.
                        # The document may carry the official 6-digit satker (e.g. "019937")
                        # while the existing D_K may carry the unit code (e.g. "1300").
                        # Try canonical first (for D_Ks created with official satker).
                        canonical_for_dk = get_official_satker_code(satker_code)
                        query_satker = canonical_for_dk if canonical_for_dk else satker_code
                        existing_dk_rows = list(
                            TransactionDetail.objects.filter(
                                satker_code=query_satker,
                                nomor_spm__iexact=nomor_spm,
                                tanggal_spm__year=int(tahun),
                            ).order_by("id")
                        )
                        # Fallback: if canonical didn't find anything, try the unit code
                        # (for D_Ks created with unit-code satker, e.g. "1300").
                        if not existing_dk_rows and canonical_for_dk:
                            unit_code = get_unit_code_from_satker(canonical_for_dk)
                            if unit_code:
                                existing_dk_rows = list(
                                    TransactionDetail.objects.filter(
                                        satker_code=unit_code,
                                        nomor_spm__iexact=nomor_spm,
                                        tanggal_spm__year=int(tahun),
                                    ).order_by("id")
                                )
                    except Exception:
                        pass

                if existing_dk_rows:
                    # Link SPM to existing D_K rows
                    for dk_row in existing_dk_rows:
                        mark_checklist_present(dk_row, "SPM", request.user)
                        refresh_transaction_document_status(dk_row, verified_document_type="SPM")

                    # Create document links for D_K rows
                    try:
                        link_paket_spm_source_document(
                            paket,
                            existing_dk_rows,
                            user=request.user,
                            parsed=parsed,
                            document_status="Lengkap SPM Utama",
                            existing_dk=True,
                        )
                    except Exception as link_exc:
                        logger.warning("[SPM PARENT] Document link failed (D_K exists), continuing: %s", link_exc)
                else:
                    # No existing D_K — still mark checklist for the package identity
                    # (D_K will be created later when DRPP is committed).
                    try:
                        link_paket_spm_source_document(
                            paket,
                            [],  # no transactions yet
                            user=request.user,
                            parsed=parsed,
                            document_status="Lengkap SPM Utama",
                            existing_dk=False,
                        )
                    except Exception as link_exc:
                        logger.warning("[SPM PARENT] Document link failed (standalone), continuing: %s", link_exc)

                # 5. Archive file (Drive if configured)
                source_path = paket.zip_file.path if paket.zip_file else ""
                if source_path:
                    from apps.documents.services.google_drive import archive_file_link
                    try:
                        archive_file_link(
                            source_path,
                            user=request.user,
                            jenis_dokumen="SPM",
                            nama_file=paket.original_filename,
                            satker_code=satker_code,
                            nomor_spm=nomor_spm,
                            no_drpp="",
                            no_kuitansi="",
                        )
                    except Exception as archive_exc:
                        logger.warning("[SPM PARENT] Drive archive failed, continuing: %s", archive_exc)

                # 6. Establish active SPM parent for DRPP uploads
                set_active_parent(
                    request=request,
                    package=package,
                    selection_method="SPM_SAVE",
                    selection_evidence={
                        "paket_spm_upload_id": paket.id,
                        "source_filename": paket.original_filename,
                    },
                    user=request.user,
                )

                paket.status = PaketSPMUpload.Status.COMMITTED
                paket.save(update_fields=["status"])

            except Exception as exc:
                logger.exception("[SPM PARENT] Save failed: %s", exc)
                messages.error(request, f"Gagal menyimpan SPM parent: {exc}")
                return redirect("paket_spm:preview")
            messages.success(request, "SPM parent berhasil disimpan.")
            request.session.pop("paket_spm_preview_id", None)
            return redirect("paket_spm:list")

        if commit_choice == "link_existing":
            matched_id = request.POST.get("matched_transaction_id")
            exact_rows = exact_transactions_for_package(parsed, paket)
            if exact_rows:
                try:
                    with transaction.atomic():
                        link_existing_package_documents(
                            paket,
                            exact_rows,
                            user=request.user,
                            parsed=parsed,
                            document_status=decision.get("document_status"),
                        )
                        paket.status = PaketSPMUpload.Status.COMMITTED
                        paket.save(update_fields=["status"])
                except Exception as e:
                    messages.error(request, str(e))
                    return redirect("paket_spm:preview")
                messages.success(request, "Dokumen berhasil dikaitkan ke seluruh grup D_K existing.")
            elif matched_id:
                tx = TransactionDetail.objects.filter(id=matched_id).first()
                if tx:
                    try:
                        with transaction.atomic():
                            link_existing_package_documents(
                                paket,
                                [tx],
                                user=request.user,
                                parsed=parsed,
                                document_status=decision.get("document_status"),
                            )
                            paket.status = PaketSPMUpload.Status.COMMITTED
                            paket.save(update_fields=["status"])
                    except Exception as e:
                        messages.error(request, str(e))
                        return redirect("paket_spm:preview")
                    messages.success(request, "Dokumen berhasil dikaitkan ke D_K existing.")
                else:
                    messages.error(request, "D_K existing tidak ditemukan.")
                    return redirect("paket_spm:preview")
            else:
                messages.error(request, "Pilih D_K existing terlebih dahulu.")
                return redirect("paket_spm:preview")

        elif commit_choice == "create_from_package":
            # SPM-only: document is SPM parent with no DRPP pages.
            # Must not create TransactionDetail rows from SPM-only uploads.
            if (
                bool(parsed.get("spm"))
                and not parsed.get("drpps")
                and not (parsed.get("kw_items") or [])
            ):
                messages.error(request, "Dokumen SPM tanpa DRPP tidak boleh membuat transaksi baru. Unggah DRPP terkait terlebih dahulu.")
                return redirect("paket_spm:preview")
            try:
                with transaction.atomic():
                    rows = build_transaction_rows_from_package(
                        parsed,
                        paket,
                        request.user,
                        sp2d_raw=forced_sp2d,
                        document_status=decision.get("document_status"),
                        save=True,
                    )
                    if not rows:
                        meta = decision.get("meta", {})
                        rows = list(TransactionDetail.objects.filter(
                            satker_code=meta.get("satker_code") or paket.satker_code,
                            nomor_spm__iexact=meta.get("nomor_spm") or paket.nomor_spm,
                            tanggal_spm__year=getattr(meta.get("tanggal_spm") or paket.tanggal_spm, "year", None),
                        ))
                    link_paket_spm_source_document(
                        paket,
                        rows,
                        user=request.user,
                        parsed=parsed,
                        document_status=decision.get("document_status"),
                    )
                    paket.status = PaketSPMUpload.Status.COMMITTED
                    paket.save(update_fields=["status"])
            except Exception as e:
                messages.error(request, str(e))
                return redirect("paket_spm:preview")

            messages.success(request, "Dokumen berhasil dibaca. D_K telah diperbarui/dibuat.")

        elif commit_choice == "update_existing":
            try:
                with transaction.atomic():
                    rows = merge_followup_into_existing_dk(
                        parsed,
                        paket,
                        user=request.user,
                        document_status=decision.get("document_status"),
                    )
                    paket.status = PaketSPMUpload.Status.COMMITTED
                    paket.save(update_fields=["status"])
            except Exception as e:
                messages.error(request, str(e))
                return redirect("paket_spm:preview")
            request.session.pop("paket_spm_preview_id", None)
            messages.success(request, "DRPP/KW berhasil memperbarui D_K existing.")
            satker = clean_optional(rows[0].satker_code if rows else paket.satker_code)
            nomor_spm = clean_optional(rows[0].nomor_spm if rows else paket.nomor_spm)
            return redirect(f"{reverse('dk:transaction_list')}?satker={satker}&q={nomor_spm}")

        request.session.pop("paket_spm_preview_id", None)
        return redirect("paket_spm:list")

    decision = build_package_decision(parsed, paket.original_filename, forced_sp2d=forced_sp2d, current_paket_id=paket.id)
    preview_summary = build_preview_summary(parsed, decision, {"original_filename": paket.original_filename})
    summary_document_status = preview_summary.get("document_status") or decision.get("document_status") or "-"

    # Render preview rows dynamically (without saving)
    rekon_errors = []
    if parsed.get("parser_version") == DRPP_BATCH_VERSION:
        transaction_rows = build_drpp_batch_rows(parsed, paket, request.user)
    elif decision.get("matched_transaction") and decision.get("commit_action") in {"link_existing", "update_existing"}:
        transaction_rows = exact_transactions_for_package(parsed, paket)
    else:
        try:
            transaction_rows = build_transaction_rows_from_package(parsed, paket, request.user, sp2d_raw=forced_sp2d, document_status=decision.get("document_status"), save=False, skip_existing=False)
        except ValueError as e:
            transaction_rows = []
            rekon_errors.append(str(e))

    sum_bruto = sum(row.nilai_bruto for row in transaction_rows)
    sum_netto = sum(row.nilai_netto for row in transaction_rows)

    spm_meta = dict((parsed.get("spm") or {}).get("metadata", {}))
    if spm_meta.get("tanggal_spm"):
        spm_meta["tanggal_spm"] = parse_date(spm_meta["tanggal_spm"])
    scan_rows = build_scan_rows(parsed, decision)
    drpp_rows = build_drpp_rows(parsed)
    kw_rows = build_kw_rows(parsed)
    document_checklist = build_document_checklist(parsed, decision)
    from apps.paket_spm.services import is_gup, is_tup, money_value
    spm_bruto = money_value(spm_meta.get("jumlah_pengeluaran"))
    spm_netto = money_value(spm_meta.get("total_pembayaran"))
    spm_potongan = money_value(spm_meta.get("jumlah_potongan"))

    if parsed.get("parser_version") != DRPP_BATCH_VERSION:
        # Rekonsiliasi SPM penuh hanya untuk parser Paket SPM lama. Upload DRPP
        # dapat merupakan sebagian dari satu SPM dan divalidasi per DRPP.
        diff_bruto = abs(sum_bruto - spm_bruto)
        if diff_bruto > 1 and spm_bruto > 0:
            rekon_errors.append(f"Total Bruto baris Rp{sum_bruto:,.0f}, sedangkan Bruto SPM Rp{spm_bruto:,.0f}. Selisih Rp{diff_bruto:,.0f}.")

        is_gu_package = is_gup(spm_meta.get("jenis_spm", "")) or is_tup(spm_meta.get("jenis_spm", ""))
        if is_gu_package:
            row_deduction = sum_bruto - sum_netto
            header_deduction = spm_potongan if spm_potongan > 0 else row_deduction
            diff_gu = abs((sum_netto + header_deduction) - sum_bruto)
            if diff_gu > 1 and sum_bruto > 0:
                rekon_errors.append(
                    f"Rekonsiliasi GUP belum balance: Netto baris + potongan = Rp{(sum_netto + header_deduction):,.0f}, "
                    f"sedangkan Bruto baris Rp{sum_bruto:,.0f}. Selisih Rp{diff_gu:,.0f}."
                )
        else:
            diff_netto = abs(sum_netto - spm_netto)
            if diff_netto > 1 and spm_netto > 0:
                rekon_errors.append(f"Total Netto baris Rp{sum_netto:,.0f}, sedangkan Pembayaran SPM Rp{spm_netto:,.0f}. Selisih Rp{diff_netto:,.0f}.")

    # Jika ada error, blokir tombol SIMPAN KE D_K
    transaction_groups = build_transaction_groups(parsed, transaction_rows)
    # Filter out TANPA_DRPP placeholder groups (created when SPM-only is uploaded).
    # These fake groups must not be shown as saveable DRPP rows.
    transaction_groups = [g for g in transaction_groups if g.get("no_drpp") != "TANPA_DRPP"]
    # Detect SPM-only: batch parser parsed SPM but no real DRPP pages were detected.
    # Groups with "TANPA_DRPP" are filtered out in transaction_groups above.
    spm_only = (
        bool(parsed.get("spm"))
        and not parsed.get("drpps")
        and not (parsed.get("kw_items") or [])
        and not transaction_groups
    )
    can_commit = (
        any(group["can_commit"] for group in transaction_groups)
        if transaction_groups
        else decision.get("can_commit", False)
    )
    if getattr(paket, "alokasi_potongan_ambigu", False):
        rekon_errors.append("Alokasi potongan ambigu untuk beberapa baris pengeluaran. Potongan tidak dapat dialokasikan secara eksplisit. Harap perbaiki nilai potongan per baris secara manual.")

    if rekon_errors and not transaction_groups:
        can_commit = False

    # Get active parent for preview context
    active_parent = get_active_parent_for_user(request=request, user=request.user)

    context = permission_context(request.user)
    context.update({
        "page_title": "Preview Upload DRPP",
        "page_subtitle": "Tinjau dan perbaiki 15 kolom per kelompok DRPP sebelum upsert ke D_K.",
        "parsed": parsed,
        "decision": decision,
        "preview_summary": preview_summary,
        "summary_document_status": summary_document_status,
        "transaction_rows": transaction_rows,
        "transaction_groups": transaction_groups,
        "spm_only": spm_only,
        "scan_rows": scan_rows,
        "drpp_rows": drpp_rows,
        "kw_rows": kw_rows,
        "ai_shadow_result": parsed.get("ai_shadow") or {},
        "document_checklist": document_checklist,
        "spm_meta": spm_meta,
        "spm_bruto": spm_bruto,
        "spm_netto": spm_netto,
        "sum_bruto": sum_bruto,
        "sum_netto": sum_netto,
        "rekon_errors": rekon_errors,
        "sp2d_context": sp2d_context,
        "paket": paket,
        "can_commit": can_commit,
        "lampiran_warnings": lampiran_warnings(parsed),
        "active_parent": active_parent,
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
    document_status = decision.get("document_status") or "-"
    if parsed.get("parser_version") == DRPP_BATCH_VERSION:
        groups = parsed.get("drpp_groups") or []
        balanced = sum(1 for group in groups if (group.get("validation") or {}).get("status") == "BALANCE")
        is_kkp = parsed.get("spm_family") == SPMFamily.GUP_KKP.value
        kkp_reference_count = sum(1 for group in groups if group.get("is_kkp"))
        drpp_count = sum(1 for group in groups if not group.get("is_kkp"))
        return {
            "upload_name": preview_state.get("original_filename", "-"),
            "file_count": len(parsed.get("files", [])),
            "spm_count": 1 if parsed.get("spm") else 0,
            "drpp_count": drpp_count,
            "kkp_reference_count": kkp_reference_count,
            "kw_count": len(parsed.get("kw_items", [])),
            "total": sum((parse_user_decimal(item.get("nilai_bruto") or item.get("jumlah")) for item in parsed.get("kw_items", [])), Decimal("0")),
            "document_status": "Siap ditinjau" if parsed.get("ok") else "Perlu Review",
            "reconciliation_status": (
                "Ringkasan KKP terbaca" if is_kkp and balanced else f"{balanced}/{len(groups)} DRPP balance"
            ),
            "commit_label": "Upsert paket KKP" if is_kkp else "Upsert per DRPP",
        }
    if lampiran_warnings(parsed) and document_status in {"-", "Lengkap"}:
        if parsed.get("spm") and (parsed.get("drpps") or parsed.get("drpp")) and parsed.get("kw_items"):
            document_status = "Lengkap dengan Peringatan Lampiran"
    return {
        "upload_name": preview_state.get("original_filename", "-"),
        "file_count": len(parsed.get("files", [])),
        "spm_count": 1 if parsed.get("spm") else 0,
        "drpp_count": len(parsed.get("drpps", []) or ([parsed.get("drpp")] if parsed.get("drpp") else [])),
        "kkp_reference_count": 0,
        "kw_count": len(parsed.get("kw_items", [])),
        "total": meta.get("total") or Decimal("0"),
        "document_status": document_status,
        "reconciliation_status": decision.get("reconciliation_status", "-"),
        "commit_label": decision.get("commit_label", "-"),
    }


def build_document_checklist(parsed, decision):
    spm = parsed.get("spm") or {}
    spm_meta = spm.get("metadata", {}) or {}
    drpps = parsed.get("drpps") or ([parsed.get("drpp")] if parsed.get("drpp") else [])
    kw_items = parsed.get("kw_items") or []
    if parsed.get("parser_version") == DRPP_BATCH_VERSION:
        if parsed.get("spm_family") == SPMFamily.GUP_KKP.value:
            return [
                {"label": "SPM parent", "status": "Terhubung" if spm else "Perlu diisi pada preview"},
                {"label": "Referensi KKP", "status": "Ringkasan KKP terbaca" if drpps else "Belum terbaca"},
                {"label": "DRPP", "status": "Tidak diwajibkan untuk GUP-KKP"},
                {"label": "Transaksi", "status": f"{len(kw_items)} baris terverifikasi" if kw_items else "Belum terbaca"},
                {"label": "SP2D pembanding", "status": "Terhubung" if parsed.get("sp2d_parent_id") or decision.get("matched_sp2d") else "Belum terhubung"},
            ]
        return [
            {"label": "SPM parent", "status": "Terhubung" if spm else "Perlu diisi pada preview"},
            {"label": "DRPP", "status": f"{len(drpps)} kelompok terbaca" if drpps else "Belum terbaca"},
            {"label": "Kuitansi", "status": f"{len(kw_items)} baris terverifikasi" if kw_items else "Belum terbaca"},
            {"label": "SP2D pembanding", "status": "Terhubung" if parsed.get("sp2d_parent_id") or decision.get("matched_sp2d") else "Belum terhubung"},
        ]
    return [
        {"label": "SPM", "status": "Tersedia" if spm else "Belum tersedia"},
        {"label": "SPP", "status": "Tersedia" if spm_meta.get("nomor_spp") else "Belum terbaca"},
        {"label": "Detail transaksi", "status": "Tersedia" if spm.get("detail_items") else "Belum terbaca"},
        {"label": "DRPP", "status": "Tersedia" if drpps else "Belum diunggah"},
        {"label": "KW/Bukti", "status": "Tersedia" if kw_items else "Belum diunggah"},
        {"label": "SP2D pembanding", "status": "Terhubung" if decision.get("matched_sp2d") else "Belum terhubung"},
    ]


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
                "satker": (
                    f"{row_meta.get('satker_app_code')} - {row_meta.get('satker_app_name')}"
                    if row_meta.get('satker_app_code')
                    else f"{row_meta.get('satker_djpb_code')} - {row_meta.get('satker_name_ocr')} (Perlu Mapping)"
                    if row_meta.get('satker_djpb_code')
                    else row_meta.get("satker_code") or meta.get("satker_code") or "Perlu Review"
                ),
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
                "satker": (
                    f"{spm_meta.get('satker_app_code')} - {spm_meta.get('satker_app_name')}"
                    if spm_meta.get('satker_app_code')
                    else f"{spm_meta.get('satker_djpb_code')} - {spm_meta.get('satker_name_ocr')} (Perlu Mapping)"
                    if spm_meta.get('satker_djpb_code')
                    else spm_meta.get("satker_code") or "Perlu Review"
                ),
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
    main_spm = ((parsed.get("spm") or {}).get("metadata", {}) or {}).get("nomor_spm")
    for drpp in parsed.get("drpps", []) or ([parsed.get("drpp")] if parsed.get("drpp") else []):
        if not drpp:
            continue
        meta = drpp.get("metadata", {})
        items = drpp.get("items", []) or []
        rows.append(
            {
                "nomor_drpp": meta.get("nomor_drpp") or "-",
                "nomor_spm": main_spm or meta.get("nomor_spm") or "-",
                "satker": meta.get("satker_app_code") or meta.get("satker_code") or "-",
                "tahun": meta.get("tahun") or "-",
                "tanggal_drpp": meta.get("tanggal_drpp") or "",
                "item_count": len(items),
                "total": meta.get("total") or sum((row.get("jumlah") or Decimal("0") for row in items), Decimal("0")),
                "status": drpp.get("status") or "-",
                "file_name": drpp.get("file_name") or "-",
            }
        )
    return rows


def build_transaction_groups(parsed, transaction_rows):
    if parsed.get("parser_version") != DRPP_BATCH_VERSION:
        return []
    metrics = parsed.get("metrics") or {}
    committed = set(parsed.get("committed_drpps") or [])
    seen_keys = {}
    duplicate_groups = set()
    for row in transaction_rows:
        row_group = clean_optional(getattr(row, "batch_group_key", "") or row.no_drpp)
        key = (
            clean_optional(row.satker_code).upper(),
            getattr(row.tanggal_spm, "year", None),
            clean_optional(row.nomor_spm).upper(),
            clean_optional(row.no_kuitansi).upper(),
            clean_optional(row.akun).upper(),
        )
        if key in seen_keys:
            duplicate_groups.update((seen_keys[key], row_group))
        else:
            seen_keys[key] = row_group

    output = []
    for group in parsed.get("drpp_groups") or []:
        number = clean_optional(group.get("no_drpp"))
        group_identifier = clean_optional(group.get("group_key") or number)
        rows = [
            row for row in transaction_rows
            if clean_optional(getattr(row, "batch_group_key", "") or row.no_drpp) == group_identifier
        ]
        drpp = group.get("drpp") or {}
        for row in rows:
            row.form_index = transaction_rows.index(row)
            if (
                (
                    not row.no_kuitansi
                    and not (
                        getattr(row, "receipt_policy", "") == "not_available_from_source"
                        and getattr(row, "receipt_not_available_from_source", False) is True
                    )
                )
                or not row.akun
                or row.nilai_bruto <= 0
                or not row.nomor_spm
                or not row.tanggal_spm
            ):
                row.batch_status = "GAGAL"
            elif getattr(row, "preview_review_fields", None) or row.batch_status == "PERLU_REVIEW" or not row.pembebanan:
                row.batch_status = "PERLU_REVIEW"
            else:
                row.batch_status = "LENGKAP"
        validator = (
            evaluate_kkp_group_commitability
            if group.get("is_kkp") else evaluate_drpp_group_commitability
        )
        validation = validator(
            drpp,
            rows,
            parser_validation=group.get("validation") or {},
            extra_errors=(
                ["Duplikat exact key ditemukan dalam upload yang sama."]
                if group_identifier in duplicate_groups
                else []
            ),
        )
        output.append(
            {
                **validation,
                "no_drpp": number,
                "is_kkp": bool(group.get("is_kkp")),
                "group_identifier": group_identifier,
                "display_label": (
                    f"Paket KKP {rows[0].nomor_spm if rows else ((parsed.get('spm') or {}).get('metadata') or {}).get('nomor_spm', '')}"
                    if group.get("is_kkp") else f"DRPP {number}"
                ),
                "rows": rows,
                "committed": group_identifier in committed,
                "ocr_seconds": metrics.get("ocr_seconds", 0),
                "page_total": metrics.get("page_total", 0),
                "unique_pages": metrics.get("unique_pages", 0),
                "ocr_pages": metrics.get("ocr_pages", 0),
            }
        )
    return output


def build_kw_rows(parsed):
    return parsed.get("kw_items", []) or []


def _update_dk_drafts_with_manual_edits(parsed, preview_rows):
    """
    Update dk_drafts with manual edits from preview form.

    When operator edits a field:
    - Update the row value
    - field_source becomes manual_confirmed
    - field_status becomes MANUAL_CONFIRMED
    - Old evidence remains preserved
    - Manual values are NOT overwritten by parser/enrichment

    Helper field remains read-only (derived from akun + no_kuitansi).
    """
    dk_drafts = parsed.get("dk_drafts", [])
    if not dk_drafts or not preview_rows:
        return

    # Get the 15 column field names (excluding helper which is read-only)
    editable_fields = [
        "akun", "bulan_sp2d", "cara_pembayaran", "nomor_spm", "tanggal_spm",
        "jenis_spm", "no_kuitansi", "no_drpp", "deskripsi", "nilai_bruto",
        "nilai_netto", "pembebanan", "fp", "pph21",
    ]

    for idx, edited_row in enumerate(preview_rows):
        if idx >= len(dk_drafts):
            break

        draft = dk_drafts[idx]
        row = draft.get("row", {})
        metadata = draft.get("review_metadata", {})

        for field in editable_fields:
            new_value = edited_row.get(field)
            if new_value is not None and new_value != "":
                # Check if value changed from original
                original_value = row.get(field)
                if new_value != original_value:
                    # Manual edit detected - update row with manual value
                    row[field] = new_value

                    # Update metadata
                    metadata["field_status"][field] = DraftStatus.MANUAL_CONFIRMED
                    metadata["field_source"][field] = DraftSource.MANUAL_CONFIRMED

                    # Preserve old evidence in field_evidence (keyed by old value)
                    if original_value is not None:
                        old_evidence_key = f"original_{field}"
                        metadata["field_evidence"][old_evidence_key] = original_value

                    # Recalculate helper if akun or no_kuitansi changed
                    if field in ("akun", "no_kuitansi"):
                        _recalculate_helper(row, metadata)

    # Update requires_review based on current status
    _update_requires_review(metadata)


def _recalculate_helper(row, metadata):
    """Recalculate helper from akun + no_kuitansi."""
    akun = row.get("akun")
    no_kuitansi = row.get("no_kuitansi")

    if akun and no_kuitansi:
        helper = f"{akun}{no_kuitansi}"
        row["helper"] = helper
        metadata["field_status"]["helper"] = DraftStatus.DERIVED
        metadata["field_source"]["helper"] = DraftSource.DERIVED
    else:
        row["helper"] = None
        metadata["field_status"]["helper"] = DraftStatus.REVIEW
        metadata["field_source"]["helper"] = DraftSource.NULL_REVIEW


def _update_requires_review(metadata):
    """Update requires_review flag based on current field statuses."""
    review_statuses = (DraftStatus.REVIEW, DraftStatus.MISSING)
    metadata["requires_review"] = any(
        status in review_statuses
        for status in metadata.get("field_status", {}).values()
    )


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
        "page_title": "Draft Review Upload DRPP",
        "page_subtitle": "Lanjutkan review kelompok DRPP yang belum disimpan ke D_K.",
        "drafts": drafts,
    })
    return render(request, "paket_spm/drafts.html", context)


@login_required
@require_POST
def change_active_parent(request):
    """Ganti SPM: clear current parent and redirect to SPM upload workflow."""
    cleared = _clear_active_parent_service(request=request, user=request.user)
    if cleared:
        messages.info(request, "SPM Parent sebelumnya telah dilepas. Silakan pilih atau upload SPM baru.")
    else:
        messages.info(request, "Silakan pilih atau upload SPM baru.")
    return redirect("paket_spm:list")


@login_required
@require_POST
def clear_active_parent(request):
    """Clear the active SPM parent (Lepas SPM Parent)."""
    cleared = _clear_active_parent_service(request=request, user=request.user)
    if cleared:
        messages.info(request, "SPM Parent aktif telah dilepas.")
    else:
        messages.info(request, "Tidak ada SPM Parent aktif yang perlu dilepas.")
    return redirect("paket_spm:list")
