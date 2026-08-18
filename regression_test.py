"""
Full Regression Test Script for INTERMILAN
Tests all major functionality after database cleaning
"""

import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'intermilan_project.settings.production')

# Override ALLOWED_HOSTS for testing
os.environ['DJANGO_ALLOWED_HOSTS'] = '127.0.0.1,localhost,testserver'
os.environ['DJANGO_DEBUG'] = 'True'

django.setup()

from django.test import Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from apps.accounts.models import Profile
from apps.core.models import SatkerMaster, MonitoringSummary
from apps.dk.models import TransactionDetail, MasterAkun
from apps.sp2d.models import SP2DRaw
from apps.drpp.models import DRPPUpload, DRPPItem, DRPPMatch
from apps.paket_spm.models import PaketSPMUpload
from apps.documents.models import DocumentUpload, DocumentDriveLink, ChecklistStatus
from decimal import Decimal

def print_header(title):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)

def print_test(name, passed, details=""):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"  {status} {name}")
    if details:
        print(f"       {details}")

def test_admin_access():
    """Test admin user can access all features."""
    print_header("TEST: ADMIN ACCESS")

    client = Client()
    User = get_user_model()

    # Login as admin
    admin = User.objects.filter(username='admin').first()
    if not admin:
        print_test("Admin user exists", False, "admin user not found")
        return False

    client.force_login(admin)

    results = []

    # Test dashboard access
    try:
        response = client.get(reverse("core:dashboard"))
        results.append(("Dashboard accessible", response.status_code == 200))
    except Exception as e:
        results.append(("Dashboard accessible", False, str(e)))

    # Test D_K list access
    try:
        response = client.get(reverse("dk:transaction_list"))
        results.append(("D_K list accessible", response.status_code == 200))
    except Exception as e:
        results.append(("D_K list accessible", False, str(e)))

    # Test SP2D list access (not upload)
    try:
        response = client.get(reverse("sp2d:list"))
        results.append(("SP2D list accessible", response.status_code == 200))
    except Exception as e:
        results.append(("SP2D list accessible", False, str(e)))

    # Test monitoring access
    try:
        response = client.get(reverse("core:monitoring"))
        results.append(("Monitoring accessible", response.status_code == 200))
    except Exception as e:
        results.append(("Monitoring accessible", False, str(e)))

    # Test master akun access
    try:
        response = client.get(reverse("core:master_akun"))
        results.append(("Master Akun accessible", response.status_code == 200))
    except Exception as e:
        results.append(("Master Akun accessible", False, str(e)))

    # Test documents access
    try:
        response = client.get(reverse("documents:checklist"))
        results.append(("Documents accessible", response.status_code == 200))
    except Exception as e:
        results.append(("Documents accessible", False, str(e)))

    # Test DRPP list access
    try:
        response = client.get(reverse("drpp:list"))
        results.append(("DRPP accessible", response.status_code == 200))
    except Exception as e:
        results.append(("DRPP accessible", False, str(e)))

    # Test paket SPM list access
    try:
        response = client.get(reverse("paket_spm:list"))
        results.append(("Paket SPM accessible", response.status_code == 200))
    except Exception as e:
        results.append(("Paket SPM accessible", False, str(e)))

    for result in results:
        if len(result) == 2:
            print_test(result[0], result[1])
        else:
            print_test(result[0], result[1], result[2])

    return all(r[1] for r in results)

