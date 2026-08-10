"""
Read-only diagnostic for DRPP TSV/OCR row extraction.

Usage:
  python manage.py diagnose_drpp_tsv <path>
  python manage.py diagnose_drpp_tsv --latest

Prints per-DRPP-page TSV structure and parser stage results to identify
why transaction rows are missing or duplicated.
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.parsers import (
    _group_tsv_words_by_line,
    _to_tsv_word,
    extract_pdf_text,
    normalize_text,
    parse_decimal,
    parse_drpp_financial_table_rows,
    parse_drpp_items_from_tsv,
    parse_drpp_items_from_tsv_rows,
)
from apps.paket_spm.models import PaketSPMUpload


# Check whether the multiline KW merge fix is present in production.
def _has_multiline_fix():
    import inspect
    src = inspect.getsource(parse_drpp_items_from_tsv_rows)
    return "kw_prefix_re" in src


class Command(BaseCommand):
    help = "Diagnose DRPP TSV row extraction. READ ONLY — no DB writes."

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            nargs="?",
            default=None,
            help="Path ke file PDF atau ZIP DRPP.",
        )
        parser.add_argument(
            "--latest",
            action="store_true",
            help="Gunakan PaketSPMUpload terbaru.",
        )

    def handle(self, *args, **options):
        path = options.get("path")
        use_latest = options.get("latest", False)

        # ── Resolve file path ─────────────────────────────────────────────────
        if use_latest:
            paket = PaketSPMUpload.objects.order_by("-uploaded_at").first()
            if not paket:
                raise CommandError("Tidak ada PaketSPMUpload di database.")
            zip_path = paket.zip_file.path
            if not Path(zip_path).exists():
                raise CommandError(f"File ZIP tidak ditemukan: {zip_path}")
            path = zip_path
            self.stdout.write(f"=== Latest PaketSPMUpload #{paket.id} ===")
            self.stdout.write(f"  original_filename: {paket.original_filename}")
            self.stdout.write(f"  uploaded_at: {paket.uploaded_at}")
            self.stdout.write(f"  status: {paket.status}")
            self.stdout.write(f"  zip_file: {zip_path}")
        elif not path:
            raise CommandError("Berikan path file atau gunakan --latest.")
        else:
            path = str(Path(path).resolve())

        if not Path(path).exists():
            raise CommandError(f"File tidak ditemukan: {path}")

        # ── Parser version marker ───────────────────────────────────────────
        self.stdout.write("\n=== PARSER SOURCE MARKER ===")
        self.stdout.write(f"  multiline_kw_fix_present: {_has_multiline_fix()}")
        self.stdout.write(f"  parse_drpp_items_from_tsv_rows_id: {id(parse_drpp_items_from_tsv_rows)}")

        # ── Extract OCR ─────────────────────────────────────────────────────
        self.stdout.write("\n=== FILE ===")
        self.stdout.write(f"  path: {path}")
        self.stdout.write(f"  size: {Path(path).stat().st_size} bytes")

        extracted = extract_pdf_text(path, ocr=True)
        page_details = extracted.get("page_details", [])

        # Find DRPP pages
        drpp_indices = []
        for i, page in enumerate(page_details):
            if isinstance(page, str):
                continue
            types = page.get("page_types", [])
            text = page.get("text", "") or page.get("extracted_text", "") or ""
            if any(t in {"DRPP", "LAMPIRAN_COA"} for t in types) or "DRPP" in text.upper():
                drpp_indices.append(i)

        if not drpp_indices:
            self.stdout.write("\n  [Tidak ada halaman DRPP terdeteksi]")
            return

        self.stdout.write(f"\n=== DRPP PAGES ({len(drpp_indices)} ditemukan) ===")

        for page_idx in drpp_indices:
            page = page_details[page_idx]
            if isinstance(page, str):
                continue
            page_num = page.get("page_number") or page.get("page", page_idx + 1)
            page_types = page.get("page_types", [])
            engine = page.get("engine", "-")
            tsv_words = page.get("tsv_words") or []
            words = [_to_tsv_word(w) for w in tsv_words]
            words = [w for w in words if w]
            lines = _group_tsv_words_by_line(words)

            self.stdout.write(f"\n  --- Halaman {page_num} ---")
            self.stdout.write(f"  types: {', '.join(page_types) or '-'}")
            self.stdout.write(f"  engine: {engine}")
            self.stdout.write(f"  rotation: {next((w.get('rotation',0) for w in words if isinstance(w,dict)), 0)}")
            self.stdout.write(f"  tsv_word_count: {len(words)}")
            self.stdout.write(f"  line_count: {len(lines)}")

            if not words:
                self.stdout.write("  [Tidak ada TSV words — skip]")
                continue

            # ── Print all lines with KW / akun / amount evidence ───────────
            self.stdout.write("\n  --- Lines with KW/akun/amount evidence ---")
            kw_re = _kw_pattern()
            amount_re = _amount_pattern()

            for li, line in enumerate(lines):
                sorted_words = sorted(line.get("words", []), key=lambda w: w.get("left", 0))
                line_text = normalize_text(" ".join(w.get("text", "") for w in sorted_words))
                upper = line_text.upper()

                has_kw = bool(kw_re.search(line_text))
                has_akun = bool(_akun_pattern().search(line_text))
                has_amount = bool(amount_re.search(line_text))

                if not (has_kw or has_akun or has_amount):
                    continue

                self.stdout.write(f"\n  line[{li}] top={line.get('center_y', 0):.0f}")
                self.stdout.write(f"    text: {line_text[:120]}")
                self.stdout.write(
                    f"    KW={has_kw} AKUN={has_akun} AMOUNT={has_amount}"
                )
                for w in sorted_words:
                    x = w.get("center_x", 0)
                    conf = w.get("confidence", 0)
                    t = w.get("text", "")
                    if kw_re.search(t) or _akun_pattern().search(t) or amount_re.search(t):
                        self.stdout.write(f"      [{x:.0f}] conf={conf:.0f}: {t}")

            # ── Run each parser and print stage results ─────────────────────
            page_number = page.get("page_number") or page.get("page") or 1

            self.stdout.write("\n  --- Parser stage results ---")

            # A) TSV column parser
            cell_items = parse_drpp_items_from_tsv(words, page_number=page_number)
            self.stdout.write(
                f"  A) parse_drpp_items_from_tsv: {len(cell_items)} items"
            )
            for item in cell_items:
                self.stdout.write(
                    f"     KW={item.get('no_bukti','-')} AKUN={item.get('akun','-')} "
                    f"JML={item.get('jumlah','-')} METHOD={item.get('method','-')} "
                    f"REVIEW={item.get('needs_review', False)}"
                )

            # B) Anchor-based parser
            anchor_items = parse_drpp_items_from_tsv_rows(words, page_number=page_number)
            self.stdout.write(
                f"  B) parse_drpp_items_from_tsv_rows: {len(anchor_items)} items"
            )
            for item in anchor_items:
                raw = item.get("raw_fields", {})
                self.stdout.write(
                    f"     KW={item.get('no_bukti','-')} AKUN={item.get('akun','-')} "
                    f"JML={item.get('jumlah','-')} METHOD={item.get('method','-')} "
                    f"REVIEW={item.get('needs_review', False)}"
                )
                self.stdout.write(
                    f"       row_text: {(raw.get('row','') or '')[:100]}"
                )

            # C) Financial table parser
            financial_items = parse_drpp_financial_table_rows(
                words, page_number=page_number
            )
            self.stdout.write(
                f"  C) parse_drpp_financial_table_rows: {len(financial_items)} items"
            )
            for item in financial_items:
                self.stdout.write(
                    f"     KW={item.get('no_bukti','-')} AKUN={item.get('akun','-')} "
                    f"JML={item.get('jumlah','-')} METHOD={item.get('method','-')} "
                    f"REVIEW={item.get('needs_review', False)}"
                )

            # D) row_quality comparison
            self.stdout.write("\n  --- row_quality comparison ---")
            for label, rows in [
                ("cell_items", cell_items),
                ("anchor_items", anchor_items),
                ("financial_items", financial_items),
            ]:
                ci = sum(bool(r.get("no_bukti") and r.get("akun") and r.get("jumlah")) for r in rows)
                fe = sum(
                    bool(r.get("bruto") and parse_decimal(r.get("bruto", 0)) > 0)
                    + bool(r.get("netto") and parse_decimal(r.get("netto", 0)) > 0)
                    + bool(r.get("pph21") and parse_decimal(r.get("pph21", 0)) > 0)
                    + bool(r.get("pembebanan"))
                    + bool(r.get("keperluan"))
                    for r in rows
                )
                score = (ci, fe, len(rows))
                self.stdout.write(
                    f"  {label}: complete_identity={ci} financial_evidence={fe} "
                    f"rows={len(rows)} score={score}"
                )

            # E) Final selected (max by quality)
            all_items = [cell_items, anchor_items, financial_items]
            all_labels = ["cell_items", "anchor_items", "financial_items"]
            selected = max(all_items, key=lambda rows: (
                sum(bool(r.get("no_bukti") and r.get("akun") and r.get("jumlah")) for r in rows),
                sum(
                    bool(r.get("bruto") and parse_decimal(r.get("bruto", 0)) > 0)
                    + bool(r.get("netto") and parse_decimal(r.get("netto", 0)) > 0)
                    + bool(r.get("pph21") and parse_decimal(r.get("pph21", 0)) > 0)
                    + bool(r.get("pembebanan"))
                    + bool(r.get("keperluan"))
                    for r in rows
                ),
                len(rows),
            ))
            winner_label = all_labels[all_items.index(selected)]
            self.stdout.write(f"\n  E) WINNER: {winner_label} ({len(selected)} items)")
            for item in selected:
                self.stdout.write(
                    f"     KW={item.get('no_bukti','-')} AKUN={item.get('akun','-')} "
                    f"JML={item.get('jumlah','-')}"
                )

        self.stdout.write("\n=== DIAGNOSTIC COMPLETE ===")
        self.stdout.write("  [READ ONLY — tidak ada data yang diubah]")


# ── Local regex helpers (mirrors parsers.py) ─────────────────────────────────

import re


def _kw_pattern():
    return re.compile(
        r"[0-9OIL]{3,6}(?:[\s]*[0-9OIL]*)?[\s]*/?[\s]*KW[\s/]*[0-9OIL\s]{5,12}[\s/]*20[0-9OIL]{2}",
        re.IGNORECASE,
    )


def _amount_pattern():
    return re.compile(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?")


def _akun_pattern():
    return re.compile(r"\b(5\d{5})\b")
