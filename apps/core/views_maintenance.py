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

try:
    from apps.core.models import ActiveParentSession
except ImportError:
    ActiveParentSession = None

def is_superuser(user):
    return user.is_authenticated and user.is_superuser

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

@user_passes_test(is_superuser, login_url='/admin/login/')
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
