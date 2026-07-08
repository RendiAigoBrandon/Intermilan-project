"""
Management command: repair_monitoring_summary_cumulative

Hitung ulang intermilan_sd_bulan_ini dari MonitoringSummary yang sudah ada.

Logika:
  Untuk setiap (satker_code, tahun), urutkan bulan_number 1-12.
  cumulative = 0
  Untuk setiap bulan N:
      cumulative += intermilan_bulan_ini[bulan N]
      intermilan_sd_bulan_ini[bulan N] = cumulative

Tidak mengubah:
  - fa16_bulan_ini
  - D_K / TransactionDetail
  - SP2D raw / DRPP / ChecklistStatus
  - Schema / model / migration
  - Tidak membuat baris baru (hanya update field yang sudah ada)
"""
from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.models import MonitoringSummary


class Command(BaseCommand):
    help = (
        "Repair: hitung ulang intermilan_sd_bulan_ini sebagai kumulatif "
        "sum(intermilan_bulan_ini) dari bulan 1 s.d bulan N per satker per tahun. "
        "Tidak mengubah fa16, D_K, SP2D, DRPP, Checklist, atau schema database."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--tahun",
            type=int,
            default=None,
            help="Filter tahun tertentu (contoh: 2026). Default: semua tahun.",
        )
        parser.add_argument(
            "--satker-code",
            default="",
            help="Filter kode satker (contoh: 1300). Default: semua satker.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Tampilkan rencana tanpa menyimpan ke database.",
        )

    def handle(self, *args, **options):
        tahun_filter = options["tahun"]
        satker_filter = (options["satker_code"] or "").strip()
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("Mode: DRY-RUN (tidak ada yang disimpan)"))
        else:
            self.stdout.write(self.style.SUCCESS("Mode: REPAIR ASLI"))

        qs = MonitoringSummary.objects.all()
        if tahun_filter:
            qs = qs.filter(tahun=tahun_filter)
        if satker_filter:
            qs = qs.filter(satker_code=satker_filter)

        pairs = list(
            qs.values_list("satker_code", "tahun")
            .distinct()
            .order_by("satker_code", "tahun")
        )

        if not pairs:
            self.stdout.write(self.style.WARNING(
                "Tidak ada data MonitoringSummary yang cocok dengan filter."
            ))
            return

        self.stdout.write(f"Memproses {len(pairs)} kombinasi (satker x tahun)...")

        total_updated = 0
        total_unchanged = 0
        rows_to_update = []

        for satker_code, tahun in pairs:
            rows = list(
                MonitoringSummary.objects.filter(
                    satker_code=satker_code, tahun=tahun
                ).order_by("bulan_number")
            )

            cumulative = Decimal("0")
            for row in rows:
                cumulative += row.intermilan_bulan_ini or Decimal("0")
                new_sd = cumulative.quantize(Decimal("0.01"))

                if row.intermilan_sd_bulan_ini != new_sd:
                    if not dry_run:
                        row.intermilan_sd_bulan_ini = new_sd
                        rows_to_update.append(row)
                    total_updated += 1
                    if dry_run:
                        self.stdout.write(
                            f"  [{satker_code} tahun={tahun} bulan={row.bulan_number:02d}] "
                            f"sd: {row.intermilan_sd_bulan_ini} -> {new_sd}"
                        )
                else:
                    total_unchanged += 1

        if not dry_run and rows_to_update:
            with transaction.atomic():
                MonitoringSummary.objects.bulk_update(
                    rows_to_update, ["intermilan_sd_bulan_ini"], batch_size=200
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Selesai. updated={total_updated}, unchanged={total_unchanged}, "
                f"total_rows={total_updated + total_unchanged}."
            )
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY-RUN: tidak ada perubahan tersimpan."))

