"""Diagnostic: cek DB backend aktif dan MonitoringSummary count."""
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "intermilan_project.settings.development")
sys.path.insert(0, ".")
import django
django.setup()

from django.db import connection
from apps.core.models import MonitoringSummary
from apps.dk.models import TransactionDetail

print("=== DIAGNOSTIC DB AKTIF ===")
print(f"DB backend  : {connection.vendor}")
db_name = connection.settings_dict.get("NAME", "-")
db_host = connection.settings_dict.get("HOST", "-")
db_port = connection.settings_dict.get("PORT", "-")
db_user = connection.settings_dict.get("USER", "-")
print(f"DB name     : {db_name}")
print(f"DB host     : {db_host}")
print(f"DB port     : {db_port}")
print(f"DB user     : {db_user}")
print()
ms_count = MonitoringSummary.objects.count()
dk_count = TransactionDetail.objects.count()
print(f"MonitoringSummary count : {ms_count}")
print(f"D_K (TransactionDetail) : {dk_count}")
print()
if connection.vendor == "postgresql":
    print("STATUS: PostgreSQL AKTIF => Lanjut ke repair.")
else:
    print("STATUS: BUKAN PostgreSQL => STOP. Set env DATABASE_ENGINE=postgres sebelum lanjut.")
    print()
    print("Command PowerShell untuk aktifkan PostgreSQL:")
    print('  $env:DATABASE_ENGINE = "postgres"')
    print('  $env:DATABASE_NAME   = "intermilan_rehearsal"')
    print('  $env:DATABASE_USER   = "intermilan_user"')
    print('  $env:DATABASE_HOST   = "127.0.0.1"')
    print('  $env:DATABASE_PORT   = "5432"')
    print("  Kemudian jalankan ulang script/manage.py dalam sesi PowerShell yang sama.")
