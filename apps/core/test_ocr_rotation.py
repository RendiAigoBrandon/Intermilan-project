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

        def fake_page_text(_pytesseract, image):
            return texts[image.rotation], 80.0, [], []

        with patch("apps.core.ocr.tesseract_page_text", side_effect=fake_page_text):
            result = tesseract_page_text_best_rotation(object(), _Image())

        self.assertEqual(result[4], 270)
        self.assertEqual(result[5], [0, 90, 270])
        self.assertIn("DETAIL PENGELUARAN", result[0])
