"""
Read-only diagnostic V2 for DRPP TSV extraction.

Key insight: the production web preview reads from PaketSPMUpload.parsed_data,
which stores the ORIGINAL OCR results (including page_details[tsv_words]) from the
time of upload. Running fresh extract_pdf_text() gives DIFFERENT results due to
cache, DPI, or timing differences.

Usage:
  python manage.py diagnose_drpp_tsv <path> [--exact-batch]
  python manage.py diagnose_drpp_tsv --latest [--exact-batch]

Sections:
  1. PARSER SOURCE MARKER — confirms kw_prefix_re multiline fix
  2. STORED WEB PREVIEW DATA — from PaketSPMUpload.parsed_data
  3. EXACT BATCH REPLAY — parse_paket_spm_zip using same cache
  4. CONSISTENCY CHECK — stored vs replayed items
"""
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.parsers import (
    _group_tsv_words_by_line,
    _to_tsv_word,
    normalize_text,
    parse_drpp_items_from_tsv_rows,
)
from apps.core.parsers import parse_paket_spm_zip


def _has_multiline_fix():
    import inspect
    src = inspect.getsource(parse_drpp_items_from_tsv_rows)
    return "kw_prefix_re" in src


# ── Regex helpers (mirrors parsers.py) ─────────────────────────────────────────

