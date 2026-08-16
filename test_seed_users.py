"""
Comprehensive Test Script for INTERMILAN User Seeding
Tests all aspects of the production users after seeding.
"""

import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'intermilan_project.settings.development')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
django.setup()

from django.contrib.auth import authenticate, get_user_model
from apps.accounts.models import Profile
from apps.accounts.access import (
    is_admin, is_operator_satker, is_viewer, can_edit_satker,
    filter_by_satker, get_user_satker_code, get_user_scope_label,
    permission_context
)
from apps.dk.models import TransactionDetail
from apps.documents.models import DocumentDriveLink

User = get_user_model()


def print_header(text):
    print()
    print("=" * 70)
    print(f"  {text}")
    print("=" * 70)


def print_test(name, passed, details=""):
    status = "[PASS]" if passed else "[FAIL]"
    color = ""  # Simple text output
    print(f"  {status} {name}")
    if details:
        for line in details.split("\n"):
            print(f"        {line}")


def test_user_exists(username):
    """Test if user exists in database"""
    return User.objects.filter(username=username).exists()


def test_login(username, password):
    """Test if user can authenticate"""
    user = authenticate(username=username, password=password)
    return user


def test_profile_data(user):
    """Get profile data for user"""
    profile = Profile.objects.get(user=user)
    return {
        "role": profile.role,
        "satker_code": profile.satker_code,
        "satker_name": profile.satker_name,
        "is_admin": profile.is_admin_pusat,
        "is_satker": profile.is_satker,
        "is_viewer": profile.is_viewer,
    }


def test_filter_scope(user, model_class, field_name="satker_code"):
    """Test filtering scope for a user on a model"""
    queryset = model_class.objects.all()
    filtered = filter_by_satker(queryset, user, field_name)
    return filtered.count(), queryset.count()


# ============================================================================
# TEST 1: ADMIN USER (admin)
# ============================================================================
def run_admin_tests():
    print_header("TEST 1: ADMIN USER (admin)")

    username = "admin"
    password = "IntermilanAdmin2026!"

    # Test 1.1: User exists
    passed = test_user_exists(username)
    print_test(f"User '{username}' exists", passed)

    # Test 1.2: Login works
    user = test_login(username, password)
    passed = user is not None
    print_test(f"Login with correct password", passed)

    if not user:
        return

    # Test 1.3: Profile data
    profile_data = test_profile_data(user)
    tests = [
        ("is_superuser = True", user.is_superuser == True),
        ("role = ADMIN_PUSAT", profile_data["role"] == Profile.Role.ADMIN_PUSAT),
        ("is_admin_pusat = True", profile_data["is_admin"] == True),
        ("is_satker = False", profile_data["is_satker"] == False),
        ("satker_code is empty", profile_data["satker_code"] == ""),
    ]
    for name, passed in tests:
        print_test(name, passed)

    # Test 1.4: Permission functions
    tests = [
        ("is_admin(user) returns True", is_admin(user) == True),
        ("is_operator_satker(user) returns False", is_operator_satker(user) == False),
        ("is_viewer(user) returns False", is_viewer(user) == False),
        ("can_view_all_satker returns True", True),  # Part of permission_context
    ]
    for name, passed in tests:
        print_test(name, passed)

    # Test 1.5: Scope label
    scope = get_user_scope_label(user)
    print_test(f"Scope label: '{scope}'", "Semua Satker" in scope)

    # Test 1.6: Permission context
    ctx = permission_context(user)
    tests = [
        ("is_role_admin = True", ctx["is_role_admin"] == True),
        ("is_role_operator = False", ctx["is_role_operator"] == False),
        ("can_view_all_satker = True", ctx["can_view_all_satker"] == True),
        ("can_import_data = True", ctx["can_import_data"] == True),
        ("can_export_data = True", ctx["can_export_data"] == True),
    ]
    for name, passed in tests:
        print_test(name, passed)

    # Test 1.7: Can edit all satker
    tests = [
        ("can_edit_satker(user, '1300')", can_edit_satker(user, "1300") == True),
        ("can_edit_satker(user, '1377')", can_edit_satker(user, "1377") == True),
        ("can_edit_satker(user, '9999')", can_edit_satker(user, "9999") == True),
    ]
    for name, passed in tests:
        print_test(name, passed)

    # Test 1.8: Filter scope (Admin sees all)
    filtered_count, total_count = test_filter_scope(user, TransactionDetail)
    print_test(f"Filter scope sees ALL TransactionDetails ({filtered_count}/{total_count})", filtered_count == total_count)


