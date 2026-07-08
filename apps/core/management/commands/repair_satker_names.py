from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.accounts.models import Profile
from apps.core.satker import SATKER_NAME_FALLBACKS, fallback_satker_name, normalize_satker_code
from apps.dk.models import TransactionDetail
from apps.sp2d.models import SP2DRaw


class Command(BaseCommand):
    help = "Isi nama satker kosong dari mapping resmi internal tanpa mengubah struktur database."

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true", help="Simpan perubahan. Default hanya dry-run.")

    def handle(self, *args, **options):
        commit = options["commit"]
        codes = set(SATKER_NAME_FALLBACKS)
        codes.update(normalize_satker_code(code) for code in TransactionDetail.objects.exclude(satker_code="").values_list("satker_code", flat=True))
        codes.update(normalize_satker_code(code) for code in SP2DRaw.objects.exclude(satker_code="").values_list("satker_code", flat=True))
        codes.update(normalize_satker_code(code) for code in Profile.objects.exclude(satker_code="").values_list("satker_code", flat=True))
        codes = sorted(code for code in codes if code)

        profile_updates = 0
        sp2d_updates = 0
        for code in codes:
            name = fallback_satker_name(code)
            if not name:
                continue
            profile_qs = Profile.objects.filter(satker_code=code).filter(invalid_satker_name_q("satker_name", code))
            sp2d_qs = SP2DRaw.objects.filter(satker_code=code).filter(invalid_satker_name_q("satker_name", code))
            profile_count = profile_qs.count()
            sp2d_count = sp2d_qs.count()
            profile_updates += profile_count
            sp2d_updates += sp2d_count
            if commit:
                profile_qs.update(satker_name=name)
                sp2d_qs.update(satker_name=name)

        mode = "COMMIT" if commit else "DRY-RUN"
        self.stdout.write(f"Mode: {mode}")
        self.stdout.write(f"Mapping tersedia: {len(SATKER_NAME_FALLBACKS)} satker")
        self.stdout.write(f"Profile akan diisi: {profile_updates}")
        self.stdout.write(f"SP2DRaw akan diisi: {sp2d_updates}")
        self.stdout.write("Contoh mapping:")
        for code in codes[:5]:
            self.stdout.write(f"- {code} - {fallback_satker_name(code) or '-'}")
        if not commit:
            self.stdout.write(self.style.WARNING("Jalankan dengan --commit untuk menyimpan perubahan."))
        else:
            self.stdout.write(self.style.SUCCESS("Nama satker kosong berhasil diperbaiki."))


def invalid_satker_name_q(field_name, code):
    return (
        Q(**{field_name: ""})
        | Q(**{field_name: "-"})
        | Q(**{f"{field_name}__iexact": f"bps{code}"})
        | Q(**{f"{field_name}__iexact": f"operator_{code}"})
        | Q(**{f"{field_name}__iexact": "admin"})
        | Q(**{f"{field_name}__iexact": "viewer"})
    )
