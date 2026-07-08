from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.dk.models import TransactionDetail
from apps.sp2d.models import SP2DRaw
from apps.core.views import month_name


class Command(BaseCommand):
    help = "Inspect active SQLite data for filter diagnostics. Read-only."

    def handle(self, *args, **options):
        self.stdout.write("INTERMILAN active data diagnostic")
        self.stdout.write(f"SP2DRaw total: {SP2DRaw.objects.count()}")
        self.stdout.write(f"TransactionDetail total: {TransactionDetail.objects.count()}")
        self.print_distinct("SP2DRaw satker", SP2DRaw.objects.exclude(satker_code="").values_list("satker_code", flat=True).distinct().order_by("satker_code"))
        self.print_distinct("D_K satker", TransactionDetail.objects.exclude(satker_code="").values_list("satker_code", flat=True).distinct().order_by("satker_code"))
        self.print_months("SP2DRaw bulan_sp2d", SP2DRaw.objects.exclude(bulan_sp2d__isnull=True).values_list("bulan_sp2d", flat=True).distinct().order_by("bulan_sp2d"))
        self.print_months("D_K bulan_sp2d", TransactionDetail.objects.exclude(bulan_sp2d__isnull=True).values_list("bulan_sp2d", flat=True).distinct().order_by("bulan_sp2d"))
        self.print_counts("D_K per satker", TransactionDetail.objects.values("satker_code").annotate(total=Count("id")).order_by("satker_code"))
        self.print_counts("D_K per bulan", TransactionDetail.objects.values("bulan_sp2d").annotate(total=Count("id")).order_by("bulan_sp2d"), month=True)
        self.print_counts("SP2D per satker", SP2DRaw.objects.values("satker_code").annotate(total=Count("id")).order_by("satker_code"))
        self.print_counts("SP2D per bulan", SP2DRaw.objects.values("bulan_sp2d").annotate(total=Count("id")).order_by("bulan_sp2d"), month=True)

    def print_distinct(self, label, values):
        self.stdout.write(f"{label}: {', '.join(str(value) for value in values) or '-'}")

    def print_months(self, label, values):
        self.stdout.write(f"{label}: {', '.join(f'{value} ({month_name(value)})' for value in values) or '-'}")

    def print_counts(self, label, rows, month=False):
        self.stdout.write(label + ":")
        for row in rows:
            key = row["bulan_sp2d"] if month else row["satker_code"]
            if month:
                key = f"{key} ({month_name(key)})"
            self.stdout.write(f"  - {key or '-'}: {row['total']}")
