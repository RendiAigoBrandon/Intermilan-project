from django.contrib.auth.decorators import login_required
import csv
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db.models import Count, Max, Q, Sum
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.access import can_access_audit_data, can_edit_satker, filter_by_satker, get_profile, permission_context
from apps.core.models import MonitoringSummary
from apps.core.satker import get_satker_name_map
from apps.dk.models import MasterAkun, TransactionDetail
from apps.documents.models import ChecklistStatus, ChecklistTemplate, DocumentDriveLink, DocumentUpload
from apps.drpp.models import DRPPItem, DRPPMatch, DRPPUpload
from apps.paket_spm.models import PaketSPMUpload
from apps.sp2d.models import SP2DImportBatch, SP2DRaw


MONITORING_COLUMNS = [
    "BPS Prov/Kab/Kota (pilih sesuai satker msg2)",
    "Bulan SP2D",
    "Realisasi FA 16 Detil Bulan ini (di isi satker)",
    "Realisasi Intermilan Bulan ini",
    "Realisasi Intermilan s.d Bulan Ini",
    "Persentase Realisasi Intermilan terhadap FA 16 Detil (Max 100%)",
    "Persentase Kelengkapan Dokumen",
    "Persentase SPJ yang sudah di Upload",
    "Apakah sudah di arsipkan?        (V) Sudah       ( ) Belum",
    "Deadline",
    "Status",
    "% Completed",
    "BAR",
]

D_K_COLUMNS = [
    "Helper", "Akun", "SP2D Bulan", "Cara Pembayaran", "Nomor SPM", "Tanggal SPM",
    "Jenis SPM", "No. Kuitansi", "No. DRPP", "Deskripsi", "Nilai Bruto", "Nilai Netto",
    "Pembebanan", "FP", "PPh21",
]

DASHBOARD_COLUMNS = [
    "Bulan SP2D",
    "Cara Pembayaran",
    "Nomor SPM",
    "Jenis SPM",
    "No. Kuitansi (Hanya untuk dana UP/PTUP)/No. SPM",
    "No. DRPP",
    "Uraian Belanja per Transaksi",
    "Nilai (Bruto)",
    "Pembebanan",
    "% Kelengkapan",
]

SP2D_COLUMNS = [
    "No", "Satker", "Nama Satker", "No. SP2D", "Tanggal Selesai SP2D", "Tgl SP2D",
    "Mata Uang", "Nilai SPM", "Potongan", "Nilai SP2D", "Nomor Invoice", "Status",
]

UPLOAD_COLUMNS = ["No. SPM / Kuitansi", "URL"]

CHECKLIST_ROWS = [
    "SP2D", "SPM", "SPBy", "KAK", "Form permintaan/ nota dinas", "Undangan", "Daftar Hadir",
    "Kuitansi dan Bukti Pembayaran", "Bukti Prestasi Kerja", "Laporan Pelaksanaan Kegiatan", "BAPP",
    "BAST", "BAP", "SSP", "Realisasi BOS", "Pencatatan Non Tender", "Catatan Petugas",
    "Tagihan/Rekening", "Kuitansi/Bukti Pembayaran", "SSP/Pajak", "Faktur/Nota/Invoice",
    "Dokumen pendukung tambahan",
]

MONTH_OPTIONS = [
    (1, "Januari"),
    (2, "Februari"),
    (3, "Maret"),
    (4, "April"),
    (5, "Mei"),
    (6, "Juni"),
    (7, "Juli"),
    (8, "Agustus"),
    (9, "September"),
    (10, "Oktober"),
    (11, "November"),
    (12, "Desember"),
]

PAGE_SIZE_OPTIONS = (20, 50, 100)

MASTER_AKUN_ROWS = [
    ("51", "Belanja Pegawai", "Belanja Pegawai"),
    ("511111", "Akun 511111", "Belanja Pegawai"),
    ("511112", "Akun 511112", "Belanja Pegawai"),
    ("511119", "Akun 511119", "Belanja Pegawai"),
    ("511121", "Akun 511121", "Belanja Pegawai"),
    ("511124", "Belanja Tunjangan Fungsional PNS", "Belanja Pegawai"),
    ("511129", "Belanja Uang Makan PNS", "Belanja Pegawai"),
    ("521111", "Belanja Keperluan Perkan", "Belanja Barang Operasional"),
    ("521211", "Belanja Bahan", "Belanja Barang Operasional"),
    ("521213", "Honor Petugas", "Belanja Barang Non Operasional"),
    ("522111", "Belanja Langganan Listrik", "Belanja Jasa"),
    ("522141", "Sewa", "Belanja Jasa"),
    ("524111", "Belanja Perjalanan Dinas Biasa", "Belanja Perjalanan Dinas"),
]

MOM_ROWS = [
    {"satker": "bps1300", "pct": "93.94%", "fa": 100, "bulan": 92, "sd": 90},
    {"satker": "bps1301", "pct": "74.57%", "fa": 28, "bulan": 18, "sd": 18},
    {"satker": "bps1302", "pct": "96.07%", "fa": 22, "bulan": 21, "sd": 21},
    {"satker": "bps1303", "pct": "79.22%", "fa": 22, "bulan": 17, "sd": 17},
    {"satker": "bps1304", "pct": "95.55%", "fa": 21, "bulan": 19, "sd": 19},
    {"satker": "bps1305", "pct": "94.00%", "fa": 20, "bulan": 18, "sd": 18},
    {"satker": "bps1306", "pct": "95.72%", "fa": 35, "bulan": 33, "sd": 33},
    {"satker": "bps1307", "pct": "0.00%", "fa": 0, "bulan": 0, "sd": 0},
    {"satker": "bps1308", "pct": "94.53%", "fa": 32, "bulan": 30, "sd": 29},
    {"satker": "bps1309", "pct": "94.99%", "fa": 22, "bulan": 20, "sd": 20},
]

