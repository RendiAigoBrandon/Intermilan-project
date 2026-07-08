"""Post-repair diagnostic: validasi MonitoringSummary PostgreSQL Mei 2026."""
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "intermilan_project.settings.development")
sys.path.insert(0, ".")
import django
django.setup()

from django.db import connection
from apps.core.models import MonitoringSummary

print("=== POST-REPAIR DIAGNOSTIC ===")
print(f"DB backend  : {connection.vendor}")
print(f"DB name     : {connection.settings_dict.get('NAME', '-')}")
print(f"Total count : {MonitoringSummary.objects.count()}")
print(f"Mei 2026    : {MonitoringSummary.objects.filter(tahun=2026, bulan_number=5).count()}")
print()

target_satkers = ["1300", "1301", "1302", "1303", "1304"]
qs = MonitoringSummary.objects.filter(
    tahun=2026, bulan_number=5, satker_code__in=target_satkers
).order_by("satker_code")

print("=== SAMPLE MEI 2026 (bps1300-bps1304) SETELAH REPAIR ===")
for row in qs:
    sd_ok = "!!! MASIH 0 !!!" if row.intermilan_sd_bulan_ini == 0 and row.intermilan_bulan_ini > 0 else "OK"
    print(f"\n  satker_code             = {row.satker_code} ({row.satker_label})")
    print(f"  bulan                   = {row.bulan} ({row.bulan_number}) tahun={row.tahun}")
    print(f"  fa16_bulan_ini          = {row.fa16_bulan_ini:,.2f}")
    print(f"  intermilan_bulan_ini    = {row.intermilan_bulan_ini:,.2f}")
    print(f"  intermilan_sd_bulan_ini = {row.intermilan_sd_bulan_ini:,.2f}  [{sd_ok}]")
    print(f"  persen_realisasi        = {row.persen_realisasi}")
    print(f"  persen_kelengkapan_dok  = {row.persen_kelengkapan_dokumen}")
    print(f"  persen_spj_upload       = {row.persen_spj_upload}")
    print(f"  persen_arsip            = {row.persen_arsip}")
    print(f"  percent_completed       = {row.percent_completed}")
    print(f"  source                  = {row.source}")

print()
print("=== KUMULATIF BARIS bps1303 SEMUA BULAN 2026 ===")
rows_1303 = MonitoringSummary.objects.filter(
    tahun=2026, satker_code="1303"
).order_by("bulan_number")
for r in rows_1303:
    print(f"  bulan={r.bulan_number:02d} ({r.bulan:<10}) "
          f"bulan_ini={r.intermilan_bulan_ini:>16,.2f}  "
          f"sd={r.intermilan_sd_bulan_ini:>16,.2f}")
