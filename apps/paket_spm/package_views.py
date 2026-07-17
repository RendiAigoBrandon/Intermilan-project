"""Upload view Paket SPM yang memakai document graph parser.

View preview, draft, dan commit tetap menggunakan implementasi lama. Hanya jalur
upload yang diganti agar satu PDF gabungan tidak lagi diputuskan sebagai satu
jenis dokumen berdasarkan klasifikasi seluruh file.
"""

import json
import os
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files import File
from django.core.files.storage import FileSystemStorage
from django.shortcuts import redirect, render

from apps.accounts.access import filter_by_satker, permission_context
from apps.core.ocr import check_ocr_environment
from apps.core.package_graph import parse_uploaded_package
from apps.core.parsers import make_json_safe, parse_decimal, parse_month
from apps.paket_spm.services import parsed_from_identity_probe, probe_package_identity

from . import views as legacy_views
from .models import PaketSPMUpload


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

        validation_error = legacy_views.validate_paket_upload(upload_file, upload_files)
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
            filename = legacy_views.save_many_files_as_zip(fs, upload_files)
            original_filename = filename
            kind = "zip"
        else:
            lower_name = upload_file.name.lower()
            filename = fs.save(upload_file.name, upload_file)
            original_filename = upload_file.name
            kind = "zip" if lower_name.endswith(".zip") else "pdf"

        file_path = fs.path(filename)
        sp2d_context = legacy_views.get_sp2d_context(request.POST.get("sp2d_raw_id"), request.user)
        sp2d_row = sp2d_context.get("row") if sp2d_context else None
        input_tahun = str(request.POST.get("tahun") or getattr(sp2d_row, "tahun", "") or "")
        input_satker = str(
            request.POST.get("satker_code") or getattr(sp2d_row, "satker_code", "") or ""
        ).split(" - ")[0].strip()

        identity_probe = probe_package_identity(
            file_path,
            original_filename,
            input_satker=input_satker,
            input_tahun=input_tahun,
            kind=kind,
        )

        try:
            # Hanya identitas yang pasti dan sudah memiliki transaksi D_K yang
            # boleh melewati OCR. Kondisi needs_review tetap menjalankan parser
            # agar PDF baru tidak berhenti pada fallback filename atau nilai nol.
            if identity_probe.get("exact_transaction_ids") and not identity_probe.get("needs_review"):
                parsed = parsed_from_identity_probe(identity_probe, original_filename)
                parsed["architecture"] = "identity-probe-existing-dk"
            else:
                parsed = parse_uploaded_package(
                    file_path,
                    original_filename,
                    kind=kind,
                )
                parsed["identity_probe"] = identity_probe
                if identity_probe.get("warnings"):
                    parsed.setdefault("warnings", []).extend(identity_probe["warnings"])
        except Exception as exc:
            parsed = {
                "ok": False,
                "architecture": "document-graph-v1",
                "files": [],
                "spm": None,
                "drpp": None,
                "drpps": [],
                "kw_by_drpp": {},
                "kw_items": [],
                "warnings": [f"Document graph parser gagal: {exc}"],
                "temp_dir": "",
                "validation": {
                    "status": "GAGAL",
                    "issues": [str(exc)],
                    "row_count": 0,
                },
            }

        spm_meta = (parsed.get("spm") or {}).get("metadata", {})
        drpp_list = parsed.get("drpps") or ([parsed.get("drpp")] if parsed.get("drpp") else [])
        drpp_meta = ((parsed.get("drpp") or (drpp_list[0] if drpp_list else {})) or {}).get("metadata", {})
        tanggal_spm = spm_meta.get("tanggal_spm")
        tanggal_sp2d = spm_meta.get("tanggal_sp2d")
        tahun = (
            (int(request.POST.get("tahun")) if str(request.POST.get("tahun", "")).isdigit() else None)
            or getattr(tanggal_spm, "year", None)
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

        safe_parsed = make_json_safe(parsed)
        try:
            json.dumps(safe_parsed, ensure_ascii=False)
        except TypeError as exc:
            messages.error(request, f"System Error: Gagal mengonversi data OCR ke JSON. {exc}")
            return redirect("paket_spm:list")

        kw_items = parsed.get("kw_items", [])
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
            total_rincian_bruto=sum(
                (parse_decimal(item.get("bruto") or item.get("jumlah") or 0) for item in kw_items),
                Decimal("0"),
            ),
            total_rincian_netto=sum(
                (parse_decimal(item.get("netto") or item.get("jumlah") or 0) for item in kw_items),
                Decimal("0"),
            ),
            status=PaketSPMUpload.Status.PREVIEW,
            uploaded_by=request.user,
            parsed_data=safe_parsed,
        )
        with open(file_path, "rb") as source_file:
            paket.zip_file.save(original_filename, File(source_file), save=False)
        paket.save()

        request.session["paket_spm_preview_id"] = paket.id
        request.session["sp2d_raw_id"] = request.POST.get("sp2d_raw_id", "")
        print(
            f"[INTERMILAN PaketSPM Upload] document_graph saved PREVIEW id={paket.id} "
            f"validation={(parsed.get('validation') or {}).get('status', '-')}",
            flush=True,
        )
        return redirect("paket_spm:preview")

    rows = filter_by_satker(PaketSPMUpload.objects.select_related("uploaded_by"), request.user)
    sp2d_context = legacy_views.get_sp2d_context(request.GET.get("sp2d_raw_id"), request.user)
    context = permission_context(request.user)
    context.update(
        {
            "page_title": "Upload Paket SPM",
            "page_subtitle": "Siapkan paket dokumen SPM, DRPP, dan kuitansi untuk preview D_K sebelum disimpan.",
            "rows": rows[:50],
            "max_zip_size_mb": settings.MAX_ZIP_SIZE_MB,
            "max_upload_size_mb": settings.MAX_UPLOAD_SIZE_MB,
            "sp2d_context": sp2d_context,
            "ocr_environment": check_ocr_environment(),
        }
    )
    return render(request, "paket_spm/list.html", context)
