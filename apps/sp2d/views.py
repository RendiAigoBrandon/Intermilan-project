import os
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import FileSystemStorage
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date

from apps.accounts.access import (
    can_upload_document, filter_by_satker, permission_context, can_import_data,
    can_view_all_satker, get_user_satker_code, can_edit_satker
)
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
            
        if not upload_file.name.lower().endswith('.xlsx'):
            messages.error(request, "Format file tidak valid. Harap unggah file .xlsx.")
            return redirect("sp2d:list")
            
        max_mb = getattr(settings, "SP2D_MAX_UPLOAD_MB", 10)
        if upload_file.size > max_mb * 1024 * 1024:
            messages.error(request, f"Ukuran file melebihi batas maksimal ({max_mb} MB).")
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
            'bulan': bulan,
            'uploaded_by_user_id': request.user.id
        }
        return redirect("sp2d:preview")

    # Header Rows (SP2DRaw)
    rows = filter_by_satker(
        SP2DRaw.objects.select_related("import_batch", "created_by")
        .annotate(
            dk_count=Count(
                "transaction_details", 
                filter=~Q(transaction_details__status_detail=TransactionDetail.StatusDetail.DIARSIPKAN), 
                distinct=True
            )
        ), 
        request.user
    )
    
    # Batch Rows (SP2DImportBatch)
    if can_view_all_satker(request.user):
        batch_qs = SP2DImportBatch.objects.all()
    else:
        user_satker = get_user_satker_code(request.user)
        batch_qs = SP2DImportBatch.objects.filter(raw_rows__satker_code=user_satker).distinct()

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
        rows = rows.filter(status=SP2DRaw.Status.COCOK)
    elif status == "belum":
        rows = rows.exclude(status=SP2DRaw.Status.COCOK)
    if satker:
        rows = rows.filter(satker_code=satker)
    if bulan:
        rows = rows.filter(Q(bulan_sp2d=bulan) | Q(tgl_sp2d__month=bulan) | Q(tanggal_invoice__month=bulan))

    page_size = normalize_page_size(request.GET.get("page_size"))
    paginator = Paginator(rows.order_by("-created_at", "id"), page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    
    batch_paginator = Paginator(batch_qs, 10)
    batch_page_obj = batch_paginator.get_page(request.GET.get("batch_page"))
    batch_rows = list(batch_page_obj.object_list)
    
    month_dict = dict(MONTH_OPTIONS)
    for batch in batch_rows:
        batch.bulan_name = month_dict.get(batch.bulan, str(batch.bulan)) if batch.bulan else "-"
    
    header_rows = list(page_obj.object_list)
    for row in header_rows:
        row.status_detail_label = "Sudah Ada D_K" if row.status == SP2DRaw.Status.COCOK else "Belum Lengkap"
        row.can_edit_sp2d = can_edit_satker(request.user, row.satker_code)
        row.bulan_name = month_dict.get(row.bulan_sp2d, str(row.bulan_sp2d)) if row.bulan_sp2d else "-"
        
    base_query = request.GET.copy()
    base_query.pop("page", None)
    
    batch_query = request.GET.copy()
    batch_query.pop("batch_page", None)

    context = permission_context(request.user)
    context.update(
        {
            "page_title": "Upload SP2D",
            "page_subtitle": "Data mentah SP2D dan riwayat import.",
            "header_rows": header_rows,
            "batch_rows": batch_rows,
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
            "batch_base_querystring": batch_query.urlencode(),
            "pagination_window": build_pagination_window(page_obj),
            "batch_paginator": batch_paginator,
            "batch_page_obj": batch_page_obj,
        }
    )
    return render(request, "sp2d/list.html", context)


@login_required
def sp2d_inbox_detail(request, pk):
    queryset = filter_by_satker(SP2DRaw.objects.select_related("import_batch", "created_by"), request.user)
    row = get_object_or_404(queryset, pk=pk)
    detail_query = TransactionDetail.objects.filter(sp2d_raw=row).order_by("nomor_spm", "akun", "id")

    if request.method == "POST":
        messages.error(request, "Pembuatan Rincian D_K manual dari halaman ini telah dinonaktifkan. Silakan gunakan menu Tambah Rincian Manual pada Daftar D_K.")
        return redirect("sp2d:inbox_detail", pk=row.pk)

    has_dk_detail = detail_query.exists()
    context = permission_context(request.user)
    context.update(
        {
            "page_title": "Detail Inbox SP2D",
            "page_subtitle": "Detail read-only data awal SP2D yang telah diimpor.",
            "row": row,
            "detail_rows": detail_query[:50],
            "has_dk_detail": has_dk_detail,
            "status_detail_label": "Sudah Ada D_K" if has_dk_detail else "Belum Ada Detail D_K",
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
        
    if import_data.get('uploaded_by_user_id') != request.user.id or not can_import_data(request.user):
        messages.error(request, "Anda tidak memiliki izin untuk memproses sesi import ini.")
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
                    parser_failed_rows = max(parse_result["raw_rows"] - len(mapped_rows), 0)
                    batch = SP2DImportBatch.objects.create(
                        filename=os.path.basename(file_path),
                        original_filename=import_data['original_filename'],
                        tahun=tahun,
                        bulan=bulan,
                        total_rows=parse_result["raw_rows"],
                        failed_rows=parser_failed_rows,
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
                    
                    commit_sp2d_rows(batch, mapped_rows, request.user, filename=import_data['original_filename'])
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
            "BARU": sum(1 for r in all_preview_rows if r.get("preview_status") == "BARU"),
            "AKAN_DIPERBARUI": sum(1 for r in all_preview_rows if r.get("preview_status") == "AKAN_DIPERBARUI"),
            "IDENTIK_DILEWATI": sum(1 for r in all_preview_rows if r.get("preview_status") == "IDENTIK_DILEWATI"),
            "KONFLIK": sum(1 for r in all_preview_rows if r.get("preview_status") == "KONFLIK"),
            "GAGAL": sum(1 for r in all_preview_rows if r.get("preview_status") == "GAGAL"),
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
