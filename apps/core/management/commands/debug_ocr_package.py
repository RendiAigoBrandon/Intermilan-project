"""debug_ocr_package — Debug OCR untuk folder atau ZIP Paket SPM. Read-only."""

import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.ocr import check_ocr_environment
from apps.core.parsers import (
    classify_document,
    extract_pdf_text,
    parse_drpp_pdf,
    parse_spm_pdf,
)
from apps.paket_spm.services import evaluate_document_status, package_metadata


class Command(BaseCommand):
    help = "Debug OCR untuk folder atau ZIP Paket SPM (multi-file). Read-only."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path folder atau file ZIP Paket SPM.")
        parser.add_argument(
            "--no-ocr", action="store_true", default=False,
            help="Gunakan text-only (tanpa Tesseract/PaddleOCR). Default: OCR aktif."
        )

    def handle(self, *args, **options):
        raw_path = Path(options["path"])
        use_ocr = not options["no_ocr"]
        temp_dir = None

        try:
            if raw_path.is_file() and raw_path.suffix.lower() == ".zip":
                self.stdout.write(f"[ZIP] Membuka: {raw_path}")
                temp_dir = tempfile.mkdtemp(prefix="debug_paket_")
                with zipfile.ZipFile(raw_path) as z:
                    z.extractall(temp_dir)
                folder = Path(temp_dir)
            elif raw_path.is_dir():
                folder = raw_path
            else:
                raise CommandError(f"Path harus berupa folder atau file ZIP: {raw_path}")

            self.stdout.write(f"\nFolder: {folder}")
            self.stdout.write(f"OCR aktif: {use_ocr}")

            env = check_ocr_environment()
            self.stdout.write("\n=== OCR Environment ===")
            for key, value in env.items():
                if key != "warnings":
                    self.stdout.write(f"  {key}: {value}")
            for w in env.get("warnings", []):
                self.stdout.write(f"  warning: {w}")

            # Kumpulkan semua PDF
            pdf_files = sorted(folder.rglob("*.pdf"))
            if not pdf_files:
                self.stdout.write("Tidak ada file PDF ditemukan di folder.")
                return

            self.stdout.write(f"\nDitemukan {len(pdf_files)} file PDF:")
            for f in pdf_files:
                self.stdout.write(f"  {f.relative_to(folder)}")

            # Proses setiap file
            spm_data = None
            drpp_list = []
            kw_items = []

            for pdf_path in pdf_files:
                self.stdout.write(f"\n{'='*60}")
                self.stdout.write(f"File: {pdf_path.name}")
                probe = extract_pdf_text(str(pdf_path), ocr=False)  # quick probe
                doc_type = classify_document(pdf_path.name, probe.get("combined_text", ""))
                self.stdout.write(f"  jenis_dokumen: {doc_type}")

                if doc_type == "SPM":
                    parsed = parse_spm_pdf(str(pdf_path), ocr=use_ocr)
                    spm_data = spm_data or parsed
                    self._print_spm_summary(parsed)
                elif doc_type in {"DRPP", "KW"}:
                    parsed = parse_drpp_pdf(str(pdf_path), ocr=use_ocr)
                    if doc_type == "DRPP":
                        drpp_list.append(parsed)
                    kw_items.extend(parsed.get("items", []))
                    self._print_drpp_summary(parsed, doc_type)
                else:
                    self.stdout.write("  status: Lampiran/Unknown — dilewati parsing mendalam.")
                    probe_full = extract_pdf_text(str(pdf_path), ocr=use_ocr)
                    self.stdout.write(f"  engine: {probe_full.get('best_engine')}, text_length: {len(probe_full.get('combined_text') or '')}")

            # Evaluasi paket gabungan
            if spm_data or drpp_list:
                self.stdout.write(f"\n{'='*60}")
                self.stdout.write("=== Evaluasi Paket Gabungan ===")
                parsed_package = {
                    "spm": spm_data,
                    "drpp": drpp_list[0] if drpp_list else None,
                    "drpps": drpp_list,
                    "kw_items": kw_items,
                    "files": [],
                }
                try:
                    meta = package_metadata(parsed_package)
                    doc_status, doc_notes = evaluate_document_status(parsed_package)
                    self.stdout.write(f"  No SPM final: {meta.get('nomor_spm_final') or '-'}")
                    self.stdout.write(f"  No SPP: {meta.get('nomor_spp') or '-'}")
                    self.stdout.write(f"  No SP2D: {meta.get('nomor_sp2d') or '-'}")
                    self.stdout.write(f"  No Invoice: {meta.get('nomor_invoice') or '-'}")
                    self.stdout.write(f"  Jenis SPM: {meta.get('jenis_spm') or '-'}")
                    self.stdout.write(f"  Total Netto: {meta.get('total') or '-'}")
                    self.stdout.write(f"  Jumlah DRPP: {meta.get('drpp_count') or 0}")
                    self.stdout.write(f"  Jumlah KW/Item: {meta.get('kw_count') or 0}")
                    self.stdout.write(f"  Jumlah Akun: {meta.get('akun_count') or 0}")
                    self.stdout.write(f"  Status Dokumen: {doc_status}")
                    for note in doc_notes:
                        self.stdout.write(f"  Keterangan: {note}")
                    # Akun
                    spm_akun = (spm_data or {}).get("akun_rows", [])
                    if spm_akun:
                        self.stdout.write(f"  Akun dari SPM: {', '.join(r.get('akun', '') for r in spm_akun[:20])}")
                    if kw_items:
                        akun_kw = sorted(set(item.get("akun", "") for item in kw_items if item.get("akun")))
                        self.stdout.write(f"  Akun dari KW/DRPP: {', '.join(akun_kw[:20])}")
                        self.stdout.write(f"\n  Draft D_K (5 pertama dari {len(kw_items)} item):")
                        for item in kw_items[:5]:
                            self.stdout.write(
                                f"    - KW={item.get('no_bukti') or '-'}; "
                                f"akun={item.get('akun') or '-'}; "
                                f"jumlah={item.get('jumlah') or '-'}; "
                                f"drpp={item.get('no_drpp') or '-'}"
                            )
                except Exception as exc:
                    self.stdout.write(f"  Gagal evaluasi paket: {exc}")
            else:
                self.stdout.write("\nTidak ada SPM atau DRPP yang berhasil diparse.")

        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _print_spm_summary(self, parsed):
        meta = parsed.get("metadata", {})
        self.stdout.write(f"  engine: {parsed.get('best_engine')}, confidence: {parsed.get('confidence', 0):.1f}%")
        self.stdout.write(f"  paddleocr_dipanggil: {parsed.get('paddleocr_called', False)}")
        self.stdout.write(f"  No SPM final: {meta.get('nomor_spm_final') or meta.get('nomor_spm') or '-'}")
        self.stdout.write(f"  No SPP: {meta.get('nomor_spp') or '-'}")
        self.stdout.write(f"  No SP2D: {meta.get('nomor_sp2d') or '-'}")
        self.stdout.write(f"  No Invoice: {meta.get('nomor_invoice') or '-'}")
        self.stdout.write(f"  Jenis SPM: {meta.get('jenis_spm') or '-'}")
        self.stdout.write(f"  Pengeluaran: {meta.get('jumlah_pengeluaran') or '-'}")
        self.stdout.write(f"  Potongan: {meta.get('jumlah_potongan') or '-'}")
        self.stdout.write(f"  Total Netto: {meta.get('total_pembayaran') or '-'}")
        akun_rows = parsed.get("akun_rows", [])
        if akun_rows:
            self.stdout.write(f"  Akun: {', '.join(r.get('akun', '') for r in akun_rows[:10])}")
        pem = meta.get("pembebanan_list", [])
        if pem:
            self.stdout.write(f"  Pembebanan COA: {', '.join(pem[:5])}")
        for w in parsed.get("warnings", [])[:5]:
            self.stdout.write(f"  warning: {w}")

    def _print_drpp_summary(self, parsed, doc_type):
        meta = parsed.get("metadata", {})
        items = parsed.get("items", [])
        self.stdout.write(f"  engine: {parsed.get('best_engine')}, confidence: {parsed.get('confidence', 0):.1f}%")
        self.stdout.write(f"  paddleocr_dipanggil: {parsed.get('paddleocr_called', False)}")
        self.stdout.write(f"  No DRPP: {meta.get('nomor_drpp') or '-'}")
        self.stdout.write(f"  No SPM: {meta.get('nomor_spm') or '-'}")
        self.stdout.write(f"  Total: {meta.get('total') or '-'}")
        self.stdout.write(f"  Jumlah item: {len(items)}")
        for item in items[:5]:
            self.stdout.write(
                f"    - KW={item.get('no_bukti') or '-'}; "
                f"akun={item.get('akun') or '-'}; "
                f"jumlah={item.get('jumlah') or '-'}"
            )
        for w in parsed.get("warnings", [])[:3]:
            self.stdout.write(f"  warning: {w}")