_KW_RE = re.compile(
    r"[0-9OIL]{3,6}(?:[\s]*[0-9OIL]*)?[\s]*/?[\s]*KW[\s/]*"
    r"[0-9OIL\s]{5,12}[\s/]*20[0-9OIL]{2}",
    re.IGNORECASE,
)
_AMOUNT_RE = re.compile(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?")
_AKUN_RE = re.compile(r"\b(5\d{5})\b")
_KW_SIMPLE = re.compile(r"(?:KW|KUITANSI)\s*[:\-]?\s*([0-9A-Z./-]{3,})", re.I)


class Command(BaseCommand):
    help = "Diagnose DRPP TSV row extraction V2. READ ONLY."

    def add_arguments(self, parser):
        parser.add_argument(
            "path", nargs="?", default=None, help="Path ke file PDF atau ZIP."
        )
        parser.add_argument(
            "--latest",
            action="store_true",
            help="Gunakan PaketSPMUpload terbaru.",
        )
        parser.add_argument(
            "--exact-batch",
            action="store_true",
            help=(
                "Jalankan parse_paket_spm_zip untuk replay path yang SAMA "
                "dengan web upload (dengan cache OCR yang sama)."
            ),
        )

    def handle(self, *args, **options):
        path = options.get("path")
        use_latest = options.get("latest", False)
        use_exact = options.get("exact_batch", False)

        # ── Resolve file path ──────────────────────────────────────────────
        if use_latest:
            from apps.paket_spm.models import PaketSPMUpload
            paket = PaketSPMUpload.objects.order_by("-uploaded_at").first()
            if not paket:
                raise CommandError("Tidak ada PaketSPMUpload di database.")
            zip_path = paket.zip_file.path
            if not Path(zip_path).exists():
                raise CommandError(f"File ZIP tidak ditemukan: {zip_path}")
            path = zip_path
            parsed_data = paket.parsed_data or {}
            self.stdout.write(f"=== Latest PaketSPMUpload #{paket.id} ===")
            self.stdout.write(f"  original_filename: {paket.original_filename}")
            self.stdout.write(f"  uploaded_at:      {paket.uploaded_at}")
            self.stdout.write(f"  status:           {paket.status}")
            self.stdout.write(f"  zip_file:         {zip_path}")
            self.stdout.write(f"  parsed_data keys: {list(parsed_data.keys())}")
        elif not path:
            raise CommandError("Berikan path atau gunakan --latest.")
        else:
            path = str(Path(path).resolve())
            parsed_data = None

        if not Path(path).exists():
            raise CommandError(f"File tidak ditemukan: {path}")

        # ── 1. Parser source marker ─────────────────────────────────────
        self.stdout.write("\n=== 1. PARSER SOURCE MARKER ===")
        self.stdout.write(f"  multiline_kw_fix_present: {_has_multiline_fix()}")
        self.stdout.write(f"  parse_paket_spm_zip_id: {id(parse_paket_spm_zip)}")

        # ── 2. Stored web preview data ───────────────────────────────────
        if parsed_data:
            self.stdout.write("\n=== 2. STORED WEB PREVIEW DATA ===")
            self._print_stored_data(parsed_data)
        else:
            self.stdout.write("\n=== 2. STORED WEB PREVIEW DATA ===")
            self.stdout.write("  [Tidak ada parsed_data — file langsung]")

        # ── 3. Exact batch replay ───────────────────────────────────────
        if use_exact:
            self.stdout.write("\n=== 3. EXACT BATCH REPLAY ===")
            self.stdout.write(f"  path: {path}")
            self.stdout.write(f"  Using parse_paket_spm_zip (batch pipeline)")

            # Run the SAME code path as web upload
            try:
                replay = parse_paket_spm_zip(path, ocr=True)
            except Exception as exc:
                self.stdout.write(f"  ERROR: {exc}")
                return

            # Check OCR cache stats
            ocr_pages = 0
            cache_hits = 0
            tsv_word_count = 0
            for detail in replay.get("page_details", []):
                if isinstance(detail, dict):
                    if detail.get("engine") == "tesseract":
                        ocr_pages += 1
                    if detail.get("cache_hit"):
                        cache_hits += 1
                    tsv_word_count += len(detail.get("tsv_words", []))

            self.stdout.write(f"  Cache hits:   {cache_hits}")
            self.stdout.write(f"  OCR pages:    {ocr_pages}")
            self.stdout.write(f"  tsv_word_count: {tsv_word_count}")

            self._print_stored_data(replay)
        else:
            self.stdout.write("\n=== 3. EXACT BATCH REPLAY ===")
            self.stdout.write("  [Gunakan --exact-batch untuk replay]")

        # ── 4. Consistency check ────────────────────────────────────────
        if parsed_data and use_exact:
            self.stdout.write("\n=== 4. CONSISTENCY CHECK ===")
            stored_items = self._get_items(parsed_data)
            replay_items = self._get_items(replay)
            self.stdout.write(f"  Stored item count:  {len(stored_items)}")
            self.stdout.write(f"  Replay item count: {len(replay_items)}")
            if len(stored_items) == len(replay_items) == 0:
                self.stdout.write("  SAME: both zero items")
            elif len(stored_items) == len(replay_items):
                self.stdout.write("  SAME: same count")
            else:
                self.stdout.write("  DIFFERENT: item counts diverge")
                self.stdout.write("  Stored:")
                for it in stored_items:
                    self.stdout.write(
                        f"    {it.get('method','?')} | "
                        f"KW={it.get('no_bukti','-')} | "
                        f"AKUN={it.get('akun','-')} | "
                        f"JML={it.get('jumlah','-')}"
                    )
                self.stdout.write("  Replay:")
                for it in replay_items:
                    self.stdout.write(
                        f"    {it.get('method','?')} | "
                        f"KW={it.get('no_bukti','-')} | "
                        f"AKUN={it.get('akun','-')} | "
                        f"JML={it.get('jumlah','-')}"
                    )
        elif parsed_data:
            self.stdout.write("\n=== 4. CONSISTENCY CHECK ===")
            self.stdout.write("  [Gunakan --exact-batch untuk bandingkan]")

        self.stdout.write("\n=== DIAGNOSTIC COMPLETE ===")
        self.stdout.write("  READ ONLY — tidak ada data yang diubah.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_items(self, parsed):
        """Extract all DRPP items from parsed_data structure."""
        items = []
        for drpp_key in ("drpp", "drpps"):
            val = parsed.get(drpp_key)
            if isinstance(val, dict):
                items.extend(val.get("items", []) or [])
            elif isinstance(val, list):
                for d in val:
                    if isinstance(d, dict):
                        items.extend(d.get("items", []) or [])
        # Also check top-level kw_items
        items.extend(parsed.get("kw_items", []) or [])
        return items

    def _print_stored_data(self, parsed):
        """Print structural summary of stored parsed_data."""
        # Top-level OCR stats
        drpp = parsed.get("drpp") or {}
        drpps = parsed.get("drpps", [])

        # OCR metadata
        drpp_meta = drpp.get("metadata", {}) if isinstance(drpp, dict) else {}
        self.stdout.write(f"  Document type:    {parsed.get('document_type', '-')}")
        self.stdout.write(f"  Best engine:     {parsed.get('best_engine', '-')}")
        self.stdout.write(f"  tesseract_called:{parsed.get('tesseract_called', '-')}")
        self.stdout.write(f"  native_text_len: {parsed.get('native_text_length', '-')}")
        self.stdout.write(
            f"  tesseract_text:  {parsed.get('tesseract_text_length', '-')}"
        )
        self.stdout.write(f"  Status:          {parsed.get('status', '-')}")

        # Item count per group
        self.stdout.write(
            f"  DRPP groups:     {len(drpps) or (1 if drpp else 0)}"
        )
        self.stdout.write(f"  Top-level kw_items: {len(parsed.get('kw_items', []))}")

        # Aggregate items
        all_items = self._get_items(parsed)
        self.stdout.write(f"\n  Total items:      {len(all_items)}")

        if all_items:
            self.stdout.write("\n  --- Stored items ---")
            for idx, item in enumerate(all_items):
                self.stdout.write(
                    f"  [{idx}] "
                    f"METHOD={item.get('method','-')} | "
                    f"KW={item.get('no_bukti','-')} | "
                    f"AKUN={item.get('akun','-')} | "
                    f"JML={item.get('jumlah','-')} | "
                    f"REVIEW={item.get('needs_review',False)}"
                )

        # Print page details TSV evidence for DRPP group
        if drpp and isinstance(drpp, dict):
            page_details = drpp.get("page_details", [])
            if page_details:
                self.stdout.write(
                    f"\n  DRPP page_details: {len(page_details)} pages"
                )
                for pd in page_details:
                    if not isinstance(pd, dict):
                        continue
                    page_num = pd.get("page_number", "?")
                    engine = pd.get("engine", "-")
                    tsv_words = pd.get("tsv_words", [])
                    text_len = len(pd.get("text", "") or pd.get("extracted_text", ""))
                    self.stdout.write(
                        f"    Page {page_num}: engine={engine}, "
                        f"text_len={text_len}, tsv_words={len(tsv_words)}"
                    )
                    if tsv_words:
                        self._print_kw_lines(tsv_words)
        elif drpps:
            for d in drpps:
                if not isinstance(d, dict):
                    continue
                page_details = d.get("page_details", [])
                if page_details:
                    self.stdout.write(f"\n  DRPP page_details: {len(page_details)} pages")
                    for pd in page_details:
                        if not isinstance(pd, dict):
                            continue
                        page_num = pd.get("page_number", "?")
                        engine = pd.get("engine", "-")
                        tsv_words = pd.get("tsv_words", [])
                        text_len = len(pd.get("text", "") or pd.get("extracted_text", ""))
                        self.stdout.write(
                            f"    Page {page_num}: engine={engine}, "
                            f"text_len={text_len}, tsv_words={len(tsv_words)}"
                        )
                        if tsv_words:
                            self._print_kw_lines(tsv_words)

    def _print_kw_lines(self, tsv_words):
        """Print TSV lines that contain KW/akun/amount evidence."""
        words = [_to_tsv_word(w) for w in tsv_words]
        words = [w for w in words if w]
        lines = _group_tsv_words_by_line(words)

        for li, line in enumerate(lines):
            sorted_words = sorted(line.get("words", []), key=lambda w: w.get("left", 0))
            line_text = normalize_text(" ".join(w.get("text", "") for w in sorted_words))
            upper = line_text.upper()

            has_kw = bool(_KW_RE.search(line_text))
            has_akun = bool(_AKUN_RE.search(line_text))
            has_amount = bool(_AMOUNT_RE.search(line_text))

            if not (has_kw or has_akun or has_amount):
                continue

            self.stdout.write(
                f"      line[{li}] top={line.get('center_y', 0):.0f} "
                f"KW={has_kw} AKUN={has_akun} AMOUNT={has_amount}"
            )
            self.stdout.write(f"        {line_text[:120]}")
            for w in sorted_words:
                t = w.get("text", "")
                x = w.get("center_x", 0)
                conf = w.get("confidence", 0)
                if _KW_RE.search(t) or _AKUN_RE.search(t) or _AMOUNT_RE.search(t):
                    self.stdout.write(
                        f"          [{x:.0f}] conf={conf:.0f}: {t}"
                    )