DASHBOARD_TABLE_ROWS = []

AUDIT_FINDINGS = {
    "sp2d_batch": 43,
    "sp2d_raw": 1874,
    "dk_total": 6661,
    "master_akun_total": 53,
    "checklist_template_total": 601,
    "checklist_status_total": 127188,
    "drive_total": 4060,
    "drpp_upload": 1,
    "drpp_item": 4,
    "drpp_match": 4,
    "dk_legacy": 5359,
    "dk_extra": 1302,
    "dk_blank_keys": 30,
    "dk_duplicate_groups": 1,
    "master_akun_extra": "51xxxx",
    "master_akun_legacy": 52,
    "checklist_distinct_names": 168,
    "checklist_ada": 9082,
    "checklist_belum": 116472,
    "checklist_tidak_perlu": 1634,
    "checklist_orphan": 0,
    "checklist_duplicate_pairs": 0,
    "drive_matched": 3994,
    "drive_unmatched": 66,
    "drive_invalid_id": 813,
    "drive_invalid_satker": "1303",
    "drive_invalid_spm": "00040A",
    "drive_invalid_sample": "00040A.pdf",
    "sp2d_empty_no_sp2d": 1694,
    "sp2d_duplicate_groups": 90,
    "sp2d_empty_extracted_spm": 0,
    "sp2d_invalid_value": 126,
    "sp2d_null_date": 96,
    "invalid_date_workbook": "KK_1308.xlsx",
    "invalid_date_sheet": "D_K",
    "invalid_date_cells": "F256-F280",
    "invalid_date_column": "Tanggal SPM",
    "invalid_date_values": "6693561.0 dan 6693566.0",
    "transaction_test_keyword": 2,
    "sp2d_test_keyword": 1,
}


def common_context(request):
    context = {
        "user_scope": "Semua Satker",
        "current_time_label": "30/06/2026 12:05",
    }
    context.update(permission_context(request.user))
    return context


def normalize_page_size(value):
    try:
        page_size = int(value)
    except (TypeError, ValueError):
        return 20
    return page_size if page_size in PAGE_SIZE_OPTIONS else 20


def build_pagination_window(page_obj):
    number = page_obj.number
    total = page_obj.paginator.num_pages
    pages = {1, total, number - 1, number, number + 1}
    if number <= 3:
        pages.update(range(1, min(total, 4) + 1))
    if number >= total - 2:
        pages.update(range(max(1, total - 3), total + 1))
    return [page for page in sorted(pages) if 1 <= page <= total]


def attach_satker_names(rows):
    codes = {row.satker_code for row in rows if row.satker_code}
    names = get_satker_name_map(codes)
    for row in rows:
        row.display_satker_name = names.get(row.satker_code, "")


@login_required
def home(request):
    context = common_context(request)
    context.update({"page_title": "Home", "hide_page_heading": True})
    return render(request, "core/home.html", context)


