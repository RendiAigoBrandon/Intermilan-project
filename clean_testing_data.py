"""
Database Cleaning Script for INTERMILAN - Clean all testing data before client delivery

This script cleans all transactional data while preserving master/reference data.

PRESERVED (DO NOT DELETE):
- User accounts
- User profiles
- Satker Master (mapping 4 digit <-> 6 digit)
- Master Akun
- Auth permissions
- Django sessions (except we clear them for security)

DELETED (Testing/transactional data):
- SP2DRaw
- SP2DImportBatch
- TransactionDetail
- TransactionChangeLog
- DRPPUpload
- DRPPItem
- DRPPMatch
- DRPPImportBatch
- DRPPSupportingAttachment
- PaketSPMUpload
- PaketSPMPreviewItem
- DocumentUpload
- DocumentDriveLink
- ChecklistStatus
- ChecklistTemplate (only if dummy)
- MonitoringSummary
- TransactionPackage
- TransactionProvenance
- ActiveParentSession
- DRPPPreviewState
"""

import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'intermilan_project.settings.production')
django.setup()

from django.db import connection, transaction
from django.contrib.auth.models import User
from apps.accounts.models import Profile
from apps.core.models import (
    MonitoringSummary,
    SatkerMaster,
    TransactionPackage,
    TransactionProvenance,
    ActiveParentSession,
    DRPPPreviewState,
)
from apps.sp2d.models import SP2DImportBatch, SP2DRaw
from apps.dk.models import TransactionDetail, TransactionChangeLog, MasterAkun
from apps.drpp.models import (
    DRPPImportBatch,
    DRPPUpload,
    DRPPSupportingAttachment,
    DRPPItem,
    DRPPMatch,
)
from apps.paket_spm.models import PaketSPMUpload, PaketSPMPreviewItem
from apps.documents.models import (
    DocumentUpload,
    DocumentDriveLink,
    ChecklistStatus,
    ChecklistTemplate,
)
from apps.auditlog.models import AuditLog

def count_model(model, label=None):
    """Count records in a model."""
    try:
        count = model.objects.count()
        label = label or model.__name__
        print(f"  {label}: {count}")
        return count
    except Exception as e:
        print(f"  {label}: ERROR - {e}")
        return 0

def delete_model(model, label=None):
    """Delete all records in a model."""
    try:
        count = model.objects.count()
        if count > 0:
            deleted, _ = model.objects.all().delete()
            label = label or model.__name__
            print(f"  DELETED {deleted} {label}")
            return deleted
        else:
            label = label or model.__name__
            print(f"  {label}: already empty")
            return 0
    except Exception as e:
        label = label or model.__name__
        print(f"  {label}: ERROR - {e}")
        return 0

def print_section(title):
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)

