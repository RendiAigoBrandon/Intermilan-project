from copy import deepcopy
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from .document_policy import SPMFamily
from .drpp_batch_parser import (
    _classification,
    _kkp_money_tokens,
    _kkp_payment_orders,
    _match_kkp_receipts,
    _sanitize_kkp_description,
    evaluate_kkp_group_commitability,
    parse_kkp_reference,
)


def word(text, left, top, width=None):
    return {
        "text": str(text),
        "left": left,
        "top": top,
        "width": width or max(12, len(str(text)) * 8),
        "height": 20,
        "confidence": 90,
    }


def page(number, document_type, text="", words=None):
    return {
        "file_name": "synthetic.pdf",
        "page_number": number,
        "document_type": document_type,
        "text": text,
        "tsv_words": words or [],
        "engine": "tesseract",
        "confidence": 90,
    }


class GUPKKPParserTests(SimpleTestCase):
    def setUp(self):
        payment_words = [
            word("Kode", 500, 100), word("Pembayaran", 760, 100), word("Akun", 620, 100),
            word("1", 20, 200), word("524111", 600, 200),
            word("Perjalanan", 700, 200), word("dinas", 790, 200), word("4.076.000", 950, 200),
            word("2", 20, 300), word("524111", 600, 300),
            word("Penginapan", 700, 300), word("5.700.000", 950, 300),
        ]
        statement_words = [
            word("21-05-2026", 20, 200), word("21-05-2026", 130, 200),
            word("HOTEL-A", 300, 200), word("1.076.000", 900, 200),
            word("25-05-2026", 20, 240), word("25-05-2026", 130, 240),
            word("ADMIN", 300, 240), word("66.000", 900, 240),
            word("26-05-2026", 20, 280), word("26-05-2026", 130, 280),
            word("PEMBAYARAN", 300, 280), word("66.000", 900, 280), word("CR", 980, 280),
            word("28-05-2026", 20, 320), word("30-05-2026", 130, 320),
            word("HOTEL-B", 300, 320), word("5.700.000", 900, 320),
        ]
        self.pages = [
            page(
                1,
                "KKP_PAYMENT_LIST",
                "DAFTAR PEMBAYARAN TAGIHAN KARTU KREDIT PEMERINTAH",
                payment_words,
            ),
            page(
                2,
                "KKP_CARD_STATEMENT",
                "LEMBAR PENAGIHAN KARTU KREDIT PEMERINTAH",
                statement_words,
            ),
            page(
                3,
                "DRPP_COA",
                "LAMPIRAN SURAT PERINTAH MEMBAYAR DETAIL COA "
                "019937.010.524111.05401GG.2902BMA.A000000001.00000.2.0800.2.000000.000000 "
                "006.523.0A.000337 Perjalanan dinas 1.076.000,00 "
                "006.530.0B.000491 Penginapan 5.700.000,00",
            ),
            page(
                4,
                "KKP_PAYMENT_ORDER",
                "SURAT PERINTAH BAYAR KKP Rp. 1.076.000,00 Kode: 524111 "
                "Kuitansi/bukti: 00095/KW/KKP/019937/2026",
            ),
            page(5, "SPP", "SURAT PERMINTAAN PEMBAYARAN Nomor 00207T"),
            page(6, "KUITANSI", "Rp360.000 Rp291.000 Rp425.000"),
        ]
        self.spm = {
            "metadata": {
                "nomor_spm": "00207A",
                "tanggal_spm": date(2026, 7, 16),
                "jenis_spm": "GUP-KKP",
                "jumlah_pengeluaran": Decimal("6776000"),
                "total_pembayaran": Decimal("6776000"),
                "bulan_sp2d": None,
            }
        }

    def test_classifier_requires_strong_payment_list_anchor(self):
        self.assertEqual(
            _classification("DAFTAR PEMBAYARAN TAGIHAN KARTU KREDIT PEMERINTAH")[0],
            "KKP_PAYMENT_LIST",
        )
        self.assertEqual(
            _classification("Lembar Penagihan BNI Kartu Kredit Pemerintah")[0],
            "KKP_CARD_STATEMENT",
        )
        self.assertNotEqual(_classification("Informasi kartu kredit nasabah")[0], "KKP_PAYMENT_LIST")

    def test_payment_order_accepts_generic_ocr_account_labels(self):
        labels = ("KODE", "KOD", "KD", "KODE AKUN", "KOD AKUN", "AKUN", "Kod.", "kd.", "akun")
        for label in labels:
            with self.subTest(label=label):
                orders = _kkp_payment_orders([
                    page(
                        7,
                        "KKP_PAYMENT_ORDER",
                        "SURAT PERINTAH BAYAR KKP Rp. 1.076.000,00 "
                        f"{label}: 524111 Kuitansi/bukti: 00095/KW/KKP/019937/2026",
                    )
                ])
                self.assertEqual(len(orders), 1)
                self.assertEqual(orders[0]["akun"], "524111")
                self.assertEqual(orders[0]["jumlah"], Decimal("1076000"))
                self.assertEqual(orders[0]["no_kuitansi"], "00095/KW/KKP/019937/2026")

    def test_receipt_matching_requires_exact_amount_and_uses_account_evidence(self):
        rows = [
            {"akun": "524111", "jumlah": Decimal("1076000"), "keperluan": "Perjalanan", "source_page": 1},
            {"akun": "524111", "jumlah": Decimal("5700000"), "keperluan": "Penginapan", "source_page": 1},
        ]
        order = _kkp_payment_orders([self.pages[3]])[0]
        matches, ambiguous = _match_kkp_receipts(
            rows, [row["jumlah"] for row in rows], [order]
        )
        self.assertEqual(matches, {0: order})
        self.assertEqual(ambiguous, set())
        self.assertNotIn(1, matches)

        wrong_account = {**order, "akun": "521111"}
        matches, ambiguous = _match_kkp_receipts(
            rows, [row["jumlah"] for row in rows], [wrong_account]
        )
        self.assertEqual(matches, {})
        self.assertEqual(ambiguous, set())

    def test_equal_amount_without_distinguishing_evidence_is_ambiguous(self):
        rows = [
            {"akun": "524111", "jumlah": Decimal("100"), "keperluan": "Belanja", "source_page": 1},
            {"akun": "524111", "jumlah": Decimal("100"), "keperluan": "Belanja", "source_page": 1},
        ]
        order = {
            "no_kuitansi": "001/KW/KKP/019937/2026",
            "akun": "524111",
            "jumlah": Decimal("100"),
            "description": "",
            "recipient": "",
            "page": page(4, "KKP_PAYMENT_ORDER"),
        }
        matches, ambiguous = _match_kkp_receipts(rows, [Decimal("100"), Decimal("100")], [order])
        self.assertEqual(matches, {})
        self.assertEqual(ambiguous, {0, 1})

    def test_equal_amount_package_stays_review_instead_of_guessing_receipt(self):
        pages = deepcopy(self.pages)
        for item in pages[0]["tsv_words"]:
            if item["text"] in {"4.076.000", "5.700.000"}:
                item["text"] = "3.388.000"
        pages[3]["text"] = pages[3]["text"].replace("1.076.000", "3.388.000")
        reference, validation = parse_kkp_reference(pages, deepcopy(self.spm), "abc123")
        self.assertFalse(validation["can_commit"])
        self.assertTrue(all(item["status"] == "PERLU_REVIEW" for item in reference["items"]))
        self.assertTrue(all(item["no_kuitansi"] == "" for item in reference["items"]))
        self.assertTrue(all(item["receipt_policy"] == "ambiguous_source" for item in reference["items"]))

    def test_reconciles_two_rows_without_flattening_supporting_receipts(self):
        reference, validation = parse_kkp_reference(self.pages, self.spm, "abc123")
        items = reference["items"]
        self.assertEqual([item["nilai_bruto"] for item in items], [Decimal("1076000"), Decimal("5700000")])
        self.assertEqual(sum(item["nilai_bruto"] for item in items), Decimal("6776000"))
        self.assertEqual(len(items), 2)
        self.assertTrue(validation["can_commit"], validation["errors"])
        self.assertEqual(reference["metadata"]["nomor_spp"], "00207T")
        self.assertEqual(reference["metadata"]["payment_list_total"], Decimal("6776000"))
        self.assertEqual(reference["metadata"]["printed_total"], Decimal("6776000"))
        self.assertEqual(reference["metadata"]["canonical_total"], Decimal("6776000"))
        self.assertEqual(reference["metadata"]["total_resolution_status"], "CONSENSUS")

    def test_indonesian_money_formats_are_normalized_without_factor_guessing(self):
        for raw in (
            "6.776.000",
            "6.776.000,00",
            "Rp6.776.000",
            "Rp 6.776.000,00",
            "6 776 000",
            "6776000",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(_kkp_money_tokens(raw), [Decimal("6776000")])
        self.assertEqual(_kkp_money_tokens("6.776.00000"), [])
        for suspect in ("677.600.000", "677600000"):
            self.assertEqual(_kkp_money_tokens(suspect), [Decimal("677600000")])
            self.assertNotEqual(_kkp_money_tokens(suspect), [Decimal("6776000")])

    def test_header_outlier_is_preserved_but_consensus_stays_balance(self):
        spm = deepcopy(self.spm)
        spm["metadata"]["jumlah_pengeluaran"] = Decimal("677600000")
        spm["metadata"]["total_pembayaran"] = Decimal("677600000")
        reference, validation = parse_kkp_reference(self.pages, spm, "abc123")
        metadata = reference["metadata"]
        self.assertEqual(metadata["spm_header_total_raw"], Decimal("677600000"))
        self.assertEqual(metadata["payment_list_total"], Decimal("6776000"))
        self.assertEqual(metadata["canonical_total"], Decimal("6776000"))
        self.assertEqual(metadata["printed_total"], Decimal("6776000"))
        self.assertGreaterEqual(len(metadata["total_resolution_sources"]), 2)
        self.assertIn("CARD_STATEMENT", metadata["total_resolution_sources"])
        self.assertIn("SPM_DETAIL", metadata["total_resolution_sources"])
        self.assertTrue(metadata["total_resolution_warnings"])
        self.assertTrue(metadata["total_provenance"]["spm_header_total_raw"]["suspect"])
        self.assertEqual(
            metadata["total_provenance"]["spm_header_total_raw"]["raw_token"],
            "677600000",
        )
        self.assertEqual(validation["status"], "BALANCE")
        self.assertTrue(validation["can_commit"], validation["errors"])

    def test_footer_year_and_duplicate_detail_page_do_not_change_totals(self):
        pages = deepcopy(self.pages)
        pages[0]["tsv_words"].append(word("2026", 950, 500))
        pages[2]["page_hash"] = "same-detail"
        duplicate = deepcopy(pages[2])
        duplicate["page_number"] = 7
        duplicate["page_hash"] = "same-detail"
        pages.append(duplicate)
        reference, validation = parse_kkp_reference(pages, deepcopy(self.spm), "abc123")
        self.assertEqual(reference["metadata"]["payment_list_raw_total"], Decimal("9776000"))
        self.assertEqual(reference["metadata"]["spm_detail_total"], Decimal("6776000"))
        self.assertEqual(reference["metadata"]["canonical_total"], Decimal("6776000"))
        self.assertTrue(validation["can_commit"], validation["errors"])

    def test_no_consensus_blocks_instead_of_dividing_outlier(self):
        spm = deepcopy(self.spm)
        spm["metadata"]["jumlah_pengeluaran"] = Decimal("677600000")
        spm["metadata"]["total_pembayaran"] = Decimal("677600000")
        pages = [self.pages[0], self.pages[3], self.pages[4]]
        reference, validation = parse_kkp_reference(pages, spm, "abc123")
        self.assertEqual(reference["metadata"]["canonical_total"], Decimal("0"))
        self.assertEqual(reference["metadata"]["printed_total"], Decimal("0"))
        self.assertEqual(reference["metadata"]["total_resolution_status"], "PERLU_REVIEW")
        self.assertFalse(validation["can_commit"])
        self.assertEqual(validation["status"], "PERLU_REVIEW")

    def test_payment_and_canonical_totals_are_validated_separately(self):
        reference, _validation = parse_kkp_reference(self.pages, deepcopy(self.spm), "abc123")
        for field in ("payment_list_total", "canonical_total"):
            with self.subTest(field=field):
                changed = deepcopy(reference)
                changed["metadata"][field] = Decimal("1")
                validation = evaluate_kkp_group_commitability(changed, changed["items"])
                self.assertFalse(validation["can_commit"])

    def test_description_noise_is_removed_and_raw_value_remains_in_provenance(self):
        noisy = "Belanja Barang Non . A $ | - Perjalanan dinas"
        cleaned = _sanitize_kkp_description(noisy)
        self.assertNotIn("Non . A", cleaned)
        self.assertNotIn("$", cleaned)
        self.assertNotIn("| -", cleaned)
        reference, _validation = parse_kkp_reference(self.pages, deepcopy(self.spm), "abc123")
        first = reference["items"][0]
        self.assertTrue(first["deskripsi"])
        self.assertIn("raw_value", first["field_provenance"]["deskripsi"])
        self.assertEqual(first["pph21"], Decimal("0"))
        self.assertEqual(
            first["field_provenance"]["pph21"]["extraction_method"],
            "confirmed_zero",
        )

    def test_business_identifiers_and_receipt_provenance(self):
        reference, _validation = parse_kkp_reference(self.pages, self.spm, "abc123")
        first, second = reference["items"]
        self.assertEqual(reference["metadata"]["nomor_drpp"], "")
        self.assertEqual(first["no_drpp"], "")
        self.assertEqual(second["no_drpp"], "")
        self.assertTrue(reference["metadata"]["group_key"].startswith("KKP:"))
        self.assertNotIn(reference["metadata"]["group_key"], (first["no_kuitansi"], second["no_kuitansi"]))
        self.assertEqual(first["no_kuitansi"], "00095/KW/KKP/019937/2026")
        self.assertEqual(second["no_kuitansi"], "")
        self.assertEqual(second["receipt_policy"], "not_available_from_source")
        self.assertTrue(second["receipt_not_available_from_source"])
        self.assertIsNone(second["bulan_sp2d"])
        self.assertEqual(first["pembebanan"], "2902.BMA.006.523.524111")
        self.assertEqual(second["pembebanan"], "2902.BMA.006.523.524111")

    def test_empty_receipt_without_provenance_is_blocked(self):
        reference, _validation = parse_kkp_reference(self.pages, self.spm, "abc123")
        items = deepcopy(reference["items"])
        items[1]["receipt_policy"] = ""
        items[1]["receipt_not_available_from_source"] = False
        validation = evaluate_kkp_group_commitability(reference, items)
        self.assertFalse(validation["can_commit"])
        self.assertIn("Nomor kuitansi kosong tanpa provenance sumber.", validation["errors"])

    def test_total_account_and_charge_mismatch_are_blocked(self):
        reference, _validation = parse_kkp_reference(self.pages, self.spm, "abc123")
        for field, value, expected_error in (
            ("nilai_bruto", Decimal("1"), "Total baris"),
            ("akun", "", "Akun kosong."),
            ("pembebanan", "2902.BMA.006.523.521111", "Pembebanan tidak cocok dengan Akun."),
        ):
            with self.subTest(field=field):
                items = deepcopy(reference["items"])
                items[0][field] = value
                validation = evaluate_kkp_group_commitability(reference, items)
                self.assertFalse(validation["can_commit"])
                self.assertTrue(any(expected_error in error for error in validation["errors"]))

    def test_regular_gup_does_not_enter_kkp_reference_parser(self):
        spm = deepcopy(self.spm)
        spm["metadata"]["jenis_spm"] = "GUP 17"
        self.assertIsNone(parse_kkp_reference(self.pages, spm, "abc123"))
        self.assertEqual(SPMFamily.GUP_REGULAR.value, "GUP_REGULAR")

    def test_raw_jenis_is_preserved_and_business_value_is_canonical(self):
        spm = deepcopy(self.spm)
        spm["metadata"]["jenis_spm"] = "GUP-Kkp"
        reference, validation = parse_kkp_reference(self.pages, spm, "abc123")
        self.assertTrue(validation["can_commit"], validation["errors"])
        self.assertEqual(spm["metadata"]["jenis_spm_raw"], "GUP-Kkp")
        self.assertEqual(spm["metadata"]["jenis_spm"], "GUP-KKP")
        self.assertEqual(reference["metadata"]["jenis_spm"], "GUP-KKP")
        self.assertTrue(all(item["jenis_spm"] == "GUP-KKP" for item in reference["items"]))

    def test_two_confirmed_empty_receipts_with_same_exact_key_are_blocked(self):
        reference, _validation = parse_kkp_reference(self.pages, self.spm, "abc123")
        empty = deepcopy(reference["items"][1])
        reference["metadata"]["source_item_count"] = 2
        reference["metadata"]["printed_total"] = empty["nilai_bruto"] * 2
        reference["metadata"]["spm_total"] = empty["nilai_bruto"] * 2
        validation = evaluate_kkp_group_commitability(reference, [empty, deepcopy(empty)])
        self.assertFalse(validation["can_commit"])
        self.assertIn("Duplikat exact key ditemukan dalam upload yang sama.", validation["errors"])
