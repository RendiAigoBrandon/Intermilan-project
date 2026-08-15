"""
Management command to seed production users for INTERMILAN application.

Usage:
    python manage.py seed_production_users

This command creates:
    - 1 ADMIN_PUSAT user (admin)
    - 21 SATKER users (KK_1300 - KK_1377)

If a user already exists, it will be updated (password and profile).
No duplicates will be created.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.accounts.models import Profile

User = get_user_model()


# ADMIN_PUSAT user data
ADMIN_USER = {
    "username": "admin",
    "password": "IntermilanAdmin2026!",
    "role": Profile.Role.ADMIN_PUSAT,
    "is_superuser": True,
    "satker_code": "",
    "satker_name": "",
}

# SATKER users data (21 units)
SATKER_USERS = [
    {
        "username": "KK_1300",
        "password": "bpsProvinsiSumateraBarat",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1300",
        "satker_name": "BPS Provinsi Sumatera Barat",
    },
    {
        "username": "KK_1301",
        "password": "bpsKabupatenKepulauanMentawai",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1301",
        "satker_name": "BPS Kabupaten Kepulauan Mentawai",
    },
    {
        "username": "KK_1302",
        "password": "bpsKabupatenPesisirSelatan",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1302",
        "satker_name": "BPS Kabupaten Pesisir Selatan",
    },
    {
        "username": "KK_1303",
        "password": "bpsKabupatenSolok",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1303",
        "satker_name": "BPS Kabupaten Solok",
    },
    {
        "username": "KK_1304",
        "password": "bpsKabupatenSijunjung",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1304",
        "satker_name": "BPS Kabupaten Sijunjung",
    },
    {
        "username": "KK_1305",
        "password": "bpsKabupatenTanahDatar",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1305",
        "satker_name": "BPS Kabupaten Tanah Datar",
    },
    {
        "username": "KK_1306",
        "password": "bpsKabupatenPadangPariaman",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1306",
        "satker_name": "BPS Kabupaten Padang Pariaman",
    },
    {
        "username": "KK_1307",
        "password": "bpsKabupatenAgam",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1307",
        "satker_name": "BPS Kabupaten Agam",
    },
    {
        "username": "KK_1308",
        "password": "bpsKabupatenLimaPuluhKota",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1308",
        "satker_name": "BPS Kabupaten Lima Puluh Kota",
    },
    {
        "username": "KK_1309",
        "password": "bpsKabupatenPasaman",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1309",
        "satker_name": "BPS Kabupaten Pasaman",
    },
    {
        "username": "KK_1310",
        "password": "bpsKabupatenSolokSelatan",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1310",
        "satker_name": "BPS Kabupaten Solok Selatan",
    },
    {
        "username": "KK_1311",
        "password": "bpsKabupatenDharmasraya",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1311",
        "satker_name": "BPS Kabupaten Dharmasraya",
    },
    {
        "username": "KK_1312",
        "password": "bpsKabupatenPasamanBarat",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1312",
        "satker_name": "BPS Kabupaten Pasaman Barat",
    },
    {
        "username": "KK_1371",
        "password": "bpsKotaPadang",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1371",
        "satker_name": "BPS Kota Padang",
    },
    {
        "username": "KK_1372",
        "password": "bpsKotaSolok",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1372",
        "satker_name": "BPS Kota Solok",
    },
    {
        "username": "KK_1373",
        "password": "bpsKotaSawahlunto",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1373",
        "satker_name": "BPS Kota Sawahlunto",
    },
    {
        "username": "KK_1374",
        "password": "bpsKotaPadangPanjang",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1374",
        "satker_name": "BPS Kota Padang Panjang",
    },
    {
        "username": "KK_1375",
        "password": "bpsKotaBukittinggi",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1375",
        "satker_name": "BPS Kota Bukittinggi",
    },
    {
        "username": "KK_1376",
        "password": "bpsKotaPayakumbuh",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1376",
        "satker_name": "BPS Kota Payakumbuh",
    },
    {
        "username": "KK_1377",
        "password": "bpsKotaPariaman",
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": "1377",
        "satker_name": "BPS Kota Pariaman",
    },
]

ALL_USERS = [ADMIN_USER] + SATKER_USERS


class Command(BaseCommand):
    help = "Seed production users for INTERMILAN application"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created without making changes",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Update existing users even if they exist",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force_update = options["force"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No changes will be made\n"))

        created_count = 0
        updated_count = 0
        skipped_count = 0

        created_users = []
        updated_users = []
        skipped_users = []

        for user_data in ALL_USERS:
            username = user_data["username"]
            existing_user = User.objects.filter(username=username).first()

            if existing_user and not force_update:
                skipped_count += 1
                skipped_users.append(username)
                continue

            if existing_user and force_update:
                # Update existing user
                existing_user.set_password(user_data["password"])
                existing_user.is_superuser = user_data.get("is_superuser", False)
                existing_user.is_staff = user_data.get("is_superuser", False)
                existing_user.save()

                # Update profile
                profile, _ = Profile.objects.get_or_create(user=existing_user)
                profile.role = user_data["role"]
                profile.satker_code = user_data.get("satker_code", "")
                profile.satker_name = user_data.get("satker_name", "")
                profile.save()

                if not dry_run:
                    updated_count += 1
                    updated_users.append(username)
                    self.stdout.write(
                        self.style.SUCCESS(f"  [UPDATED] {username} - {user_data['role']}")
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(f"  [WOULD UPDATE] {username}")
                    )
            else:
                # Create new user
                if not dry_run:
                    user = User.objects.create_user(
                        username=username,
                        password=user_data["password"],
                        is_superuser=user_data.get("is_superuser", False),
                        is_staff=user_data.get("is_superuser", False),
                    )

                    # Create or update profile
                    profile, _ = Profile.objects.get_or_create(user=user)
                    profile.role = user_data["role"]
                    profile.satker_code = user_data.get("satker_code", "")
                    profile.satker_name = user_data.get("satker_name", "")
                    profile.save()

                    created_count += 1
                    created_users.append(username)
                    self.stdout.write(
                        self.style.SUCCESS(f"  [CREATED] {username} - {user_data['role']}")
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(f"  [WOULD CREATE] {username}")
                    )

        # Summary
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS("SEED SUMMARY"))
        self.stdout.write(self.style.SUCCESS("=" * 60))

        if dry_run:
            self.stdout.write(self.style.WARNING("Mode: DRY RUN (no changes)"))
        else:
            self.stdout.write(self.style.SUCCESS(f"  Created:  {created_count} user(s)"))
            self.stdout.write(self.style.WARNING(f"  Updated:  {updated_count} user(s)"))
            self.stdout.write(self.style.WARNING(f"  Skipped:  {skipped_count} user(s)"))

        self.stdout.write("")

        if created_users:
            self.stdout.write(self.style.SUCCESS("Created accounts:"))
            for username in created_users:
                user_data = next(u for u in ALL_USERS if u["username"] == username)
                self.stdout.write(f"  - {username} ({user_data['role']})")
            self.stdout.write("")

        if updated_users:
            self.stdout.write(self.style.WARNING("Updated accounts:"))
            for username in updated_users:
                user_data = next(u for u in ALL_USERS if u["username"] == username)
                self.stdout.write(f"  - {username} ({user_data['role']})")
            self.stdout.write("")

        if skipped_users:
            self.stdout.write(self.style.NOTICE("Skipped (already exists):"))
            for username in skipped_users:
                user_data = next(u for u in ALL_USERS if u["username"] == username)
                self.stdout.write(f"  - {username} ({user_data['role']})")
            self.stdout.write("")

        self.stdout.write(self.style.SUCCESS("=" * 60))

        if dry_run:
            self.stdout.write(
                self.style.NOTICE(
                    "\nTip: Run without --dry-run to apply changes.\n"
                    "     Use --force to update existing users."
                )
            )
