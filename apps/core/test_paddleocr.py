from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from PIL import Image

from apps.core.ocr import (
    extract_paddleocr,
    load_ocr_cache,
    ocr_cache_key,
    ocr_cache_path,
    save_ocr_cache,
)


class PaddleOCRAdapterTests(SimpleTestCase):
    def test_empty_or_failed_ocr_result_is_never_reused_from_cache(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scan.pdf"
            path.write_bytes(b"scan")
            cache_path = Path(ocr_cache_path(path))
            cache_path.parent.mkdir(parents=True)
            cache_path.write_text(json.dumps({"combined_text": "", "page_details": []}), encoding="utf-8")

            self.assertIsNone(load_ocr_cache(path))
            cache_path.unlink()
            save_ocr_cache(path, {"combined_text": "", "page_details": []})
            self.assertFalse(cache_path.exists())

    def test_enabling_paddle_changes_cache_identity(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scan.pdf"
            path.write_bytes(b"same pdf bytes")
            with patch.dict("os.environ", {"OCR_ENABLE_PADDLEOCR": "false"}, clear=False):
                without_paddle = ocr_cache_key(path)
            with patch.dict("os.environ", {"OCR_ENABLE_PADDLEOCR": "true"}, clear=False):
                with_paddle = ocr_cache_key(path)
        self.assertNotEqual(without_paddle, with_paddle)

    def test_paddle_v3_predict_result_keeps_text_scores_and_boxes(self):
        class FakeOCR:
            def __init__(self, **options):
                self.options = options

            def predict(self, _image):
                return [
                    SimpleNamespace(
                        json={
                            "res": {
                                "rec_texts": ["SURAT PERINTAH MEMBAYAR", "NOMOR SPM"],
                                "rec_scores": [0.91, 0.81],
                                "rec_polys": [
                                    [[1, 2], [101, 2], [101, 22], [1, 22]],
                                    [[1, 30], [81, 30], [81, 50], [1, 50]],
                                ],
                            }
                        }
                    )
                ]

        module = SimpleNamespace(__version__="3.3.0", PaddleOCR=FakeOCR)
        with patch("apps.core.ocr.optional_import", side_effect=lambda name: module if name == "paddleocr" else __import__(name)), patch.dict(
            "os.environ", {"OCR_ENABLE_PADDLEOCR": "true"}, clear=False
        ):
            result = extract_paddleocr("dummy.pdf", images=[Image.new("RGB", (120, 80), "white")])

        self.assertEqual(result.pages[0].confidence, 86.0)
        self.assertIn("SURAT PERINTAH MEMBAYAR", result.pages[0].extracted_text)
        self.assertEqual(result.pages[0].tsv_words[0]["width"], 100)

    def test_paddle_v2_result_remains_supported(self):
        class FakeOCR:
            def __init__(self, **_options):
                pass

            def ocr(self, _image, cls=True):
                return [[[[[2, 3], [52, 3], [52, 13], [2, 13]], ["NOMOR SPP", 0.75]]]]

        module = SimpleNamespace(__version__="2.7.0", PaddleOCR=FakeOCR)
        with patch("apps.core.ocr.optional_import", side_effect=lambda name: module if name == "paddleocr" else __import__(name)), patch.dict(
            "os.environ", {"OCR_ENABLE_PADDLEOCR": "true"}, clear=False
        ):
            result = extract_paddleocr("dummy.pdf", images=[Image.new("RGB", (80, 40), "white")])

        self.assertEqual(result.pages[0].confidence, 75.0)
        self.assertEqual(result.pages[0].tsv_words[0]["height"], 10)
