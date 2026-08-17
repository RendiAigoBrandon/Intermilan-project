import os
import sys
import json
import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from django.conf import settings
from urllib.parse import urlparse

# Import transaction models to clean
from apps.sp2d.models import SP2DRaw
from apps.dk.models import TransactionDetail
from apps.drpp.models import DRPPUpload
from apps.paket_spm.models import PaketSPMUpload
from apps.core.models import TransactionPackage
from apps.documents.models import DocumentUpload, DocumentDriveLink

try:
    from apps.core.models import ActiveParentSession
except ImportError:
    ActiveParentSession = None

class Command(BaseCommand):
    help = 'Clean all transaction test data on production database. REQUIRES --confirm-delete flag.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm-delete',
            action='store_true',
            help='Wajib digunakan untuk benar-benar menghapus data dari database',
        )

    def get_counts(self):
        counts = {
            "SP2DRaw": SP2DRaw.objects.count(),
            "TransactionDetail": TransactionDetail.objects.count(),
            "DRPPUpload": DRPPUpload.objects.count(),
            "PaketSPMUpload": PaketSPMUpload.objects.count(),
            "TransactionPackage": TransactionPackage.objects.count(),
            "DocumentUpload": DocumentUpload.objects.count(),
            "DocumentDriveLink": DocumentDriveLink.objects.count(),
        }
        if ActiveParentSession:
            counts["ActiveParentSession"] = ActiveParentSession.objects.count()
        return counts

    def handle(self, *args, **options):
        confirm = options['confirm-delete']
        
        # 1. CETAK ENV DAN CEK PRODUCTION
        db_url = os.environ.get("DATABASE_URL")
        db_host = "Unknown"
        db_name = "Unknown"
        if db_url:
            parsed = urlparse(db_url)
            db_host = parsed.hostname
            db_name = parsed.path.lstrip("/")
        else:
            db_host = settings.DATABASES['default'].get('HOST', 'Unknown')
            db_name = settings.DATABASES['default'].get('NAME', 'Unknown')
            
        django_settings = os.environ.get("DJANGO_SETTINGS_MODULE", "Unknown")

        self.stdout.write(self.style.WARNING("=== IDENTIFIKASI LINGKUNGAN DATABASE ==="))
        self.stdout.write(f"DATABASE HOST: {db_host}")
        self.stdout.write(f"DATABASE NAME: {db_name}")
        self.stdout.write(f"DJANGO SETTINGS: {django_settings}")

        # 2. JIKA BUKAN PRODUCTION, BATALKAN
        if "production" not in django_settings.lower():
            self.stdout.write(self.style.ERROR("\n[ABORTED] Proses dibatalkan. Ini BUKAN environment production!"))
            return
            
        self.stdout.write(self.style.SUCCESS("\n[OK] Environment Production Dikonfirmasi."))
        
        self.stdout.write(self.style.WARNING("\n=== PREVIEW JUMLAH DATA SEBELUM HAPUS ==="))
        
        counts_before = self.get_counts()
        total_data = sum(counts_before.values())
        
        for model_name, count in counts_before.items():
            self.stdout.write(f"{model_name} memiliki: {count} record")
            
        if total_data == 0:
            self.stdout.write(self.style.SUCCESS("\n[BERSIH] Tidak ada data transaksi yang perlu dihapus. Database sudah kosong dari data test."))
            return

        self.stdout.write(self.style.WARNING("\n=== DETAIL DATA YANG AKAN DIHAPUS (HANYA TRANSAKSI) ==="))
        self.stdout.write("Catatan: User, Profile, SatkerMaster, MasterAkun, dsb TIDAK AKAN DIHAPUS.\n")
        
        if counts_before["SP2DRaw"] > 0:
            for s in SP2DRaw.objects.all()[:5]:
                self.stdout.write(f"- SP2DRaw | id: {s.id} | satker_code: {getattr(s, 'satker_code', 'N/A')}")
        
        if counts_before["TransactionDetail"] > 0:
            for td in TransactionDetail.objects.all()[:5]:
                self.stdout.write(f"- TransactionDetail | id: {td.id} | satker_code: {getattr(td, 'satker_code', 'N/A')}")
                
        if counts_before["TransactionPackage"] > 0:
            for tp in TransactionPackage.objects.all()[:5]:
                self.stdout.write(f"- TransactionPackage | id: {tp.id} | satker_code: {getattr(tp, 'satker_code', 'N/A')}")
                
        if counts_before["DRPPUpload"] > 0:
            for d in DRPPUpload.objects.all()[:5]:
                self.stdout.write(f"- DRPPUpload | id: {d.id} | satker_code: {getattr(d, 'satker_code', 'N/A')}")
                
        if counts_before["PaketSPMUpload"] > 0:
            for p in PaketSPMUpload.objects.all()[:5]:
                self.stdout.write(f"- PaketSPMUpload | id: {p.id} | satker_code: {getattr(p, 'satker_code', 'N/A')}")
                
        if counts_before["DocumentUpload"] > 0:
            for doc in DocumentUpload.objects.all()[:5]:
                self.stdout.write(f"- DocumentUpload | id: {doc.id} | satker_code: {getattr(doc, 'satker_code', 'N/A')}")

        self.stdout.write(self.style.WARNING(f"\nTotal record transaksi yang akan dihapus: {total_data}"))

        if not confirm:
            self.stdout.write(self.style.ERROR("\n[ABORTED] Operasi dibatalkan karena tidak ada flag --confirm-delete."))
            self.stdout.write("Jalankan: python manage.py clean_test_data --confirm-delete")
            return

        # 6. KONFIRMASI KEDUA SETELAH PREVIEW (Input manual interaktif)
        self.stdout.write(self.style.ERROR("\nPERINGATAN: ANDA BERADA DI PRODUCTION DATABASE!"))
        konfirmasi = input(f"Ketik 'LANJUT' untuk menghapus {total_data} record di atas secara permanen: ")
        
        if konfirmasi != 'LANJUT':
            self.stdout.write(self.style.ERROR("\n[ABORTED] Kata kunci salah. Operasi dibatalkan."))
            return

        self.stdout.write(self.style.WARNING("\n[!] PROSES PENGHAPUSAN DIMULAI [!]"))
        
        # Ekspor metadata sebelum dihapus
        timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        report_filename = f"cleaning_report_{timestamp_str}.json"
        
        self.stdout.write(f"Menyimpan audit trail ke {report_filename}...")
        
        audit_trail = {
            "timestamp": datetime.datetime.now().isoformat(),
            "database_host": db_host,
            "database_name": db_name,
            "deleted_records": {}
        }
        
        def extract_meta(qs):
            return [{"id": obj.id, "satker_code": getattr(obj, 'satker_code', 'N/A')} for obj in qs]
            
        audit_trail["deleted_records"]["TransactionDetail"] = extract_meta(TransactionDetail.objects.all())
        audit_trail["deleted_records"]["TransactionPackage"] = extract_meta(TransactionPackage.objects.all())
        if ActiveParentSession:
            audit_trail["deleted_records"]["ActiveParentSession"] = extract_meta(ActiveParentSession.objects.all())
        audit_trail["deleted_records"]["SP2DRaw"] = extract_meta(SP2DRaw.objects.all())
        audit_trail["deleted_records"]["DRPPUpload"] = extract_meta(DRPPUpload.objects.all())
        audit_trail["deleted_records"]["PaketSPMUpload"] = extract_meta(PaketSPMUpload.objects.all())
        audit_trail["deleted_records"]["DocumentUpload"] = extract_meta(DocumentUpload.objects.all())
        audit_trail["deleted_records"]["DocumentDriveLink"] = extract_meta(DocumentDriveLink.objects.all())
        
        try:
            with open(report_filename, 'w') as f:
                json.dump(audit_trail, f, indent=2)
            self.stdout.write(self.style.SUCCESS(f"[OK] Audit trail berhasil disimpan di {report_filename}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[ERROR] Gagal menyimpan audit trail: {e}"))
            self.stdout.write("Membatalkan proses agar aman.")
            return

        try:
            with transaction.atomic():
                # Hapus hanya tabel transaksi.
                TransactionDetail.objects.all().delete()
                TransactionPackage.objects.all().delete()
                if ActiveParentSession:
                    ActiveParentSession.objects.all().delete()
                SP2DRaw.objects.all().delete()
                DRPPUpload.objects.all().delete()
                PaketSPMUpload.objects.all().delete()
                DocumentUpload.objects.all().delete()
                DocumentDriveLink.objects.all().delete()
                
            self.stdout.write(self.style.SUCCESS("\n[SUCCESS] Penghapusan data transaksi berhasil dilakukan!"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"\n[ERROR] Terjadi kesalahan saat menghapus data: {e}"))
            return

        # 7. LAPORAN CLEANING RESULT
        self.stdout.write(self.style.WARNING("\n=== CLEANING RESULT (VERIFIKASI AKHIR) ==="))
        counts_after = self.get_counts()
        for model_name, count in counts_after.items():
            if count == 0:
                self.stdout.write(self.style.SUCCESS(f"{model_name} = {count} (BERSIH)"))
            else:
                self.stdout.write(self.style.ERROR(f"{model_name} = {count} (MASIH TERSISA)"))
                
        if sum(counts_after.values()) == 0:
            self.stdout.write(self.style.SUCCESS("\n======================================================="))
            self.stdout.write(self.style.SUCCESS("  PRODUCTION DATABASE CLEAN - SIAP MENERIMA DATA BARU  "))
            self.stdout.write(self.style.SUCCESS("======================================================="))
        else:
            self.stdout.write(self.style.ERROR("\n[WARNING] Masih ada data transaksi yang tersisa. Harap cek manual."))