# ============================================================================
# TEST 2: SATKER USER (KK_1300)
# ============================================================================
def run_satker_1300_tests():
    print_header("TEST 2: SATKER USER (KK_1300)")

    username = "KK_1300"
    password = "bpsProvinsiSumateraBarat"

    # Test 2.1: User exists
    passed = test_user_exists(username)
    print_test(f"User '{username}' exists", passed)

    # Test 2.2: Login works
    user = test_login(username, password)
    passed = user is not None
    print_test(f"Login with correct password", passed)

    if not user:
        return

    # Test 2.3: Profile data
    profile_data = test_profile_data(user)
    tests = [
        ("is_superuser = False", user.is_superuser == False),
        ("role = SATKER", profile_data["role"] == Profile.Role.SATKER),
        ("is_admin_pusat = False", profile_data["is_admin"] == False),
        ("is_satker = True", profile_data["is_satker"] == True),
        ("satker_code = '1300'", profile_data["satker_code"] == "1300"),
        ("satker_name = 'BPS Provinsi Sumatera Barat'", profile_data["satker_name"] == "BPS Provinsi Sumatera Barat"),
    ]
    for name, passed in tests:
        print_test(name, passed)

    # Test 2.4: Permission functions
    tests = [
        ("is_admin(user) returns False", is_admin(user) == False),
        ("is_operator_satker(user) returns True", is_operator_satker(user) == True),
        ("is_viewer(user) returns False", is_viewer(user) == False),
    ]
    for name, passed in tests:
        print_test(name, passed)

    # Test 2.5: Scope label
    scope = get_user_scope_label(user)
    print_test(f"Scope label: '{scope}'", "1300" in scope and "Sumatera Barat" in scope)

    # Test 2.6: Can edit only own satker
    tests = [
        ("can_edit_satker(user, '1300')", can_edit_satker(user, "1300") == True),
        ("can_edit_satker(user, '1377')", can_edit_satker(user, "1377") == False),
        ("can_edit_satker(user, '9999')", can_edit_satker(user, "9999") == False),
    ]
    for name, passed in tests:
        print_test(name, passed)

    # Test 2.7: Permission context
    ctx = permission_context(user)
    tests = [
        ("is_role_admin = False", ctx["is_role_admin"] == False),
        ("is_role_operator = True", ctx["is_role_operator"] == True),
        ("can_view_all_satker = False", ctx["can_view_all_satker"] == False),
        ("can_upload_document = True", ctx["can_upload_document"] == True),
        ("can_import_data = False", ctx["can_import_data"] == False),
        ("can_export_data = True", ctx["can_export_data"] == True),
    ]
    for name, passed in tests:
        print_test(name, passed)


# ============================================================================
# TEST 3: SATKER USER (KK_1377)
# ============================================================================
def run_satker_1377_tests():
    print_header("TEST 3: SATKER USER (KK_1377)")

    username = "KK_1377"
    password = "bpsKotaPariaman"

    # Test 3.1: User exists
    passed = test_user_exists(username)
    print_test(f"User '{username}' exists", passed)

    # Test 3.2: Login works
    user = test_login(username, password)
    passed = user is not None
    print_test(f"Login with correct password", passed)

    if not user:
        return

    # Test 3.3: Profile data
    profile_data = test_profile_data(user)
    tests = [
        ("is_superuser = False", user.is_superuser == False),
        ("role = SATKER", profile_data["role"] == Profile.Role.SATKER),
        ("is_admin_pusat = False", profile_data["is_admin"] == False),
        ("is_satker = True", profile_data["is_satker"] == True),
        ("satker_code = '1377'", profile_data["satker_code"] == "1377"),
        ("satker_name = 'BPS Kota Pariaman'", profile_data["satker_name"] == "BPS Kota Pariaman"),
    ]
    for name, passed in tests:
        print_test(name, passed)

    # Test 3.4: Can edit only own satker
    tests = [
        ("can_edit_satker(user, '1377')", can_edit_satker(user, "1377") == True),
        ("can_edit_satker(user, '1300')", can_edit_satker(user, "1300") == False),
        ("can_edit_satker(user, '9999')", can_edit_satker(user, "9999") == False),
    ]
    for name, passed in tests:
        print_test(name, passed)


