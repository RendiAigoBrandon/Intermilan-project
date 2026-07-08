from django.core.management import BaseCommand

from apps.drpp.models import DRPPItem, DRPPMatch, DRPPUpload


class Command(BaseCommand):
    help = "Cleanup DRPP upload testing data. Default dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true")

    def handle(self, *args, **options):
        counts = {
            "DRPPMatch": DRPPMatch.objects.count(),
            "DRPPItem": DRPPItem.objects.count(),
            "DRPPUpload": DRPPUpload.objects.count(),
        }
        for name, total in counts.items():
            self.stdout.write(f"{name}: {total}")
        if not options["commit"]:
            self.stdout.write(self.style.WARNING("Dry-run. Tambahkan --commit untuk menghapus data DRPP testing."))
            return
        DRPPMatch.objects.all().delete()
        DRPPItem.objects.all().delete()
        DRPPUpload.objects.all().delete()
        self.stdout.write(self.style.SUCCESS("Data DRPP testing dibersihkan."))