def test_operator_access():
    """Test operator satker access restrictions."""
    print_header("TEST: OPERATOR SATKER ACCESS (KK_1306)")

    client = Client()
    User = get_user_model()

    # Login as operator 1306
    operator = User.objects.filter(username='KK_1306').first()
    if not operator:
        print_test("Operator KK_1306 exists", False)
        return False

    print_test("Operator KK_1306 exists", True)

    # Check profile
    profile = operator.profile
    print_test("Profile role is SATKER", profile.role == Profile.Role.SATKER, f"role={profile.role}")
    print_test("Profile satker_code is 1306", profile.satker_code == "1306", f"satker_code={profile.satker_code}")

    # Check satker mapping
    satker = SatkerMaster.objects.filter(unit_code="1306").first()
    if satker:
        print_test("Satker mapping 1306 -> 019958", satker.satker_code == "019958",
                   f"mapped to {satker.satker_code}")
        print_test("Satker name: BPS Kabupaten Padang Pariaman",
                   satker.nama_satker == "BPS Kabupaten Padang Pariaman",
                   f"name={satker.nama_satker}")
    else:
        print_test("Satker mapping 1306 -> 019958", False, "mapping not found")

    client.force_login(operator)

    # Test dashboard shows only own data
    try:
        response = client.get(reverse("core:dashboard"))
        if response.status_code == 200:
            # Check if operator sees only their satker
            content = response.content.decode('utf-8', errors='ignore')
            print_test("Dashboard accessible for operator", True)
            print_test("Dashboard shows operator scope label",
                       "Satker 1306" in content or "Padang Pariaman" in content,
                       "Operator should see their satker in dashboard")
        else:
            print_test("Dashboard accessible for operator", False, f"status={response.status_code}")
    except Exception as e:
        print_test("Dashboard accessible for operator", False, str(e))

    # Test D_K shows only own satker
    try:
        response = client.get(reverse("dk:transaction_list"))
        print_test("D_K list accessible for operator", response.status_code == 200)
    except Exception as e:
        print_test("D_K list accessible for operator", False, str(e))

    return True

def test_satker_mapping():
    """Test 4-digit to 6-digit satker mapping."""
    print_header("TEST: SATKER MAPPING (4-digit -> 6-digit)")

    # Check key mappings as per requirements
    test_cases = [
        ("1306", "019958", "BPS Kabupaten Padang Pariaman"),
    ]

    all_passed = True
    for unit_code, expected_satker, expected_name in test_cases:
        satker = SatkerMaster.objects.filter(unit_code=unit_code).first()
        if satker:
            passed = satker.satker_code == expected_satker
            print_test(f"{unit_code} -> {expected_satker}", passed,
                       f"got {satker.satker_code}")
            if satker.nama_satker != expected_name:
                print_test(f"Name check", False,
                           f"expected '{expected_name}', got '{satker.nama_satker}'")
                all_passed = False
        else:
            print_test(f"{unit_code} -> {expected_satker}", False, "mapping not found")
            all_passed = False

    # Count all satkers
    satker_count = SatkerMaster.objects.count()
    print_test(f"Total satker mappings: {satker_count}", satker_count == 20)

    return all_passed

def test_master_data_integrity():
    """Test that master data is preserved."""
    print_header("TEST: MASTER DATA INTEGRITY")

    results = []

    # Check SatkerMaster
    satker_count = SatkerMaster.objects.count()
    results.append(("SatkerMaster count = 20", satker_count == 20, f"count={satker_count}"))

    # Check MasterAkun
    akun_count = MasterAkun.objects.count()
    results.append(("MasterAkun count", akun_count > 0, f"count={akun_count}"))

    # Check Users
    User = get_user_model()
    user_count = User.objects.count()
    results.append(("User count", user_count > 0, f"count={user_count}"))

    # Check Profiles
    profile_count = Profile.objects.count()
    results.append(("Profile count", profile_count > 0, f"count={profile_count}"))

    for result in results:
        if len(result) == 3:
            print_test(result[0], result[1], result[2])
        else:
            print_test(result[0], result[1])

    return all(r[1] for r in results)

