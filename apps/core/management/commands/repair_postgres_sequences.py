from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Repair PostgreSQL sequences agar next id mengikuti MAX(pk). Default dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true", help="Update sequence PostgreSQL.")
        parser.add_argument("--dry-run", action="store_true", help="Tampilkan rencana tanpa update.")

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stdout.write(self.style.WARNING(f"Database vendor {connection.vendor}; command hanya untuk PostgreSQL."))
            return

        commit = options["commit"]
        self.stdout.write(self.style.WARNING("Mode: COMMIT") if commit else self.style.WARNING("Mode: DRY-RUN"))
        self.stdout.write("table | sequence | max_id | next_id | status")
        self.stdout.write("-" * 100)

        updated = 0
        skipped = 0
        with connection.cursor() as cursor:
            for model in apps.get_models():
                pk = model._meta.pk
                if not pk or pk.get_internal_type() not in {"AutoField", "BigAutoField", "SmallAutoField"}:
                    skipped += 1
                    continue

                table = model._meta.db_table
                pk_column = pk.column
                cursor.execute("SELECT pg_get_serial_sequence(%s, %s)", [table, pk_column])
                row = cursor.fetchone()
                sequence = row[0] if row else None
                if not sequence:
                    skipped += 1
                    self.stdout.write(f"{table} | - | - | - | skipped:no_sequence")
                    continue

                cursor.execute(f'SELECT COALESCE(MAX("{pk_column}"), 0) FROM "{table}"')
                max_id = cursor.fetchone()[0] or 0
                cursor.execute("SELECT last_value, is_called FROM " + sequence)
                last_value, is_called = cursor.fetchone()
                next_id = (last_value + 1) if is_called else last_value
                expected_next_id = max_id + 1 if max_id else 1
                status = "ok"

                if next_id != expected_next_id:
                    status = "would_update"
                    if commit:
                        if max_id:
                            cursor.execute("SELECT setval(%s, %s, true)", [sequence, max_id])
                        else:
                            cursor.execute("SELECT setval(%s, 1, false)", [sequence])
                        status = "updated"
                        updated += 1
                else:
                    skipped += 1
                self.stdout.write(f"{table} | {sequence} | {max_id} | {next_id} | {status}")

        if commit:
            self.stdout.write(self.style.SUCCESS(f"Sequence repair selesai. Updated={updated}, skipped={skipped}."))
        else:
            self.stdout.write(self.style.SUCCESS("Dry-run selesai. Jalankan --commit untuk memperbaiki sequence."))
