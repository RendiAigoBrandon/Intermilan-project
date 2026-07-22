from django.test import SimpleTestCase

from apps.core.parsers import parse_drpp_items_from_tsv, parse_drpp_items_from_tsv_rows


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