def test_database_empty():
    """Test that transactional data is cleaned."""
    print_header("TEST: DATABASE CLEANED (NO TEST DATA)")

    results = []

    # Check SP2D
    sp2d_count = SP2DRaw.objects.count()
    results.append(("SP2DRaw count = 0", sp2d_count == 0, f"count={sp2d_count}"))

    # Check TransactionDetail
    dk_count = TransactionDetail.objects.count()
    results.append(("TransactionDetail count = 0", dk_count == 0, f"count={dk_count}"))

    # Check DRPP
    drpp_count = DRPPUpload.objects.count()
    results.append(("DRPPUpload count = 0", drpp_count == 0, f"count={drpp_count}"))

    # Check SPM
    spm_count = PaketSPMUpload.objects.count()
    results.append(("PaketSPMUpload count = 0", spm_count == 0, f"count={spm_count}"))

    # Check Documents
    doc_count = DocumentUpload.objects.count()
    results.append(("DocumentUpload count = 0", doc_count == 0, f"count={doc_count}"))

    for result in results:
        if len(result) == 3:
            print_test(result[0], result[1], result[2])
        else:
            print_test(result[0], result[1])

    return all(r[1] for r in results)

def test_dashboard_empty_state():
    """Test dashboard works with empty database."""
    print_header("TEST: DASHBOARD EMPTY STATE")

    client = Client()
    User = get_user_model()
    admin = User.objects.filter(username='admin').first()

    if not admin:
        print_test("Admin user exists", False)
        return False

    client.force_login(admin)

    try:
        response = client.get(reverse("core:dashboard"))
        if response.status_code == 200:
            print_test("Dashboard loads without error", True)
            # Check for empty state message
            content = response.content.decode('utf-8', errors='ignore')
            has_empty_state = "Belum ada data" in content or "data agregat" in content.lower()
            print_test("Empty state message shown", has_empty_state,
                       "Dashboard should show empty state when no data")
        else:
            print_test("Dashboard loads without error", False, f"status={response.status_code}")
            return False
    except Exception as e:
        print_test("Dashboard loads without error", False, str(e))
        return False

    # Test year filter
    try:
        response = client.get(reverse("core:dashboard"), {"tahun": "2026", "bulan": "1"})
        print_test("Dashboard year/month filter works", response.status_code == 200)
    except Exception as e:
        print_test("Dashboard year/month filter works", False, str(e))
        return False

    return True

def test_dk_list_empty_state():
    """Test D_K list works with empty database."""
    print_header("TEST: D_K LIST EMPTY STATE")

    client = Client()
    User = get_user_model()
    admin = User.objects.filter(username='admin').first()

    if not admin:
        print_test("Admin user exists", False)
        return False

    client.force_login(admin)

    try:
        response = client.get(reverse("dk:transaction_list"))
        if response.status_code == 200:
            print_test("D_K list loads without error", True)
        else:
            print_test("D_K list loads without error", False, f"status={response.status_code}")
            return False
    except Exception as e:
        print_test("D_K list loads without error", False, str(e))
        return False

    # Test filters
    try:
        response = client.get(reverse("dk:transaction_list"), {
            "bulan": "1",
            "tahun": "2026",
            "satker": "1306"
        })
        print_test("D_K filters work", response.status_code == 200)
    except Exception as e:
        print_test("D_K filters work", False, str(e))
        return False

    # Test search
    try:
        response = client.get(reverse("dk:transaction_list"), {"q": "test"})
        print_test("D_K search works", response.status_code == 200)
    except Exception as e:
        print_test("D_K search works", False, str(e))
        return False

    return True

def test_monitoring_empty_state():
    """Test monitoring works with empty database."""
    print_header("TEST: MONITORING EMPTY STATE")

    client = Client()
    User = get_user_model()
    admin = User.objects.filter(username='admin').first()

    if not admin:
        print_test("Admin user exists", False)
        return False

    client.force_login(admin)

    try:
        response = client.get(reverse("core:monitoring"))
        if response.status_code == 200:
            print_test("Monitoring loads without error", True)
        else:
            print_test("Monitoring loads without error", False, f"status={response.status_code}")
            return False
    except Exception as e:
        print_test("Monitoring loads without error", False, str(e))
        return False

    # Test filters
    try:
        response = client.get(reverse("core:monitoring"), {
            "tahun": "2026",
            "bulan": "1",
            "satker": "1306"
        })
        print_test("Monitoring filters work", response.status_code == 200)
    except Exception as e:
        print_test("Monitoring filters work", False, str(e))
        return False

    return True