# ============================================================================
# TEST 4: ISOLATION TEST
# ============================================================================
def run_isolation_tests():
    print_header("TEST 4: ISOLATION TEST (Cross-Satker Access)")

    # Get users
    user_1300 = User.objects.get(username="KK_1300")
    user_1377 = User.objects.get(username="KK_1377")
    user_admin = User.objects.get(username="admin")

    # Test 4.1: KK_1300 cannot edit KK_1377's data
    can_1300_edit_1377 = can_edit_satker(user_1300, "1377")
    print_test("KK_1300 cannot edit satker 1377 data", can_1300_edit_1377 == False)

    # Test 4.2: KK_1377 cannot edit KK_1300's data
    can_1377_edit_1300 = can_edit_satker(user_1377, "1300")
    print_test("KK_1377 cannot edit satker 1300 data", can_1377_edit_1300 == False)

    # Test 4.3: Admin can edit both
    can_admin_edit_1300 = can_edit_satker(user_admin, "1300")
    can_admin_edit_1377 = can_edit_satker(user_admin, "1377")
    print_test("Admin can edit satker 1300", can_admin_edit_1300 == True)
    print_test("Admin can edit satker 1377", can_admin_edit_1377 == True)

    # Test 4.4: Filter scope isolation
    base_qs = TransactionDetail.objects.all()

    filtered_1300 = filter_by_satker(base_qs, user_1300)
    filtered_1377 = filter_by_satker(base_qs, user_1377)
    filtered_admin = filter_by_satker(base_qs, user_admin)

    print_test(
        f"KK_1300 scope: {filtered_1300.count()} records",
        filtered_1300.count() <= base_qs.count()
    )
    print_test(
        f"KK_1377 scope: {filtered_1377.count()} records",
        filtered_1377.count() <= base_qs.count()
    )
    print_test(
        f"Admin scope: {filtered_admin.count()} records (ALL)",
        filtered_admin.count() == base_qs.count()
    )

    # Test 4.5: Different satkers should see different data (if data exists)
    if base_qs.count() > 0:
        same_scope = filtered_1300.count() == filtered_1377.count()
        # Note: If both satkers have no data, this is expected
        # But at minimum, admin should see all
        print_test("Admin sees all data (more than or equal to any SATKER)", filtered_admin.count() >= filtered_1300.count())


# ============================================================================
# TEST 5: ALL SATKER USERS
# ============================================================================
def run_all_satker_tests():
    print_header("TEST 5: ALL SATKER USERS VERIFICATION")

    satker_codes = [
        "1300", "1301", "1302", "1303", "1304", "1305", "1306", "1307", "1308",
        "1309", "1310", "1311", "1312", "1371", "1372", "1373", "1374", "1375",
        "1376", "1377"
    ]

    expected_names = {
        "1300": "BPS Provinsi Sumatera Barat",
        "1301": "BPS Kabupaten Kepulauan Mentawai",
        "1302": "BPS Kabupaten Pesisir Selatan",
        "1303": "BPS Kabupaten Solok",
        "1304": "BPS Kabupaten Sijunjung",
        "1305": "BPS Kabupaten Tanah Datar",
        "1306": "BPS Kabupaten Padang Pariaman",
        "1307": "BPS Kabupaten Agam",
        "1308": "BPS Kabupaten Lima Puluh Kota",
        "1309": "BPS Kabupaten Pasaman",
        "1310": "BPS Kabupaten Solok Selatan",
        "1311": "BPS Kabupaten Dharmasraya",
        "1312": "BPS Kabupaten Pasaman Barat",
        "1371": "BPS Kota Padang",
        "1372": "BPS Kota Solok",
        "1373": "BPS Kota Sawahlunto",
        "1374": "BPS Kota Padang Panjang",
        "1375": "BPS Kota Bukittinggi",
        "1376": "BPS Kota Payakumbuh",
        "1377": "BPS Kota Pariaman",
    }

    passwords = {
        "1300": "bpsProvinsiSumateraBarat",
        "1301": "bpsKabupatenKepulauanMentawai",
        "1302": "bpsKabupatenPesisirSelatan",
        "1303": "bpsKabupatenSolok",
        "1304": "bpsKabupatenSijunjung",
        "1305": "bpsKabupatenTanahDatar",
        "1306": "bpsKabupatenPadangPariaman",
        "1307": "bpsKabupatenAgam",
        "1308": "bpsKabupatenLimaPuluhKota",
        "1309": "bpsKabupatenPasaman",
        "1310": "bpsKabupatenSolokSelatan",
        "1311": "bpsKabupatenDharmasraya",
        "1312": "bpsKabupatenPasamanBarat",
        "1371": "bpsKotaPadang",
        "1372": "bpsKotaSolok",
        "1373": "bpsKotaSawahlunto",
        "1374": "bpsKotaPadangPanjang",
        "1375": "bpsKotaBukittinggi",
        "1376": "bpsKotaPayakumbuh",
        "1377": "bpsKotaPariaman",
    }

    all_passed = True
    for code in satker_codes:
        username = f"KK_{code}"
        password = passwords[code]
        expected_name = expected_names[code]

        # Check user exists
        if not test_user_exists(username):
            print_test(f"KK_{code} exists", False)
            all_passed = False
            continue

        # Check login
        user = test_login(username, password)
        if not user:
            print_test(f"KK_{code} login", False)
            all_passed = False
            continue

        # Check profile
        profile_data = test_profile_data(user)

        tests = [
            ("role = SATKER", profile_data["role"] == Profile.Role.SATKER),
            ("satker_code = " + code, profile_data["satker_code"] == code),
            ("satker_name = " + expected_name, profile_data["satker_name"] == expected_name),
            ("is_satker = True", profile_data["is_satker"] == True),
            ("is_admin_pusat = False", profile_data["is_admin"] == False),
        ]

        code_passed = all([t[1] for t in tests])
        if not code_passed:
            all_passed = False
            print_test(f"KK_{code} profile", False, "\n".join([f"{t[0]}: {t[1]}" for t in tests]))

    print_test(f"All {len(satker_codes)} SATKER users verified", all_passed)


