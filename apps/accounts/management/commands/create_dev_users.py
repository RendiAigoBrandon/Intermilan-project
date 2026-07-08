from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Profile
from apps.core.models import MonitoringSummary
from apps.core.satker import SATKER_NAME_FALLBACKS, fallback_satker_name, get_satker_name_map, normalize_satker_code
from apps.dk.models import TransactionDetail


class Command(BaseCommand):
    help = "Create safe development users for INTERMILAN role testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default="IntermilanDev123!",
            help="Temporary development password. Change before real use.",
        )
        parser.add_argument(
            "--all-satker",
            action="store_true",
            help="Buat operator untuk semua satker aktif dari D_K/MonitoringSummary.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("create_dev_users hanya boleh dijalankan saat DEBUG=True.")

        password = options["password"]
        users = self.build_users(options["all_satker"])
        User = get_user_model()
        created_count = 0
        updated_count = 0
        for username, role, satker_code, satker_name, is_staff, is_superuser in users:
            user, created = User.objects.get_or_create(username=username)
            user.set_password(password)
            user.is_staff = is_staff
            user.is_superuser = is_superuser
            user.is_active = True
            user.save()
            profile, _ = Profile.objects.get_or_create(user=user)
            profile.role = role
            profile.satker_code = satker_code
            profile.satker_name = satker_name
            profile.must_change_password = created
            profile.save()
            action = "created" if created else "updated"
            created_count += 1 if created else 0
            updated_count += 0 if created else 1
            self.stdout.write(f"{username}: {action} ({role}, satker={satker_code or '-'})")

        self.stdout.write("")
        self.stdout.write(f"Created: {created_count}")
        self.stdout.write(f"Updated/existing: {updated_count}")
        self.stdout.write(f"Total managed this run: {len(users)}")
        self.stdout.write(f"Total user akhir: {User.objects.count()}")
        self.stdout.write("Contoh operator:")
        for username, _, satker_code, satker_name, *_ in [user for user in users if user[0].startswith("operator_")][:5]:
            self.stdout.write(f"- {username}: {satker_code} - {satker_name}")
        self.stdout.write(self.style.SUCCESS("Dev users siap. Password default development sudah diset ulang sesuai parameter."))

    def build_users(self, all_satker):
        users = [
            ("admin", Profile.Role.ADMIN_PUSAT, "", "Administrator Lokal", True, True),
            ("viewer", Profile.Role.VIEWER, "", "Viewer", False, False),
        ]
        satkers = self.get_all_satkers() if all_satker else {"1300": SATKER_NAME_FALLBACKS["1300"]}
        for code in sorted(satkers):
            users.append((f"operator_{code}", Profile.Role.SATKER, code, satkers.get(code) or "-", False, False))
        return users

    def get_all_satkers(self):
        satkers = dict(SATKER_NAME_FALLBACKS)
        known_names = get_satker_name_map()
        for item in MonitoringSummary.objects.exclude(satker_code="").values("satker_code", "satker_label").distinct():
            code = normalize_satker_code(item["satker_code"])
            satkers[code] = known_names.get(code) or fallback_satker_name(code) or item["satker_label"] or "-"
        for code in TransactionDetail.objects.exclude(satker_code="").values_list("satker_code", flat=True).distinct():
            code = normalize_satker_code(code)
            satkers.setdefault(code, known_names.get(code) or fallback_satker_name(code) or "-")
        return satkers
