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
from apps.core.parsers import parse_decimal, parse_month, parse_sp2d_excel_file
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

    context = permission_context(request.user)
    context.update(
        {
            "page_title": "Upload SP2D",
            "page_subtitle": "Upload file Excel Daftar SP2D",
            "months": MONTH_OPTIONS,
            "satker_options": get_satker_options(request.user) if 'get_satker_options' in globals() else [],
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
    result = parse_decimal(value)
    if result == Decimal("0"):
        # Preserve fallback only for genuinely zero/null input, not for parse failures.
        # parse_decimal already returns Decimal("0") on failure.
        return fallback or Decimal("0")
    return result


from apps.core.document_policy import (
    get_required_documents_for_akun_family,
    normalize_akun_family,
    AkunFamily,
)


def generate_checklist_for_detail(detail, user, has_sp2d=False):
    """
    Buat baris ChecklistStatus untuk transaksi ini berdasarkan keluarga Akun.

    Prioritas:
    1. Gunakan account-family-specific docs (dari AKUN_FAMILY_REQUIRED_DOCS)
    2. Jika tidak ada rules untuk keluarga ini, fallback ke ChecklistTemplate
    3. Jika tidak ada template aktif, fallback ke CHECKLIST_ROWS
    """
    # Ambil dokumen berdasarkan account family
    akun_family = normalize_akun_family(detail.akun, detail.jenis_spm)
    required_docs = get_required_documents_for_akun_family(akun_family)

    if required_docs:
        # Account-family-specific documents take priority
        rows = [(name, True) for name in required_docs]
    else:
        # Fallback: use ChecklistTemplate or CHECKLIST_ROWS
        templates = list(ChecklistTemplate.objects.filter(is_active=True).order_by("urutan", "nama_dokumen")[:100])
        if templates:
            rows = [(template.nama_dokumen, template.wajib) for template in templates]
        else:
            rows = [(name, True) for name in CHECKLIST_ROWS]

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
            # 1. Read POST array data
            satker_codes = request.POST.getlist("satker_code[]")
            satker_names = request.POST.getlist("satker_name[]")
            no_sp2ds = request.POST.getlist("no_sp2d[]")
            tahuns = request.POST.getlist("tahun[]")
            bulan_sp2ds = request.POST.getlist("bulan_sp2d[]")
            nomor_spms = request.POST.getlist("nomor_spm[]")
            tgl_spms = request.POST.getlist("tgl_spm[]")
            jenis_spms = request.POST.getlist("jenis_spm[]")
            cara_pembayarans = request.POST.getlist("cara_pembayaran[]")
            akuns = request.POST.getlist("akun[]")
            deskripsis = request.POST.getlist("deskripsi[]")
            nilai_brutos = request.POST.getlist("nilai_bruto[]")
            nilai_nettos = request.POST.getlist("nilai_netto[]")
            potongans = request.POST.getlist("potongan[]")
            no_kuitansis = request.POST.getlist("no_kuitansi[]")
            no_drpps = request.POST.getlist("no_drpp[]")
            pembebanans = request.POST.getlist("pembebanan[]")
            fps = request.POST.getlist("fp[]")
            pph21s = request.POST.getlist("pph21[]")

            # Validate counts match
            num_rows = len(satker_codes)
            if not num_rows:
                messages.error(request, "Tidak ada data untuk disimpan.")
                return redirect("sp2d:preview")

            mapped_rows = []
            errors = []
            for i in range(num_rows):
                row = {
                    "satker_code": satker_codes[i] if i < len(satker_codes) else "",
                    "satker_name": satker_names[i] if i < len(satker_names) else "",
                    "no_sp2d": no_sp2ds[i] if i < len(no_sp2ds) else "",
                    "tahun": tahuns[i] if i < len(tahuns) else "",
                    "bulan_sp2d": bulan_sp2ds[i] if i < len(bulan_sp2ds) else "",
                    "nomor_spm": nomor_spms[i] if i < len(nomor_spms) else "",
                    "tgl_spm": tgl_spms[i] if i < len(tgl_spms) else "",
                    "jenis_spm": jenis_spms[i] if i < len(jenis_spms) else "",
                    "cara_pembayaran": cara_pembayarans[i] if i < len(cara_pembayarans) else "",
                    "akun": akuns[i] if i < len(akuns) else "",
                    "deskripsi": deskripsis[i] if i < len(deskripsis) else "",
                    "nilai_bruto": parse_money_input(nilai_brutos[i] if i < len(nilai_brutos) else "0"),
                    "nilai_netto": parse_money_input(nilai_nettos[i] if i < len(nilai_nettos) else "0"),
                    "potongan": parse_money_input(potongans[i] if i < len(potongans) else "0"),
                    "no_kuitansi": no_kuitansis[i] if i < len(no_kuitansis) else "",
                    "no_drpp": no_drpps[i] if i < len(no_drpps) else "",
                    "pembebanan": pembebanans[i] if i < len(pembebanans) else "",
                    "fp": fps[i] if i < len(fps) else "",
                    "pph21": parse_money_input(pph21s[i] if i < len(pph21s) else "0"),
                    
                    # For legacy compatibility with commit_sp2d_rows
                    "nilai_spm": parse_money_input(nilai_brutos[i] if i < len(nilai_brutos) else "0"),
                    "nilai_sp2d": parse_money_input(nilai_nettos[i] if i < len(nilai_nettos) else "0"),
                    "nomor_spm_extracted": nomor_spms[i] if i < len(nomor_spms) else "",
                    "nomor_invoice": nomor_spms[i] if i < len(nomor_spms) else "",
                    "tgl_sp2d": tgl_spms[i] if i < len(tgl_spms) else "",
                    "tanggal_invoice": tgl_spms[i] if i < len(tgl_spms) else "",
                    "mata_uang": cara_pembayarans[i] if i < len(cara_pembayarans) else "",
                    "jenis_sp2d": jenis_spms[i] if i < len(jenis_spms) else "",
                }
                
                # Check permission
                if not can_edit_satker(request.user, row["satker_code"]):
                    errors.append(f"Baris {i+1}: Anda tidak memiliki akses ke satker {row['satker_code']}")
                    
                mapped_rows.append(row)

            if errors:
                for error in errors:
                    messages.error(request, error)
                # Re-render form with posted data to preserve edits
                context = permission_context(request.user)
                context.update({
                    "page_title": "Preview Import SP2D",
                    "preview_rows": mapped_rows,
                    "import_data": import_data,
                    "can_commit": True,
                })
                return render(request, "sp2d/preview.html", context)

            try:
                tahun = int(import_data['tahun']) if str(import_data['tahun']).isdigit() else None
                bulan = parse_month(import_data["bulan"])

                with transaction.atomic():
                    # Create batch
                    batch = SP2DImportBatch.objects.create(
                        filename=os.path.basename(file_path),
                        original_filename=import_data['original_filename'],
                        tahun=tahun,
                        bulan=bulan,
                        total_rows=num_rows,
                        failed_rows=0,
                        status=SP2DImportBatch.Status.PROCESSING,
                        uploaded_by=request.user,
                        notes="Direct D_K save",
                    )
                    
                    # Call legacy commit to handle SP2DRaw deduplication
                    commit_sp2d_rows(batch, mapped_rows, request.user, filename=import_data['original_filename'])
                    
                    # Fetch created SP2DRaw items for this batch to link them
                    raw_records = list(SP2DRaw.objects.filter(last_import_batch=batch))
                    
                    # Save D_K (TransactionDetail)
                    for i, row in enumerate(mapped_rows):
                        tgl_spm = parse_date(row["tgl_spm"]) if row["tgl_spm"] else None
                        bln_sp2d = int(row["bulan_sp2d"]) if str(row["bulan_sp2d"]).isdigit() else bulan
                        
                        # Find corresponding raw record by fallback matching
                        sp2d_raw = None
                        if raw_records:
                            # Try exact match by no_sp2d
                            sp2d_raw = next((r for r in raw_records if r.no_sp2d == row["no_sp2d"]), None)
                            if not sp2d_raw:
                                sp2d_raw = next((r for r in raw_records if r.nomor_spm_extracted == row["nomor_spm"]), None)
                                
                        if sp2d_raw:
                            detail, created = TransactionDetail.objects.update_or_create(
                                sp2d_raw=sp2d_raw,
                                defaults={
                                    "satker_code": row["satker_code"],
                                    "akun": row["akun"],
                                    "bulan_sp2d": bln_sp2d,
                                    "cara_pembayaran": row["cara_pembayaran"],
                                    "nomor_spm": row["nomor_spm"],
                                    "tanggal_spm": tgl_spm,
                                    "jenis_spm": row["jenis_spm"],
                                    "no_kuitansi": row["no_kuitansi"],
                                    "no_drpp": row["no_drpp"],
                                    "deskripsi": row["deskripsi"],
                                    "nilai_bruto": row["nilai_bruto"],
                                    "nilai_netto": row["nilai_netto"],
                                    "pembebanan": row["pembebanan"],
                                    "fp": row["fp"],
                                    "pph21": row["pph21"],
                                    "created_by": request.user,
                                    "status_detail": TransactionDetail.StatusDetail.DRAFT
                                }
                            )
                        else:
                            detail = TransactionDetail.objects.create(
                                sp2d_raw=sp2d_raw,
                            satker_code=row["satker_code"],
                            akun=row["akun"],
                            bulan_sp2d=bln_sp2d,
                            cara_pembayaran=row["cara_pembayaran"],
                            nomor_spm=row["nomor_spm"],
                            tanggal_spm=tgl_spm,
                            jenis_spm=row["jenis_spm"],
                            no_kuitansi=row["no_kuitansi"],
                            no_drpp=row["no_drpp"],
                            deskripsi=row["deskripsi"],
                            nilai_bruto=row["nilai_bruto"],
                            nilai_netto=row["nilai_netto"],
                            pembebanan=row["pembebanan"],
                            fp=row["fp"],
                            pph21=row["pph21"],
                            created_by=request.user,
                            status_detail=TransactionDetail.StatusDetail.DRAFT
                        )
                        generate_checklist_for_detail(detail, request.user, has_sp2d=True)
                
                try:
                    os.remove(file_path)
                except OSError:
                    pass
                del request.session['sp2d_import']
                
                messages.success(request, "Data SP2D berhasil disimpan ke D_K.")
                return redirect("dk:transaction_list")
                
            except Exception as e:
                messages.error(request, f"Gagal menyimpan data: {str(e)}")
                # Render back to preview on failure
                context = permission_context(request.user)
                context.update({
                    "page_title": "Preview Import SP2D",
                    "preview_rows": mapped_rows,
                    "import_data": import_data,
                    "can_commit": True,
                })
                return render(request, "sp2d/preview.html", context)

    # Initial GET processing
    try:
        parse_result = parse_sp2d_excel_file(file_path)
        tahun = import_data.get('tahun')
        bulan = import_data.get('bulan')
        
        # We don't need complex stats anymore, just map to the new format
        mapped_rows = []
        for row in parse_result["rows"]:
            mapped_rows.append({
                "satker_code": row.get("satker_code", ""),
                "satker_name": row.get("satker_name", ""),
                "no_sp2d": row.get("no_sp2d", ""),
                "tahun": tahun,
                "bulan_sp2d": bulan,
                "nomor_spm": row.get("nomor_spm_extracted", ""),
                "tgl_spm": row.get("tgl_sp2d") or row.get("tanggal_invoice", ""),
                "jenis_spm": row.get("jenis_spm", ""),
                "cara_pembayaran": row.get("mata_uang", ""),
                "akun": "", # To be filled by user
                "deskripsi": row.get("deskripsi", ""),
                "nilai_bruto": row.get("nilai_spm", 0),
                "nilai_netto": row.get("nilai_sp2d", 0),
                "potongan": row.get("potongan", 0),
                "no_kuitansi": "",
                "no_drpp": "",
                "pembebanan": "",
                "fp": "",
                "pph21": 0,
            })
            
    except Exception as e:
        messages.error(request, f"Gagal membaca file Excel: {str(e)}")
        return redirect("sp2d:list")

    context = permission_context(request.user)
    context.update({
        "page_title": "Preview Import SP2D",
        "page_subtitle": f"File: {import_data['original_filename']} ({parse_result['valid_rows']} baris valid)",
        "preview_rows": mapped_rows,
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
