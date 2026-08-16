from django.contrib.auth.decorators import login_required
import csv
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db.models import Count, Max, Q, Sum
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.access import can_access_audit_data, can_edit_satker, can_view_all_satker, filter_by_satker, get_profile, get_user_official_satker_code, permission_context
from apps.core.models import MonitoringSummary
from apps.core.satker import get_satker_name_map
from apps.core.document_policy import get_required_documents, normalize_akun_family
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

REFERENCE_LINKS = {
    "peraturan": {
        "title": "Peraturan",
        "subtitle": "Daftar peraturan pengelolaan administrasi keuangan.",
        "items": [
            ("PMK No. 39 Tahun 2024 Tentang Standar Biaya Masukan (SBM) Tahun Anggaran 2025", "https://djpb.kemenkeu.go.id/kppn/bandarlampung/id/download/peraturan-terbaru/3089-pmk-no-39-tahun-2024-tentang-standar-biaya-masukan-sbm-tahun-anggaran-2025.html"),
            ("Perdirjen nomor PER-5/PB/2024 Tentang Petunjuk Teknis Penilaian Indikator Kinerja Pelaksanaan Anggaran Belanja Kementerian Negara/Lembaga", "https://djpb.kemenkeu.go.id/kppn/metro/id/download/peraturan/3506-perdirjen-nomor-per-5-pb-2024-tentang-petunjuk-teknis-penilaian-indikator-kinerja-pelaksanaan-anggaran-belanja-kementerian-negara-lembaga.html"),
            ("Peraturan Menteri Keuangan Nomor 119 Tahun 2023 tentang Perubahan Atas PMK Nomor 113/PMK.05/2012 tentang Perjalanan Dinas Dalam Negeri", "https://djpb.kemenkeu.go.id/kppn/metro/id/download/peraturan/3508-peraturan-menteri-keuangan-nomor-119-tahun-2023-tentang-perubahan-atas-peraturan-menteri-keuangan-nomor-113-pmk-05-2012-tentang-perjalanan-dinas-dalam-negeri-bagi-pejabat-negara,-pegawai-negeri-dan-pegawai-tidak-tetap.html"),
            ("PMK No. 232/PMK.05/2022 tentang Sistem Akuntansi dan Pelaporan Keuangan Instansi", "https://djpb.kemenkeu.go.id/kppn/metro/id/download/peraturan/3446-pmk-no-232-pmk-05-2022-tentang-sistem-akuntansi-dan-pelaporan-keuangan-instansi.html"),
            ("PMK No. 210/PMK.05/2022 Tentang Tata Cara Pembayaran Dalam Rangka Pelaksanaan Anggaran Pendapatan dan Belanja Negara", "https://djpb.kemenkeu.go.id/kppn/metro/id/download/peraturan/3444-pmk-no-210-pmk-05-2022-tentang-tata-cara-pembayaran-dalam-rangka-pelaksanaan-anggaran-pendapatan-dan-belanja-negara.html"),
            ("Perdirjen Perbendaharaan No. Per-7/PB/2022 Tentang Penggunaan Uang Persediaan Melalui Digipay Pada Satker K/L", "https://djpb.kemenkeu.go.id/kppn/metro/id/download/peraturan/3442-perdirjen-perbendaharaan-no-per-7-pb-2022-tentang-penggunaan-uang-persediaan-melalui-digipay-pada-satker-k-l.html"),
            ("Petunjuk Pengajuan UP 2025", "https://drive.google.com/file/d/1K2_zNG0vS0LLFtN6I0tV7pTrZ_wccWov/view?usp=drive_link"),
            ("Perka BPS No.115 Tahun 2024 Tentang Standar Biaya Kegiatan Statistik", "https://drive.google.com/file/d/136dGGDPbRSMW4MQUba-P0pPZl3NP7ibf/view?usp=drive_link"),
            ("Perka BPS No.165 Tahun 2024 tentang Perubahan Perka No. 115 Tahun 2024 tentang SBKS", "https://drive.google.com/file/d/1j0LiLUTbBkUdE4hLt0E0gADP4WqbsnQ9/view?usp=drive_link"),
            ("Perka BPS Nomor 5 Tahun 2024 tentang Kebijakan Akuntansi di Lingkungan BPS", "https://drive.google.com/file/d/1vyYiBCIL60r7ylemnc2z30B6w78GS3Ne/view?usp=drive_link"),
            ("Perka BPS Nomor 6 Tahun 2024 tentang Pedoman Pelaksanaan Perjalanan Dinas Jabatan di Lingkungan BPS", "https://docs.google.com/document/d/11gMIU1Lt2UAfSLzCPE5h6hJNntmDSrFj/edit"),
            ("Perka BPS Nomor 7 Tahun 2024 tentang Pedoman Transaksi Pembayaran Nontunai di Lingkungan BPS", "https://docs.google.com/document/d/1Y1NDVhAc0VlWNBL-yBFhg3ZYiqed5wcL/edit"),
            ("Perka BPS Nomor 8 Tahun 2024 tentang Pedoman Administrasi Keuangan Kegiatan Sensus dan Survei di Lingkungan BPS", "https://docs.google.com/document/d/1CRiQSqd4nxxKZoZuPb_pzw57KjHHKTkx/edit"),
        ],
    },
    "template": {
        "title": "Template",
        "subtitle": "Kumpulan format/template keuangan yang familiar dari INTERMILAN lama.",
        "items": [
            ("Template SPJ", "https://drive.google.com/drive/folders/1BxwAP32ahB1F2Gu59kuvM4iSBT6XUU8-"),
            ("Blanko Ralat Setoran MPN G2 Billing", "https://docs.google.com/document/d/15S2CBii73ybvGUq2hWS5ChnRxJmrcrE8/edit"),
            ("Blanko Ralat SPM SPAN", "https://docs.google.com/document/d/1kkN_X3lIYzGqP8arEkbdxm_I0UMspQ1b/edit"),
            ("PENONAKTIFAN-SUPPLIER-TIPE3 (PEGAWAI)", "https://docs.google.com/document/d/1zWyQNd2jopZrA_EnkyRsuJNr7WF0kvdT/edit"),
            ("PENGAKTIFAN-KEMBALI-SUPPLIER-TIPE3 (PEGAWAI)", "https://docs.google.com/document/d/1S5_aq4lEoGkM__LNCFGBEmNzdkN17Wpw/edit"),
            ("FORM PENJELASAN-KETIDAKSESUAIAN-TUP", "https://docs.google.com/document/d/1ZRxMCcPSqgfrO_p7AEgF2lAj3nn5h79H/edit"),
            ("FORM PERUBAHAN USER SAKTI", "https://docs.google.com/spreadsheets/d/1WSdGsRLxecplU4DGBWkL8q15DX2qrKQ-/edit?gid=736426447"),
            ("FORMAT SK PENETAPAN USER SAKTI", "https://docs.google.com/document/d/1wIl9ZBGJA9LM19kanPAQrXT32Jur4IiS/edit"),
            ("SURAT PERNYATAAN KETERLAMBATAN KONTRAK", "https://docs.google.com/document/d/1UbXeKAuoVbU1TvnU2Ne8npO8dE0VCYHM/edit"),
            ("FORM PERUBAHAN-DATA-KONTRAK", "https://docs.google.com/document/d/1B7r6P7T5oruC9YjpUWd_O75f57Kj8yzC/edit"),
            ("PENAMBAHAN-INFORMASI-PADA-SUPPLIER", "https://docs.google.com/document/d/19Z8T-aBFAa0yVunBVnq5ENPD0_wlBzoF/edit"),
        ],
    },
    "panduan": {
        "title": "Panduan Aplikasi",
        "subtitle": "Petunjuk penggunaan aplikasi INTERMILAN.",
        "items": [],
    },
}

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
    selected_satker = request.GET.get("satker", "").strip()
    selected_month = selected_month if selected_month.isdigit() and 1 <= int(selected_month) <= 12 else "1"
    selected_year_int = int(selected_year) if selected_year.isdigit() else 2026
    selected_month_int = int(selected_month)

    # Scoped querysets
    sp2d_qs = filter_by_satker(SP2DRaw.objects.all(), request.user)
    dk_qs = filter_by_satker(TransactionDetail.objects.all(), request.user)
    drpp_qs = filter_by_satker(DRPPUpload.objects.all(), request.user)

    # Stats cards
    totals = dk_qs.aggregate(nilai_bruto=Sum("nilai_bruto"), nilai_netto=Sum("nilai_netto"))

    # Build summary table
    dashboard_summary_rows = build_dashboard_summary_rows(
        dk_qs,
        tahun=selected_year_int,
        bulan=selected_month_int,
        satker_filter=selected_satker if selected_satker else None,
        user=request.user
    )

    # Count transactions without SP2D for warning
    no_sp2d_count = dk_qs.filter(
        bulan_sp2d=selected_month_int,
        sp2d_raw__isnull=True
    ).count()

    card_scope = build_dashboard_scope(request.user)
    year_options = get_dashboard_year_options()
    satker_options = get_satker_options_for_dashboard(dk_qs, user=request.user)

    # Chart Data
    dashboard_chart = {
        "labels": [],
        "fa16": [],
        "intermilan_bulan": [],
        "intermilan_kumulatif": []
    }
    for row in dashboard_summary_rows:
        dashboard_chart["labels"].append(row["satker_code"])
        dashboard_chart["fa16"].append(row.get("fa16_raw", 0))
        dashboard_chart["intermilan_bulan"].append(row.get("intermilan_bulan_raw", 0))
        dashboard_chart["intermilan_kumulatif"].append(row.get("intermilan_sd_raw", 0))

    context = common_context(request)
    context.update({
        "page_title": "Dashboard INTERMILAN",
        "page_subtitle": "Pantau realizesi, transaksi, dan kelengkapan dokumen per satker.",
        "stats": {
            "sp2d": sp2d_qs.count(),
            "perlu_detail": sp2d_qs.filter(status=SP2DRaw.Status.PERLU_DETAIL).count(),
            "dk": dk_qs.count(),
            "drpp": drpp_qs.count(),
            "nilai_bruto": totals["nilai_bruto"] or 0,
            "nilai_netto": totals["nilai_netto"] or 0,
        },
        "dashboard_filters": {
            "tahun": selected_year,
            "bulan": selected_month,
            "satker": selected_satker
        },
        "year_options": year_options,
        "satker_options": satker_options,
        "months": MONTH_OPTIONS,
        "card_scope_label": card_scope["label"],
        "card_scope_note": card_scope["note"],
        # Summary table data
        "dashboard_summary_rows": dashboard_summary_rows,
        "dashboard_chart": dashboard_chart,
        "no_sp2d_count": no_sp2d_count,
        "dashboard_year": selected_year_int,
        "dashboard_bulan": selected_month_int,
        "dashboard_bulan_label": month_name(selected_month_int),
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
    scoped_summary_qs = filter_by_satker(MonitoringSummary.objects.all(), request.user)
    summary_qs = scoped_summary_qs
    if filters["tahun"].isdigit():
        summary_qs = summary_qs.filter(tahun=int(filters["tahun"]))
    if filters["satker"]:
        summary_qs = summary_qs.filter(satker_code=filters["satker"])
    if filters["bulan"]:
        summary_qs = summary_qs.filter(bulan_number=filters["bulan"])
    if filters["status"]:
        summary_qs = summary_qs.filter(status__iexact=filters["status"])

    summary_available = scoped_summary_qs.exists()
    if summary_available:
        rows = build_monitoring_rows_from_summary(summary_qs)
        if filters["q"]:
            rows = filter_monitoring_rows(rows, filters["q"])
        summary = build_monitoring_summary_cards(rows)
        satker_options = get_monitoring_summary_satker_options(scoped_summary_qs)
        status_options = get_monitoring_summary_status_options(scoped_summary_qs)
        year_options = [
            str(year)
            for year in scoped_summary_qs.values_list("tahun", flat=True)
            .distinct()
            .order_by("tahun")
        ] or ["2026"]
        source_label = "MonitoringSummary"
    else:
        scoped_transactions = filter_by_satker(TransactionDetail.objects.all(), request.user)
        queryset = scoped_transactions
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
        satker_options = get_monitoring_satker_options(scoped_transactions)
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
    active_tab = request.GET.get("tab", "referensi").strip()

    # Referensi tab - list kode akun
    rows = list(MasterAkun.objects.filter(is_active=True).values_list("kode", "nama_akun", "kategori")[:100])
    if not rows:
        rows = MASTER_AKUN_ROWS

    # Transaksi tab - summary per akun (SCOPED by permission)
    transaksi_rows = []
    if active_tab == "transaksi":
        # SCOPED: Only count transactions user has permission to see
        scoped_dk = filter_by_satker(TransactionDetail.objects.all(), request.user)
        master_rows = MasterAkun.objects.filter(is_active=True)
        summaries = {
            item["akun"]: item
            for item in scoped_dk.values("akun").annotate(total=Count("id"), nilai=Sum("nilai_netto"))
        }
        for master in master_rows[:100]:
            summary = summaries.get(master.kode, {})
            transaksi_rows.append({
                "kode": master.kode,
                "nama": master.nama_akun,
                "kategori": master.kategori,
                "total": summary.get("total", 0),
                "nilai": summary.get("nilai", 0) or 0,
                "checklist": 0,
            })
        if not transaksi_rows:
            transaksi_rows = [
                {"kode": kode, "nama": nama, "kategori": kategori, "total": 0, "nilai": 0, "checklist": 0}
                for kode, nama, kategori in MASTER_AKUN_ROWS
            ]

    context = common_context(request)
    context.update({
        "page_title": "Akun Keuangan",
        "page_subtitle": "Kelola referensi kode akun dan transaksi per akun.",
        "rows": rows,
        "transaksi_rows": transaksi_rows,
        "active_tab": active_tab,
    })
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
    return redirect(f"{reverse('dk:transaction_list')}?{query.urlencode()}")


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


def get_monitoring_satker_options(queryset=None):
    queryset = queryset if queryset is not None else TransactionDetail.objects.all()
    return build_satker_options(queryset.exclude(satker_code=""))


def get_monitoring_summary_satker_options(queryset=None):
    queryset = queryset if queryset is not None else MonitoringSummary.objects.all()
    satker_names = get_satker_name_map()
    return [
        {"satker_code": item["satker_code"], "satker_name": satker_names.get(item["satker_code"], item["satker_label"] or "-")}
        for item in queryset.exclude(satker_code="")
        .values("satker_code", "satker_label")
        .distinct()
        .order_by("satker_code")
    ]


def get_monitoring_summary_status_options(queryset=None):
    queryset = queryset if queryset is not None else MonitoringSummary.objects.all()
    return list(
        queryset.exclude(status="")
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
    data = REFERENCE_LINKS.get(kind, {})
    titles = {"peraturan": "Peraturan", "template": "Template", "panduan": "Panduan Aplikasi"}
    context = common_context(request)
    context.update({
        "page_title": data.get("title", titles.get(kind, "Referensi")),
        "page_subtitle": data.get("subtitle", "Referensi pendukung penggunaan INTERMILAN."),
        "kind": kind,
        "reference_items": data.get("items", []),
    })
    return render(request, "core/reference.html", context)


def error_403(request, exception=None):
    return render(request, "403.html", status=403)


def error_404(request, exception=None):
    return render(request, "404.html", status=404)


def error_500(request):
    return render(request, "500.html", status=500)


# =============================================================================
# DASHBOARD SUMMARY FUNCTIONS
# =============================================================================


def build_dashboard_summary_rows(scoped_queryset, tahun, bulan, satker_filter=None, user=None):
    """
    Bangun ringkasan Dashboard per satker-bulan-tahun.

    SEMUA query dibatasi oleh permission scope dari scoped_queryset dan user.

    Args:
        scoped_queryset: TransactionDetail queryset yang sudah discope berdasarkan permission
        tahun: Tahun anggaran (dari sp2d_raw__tahun)
        bulan: Bulan SP2D
        satker_filter: Filter satker opsional
        user: Objek user yang login

    Returns:
        List of dict dengan 12 kolom sesuai template Excel
    """
    main_filter = Q(bulan_sp2d=bulan)
    # Use tanggal_spm__year for year filtering - authoritative source from SPM date.
    # Also include NULL tanggal_spm rows when tahun is specified - these represent
    # real transactions (e.g., satker 1301 has 98 transactions ALL with NULL dates).
    # NULL tanggal_spm rows have created_at/updated_at in the current period.
    if tahun:
        main_filter &= Q(Q(tanggal_spm__year=tahun) | Q(tanggal_spm__isnull=True))

    transaction_satkers = set(
        scoped_queryset.exclude(satker_code="")
        .filter(main_filter)
        .values_list("satker_code", flat=True)
        .distinct()
    )

    fa16_query = MonitoringSummary.objects.filter(bulan_number=bulan)
    if tahun:
        fa16_query = fa16_query.filter(tahun=tahun)

    # 1. BUILD ALLOWED SATKER CODES FROM PERMISSION SCOPE
    allowed_satker_codes = None
    if user and not can_view_all_satker(user):
        # Gunakan 6-digit official satker_code untuk filtering
        official_code = get_user_official_satker_code(user)
        if official_code:
            allowed_satker_codes = {official_code}
            fa16_query = fa16_query.filter(satker_code=official_code)
        else:
            # Mapping gagal - user tidak punya akses
            allowed_satker_codes = set()
            fa16_query = fa16_query.none()

    fa16_satkers = set(
        fa16_query.exclude(satker_code="").values_list("satker_code", flat=True).distinct()
    )

    all_potential_satkers = transaction_satkers | fa16_satkers

    if allowed_satker_codes is not None:
        display_satkers = sorted(all_potential_satkers & allowed_satker_codes)
    else:
        display_satkers = sorted(all_potential_satkers)

    if not display_satkers:
        return []

    if satker_filter:
        if satker_filter not in display_satkers:
            return []
        display_satkers = [satker_filter]

    # 2. QUERY 1: Main aggregates per satker (from scoped_queryset)

    main_stats = {}
    for item in scoped_queryset.filter(main_filter).values("satker_code").annotate(
        total_transaksi=Count("id"),
        intermilan_bulan=Sum("nilai_netto"),
        diarsipkan=Count("id", filter=Q(status_detail=TransactionDetail.StatusDetail.DIARSIPKAN))
    ):
        main_stats[item["satker_code"]] = {
            "total_transaksi": item["total_transaksi"] or 0,
            "intermilan_bulan": item["intermilan_bulan"] or Decimal("0"),
            "diarsipkan": item["diarsipkan"] or 0,
        }

    # 3. QUERY 2: Cumulative per satker (from scoped_queryset)
    # Year filter needed here too - cumulative should only include current year
    # Include NULL tanggal_spm rows (same logic as main_filter)
    cumulative_filter = Q(bulan_sp2d__lte=bulan)
    if tahun:
        cumulative_filter &= Q(Q(tanggal_spm__year=tahun) | Q(tanggal_spm__isnull=True))

    cumulative_stats = {}
    for item in scoped_queryset.filter(cumulative_filter).values("satker_code").annotate(
        nilai=Sum("nilai_netto")
    ):
        cumulative_stats[item["satker_code"]] = item["nilai"] or Decimal("0")

    # 4. QUERY 3: ChecklistStatus SCOPED through transaction_detail_id__in
    scoped_transaction_ids = scoped_queryset.filter(main_filter).values("id")

    checklist_data = {}
    for item in ChecklistStatus.objects.filter(
        transaction_detail_id__in=scoped_transaction_ids,
        wajib=True
    ).values("transaction_detail__satker_code").annotate(
        total=Count("id"),
        ada=Count("id", filter=Q(status="ADA"))
    ):
        checklist_data[item["transaction_detail__satker_code"]] = {
            "total": item["total"] or 0,
            "ada": item["ada"] or 0,
        }

    # 5. QUERY 4: DocumentDriveLink SCOPED through transaction_detail_id__in
    spj_data = {}
    for item in DocumentDriveLink.objects.filter(
        transaction_detail_id__in=scoped_transaction_ids
    ).values("transaction_detail__satker_code").annotate(
        transaksi_spj=Count("transaction_detail", distinct=True)
    ):
        spj_data[item["transaction_detail__satker_code"]] = item["transaksi_spj"] or 0

    # 6. QUERY 5: FA16 SCOPED through allowed_satker_codes
    fa16_data = {}
    if display_satkers:
        ms_query = MonitoringSummary.objects.filter(
            satker_code__in=display_satkers,
            bulan_number=bulan
        )
        if tahun:
            ms_query = ms_query.filter(tahun=tahun)
        for row in ms_query:
            fa16_data[row.satker_code] = row.fa16_bulan_ini

    # 7. WARNING: Transaksi tanpa SP2D SCOPED
    no_sp2d_count = 0
    if tahun and bulan:
        no_sp2d_count = scoped_queryset.filter(
            bulan_sp2d=bulan,
            sp2d_raw__isnull=True
        ).count()

    # 8. BUILD ROWS
    satker_names = get_satker_name_map(display_satkers)
    rows = []

    for satker in display_satkers:
        stat = main_stats.get(satker, {})
        total_transaksi = stat.get("total_transaksi", 0)
        intermilan_bulan = stat.get("intermilan_bulan", Decimal("0"))
        intermilan_sd = cumulative_stats.get(satker, Decimal("0"))
        diarsipkan = stat.get("diarsipkan", 0)
        fa16 = fa16_data.get(satker)

        # Calculate % Kelengkapan using account-family policy
        # Denominator = expected mandatory documents from policy
        # Numerator = ADA rows whose document name MATCHES policy required list
        # Legacy rows not in policy do NOT inflate the numerator
        expected_required = get_expected_checklist_count(satker, tahun, bulan)
        ada_count = get_checklist_ada_by_policy(satker, tahun, bulan) if expected_required > 0 else 0
        if expected_required > 0 and ada_count > 0:
            persen_kelengkapan = percent_safe(ada_count, expected_required)
        elif expected_required > 0:
            persen_kelengkapan = Decimal("0")
        else:
            persen_kelengkapan = None  # No expected documents means N/A

        transaksi_spj = spj_data.get(satker, 0)
        persen_spj = percent_safe(transaksi_spj, total_transaksi)

        persen_arsip = percent_safe(diarsipkan, total_transaksi)

        persen_realisasi = calculate_realisasi_percent_safe(intermilan_bulan, fa16)

        percent_completed = calculate_percent_completed(
            persen_realisasi, persen_kelengkapan, persen_spj, persen_arsip
        )

        rows.append({
            "bps": satker_names.get(satker, f"bps{satker}"),
            "satker_code": satker,
            "bulan": month_name(bulan),
            "bulan_number": bulan,
            "tahun": tahun,
            "fa16": format_id_number(fa16) if fa16 is not None else "—",
            "intermilan_bulan": format_id_number(intermilan_bulan),
            "intermilan_sd": format_id_number(intermilan_sd),
            "persen_realisasi": format_percent_safe(persen_realisasi),
            "persen_kelengkapan": format_percent_safe(persen_kelengkapan),
            "persen_spj": format_percent_safe(persen_spj),
            "persen_arsip": format_percent_safe(persen_arsip),
            "percent_completed": format_percent_safe(percent_completed),
            "fa16_raw": float(fa16) if fa16 is not None else 0,
            "intermilan_bulan_raw": float(intermilan_bulan) if intermilan_bulan is not None else 0,
            "intermilan_sd_raw": float(intermilan_sd) if intermilan_sd is not None else 0,
        })

    return rows


def percent_safe(numerator, denominator):
    """Return Decimal or None for zero denominator."""
    if not denominator:
        return None
    return min(
        (Decimal(numerator or 0) / Decimal(denominator)) * 100,
        Decimal("100")
    ).quantize(Decimal("0.01"))


def calculate_realisasi_percent_safe(intermilan_value, fa16_value):
    """Return percent or None if FA16 is None or zero."""
    if fa16_value is None or fa16_value == 0:
        return None
    return min(
        (Decimal(intermilan_value or 0) / Decimal(fa16_value)) * 100,
        Decimal("100")
    ).quantize(Decimal("0.01"))


def calculate_percent_completed(persen_realisasi, persen_kelengkapan, persen_spj, persen_arsip):
    """
    Calculate % Completed based on Excel formula:

    percent_completed = persen_realisasi * average(persen_kelengkapan, persen_spj, persen_arsip)

    Only include components that are not None.
    Return None if persen_realisasi is None or no components available.
    """
    if persen_realisasi is None:
        return None

    components = []
    for val in [persen_kelengkapan, persen_spj, persen_arsip]:
        if val is not None:
            components.append(val)

    if not components:
        return None

    avg = sum(components) / len(components)
    result = (Decimal(persen_realisasi) * avg) / 100

    return min(result, Decimal("100")).quantize(Decimal("0.01"))


def format_percent_safe(value):
    """Format percentage or return dash for None."""
    if value is None:
        return "—"
    return format_percent_id(value)


def get_expected_checklist_count(satker_code, tahun, bulan):
    """
    Calculate expected mandatory checklist count based on account-family policy.

    Returns the total number of mandatory documents that SHOULD exist for all
    transactions of this satker-month, based on account-family detection.
    """
    from apps.dk.models import TransactionDetail
    transactions = TransactionDetail.objects.filter(satker_code=satker_code, bulan_sp2d=bulan)
    if tahun:
        transactions = transactions.filter(tanggal_spm__year=tahun)
    total_required = 0
    for t in transactions:
        required_docs = get_required_documents(t.akun, t.jenis_spm)
        total_required += len(required_docs)
    return total_required


def _normalize_doc_name(name):
    """Normalize document name for policy matching."""
    import re
    text = str(name or "").upper().replace("_", " ")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return " ".join(text.split())


def _doc_matches_policy(doc_name, policy_docs):
    """Check if a document name matches any policy document (substring/exact match)."""
    normalized = _normalize_doc_name(doc_name)
    for policy_doc in policy_docs:
        pol_norm = _normalize_doc_name(policy_doc)
        if (normalized in pol_norm or pol_norm in normalized or
            normalized == pol_norm or
            # Handle KW/Kuitansi variations
            ('KUITANSI' in normalized and 'KUITANSI' in pol_norm) or
            ('KW' in normalized and 'KUITANSI' in pol_norm) or
            ('KUITANSI' in normalized and 'KW' in pol_norm)):
            return True
    return False


def get_checklist_ada_by_policy(satker_code, tahun, bulan):
    """
    Count ADA checklist rows that match policy-required documents.

    Returns dict: {satker_code: ada_count}
    Only counts ADA rows whose document name matches the policy's required list.
    """
    from apps.dk.models import TransactionDetail
    transactions = TransactionDetail.objects.filter(satker_code=satker_code, bulan_sp2d=bulan)
    if tahun:
        transactions = transactions.filter(
            Q(tanggal_spm__year=tahun) | Q(tanggal_spm__isnull=True)
        )

    ada_count = 0
    for t in transactions:
        required_docs = get_required_documents(t.akun, t.jenis_spm)
        for cs in t.checklist_statuses.filter(status="ADA"):
            if _doc_matches_policy(cs.nama_dokumen, required_docs):
                ada_count += 1
    return ada_count


def get_satker_options_for_dashboard(scoped_queryset, user=None):
    """Get satker options from scoped queryset (permission aware)."""
    satkers = set()

    # From scoped_queryset (already permission filtered)
    for item in scoped_queryset.exclude(satker_code="").values("satker_code").distinct():
        if item["satker_code"]:
            satkers.add((item["satker_code"], f"bps{item['satker_code']}"))

    # Include FA16 satkers
    fa16_query = MonitoringSummary.objects.all()
    if user and not can_view_all_satker(user):
        # Gunakan 6-digit official satker_code untuk filtering
        official_code = get_user_official_satker_code(user)
        if official_code:
            fa16_query = fa16_query.filter(satker_code=official_code)
        else:
            fa16_query = fa16_query.none()

    for item in fa16_query.exclude(satker_code="").values("satker_code", "satker_label").distinct():
        if item["satker_code"]:
            satkers.add((item["satker_code"], item["satker_label"] or f"bps{item['satker_code']}"))

    satker_names = get_satker_name_map([s[0] for s in satkers])
    return sorted([
        {
            "satker_code": code,
            "satker_name": satker_names.get(code, label)
        }
        for code, label in satkers
    ], key=lambda x: x["satker_code"])