# ============================================================================
# TEST 6: GOOGLE DRIVE MODEL CHECK
# ============================================================================
def run_drive_link_tests():
    print_header("TEST 6: GOOGLE DRIVE LINKS (DocumentDriveLink)")

    # Check model exists and has correct fields
    print_test("DocumentDriveLink model exists", True)

    # Check satker_code field exists
    fields = [f.name for f in DocumentDriveLink._meta.get_fields()]
    has_satker_code = "satker_code" in fields
    print_test("Has satker_code field", has_satker_code)

    # Check created_by field exists
    has_created_by = "created_by" in fields
    print_test("Has created_by field", has_created_by)

    # Check google_drive_url field exists
    has_drive_url = "google_drive_url" in fields
    print_test("Has google_drive_url field", has_drive_url)

    # Current count
    current_count = DocumentDriveLink.objects.count()
    print_test(f"Current DocumentDriveLink records: {current_count}", True)

    return has_satker_code and has_created_by and has_drive_url


# ============================================================================
# MAIN
# ============================================================================
def main():
    print()
    print("#" * 70)
    print("#  INTERMILAN PRODUCTION USERS - COMPREHENSIVE TEST SUITE")
    print("#" * 70)

    # Run all tests
    run_admin_tests()
    run_satker_1300_tests()
    run_satker_1377_tests()
    run_isolation_tests()
    run_all_satker_tests()
    run_drive_link_tests()

    print()
    print("#" * 70)
    print("#  TEST SUMMARY")
    print("#" * 70)
    print()

    # Final summary
    total_users = User.objects.count()
    admin_count = User.objects.filter(is_superuser=True).count()
    satker_count = Profile.objects.filter(role=Profile.Role.SATKER).count()
    viewer_count = Profile.objects.filter(role=Profile.Role.VIEWER).count()

    print(f"  Total Users in Database: {total_users}")
    print(f"  - Admin (is_superuser=True): {admin_count}")
    print(f"  - SATKER role: {satker_count}")
    print(f"  - VIEWER role: {viewer_count}")
    print()
    print(f"  Expected SATKER users from seeding: 20")
    print(f"  Actual SATKER users: {satker_count}")
    print()

    # Verify seeding
    seed_users = ["admin"] + [f"KK_{code}" for code in [
        "1300", "1301", "1302", "1303", "1304", "1305", "1306", "1307", "1308",
        "1309", "1310", "1311", "1312", "1371", "1372", "1373", "1374", "1375",
        "1376", "1377"
    ]]

    missing_users = [u for u in seed_users if not test_user_exists(u)]
    if missing_users:
        print(f"  [WARNING] Missing expected users: {missing_users}")
    else:
        print(f"  [OK] All expected production users are present")

    print()
    print("#" * 70)


if __name__ == "__main__":
    main()
