from decimal import Decimal

from django.test import SimpleTestCase

from apps.core.parsers import (
    consensus_sp2d_number,
    parse_detail_sp2d_rows_from_tsv_lines,
    reconcile_single_missing_detail_amount,
)


class StructuralAmountReconciliationTests(SimpleTestCase):
    def test_single_missing_amount_is_balanced_from_document_total(self):
        rows = [
            {"jumlah": Decimal("100"), "source_page": 1},
            {"jumlah": Decimal("0"), "source_page": 1, "amount_missing": True},
        ]

        actual = reconcile_single_missing_detail_amount(rows, Decimal("104"))

        self.assertEqual(actual[1]["jumlah"], Decimal("4"))
        self.assertEqual(actual[1]["bruto"], Decimal("4"))
        self.assertTrue(actual[1]["amount_reconciled_from_total"])
        self.assertIsNone(actual[1]["field_provenance"]["bruto"]["confidence"])

    def test_multiple_missing_amounts_remain_unresolved(self):
        rows = [
            {"jumlah": Decimal("0"), "amount_missing": True},
            {"jumlah": Decimal("0"), "amount_missing": True},
        ]

        actual = reconcile_single_missing_detail_amount(rows, Decimal("4"))

        self.assertEqual([row["jumlah"] for row in actual], [Decimal("0"), Decimal("0")])
        self.assertFalse(any(row.get("amount_reconciled_from_total") for row in actual))

    def test_structural_tsv_row_without_amount_is_preserved_for_balance(self):
        row_text = (
            "019937 00140T/019937/2026 260100000026165 2026-06-02 "
            "019937.010.511119.05401WA.2886EBA.A000000001.00000.2.0800.2."
            "000000.000000.994.001.0A.000184"
        )
        lines = [{
            "text": row_text,
            "words": [{"text": row_text, "left": 0, "top": 10, "width": 1000, "height": 20}],
            "confidence": 91.0,
        }]

        rows = parse_detail_sp2d_rows_from_tsv_lines(
            "unused.pdf", 1, 270, lines, ["DETAIL_SPP_SPM_SP2D"], {}
        )
        actual = reconcile_single_missing_detail_amount(rows, Decimal("4"))

        self.assertEqual(len(actual), 1)
        self.assertEqual(actual[0]["akun"], "511119")
        self.assertEqual(actual[0]["jumlah"], Decimal("4"))


class StructuredSp2dConsensusTests(SimpleTestCase):
    def test_unique_repeated_number_wins_over_noisy_rows(self):
        items = [
            {"nomor_sp2d": "260100000026165"},
            {"nomor_sp2d": "280100000026165"},
            {"nomor_sp2d": "260100000026165"},
        ]
        self.assertEqual(consensus_sp2d_number(items), "260100000026165")

    def test_single_candidate_or_tie_does_not_claim_consensus(self):
        self.assertEqual(consensus_sp2d_number([{"nomor_sp2d": "260100000026165"}]), "")
        self.assertEqual(
            consensus_sp2d_number([
                {"nomor_sp2d": "260100000026165"},
                {"nomor_sp2d": "260100000026165"},
                {"nomor_sp2d": "280100000026165"},
                {"nomor_sp2d": "280100000026165"},
            ]),
            "",
        )
