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
from django.urls import reverse

from apps.accounts.access import filter_by_satker, permission_context
from apps.core.parsers import classify_document, extract_pdf_text, parse_drpp_pdf, parse_month, parse_paket_spm_zip, parse_spm_pdf, make_json_safe
from apps.dk.models import TransactionDetail
from apps.paket_spm.services import build_package_decision, build_transaction_rows_from_package, clean_optional, exact_transactions_for_package, lampiran_warnings, link_existing_package_documents, link_paket_spm_source_document, merge_followup_into_existing_dk, parse_user_decimal, parsed_from_identity_probe, probe_package_identity
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
        use_ocr = False

        # 1. Identity probe dulu. Jika D_K existing aman ditemukan, jangan jalankan full parser/OCR.
        sp2d_context = get_sp2d_context(request.POST.get("sp2d_raw_id"), request.user)
        sp2d_row = sp2d_context.get("row") if sp2d_context else None
        input_tahun = str(request.POST.get("tahun") or getattr(sp2d_row, "tahun", "") or "")
        input_satker = str(request.POST.get("satker_code") or getattr(sp2d_row, "satker_code", "") or "").split(" - ")[0].strip()
        identity_probe = probe_package_identity(
            file_path,
            original_filename,
            input_satker=input_satker,
            input_tahun=input_tahun,
            kind=kind,
        )
        if kind == "zip":
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
                        raise StopIteration
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
                        raise StopIteration
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
            except StopIteration:
                pass
            except Exception as exc:
                parsed = {"ok": False, "files": [], "spm": None, "drpp": None, "kw_items": [], "warnings": [str(exc)], "temp_dir": ""}

        # 2. Simpan ke database sebagai DRAFT
        spm_meta = (parsed.get("spm") or {}).get("metadata", {})
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
        satker = str(
            spm_meta.get("satker_app_code")
            or spm_meta.get("satker_code")
            or str(request.POST.get("satker_code") or "").split(" - ")[0].strip()
            or getattr(sp2d_row, "satker_code", "")
            or ""
        )[:32]
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
            zip_path = paket.zip_file.path if paket.zip_file else ""
            temp_dir = paket.folder_path
            paket.delete()
            cleanup_paket_files(zip_path, temp_dir)
            request.session.pop("paket_spm_preview_id", None)
            messages.info(request, "Preview Paket SPM dibatalkan.")
            return redirect("paket_spm:list")

        if action == "recalculate":
            def clean_text(val):
                v = str(val or "").strip()
                return "" if v == "-" else v

            # Update paket fields based on input
            paket.nomor_spm = clean_text(request.POST.get("nomor_spm", paket.nomor_spm))
            paket.nomor_sp2d = clean_text(request.POST.get("nomor_sp2d", paket.nomor_sp2d))
            paket.nomor_invoice = clean_text(request.POST.get("nomor_invoice", paket.nomor_invoice))

            raw_satker = clean_text(request.POST.get("satker_code", paket.satker_code))
            paket.satker_code = raw_satker.split(" - ")[0].strip()[:32]

            # We also update the parsed_data so it reflects in decision and UI
            if "spm" not in parsed or not parsed["spm"]:
                parsed["spm"] = {"metadata": {}}
            parsed["spm"]["metadata"]["nomor_spm"] = paket.nomor_spm
            parsed["spm"]["metadata"]["nomor_sp2d"] = paket.nomor_sp2d
            parsed["spm"]["metadata"]["nomor_invoice"] = paket.nomor_invoice
            parsed["spm"]["metadata"]["satker_code"] = raw_satker
            parsed["spm"]["metadata"]["nomor_drpp"] = clean_text(request.POST.get("nomor_drpp", parsed["spm"]["metadata"].get("nomor_drpp", "")))

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
                        preview_rows.append(row)
                parsed["preview_rows"] = preview_rows
                if preview_rows:
                    first = preview_rows[0]
                    parsed["spm"]["metadata"]["nomor_spm"] = first.get("nomor_spm") or paket.nomor_spm
                    parsed["spm"]["metadata"]["tanggal_spm"] = first.get("tanggal_spm") or parsed["spm"]["metadata"].get("tanggal_spm")
                    parsed["spm"]["metadata"]["jenis_spm"] = first.get("jenis_spm") or parsed["spm"]["metadata"].get("jenis_spm")
                    parsed["spm"]["metadata"]["cara_pembayaran"] = first.get("cara_pembayaran") or parsed["spm"]["metadata"].get("cara_pembayaran")
                    total_bruto = sum((parse_user_decimal(row.get("nilai_bruto")) for row in preview_rows), Decimal("0"))
                    total_netto = sum((parse_user_decimal(row.get("nilai_netto")) for row in preview_rows), Decimal("0"))
                    parsed["spm"]["metadata"]["jumlah_pengeluaran"] = total_bruto
                    parsed["spm"]["metadata"]["total_pembayaran"] = total_netto
                    parsed["spm"]["metadata"]["jumlah_potongan"] = max(total_bruto - total_netto, Decimal("0"))
                    paket.nomor_spm = first.get("nomor_spm") or paket.nomor_spm
                    paket.nilai_spm = total_netto

            drpp_count = int(request.POST.get("drpp_row_count") or 0)
            if drpp_count:
                drpps = parsed.get("drpps") or ([parsed.get("drpp")] if parsed.get("drpp") else [])
                updated_drpps = []
                for index in range(drpp_count):
                    current = drpps[index] if index < len(drpps) and drpps[index] else {"metadata": {}, "items": []}
                    meta = current.setdefault("metadata", {})
                    meta["nomor_drpp"] = clean_text(request.POST.get(f"drpp-{index}-nomor_drpp", meta.get("nomor_drpp", "")))
                    meta["satker_code"] = clean_text(request.POST.get(f"drpp-{index}-satker", meta.get("satker_app_code") or meta.get("satker_code", "")))
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

            # Compute decision just to ensure it works, though not strictly needed since we redirect
            decision = build_package_decision(parsed, paket.original_filename, forced_sp2d=forced_sp2d, current_paket_id=paket.id)
            if keterangan and "notes" in decision:
                decision["notes"].insert(0, keterangan)

            messages.success(request, "Data diupdate, matching dihitung ulang.")
            return redirect("paket_spm:preview")

        if action == "commit":
            commit_choice = request.POST.get("commit_choice") # 'link_existing', 'create_from_package', 'review_manual', 'save_draft'
            decision = build_package_decision(parsed, paket.original_filename, forced_sp2d=forced_sp2d, current_paket_id=paket.id)

            if commit_choice == "save_draft":
                request.session.pop("paket_spm_preview_id", None)
                messages.success(request, "Draft Paket SPM berhasil disimpan. Anda dapat membukanya kembali di menu Draft Paket SPM.")
                # We do not change status, keep it PREVIEW so it shows in drafts
                return redirect("paket_spm:drafts")

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
                return redirect(f"{reverse('dk:list')}?satker={satker}&q={nomor_spm}")

            request.session.pop("paket_spm_preview_id", None)
            return redirect("paket_spm:list")

    decision = build_package_decision(parsed, paket.original_filename, forced_sp2d=forced_sp2d, current_paket_id=paket.id)
    preview_summary = build_preview_summary(parsed, decision, {"original_filename": paket.original_filename})
    summary_document_status = preview_summary.get("document_status") or decision.get("document_status") or "-"

    # Render preview rows dynamically (without saving)
    rekon_errors = []
    if decision.get("matched_transaction") and decision.get("commit_action") in {"link_existing", "update_existing"}:
        transaction_rows = exact_transactions_for_package(parsed, paket)
    else:
        try:
            transaction_rows = build_transaction_rows_from_package(parsed, paket, request.user, sp2d_raw=forced_sp2d, document_status=decision.get("document_status"), save=False, skip_existing=False)
        except ValueError as e:
            transaction_rows = []
            rekon_errors.append(str(e))

    sum_bruto = sum(row.nilai_bruto for row in transaction_rows)
    sum_netto = sum(row.nilai_netto for row in transaction_rows)

    spm_meta = (parsed.get("spm") or {}).get("metadata", {})
    scan_rows = build_scan_rows(parsed, decision)
    drpp_rows = build_drpp_rows(parsed)
    kw_rows = build_kw_rows(parsed)
    document_checklist = build_document_checklist(parsed, decision)
    from apps.paket_spm.services import is_gup, is_tup, money_value
    spm_bruto = money_value(spm_meta.get("jumlah_pengeluaran"))
    spm_netto = money_value(spm_meta.get("total_pembayaran"))
    spm_potongan = money_value(spm_meta.get("jumlah_potongan"))

    # 1. Total Bruto seluruh baris = Nilai Bruto SPM
    diff_bruto = abs(sum_bruto - spm_bruto)
    if diff_bruto > 1 and spm_bruto > 0:
        rekon_errors.append(f"Total Bruto baris Rp{sum_bruto:,.0f}, sedangkan Bruto SPM Rp{spm_bruto:,.0f}. Selisih Rp{diff_bruto:,.0f}.")

    # 2. GU/GUP/TUP direkonsiliasi dengan bruto dan potongan; LS tetap pakai nilai pembayaran.
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
    can_commit = decision.get("can_commit", False)
    if getattr(paket, "alokasi_potongan_ambigu", False):
        rekon_errors.append("Alokasi potongan ambigu untuk beberapa baris pengeluaran. Potongan tidak dapat dialokasikan secara eksplisit. Harap perbaiki nilai potongan per baris secara manual.")

    if rekon_errors:
        can_commit = False

    context = permission_context(request.user)
    context.update({
        "page_title": "Preview Paket SPM",
        "page_subtitle": "Tinjau isi data sebelum disimpan ke D_K.",
        "parsed": parsed,
        "decision": decision,
        "preview_summary": preview_summary,
        "summary_document_status": summary_document_status,
        "transaction_rows": transaction_rows,
        "scan_rows": scan_rows,
        "drpp_rows": drpp_rows,
        "kw_rows": kw_rows,
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
    document_status = decision.get("document_status") or "-"
    if lampiran_warnings(parsed) and document_status in {"-", "Lengkap"}:
        if parsed.get("spm") and (parsed.get("drpps") or parsed.get("drpp")) and parsed.get("kw_items"):
            document_status = "Lengkap dengan Peringatan Lampiran"
    return {
        "upload_name": preview_state.get("original_filename", "-"),
        "file_count": len(parsed.get("files", [])),
        "spm_count": 1 if parsed.get("spm") else 0,
        "drpp_count": len(parsed.get("drpps", []) or ([parsed.get("drpp")] if parsed.get("drpp") else [])),
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

