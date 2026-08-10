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


class DRPPKWWRecoveryTest(SimpleTestCase):
    """Regression: OCR noise 'KWW' in receipt marker must be tolerated."""

    def test_case_a_standard_kw_is_unchanged(self):
        """
        Case A — standard /KW/ receipt is parsed normally.
        00166/KW/019937/2026 -> unchanged.
        """
        raw_words = [
            {"text": "00166",   "left": 10,  "top": 10,  "width": 60,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 72,  "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "KW",     "left": 82,  "top": 10,  "width": 20,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 104, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "019937",  "left": 114, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 166, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "2026",    "left": 176, "top": 10,  "width": 35,  "height": 12, "confidence": 90},
            {"text": "521115",  "left": 250, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "1.000.000","left": 500, "top": 10,  "width": 80,  "height": 12, "confidence": 90},
        ]
        items = parse_drpp_items_from_tsv_rows(raw_words)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["no_bukti"], "00166/KW/019937/2026")
        self.assertEqual(items[0]["akun"], "521115")
        self.assertEqual(items[0]["jumlah"], Decimal("1000000"))

    def test_case_b_kww_is_normalized_to_kw(self):
        """
        Case B — /KWW/ OCR noise normalized to /KW/.
        00188/KWW/019937/2026 -> 00188/KW/019937/2026.
        """
        raw_words = [
            {"text": "00188",   "left": 10,  "top": 10,  "width": 60,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 72,  "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "KWW",    "left": 82,  "top": 10,  "width": 20,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 104, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "019937",  "left": 114, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 166, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "2026",    "left": 176, "top": 10,  "width": 35,  "height": 12, "confidence": 90},
            {"text": "524111",  "left": 250, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "2.423.800","left": 500, "top": 10,  "width": 80,  "height": 12, "confidence": 90},
        ]
        items = parse_drpp_items_from_tsv_rows(raw_words)
        self.assertEqual(len(items), 1, f"Case B: expected 1 row, got {len(items)}")
        self.assertEqual(items[0]["no_bukti"], "00188/KW/019937/2026",
            f"Should normalize KWW to KW, got: {items[0]['no_bukti']}")
        self.assertEqual(items[0]["akun"], "524111")
        self.assertEqual(items[0]["jumlah"], Decimal("2423800"))

    def test_case_c_kww_with_ocr_spacing(self):
        """
        Case C — /KWW/ with OCR spacing variations still normalizes correctly.
        """
        raw_words = [
            {"text": "00188",   "left": 10,  "top": 10,  "width": 60,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 72,  "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": " KWW",   "left": 82,  "top": 10,  "width": 30,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 114, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "019937",  "left": 124, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 176, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "2026",    "left": 186, "top": 10,  "width": 35,  "height": 12, "confidence": 90},
            {"text": "524111",  "left": 250, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "2.423.800","left": 500, "top": 10,  "width": 80,  "height": 12, "confidence": 90},
        ]
        items = parse_drpp_items_from_tsv_rows(raw_words)
        self.assertEqual(len(items), 1, f"Case C: expected 1 row, got {len(items)}")
        self.assertEqual(items[0]["no_bukti"], "00188/KW/019937/2026")

    def test_case_d_random_prose_kww_not_receipt(self):
        """
        Case D — 'KWW' in random prose must NOT become a receipt number.
        The normalization is scoped: it only fires when surrounded by valid
        receipt number structure (prefix + marker + satker# + year).
        """
        raw_words = [
            # Not a valid receipt — no numeric prefix before KWW
            {"text": "KWW",     "left": 10,  "top": 10,  "width": 30,  "height": 12, "confidence": 80},
            {"text": "ini",    "left": 42,  "top": 10,  "width": 30,  "height": 12, "confidence": 80},
            {"text": "bukan",  "left": 74,  "top": 10,  "width": 40,  "height": 12, "confidence": 80},
            {"text": "kwitansi", "left": 116, "top": 10, "width": 60,  "height": 12, "confidence": 80},
        ]
        items = parse_drpp_items_from_tsv_rows(raw_words)
        # No valid receipt pattern → no items emitted
        self.assertEqual(len(items), 0,
            f"Case D: KWW in prose should not emit rows. Got: {items}")

    def test_case_e_multiple_kww_rows(self):
        """
        Case F — multiple rows, one with standard KW, one with KWW.
        Both rows must be emitted independently.
        """
        raw_words = [
            # Row 1: standard /KW/
            {"text": "00166",   "left": 10,  "top": 10,  "width": 60,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 72,  "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "KW",     "left": 82,  "top": 10,  "width": 20,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 104, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "019937",  "left": 114, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 166, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "2026",    "left": 176, "top": 10,  "width": 35,  "height": 12, "confidence": 90},
            {"text": "521115",  "left": 250, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "1.000.000","left": 500, "top": 10,  "width": 80,  "height": 12, "confidence": 90},
            # Row 2: KWW
            {"text": "00188",   "left": 10,  "top": 60,  "width": 60,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 72,  "top": 60,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "KWW",    "left": 82,  "top": 60,  "width": 20,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 104, "top": 60,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "019937",  "left": 114, "top": 60,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 166, "top": 60,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "2026",    "left": 176, "top": 60,  "width": 35,  "height": 12, "confidence": 90},
            {"text": "524111",  "left": 250, "top": 60,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "2.423.800","left": 500, "top": 60,  "width": 80,  "height": 12, "confidence": 90},
        ]
        items = parse_drpp_items_from_tsv_rows(raw_words)
        self.assertEqual(len(items), 2,
            f"Case E: expected 2 rows, got {len(items)}")
        akun_set = {i["akun"] for i in items}
        amount_sum = sum(i["jumlah"] for i in items)
        self.assertIn("521115", akun_set)
        self.assertIn("524111", akun_set)
        self.assertEqual(amount_sum, Decimal("3423800"),
            f"Combined sum should be 3423800, got {amount_sum}")

    def test_real_stored_production_kww_00188(self):
        """
        Real production: '00188/KWW/019937/2026' with account 524111 and
        amount 2,423,800. After KWW normalization, the row must be emitted
        with canonical KW and correct values.

        Combined with standard row 00166: sum = 3,423,800.
        """
        raw_words = [
            # Standard row: 00166/KW/019937/2026
            {"text": "00166",   "left": 10,  "top": 10,  "width": 60,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 72,  "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "KW",     "left": 82,  "top": 10,  "width": 20,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 104, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "019937",  "left": 114, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 166, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "2026",    "left": 176, "top": 10,  "width": 35,  "height": 12, "confidence": 90},
            {"text": "521115",  "left": 250, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "1.000.000","left": 500, "top": 10,  "width": 80,  "height": 12, "confidence": 90},
            # KWW row: 00188/KWW/019937/2026
            {"text": "00188",   "left": 10,  "top": 60,  "width": 60,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 72,  "top": 60,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "KWW",    "left": 82,  "top": 60,  "width": 20,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 104, "top": 60,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "019937",  "left": 114, "top": 60,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",       "left": 166, "top": 60,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "2026",    "left": 176, "top": 60,  "width": 35,  "height": 12, "confidence": 90},
            {"text": "524111",  "left": 250, "top": 60,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "2,423,800","left": 500,"top": 60,  "width": 80,  "height": 12, "confidence": 90},
        ]
        items = parse_drpp_items_from_tsv_rows(raw_words)
        self.assertEqual(len(items), 2,
            f"Real KWW: expected 2 rows, got {len(items)}")
        akun_map = {i["akun"] for i in items}
        amount_map = {i["jumlah"] for i in items}
        kw_map = {i["no_bukti"] for i in items}
        self.assertIn("521115", akun_map, "Standard KW row should have akun 521115")
        self.assertIn("524111", akun_map, "KWW row should have akun 524111")
        self.assertIn("00188/KW/019937/2026", kw_map,
            f"KWW row must normalize to KW. Got: {kw_map}")
        self.assertIn(Decimal("1000000"), amount_map)
        self.assertIn(Decimal("2423800"), amount_map,
            f"Amount 2,423,800 should be parsed. Got amounts: {amount_map}")
        self.assertEqual(sum(amount_map), Decimal("3423800"),
            f"Combined sum should be 3423800, got {sum(amount_map)}")

    def test_case_g_kww_with_leading_ocr_noise(self):
        """
        Case G — real stored production TSV has leading OCR row noise
        ('a 2') before the receipt number. The KWW recovery must still
        find and normalize the receipt candidate anywhere in the line.

        Real production line:
        a 2 00188/KWW/019937/2026 Boy Azef 001858539201000 524111 2,423,800
        """
        raw_words = [
            # Leading OCR noise tokens
            {"text": "a",     "left": 10,  "top": 10,  "width": 20,  "height": 12, "confidence": 60},
            {"text": "2",     "left": 32,  "top": 10,  "width": 20,  "height": 12, "confidence": 60},
            # Receipt number tokens (KWW OCR noise)
            {"text": "00188", "left": 54,  "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",     "left": 106, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "KWW",   "left": 116, "top": 10,  "width": 24,  "height": 12, "confidence": 90},
            {"text": "/",     "left": 142, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "019937","left": 152, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",     "left": 204, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "2026",  "left": 214, "top": 10,  "width": 35,  "height": 12, "confidence": 90},
            # Description / NPWP continuation line
            {"text": "Boy",   "left": 10,  "top": 22,  "width": 30,  "height": 12, "confidence": 80},
            {"text": "Azef",  "left": 42,  "top": 22,  "width": 40,  "height": 12, "confidence": 80},
            {"text": "001858539201000", "left": 84, "top": 22, "width": 120, "height": 12, "confidence": 90},
            # Account and amount
            {"text": "524111", "left": 250, "top": 22, "width": 50,  "height": 12, "confidence": 90},
            {"text": "2,423,800", "left": 500, "top": 22, "width": 80, "height": 12, "confidence": 90},
        ]
        items = parse_drpp_items_from_tsv_rows(raw_words)
        self.assertEqual(len(items), 1,
            f"Case G: expected 1 row, got {len(items)}: {[i['no_bukti'] for i in items]}")
        self.assertEqual(items[0]["no_bukti"], "00188/KW/019937/2026",
            f"KWW must normalize to KW. Got: {items[0]['no_bukti']}")
        self.assertEqual(items[0]["akun"], "524111")
        self.assertEqual(items[0]["jumlah"], Decimal("2423800"))

    def test_case_h_leading_noise_with_standard_kw_first(self):
        """
        Case H — real production pattern with TWO rows: standard KW first,
        KWW with leading noise second. Combined sum = 3,423,800.
        """
        raw_words = [
            # Row 1: standard KW (clean)
            {"text": "a",     "left": 10,  "top": 10,  "width": 20,  "height": 12, "confidence": 60},
            {"text": "1",     "left": 32,  "top": 10,  "width": 20,  "height": 12, "confidence": 60},
            {"text": "00166", "left": 54,  "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",     "left": 106, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "KW",    "left": 116, "top": 10,  "width": 20,  "height": 12, "confidence": 90},
            {"text": "/",     "left": 138, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "019937","left": 148, "top": 10,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",     "left": 200, "top": 10,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "2026",  "left": 210, "top": 10,  "width": 35,  "height": 12, "confidence": 90},
            {"text": "521115", "left": 250, "top": 10, "width": 50,  "height": 12, "confidence": 90},
            {"text": "1.000.000", "left": 500, "top": 10, "width": 80, "height": 12, "confidence": 90},
            # Row 2: KWW with leading OCR noise
            {"text": "a",     "left": 10,  "top": 60,  "width": 20,  "height": 12, "confidence": 60},
            {"text": "2",     "left": 32,  "top": 60,  "width": 20,  "height": 12, "confidence": 60},
            {"text": "00188", "left": 54,  "top": 60,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",     "left": 106, "top": 60,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "KWW",   "left": 116, "top": 60,  "width": 24,  "height": 12, "confidence": 90},
            {"text": "/",     "left": 142, "top": 60,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "019937","left": 152, "top": 60,  "width": 50,  "height": 12, "confidence": 90},
            {"text": "/",     "left": 204, "top": 60,  "width": 8,   "height": 12, "confidence": 90},
            {"text": "2026",  "left": 214, "top": 60,  "width": 35,  "height": 12, "confidence": 90},
            {"text": "524111", "left": 250, "top": 60, "width": 50,  "height": 12, "confidence": 90},
            {"text": "2,423,800", "left": 500, "top": 60, "width": 80, "height": 12, "confidence": 90},
        ]
        items = parse_drpp_items_from_tsv_rows(raw_words)
        self.assertEqual(len(items), 2,
            f"Case H: expected 2 rows, got {len(items)}")
        akun_set = {i["akun"] for i in items}
        amount_sum = sum(i["jumlah"] for i in items)
        self.assertIn("521115", akun_set)
        self.assertIn("524111", akun_set)
        self.assertEqual(amount_sum, Decimal("3423800"),
            f"Combined sum should be 3423800, got {amount_sum}")
        kw_map = {i["no_bukti"] for i in items}
        self.assertIn("00188/KW/019937/2026", kw_map,
            f"Second row must normalize KWW to KW. Got: {kw_map}")

