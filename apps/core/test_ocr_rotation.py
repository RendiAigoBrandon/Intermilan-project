from unittest.mock import patch

from django.test import SimpleTestCase

from apps.core.ocr import tesseract_page_text_best_rotation


class _Image:
    def __init__(self, rotation=0):
        self.rotation = rotation

    def rotate(self, rotation, expand=True):
        return _Image(rotation)


class OCRRotationSelectionTests(SimpleTestCase):
    def test_landscape_orientation_compares_90_and_270(self):
        texts = {
            0: "",
            90: "000000 111111 222222 3.799.400 1.100.000 " * 20,
            270: (
                "KEMENTERIAN KEUANGAN REPUBLIK INDONESIA "
                "DETAIL PENGELUARAN DAN POTONGAN PADA SPP/SPM/SP2D "
                "KODE COA NILAI PENGELUARAN POTONGAN 3.799.400"
            ),
            180: "",
        }

        def fake_page_text(_pytesseract, image, **_kwargs):
            return texts[image.rotation], 80.0, [], []

        with patch("apps.core.ocr.tesseract_page_text", side_effect=fake_page_text):
            result = tesseract_page_text_best_rotation(object(), _Image())

        self.assertEqual(result[4], 270)
        self.assertEqual(result[5], [0, 90, 270])
        self.assertIn("DETAIL PENGELUARAN", result[0])

    def test_long_numeric_gibberish_at_zero_does_not_stop_landscape_comparison(self):
        texts = {
            0: "000000 111111 222222 3.799.400 1.100.000 " * 30,
            90: "teks terbalik " * 20,
            270: (
                "KEMENTERIAN KEUANGAN REPUBLIK INDONESIA "
                "DETAIL PENGELUARAN DAN POTONGAN PADA SPP/SPM/SP2D "
                "NO SP2D 260100000036855"
            ),
            180: "",
        }

        def fake_page_text(_pytesseract, image, **_kwargs):
            return texts[image.rotation], 80.0, [], []

        with patch("apps.core.ocr.tesseract_page_text", side_effect=fake_page_text):
            result = tesseract_page_text_best_rotation(object(), _Image())

        self.assertEqual(result[4], 270)
        self.assertEqual(result[5], [0, 90, 270])

    def test_upright_page_with_document_anchor_stops_without_extra_ocr(self):
        text = "SURAT PERINTAH MEMBAYAR NOMOR SPM 00203A"

        with patch(
            "apps.core.ocr.tesseract_page_text",
            return_value=(text, 80.0, [], []),
        ) as page_text:
            result = tesseract_page_text_best_rotation(object(), _Image())

        self.assertEqual(result[4], 0)
        self.assertEqual(result[5], [0])
        page_text.assert_called_once()

    def test_non_anchor_support_text_is_not_discarded_when_scores_are_zero(self):
        texts = {
            0: "alpha beta gamma tanpa judul baku " * 5,
            90: "delta epsilon zeta halaman pendukung umum " * 10,
            270: "",
            180: "",
        }

        def fake_page_text(_pytesseract, image, **_kwargs):
            return texts[image.rotation], 70.0, [], []

        with patch("apps.core.ocr.tesseract_page_text", side_effect=fake_page_text):
            result = tesseract_page_text_best_rotation(object(), _Image())

        self.assertEqual(result[4], 90)
        self.assertIn("halaman pendukung umum", result[0])
