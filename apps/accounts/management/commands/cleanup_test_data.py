"""
Management command to cleanup test/development data from INTERMILAN database.

Usage:
    # Dry-run - show what will be deleted without making changes
    python manage.py cleanup_test_data --dry-run

    # Execute cleanup
    python manage.py cleanup_test_data

This command safely removes:
    - Test/development users (not production users)
    - Their profiles
    - All related data (documents, DK, SP2D, etc.)
    - Physical media files

Production users protected:
    - admin
    - KK_1300 - KK_1312
    - KK_1371 - KK_1377
"""

import os
from django.core.management.base import BaseCommand
from django.db import transaction
from django.conf import settings


# Production users that should NOT be deleted
PROTECTED_USERS = {
    'admin',
    'KK_1300', 'KK_1301', 'KK_1302', 'KK_1303', 'KK_1304', 'KK_1305', 'KK_1306', 'KK_1307', 'KK_1308', 'KK_1309',
    'KK_1310', 'KK_1311', 'KK_1312',
    'KK_1371', 'KK_1372', 'KK_1373', 'KK_1374', 'KK_1375', 'KK_1376', 'KK_1377',
}

# Test users to be deleted
TEST_USERS = [
    'debug_admin', 'spm_parent_test',
    'test_sess', 'test_sess2', 'test_sess3', 'test_sess5',
    'test_session', 'test_session_user',
    'test302',
]


