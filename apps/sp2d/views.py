import os
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import FileSystemStorage
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date

from apps.accounts.access import can_upload_document, filter_by_satker, permission_context, can_import_data
from apps.core.parsers import parse_month, parse_sp2d_excel_file
from apps.core.satker import infer_satker_from_name
from apps.core.views import CHECKLIST_ROWS, MONTH_OPTIONS, build_pagination_window, normalize_page_size
from apps.documents.models import ChecklistStatus, ChecklistTemplate, DocumentDriveLink
from apps.documents.services.google_drive import archive_file_link
from apps.drpp.models import DRPPItem, DRPPUpload
from apps.dk.models import MasterAkun, TransactionDetail
from apps.paket_spm.models import PaketSPMPreviewItem, PaketSPMUpload

from .models import SP2DImportBatch, SP2DRaw
from .services import classify_sp2d_rows, commit_sp2d_rows


@login_required
def sp2d_list(request):
    if request.method == "POST":
        if not can_import_data(request.user):
            messages.error(request, "Anda tidak memiliki izin untuk mengimport data SP2D.")
            return redirect("sp2d:list")
            
        tahun = request.POST.get("tahun")
        bulan = request.POST.get("bulan")
        upload_file = request.FILES.get("file_sp2d")
        
        if not upload_file:
            messages.error(request, "Harap pilih file Excel.")
            return redirect("sp2d:list")
            
        if not upload_file.name.endswith(('.xlsx', '.xls')):
            messages.error(request, "Format file tidak valid. Harap unggah file .xlsx atau .xls.")
            return redirect("sp2d:list")
            
        tmp_dir = os.path.join(settings.MEDIA_ROOT, "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        fs = FileSystemStorage(location=tmp_dir)
        filename = fs.save(upload_file.name, upload_file)
        file_path = fs.path(filename)
        
        request.session['sp2d_import'] = {
            'file_path': file_path,
            'original_filename': upload_file.name,
            'tahun': tahun,
            'bulan': bulan
        }
        return redirect("sp2d:preview")

    rows = filter_by_satker(SP2DRaw.objects.select_related("import_batch", "created_by"), request.user)
    match_query = (
        TransactionDetail.objects.filter(satker_code=OuterRef("satker_code"))
        .exclude(nomor_spm="", no_kuitansi="")
        .filter(
            Q(sp2d_raw=OuterRef("pk"))
            | Q(nomor_spm=OuterRef("nomor_spm_extracted"))
            | Q(nomor_spm=OuterRef("nomor_invoice"))
            | Q(no_kuitansi=OuterRef("nomor_invoice"))
        )
    )
    rows = rows.annotate(has_dk_detail=Exists(match_query))

    search = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    satker = request.GET.get("satker", "").strip()
    bulan = request.GET.get("bulan", "").strip()
    if search:
        rows = rows.filter(
            Q(no_sp2d__icontains=search)
            | Q(nomor_invoice__icontains=search)
            | Q(nomor_spm_extracted__icontains=search)
            | Q(deskripsi__icontains=search)
            | Q(satker_code__icontains=search)
            | Q(satker_name__icontains=search)
        )
    if status == "sudah":
        rows = rows.filter(has_dk_detail=True)
    elif status == "belum":
        rows = rows.filter(has_dk_detail=False)
    if satker:
        rows = rows.filter(satker_code=satker)
    if bulan:
        rows = rows.filter(Q(bulan_sp2d=bulan) | Q(tgl_sp2d__month=bulan) | Q(tanggal_invoice__month=bulan))

    page_size = normalize_page_size(request.GET.get("page_size"))
    paginator = Paginator(rows.order_by("-created_at", "id"), page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    rows = list(page_obj.object_list)
    for row in rows:
        row.status_detail_label = "Sudah Ada D_K" if row.has_dk_detail else "Belum Ada Detail"
    base_query = request.GET.copy()
    base_query.pop("page", None)

    context = permission_context(request.user)
    context.update(
        {
            "page_title": "Upload & Inbox SP2D",
            "page_subtitle": "Data mentah SP2D dan status pencocokan ke D_K.",
            "rows": rows,
            "filters": {"q": search, "status": status, "satker": satker, "bulan": bulan},
            "months": MONTH_OPTIONS,
            "satker_options": get_satker_options(request.user),
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
    return render(request, "sp2d/list.html", context)


@login_required
def sp2d_inbox_detail(request, pk):
    queryset = filter_by_satker(SP2DRaw.objects.select_related("import_batch", "created_by"), request.user)
    row = get_object_or_404(queryset, pk=pk)
    context_can_upload = can_upload_document(request.user)
    detail_query = TransactionDetail.objects.filter(
        Q(sp2d_raw=row)
        | (
            Q(satker_code=row.satker_code)
            & (
                Q(nomor_spm=row.nomor_spm_extracted)
                | Q(nomor_spm=row.nomor_invoice)
                | Q(no_kuitansi=row.nomor_invoice)
            )
        )
    ).order_by("nomor_spm", "akun", "id")

    if request.method == "POST":
        messages.error(request, "Pembuatan Rincian D_K manual dari halaman ini telah dinonaktifkan. Silakan gunakan menu Tambah Rincian Manual pada Daftar D_K.")
        return redirect("sp2d:inbox_detail", pk=row.pk)

    has_dk_detail = detail_query.exists()
    default_date = row.tanggal_invoice or row.tgl_sp2d or row.tanggal_selesai_sp2d
    default_month = row.bulan_sp2d or parse_month(default_date.strftime("%B")) if default_date else row.bulan_sp2d
    context = permission_context(request.user)
    context.update(
        {
            "page_title": "Detail Inbox SP2D",
            "page_subtitle": "Data awal SP2D yang perlu dilengkapi dengan detail D_K dan dokumen Paket SPM.",
            "row": row,
            "detail_rows": detail_query[:50],
            "has_dk_detail": has_dk_detail,
            "status_detail_label": "Sudah Ada D_K" if has_dk_detail else "Belum Ada Detail D_K",
            "can_upload_document": context_can_upload,
            "akun_options": MasterAkun.objects.filter(is_active=True).order_by("kode")[:300],
            "months": MONTH_OPTIONS,
            "manual_defaults": {
                "nomor_spm": row.nomor_spm_extracted or (row.nomor_invoice.split("/")[0] if row.nomor_invoice else ""),
                "tanggal_spm": default_date,
                "bulan_sp2d": default_month,
                "cara_pembayaran": row.jenis_spm,
                "jenis_spm": row.jenis_spm,
                "deskripsi": row.deskripsi,
                "nilai_bruto": row.nilai_spm,
                "nilai_netto": row.nilai_sp2d,
            },
        }
    )
    return render(request, "sp2d/inbox_detail.html", context)


def parse_money_input(value, fallback=Decimal("0")):
    text = str(value or "").strip()
    if not text:
        return fallback or Decimal("0")
    text = text.replace("Rp", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except Exception:
        return fallback or Decimal("0")


def generate_checklist_for_detail(detail, user, has_sp2d=False):
    templates = list(ChecklistTemplate.objects.filter(is_active=True).order_by("urutan", "nama_dokumen")[:100])
    if templates:
        rows = [(template.nama_dokumen, template.wajib) for template in templates]
    else:
        rows = [(name, index != len(CHECKLIST_ROWS)) for index, name in enumerate(CHECKLIST_ROWS, start=1)]
    for nama_dokumen, wajib in rows:
        default_status = (
            ChecklistStatus.Status.ADA
            if has_sp2d and nama_dokumen.strip().upper() == "SP2D"
            else ChecklistStatus.Status.BELUM
        )
        ChecklistStatus.objects.get_or_create(
            transaction_detail=detail,
            nama_dokumen=nama_dokumen,
            defaults={"wajib": wajib, "status": default_status, "updated_by": user},
        )


@login_required
def sp2d_preview(request):
    import_data = request.session.get('sp2d_import')
    if not import_data:
        messages.error(request, "Sesi upload tidak ditemukan. Silakan upload ulang.")
        return redirect("sp2d:list")
        
    file_path = import_data['file_path']
    if not os.path.exists(file_path):
        messages.error(request, "File sementara hilang. Silakan upload ulang.")
        return redirect("sp2d:list")
        
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "cancel":
            os.remove(file_path)
            del request.session['sp2d_import']
            messages.info(request, "Upload dibatalkan.")
            return redirect("sp2d:list")
        elif action == "commit":
            try:
                parse_result = parse_sp2d_excel_file(file_path)
                if not parse_result["ok"]:
                    messages.error(request, parse_result["error"] or "Preview tidak valid.")
                    return redirect("sp2d:preview")
                mapped_rows = parse_result["rows"]
                
                tahun = int(import_data['tahun']) if str(import_data['tahun']).isdigit() else None
                bulan = parse_month(import_data["bulan"])

                with transaction.atomic():
                    batch = SP2DImportBatch.objects.create(
                        filename=os.path.basename(file_path),
                        original_filename=import_data['original_filename'],
                        tahun=tahun,
                        bulan=bulan,
                        total_rows=parse_result["raw_rows"],
                        status=SP2DImportBatch.Status.PROCESSING,
                        uploaded_by=request.user,
                        notes=f"Sheet {parse_result['sheet']}, header row {parse_result['header_row']}",
                    )
                    # Normalize inputs before commit
                    for row_data in mapped_rows:
                        inferred_code, inferred_name = infer_satker_from_name(row_data.get("satker_name", ""))
                        row_data["satker_code"] = str(row_data.get("satker_code") or inferred_code or "")[:32]
                        row_data["satker_name"] = str(inferred_name or row_data.get("satker_name", ""))[:255]
                        row_data["no_sp2d"] = str(row_data.get('no_sp2d', ''))[:100]
                        row_data["mata_uang"] = str(row_data.get('mata_uang', ''))[:20]
                        row_data["nomor_invoice"] = str(row_data.get('nomor_invoice', ''))[:100]
                        row_data["jenis_spm"] = str(row_data.get('jenis_spm', ''))[:100]
                        row_data["jenis_sp2d"] = str(row_data.get('jenis_sp2d', ''))[:100]
                        row_data["deskripsi"] = str(row_data.get('deskripsi', ''))
                        row_data["nomor_spm_extracted"] = str(row_data.get('nomor_spm_extracted', ''))[:100]
                    
                    commit_sp2d_rows(batch, mapped_rows, request.user)
                drive_result, _ = archive_file_link(
                    file_path,
                    user=request.user,
                    jenis_dokumen="SP2D_EXCEL",
                    nama_file=import_data["original_filename"],
                    satker_code=str(mapped_rows[0].get("satker_code", "")) if mapped_rows else "",
                    catatan_extra=f"source=SP2D; rows={len(mapped_rows)}; sheet={parse_result['sheet']}",
                )
                
                try:
                    os.remove(file_path)
                except OSError as exc:
                    messages.warning(request, f"Import berhasil, tetapi file sementara belum bisa dihapus: {exc}")
                del request.session['sp2d_import']
                
                if drive_result["status"] == "uploaded":
                    messages.success(request, f"Berhasil memproses import data SP2D secara idempoten dan mengarsipkan file ke Google Drive.")
                else:
                    messages.warning(request, f"Berhasil memproses import data SP2D secara idempoten. {drive_result['error_message']}")
                return redirect("sp2d:list")
                
            except Exception as e:
                messages.error(request, f"Gagal memproses file: {str(e)}")
                return redirect("sp2d:preview")

    try:
        parse_result = parse_sp2d_excel_file(file_path)
        tahun = import_data.get('tahun')
        all_preview_rows = classify_sp2d_rows(tahun, parse_result["rows"])
        
        preview_stats = {
            "BARU": sum(1 for r in all_preview_rows if r.get("_status") == "BARU"),
            "AKAN_DIPERBARUI": sum(1 for r in all_preview_rows if r.get("_status") == "AKAN_DIPERBARUI"),
            "IDENTIK_DILEWATI": sum(1 for r in all_preview_rows if r.get("_status") == "IDENTIK_DILEWATI"),
            "KONFLIK": sum(1 for r in all_preview_rows if r.get("_status") == "KONFLIK"),
        }
        
        if parse_result["valid_rows"] <= 100:
            preview_rows = all_preview_rows
            preview_page_obj = None
            preview_start_index = 1 if preview_rows else 0
            preview_end_index = len(preview_rows)
        else:
            preview_paginator = Paginator(all_preview_rows, 25)
            preview_page_obj = preview_paginator.get_page(request.GET.get("page"))
            preview_rows = list(preview_page_obj.object_list)
            preview_start_index = preview_page_obj.start_index()
            preview_end_index = preview_page_obj.end_index()
    except Exception as e:
        messages.error(request, f"Gagal membaca file Excel: {str(e)}")
        return redirect("sp2d:list")

    context = permission_context(request.user)
    context.update({
        "page_title": "Preview Import SP2D",
        "page_subtitle": f"File: {import_data['original_filename']} ({parse_result['valid_rows']} baris valid)",
        "columns": parse_result["columns"],
        "preview_rows": preview_rows,
        "preview_page_obj": preview_page_obj,
        "preview_start_index": preview_start_index,
        "preview_end_index": preview_end_index,
        "total_rows": parse_result["valid_rows"],
        "raw_rows": parse_result["raw_rows"],
        "parse_result": parse_result,
        "preview_stats": preview_stats,
        "import_data": import_data,
        "can_commit": parse_result["ok"],
    })
    return render(request, "sp2d/preview.html", context)


def get_satker_options(user):
    queryset = filter_by_satker(SP2DRaw.objects.exclude(Q(satker_code="") & Q(satker_name="")), user)
    return queryset.values("satker_code", "satker_name").order_by("satker_code", "satker_name").distinct()[:200]


@login_required
def sp2d_completeness(request):
    rows_qs = filter_by_satker(SP2DRaw.objects.select_related("import_batch"), request.user).order_by("-created_at", "id")
    q = request.GET.get("q", "").strip()
    if q:
        rows_qs = rows_qs.filter(
            Q(no_sp2d__icontains=q)
            | Q(nomor_invoice__icontains=q)
            | Q(nomor_spm_extracted__icontains=q)
            | Q(satker_code__icontains=q)
            | Q(satker_name__icontains=q)
            | Q(deskripsi__icontains=q)
        )
    rows = list(rows_qs[:200])
    result_rows = [build_completeness_row(row, index) for index, row in enumerate(rows, start=1)]
    summary = {
        "total": len(result_rows),
        "lengkap": sum(1 for row in result_rows if row["status"] == "Lengkap"),
        "belum": sum(1 for row in result_rows if row["status"] == "Belum Lengkap"),
        "review": sum(1 for row in result_rows if row["status"] == "Perlu Review"),
    }
    context = permission_context(request.user)
    context.update({
        "page_title": "Cek Kelengkapan SP2D",
        "page_subtitle": "Pemeriksaan awal dokumen pendukung berdasarkan nomor invoice, SPM, satker, dan metadata arsip.",
        "rows": result_rows,
        "summary": summary,
        "filters": {"q": q},
    })
    return render(request, "sp2d/completeness.html", context)


def build_completeness_row(row, index):
    spm_number = row.nomor_spm_extracted or (row.nomor_invoice.split("/")[0] if row.nomor_invoice else "")
    base_filter = Q()
    if spm_number:
        base_filter |= Q(nomor_spm__icontains=spm_number)
    if row.nomor_invoice:
        base_filter |= Q(no_kuitansi__icontains=row.nomor_invoice) | Q(nama_file__icontains=row.nomor_invoice) | Q(catatan__icontains=row.nomor_invoice)
    if row.no_sp2d:
        base_filter |= Q(nama_file__icontains=row.no_sp2d) | Q(catatan__icontains=row.no_sp2d)
    if row.satker_code:
        base_filter &= Q(satker_code__in=["", row.satker_code]) | Q(satker_code=row.satker_code)

    drive_links = DocumentDriveLink.objects.filter(base_filter) if base_filter else DocumentDriveLink.objects.none()
    spm_exists = (
        bool(spm_number)
        and (
            PaketSPMUpload.objects.filter(nomor_spm__icontains=spm_number).exists()
            or PaketSPMPreviewItem.objects.filter(nomor_spm__icontains=spm_number).exists()
            or DocumentDriveLink.objects.filter(Q(nomor_spm__icontains=spm_number) | Q(nama_file__icontains=spm_number), jenis_dokumen__icontains="SPM").exists()
        )
    )
    drpp_exists = (
        bool(spm_number)
        and (
            DRPPUpload.objects.filter(nomor_spm__icontains=spm_number).exists()
            or DocumentDriveLink.objects.filter(Q(nomor_spm__icontains=spm_number) | Q(nama_file__icontains=spm_number), jenis_dokumen__icontains="DRPP").exists()
        )
    )
    kw_exists = (
        bool(spm_number)
        and (
            PaketSPMPreviewItem.objects.filter(Q(nomor_spm__icontains=spm_number) & ~Q(no_kuitansi="")).exists()
            or DRPPItem.objects.filter(no_bukti__icontains=spm_number).exists()
            or DocumentDriveLink.objects.filter(Q(no_kuitansi__icontains=spm_number) | Q(nama_file__icontains=spm_number), jenis_dokumen__icontains="KW").exists()
        )
    )
    link_exists = drive_links.exists()

    missing = []
    if not spm_exists:
        missing.append("SPM/Paket SPM belum ditemukan")
    if not drpp_exists:
        missing.append("DRPP belum ditemukan")
    if not kw_exists:
        missing.append("KW/Kuitansi belum ditemukan")
    if not link_exists:
        missing.append("Link Drive belum ditemukan")

    if spm_exists and drpp_exists and kw_exists and link_exists:
        status = "Lengkap"
    elif spm_exists or drpp_exists or kw_exists or link_exists:
        status = "Perlu Review"
    else:
        status = "Belum Lengkap"

    return {
        "no": index,
        "satker": f"{row.satker_code} - {row.satker_name}" if row.satker_code and row.satker_name else row.satker_code or row.satker_name or "-",
        "no_sp2d": row.no_sp2d,
        "nomor_invoice": row.nomor_invoice,
        "jenis_spm": row.jenis_spm,
        "nilai_sp2d": row.nilai_sp2d,
        "spm": spm_exists,
        "drpp": drpp_exists,
        "kw": kw_exists,
        "drive": link_exists,
        "status": status,
        "catatan": "; ".join(missing) if missing else "Dokumen pendukung terdeteksi.",
    }