@login_required
def dashboard(request):
    profile = get_profile(request.user)
    selected_year = request.GET.get("tahun", "2026").strip() or "2026"
    selected_month = request.GET.get("bulan", "1").strip() or "1"
    selected_jenis_spm = request.GET.get("jenis_spm", "").strip()
    selected_month = selected_month if selected_month.isdigit() and 1 <= int(selected_month) <= 12 else "1"
    selected_year_int = int(selected_year) if selected_year.isdigit() else 2026
    selected_month_int = int(selected_month)
    sp2d_qs = filter_by_satker(SP2DRaw.objects.all(), request.user)
    dk_qs = filter_by_satker(TransactionDetail.objects.all(), request.user)
    drpp_qs = filter_by_satker(DRPPUpload.objects.all(), request.user)
    dk_chart_qs = TransactionDetail.objects.all()
    sp2d_chart_qs = sp2d_qs if selected_year == "2026" else sp2d_qs.none()
    dk_focus_qs = dk_chart_qs
    if selected_month:
        dk_focus_qs = dk_focus_qs.filter(bulan_sp2d=selected_month_int)
    dk_table_qs = dk_qs.filter(bulan_sp2d=selected_month_int) if selected_month else dk_qs
    if selected_jenis_spm:
        dk_table_qs = dk_table_qs.filter(jenis_spm=selected_jenis_spm)
    document_qs = DocumentUpload.objects.all()
    if profile and profile.is_satker:
        document_qs = document_qs.filter(transaction_detail__satker_code=profile.satker_code)
    totals = dk_qs.aggregate(nilai_bruto=Sum("nilai_bruto"), nilai_netto=Sum("nilai_netto"))
    focus_totals = dk_focus_qs.aggregate(nilai_netto=Sum("nilai_netto"))
    summary_qs = MonitoringSummary.objects.filter(tahun=selected_year_int, bulan_number=selected_month_int)
    summary_available = summary_qs.exists()
    if summary_available:
        mom_rows = build_mom_rows_from_summary(summary_qs)
        summary_focus = summary_qs.aggregate(nilai=Sum("intermilan_bulan_ini"), refreshed=Max("last_refreshed_at"))
        focus_value = summary_focus["nilai"] or 0
        last_refreshed = summary_focus["refreshed"]
    else:
        mom_rows = build_mom_rows(dk_chart_qs, selected_month)
        focus_value = focus_totals["nilai_netto"] or 0
        last_refreshed = None
    dashboard_rows = build_dashboard_rows(dk_table_qs)
    card_scope = build_dashboard_scope(request.user)
    chart_scope = build_dashboard_chart_scope(request.user)
    focus_month_label = month_name(selected_month_int)
    year_options = get_dashboard_year_options()
    context = common_context(request)
    context.update({
        "page_title": "Dashboard INTERMILAN",
        "page_subtitle": "Pantau realisasi, transaksi, dan kelengkapan dokumen per satker.",
        "stats": {
            "sp2d": sp2d_qs.count(),
            "perlu_detail": sp2d_qs.filter(status=SP2DRaw.Status.PERLU_DETAIL).count(),
            "dk": dk_qs.count(),
            "drpp": drpp_qs.count(),
            "nilai_bruto": totals["nilai_bruto"] or 0,
            "nilai_netto": totals["nilai_netto"] or 0,
            "documents": document_qs.count() + DocumentDriveLink.objects.count(),
        },
        "dashboard_filters": {"tahun": selected_year, "bulan": selected_month, "jenis_spm": selected_jenis_spm},
        "year_options": year_options,
        "months": MONTH_OPTIONS,
        "jenis_spm_options": get_dashboard_jenis_spm_options(dk_qs),
        "card_scope_label": card_scope["label"],
        "card_scope_note": card_scope["note"],
        "scope_label": chart_scope["label"],
        "scope_note": chart_scope["note"],
        "data_window_note": "Chart utama membandingkan realisasi lintas satker pada bulan terpilih; label X-axis mengikuti kode bps1300, bps1301, dst.",
        "fa16_note": (
            "Sumber chart: MonitoringSummary hasil baseline Monitoring_Combine dan refresh data web. FA16 tidak dihitung dari D_K."
            if summary_available
            else "MonitoringSummary belum tersedia untuk periode ini; chart fallback menghitung Intermilan dari D_K dan FA16 tetap 0."
        ),
        "summary_source_label": "MonitoringSummary" if summary_available else "Fallback D_K",
        "last_refreshed_label": format_datetime_id(last_refreshed) if last_refreshed else "Belum pernah refresh; memakai baseline/import terakhir.",
        "focus_summary": {
            "month": focus_month_label,
            "transaction_count": dk_focus_qs.count() if selected_month else dk_chart_qs.count(),
            "intermilan_value": format_id_number(focus_value),
        },
        "mom_rows": mom_rows,
        "dashboard_columns": DASHBOARD_COLUMNS,
        "dashboard_rows": dashboard_rows or DASHBOARD_TABLE_ROWS,
        "dashboard_table_source": "D_K preview sesuai kolom Dashboard Excel",
        "recent_sp2d": sp2d_chart_qs[:5],
    })
    return render(request, "core/dashboard.html", context)


@login_required
def monitoring(request):
    filters = {
        "q": request.GET.get("q", "").strip(),
        "tahun": request.GET.get("tahun", "2026").strip() or "2026",
        "satker": request.GET.get("satker", "").strip(),
        "bulan": request.GET.get("bulan", "").strip(),
        "status": request.GET.get("status", "").strip(),
    }
    summary_qs = MonitoringSummary.objects.all()
    if filters["tahun"].isdigit():
        summary_qs = summary_qs.filter(tahun=int(filters["tahun"]))
    if filters["satker"]:
        summary_qs = summary_qs.filter(satker_code=filters["satker"])
    if filters["bulan"]:
        summary_qs = summary_qs.filter(bulan_number=filters["bulan"])
    if filters["status"]:
        summary_qs = summary_qs.filter(status__iexact=filters["status"])

    summary_available = MonitoringSummary.objects.exists()
    if summary_available:
        rows = build_monitoring_rows_from_summary(summary_qs)
        if filters["q"]:
            rows = filter_monitoring_rows(rows, filters["q"])
        summary = build_monitoring_summary_cards(rows)
        satker_options = get_monitoring_summary_satker_options()
        status_options = get_monitoring_summary_status_options()
        year_options = get_dashboard_year_options()
        source_label = "MonitoringSummary"
    else:
        queryset = TransactionDetail.objects.all()
        if filters["satker"]:
            queryset = queryset.filter(satker_code=filters["satker"])
        if filters["bulan"]:
            queryset = queryset.filter(bulan_sp2d=filters["bulan"])
        rows = build_monitoring_rows(queryset)
        if filters["status"]:
            rows = [row for row in rows if row["status_key"] == filters["status"]]
        if filters["q"]:
            rows = filter_monitoring_rows(rows, filters["q"])
        total = queryset.count()
        lengkap = ChecklistStatus.objects.filter(transaction_detail__in=queryset, status=ChecklistStatus.Status.ADA).values("transaction_detail").distinct().count()
        persen = f"{(lengkap / total * 100):.1f}%" if total else "0.0%"
        summary = {"hasil": total, "lengkap": lengkap, "belum": max(total - lengkap, 0), "persen": persen}
        satker_options = get_monitoring_satker_options()
        status_options = ["In Progress"]
        year_options = ["2026"]
        source_label = "Fallback D_K"
    context = common_context(request)
    context.update({
        "page_title": "Monitoring Dokumen",
        "page_subtitle": "Pantau kelengkapan dokumen transaksi lintas satker secara terpusat.",
        "columns": MONITORING_COLUMNS,
        "rows": rows,
        "filters": filters,
        "satker_options": satker_options,
        "status_options": status_options,
        "year_options": year_options,
        "months": MONTH_OPTIONS,
        "summary": summary,
        "source_label": source_label,
    })
    return render(request, "core/monitoring.html", context)


