from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.ocr import check_ocr_environment
from apps.core.parsers import classify_document, extract_pdf_text, parse_drpp_pdf, parse_spm_pdf


class Command(BaseCommand):
    help = "Debug OCR untuk satu PDF. Read-only."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path file PDF SPM/DRPP/KW/lampiran.")

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"File tidak ditemukan: {path}")
        if not path.is_file():
            raise CommandError(f"Path bukan file: {path}")

        env = check_ocr_environment()
        self.stdout.write("=== OCR Environment ===")
        for key, value in env.items():
            if key == "warnings":
                continue
            self.stdout.write(f"{key}: {value}")
        for warning in env.get("warnings", []):
            self.stdout.write(f"warning: {warning}")

        self.stdout.write("\n=== File ===")
        self.stdout.write(f"path: {path}")
        self.stdout.write(f"size: {path.stat().st_size}")

        probe = extract_pdf_text(str(path), ocr=True)
        doc_type = classify_document(path.name, probe.get("combined_text", ""))
        self.stdout.write("\n=== OCR Probe ===")
        self.stdout.write(f"jenis_dokumen: {doc_type}")
        self.stdout.write(f"engine_final: {probe.get('best_engine')}")
        self.stdout.write(f"status_ocr: {probe.get('status')}")
        self.stdout.write(f"engine_dicoba: {', '.join(probe.get('engines_tried') or []) or '-'}")
        self.stdout.write(f"native_text_length: {probe.get('native_text_length')}")
        self.stdout.write(f"tesseract_called: {probe.get('tesseract_called')}")
        self.stdout.write(f"tesseract_text_length: {probe.get('tesseract_text_length')}")
        self.stdout.write(f"tesseract_reason: {probe.get('tesseract_reason') or '-'}")
        self.stdout.write(f"raw_text_length_final: {len(probe.get('combined_text') or '')}")
        for warning in probe.get("warnings", []):
            self.stdout.write(f"warning: {warning}")

        if doc_type == "SPM":
            parsed = parse_spm_pdf(str(path), ocr=True)
            self.print_spm(parsed)
        elif doc_type in {"DRPP", "KW"}:
            parsed = parse_drpp_pdf(str(path), ocr=True)
            self.print_drpp(parsed, doc_type)
        else:
            self.stdout.write("\n=== Parsed Metadata ===")
            self.stdout.write("Dokumen tidak dikenali sebagai SPM/DRPP/KW. File diperlakukan sebagai Lampiran/Unknown.")
            self.stdout.write(f"cuplikan: {(probe.get('combined_text') or '')[:500]}")

    def print_spm(self, parsed):
        meta = parsed.get("metadata", {})
        is_combined = parsed.get("is_combined_package", False)
        self.stdout.write("\n=== Parsed SPM ===")
        self.stdout.write(f"status: {parsed.get('status')}")
        self.stdout.write(f"engine_final: {parsed.get('best_engine')}")
        self.stdout.write(f"is_combined_package: {is_combined}")
        self.stdout.write(f"No SPM: {meta.get('nomor_spm') or '-'}")
        self.stdout.write(f"No SPP: {meta.get('nomor_spp') or '-'}")
        self.stdout.write(f"No DRPP: {meta.get('nomor_drpp') or '-'}")
        self.stdout.write(f"Satker: {meta.get('satker_code') or '-'}")
        self.stdout.write(f"Tanggal SPM: {meta.get('tanggal_spm') or '-'}")
        self.stdout.write(f"Jenis SPM: {meta.get('jenis_spm') or '-'}")
        self.stdout.write(f"Nilai (total_pembayaran): {meta.get('total_pembayaran') or '-'}")
        self.stdout.write(f"Jumlah Pengeluaran: {meta.get('jumlah_pengeluaran') or '-'}")
        self.stdout.write(f"Jumlah Potongan: {meta.get('jumlah_potongan') or '-'}")
        self.stdout.write(f"Akun: {', '.join(row.get('akun', '') for row in parsed.get('akun_rows', []) if row.get('akun')) or '-'}")
        self.stdout.write(f"Uraian: {meta.get('uraian') or '-'}")
        self.stdout.write("\n=== Nomor SPM Resolution ===")
        self.stdout.write(f"filename: {parsed.get('file_name') or '-'}")
        self.stdout.write(f"no_spm_from_filename: {meta.get('nomor_spm_filename') or '-'}")
        self.stdout.write(f"no_spm_from_ocr (per-halaman SPM): {meta.get('nomor_spm_ocr') or '-'}")
        self.stdout.write(f"no_spp_from_ocr (per-halaman SPP): {meta.get('nomor_spp') or '-'}")
        self.stdout.write(f"no_spp_per_page: {meta.get('nomor_spp_per_page') or '-'}")
        self.stdout.write(f"spm_pages: {meta.get('spm_page_nums') or '-'}")
        self.stdout.write(f"spp_pages: {meta.get('spp_page_nums') or '-'}")
        self.stdout.write(f"no_spm_final: {meta.get('nomor_spm_final') or meta.get('nomor_spm') or '-'}")
        self.stdout.write(f"final_source: {meta.get('nomor_spm_final_source') or '-'}")
        self.stdout.write(f"conflict: {meta.get('nomor_spm_conflict')}")
        self.stdout.write(f"status_review: {meta.get('nomor_spm_review_status') or '-'}")
        self.stdout.write(f"reason: {meta.get('nomor_spm_reason') or '-'}")
        for warning in parsed.get("warnings", []):
            self.stdout.write(f"warning: {warning}")

    def print_drpp(self, parsed, doc_type):
        meta = parsed.get("metadata", {})
        self.stdout.write(f"\n=== Parsed {doc_type} ===")
        self.stdout.write(f"status: {parsed.get('status')}")
        self.stdout.write(f"engine_final: {parsed.get('best_engine')}")
        self.stdout.write(f"No DRPP: {meta.get('nomor_drpp') or '-'}")
        self.stdout.write(f"No SPM: {meta.get('nomor_spm') or '-'}")
        self.stdout.write(f"Total: {meta.get('total') or '-'}")
        self.stdout.write(f"Item terbaca: {len(parsed.get('items', []))}")
        for item in parsed.get("items", [])[:20]:
            self.stdout.write(
                "  - "
                f"KW={item.get('no_bukti') or '-'}; "
                f"akun={item.get('akun') or '-'}; "
                f"jumlah={item.get('jumlah') or '-'}; "
                f"keperluan={item.get('keperluan') or '-'}"
            )
        for warning in parsed.get("warnings", []):
            self.stdout.write(f"warning: {warning}")
