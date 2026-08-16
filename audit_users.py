"""
Audit Script - INTERMILAN User Database
Creates a detailed report of all users for cleanup planning.
"""

import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'intermilan_project.settings.development')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from django.contrib.auth import get_user_model
from apps.accounts.models import Profile

User = get_user_model()


def main():
    print()
    print("=" * 130)
    print("AUDIT SEMUA USER DI DATABASE INTERMILAN")
    print("=" * 130)
    print()

    # Define production usernames
    PRODUCTION_USERS = [
        'admin',
        'KK_1300', 'KK_1301', 'KK_1302', 'KK_1303', 'KK_1304', 'KK_1305', 'KK_1306', 'KK_1307', 'KK_1308', 'KK_1309',
        'KK_1310', 'KK_1311', 'KK_1312',
        'KK_1371', 'KK_1372', 'KK_1373', 'KK_1374', 'KK_1375', 'KK_1376', 'KK_1377'
    ]

    # Collect all users
    all_users = []
    for user in User.objects.all().select_related('profile').order_by('username'):
        try:
            profile = user.profile
            role = profile.role
            satker_code = profile.satker_code
            satker_name = profile.satker_name
        except Profile.DoesNotExist:
            role = 'NO_PROFILE'
            satker_code = '-'
            satker_name = '-'

        all_users.append({
            'username': user.username,
            'email': user.email or '-',
            'is_superuser': user.is_superuser,
            'is_staff': user.is_staff,
            'role': role,
            'satker_code': satker_code,
            'satker_name': satker_name,
            'created_at': user.date_joined.strftime('%Y-%m-%d %H:%M') if user.date_joined else '-',
        })

    # Categorize users
    production = [u for u in all_users if u['username'] in PRODUCTION_USERS]
    non_production = [u for u in all_users if u['username'] not in PRODUCTION_USERS]

    # Further categorize non-production
    viewers = [u for u in non_production if u['role'] == 'VIEWER']
    debug_test = [u for u in non_production if any(x in u['username'].lower() for x in ['debug', 'test', 'sample', 'sess'])]
    old_satker = [u for u in non_production if u['role'] == 'SATKER']
    others = [u for u in non_production if u not in viewers and u not in debug_test and u not in old_satker]

    # Header row
    header = f"{'Username':<30} {'Email':<25} {'Super':<7} {'Staff':<7} {'Role':<15} {'Satker':<8} {'Created At':<18}"
    separator = "-" * 130

    # ==================== SECTION 1 ====================
    print()
    print("=" * 130)
    print("SECTION 1: USER PRODUKSI YANG HARUS DIPERTAHANKAN")
    print("=" * 130)
    print()
    print(header)
    print(separator)

    for u in production:
        email = u['email'][:23] if len(u['email']) > 23 else u['email']
        role = u['role'][:13] if len(u['role']) > 13 else u['role']
        satker = u['satker_code'][:6] if len(str(u['satker_code'])) > 6 else u['satker_code']
        print(f"{u['username']:<30} {email:<25} {str(u['is_superuser']):<7} {str(u['is_staff']):<7} {role:<15} {satker:<8} {u['created_at']:<18}")

    print()
    print(f"[ TOTAL: {len(production)} user produksi ]")
    print()

    # ==================== SECTION 2 ====================
    print()
    print("=" * 130)
    print("SECTION 2: USER TESTING/LAMA YANG BOLEH DIHAPUS")
    print("=" * 130)
    print()

    # 2.1 VIEWER
    print("-" * 130)
    print("2.1 USER ROLE VIEWER (Bisa Dihapus)")
    print("-" * 130)
    print(header)
    print(separator)
    if viewers:
        for u in viewers:
            email = u['email'][:23] if len(u['email']) > 23 else u['email']
            role = u['role'][:13] if len(u['role']) > 13 else u['role']
            satker = u['satker_code'][:6] if len(str(u['satker_code'])) > 6 else u['satker_code']
            print(f"{u['username']:<30} {email:<25} {str(u['is_superuser']):<7} {str(u['is_staff']):<7} {role:<15} {satker:<8} {u['created_at']:<18}")
    else:
        print("(tidak ada)")
    print(f"[ TOTAL: {len(viewers)} user ]")
    print()

    # 2.2 DEBUG/TEST
    print("-" * 130)
    print("2.2 USER DEBUG/TEST/DEVELOPMENT (Bisa Dihapus)")
    print("-" * 130)
    print(header)
    print(separator)
    if debug_test:
        for u in debug_test:
            email = u['email'][:23] if len(u['email']) > 23 else u['email']
            role = u['role'][:13] if len(u['role']) > 13 else u['role']
            satker = u['satker_code'][:6] if len(str(u['satker_code'])) > 6 else u['satker_code']
            print(f"{u['username']:<30} {email:<25} {str(u['is_superuser']):<7} {str(u['is_staff']):<7} {role:<15} {satker:<8} {u['created_at']:<18}")
    else:
        print("(tidak ada)")
    print(f"[ TOTAL: {len(debug_test)} user ]")
    print()

    # 2.3 OLD SATKER
    print("-" * 130)
    print("2.3 USER SATKER LAMA (di luar daftar produksi, Bisa Dihapus)")
    print("-" * 130)
    print(header)
    print(separator)
    if old_satker:
        for u in old_satker:
            email = u['email'][:23] if len(u['email']) > 23 else u['email']
            role = u['role'][:13] if len(u['role']) > 13 else u['role']
            satker = u['satker_code'][:6] if len(str(u['satker_code'])) > 6 else u['satker_code']
            print(f"{u['username']:<30} {email:<25} {str(u['is_superuser']):<7} {str(u['is_staff']):<7} {role:<15} {satker:<8} {u['created_at']:<18}")
    else:
        print("(tidak ada)")
    print(f"[ TOTAL: {len(old_satker)} user ]")
    print()

    # 2.4 OTHERS
    print("-" * 130)
    print("2.4 USER LAIN-LAIN (Bisa Dihapus)")
    print("-" * 130)
    print(header)
    print(separator)
    if others:
        for u in others:
            email = u['email'][:23] if len(u['email']) > 23 else u['email']
            role = u['role'][:13] if len(u['role']) > 13 else u['role']
            satker = u['satker_code'][:6] if len(str(u['satker_code'])) > 6 else u['satker_code']
            print(f"{u['username']:<30} {email:<25} {str(u['is_superuser']):<7} {str(u['is_staff']):<7} {role:<15} {satker:<8} {u['created_at']:<18}")
    else:
        print("(tidak ada)")
    print(f"[ TOTAL: {len(others)} user ]")
    print()

    # ==================== SUMMARY ====================
    print()
    print("=" * 130)
    print("SUMMARY")
    print("=" * 130)
    print()
    print(f"Total User Keseluruhan:               {len(all_users)}")
    print()
    print(f"USER PRODUKSI (Dipertahankan):        {len(production)}")
    print(f"  - admin                             1")
    print(f"  - KK_1300 - KK_1312 (Provinsi)       13")
    print(f"  - KK_1371 - KK_1377 (Kota)           7")
    print()
    print(f"USER NON-PRODUKSI (Bisa Dihapus):     {len(non_production)}")
    print(f"  - VIEWER                            {len(viewers)}")
    print(f"  - DEBUG/TEST                        {len(debug_test)}")
    print(f"  - SATKER LAMA                       {len(old_satker)}")
    print(f"  - LAIN-LAIN                         {len(others)}")
    print()

    # List usernames to delete
    if non_production:
        print("=" * 130)
        print("DAFTAR USERNAME YANG BOLEH DIHAPUS:")
        print("=" * 130)
        usernames_to_delete = [u['username'] for u in non_production]
        # Print in columns
        for i in range(0, len(usernames_to_delete), 4):
            row = usernames_to_delete[i:i+4]
            print("  " + "  |  ".join(f"{name:<25}" for name in row))
        print()
        print(f"Total: {len(usernames_to_delete)} user")
        print()

    print("=" * 130)
    print("AKSI YANG AKAN DILAKUKAN (JIKA DIIZINKAN):")
    print("=" * 130)
    print()
    print("Option A: Hapus semua user non-produksi")
    print(f"  DELETE FROM auth_user WHERE username IN ({', '.join(repr(u) for u in usernames_to_delete)});")
    print()
    print("Option B: Hapus hanya user tertentu")
    print("  (isi sesuai kebutuhan)")
    print()
    print("=" * 130)


if __name__ == "__main__":
    main()