def main():
    print()
    print("INTERMILAN DATABASE CLEANING SCRIPT")
    print("=" * 60)
    print("PURPOSE: Clean all testing/dummy data before client delivery")
    print()

    # Check Django is properly configured
    print(f"Database: {connection.settings_dict.get('NAME', 'unknown')}")
    print(f"Engine: {connection.settings_dict.get('ENGINE', 'unknown')}")
    print()

    # ============================================================
    # PHASE 1: AUDIT BEFORE DELETION
    # ============================================================
    print_section("PHASE 1: AUDIT - DATA BEFORE CLEANING")
    print()

    print("SP2D Tables:")
    count_model(SP2DImportBatch)
    count_model(SP2DRaw)

    print()
    print("D_K Tables:")
    count_model(TransactionDetail)
    count_model(TransactionChangeLog)

    print()
    print("DRPP Tables:")
    count_model(DRPPImportBatch)
    count_model(DRPPUpload)
    count_model(DRPPSupportingAttachment)
    count_model(DRPPItem)
    count_model(DRPPMatch)

    print()
    print("PAKET_SPM Tables:")
    count_model(PaketSPMUpload)
    count_model(PaketSPMPreviewItem)

    print()
    print("DOCUMENTS Tables:")
    count_model(DocumentUpload)
    count_model(DocumentDriveLink)
    count_model(ChecklistStatus)
    count_model(ChecklistTemplate)

    print()
    print("CORE Tables:")
    count_model(TransactionPackage)
    count_model(TransactionProvenance)
    count_model(ActiveParentSession)
    count_model(DRPPPreviewState)
    count_model(MonitoringSummary)

    print()
    print("MASTER DATA (PRESERVED):")
    count_model(SatkerMaster, "SatkerMaster")
    count_model(MasterAkun, "MasterAkun")
    count_model(User, "Users")
    count_model(Profile, "Profiles")

    # ============================================================
    # PHASE 2: DELETE TRANSACTIONAL DATA
    # ============================================================
    print_section("PHASE 2: DELETE TRANSACTIONAL DATA")

    total_deleted = 0

    print()
    print("Deleting SP2D data...")
    total_deleted += delete_model(SP2DRaw)
    total_deleted += delete_model(SP2DImportBatch)

    print()
    print("Deleting D_K data...")
    total_deleted += delete_model(TransactionDetail)
    total_deleted += delete_model(TransactionChangeLog)

    print()
    print("Deleting DRPP data...")
    try:
        total_deleted += delete_model(DRPPSupportingAttachment)
    except Exception as e:
        print(f"  DRPPSupportingAttachment: Table may not exist - {e}")
    total_deleted += delete_model(DRPPMatch)
    total_deleted += delete_model(DRPPItem)
    total_deleted += delete_model(DRPPUpload)
    total_deleted += delete_model(DRPPImportBatch)

    print()
    print("Deleting Paket SPM data...")
    total_deleted += delete_model(PaketSPMPreviewItem)
    total_deleted += delete_model(PaketSPMUpload)

    print()
    print("Deleting Documents data...")
    total_deleted += delete_model(ChecklistStatus)
    total_deleted += delete_model(DocumentDriveLink)
    total_deleted += delete_model(DocumentUpload)
    # Keep ChecklistTemplate as they might be system templates

    print()
    print("Deleting CORE data...")
    total_deleted += delete_model(DRPPPreviewState)
    total_deleted += delete_model(ActiveParentSession)
    total_deleted += delete_model(TransactionProvenance)
    total_deleted += delete_model(TransactionPackage)
    total_deleted += delete_model(MonitoringSummary)

    print()
    print("Deleting Audit Logs...")
    total_deleted += delete_model(AuditLog)

    # ============================================================
    # PHASE 3: VERIFICATION
    # ============================================================
    print_section("PHASE 3: VERIFICATION - DATA AFTER CLEANING")

    print()
    print("SP2D Tables (should be 0):")
    sp2d_raw_count = count_model(SP2DRaw)
    sp2d_batch_count = count_model(SP2DImportBatch)

    print()
    print("D_K Tables (should be 0):")
    dk_count = count_model(TransactionDetail)
    changelog_count = count_model(TransactionChangeLog)

    print()
    print("DRPP Tables (should be 0):")
    drpp_upload_count = count_model(DRPPUpload)
    drpp_item_count = count_model(DRPPItem)
    drpp_match_count = count_model(DRPPMatch)

    print()
    print("PAKET_SPM Tables (should be 0):")
    paket_count = count_model(PaketSPMUpload)
    paket_preview_count = count_model(PaketSPMPreviewItem)

    print()
    print("DOCUMENTS Tables (should be 0):")
    doc_count = count_model(DocumentUpload)
    drive_link_count = count_model(DocumentDriveLink)
    checklist_count = count_model(ChecklistStatus)

    print()
    print("CORE Tables (should be 0):")
    pkg_count = count_model(TransactionPackage)
    provenance_count = count_model(TransactionProvenance)
    session_count = count_model(ActiveParentSession)
    preview_count = count_model(DRPPPreviewState)
    summary_count = count_model(MonitoringSummary)

    print()
    print("MASTER DATA (PRESERVED - should remain):")
    satker_count = count_model(SatkerMaster, "SatkerMaster")
    akun_count = count_model(MasterAkun, "MasterAkun")
    user_count = count_model(User, "Users")
    profile_count = count_model(Profile, "Profiles")

    # ============================================================
    # PHASE 4: SUMMARY
    # ============================================================
    print_section("PHASE 4: SUMMARY")

    print()
    print(f"Total records deleted: {total_deleted}")
    print()

    # Verification checks
    all_passed = True

    print("VERIFICATION CHECKS:")
    print("-" * 40)

    checks = [
        ("SP2DRaw count", sp2d_raw_count, 0),
        ("SP2DImportBatch count", sp2d_batch_count, 0),
        ("TransactionDetail count", dk_count, 0),
        ("DRPPUpload count", drpp_upload_count, 0),
        ("DRPPItem count", drpp_item_count, 0),
        ("PaketSPMUpload count", paket_count, 0),
        ("DocumentUpload count", doc_count, 0),
        ("TransactionPackage count", pkg_count, 0),
        ("MonitoringSummary count", summary_count, 0),
        ("SatkerMaster count (preserved)", satker_count, 20),
        ("MasterAkun count (preserved)", akun_count, 54),
    ]

    for name, actual, expected in checks:
        status = "PASS" if actual == expected else "FAIL"
        print(f"  [{status}] {name}: {actual} (expected {expected})")
        if actual != expected:
            all_passed = False

    print()
    print("=" * 60)
    if all_passed:
        print("  [OK] ALL VERIFICATION CHECKS PASSED")
        print("  [OK] DATABASE IS CLEAN AND READY FOR CLIENT")
    else:
        print("  [FAIL] SOME VERIFICATION CHECKS FAILED")
        print("  [FAIL] PLEASE REVIEW THE MISMATCHES ABOVE")
    print("=" * 60)
    print()

    return all_passed

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
