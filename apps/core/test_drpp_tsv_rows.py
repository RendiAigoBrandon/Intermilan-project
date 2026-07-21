from django.test import SimpleTestCase

from apps.core.parsers import parse_drpp_items_from_tsv_rows


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