@login_required
def master_akun(request):
    rows = list(MasterAkun.objects.filter(is_active=True).values_list("kode", "nama_akun", "kategori")[:100])
    if not rows:
        rows = MASTER_AKUN_ROWS
    context = common_context(request)
    context.update({"page_title": "Master Akun", "page_subtitle": "Kelola referensi kode akun dan kategori transaksi.", "rows": rows})
    return render(request, "core/master_akun.html", context)


@login_required
def akun_index(request):
    master_rows = MasterAkun.objects.filter(is_active=True)
    summaries = {
        item["akun"]: item
        for item in TransactionDetail.objects.values("akun").annotate(total=Count("id"), nilai=Sum("nilai_netto"))
    }
    rows = []
    for master in master_rows[:100]:
        summary = summaries.get(master.kode, {})
        rows.append({
            "kode": master.kode,
            "nama": master.nama_akun,
            "kategori": master.kategori,
            "total": summary.get("total", 0),
            "nilai": summary.get("nilai", 0) or 0,
            "checklist": 0,
        })
    if not rows:
        rows = [
            {"kode": kode, "nama": nama, "kategori": kategori, "total": 0, "nilai": 0, "checklist": 0}
            for kode, nama, kategori in MASTER_AKUN_ROWS
        ]
    context = common_context(request)
    context.update({"page_title": "Akun Keuangan", "page_subtitle": "Ringkasan transaksi dan progres dokumen berdasarkan kode akun.", "rows": rows})
    return render(request, "core/akun_index.html", context)


@login_required
def akun_detail(request, kode):
    normalized_code = (kode or "").strip()
    query = request.GET.copy()
    query["akun"] = normalized_code
    return redirect(f"{reverse('dk:list')}?{query.urlencode()}")


@login_required
def audit_data(request):
    if not can_access_audit_data(request.user):
        raise PermissionDenied("Review Data hanya dapat diakses Admin.")
    context = common_context(request)
    context.update({
        "page_title": "Review Data",
        "page_subtitle": "Tinjau temuan audit import secara read-only sebelum proses perbaikan data.",
        **build_audit_context(),
    })
    return render(request, "core/audit_data.html", context)


@login_required
def audit_data_export(request):
    if not can_access_audit_data(request.user):
        raise PermissionDenied("Export Review Data hanya dapat diakses Admin.")
    audit_context = build_audit_context()
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    date_label = timezone.localtime().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="audit_data_intermilan_{date_label}.csv"'
    writer = csv.writer(response)
    writer.writerow(["kategori", "item", "jumlah", "detail", "status_review", "rekomendasi"])
    for item in audit_context["export_rows"]:
        writer.writerow([
            item["kategori"],
            item["item"],
            item["jumlah"],
            item["detail"],
            item["status_review"],
            item["rekomendasi"],
        ])
    return response


