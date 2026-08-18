import os
import django
from urllib.parse import urlparse

# Set Django settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'intermilan_project.settings.production')
django.setup()

from django.conf import settings
from apps.core.models import TransactionPackage
from apps.sp2d.models import SP2DRaw
from apps.dk.models import TransactionDetail
from apps.drpp.models import DRPPUpload
from apps.paket_spm.models import PaketSPMUpload
from apps.documents.models import DocumentUpload

# Attempt to load ActiveParentSession (it's in core.models based on previous audit)
try:
    from apps.core.models import ActiveParentSession
except ImportError:
    ActiveParentSession = None

def main():
    print("=== 1. Identifikasi DATABASE_URL runtime production dari container ===")
    
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        parsed_url = urlparse(db_url)
        db_host = parsed_url.hostname
        db_name = parsed_url.path.lstrip("/")
        conn_target = "Remote PostgreSQL Container"
        
        print(f"DATABASE_URL Terdeteksi: Ya")
        print(f"Database Host: {db_host}")
        print(f"Database Name: {db_name}")
        print(f"Connection Target: {conn_target}")
    else:
        # Fallback if no DATABASE_URL environment variable is found (e.g. using specific DB vars)
        db_host = settings.DATABASES['default'].get('HOST', '127.0.0.1')
        db_name = settings.DATABASES['default'].get('NAME')
        conn_target = "Local/Fallback PostgreSQL Configuration"
        
        print(f"DATABASE_URL Terdeteksi: TIDAK (Menggunakan individual env vars)")
        print(f"Database Host: {db_host}")
        print(f"Database Name: {db_name}")
        print(f"Connection Target: {conn_target}")

    print("\n=== 2. Jalankan audit menggunakan database production ===")
    counts = {
        "SP2DRaw": SP2DRaw.objects.count(),
        "TransactionDetail": TransactionDetail.objects.count(),
        "DRPPUpload": DRPPUpload.objects.count(),
        "PaketSPMUpload": PaketSPMUpload.objects.count(),
        "DocumentUpload": DocumentUpload.objects.count(),
        "TransactionPackage": TransactionPackage.objects.count(),
        "ActiveParentSession": ActiveParentSession.objects.count() if ActiveParentSession else 0,
    }

    for model, count in counts.items():
        print(f"{model}: {count}")

    print("\n=== 3. Detail Data Transaksi (Jika Masih Ada) ===")
    has_data = False
    
    if counts["SP2DRaw"] > 0:
        has_data = True
        print("\nTable: SP2DRaw")
        for s in SP2DRaw.objects.all()[:10]:
            print(f"  - ID: {s.id}, satker_code: {s.satker_code}, nomor_sp2d: {s.nomor_sp2d}, nomor_spm: {s.nomor_spm}, created_at: {getattr(s, 'created_at', '')}")

    if counts["TransactionDetail"] > 0:
        has_data = True
        print("\nTable: TransactionDetail")
        for td in TransactionDetail.objects.all()[:10]:
            print(f"  - ID: {td.id}, satker_code: {getattr(td, 'satker_code', '')}, nomor_sp2d: {getattr(td, 'sp2d', getattr(td, 'nomor_sp2d', ''))}, nomor_spm: {getattr(td, 'nomor_spm', '')}, created_at: {getattr(td, 'created_at', '')}")

    if counts["TransactionPackage"] > 0:
        has_data = True
        print("\nTable: TransactionPackage")
        for tp in TransactionPackage.objects.all()[:10]:
            print(f"  - ID: {tp.id}, satker_code: {getattr(tp, 'satker_code', '')}, nomor_spm: {getattr(tp, 'nomor_spm', '')}, created_at: {getattr(tp, 'created_at', '')}")

    if not has_data:
        print("TIDAK ADA DATA TRANSAKSI (BERSIH)")

    print("\n=== 4. Laporan Akhir ===")
    print(f"DATABASE YANG DIAUDIT = {db_name} di {db_host}")
    total_tx = sum(counts.values())
    print(f"HASIL COUNT = {total_tx} total record transaksi")
    
    if db_url and db_host != "127.0.0.1" and db_host != "localhost":
        print("APAKAH SAMA DENGAN DATABASE LIVE? = KEMUNGKINAN BESAR YA (Koneksi menggunakan remote DATABASE_URL)")
    else:
        print("APAKAH SAMA DENGAN DATABASE LIVE? = KEMUNGKINAN TIDAK (Koneksi menggunakan database lokal)")

if __name__ == "__main__":
    main()
