from django.core.management.base import BaseCommand, CommandError

from apps.core.monitoring_summary import refresh_monitoring_summary


class Command(BaseCommand):
    help = "Refresh MonitoringSummary dari data web yang dapat dihitung."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Refresh semua row MonitoringSummary.")
        parser.add_argument("--tahun", type=int, default=None, help="Filter tahun.")
        parser.add_argument("--bulan", type=int, default=None, help="Filter bulan angka 1-12.")
        parser.add_argument("--satker-code", default="", help="Filter kode satker, contoh 1300.")

    def handle(self, *args, **options):
        bulan = options["bulan"]
        if bulan and not 1 <= bulan <= 12:
            raise CommandError("--bulan harus 1 sampai 12.")
        if not options["all"] and not any([options["tahun"], bulan, options["satker_code"]]):
            raise CommandError("Gunakan --all atau filter --tahun/--bulan/--satker-code.")
        count = refresh_monitoring_summary(
            tahun=options["tahun"],
            bulan=bulan,
            satker_code=options["satker_code"] or None,
        )
        self.stdout.write(self.style.SUCCESS(f"MonitoringSummary refreshed: {count} row."))