class Command(BaseCommand):
    help = "Cleanup test/development data from INTERMILAN database"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what will be deleted without making changes',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Skip confirmation prompt',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']

        self.stdout.write('')
        self.stdout.write(self.style.WARNING('=' * 70))
        self.stdout.write(self.style.WARNING('  INTERMILAN - CLEANUP TEST DATA'))
        self.stdout.write(self.style.WARNING('=' * 70))
        self.stdout.write('')

        if dry_run:
            self.stdout.write(self.style.WARNING('  MODE: DRY RUN - Tidak ada perubahan akan dilakukan'))
        else:
            self.stdout.write(self.style.ERROR('  MODE: LIVE - Data akan dihapus!'))
        self.stdout.write('')

        # Import models here to avoid circular imports
        from django.contrib.auth import get_user_model
        from django.contrib.admin.models import LogEntry
        from apps.accounts.models import Profile
        from apps.auditlog.models import AuditLog
        from apps.core.models import ActiveParentSession, DRPPPreviewState
        from apps.dk.models import TransactionDetail, TransactionChangeLog
        from apps.documents.models import DocumentUpload, DocumentDriveLink, ChecklistStatus
        from apps.drpp.models import DRPPImportBatch, DRPPUpload
        from apps.paket_spm.models import PaketSPMUpload
        from apps.sp2d.models import SP2DImportBatch, SP2DRaw

        User = get_user_model()

        # Find test users (exclude protected)
        all_test_patterns = TEST_USERS + ['test_', 'debug_', 'spm_parent']
        test_users = User.objects.filter(
            username__in=TEST_USERS
        ) | User.objects.filter(
            username__startswith='test_'
        ) | User.objects.filter(
            username__startswith='debug_'
        )
        test_users = test_users.exclude(username__in=PROTECTED_USERS).distinct()

        test_user_ids = list(test_users.values_list('id', flat=True))

        if not test_user_ids:
            self.stdout.write(self.style.SUCCESS('  Tidak ada test user yang ditemukan.'))
            return

        # Collect deletion plan
        deletion_plan = {}

        # 1. DocumentUpload
        doc_uploads = DocumentUpload.objects.filter(uploaded_by_id__in=test_user_ids)
        deletion_plan['documents.DocumentUpload'] = {
            'count': doc_uploads.count(),
            'queryset': doc_uploads,
            'files': [d.file.name for d in doc_uploads if d.file],
        }

        # 2. ChecklistStatus
        checklist = ChecklistStatus.objects.filter(updated_by_id__in=test_user_ids)
        deletion_plan['documents.ChecklistStatus'] = {
            'count': checklist.count(),
            'queryset': checklist,
        }

        # 3. DocumentDriveLink
        drive_links = DocumentDriveLink.objects.filter(created_by_id__in=test_user_ids)
        deletion_plan['documents.DocumentDriveLink'] = {
            'count': drive_links.count(),
            'queryset': drive_links,
        }

        # 4. TransactionDetail
        dk_details = TransactionDetail.objects.filter(created_by_id__in=test_user_ids)
        deletion_plan['dk.TransactionDetail'] = {
            'count': dk_details.count(),
            'queryset': dk_details,
        }

        # 5. TransactionChangeLog
        dk_changelog = TransactionChangeLog.objects.filter(changed_by_id__in=test_user_ids)
        deletion_plan['dk.TransactionChangeLog'] = {
            'count': dk_changelog.count(),
            'queryset': dk_changelog,
        }

        # 6. DRPPImportBatch
        drpp_batch = DRPPImportBatch.objects.filter(uploaded_by_id__in=test_user_ids)
        deletion_plan['drpp.DRPPImportBatch'] = {
            'count': drpp_batch.count(),
            'queryset': drpp_batch,
        }

        # 7. DRPPUpload
        drpp_upload = DRPPUpload.objects.filter(uploaded_by_id__in=test_user_ids)
        deletion_plan['drpp.DRPPUpload'] = {
            'count': drpp_upload.count(),
            'queryset': drpp_upload,
            'files': [d.file.name for d in drpp_upload if hasattr(d, 'file') and d.file],
        }

        # 8. PaketSPMUpload
        paket_upload = PaketSPMUpload.objects.filter(uploaded_by_id__in=test_user_ids)
        deletion_plan['paket_spm.PaketSPMUpload'] = {
            'count': paket_upload.count(),
            'queryset': paket_upload,
            'files': [p.file.name for p in paket_upload if hasattr(p, 'file') and p.file],
        }

        # 9. SP2DImportBatch
        sp2d_batch = SP2DImportBatch.objects.filter(uploaded_by_id__in=test_user_ids)
        deletion_plan['sp2d.SP2DImportBatch'] = {
            'count': sp2d_batch.count(),
            'queryset': sp2d_batch,
        }

        # 10. SP2DRaw
        sp2d_raw = SP2DRaw.objects.filter(created_by_id__in=test_user_ids)
        deletion_plan['sp2d.SP2DRaw'] = {
            'count': sp2d_raw.count(),
            'queryset': sp2d_raw,
        }

        # 11. DRPPPreviewState
        preview_state = DRPPPreviewState.objects.filter(user_id__in=test_user_ids)
        deletion_plan['core.DRPPPreviewState'] = {
            'count': preview_state.count(),
            'queryset': preview_state,
        }

        # 12. ActiveParentSession
        active_session = ActiveParentSession.objects.filter(user_id__in=test_user_ids)
        deletion_plan['core.ActiveParentSession'] = {
            'count': active_session.count(),
            'queryset': active_session,
        }

        # 13. AuditLog
        audit_log = AuditLog.objects.filter(user_id__in=test_user_ids)
        deletion_plan['auditlog.AuditLog'] = {
            'count': audit_log.count(),
            'queryset': audit_log,
        }

        # 14. LogEntry
        log_entry = LogEntry.objects.filter(user_id__in=test_user_ids)
        deletion_plan['admin.LogEntry'] = {
            'count': log_entry.count(),
            'queryset': log_entry,
        }

        # 15. Profile
        profiles = Profile.objects.filter(user_id__in=test_user_ids)
        deletion_plan['accounts.Profile'] = {
            'count': profiles.count(),
            'queryset': profiles,
        }

        # 16. Users
        deletion_plan['auth.User (TEST)'] = {
            'count': test_users.count(),
            'usernames': list(test_users.values_list('username', flat=True)),
            'queryset': test_users,
        }

        # =====================================================
        # DISPLAY DELETION PLAN
        # =====================================================
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('-' * 70))
        self.stdout.write(self.style.WARNING('  USER YANG AKAN DIHAPUS:'))
        self.stdout.write(self.style.WARNING('-' * 70))
        for username in sorted(deletion_plan['auth.User (TEST)']['usernames']):
            self.stdout.write(f'    - {username}')
        self.stdout.write('')

        self.stdout.write('')
        self.stdout.write(self.style.WARNING('-' * 70))
        self.stdout.write(self.style.WARNING('  DATA YANG AKAN DIHAPUS:'))
        self.stdout.write(self.style.WARNING('-' * 70))
        self.stdout.write('')
        self.stdout.write(f"  {'Model':<45} {'Records':>10}")
        self.stdout.write(f"  {'-'*45} {'-'*10}")

        total_records = 0
        files_to_delete = []

        for model_name in deletion_plan:
            if model_name == 'auth.User (TEST)':
                continue
            info = deletion_plan[model_name]
            count = info['count']
            total_records += count
            self.stdout.write(f"  {model_name:<45} {count:>10}")

            # Collect files
            if 'files' in info:
                files_to_delete.extend(info['files'])

        self.stdout.write(f"  {'-'*45} {'-'*10}")
        self.stdout.write(f"  {'TOTAL DATA RECORDS':<45} {total_records:>10}")
        self.stdout.write('')

        # Show files to delete
        if files_to_delete:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('-' * 70))
            self.stdout.write(self.style.WARNING('  FILE FISIK YANG AKAN DIHAPUS:'))
            self.stdout.write(self.style.WARNING('-' * 70))
            for f in files_to_delete:
                if f:
                    self.stdout.write(f'    - {f}')
            self.stdout.write('')

        # Protected users info
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('-' * 70))
        self.stdout.write(self.style.SUCCESS('  USER PRODUKSI YANG DILINDUNGI (TIDAK AKAN DIHAPUS):'))
        self.stdout.write(self.style.SUCCESS('-' * 70))
        self.stdout.write('    - admin')
        self.stdout.write('    - KK_1300 s/d KK_1312 (13 satker provinsi)')
        self.stdout.write('    - KK_1371 s/d KK_1377 (7 satker kota)')
        self.stdout.write('')

        # =====================================================
        # SUMMARY
        # =====================================================
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('=' * 70))
        self.stdout.write(self.style.WARNING('  SUMMARY'))
        self.stdout.write(self.style.WARNING('=' * 70))
        self.stdout.write(f'    User akan dihapus:    {deletion_plan["auth.User (TEST)"]["count"]}')
        self.stdout.write(f'    Profile akan dihapus:  {deletion_plan["accounts.Profile"]["count"]}')
        self.stdout.write(f'    Total data records:    {total_records}')
        self.stdout.write(f'    File fisik:           {len(files_to_delete)}')
        self.stdout.write('')

        if dry_run:
            self.stdout.write(self.style.WARNING('    >>> DRY RUN: Tidak ada data yang dihapus <<<'))
            self.stdout.write('')
            self.stdout.write('    Jalankan tanpa --dry-run untuk menghapus data:')
            self.stdout.write('    python manage.py cleanup_test_data')
            self.stdout.write('')
            return

        # =====================================================
        # EXECUTE DELETION
        # =====================================================
        if not force:
            self.stdout.write('')
            self.stdout.write(self.style.ERROR('  PERINGATAN: Data akan dihapus permanen!'))
            self.stdout.write('')
            confirm = input('    Ketik "yes" untuk melanjutkan: ')
            if confirm.lower() != 'yes':
                self.stdout.write('')
                self.stdout.write(self.style.WARNING('    Batal. Tidak ada perubahan.'))
                return

        self.stdout.write('')
        self.stdout.write(self.style.WARNING('  Memulai penghapusan...'))
        self.stdout.write('')

        try:
            with transaction.atomic():
                deleted_counts = {}

                # Delete in order (respecting FK constraints)
                deletion_order = [
                    'documents.DocumentUpload',
                    'documents.ChecklistStatus',
                    'documents.DocumentDriveLink',
                    'dk.TransactionDetail',
                    'dk.TransactionChangeLog',
                    'drpp.DRPPImportBatch',
                    'drpp.DRPPUpload',
                    'paket_spm.PaketSPMUpload',
                    'sp2d.SP2DImportBatch',
                    'sp2d.SP2DRaw',
                    'core.DRPPPreviewState',
                    'core.ActiveParentSession',
                    'auditlog.AuditLog',
                    'admin.LogEntry',
                    'accounts.Profile',
                    'auth.User (TEST)',
                ]

                for model_name in deletion_order:
                    info = deletion_plan[model_name]
                    deleted = info['queryset'].delete()[0]
                    deleted_counts[model_name] = deleted
                    if deleted > 0:
                        self.stdout.write(f'    [DELETED] {model_name}: {deleted} record(s)')

                # Delete physical files
                if files_to_delete:
                    self.stdout.write('')
                    self.stdout.write(self.style.WARNING('  Menghapus file fisik...'))
                    media_root = getattr(settings, 'MEDIA_ROOT', None)
                    if media_root:
                        for file_path in files_to_delete:
                            if file_path:
                                full_path = os.path.join(media_root, file_path)
                                if os.path.exists(full_path):
                                    try:
                                        os.remove(full_path)
                                        self.stdout.write(f'    [DELETED FILE] {file_path}')
                                    except Exception as e:
                                        self.stdout.write(f'    [ERROR] Gagal hapus {file_path}: {e}')
                                else:
                                    self.stdout.write(f'    [SKIP] File tidak ditemukan: {file_path}')

            # =====================================================
            # SUCCESS SUMMARY
            # =====================================================
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('=' * 70))
            self.stdout.write(self.style.SUCCESS('  CLEANUP BERHASIL!'))
            self.stdout.write(self.style.SUCCESS('=' * 70))
            self.stdout.write('')
            self.stdout.write(f'    User dihapus:   {deleted_counts.get("auth.User (TEST)", 0)}')
            self.stdout.write(f'    Profile dihapus: {deleted_counts.get("accounts.Profile", 0)}')

            total_deleted = sum(v for k, v in deleted_counts.items() if k != 'auth.User (TEST)' and k != 'accounts.Profile')
            self.stdout.write(f'    Total records:  {total_deleted}')
            self.stdout.write(f'    File dihapus:   {len(files_to_delete)}')
            self.stdout.write('')

        except Exception as e:
            self.stdout.write('')
            self.stdout.write(self.style.ERROR('=' * 70))
            self.stdout.write(self.style.ERROR('  ERROR: Cleanup gagal!'))
            self.stdout.write(self.style.ERROR('=' * 70))
            self.stdout.write('')
            self.stdout.write(f'    {str(e)}')
            self.stdout.write('')
            raise