def build_audit_context():
    duplicate_dk_groups = list(
        TransactionDetail.objects.values(
            "satker_code", "nomor_spm", "no_kuitansi", "no_drpp", "akun", "nilai_bruto", "nilai_netto"
        )
        .annotate(total=Count("id"))
        .filter(total__gt=1)
        .order_by("-total", "satker_code")[:10]
    )
    blank_key_rows = list(
        TransactionDetail.objects.filter(nomor_spm="", no_kuitansi="", no_drpp="")
        .values("id", "satker_code", "akun", "kategori", "nilai_netto", "deskripsi")[:10]
    )
    master_extra = list(MasterAkun.objects.filter(kode__iexact=AUDIT_FINDINGS["master_akun_extra"])[:5])
    invalid_drive_links = list(
        DocumentDriveLink.objects.exclude(google_drive_url__startswith="http")
        .values("id", "satker_code", "nomor_spm", "google_drive_url")[:10]
    )
    duplicate_sp2d_groups = list(
        SP2DRaw.objects.exclude(no_sp2d="")
        .values("no_sp2d")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
        .order_by("-total", "no_sp2d")[:10]
    )

    totals = {
        "sp2d_batch": SP2DImportBatch.objects.count(),
        "sp2d": SP2DRaw.objects.count(),
        "dk": TransactionDetail.objects.count(),
        "master_akun": MasterAkun.objects.count(),
        "checklist_template": ChecklistTemplate.objects.count(),
        "checklist_status": ChecklistStatus.objects.count(),
        "document_links": DocumentDriveLink.objects.count(),
        "document_uploads": DocumentUpload.objects.count(),
        "drpp_upload": DRPPUpload.objects.count(),
        "drpp_item": DRPPItem.objects.count(),
        "drpp_match": DRPPMatch.objects.count(),
        "paket_spm": PaketSPMUpload.objects.count(),
        "monitoring_summary": MonitoringSummary.objects.count(),
    }
    audit_is_clean = all(
        totals[key] == 0
        for key in [
            "sp2d_batch",
            "sp2d",
            "dk",
            "checklist_status",
            "document_links",
            "document_uploads",
            "drpp_upload",
            "drpp_item",
            "drpp_match",
            "paket_spm",
            "monitoring_summary",
        ]
    )
    sp2d_review = {
        "empty_no_sp2d": SP2DRaw.objects.filter(no_sp2d="").count(),
        "duplicate_groups": (
            SP2DRaw.objects.exclude(no_sp2d="")
            .values("no_sp2d")
            .annotate(total=Count("id"))
            .filter(total__gt=1)
            .count()
        ),
        "invalid_value": SP2DRaw.objects.filter(nilai_sp2d__lte=0).count(),
        "null_date": SP2DRaw.objects.filter(tgl_sp2d__isnull=True).count(),
    }
    drive_summary = {
        "total": DocumentDriveLink.objects.count(),
        "matched": DocumentDriveLink.objects.filter(transaction_detail__isnull=False).count(),
        "unmatched": DocumentDriveLink.objects.filter(transaction_detail__isnull=True).count(),
        "invalid_id": AUDIT_FINDINGS["drive_invalid_id"],
        "invalid_satker": AUDIT_FINDINGS["drive_invalid_satker"],
        "invalid_spm": AUDIT_FINDINGS["drive_invalid_spm"],
        "invalid_sample": AUDIT_FINDINGS["drive_invalid_sample"],
    }
    checklist_summary = {
        "template_total": ChecklistTemplate.objects.count(),
        "distinct_names": ChecklistTemplate.objects.values("nama_dokumen").distinct().count(),
        "status_total": ChecklistStatus.objects.count(),
        "ada": AUDIT_FINDINGS["checklist_ada"],
        "belum": AUDIT_FINDINGS["checklist_belum"],
        "tidak_perlu": AUDIT_FINDINGS["checklist_tidak_perlu"],
        "orphan": AUDIT_FINDINGS["checklist_orphan"],
        "duplicate_pairs": AUDIT_FINDINGS["checklist_duplicate_pairs"],
    }
    dummy_summary = {
        "transaction_test": TransactionDetail.objects.filter(deskripsi__icontains="test").count(),
        "sp2d_test": SP2DRaw.objects.filter(deskripsi__icontains="test").count(),
    }
    export_rows = [
        {"kategori": "Jumlah Data", "item": "SP2D batch", "jumlah": totals["sp2d_batch"], "detail": "Metadata batch import SP2D.", "status_review": "read-only", "rekomendasi": "Pantau konsistensi batch sebelum import ulang."},
        {"kategori": "Jumlah Data", "item": "SP2D raw", "jumlah": totals["sp2d"], "detail": "Data mentah SP2D di database development.", "status_review": "read-only", "rekomendasi": "Tidak ada cleanup otomatis."},
        {"kategori": "Jumlah Data", "item": "D_K / TransactionDetail", "jumlah": totals["dk"], "detail": "Total transaksi detail saat audit.", "status_review": "read-only", "rekomendasi": "Review tambahan non-legacy dan duplikat kandidat."},
        {"kategori": "Jumlah Data", "item": "Master Akun", "jumlah": totals["master_akun"], "detail": "Total master akun saat audit.", "status_review": "read-only", "rekomendasi": "Putuskan status kode 51xxxx."},
        {"kategori": "Jumlah Data", "item": "Checklist template", "jumlah": totals["checklist_template"], "detail": "Template legacy berisi kombinasi dokumen/rule.", "status_review": "read-only", "rekomendasi": "Normalisasi hanya setelah disetujui."},
        {"kategori": "Jumlah Data", "item": "Checklist status", "jumlah": totals["checklist_status"], "detail": "Status checklist per transaksi.", "status_review": "valid", "rekomendasi": "Tidak ada orphan/duplikat pasangan dari audit."},
        {"kategori": "Jumlah Data", "item": "DocumentDriveLink", "jumlah": totals["document_links"], "detail": "Metadata link dokumen.", "status_review": "read-only", "rekomendasi": "Review link belum match."},
        {"kategori": "Jumlah Data", "item": "DRPP upload/item/match", "jumlah": f"{totals['drpp_upload']} / {totals['drpp_item']} / {totals['drpp_match']}", "detail": "Foundation DRPP hasil import.", "status_review": "read-only", "rekomendasi": "Tidak lanjut parser/OCR."},
        {"kategori": "D_K", "item": "Cocok SQLite legacy", "jumlah": AUDIT_FINDINGS["dk_legacy"], "detail": "Baris cocok dengan data legacy.", "status_review": "tercatat", "rekomendasi": "Pertahankan sebagai baseline review."},
        {"kategori": "D_K", "item": "Tambahan non-legacy", "jumlah": AUDIT_FINDINGS["dk_extra"], "detail": "Berdasarkan audit pembanding SQLite legacy.", "status_review": "perlu review", "rekomendasi": "Review manual sebelum cleanup."},
        {"kategori": "D_K", "item": "Baris tanpa SPM/kuitansi/DRPP", "jumlah": AUDIT_FINDINGS["dk_blank_keys"], "detail": "Key transaksi kosong pada audit.", "status_review": "perlu review", "rekomendasi": "Validasi sumber baris."},
        {"kategori": "D_K", "item": "Kandidat duplikat key gabungan", "jumlah": AUDIT_FINDINGS["dk_duplicate_groups"], "detail": "Satker 1376, SPM 00085A, KW 00085A, akun 522112.", "status_review": "perlu review", "rekomendasi": "Jangan hapus sebelum disetujui."},
        {"kategori": "Master Akun", "item": "Tambahan dibanding legacy", "jumlah": AUDIT_FINDINGS["master_akun_extra"], "detail": "Legacy 52, hasil akhir 53.", "status_review": "perlu keputusan", "rekomendasi": "Tentukan valid/agregat/placeholder."},
        {"kategori": "Checklist", "item": "ChecklistTemplate", "jumlah": checklist_summary["template_total"], "detail": "Distinct nama_dokumen 168.", "status_review": "perlu review", "rekomendasi": "Pertimbangkan normalisasi setelah disetujui."},
        {"kategori": "Checklist", "item": "ChecklistStatus ADA/BELUM/TIDAK_PERLU", "jumlah": "9082 / 116472 / 1634", "detail": "Orphan 0, duplikat pair 0.", "status_review": "valid", "rekomendasi": "Tidak ada perbaikan otomatis."},
        {"kategori": "DocumentDriveLink", "item": "Matched/belum matched", "jumlah": f"{drive_summary['matched']} / {drive_summary['unmatched']}", "detail": "Invalid sample id 813 satker 1303 SPM 00040A URL 00040A.pdf.", "status_review": "perlu review", "rekomendasi": "Validasi 66 link belum match."},
        {"kategori": "SP2D", "item": "no_sp2d kosong", "jumlah": sp2d_review["empty_no_sp2d"], "detail": "nomor_spm_extracted kosong 0.", "status_review": "perlu review", "rekomendasi": "Jangan jadikan no_sp2d unique key tunggal dulu."},
        {"kategori": "SP2D", "item": "Duplikat no_sp2d", "jumlah": sp2d_review["duplicate_groups"], "detail": "Grup duplikat non-empty no_sp2d.", "status_review": "perlu review", "rekomendasi": "Review manual sebelum constraint."},
        {"kategori": "SP2D", "item": "Nilai/tanggal bermasalah", "jumlah": f"{sp2d_review['invalid_value']} / {sp2d_review['null_date']}", "detail": "nilai_sp2d <= 0 dan tanggal_sp2d null.", "status_review": "perlu review", "rekomendasi": "Validasi dengan sumber SP2D."},
        {"kategori": "Tanggal Excel", "item": "Tanggal SPM invalid", "jumlah": AUDIT_FINDINGS["invalid_date_cells"], "detail": "KK_1308.xlsx, sheet D_K, nilai 6693561.0 dan 6693566.0.", "status_review": "perlu review", "rekomendasi": "Perbaiki sumber Excel sebelum laporan resmi."},
        {"kategori": "Data test", "item": "TransactionDetail.deskripsi", "jumlah": dummy_summary["transaction_test"], "detail": "Keyword test ditemukan.", "status_review": "perlu review", "rekomendasi": "Jangan hapus otomatis."},
        {"kategori": "Data test", "item": "SP2DRaw.deskripsi", "jumlah": dummy_summary["sp2d_test"], "detail": "Keyword test ditemukan.", "status_review": "perlu review", "rekomendasi": "Jangan hapus otomatis."},
    ]
    return {
        "audit_findings": AUDIT_FINDINGS,
        "audit_totals": totals,
        "audit_is_clean": audit_is_clean,
        "duplicate_dk_groups": duplicate_dk_groups,
        "blank_key_rows": blank_key_rows,
        "master_extra": master_extra,
        "checklist_summary": checklist_summary,
        "drive_summary": drive_summary,
        "invalid_drive_links": invalid_drive_links,
        "sp2d_review": sp2d_review,
        "duplicate_sp2d_groups": duplicate_sp2d_groups,
        "dummy_summary": dummy_summary,
        "export_rows": export_rows,
    }


