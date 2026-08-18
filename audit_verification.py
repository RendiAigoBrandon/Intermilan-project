import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'intermilan_project.settings.production')
django.setup()

from django.conf import settings
from django.db import connection
from apps.core.models import SatkerMaster, ActiveParentSession, TransactionPackage
from apps.accounts.models import Profile
from django.contrib.auth import get_user_model
User = get_user_model()
from apps.sp2d.models import SP2DRaw
from apps.dk.models import TransactionDetail, MasterAkun
from apps.drpp.models import DRPPUpload
from apps.paket_spm.models import PaketSPMUpload
from apps.documents.models import DocumentUpload

print("=== 1. Identifikasi Environment Aktif ===")
print(f"DJANGO_SETTINGS_MODULE: {os.environ.get('DJANGO_SETTINGS_MODULE')}")
print(f"DATABASE_ENGINE: {settings.DATABASES['default']['ENGINE']}")
print(f"DATABASE_NAME: {settings.DATABASES['default']['NAME']}")
print(f"DATABASE_HOST: {settings.DATABASES['default']['HOST']}")
print(f"DATABASE_PORT: {settings.DATABASES['default']['PORT']}")
print(f"DATABASE_USER: {settings.DATABASES['default']['USER']}")

print("\n=== 2. Audit Jumlah Data Seluruh Transaksi ===")
print(f"SP2DRaw: {SP2DRaw.objects.count()}")
print(f"TransactionDetail: {TransactionDetail.objects.count()}")
print(f"DRPPUpload: {DRPPUpload.objects.count()}")
print(f"PaketSPMUpload: {PaketSPMUpload.objects.count()}")
print(f"DocumentUpload: {DocumentUpload.objects.count()}")
print(f"TransactionPackage: {TransactionPackage.objects.count()}")
print(f"ActiveParentSession: {ActiveParentSession.objects.count()}")

print("\n=== 3. Pastikan Master Data TIDAK Terhapus ===")
print(f"SatkerMaster: {SatkerMaster.objects.count()}")
print(f"MasterAkun: {MasterAkun.objects.count()}")
print(f"User: {User.objects.count()}")
print(f"Profile: {Profile.objects.count()}")

print("\n=== 4. Detail Data Transaksi (Jika Ada) ===")
# SP2DRaw
sp2ds = SP2DRaw.objects.all()[:10]
if sp2ds:
    print("SP2DRaw data:")
    for s in sp2ds:
        print(f"  ID: {s.id}, Model: SP2DRaw, satker_code: {s.satker_code}, nama_satker: {getattr(s, 'satker_name', '')}, nomor SP2D: {s.nomor_sp2d}, nomor SPM: {s.nomor_spm}, created_at: {getattr(s, 'created_at', '')}")

# TransactionDetail
tds = TransactionDetail.objects.all()[:10]
if tds:
    print("TransactionDetail data:")
    for td in tds:
        print(f"  ID: {td.id}, Model: TransactionDetail, satker_code: {getattr(td, 'satker', '')}, nama_satker: {getattr(getattr(td, 'satker', ''), 'nama_satker', '') if hasattr(td, 'satker') and hasattr(getattr(td, 'satker', ''), 'nama_satker') else ''}, nomor SP2D: {getattr(td, 'sp2d', '')}, nomor SPM: {getattr(td, 'spm', '')}, created_at: {getattr(td, 'created_at', '')}")

# DRPPUpload
drpps = DRPPUpload.objects.all()[:10]
if drpps:
    print("DRPPUpload data:")
    for d in drpps:
        print(f"  ID: {d.id}, Model: DRPPUpload, created_at: {getattr(d, 'created_at', '')}")

# PaketSPMUpload
pakets = PaketSPMUpload.objects.all()[:10]
if pakets:
    print("PaketSPMUpload data:")
    for p in pakets:
        print(f"  ID: {p.id}, Model: PaketSPMUpload, created_at: {getattr(p, 'created_at', '')}")

# DocumentUpload
docs = DocumentUpload.objects.all()[:10]
if docs:
    print("DocumentUpload data:")
    for d in docs:
        print(f"  ID: {d.id}, Model: DocumentUpload, created_at: {getattr(d, 'created_at', '')}")

# TransactionPackage
tps = TransactionPackage.objects.all()[:10]
if tps:
    print("TransactionPackage data:")
    for t in tps:
        print(f"  ID: {t.id}, Model: TransactionPackage, created_at: {getattr(t, 'created_at', '')}")

# ActiveParentSession
aps = ActiveParentSession.objects.all()[:10]
if aps:
    print("ActiveParentSession data:")
    for a in aps:
        print(f"  ID: {a.id}, Model: ActiveParentSession, created_at: {getattr(a, 'created_at', '')}")

