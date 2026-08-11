"""
Management command to seed/update SatkerMaster with authoritative BPS unit -> satker mapping.

This command safely seeds the SatkerMaster table with the official mapping between
4-digit BPS unit codes (from filenames like KK_1300.xlsx) and 6-digit official
financial satker codes.

Usage:
    python manage.py seed_satker_master

The command is idempotent - running it multiple times is safe and will not create duplicates.
"""

from django.core.management.base import BaseCommand

from apps.core.models import SatkerMaster


# Authoritative mapping from 4-digit unit_code to 6-digit official satker_code
# Source: Actual KK source dataset
UNIT_TO_SATKER_MAPPING = {
    "1300": {"nama_satker": "BPS Provinsi Sumatera Barat", "satker_code": "019937", "jenis_unit": "PROVINSI"},
    "1301": {"nama_satker": "BPS Kabupaten Kepulauan Mentawai", "satker_code": "636977", "jenis_unit": "KABUPATEN"},
    "1302": {"nama_satker": "BPS Kabupaten Pesisir Selatan", "satker_code": "427981", "jenis_unit": "KABUPATEN"},
    "1303": {"nama_satker": "BPS Kabupaten Solok", "satker_code": "019979", "jenis_unit": "KABUPATEN"},
    "1304": {"nama_satker": "BPS Kabupaten Sijunjung", "satker_code": "019983", "jenis_unit": "KABUPATEN"},
    "1305": {"nama_satker": "BPS Kabupaten Tanah Datar", "satker_code": "019990", "jenis_unit": "KABUPATEN"},
    "1306": {"nama_satker": "BPS Kabupaten Padang Pariaman", "satker_code": "019958", "jenis_unit": "KABUPATEN"},
    "1307": {"nama_satker": "BPS Kabupaten Agam", "satker_code": "428041", "jenis_unit": "KABUPATEN"},
    "1308": {"nama_satker": "BPS Kabupaten Lima Puluh Kota", "satker_code": "428063", "jenis_unit": "KABUPATEN"},
    "1309": {"nama_satker": "BPS Kabupaten Pasaman", "satker_code": "428057", "jenis_unit": "KABUPATEN"},
    "1310": {"nama_satker": "BPS Kabupaten Solok Selatan", "satker_code": "667193", "jenis_unit": "KABUPATEN"},
    "1311": {"nama_satker": "BPS Kabupaten Dharmasraya", "satker_code": "667172", "jenis_unit": "KABUPATEN"},
    "1312": {"nama_satker": "BPS Kabupaten Pasaman Barat", "satker_code": "667189", "jenis_unit": "KABUPATEN"},
    "1371": {"nama_satker": "BPS Kota Padang", "satker_code": "019941", "jenis_unit": "KOTA"},
    "1372": {"nama_satker": "BPS Kota Solok", "satker_code": "019962", "jenis_unit": "KOTA"},
    "1373": {"nama_satker": "BPS Kota Sawahlunto", "satker_code": "428001", "jenis_unit": "KOTA"},
    "1374": {"nama_satker": "BPS Kota Padang Panjang", "satker_code": "427990", "jenis_unit": "KOTA"},
    "1375": {"nama_satker": "BPS Kota Bukittinggi", "satker_code": "428026", "jenis_unit": "KOTA"},
    "1376": {"nama_satker": "BPS Kota Payakumbuh", "satker_code": "428032", "jenis_unit": "KOTA"},
    "1377": {"nama_satker": "BPS Kota Pariaman", "satker_code": "668512", "jenis_unit": "KOTA"},
}


class Command(BaseCommand):
    help = "Seed/update SatkerMaster with authoritative BPS unit -> satker code mapping"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created/updated without making changes",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        created_count = 0
        updated_count = 0
        unchanged_count = 0

        self.stdout.write(f"Processing {len(UNIT_TO_SATKER_MAPPING)} unit -> satker mappings...")

        for unit_code, data in sorted(UNIT_TO_SATKER_MAPPING.items()):
            nama_satker = data["nama_satker"]
            satker_code = data["satker_code"]
            jenis_unit = data.get("jenis_unit", "")

            try:
                satker = SatkerMaster.objects.get(unit_code=unit_code)

                # Check if any field needs updating
                needs_update = (
                    satker.nama_satker != nama_satker or
                    satker.satker_code != satker_code or
                    satker.jenis_unit != jenis_unit
                )

                if needs_update:
                    if dry_run:
                        self.stdout.write(
                            f"  [DRY-RUN] Would update: {unit_code} -> {satker_code} ({nama_satker})"
                        )
                    else:
                        satker.nama_satker = nama_satker
                        satker.satker_code = satker_code
                        satker.jenis_unit = jenis_unit
                        satker.save(update_fields=["nama_satker", "satker_code", "jenis_unit", "updated_at"])
                        updated_count += 1
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"  Updated: {unit_code} -> {satker_code} ({nama_satker})"
                            )
                        )
                else:
                    unchanged_count += 1
                    self.stdout.write(
                        f"  Unchanged: {unit_code} -> {satker_code} ({nama_satker})"
                    )

            except SatkerMaster.DoesNotExist:
                if dry_run:
                    self.stdout.write(
                        f"  [DRY-RUN] Would create: {unit_code} -> {satker_code} ({nama_satker})"
                    )
                else:
                    SatkerMaster.objects.create(
                        unit_code=unit_code,
                        nama_satker=nama_satker,
                        satker_code=satker_code,
                        jenis_unit=jenis_unit,
                    )
                    created_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  Created: {unit_code} -> {satker_code} ({nama_satker})"
                        )
                    )

        # Summary
        total = SatkerMaster.objects.count()
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 60))

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes were made"))
            self.stdout.write(f"  Would create: {created_count}")
            self.stdout.write(f"  Would update: {updated_count}")
            self.stdout.write(f"  Would leave unchanged: {unchanged_count}")
        else:
            self.stdout.write("SatkerMaster seeding complete!")
            self.stdout.write(f"  Created: {created_count}")
            self.stdout.write(f"  Updated: {updated_count}")
            self.stdout.write(f"  Unchanged: {unchanged_count}")
            self.stdout.write(f"  Total records in database: {total}")

        self.stdout.write("=" * 60)
