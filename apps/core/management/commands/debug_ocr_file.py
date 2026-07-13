from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.ocr import check_ocr_environment
from apps.core.parsers import classify_document, extract_pdf_text, make_json_safe, parse_drpp_pdf, parse_spm_pdf
from apps.core.templatetags.intermilan_format import month_id
from apps.paket_spm.models import PaketSPMUpload
from apps.paket_spm.services import build_package_decision, build_transaction_rows_from_package, evaluate_document_status


class Command(BaseCommand):
    help = "Debug OCR untuk satu PDF. Read-only. Output lengkap untuk diagnosa."

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
            self.stdout.write(f"  {key}: {value}")
        for warning in env.get("warnings", []):
            self.stdout.write(f"  warning: {warning}")

        self.stdout.write("\n=== File ===")
        self.stdout.write(f"  path: {path}")
        self.stdout.write(f"  size: {path.stat().st_size} bytes")

        probe = extract_pdf_text(str(path), ocr=True)
        doc_type = classify_document(path.name, probe.get("combined_text", ""))
        self.stdout.write("\n=== OCR Probe ===")
        self.stdout.write(f"  jenis_dokumen: {doc_type}")
        self.stdout.write(f"  engine_final: {probe.get('best_engine')}")
        self.stdout.write(f"  status_ocr: {probe.get('status')}")
        self.stdout.write(f"  engine_dicoba: {', '.join(probe.get('engines_tried') or []) or '-'}")
        self.stdout.write(f"  native_text_length: {probe.get('native_text_length', 0)}")
        self.stdout.write(f"  tesseract_dipanggil: {probe.get('tesseract_called', False)}")
        self.stdout.write(f"  tesseract_text_length: {probe.get('tesseract_text_length', 0)}")
        self.stdout.write(f"  tesseract_reason: {probe.get('tesseract_reason') or '-'}")
        self.stdout.write(f"  paddleocr_dipanggil: {probe.get('paddleocr_called', False)}")
        self.stdout.write(f"  paddleocr_text_length: {probe.get('paddleocr_text_length', 0)}")
        self.stdout.write(f"  raw_text_length_final: {len(probe.get('combined_text') or '')}")
        self.stdout.write(f"  confidence: {probe.get('confidence', 0.0):.1f}%")
        self.stdout.write(f"  jumlah_halaman: {probe.get('page_count', 0)}")
        for warning in probe.get("warnings", []):
            self.stdout.write(f"  warning: {warning}")

        # Klasifikasi per halaman
        self.stdout.write("\n=== Klasifikasi Per Halaman ===")
        for page in probe.get("page_details", []):
            if isinstance(page, str):
                continue  # skip plain strings
            page_num = page.get("page_number") or page.get("page", "?")
            engine = page.get("engine", "-")
            page_class = page.get("page_classification", "-")
            conf = page.get("confidence", 0.0)
            text_len = len(page.get("text") or page.get("extracted_text") or "")
            self.stdout.write(f"  Halaman {page_num}: engine={engine}, klasifikasi={page_class}, confidence={conf:.1f}%, text_length={text_len}")

        if doc_type == "SPM":
            parsed = parse_spm_pdf(str(path), ocr=True)
            self.print_spm(parsed)
        elif doc_type in {"DRPP", "KW"}:
            parsed = parse_drpp_pdf(str(path), ocr=True)
            self.print_drpp(parsed, doc_type)
        else:
            self.stdout.write("\n=== Parsed Metadata ===")
            self.stdout.write("  Dokumen tidak dikenali sebagai SPM/DRPP/KW. File diperlakukan sebagai Lampiran/Unknown.")
            self.stdout.write(f"  cuplikan: {(probe.get('combined_text') or '')[:500]}")

    def print_spm(self, parsed):
        meta = parsed.get("metadata", {})
        is_combined = parsed.get("is_combined_package", False)
        akun_rows = parsed.get("akun_rows", [])
        self.stdout.write("\n=== Parsed SPM ===")
        self.stdout.write(f"  status_ocr: {parsed.get('status')}")
        self.stdout.write(f"  engine_final: {parsed.get('best_engine')}")
        self.stdout.write(f"  paddleocr_dipanggil: {parsed.get('paddleocr_called', False)}")
        self.stdout.write(f"  is_combined_package: {is_combined}")
        self.stdout.write("")
        self.stdout.write("  --- Nomor Dokumen ---")
        self.stdout.write(f"  No SPM (final): {meta.get('nomor_spm_final') or meta.get('nomor_spm') or '-'}")
        self.stdout.write(f"  No SPP: {meta.get('nomor_spp') or '-'}")
        self.stdout.write(f"  No SP2D: {meta.get('nomor_sp2d') or '-'}")
        self.stdout.write(f"  No Invoice/SPP-SPM: {meta.get('nomor_invoice') or '-'}")
        self.stdout.write(f"  No DRPP: {meta.get('nomor_drpp') or '-'}")
        self.stdout.write(f"  Satker: {meta.get('satker_app_name') or meta.get('satker_code') or '-'}")
        self.stdout.write(f"  Kode Satker DJPb: {meta.get('satker_djpb_code') or '-'}")
        self.stdout.write(f"  Kode Satker Aplikasi: {meta.get('satker_app_code') or '-'}")
        self.stdout.write(f"  Tanggal SPM: {meta.get('tanggal_spm') or '-'}")
        self.stdout.write(f"  Jenis SPM: {meta.get('jenis_spm') or '-'}")
        self.stdout.write(f"  Supplier/Penerima: {meta.get('supplier') or '-'}")
        self.stdout.write(f"  KPPN: {meta.get('kppn') or '-'}")
        self.stdout.write(f"  Halaman SPM: {meta.get('spm_page_nums') or '-'}")
        self.stdout.write(f"  Halaman SPP: {meta.get('spp_page_nums') or '-'}")
        self.stdout.write("")
        self.stdout.write("  --- Akun/COA ---")
        if akun_rows:
            for row in akun_rows[:30]:
                pembebanan = row.get("pembebanan") or "-"
                self.stdout.write(f"  Akun: {row.get('akun', '-')}  Pembebanan: {pembebanan}")
        else:
            self.stdout.write("  Akun: - (tidak terbaca)")
        self.stdout.write(f"  Akun Pengeluaran: {', '.join(meta.get('akun_pengeluaran') or []) or '-'}\n"
            f"  Akun Potongan: {', '.join(meta.get('akun_potongan') or []) or '-'}\n"
            f"  Pembebanan/COA strings: {', '.join(meta.get('pembebanan_list') or [])}\n")
        self.stdout.write("")
        self.stdout.write("  --- Nilai Keuangan ---")
        self.stdout.write(f"  Pengeluaran (Bruto): {meta.get('jumlah_pengeluaran') or '-'}")
        self.stdout.write(f"  Potongan/PPh21: {meta.get('jumlah_potongan') or '-'}")
        self.stdout.write(f"  Total Pembayaran (Netto): {meta.get('total_pembayaran') or '-'}")
        self.stdout.write("")
        self.stdout.write("  --- Nomor SPM Resolution ---")
        self.stdout.write(f"  filename: {parsed.get('file_name') or '-'}")
        self.stdout.write(f"  no_spm_dari_filename: {meta.get('nomor_spm_filename') or '-'}")
        self.stdout.write(f"  no_spm_dari_ocr (halaman SPM): {meta.get('nomor_spm_ocr') or '-'}")
        self.stdout.write(f"  no_spp_dari_ocr (halaman SPP): {meta.get('nomor_spp') or '-'}")
        self.stdout.write(f"  no_spm_final: {meta.get('nomor_spm_final') or meta.get('nomor_spm') or '-'}")
        self.stdout.write(f"  source: {meta.get('nomor_spm_final_source') or '-'}")
        self.stdout.write(f"  conflict: {meta.get('nomor_spm_conflict')}")
        self.stdout.write(f"  status_review: {meta.get('nomor_spm_review_status') or '-'}")
        self.stdout.write(f"  reason: {meta.get('nomor_spm_reason') or '-'}")
        self.stdout.write("")
        # Evaluasi status dokumen
        parsed_wrapped = {"spm": parsed}
        try:
            doc_status, doc_notes = evaluate_document_status(parsed_wrapped)
            self.stdout.write(f"  --- Status Dokumen ---")
            self.stdout.write(f"  status_dokumen: {doc_status}")
            for note in doc_notes:
                self.stdout.write(f"  keterangan: {note}")
        except Exception as exc:
            self.stdout.write(f"  status_dokumen: (gagal dievaluasi: {exc})")
        self.print_preview_rows(parsed)
        self.stdout.write("")
        self.stdout.write("  --- Warning Teknis ---")
        for warning in parsed.get("warnings", []):
            self.stdout.write(f"  warning: {warning}")

    def print_preview_rows(self, spm):
        meta = spm.get("metadata", {})
        parsed = make_json_safe({
            "ok": True,
            "files": [{"file_name": spm.get("file_name", "-"), "type": "SPM"}],
            "spm": spm,
            "drpp": None,
            "drpps": [],
            "kw_items": [],
        })
        paket = PaketSPMUpload(
            original_filename=spm.get("file_name", ""),
            nomor_spm=meta.get("nomor_spm", ""),
            nomor_invoice=meta.get("nomor_invoice", ""),
            satker_code=meta.get("satker_app_code") or meta.get("satker_code") or "",
            tanggal_spm=meta.get("tanggal_spm"),
            tahun=getattr(meta.get("tanggal_spm"), "year", None),
            bulan=getattr(meta.get("tanggal_sp2d"), "month", None),
            jenis_spm_asli=meta.get("jenis_spm", ""),
            jenis_spm_label=meta.get("jenis_spm", ""),
            parsed_data=parsed,
        )
        self.stdout.write("")
        self.stdout.write("  --- Preview D_K 15 Kolom ---")
        try:
            decision = build_package_decision(parsed, spm.get("file_name", ""))
            rows = build_transaction_rows_from_package(parsed, paket, save=False, document_status=decision.get("document_status"))
        except Exception as exc:
            self.stdout.write(f"  preview_error: {exc}")
            return
        for row in rows:
            self.stdout.write(f"  Helper: {row.akun}{row.no_kuitansi}")
            self.stdout.write(f"  Akun: {row.akun}")
            self.stdout.write(f"  SP2D Bulan: {month_id(row.bulan_sp2d)}")
            self.stdout.write(f"  Cara Pembayaran: {row.cara_pembayaran}")
            self.stdout.write(f"  Nomor SPM: {row.nomor_spm}")
            self.stdout.write(f"  Tanggal SPM: {row.tanggal_spm}")
            self.stdout.write(f"  Jenis SPM: {row.jenis_spm}")
            self.stdout.write(f"  No. Kuitansi: {row.no_kuitansi}")
            self.stdout.write(f"  No. DRPP: {row.no_drpp}")
            self.stdout.write(f"  Deskripsi: {row.deskripsi}")
            self.stdout.write(f"  Nilai Bruto: {row.nilai_bruto}")
            self.stdout.write(f"  Nilai Netto: {row.nilai_netto}")
            self.stdout.write(f"  Pembebanan: {row.pembebanan}")
            self.stdout.write(f"  FP: {row.fp}")
            self.stdout.write(f"  PPh21: {row.pph21}")

    def print_drpp(self, parsed, doc_type):
        meta = parsed.get("metadata", {})
        self.stdout.write(f"\n=== Parsed {doc_type} ===")
        self.stdout.write(f"  status_ocr: {parsed.get('status')}")
        self.stdout.write(f"  engine_final: {parsed.get('best_engine')}")
        self.stdout.write(f"  paddleocr_dipanggil: {parsed.get('paddleocr_called', False)}")
        self.stdout.write(f"  No DRPP: {meta.get('nomor_drpp') or '-'}")
        self.stdout.write(f"  No SPM: {meta.get('nomor_spm') or '-'}")
        self.stdout.write(f"  Total: {meta.get('total') or '-'}")
        self.stdout.write(f"  Item terbaca: {len(parsed.get('items', []))}")
        for item in parsed.get("items", [])[:20]:
            self.stdout.write(
                "    - "
                f"KW={item.get('no_bukti') or '-'}; "
                f"akun={item.get('akun') or '-'}; "
                f"jumlah={item.get('jumlah') or '-'}; "
                f"keperluan={str(item.get('keperluan') or '')[:80]}"
            )
        for warning in parsed.get("warnings", []):
            self.stdout.write(f"  warning: {warning}")
