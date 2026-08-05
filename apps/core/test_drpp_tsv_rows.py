from decimal import Decimal

from django.test import SimpleTestCase

from apps.core.parsers import (
    clean_description,
    extract_drpp_printed_total,
    extract_drpp_total_candidates,
    parse_drpp_financial_table_rows,
    parse_drpp_pdf,
    parse_drpp_items_from_tsv,
    parse_drpp_items_from_tsv_rows,
    select_drpp_printed_total_candidate,
)


def line_words(text, top):
    words = []
    left = 10
    for token in text.split():
        width = max(10, len(token) * 7)
        words.append(
            {
                "text": token,
                "left": left,
                "top": top,
                "width": width,
                "height": 12,
                "confidence": 80,
            }
        )
        left += width + 8
    return words


class DRPPTSVRowRecoveryTests(SimpleTestCase):
    def test_layout_fallback_recovers_split_receipts_and_four_financial_columns(self):
        def word(text, left, top, width=None):
            return {
                "text": text,
                "left": left,
                "top": top,
                "width": width or max(18, len(text) * 7),
                "height": 16,
                "confidence": 90,
            }

        words = [
            word("00081/KW/", 120, 100), word("521219", 320, 100),
            word("Belanja", 480, 100), word("alat", 560, 100), word("kantor", 610, 100),
            word("2.400.000", 900, 100), word("40.000", 1090, 100), word("2.360.000", 1220, 100),
            word("012345/2028", 120, 124), word("periode", 480, 124), word("Agustus", 550, 124),
            word("00082/KW/", 120, 180), word("523123", 320, 180),
            word("Pemeliharaan", 480, 180), word("perangkat", 590, 180),
            word("3.", 900, 180), word("500.", 925, 180), word("000", 970, 180),
            word("35.000", 1010, 180), word("75.000", 1090, 180), word("3.390.000", 1220, 180),
            word("012345/2028", 120, 204), word("layanan", 480, 204), word("publik", 545, 204),
            word("TOTAL", 120, 260), word("BRUTO", 180, 260),
            word("TOTAL", 440, 260), word("FP", 500, 260),
            word("TOTAL", 700, 260), word("PPh2l", 760, 260),
            word("TOTAL", 960, 260), word("NETO", 1020, 260),
            word("5.900.000", 120, 285), word("35.000", 440, 285),
            word("115.000", 700, 285), word("5.750.000", 960, 285),
        ]

        rows = parse_drpp_financial_table_rows(words, page_number=3)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["no_bukti"], "00081/KW/012345/2028")
        self.assertEqual(rows[0]["keperluan"], "Belanja alat kantor periode Agustus")
        self.assertEqual(rows[0]["bruto"], 2400000)
        self.assertEqual(rows[0]["fp"], 0)
        self.assertEqual(rows[0]["pph21"], 40000)
        self.assertEqual(rows[0]["netto"], 2360000)
        self.assertEqual(rows[1]["no_bukti"], "00082/KW/012345/2028")
        self.assertEqual(rows[1]["keperluan"], "Pemeliharaan perangkat layanan publik")
        self.assertEqual(rows[1]["bruto"], 3500000)
        self.assertEqual(rows[1]["fp"], 35000)
        self.assertEqual(rows[1]["pph21"], 75000)
        self.assertEqual(rows[1]["netto"], 3390000)

    def test_layout_fallback_is_metamorphic_for_shift_width_and_wrapped_description(self):
        def parse_case(shift, scale, receipt, account, gross, net):
            def x(value):
                return shift + int(value * scale)

            def word(text, left, top):
                return {
                    "text": text,
                    "left": x(left),
                    "top": top,
                    "width": max(14, int(len(text) * 7 * scale)),
                    "height": 15,
                    "confidence": 88,
                }

            words = [
                word("No.", 20, 40), word("Kuitansl", 100, 40), word("Akun", 300, 40),
                word("Uraian", 470, 40), word("Jumlah", 900, 40), word("FP", 1010, 40),
                word("PPh2l", 1100, 40), word("Neto", 1230, 40),
                word(receipt.split("/", 2)[0] + "/KW/", 100, 90), word(account, 300, 90),
                word("Pengadaan", 470, 90), word("sarana", 550, 90), word(gross, 900, 90),
                word(net, 1230, 90), word("012345/2028", 100, 114),
                word("pendukung", 470, 114), word("operasional", 560, 114),
                word("kantor", 470, 136),
                word("TOTAL", 100, 180), word("BRUTO", 170, 180),
                word("TOTAL", 430, 180), word("FP", 500, 180),
                word("TOTAL", 690, 180), word("PPh21", 760, 180),
                word("TOTAL", 950, 180), word("NETTO", 1020, 180),
                word(gross, 100, 205), word("0", 430, 205),
                word("0", 690, 205), word(net, 950, 205),
            ]
            return parse_drpp_financial_table_rows(words)[0]

        first = parse_case(0, 1.0, "00456/KW/012345/2028", "521219", "9.750.000", "9.750.000")
        second = parse_case(135, 1.28, "00888/KW/012345/2028", "524111", "12.345.000", "12.345.000")

        self.assertEqual(first["no_bukti"], "00456/KW/012345/2028")
        self.assertEqual(first["keperluan"], "Pengadaan sarana pendukung operasional kantor")
        self.assertEqual(first["jumlah"], 9750000)
        self.assertEqual(second["no_bukti"], "00888/KW/012345/2028")
        self.assertEqual(second["akun"], "524111")
        self.assertEqual(second["jumlah"], 12345000)

    def test_recovers_split_financial_line_and_small_amount(self):
        words = []
        words += line_words("17 00265/KW/019937/2026", 100)
        words += line_words("BNI 001858539201000 521119 13,000", 112)
        words += line_words("10-06-2026 Biaya transfer bank", 135)
        words += line_words("18 00266/KW/019937/2026 BPJS 001858539201000 521111 300", 170)
        words += line_words("15-06-2026 Iuran BPJS", 195)

        rows = parse_drpp_items_from_tsv_rows(words, page_number=2)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["akun"], "521119")
        self.assertEqual(str(rows[0]["jumlah"]), "13000")
        self.assertEqual(str(rows[1]["jumlah"]), "300")

    def test_date_line_does_not_start_a_second_cell_row(self):
        def word(text, left, top):
            return {
                "text": text,
                "left": left,
                "top": top,
                "width": max(12, len(text) * 7),
                "height": 12,
                "confidence": 80,
            }

        words = [
            word("No", 10, 50), word("Tgl", 90, 50), word("Bukti", 135, 50),
            word("Nama", 300, 50), word("Penerima", 345, 50), word("NPWP", 600, 50),
            word("Akun", 750, 50), word("Jumlah", 850, 50),
            word("1", 15, 100), word("00240/KW/019937/2026", 95, 100),
            word("Pertamina", 310, 100), word("018468918051000", 610, 100),
            word("523121", 760, 100), word("200,000", 860, 100),
            word("05-06-2026", 95, 125), word("Pembelian", 310, 125), word("BBM", 385, 125),
        ]

        rows = parse_drpp_items_from_tsv(words, page_number=1)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["no_bukti"], "00240/KW/019937/2026")
        self.assertEqual(str(rows[0]["jumlah"]), "200000")

    def test_recovers_rows_and_multiline_descriptions_without_clean_header(self):
        words = []
        words += line_words("1 O0268/KW/019937/2026 PT Indonesia 010611903051000 522119 2,234,500", 100)
        words += line_words("24-06-2026 Biaya tagihan internet bulan Mei 2026", 125)
        words += line_words("2 00272/KW/019937/2026 PT Pos 010016202093000 521111 353,000", 160)
        words += line_words("24-06-2026 Biaya pengiriman surat dinas", 185)
        words += line_words("dalam rangka layanan perkantoran", 205)

        rows = parse_drpp_items_from_tsv_rows(words, page_number=8)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["no_bukti"], "00268/KW/019937/2026")
        self.assertEqual(rows[0]["akun"], "522119")
        self.assertEqual(str(rows[0]["jumlah"]), "2234500")
        self.assertEqual(rows[1]["keperluan"], "Biaya pengiriman surat dinas dalam rangka layanan perkantoran")
        self.assertEqual(rows[1]["method"], "tsv_row_anchor")

    def test_financial_table_keeps_distinct_gross_net_tax_and_multiline_description(self):
        def word(text, left, top):
            return {
                "text": text,
                "left": left,
                "top": top,
                "width": max(18, len(text) * 7),
                "height": 14,
                "confidence": 91,
            }

        words = [
            word("No.", 20, 50), word("Kuitansi", 100, 50), word("Akun", 300, 50),
            word("Deskripsi", 500, 50), word("Bruto", 1000, 50), word("Netto", 1200, 50),
            word("Pembebanan", 1400, 50), word("PPh21", 1750, 50),
            word("Pemeliharaan", 440, 85), word("perangkat", 570, 85),
            word("kantor", 680, 85), word("bulan", 440, 105), word("Agustus", 500, 105),
            word("00777/KW/045678/2027", 90, 130), word("523123", 300, 130),
            word("4.500.000", 1000, 130), word("4.425.000", 1200, 130),
            word("3012.EFG.111.222.523123", 1400, 130), word("75.000", 1750, 130),
            word("TOTAL", 900, 170), word("DRPP", 970, 170), word("4.500.000", 1050, 170),
        ]

        rows = parse_drpp_financial_table_rows(words, page_number=2)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["no_bukti"], "00777/KW/045678/2027")
        self.assertEqual(rows[0]["akun"], "523123")
        self.assertEqual(rows[0]["keperluan"], "Pemeliharaan perangkat kantor bulan Agustus")
        self.assertEqual(rows[0]["bruto"], 4500000)
        self.assertEqual(rows[0]["netto"], 4425000)
        self.assertEqual(rows[0]["pph21"], 75000)
        self.assertEqual(rows[0]["pembebanan"], "3012.EFG.111.222.523123")

    def test_financial_table_is_metamorphic_for_identity_and_amount_changes(self):
        def parse_case(receipt, account, gross, net, tax, charge):
            positions = {
                "Kuitansi": 100, "Akun": 300, "Deskripsi": 500, "Bruto": 1000,
                "Netto": 1200, "Pembebanan": 1400, "PPh21": 1750,
            }

            def word(text, left, top):
                return {"text": text, "left": left, "top": top, "width": max(18, len(text) * 7), "height": 14, "confidence": 88}

            words = [word(label, left, 40) for label, left in positions.items()]
            words += [
                word("Belanja", 450, 75), word("operasional", 540, 75),
                word(receipt, 100, 105), word(account, 300, 105),
                word(gross, 1000, 105), word(net, 1200, 105),
                word(charge, 1400, 105), word(tax, 1750, 105),
            ]
            return parse_drpp_financial_table_rows(words)[0]

        first = parse_case(
            "00456/KW/012345/2028", "521219", "9.750.000", "9.700.000",
            "50.000", "4001.ABC.010.020.521219",
        )
        second = parse_case(
            "00888/KW/098765/2029", "524111", "12.345.000", "12.345.000",
            "0", "5002.XYZ.333.444.524111",
        )

        self.assertEqual(first["no_bukti"], "00456/KW/012345/2028")
        self.assertEqual(first["jumlah"], 9750000)
        self.assertEqual(first["pph21"], 50000)
        self.assertEqual(second["no_bukti"], "00888/KW/098765/2029")
        self.assertEqual(second["akun"], "524111")
        self.assertEqual(second["jumlah"], 12345000)

    def test_printed_total_is_read_from_noisy_summary_or_coa_labels(self):
        cases = [
            "Jumlah SPP ini : Rp9.720.000 Lembar",
            "Jumiah DRPP Rp 12,345,000",
            "Total DRPP - 7.654.321",
            "Jumlah : 1.234.567",
        ]

        self.assertEqual(extract_drpp_printed_total(cases[0]), Decimal("9720000"))
        self.assertEqual(extract_drpp_printed_total(cases[1]), Decimal("12345000"))
        self.assertEqual(extract_drpp_printed_total(cases[2]), Decimal("7654321"))
        self.assertEqual(extract_drpp_printed_total(cases[3]), Decimal("0"))
        self.assertEqual(extract_drpp_printed_total("Jumlah s.d. lalu 99.999.999"), Decimal("0"))

    def test_printed_total_candidates_keep_provenance_and_reject_wrong_sources(self):
        summary = (
            "Nomor : 00421/DRPP/123456/2028 "
            "Jumlah SPP ini : Rp1.200.000 "
            "Jumlah s.d. lalu atas beban output ini : Rp1.970.000 "
            "Jumlah s.d.SPP ini atas beban output ini : Rp3.170.000"
        )
        support = "MEMO PERINTAH BAYAR Jumlah : Rp4.200.000"
        candidates = [
            *extract_drpp_total_candidates(
                summary,
                file_name="holdout-a.pdf",
                page_number=5,
                document_type="DRPP_SUMMARY",
                nomor_drpp="00421",
            ),
            *extract_drpp_total_candidates(
                support,
                file_name="holdout-a.pdf",
                page_number=9,
                document_type="SUPPORT_DOCUMENT",
                nomor_drpp="00421",
            ),
        ]
        selected = select_drpp_printed_total_candidate(candidates)
        rejected_reasons = {item["reason"] for item in candidates if not item["accepted"]}

        self.assertEqual(selected["value"], Decimal("1200000"))
        self.assertEqual(selected["file"], "holdout-a.pdf")
        self.assertEqual(selected["page"], 5)
        self.assertEqual(selected["document_type"], "DRPP_SUMMARY")
        self.assertEqual(selected["raw_label"], "JUMLAH SPP INI")
        self.assertEqual(selected["raw_money_token"], "1.200.000")
        self.assertIn("cumulative_previous", rejected_reasons)
        self.assertIn("cumulative_through_current", rejected_reasons)
        self.assertIn("wrong_document_type", rejected_reasons)

    def test_clean_description_stops_before_drpp_footer_variants(self):
        raw = (
            "Honor kegiatan keluarga statistik bulan Juli 2028 "
            "Jumlah Lampiran 2 Jumlah SPP ini : 9.720.000 "
            "SUMATERA BARAT, 17-07-2028 Pejabat Pembuat Komitmen"
        )

        self.assertEqual(
            clean_description(raw),
            "Honor kegiatan keluarga statistik bulan Juli 2028",
        )

    def test_parse_drpp_pdf_uses_summary_total_evidence_not_only_row_sum(self):
        def word(text, left, top):
            return {
                "text": text,
                "left": left,
                "top": top,
                "width": max(18, len(text) * 7),
                "height": 14,
                "confidence": 90,
            }

        text = (
            "DAFTAR RINCIAN PERMINTAAN PEMBAYARAN "
            "Nomor DRPP 00444/DRPP/123456/2028 "
            "Jumlah SPP ini : Rp12.300.000"
        )
        words = [
            word("Kuitansi", 100, 40), word("Akun", 300, 40),
            word("Deskripsi", 500, 40), word("Bruto", 1000, 40),
            word("Netto", 1200, 40), word("Pembebanan", 1400, 40),
            word("PPh21", 1750, 40),
            word("Pengadaan", 450, 75), word("peralatan", 540, 75),
            word("00991/KW/123456/2028", 100, 105), word("523121", 300, 105),
            word("12.300.000", 1000, 105), word("12.177.000", 1200, 105),
            word("4001.ABC.111.222.523121", 1400, 105), word("123.000", 1750, 105),
        ]

        parsed = parse_drpp_pdf(
            "synthetic-holdout.pdf",
            ocr=False,
            extracted={
                "status": "parsed_ocr",
                "page_count": 1,
                "method": "unit",
                "warnings": [],
                "pages": [text],
                "page_details": [{
                    "page_number": 1,
                    "text": text,
                    "extracted_text": text,
                    "tsv_words": words,
                }],
            },
        )

        self.assertEqual(parsed["metadata"]["printed_total"], Decimal("12300000"))
        self.assertTrue(parsed["metadata"]["total_valid"])
        self.assertEqual(len(parsed["items"]), 1)
        self.assertEqual(parsed["items"][0]["no_bukti"], "00991/KW/123456/2028")
