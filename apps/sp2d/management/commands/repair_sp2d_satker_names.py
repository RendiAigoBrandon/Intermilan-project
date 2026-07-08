from django.core.management.base import BaseCommand

from apps.core.satker import infer_satker_from_name
from apps.sp2d.models import SP2DRaw


class Command(BaseCommand):
    help = "Repair satker_code/satker_name SP2DRaw dari nama satker Excel. Default dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        commit = options["commit"]
        rows = SP2DRaw.objects.filter(satker_code="").exclude(satker_name="")
        self.stdout.write(self.style.WARNING("Mode: COMMIT") if commit else self.style.WARNING("Mode: DRY-RUN"))
        self.stdout.write(f"Kandidat repair: {rows.count()}")
        updated = 0
        skipped = 0
        for row in rows.order_by("id"):
            code, name = infer_satker_from_name(row.satker_name)
            if not code:
                skipped += 1
                self.stdout.write(f"SKIP #{row.id}: {row.satker_name}")
                continue
            self.stdout.write(f"REPAIR #{row.id}: '{row.satker_name}' -> {code} - {name}")
            if commit:
                row.satker_code = code
                row.satker_name = name or row.satker_name
                row.save(update_fields=["satker_code", "satker_name", "updated_at"])
            updated += 1
        self.stdout.write(self.style.SUCCESS(f"Selesai. matched={updated}, skipped={skipped}."))
