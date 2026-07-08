"""Diagnostic script: cek MonitoringSummary di SQLite aktif."""
import os
import sys
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "intermilan_project.settings.development")
sys.path.insert(0, ".")
django.setup()

from django.db import connection
from apps.core.models import MonitoringSummary

print("=== DIAGNOSTIC MonitoringSummary ===")
print(f"DB backend : {connection.vendor}")
db_name = connection.settings_dict.get("NAME", "-")
print(f"DB name    : {db_name}")
total = MonitoringSummary.objects.count()
print(f"Total count: {total}")
mei2026 = MonitoringSummary.objects.filter(tahun=2026, bulan_number=5).count()
print(f"Mei 2026   : {mei2026}")
print()

years = list(MonitoringSummary.objects.values_list("tahun", flat=True).distinct().order_by("tahun"))
bulans = list(MonitoringSummary.objects.values_list("bulan_number", flat=True).distinct().order_by("bulan_number"))
satkers = list(MonitoringSummary.objects.values_list("satker_code", flat=True).distinct().order_by("satker_code"))
print(f"Distinct tahun  : {years}")
print(f"Distinct bulan  : {bulans}")
print(f"Distinct satker : {satkers}")
print()

target_satkers = ["1300", "1301", "1302", "1303", "1304"]
qs = MonitoringSummary.objects.filter(
    tahun=2026, bulan_number=5, satker_code__in=target_satkers
).order_by("satker_code")

if qs.exists():
    print("=== SAMPLE MEI 2026 (bps1300-bps1304) ===")
    for row in qs:
        print(f"\n  satker_code             = {row.satker_code}")
        print(f"  satker_label            = {row.satker_label}")
        print(f"  bulan                   = {row.bulan}")
        print(f"  bulan_number            = {row.bulan_number}")
        print(f"  tahun                   = {row.tahun}")
        print(f"  fa16_bulan_ini          = {row.fa16_bulan_ini}")
        print(f"  intermilan_bulan_ini    = {row.intermilan_bulan_ini}")
        print(f"  intermilan_sd_bulan_ini = {row.intermilan_sd_bulan_ini}")
        print(f"  persen_realisasi        = {row.persen_realisasi}")
        print(f"  persen_kelengkapan_dok  = {row.persen_kelengkapan_dokumen}")
        print(f"  persen_spj_upload       = {row.persen_spj_upload}")
        print(f"  persen_arsip            = {row.persen_arsip}")
        print(f"  percent_completed       = {row.percent_completed}")
        print(f"  source                  = {row.source}")
else:
    print("KOSONG: Tidak ada data MonitoringSummary Mei 2026 untuk satker 1300-1304.")
    print(f"=> Dashboard akan FALLBACK ke D_K. Total MonitoringSummary = {total}")
