from django.shortcuts import render
from django.contrib.auth.decorators import user_passes_test
from django.db import transaction
from django.contrib import messages
import logging
import os
from django.conf import settings
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

from apps.sp2d.models import SP2DRaw
from apps.dk.models import TransactionDetail
from apps.drpp.models import DRPPUpload
from apps.paket_spm.models import PaketSPMUpload
from apps.core.models import TransactionPackage
from apps.documents.models import DocumentUpload, DocumentDriveLink
from apps.accounts.access import is_admin

try:
    from apps.core.models import ActiveParentSession
except ImportError:
    ActiveParentSession = None

def get_counts():
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

def get_previews():
    previews = {}
    
    previews['SP2DRaw'] = [{"id": s.id, "satker": getattr(s, 'satker_code', 'N/A')} for s in SP2DRaw.objects.all()[:5]]
    previews['TransactionDetail'] = [{"id": s.id, "satker": getattr(s, 'satker_code', 'N/A')} for s in TransactionDetail.objects.all()[:5]]
    previews['DRPPUpload'] = [{"id": s.id, "satker": getattr(s, 'satker_code', 'N/A')} for s in DRPPUpload.objects.all()[:5]]
    previews['PaketSPMUpload'] = [{"id": s.id, "satker": getattr(s, 'satker_code', 'N/A')} for s in PaketSPMUpload.objects.all()[:5]]
    previews['TransactionPackage'] = [{"id": s.id, "satker": getattr(s, 'satker_code', 'N/A')} for s in TransactionPackage.objects.all()[:5]]
    previews['DocumentUpload'] = [{"id": s.id, "satker": getattr(s, 'satker_code', 'N/A')} for s in DocumentUpload.objects.all()[:5]]
    previews['DocumentDriveLink'] = [{"id": s.id, "satker": getattr(s, 'satker_code', 'N/A')} for s in DocumentDriveLink.objects.all()[:5]]
    
    if ActiveParentSession:
        previews['ActiveParentSession'] = [{"id": s.id, "satker": "N/A"} for s in ActiveParentSession.objects.all()[:5]]
    else:
        previews['ActiveParentSession'] = []
        
    return previews

@user_passes_test(is_admin, login_url='/accounts/login/')
def clean_test_data_view(request):
    counts_before = get_counts()
    total_before = sum(counts_before.values())
    previews = get_previews()
    
    context = {
        'counts_before': counts_before,
        'total_before': total_before,
        'previews': previews,
        'status': None,
        'counts_after': None,
        'total_after': None,
    }

    if request.method == 'POST':
        confirmation = request.POST.get('confirmation', '')
        if confirmation == 'LANJUT':
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
                
            logger.warning("=== EXECUTING PRODUCTION CLEANUP ===")
            logger.warning(f"DATABASE HOST: {db_host}")
            logger.warning(f"DATABASE NAME: {db_name}")
            logger.warning(f"Total data before delete: {total_before}")
            
            try:
                with transaction.atomic():
                    TransactionDetail.objects.all().delete()
                    TransactionPackage.objects.all().delete()
                    if ActiveParentSession:
                        ActiveParentSession.objects.all().delete()
                    SP2DRaw.objects.all().delete()
                    DRPPUpload.objects.all().delete()
                    PaketSPMUpload.objects.all().delete()
                    DocumentUpload.objects.all().delete()
                    DocumentDriveLink.objects.all().delete()
                
                context['status'] = 'CLEAN SUCCESS'
                messages.success(request, "Penghapusan data transaksi berhasil dilakukan!")
            except Exception as e:
                context['status'] = 'ERROR'
                messages.error(request, f"Terjadi kesalahan: {e}")
                
            counts_after = get_counts()
            context['counts_after'] = counts_after
            context['total_after'] = sum(counts_after.values())
        else:
            messages.error(request, "Kata kunci konfirmasi salah. Penghapusan dibatalkan.")
            
    return render(request, 'maintenance/clean_test_data.html', context)

from django.core.management import call_command
from apps.dk.models import MasterAkun

@user_passes_test(is_admin, login_url='/accounts/login/')
def seed_master_akun_view(request):
    counts_before = MasterAkun.objects.count()
    status = None
    counts_after = None

    if request.method == 'POST':
        try:
            with transaction.atomic():
                call_command('loaddata', 'master_akun_database_awal.json')
            status = 'SUCCESS'
            messages.success(request, "Import Master Akun berhasil!")
        except Exception as e:
            status = 'ERROR'
            messages.error(request, f"Terjadi kesalahan saat import: {e}")
        
        counts_after = MasterAkun.objects.count()

    context = {
        'counts_before': counts_before,
        'status': status,
        'counts_after': counts_after,
        'preview_count': 41,
    }
    return render(request, 'maintenance/seed_master_akun.html', context)

from django.contrib.auth import get_user_model

@user_passes_test(is_admin, login_url='/accounts/login/')
def sync_satker_passwords_view(request):
    User = get_user_model()
    users = User.objects.filter(profile__satker_code__isnull=False).exclude(profile__satker_code='')
    
    users_to_update = []
    for user in users:
        code = user.profile.satker_code
        env_key = f"SATKER_{code}_PASSWORD"
        if os.environ.get(env_key):
            users_to_update.append({
                "username": user.username,
                "satker": code,
                "env_key": env_key,
                "user_obj": user
            })

    status = None
    updated_count = 0

    if request.method == 'POST':
        try:
            with transaction.atomic():
                for udata in users_to_update:
                    user_obj = udata["user_obj"]
                    new_password = os.environ.get(udata["env_key"])
                    user_obj.set_password(new_password)
                    user_obj.save()
                    updated_count += 1
            status = 'SUCCESS'
            messages.success(request, f"Berhasil sinkronisasi {updated_count} password satker dari Environment Variables!")
        except Exception as e:
            status = 'ERROR'
            messages.error(request, f"Terjadi kesalahan saat update password: {e}")

    context = {
        'users_to_update': users_to_update,
        'status': status,
        'updated_count': updated_count,
    }
    return render(request, 'maintenance/sync_passwords.html', context)
