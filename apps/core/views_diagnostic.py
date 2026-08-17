import os
import subprocess
from django.http import JsonResponse
from django.contrib.auth.decorators import user_passes_test
from django.conf import settings
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.db.migrations.executor import MigrationExecutor

# Models
from apps.dk.models import TransactionDetail, MasterAkun
from apps.sp2d.models import SP2DRaw
from apps.drpp.models import DRPPUpload, DRPPItem, DRPPMatch
from apps.paket_spm.models import PaketSPMUpload
from apps.documents.models import DocumentUpload, DocumentDriveLink
from apps.core.models import SatkerMaster
from django.contrib.auth import get_user_model

def is_admin(user):
    return user.is_authenticated and hasattr(user, 'profile') and user.profile.is_admin_pusat

def get_git_info():
    try:
        commit_hash = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode('utf-8').strip()
        branch = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD']).decode('utf-8').strip()
        return {'commit': commit_hash, 'branch': branch}
    except Exception as e:
        return {'commit': 'Unknown', 'branch': 'Unknown', 'error': str(e)}

@user_passes_test(is_admin)
def diagnostic_audit_view(request):
    """
    Lightweight diagnostic view returning COUNT() only.
    No sensitive data is exposed.
    """
    git_info = get_git_info()
    
    # DB Info (No passwords)
    db_config = settings.DATABASES['default']
    db_info = {
        'engine': db_config.get('ENGINE', ''),
        'host': db_config.get('HOST', ''),
        'name': db_config.get('NAME', '')
    }
    
    # Migration Status
    try:
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        unapplied = executor.migration_plan(targets)
        migration_status = {
            'all_applied': len(unapplied) == 0,
            'unapplied_count': len(unapplied),
        }
    except Exception as e:
        migration_status = {'error': str(e)}
        
    # Table Counts
    User = get_user_model()
    counts = {
        'Master_SatkerMaster': SatkerMaster.objects.count(),
        'Master_MasterAkun': MasterAkun.objects.count(),
        'Master_User': User.objects.count(),
        'Tx_SP2DRaw': SP2DRaw.objects.count(),
        'Tx_TransactionDetail': TransactionDetail.objects.count(),
        'Tx_DRPPUpload': DRPPUpload.objects.count(),
        'Tx_DRPPItem': DRPPItem.objects.count(),
        'Tx_DRPPMatch': DRPPMatch.objects.count(),
        'Tx_PaketSPMUpload': PaketSPMUpload.objects.count(),
        'Tx_DocumentUpload': DocumentUpload.objects.count(),
        'Tx_DocumentDriveLink': DocumentDriveLink.objects.count(),
    }
    
    return JsonResponse({
        'status': 'success',
        'git': git_info,
        'database': db_info,
        'migrations': migration_status,
        'counts': counts,
    })