def build_dashboard_rows(queryset):
    rows = []
    page_rows = list(queryset.select_related("sp2d_raw").order_by("bulan_sp2d", "cara_pembayaran", "nomor_spm", "id")[:20])
    satker_names = get_satker_name_map(row.satker_code for row in page_rows)
    for item in page_rows:
        rows.append(
            {
                "satker_code": item.satker_code or "-",
                "satker_name": satker_names.get(item.satker_code, "-"),
                "cells": [
                    month_name(item.bulan_sp2d),
                    item.cara_pembayaran or "-",
                    item.nomor_spm or "-",
                    item.jenis_spm or "-",
                    item.no_kuitansi or item.nomor_spm or "-",
                    item.no_drpp or "-",
                    item.deskripsi or "-",
                    format_id_number(item.nilai_bruto),
                    item.pembebanan or "-",
                    "-",
                ],
            }
        )
    return rows


def build_dashboard_scope(user):
    profile = get_profile(user)
    if not profile:
        return {"label": "Scope: Semua Satker", "note": "Data dashboard mengikuti akses pengguna aktif."}
    if user.is_superuser or profile.is_admin_pusat:
        return {
            "label": "Scope: Semua Satker",
            "note": "Admin melihat agregasi seluruh satker dari database aktif.",
        }
    if profile.is_satker:
        satker_name = profile.satker_name or "-"
        return {
            "label": f"Scope: Satker {profile.satker_code}",
            "note": f"Operator melihat data milik satker {profile.satker_code} - {satker_name}.",
        }
    return {
        "label": "Scope: Semua Satker (Read Only)",
        "note": "Viewer melihat agregasi lintas satker tanpa akses ubah data.",
    }