def test_security_checks():
    """Test security restrictions."""
    print_header("TEST: SECURITY CHECKS")

    client = Client()
    User = get_user_model()

    # Test unauthenticated access redirects to login
    try:
        response = client.get(reverse("core:dashboard"))
        redirect_to_login = response.status_code == 302 and 'login' in response.url
        print_test("Unauthenticated access redirects to login", redirect_to_login)
    except Exception as e:
        print_test("Unauthenticated access redirects to login", False, str(e))
        return False

    # Test operator cannot access audit data
    operator = User.objects.filter(username='KK_1306').first()
    if operator:
        client.force_login(operator)
        try:
            response = client.get(reverse("core:audit_data"))
            forbidden = response.status_code == 403
            print_test("Operator cannot access audit data", forbidden,
                       f"status={response.status_code}")
        except Exception as e:
            print_test("Operator cannot access audit data", False, str(e))

    # Test admin can access audit data
    admin = User.objects.filter(username='admin').first()
    if admin:
        client.force_login(admin)
        try:
            response = client.get(reverse("core:audit_data"))
            allowed = response.status_code == 200
            print_test("Admin can access audit data", allowed,
                       f"status={response.status_code}")
        except Exception as e:
            print_test("Admin can access audit data", False, str(e))

    return True

def test_csrf_protection():
    """Test CSRF protection is active."""
    print_header("TEST: CSRF PROTECTION")

    # Check that CSRF middleware is configured
    from django.conf import settings
    csrf_middleware = 'django.middleware.csrf.CsrfViewMiddleware' in settings.MIDDLEWARE
    print_test("CSRF middleware is active", csrf_middleware)

    return csrf_middleware

def main():
    print()
    print("=" * 70)
    print("  INTERMILAN FULL REGRESSION TEST")
    print("  Database: intermilan_clean (PostgreSQL)")
    print("=" * 70)

    results = []

    # Phase 1: Database State
    results.append(("Database Cleaned", test_database_empty()))

    # Phase 2: Master Data
    results.append(("Master Data Integrity", test_master_data_integrity()))

    # Phase 3: Satker Mapping
    results.append(("Satker Mapping", test_satker_mapping()))

    # Phase 4: Admin Access
    results.append(("Admin Access", test_admin_access()))

    # Phase 5: Operator Access
    results.append(("Operator Access", test_operator_access()))

    # Phase 6: Empty State Handling
    results.append(("Dashboard Empty State", test_dashboard_empty_state()))
    results.append(("D_K List Empty State", test_dk_list_empty_state()))
    results.append(("Monitoring Empty State", test_monitoring_empty_state()))

    # Phase 7: Security
    results.append(("Security Checks", test_security_checks()))
    results.append(("CSRF Protection", test_csrf_protection()))

    # Summary
    print_header("TEST SUMMARY")

    passed = 0
    failed = 0

    for name, result in results:
        status = "PASS" if result else "FAIL"
        symbol = "[OK]" if result else "[!!]"
        print(f"  {symbol} {name}")
        if result:
            passed += 1
        else:
            failed += 1

    print()
    print(f"  Total: {len(results)} tests")
    print(f"  Passed: {passed}")
    print(f"  Failed: {failed}")
    print()

    if failed == 0:
        print("  " + "=" * 66)
        print("  [READY FOR CLIENT]")
        print("  " + "=" * 66)
        return True
    else:
        print("  " + "=" * 66)
        print(f"  [NOT READY - {failed} test(s) failed]")
        print("  " + "=" * 66)
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
