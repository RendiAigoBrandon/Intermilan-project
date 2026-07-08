import tempfile
import zipfile
import os
import shutil
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.parsers import parse_paket_spm_zip


class Command(BaseCommand):
    help = "Debug OCR paket dokumen dari ZIP atau folder. Read-only."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path ZIP atau folder berisi PDF.")

    def handle(self, *args, **options):
        source = Path(options["path"])
        if not source.exists():
            raise CommandError(f"Path tidak ditemukan: {source}")

        cleanup_zip = None
        if source.is_dir():
            cleanup_zip = self.make_zip_from_folder(source)
            zip_path = cleanup_zip
        elif source.is_file() and source.suffix.lower() == ".zip":
            zip_path = source
        else:
            raise CommandError("Path harus berupa folder atau ZIP.")

        parsed = None
        try:
            parsed = parse_paket_spm_zip(str(zip_path), ocr=True)
            self.print_package(parsed, source)
        finally:
            if parsed and parsed.get("temp_dir"):
                shutil.rmtree(parsed["temp_dir"], ignore_errors=True)
            if cleanup_zip and cleanup_zip.exists():
                cleanup_zip.unlink(missing_ok=True)

    def make_zip_from_folder(self, folder):
        fd, name = tempfile.mkstemp(prefix="intermilan_debug_package_", suffix=".zip")
        os.close(fd)
        Path(name).unlink(missing_ok=True)
        with zipfile.ZipFile(name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path in folder.rglob("*"):
                if file_path.is_file() and file_path.suffix.lower() == ".pdf":
                    archive.write(file_path, file_path.relative_to(folder).as_posix())
        return Path(name)

    def print_package(self, parsed, source):
        self.stdout.write("=== OCR Package Debug ===")
        self.stdout.write(f"source: {source}")
        self.stdout.write(f"jumlah_file: {len(parsed.get('files', []))}")
        self.stdout.write(f"spm_terbaca: {1 if parsed.get('spm') else 0}")
        self.stdout.write(f"drpp_terbaca: {len(parsed.get('drpps', []))}")
        self.stdout.write(f"kw_item_terbaca: {len(parsed.get('kw_items', []))}")
        for warning in parsed.get("warnings", []):
            self.stdout.write(f"warning: {warning}")

        self.stdout.write("\n=== Klasifikasi File ===")
        for item in parsed.get("files", []):
            self.stdout.write(
                f"- {item.get('file_name')}: "
                f"type={item.get('type')}; "
                f"status={item.get('parse_status') or item.get('status')}; "
                f"method={item.get('method') or '-'}; "
                f"warning={'; '.join(item.get('warnings') or []) or '-'}"
            )

        spm = parsed.get("spm")
        if spm:
            meta = spm.get("metadata", {})
            self.stdout.write("\n=== SPM Utama ===")
            self.stdout.write(f"No SPM: {meta.get('nomor_spm') or '-'}")
            self.stdout.write(f"Jenis: {meta.get('jenis_spm') or '-'}")
            self.stdout.write(f"Nilai: {meta.get('total_pembayaran') or '-'}")
            self.stdout.write(f"Engine: {spm.get('best_engine')}; status={spm.get('status')}")

        self.stdout.write("\n=== DRPP Terbaca ===")
        for drpp in parsed.get("drpps", []):
            meta = drpp.get("metadata", {})
            self.stdout.write(
                f"- DRPP={meta.get('nomor_drpp') or '-'}; "
                f"SPM={meta.get('nomor_spm') or '-'}; "
                f"items={len(drpp.get('items', []))}; "
                f"total={meta.get('total') or '-'}; "
                f"engine={drpp.get('best_engine')}; status={drpp.get('status')}"
            )

        self.stdout.write("\n=== Grouping KW per DRPP ===")
        for no_drpp, items in (parsed.get("kw_by_drpp") or {}).items():
            self.stdout.write(f"- {no_drpp or 'TANPA_DRPP'}: {len(items)} item")
            for item in items[:20]:
                self.stdout.write(
                    "  * "
                    f"KW={item.get('no_bukti') or '-'}; "
                    f"akun={item.get('akun') or '-'}; "
                    f"jumlah={item.get('jumlah') or '-'}; "
                    f"file={item.get('source_file') or '-'}"
                )