def build_dashboard_chart_scope(user):
    profile = get_profile(user)
    if profile and profile.is_satker:
        return {
            "label": "Scope Chart: Semua Satker (Read Only)",
            "note": "Operator dapat membandingkan monitoring lintas satker, tetapi aksi/edit tetap dibatasi ke satker sendiri.",
        }
    if profile and profile.is_viewer:
        return {
            "label": "Scope Chart: Semua Satker (Read Only)",
            "note": "Viewer melihat monitoring lintas satker tanpa akses ubah data.",
        }
    return {
        "label": "Scope Chart: Semua Satker",
        "note": "Admin melihat chart monitoring lintas seluruh satker.",
    }


def build_monitoring_rows(queryset):
    grouped = (
        queryset.values("satker_code", "bulan_sp2d")
        .annotate(nilai=Sum("nilai_netto"), transaksi=Count("id"))
        .order_by("satker_code", "bulan_sp2d")[:40]
    )
    satker_names = {
        item["satker_code"]: item["satker_name"]
        for item in SP2DRaw.objects.filter(satker_code__in=[item["satker_code"] for item in grouped])
        .exclude(satker_name="")
        .values("satker_code", "satker_name")
        .distinct()
    }
    rows = []
    for item in grouped:
        nilai = item["nilai"] or 0
        pct = "100,00%" if nilai else "0,00%"
        completed = "38,89%" if nilai else "0,00%"
        rows.append(
            {
                "bps": f"bps{item['satker_code']}" if item["satker_code"] else "-",
                "satker_name": satker_names.get(item["satker_code"], ""),
                "bulan": month_name(item["bulan_sp2d"]),
                "fa": format_id_number(nilai),
                "intermilan_bulan": format_id_number(nilai),
                "intermilan_sd": format_id_number(nilai),
                "pct_realisasi": pct,
                "pct_dokumen": "16,67%" if nilai else "0,00%",
                "pct_spj": "100,00%" if nilai else "0,00%",
                "arsip": "0",
                "deadline": "25 February 2026",
                "status": "In Progress",
                "status_key": "in_progress",
                "completed": completed,
                "bar": completed,
            }
        )
    return rows


def build_monitoring_rows_from_summary(queryset):
    rows = []
    for item in queryset.order_by("tahun", "bulan_number", "satker_code")[:500]:
        rows.append({
            "bps": item.satker_label or f"bps{item.satker_code}",
            "satker_name": item.satker_label or "",
            "bulan": item.bulan or month_name(item.bulan_number),
            "fa": format_id_number(item.fa16_bulan_ini),
            "intermilan_bulan": format_id_number(item.intermilan_bulan_ini),
            "intermilan_sd": format_id_number(item.intermilan_sd_bulan_ini),
            "pct_realisasi": format_percent_id(item.persen_realisasi),
            "pct_dokumen": format_percent_id(item.persen_kelengkapan_dokumen),
            "pct_spj": format_percent_id(item.persen_spj_upload),
            "arsip": format_percent_id(item.persen_arsip),
            "deadline": item.deadline.strftime("%d %B %Y") if item.deadline else "-",
            "status": item.status or "-",
            "status_key": (item.status or "").lower(),
            "completed": format_percent_id(item.percent_completed),
            "bar": item.bar or format_percent_id(item.percent_completed),
        })
    return rows


def build_monitoring_summary_cards(rows):
    total = len(rows)
    lengkap = sum(1 for row in rows if row.get("completed") == "100,00%")
    avg = Decimal("0")
    if rows:
        values = [parse_percent_display(row.get("completed")) for row in rows]
        avg = sum(values, Decimal("0")) / Decimal(len(values))
    return {
        "hasil": total,
        "lengkap": lengkap,
        "belum": max(total - lengkap, 0),
        "persen": format_percent_id(avg),
    }


def get_monitoring_satker_options():
    return build_satker_options(TransactionDetail.objects.exclude(satker_code=""))


def get_monitoring_summary_satker_options():
    satker_names = get_satker_name_map()
    return [
        {"satker_code": item["satker_code"], "satker_name": satker_names.get(item["satker_code"], item["satker_label"] or "-")}
        for item in MonitoringSummary.objects.exclude(satker_code="")
        .values("satker_code", "satker_label")
        .distinct()
        .order_by("satker_code")
    ]


def get_monitoring_summary_status_options():
    return list(
        MonitoringSummary.objects.exclude(status="")
        .values_list("status", flat=True)
        .distinct()
        .order_by("status")
    )


def build_satker_options(queryset):
    codes = list(queryset.values_list("satker_code", flat=True).distinct().order_by("satker_code")[:300])
    names = get_satker_name_map(codes)
    return [{"satker_code": code, "satker_name": names.get(code, "")} for code in codes if code]


