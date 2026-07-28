from django.test import SimpleTestCase

from apps.core.parsers import (
    parse_drpp_financial_table_rows,
    parse_drpp_items_from_tsv,
    parse_drpp_items_from_tsv_rows,
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
