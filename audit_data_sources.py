"""
Comprehensive Data Audit Script
Investigates ALL models that might contain transaction data
"""

import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'intermilan_project.settings.production')
os.environ['DJANGO_ALLOWED_HOSTS'] = '127.0.0.1,localhost,testserver'
os.environ['DJANGO_DEBUG'] = 'True'
django.setup()

from django.db import connection

def count_table(table_name):
    """Count records in a raw SQL table."""
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT COUNT(*) FROM {table_name}')
            return cursor.fetchone()[0]
    except Exception as e:
        return f"ERROR: {e}"

def audit_all_tables():
    print("=" * 80)
    print("COMPREHENSIVE DATA AUDIT - ALL TABLES")
    print("=" * 80)

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)
        tables = [row[0] for row in cursor.fetchall()]

    # Group tables by prefix
    groups = {
        'dk': [t for t in tables if t.startswith('dk_')],
        'sp2d': [t for t in tables if t.startswith('sp2d_')],
        'drpp': [t for t in tables if t.startswith('drpp_')],
        'documents': [t for t in tables if t.startswith('documents_')],
        'paket': [t for t in tables if t.startswith('paket_')],
        'core': [t for t in tables if t.startswith('core_')],
        'accounts': [t for t in tables if t.startswith('accounts_')],
        'auth': [t for t in tables if t.startswith('auth_')],
        'django': [t for t in tables if t.startswith('django_')],
    }

    total_data = 0

    for group_name, group_tables in groups.items():
        if not group_tables:
            continue

        print(f"\n{'=' * 60}")
        print(f"  {group_name.upper()} TABLES")
        print(f"{'=' * 60}")

        for table in sorted(group_tables):
            count = count_table(table)
            print(f"  {table}: {count}")
            if isinstance(count, int) and count > 0:
                total_data += count

    print(f"\n{'=' * 60}")
    print(f"  TOTAL DATA ROWS (all tables): {total_data}")
    print(f"{'=' * 60}")

    return total_data

def audit_transaction_models():
    """Audit specific transaction-related models."""
    print("\n" + "=" * 80)
    print("TRANSACTION MODELS AUDIT (via Django ORM)")
    print("=" * 80)

    from apps.sp2d.models import SP2DRaw, SP2DImportBatch
    from apps.dk.models import TransactionDetail, TransactionChangeLog, MasterAkun
    from apps.drpp.models import DRPPUpload, DRPPItem, DRPPMatch, DRPPImportBatch, DRPPSupportingAttachment
    from apps.paket_spm.models import PaketSPMUpload, PaketSPMPreviewItem
    from apps.documents.models import DocumentUpload, DocumentDriveLink, ChecklistStatus, ChecklistTemplate
    from apps.core.models import MonitoringSummary, TransactionPackage, TransactionProvenance, ActiveParentSession, DRPPPreviewState
    from apps.accounts.models import Profile
    from django.contrib.auth.models import User
    from apps.core.models import SatkerMaster

    models_to_check = [
        # SP2D
        (SP2DRaw, "SP2DRaw"),
        (SP2DImportBatch, "SP2DImportBatch"),
        # DK
        (TransactionDetail, "TransactionDetail"),
        (TransactionChangeLog, "TransactionChangeLog"),
        (MasterAkun, "MasterAkun (MUST HAVE DATA)"),
        # DRPP
        (DRPPUpload, "DRPPUpload"),
        (DRPPItem, "DRPPItem"),
        (DRPPMatch, "DRPPMatch"),
        (DRPPImportBatch, "DRPPImportBatch"),
        (DRPPSupportingAttachment, "DRPPSupportingAttachment"),
        # Paket SPM
        (PaketSPMUpload, "PaketSPMUpload"),
        (PaketSPMPreviewItem, "PaketSPMPreviewItem"),
        # Documents
        (DocumentUpload, "DocumentUpload"),
        (DocumentDriveLink, "DocumentDriveLink"),
        (ChecklistStatus, "ChecklistStatus"),
        (ChecklistTemplate, "ChecklistTemplate"),
        # Core
        (MonitoringSummary, "MonitoringSummary"),
        (TransactionPackage, "TransactionPackage"),
        (TransactionProvenance, "TransactionProvenance"),
        (ActiveParentSession, "ActiveParentSession"),
        (DRPPPreviewState, "DRPPPreviewState"),
        # Auth/Accounts
        (Profile, "Profile"),
        (User, "User"),
        (SatkerMaster, "SatkerMaster (MUST HAVE DATA)"),
    ]

    total_transaction_data = 0

    for model, label in models_to_check:
        try:
            count = model.objects.count()
            status = ">>> HAS DATA" if count > 0 else "OK (empty)"
            print(f"  {label:40} : {count:6} {status}")
            if 'MUST HAVE' in label:
                if count == 0:
                    print(f"    WARNING: {label} should have data!")
            if count > 0 and 'MUST HAVE' not in label:
                total_transaction_data += count
        except Exception as e:
            print(f"  {label:40} : ERROR - {e}")

    print(f"\n  Total transaction data (excluding master): {total_transaction_data}")

    return total_transaction_data

def audit_sp2d_details():
    """Show details of SP2DRaw data."""
    print("\n" + "=" * 80)
    print("SP2D RAW DETAILS")
    print("=" * 80)

    from apps.sp2d.models import SP2DRaw

    records = SP2DRaw.objects.all()
    print(f"\nTotal SP2DRaw records: {records.count()}")

    for record in records[:20]:  # Show first 20
        print(f"  ID={record.id}, satker={record.satker_code}, no_sp2d={record.no_sp2d}, no_spm={record.nomor_spm_extracted}")

def audit_transaction_details():
    """Show details of TransactionDetail data."""
    print("\n" + "=" * 80)
    print("TRANSACTION DETAIL DETAILS")
    print("=" * 80)

    from apps.dk.models import TransactionDetail

    records = TransactionDetail.objects.all()
    print(f"\nTotal TransactionDetail records: {records.count()}")

    for record in records[:20]:  # Show first 20
        print(f"  ID={record.id}, satker={record.satker_code}, no_spm={record.nomor_spm}, akun={record.akun}")

def audit_satker_scope():
    """Show data by satker."""
    print("\n" + "=" * 80)
    print("DATA BY SATKER CODE")
    print("=" * 80)

    from django.db.models import Count
    from apps.sp2d.models import SP2DRaw
    from apps.dk.models import TransactionDetail

    print("\nSP2DRaw by satker:")
    sp2d_by_satker = SP2DRaw.objects.values('satker_code').annotate(count=Count('id'))
    for item in sp2d_by_satker:
        print(f"  satker={item['satker_code']}: {item['count']} records")

    print("\nTransactionDetail by satker:")
    dk_by_satker = TransactionDetail.objects.values('satker_code').annotate(count=Count('id'))
    for item in dk_by_satker:
        print(f"  satker={item['satker_code']}: {item['count']} records")

if __name__ == "__main__":
    print("\n" + "#" * 80)
    print("# COMPREHENSIVE DATA AUDIT")
    print("#" * 80 + "\n")

    # Run all audits
    total_tables = audit_all_tables()
    total_orm = audit_transaction_models()
    audit_sp2d_details()
    audit_transaction_details()
    audit_satker_scope()

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total records in database (via SQL): {total_tables}")
    print(f"Total transaction model records (via ORM): {total_orm}")
    print()
