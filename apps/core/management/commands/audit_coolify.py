import os
from django.core.management.base import BaseCommand
from django.conf import settings
from apps.core.models import TransactionPackage
from apps.sp2d.models import SP2DRaw
from apps.dk.models import TransactionDetail
from apps.drpp.models import DRPPUpload
from apps.paket_spm.models import PaketSPMUpload
from apps.documents.models import DocumentUpload

try:
    from apps.core.models import ActiveParentSession
except ImportError:
    ActiveParentSession = None

class Command(BaseCommand):
    help = 'Audit database production Coolify menggunakan runtime environment variables'

    def handle(self, *args, **options):
        self.stdout.write("=== 1. Identifikasi DATABASE_URL runtime production dari container ===")
        
        # 1. Jangan membuat asumsi dari file .env lokal.
        # 2. Gunakan environment variable runtime container production.
        db_url = os.environ.get("DATABASE_URL")
        db_host = "Unknown"
        db_name = "Unknown"
        conn_target = "Unknown"

        if db_url:
            from urllib.parse import urlparse
            parsed = urlparse(db_url)
            db_host = parsed.hostname
            db_name = parsed.path.lstrip("/")
            conn_target = "Remote Coolify PostgreSQL"
            self.stdout.write(f"DATABASE_URL: Terdeteksi di runtime")
        else:
            db_host = settings.DATABASES['default'].get('HOST', 'Unknown')
            db_name = settings.DATABASES['default'].get('NAME', 'Unknown')
            conn_target = "Django Settings (Fallback)"
            self.stdout.write(f"DATABASE_URL: TIDAK terdeteksi (menggunakan fallback settings)")

        self.stdout.write(f"- Database host: {db_host}")
        self.stdout.write(f"- Database name: {db_name}")
        self.stdout.write(f"- Connection target: {conn_target}")
        self.stdout.write(f"- Environment Django aktif: {os.environ.get('DJANGO_SETTINGS_MODULE', 'Tidak diset')}")

        self.stdout.write("\n=== 2. Jalankan audit menggunakan database production ===")
        
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
            self.stdout.write(f"{model}.objects.count() = {count}")

        self.stdout.write("\n=== 3. Detail Data Transaksi ===")
        total_data = sum(counts.values())
        
        if total_data > 0:
            for s in SP2DRaw.objects.all()[:5]:
                self.stdout.write(f"- model: SP2DRaw | id: {s.id} | satker_code: {s.satker_code} | nomor_sp2d: {s.nomor_sp2d} | nomor_spm: {s.nomor_spm} | created_at: {getattr(s, 'created_at', '')}")

            for td in TransactionDetail.objects.all()[:5]:
                self.stdout.write(f"- model: TransactionDetail | id: {td.id} | satker_code: {getattr(td, 'satker_code', '')} | nomor_sp2d: {getattr(td, 'sp2d', getattr(td, 'nomor_sp2d', ''))} | nomor_spm: {getattr(td, 'nomor_spm', '')} | created_at: {getattr(td, 'created_at', '')}")

            for d in DRPPUpload.objects.all()[:5]:
                self.stdout.write(f"- model: DRPPUpload | id: {d.id} | created_at: {getattr(d, 'created_at', '')}")
                
            for p in PaketSPMUpload.objects.all()[:5]:
                self.stdout.write(f"- model: PaketSPMUpload | id: {p.id} | created_at: {getattr(p, 'created_at', '')}")
                
            for tp in TransactionPackage.objects.all()[:5]:
                self.stdout.write(f"- model: TransactionPackage | id: {tp.id} | satker_code: {getattr(tp, 'satker_code', '')} | nomor_spm: {getattr(tp, 'nomor_spm', '')} | created_at: {getattr(tp, 'created_at', '')}")
        else:
            self.stdout.write("Tidak ada data transaksi (Bersih)")

        self.stdout.write("\n=== LAPORAN AKHIR ===")
        self.stdout.write(f"DATABASE YANG DIAUDIT = {db_name} (Host: {db_host})")
        self.stdout.write(f"HASIL COUNT = {total_data} total record transaksi")
        
        is_live = "YA" if db_url and db_host not in ["127.0.0.1", "localhost"] else "TIDAK (Sepertinya ini database lokal/fallback)"
        self.stdout.write(f"APAKAH SAMA DENGAN DATABASE LIVE? = {is_live}")
