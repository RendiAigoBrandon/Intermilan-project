"""
Regression tests for DRPP multiline row reconstruction.

Tests that parse_drpp_items_from_tsv_rows correctly reconstructs
transaction rows from OCR output that has kuitansi numbers split
across lines, nominal amounts on separate lines, or descriptions
wrapped between fields.

Covered cases:
  A — same-line row (control)
  B — multiline KW split across two lines
  C — description wrapped between KW and amount
  D — multiple independent rows
  E — no total-difference inference (only emitted rows count)
  F — distant unrelated fields do not form a combined row
"""
from decimal import Decimal

from django.test import SimpleTestCase

from apps.core.parsers import parse_drpp_items_from_tsv_rows


def _w(text, left=10, top=10, width=80, height=12, conf=90):
    """Construct a minimal TSV word dict."""
    return {
        "text": text,
        "left": left,
        "top": top,
        "width": width,
        "height": height,
        "confidence": conf,
    }


def _line(words):
    """Group words into a line dict matching _group_tsv_words_by_line output."""
    if not words:
        return {"words": [], "center_y": 0}
    tops = [w["top"] for w in words]
    return {
        "words": words,
        "center_y": sum(tops) / len(tops),
    }


def _kw_row(num, akun, amount, top=10, conf=90):
    """Standard same-line KW row."""
    return _line([
        _w(num,     left=10,  top=top, width=60, conf=conf),
        _w("/",     left=72,  top=top, width=8,  conf=conf),
        _w("KW",    left=82,  top=top, width=20, conf=conf),
        _w("/",     left=104, top=top, width=8,  conf=conf),
        _w("019937",left=114, top=top, width=50, conf=conf),
        _w("/",     left=166, top=top, width=8,  conf=conf),
        _w("2026",  left=176, top=top, width=35, conf=conf),
        _w(akun,    left=250, top=top, width=50, conf=conf),
        _w(amount,  left=500, top=top, width=80, conf=conf),
    ])


def _prefix_only(num, top=10, conf=90):
    """Line with only the numeric prefix of a KW number (no /KW/)."""
    return _line([
        _w(num, left=10, top=top, width=60, conf=conf),
    ])


def _suffix_only(suffix_text, top=10, conf=90):
    """Line with only the /KW/... suffix of a KW number."""
    return _line([
        _w(suffix_text, left=10, top=top, width=160, conf=conf),
    ])


