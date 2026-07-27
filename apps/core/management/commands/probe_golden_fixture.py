import json
import shutil
import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings

from apps.core.golden_runner import cache_snapshot, probe_fixture, remove_new_cache_files


class Command(BaseCommand):
    help = "Probe read-only satu fixture golden PDF/ZIP dan hapus cache yang dibuat oleh run."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path fixture PDF atau ZIP.")
        parser.add_argument("--no-ocr", action="store_true", help="Jalankan probe native-text saja.")
        parser.add_argument("--measure-cache", action="store_true", help="Jalankan cold lalu cache-hit probe.")
        parser.add_argument("--isolated-cache", action="store_true", help="Salin fixture dan gunakan MEDIA_ROOT sementara.")
        parser.add_argument("--summary", action="store_true", help="Sembunyikan detail baris teredaksi.")
        parser.add_argument("--group", help="Batasi tampilan report ke satu nomor DRPP untuk diagnosis.")

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.is_file():
            raise CommandError(f"Fixture tidak ditemukan: {path}")
        temp_dir = None
        run_path = path
        setting_context = None
        try:
            if options["isolated_cache"]:
                temp_dir = Path(tempfile.mkdtemp(prefix="golden_probe_"))
                run_path = temp_dir / path.name
                shutil.copy2(path, run_path)
                setting_context = override_settings(MEDIA_ROOT=str(temp_dir / "media"))
                setting_context.enable()

            if options["measure_cache"]:
                before = cache_snapshot(run_path)
                report = probe_fixture(run_path, ocr=not options["no_ocr"], cleanup_cache=False)
                cache_run = probe_fixture(run_path, ocr=not options["no_ocr"], cleanup_cache=False)
                removed = remove_new_cache_files(run_path, before)
                report["cache_run"] = {
                    "metrics": cache_run.get("metrics") or {},
                    "transaction_count": cache_run.get("transaction_count"),
                    "total_nominal": cache_run.get("total_nominal"),
                }
            else:
                report = probe_fixture(run_path, ocr=not options["no_ocr"])
                removed = probe_fixture.last_removed_cache_files
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        finally:
            if setting_context:
                setting_context.disable()
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

        if options.get("group"):
            selected = options["group"].strip().upper()
            report["groups"] = [group for group in report.get("groups", []) if str(group.get("no_drpp", "")).upper() == selected]
            report["rows"] = [
                row for row in report.get("rows", [])
                if str((row.get("columns", {}).get("no_drpp") or {}).get("value") or "").upper() == selected
            ]
            report["enrichment_rows"] = [
                row for row in report.get("enrichment_rows", [])
                if str((row.get("columns", {}).get("no_drpp") or {}).get("value") or "").upper() == selected
            ]
        if options["summary"]:
            report["row_count_reported"] = len(report.pop("rows", []))
            report["enrichment_row_count_reported"] = len(report.pop("enrichment_rows", []))
            report["warning_count"] = len(report.pop("warnings", []))
        report["cache_cleanup"] = {"new_files_removed": len(removed)}
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