def format_id_number(value):
    if value in (None, ""):
        return "-"
    return f"{value:,.0f}".replace(",", ".")


def build_mom_rows(queryset, selected_month=""):
    month_number = int(selected_month) if selected_month else None
    month_queryset = queryset.filter(bulan_sp2d=month_number) if month_number else queryset
    cumulative_queryset = queryset.filter(bulan_sp2d__lte=month_number) if month_number else queryset
    month_values = {
        item["satker_code"]: item["nilai"] or Decimal("0")
        for item in month_queryset.values("satker_code").annotate(nilai=Sum("nilai_netto"))
    }
    cumulative_values = {
        item["satker_code"]: item["nilai"] or Decimal("0")
        for item in cumulative_queryset.values("satker_code").annotate(nilai=Sum("nilai_netto"))
    }
    satker_codes = sorted(
        code for code in set(month_values.keys()) | set(cumulative_values.keys()) if code
    )
    max_value = max([*month_values.values(), *cumulative_values.values(), Decimal("1")])
    label_month = month_name(month_number) if month_number else "Semua Bulan"
    rows = []
    for code in satker_codes:
        intermilan_value = month_values.get(code, Decimal("0"))
        cumulative = cumulative_values.get(code, Decimal("0"))
        fa_value = Decimal("0")
        pct = Decimal("0")
        rows.append({
            "satker_code": code,
            "satker": f"bps{code}",
            "month": label_month,
            "pct": format_percent_id(pct),
            "pct_height": 0,
            "fa": percent_height(fa_value, max_value),
            "bulan": percent_height(intermilan_value, max_value),
            "sd": percent_height(cumulative, max_value),
            "fa_label": format_id_number(fa_value),
            "bulan_label": format_id_number(intermilan_value),
            "sd_label": format_id_number(cumulative),
            "pct_label": format_percent_id(pct),
        })
    return rows


def build_mom_rows_from_summary(queryset):
    rows = list(queryset.order_by("satker_code"))
    max_value = max(
        [
            *[item.fa16_bulan_ini for item in rows],
            *[item.intermilan_bulan_ini for item in rows],
            *[item.intermilan_sd_bulan_ini for item in rows],
            Decimal("1"),
        ]
    )
    result = []
    for item in rows:
        result.append({
            "satker_code": item.satker_code,
            "satker": item.satker_label or f"bps{item.satker_code}",
            "month": item.bulan,
            "pct": format_percent_id(item.persen_realisasi),
            "pct_height": percent_height(item.persen_realisasi, Decimal("100")),
            "fa": percent_height(item.fa16_bulan_ini, max_value),
            "bulan": percent_height(item.intermilan_bulan_ini, max_value),
            "sd": percent_height(item.intermilan_sd_bulan_ini, max_value),
            "fa_label": format_id_number(item.fa16_bulan_ini),
            "bulan_label": format_id_number(item.intermilan_bulan_ini),
            "sd_label": format_id_number(item.intermilan_sd_bulan_ini),
            "pct_label": format_percent_id(item.persen_realisasi),
        })
    return result


def get_dashboard_year_options():
    years = list(
        MonitoringSummary.objects.values_list("tahun", flat=True)
        .distinct()
        .order_by("tahun")
    )
    return [str(year) for year in years] or ["2026"]


def get_dashboard_jenis_spm_options(queryset):
    return list(
        queryset.exclude(jenis_spm="")
        .values_list("jenis_spm", flat=True)
        .distinct()
        .order_by("jenis_spm")[:100]
    )


def percent_height(value, max_value):
    if not max_value:
        return 0
    return max(0, min(100, int((value / max_value) * 100)))


def format_percent_id(value):
    return f"{value:.2f}%".replace(".", ",")


def parse_percent_display(value):
    text = str(value or "0").replace("%", "").replace(",", ".")
    try:
        return Decimal(text)
    except Exception:
        return Decimal("0")


def format_datetime_id(value):
    if not value:
        return "-"
    return timezone.localtime(value).strftime("%d/%m/%Y %H:%M")


def month_name(value):
    names = {
        1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
        7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November", 12: "Desember",
    }
    return names.get(value, "-")


def month_number_from_text(value):
    normalized = (value or "").strip().lower()
    for number, label in MONTH_OPTIONS:
        if label.lower() == normalized:
            return number
    return None


def filter_monitoring_rows(rows, search):
    term = (search or "").strip().lower()
    if not term:
        return rows
    filtered = []
    for row in rows:
        haystack = " ".join(
            str(row.get(key, ""))
            for key in ["bps", "satker_name", "bulan", "status", "completed", "bar", "fa", "intermilan_bulan", "intermilan_sd"]
        ).lower()
        if term in haystack:
            filtered.append(row)
    return filtered


@login_required
def static_reference(request, kind):
    titles = {"peraturan": "Peraturan", "template": "Template", "panduan": "Panduan Aplikasi"}
    context = common_context(request)
    context.update({"page_title": titles.get(kind, "Referensi"), "page_subtitle": "Referensi pendukung penggunaan INTERMILAN.", "kind": kind})
    return render(request, "core/reference.html", context)


def error_403(request, exception=None):
    return render(request, "403.html", status=403)


def error_404(request, exception=None):
    return render(request, "404.html", status=404)


def error_500(request):
    return render(request, "500.html", status=500)