class DRPPMultilineRowTest(SimpleTestCase):
    """Regression: multiline KW reconstruction and row emission."""

    def test_case_a_same_line_row_control(self):
        """
        Case A — same-line row (control).
        Input:  "00166/KW/019937/2026 521115 1.000.000"
        Expect: one row with correct no_bukti, akun, jumlah.
        """
        raw_words = [
            {"text": "00166",  "left": 10,  "top": 10, "width": 60,  "height": 12, "confidence": 90},
            {"text": "/",      "left": 72,  "top": 10, "width": 8,   "height": 12, "confidence": 90},
            {"text": "KW",     "left": 82,  "top": 10, "width": 20,  "height": 12, "confidence": 90},
            {"text": "/",      "left": 104, "top": 10, "width": 8,   "height": 12, "confidence": 90},
            {"text": "019937", "left": 114, "top": 10, "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",      "left": 166, "top": 10, "width": 8,   "height": 12, "confidence": 90},
            {"text": "2026",   "left": 176, "top": 10, "width": 35,  "height": 12, "confidence": 90},
            {"text": "521115", "left": 250, "top": 10, "width": 50,  "height": 12, "confidence": 90},
            {"text": "1.000.000", "left": 500, "top": 10, "width": 80, "height": 12, "confidence": 90},
        ]
        items = parse_drpp_items_from_tsv_rows(raw_words)
        self.assertEqual(len(items), 1, f"Expected 1 row, got {len(items)}: {items}")
        self.assertIn("521115", items[0]["akun"])
        self.assertEqual(items[0]["jumlah"], Decimal("1000000"))

    def test_case_b_multiline_kw_split(self):
        """
        Case B — multiline row: KW number split across two lines.
        Line 1: "00166"  (bare numeric prefix, no /KW/)
        Line 2: "/KW/019937/2026 521115 1.000.000"

        This pattern occurs when OCR renders the slash as a line break.
        The parser must merge adjacent lines to reconstruct the full KW number.
        """
        raw_words = [
            # Line 1: just "00166"
            {"text": "00166", "left": 10, "top": 10,  "width": 60, "height": 12, "confidence": 90},
            # Line 2: /KW/... + akun + amount
            {"text": "/",       "left": 10,  "top": 22, "width": 8,  "height": 12, "confidence": 90},
            {"text": "KW",      "left": 20,  "top": 22, "width": 20, "height": 12, "confidence": 90},
            {"text": "/",       "left": 42,  "top": 22, "width": 8,  "height": 12, "confidence": 90},
            {"text": "019937",  "left": 52,  "top": 22, "width": 50, "height": 12, "confidence": 90},
            {"text": "/",       "left": 104, "top": 22, "width": 8,  "height": 12, "confidence": 90},
            {"text": "2026",    "left": 114, "top": 22, "width": 35, "height": 12, "confidence": 90},
            {"text": "521115",  "left": 250, "top": 22, "width": 50, "height": 12, "confidence": 90},
            {"text": "1.000.000","left": 500, "top": 22, "width": 80,"height": 12, "confidence": 90},
        ]
        items = parse_drpp_items_from_tsv_rows(raw_words)
        self.assertEqual(len(items), 1, f"Case B failed: expected 1 row, got {len(items)}: {items}")
        self.assertIn("521115", items[0]["akun"])
        self.assertEqual(items[0]["jumlah"], Decimal("1000000"))

    def test_case_c_description_wrapped_between_fields(self):
        """
        Case C — description wrapped between KW and nominal.
        KW on line 1, description spans lines 2-3, amount on line 4.

        The parser must not associate a distant amount with this row.
        """
        raw_words = [
            # Line 1: KW
            {"text": "00188",  "left": 10,  "top": 10,  "width": 60,  "height": 12, "confidence": 90},
            {"text": "/",      "left": 72,  "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "KW",     "left": 82,  "top": 10,  "width": 20,  "height": 12, "confidence": 90},
            {"text": "/",      "left": 104, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "019937", "left": 114, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",      "left": 166, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "2026",   "left": 176, "top": 10,  "width": 35,  "height": 12, "confidence": 90},
            # Line 2: description part 1
            {"text": "Belanja",   "left": 10, "top": 22, "width": 60,  "height": 12, "confidence": 80},
            {"text": "perjalanan", "left": 72, "top": 22, "width": 80,  "height": 12, "confidence": 80},
            {"text": "dinas",     "left": 155,"top": 22, "width": 50,  "height": 12, "confidence": 80},
            # Line 3: description part 2
            {"text": "sesuai",    "left": 10, "top": 34, "width": 50,  "height": 12, "confidence": 80},
            {"text": "bukti",     "left": 62, "top": 34, "width": 40,  "height": 12, "confidence": 80},
            {"text": "terlampir", "left": 104,"top": 34, "width": 70,  "height": 12, "confidence": 80},
            # Line 4: nominal
            {"text": "2.423.800","left": 500,"top": 46, "width": 80,  "height": 12, "confidence": 90},
        ]
        items = parse_drpp_items_from_tsv_rows(raw_words)
        self.assertGreaterEqual(len(items), 1, f"Case C: expected >=1 row, got {len(items)}")
        # The amount should be found on/near the last line
        row = items[0]
        self.assertEqual(row["jumlah"], Decimal("2423800"),
            f"Amount should be 2423800, got {row['jumlah']}")

    def test_case_d_multiple_independent_rows(self):
        """
        Case D — multiple independent rows.
        Two different KW numbers must produce two separate transaction rows.
        Do not collapse them.
        """
        raw_words = [
            # Row 1 — KW 00166
            {"text": "00166",   "left": 10,  "top": 10,  "width": 60,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 72,  "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "KW",      "left": 82,  "top": 10,  "width": 20,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 104, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "019937",   "left": 114, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 166, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "2026",    "left": 176, "top": 10,  "width": 35,  "height": 12, "confidence": 90},
            {"text": "521115",  "left": 250, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "1.000.000","left": 500, "top": 10,  "width": 80,  "height": 12, "confidence": 90},
            # Row 2 — KW 00188
            {"text": "00188",   "left": 10,  "top": 60,  "width": 60,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 72,  "top": 60,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "KW",      "left": 82,  "top": 60,  "width": 20,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 104, "top": 60,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "019937",  "left": 114, "top": 60,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 166, "top": 60,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "2026",   "left": 176, "top": 60,  "width": 35,  "height": 12, "confidence": 90},
            {"text": "524111",  "left": 250, "top": 60,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "2.423.800","left": 500, "top": 60, "width": 80,  "height": 12, "confidence": 90},
        ]
        items = parse_drpp_items_from_tsv_rows(raw_words)
        self.assertEqual(len(items), 2,
            f"Case D: expected 2 rows, got {len(items)}: {[(i.get('akun'), i.get('jumlah')) for i in items]}")
        akun_set = {i["akun"] for i in items}
        amount_set = {str(i["jumlah"]) for i in items}
        self.assertIn("521115", akun_set)
        self.assertIn("524111", akun_set)
        self.assertIn("1000000", amount_set)
        self.assertIn("2423800", amount_set)

    def test_case_e_no_total_difference_inference(self):
        """
        Case E — no total-difference inference.
        Only one row's evidence is available; the second row must NOT be
        invented to fill the gap between a printed total and the single row.

        Printed total would be 3.423.800, but only evidence for 1.000.000
        exists in OCR. Expect exactly 1 row.
        """
        raw_words = [
            # Only one KW present — no second KW number in OCR
            {"text": "00166",   "left": 10,  "top": 10,  "width": 60,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 72,  "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "KW",      "left": 82,  "top": 10,  "width": 20,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 104, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "019937",  "left": 114, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 166, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "2026",    "left": 176, "top": 10,  "width": 35,  "height": 12, "confidence": 90},
            {"text": "521115",  "left": 250, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "1.000.000","left": 500, "top": 10,  "width": 80,  "height": 12, "confidence": 90},
        ]
        items = parse_drpp_items_from_tsv_rows(raw_words)
        # Exactly 1 row — NOT 2 rows invented to match 3.423.800 total
        self.assertEqual(len(items), 1,
            f"Case E: expected exactly 1 row (no invented row), got {len(items)}")
        self.assertEqual(items[0]["jumlah"], Decimal("1000000"))

    def test_case_f_distant_fields_with_date_boundary(self):
        """
        Case F — when a date separator appears between KW+akun and the amount,
        the amount must NOT be associated with this row (it belongs to the next
        logical block after the date).

        KW row at top=10, date separator at top=200, unrelated amount at top=400.
        The amount is after the date boundary so it must NOT be captured as
        this row's jumlah.
        """
        raw_words = [
            # Line 1: KW kuitansi (no amount on this line)
            {"text": "00166",   "left": 10,  "top": 10,  "width": 60,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 72,  "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "KW",      "left": 82,  "top": 10,  "width": 20,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 104, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "019937",  "left": 114, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 166, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "2026",   "left": 176, "top": 10,  "width": 35,  "height": 12, "confidence": 90},
            {"text": "521115",  "left": 250, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            # Line 2: date separator — blocks continuation for this KW row
            {"text": "15/01/2026", "left": 10, "top": 200, "width": 80, "height": 12, "confidence": 80},
            # Line 3: unrelated header text
            {"text": "Pembayaran", "left": 10, "top": 350, "width": 100, "height": 12, "confidence": 80},
            # Line 4: amount for the NEXT section, NOT this KW row
            {"text": "9.999.999", "left": 500, "top": 400, "width": 80, "height": 12, "confidence": 90},
        ]
        items = parse_drpp_items_from_tsv_rows(raw_words)
        # The amount belongs to a section starting AFTER the date separator,
        # not to the KW row above it. So either: (a) the row has jumlah=0
        # because no amount was found before the date boundary, or
        # (b) the amount is not captured at all.
        # Most importantly: the amount 9.999.999 must NOT appear as this row's jumlah.
        amounts_emitted = [str(i["jumlah"]) for i in items]
        self.assertNotIn(
            "9999999", amounts_emitted,
            f"Case F: unrelated amount after date boundary was incorrectly "
            f"captured. Items: {[(i.get('akun'), str(i.get('jumlah'))) for i in items]}"
        )

    def test_case_b_multiline_real_drpp_00025_pattern(self):
        """
        Real-world regression: DRPP 00025 with OCR splitting "00166" and
        "/KW/019937/2026" across two lines.

        Expected rows:
          00166/KW/019937/2026  | 521115 | 1.000.000
          00188/KW/019937/2026  | 524111 | 2.423.800

        Printed total: 3.423.800 — both rows must be emitted.
        """
        raw_words = [
            # Row 1 — KW 00166 split across lines 1-2
            {"text": "00166",   "left": 10,  "top": 10,  "width": 60,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 10,  "top": 22,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "KW",      "left": 20,  "top": 22,  "width": 20,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 42,  "top": 22,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "019937",  "left": 52,  "top": 22,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 104, "top": 22,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "2026",    "left": 114, "top": 22,  "width": 35,  "height": 12, "confidence": 90},
            {"text": "521115",  "left": 250, "top": 22,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "1.000.000","left": 500, "top": 22,  "width": 80,  "height": 12, "confidence": 90},
            # Row 2 — KW 00188 split across lines 3-4
            {"text": "00188",   "left": 10,  "top": 80,  "width": 60,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 10,  "top": 92,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "KW",      "left": 20,  "top": 92,  "width": 20,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 42,  "top": 92,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "019937",  "left": 52,  "top": 92,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 104, "top": 92,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "2026",    "left": 114, "top": 92,  "width": 35,  "height": 12, "confidence": 90},
            {"text": "524111",  "left": 250, "top": 92,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "2.423.800","left": 500, "top": 92,  "width": 80,  "height": 12, "confidence": 90},
        ]
        items = parse_drpp_items_from_tsv_rows(raw_words)
        self.assertEqual(len(items), 2,
            f"DRPP 00025: expected 2 rows, got {len(items)}: "
            f"{[(i.get('akun'), str(i.get('jumlah'))) for i in items]}")
        akun_set = {i["akun"] for i in items}
        amount_sum = sum(i["jumlah"] for i in items)
        self.assertIn("521115", akun_set)
        self.assertIn("524111", akun_set)
        self.assertEqual(amount_sum, Decimal("3423800"),
            f"Row sum should be 3423800, got {amount_sum}")
