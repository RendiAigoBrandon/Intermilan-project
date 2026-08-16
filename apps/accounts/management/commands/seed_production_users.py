"""
Management command to seed production users for INTERMILAN application.

Passwords are read from Environment Variables (Coolify).

Usage:
    # Default: create users, preserve existing passwords
    python manage.py seed_production_users

    # Sync passwords from Environment Variables
    python manage.py seed_production_users --sync-passwords

    # Dry-run preview
    python manage.py seed_production_users --dry-run

Environment Variables Required (Coolify):
    ADMIN_USERNAME=admin
    ADMIN_PASSWORD=<password>
    SATKER_1300_USERNAME=KK_1300
    SATKER_1300_PASSWORD=<password>
    SATKER_1301_USERNAME=KK_1301
    SATKER_1301_PASSWORD=<password>
    ... (all 21 satkers)

All passwords are hashed using Django's set_password() before storage.
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.accounts.models import Profile


User = get_user_model()

# SATKER unit codes
SATKER_CODES = [
    "1300", "1301", "1302", "1303", "1304", "1305", "1306", "1307", "1308",
    "1309", "1310", "1311", "1312", "1371", "1372", "1373", "1374", "1375",
    "1376", "1377",
]


def get_admin_config():
    """Read admin credentials from Environment Variables."""
    username = os.environ.get("ADMIN_USERNAME", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not username or not password:
        return None
    return {
        "username": username,
        "password": password,
        "role": Profile.Role.ADMIN_PUSAT,
        "is_superuser": True,
        "satker_code": "",
        "satker_name": "",
    }


def get_satker_config(satker_code):
    """Read SATKER credentials from Environment Variables."""
    username = os.environ.get(f"SATKER_{satker_code}_USERNAME", "").strip()
    password = os.environ.get(f"SATKER_{satker_code}_PASSWORD", "").strip()
    if not username or not password:
        return None
    return {
        "username": username,
        "password": password,
        "role": Profile.Role.SATKER,
        "is_superuser": False,
        "satker_code": satker_code,
        "satker_name": get_satker_name(satker_code),
    }


def get_satker_name(satker_code):
    """Get SATKER name from satker code."""
    names = {
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
    return names.get(satker_code, f"SATKER {satker_code}")


class Command(BaseCommand):
    help = "Seed production users. Passwords from Environment Variables."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created/updated without making changes",
        )
        parser.add_argument(
            "--sync-passwords",
            action="store_true",
            help="Sync passwords from Environment Variables (overwrites existing passwords)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Update existing users even if they exist (profile only, not password)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        sync_passwords = options["sync_passwords"]
        force_update = options["force"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No changes will be made\n"))

        if sync_passwords:
            self.stdout.write(self.style.WARNING("SYNC PASSWORDS MODE - Passwords will be overwritten\n"))

        # Build user configs from Environment Variables
        user_configs = []

        # Admin
        admin_config = get_admin_config()
        if admin_config:
            user_configs.append(admin_config)
        else:
            self.stdout.write(self.style.ERROR("WARNING: ADMIN_USERNAME or ADMIN_PASSWORD not set in Environment Variables"))
            self.stdout.write("")

        # SATKER users
        missing_satker = []
        for code in SATKER_CODES:
            satker_config = get_satker_config(code)
            if satker_config:
                user_configs.append(satker_config)
            else:
                missing_satker.append(code)

        if missing_satker:
            self.stdout.write(self.style.ERROR(f"WARNING: Missing Environment Variables for SATKER: {', '.join(missing_satker)}"))
            self.stdout.write("")

        if not user_configs:
            self.stdout.write(self.style.ERROR("ERROR: No users configured. Please set Environment Variables in Coolify."))
            self.stdout.write("")
            self.stdout.write("Required Environment Variables:")
            self.stdout.write("  ADMIN_USERNAME")
            self.stdout.write("  ADMIN_PASSWORD")
            for code in SATKER_CODES:
                self.stdout.write(f"  SATKER_{code}_USERNAME")
                self.stdout.write(f"  SATKER_{code}_PASSWORD")
            return

        # Process users
        created_count = 0
        updated_count = 0
        password_synced_count = 0
        skipped_count = 0

        created_users = []
        updated_users = []
        password_synced_users = []
        skipped_users = []

        for user_data in user_configs:
            username = user_data["username"]
            existing_user = User.objects.filter(username=username).first()

            if existing_user and not force_update and not sync_passwords:
                # User exists, skip (preserve existing)
                skipped_count += 1
                skipped_users.append(username)
                continue

            if existing_user:
                # Update existing user
                if sync_passwords:
                    # Sync password from Environment Variable
                    existing_user.set_password(user_data["password"])
                    existing_user.save()
                    password_synced_users.append(username)
                    password_synced_count += 1
                    if not dry_run:
                        # Verify password was set correctly
                        existing_user.refresh_from_db()
                        password_ok = existing_user.check_password(user_data["password"])
                        self.stdout.write(
                            self.style.SUCCESS(f"  [PASSWORD SYNCED] {username}")
                        )
                        self.stdout.write(
                            f"  [VERIFY] {username} active={existing_user.is_active} password_ok={password_ok}"
                        )

                # Update profile
                profile, _ = Profile.objects.get_or_create(user=existing_user)
                profile.role = user_data["role"]
                profile.satker_code = user_data.get("satker_code", "")
                profile.satker_name = user_data.get("satker_name", "")
                profile.save()

                if not dry_run and not sync_passwords:
                    updated_count += 1
                    updated_users.append(username)
                    self.stdout.write(
                        self.style.WARNING(f"  [UPDATED] {username} - {user_data['role']}")
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

                    # Create profile
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
            self.stdout.write(self.style.SUCCESS(f"  Created:     {created_count} user(s)"))
            self.stdout.write(self.style.WARNING(f"  Updated:    {updated_count} user(s)"))
            if sync_passwords:
                self.stdout.write(self.style.WARNING(f"  Passwords:   {password_synced_count} user(s)"))
            self.stdout.write(self.style.WARNING(f"  Skipped:    {skipped_count} user(s)"))
            self.stdout.write(self.style.NOTICE(f"  Total:      {User.objects.count()} user(s) in database"))

        self.stdout.write("")

        if created_users:
            self.stdout.write(self.style.SUCCESS("Created accounts:"))
            for username in created_users:
                user_data = next(u for u in user_configs if u["username"] == username)
                self.stdout.write(f"  - {username} ({user_data['role']})")
            self.stdout.write("")

        if updated_users:
            self.stdout.write(self.style.WARNING("Updated accounts (profile only):"))
            for username in updated_users:
                user_data = next(u for u in user_configs if u["username"] == username)
                self.stdout.write(f"  - {username} ({user_data['role']})")
            self.stdout.write("")

        if password_synced_users:
            self.stdout.write(self.style.WARNING("Passwords synced from Environment Variables:"))
            for username in password_synced_users:
                self.stdout.write(f"  - {username}")
            self.stdout.write("")

        if skipped_users:
            self.stdout.write(self.style.NOTICE("Skipped (already exists, no --force or --sync-passwords):"))
            for username in skipped_users:
                user_data = next(u for u in user_configs if u["username"] == username)
                self.stdout.write(f"  - {username} ({user_data['role']})")
            self.stdout.write("")

        self.stdout.write(self.style.SUCCESS("=" * 60))

        if dry_run:
            self.stdout.write(
                self.style.NOTICE(
                    "\nTip: Run without --dry-run to apply changes.\n"
                    "     Use --force to update existing users.\n"
                    "     Use --sync-passwords to sync passwords from Environment Variables."
                )
            )
        else:
            self.stdout.write(
                self.style.NOTICE(
                    "\nTo sync passwords after changing Environment Variables:\n"
                    "  python manage.py seed_production_users --sync-passwords\n"
                )
            )
