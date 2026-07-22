import os
import tempfile
import zipfile
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.core.drpp_batch_parser import (
    TOO_MANY_DRPP_MESSAGE,
    build_transaction_items,
    classify_candidate_pages,
    parse_drpp_summary,
    parse_drpp_upload_batch,
)


class DRPPBatchParserUnitTests(SimpleTestCase):
    def test_three_drpp_is_rejected_before_page_index_or_ocr(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            path = os.path.join(media_root, "three.zip")
            with zipfile.ZipFile(path, "w") as archive:
                for number in ("00042", "00043", "00044"):
                    archive.writestr(f"DRPP {number}.pdf", b"not-a-real-pdf")
            with patch("apps.core.drpp_batch_parser.build_page_index", side_effect=AssertionError("heavy page index must not run")):
                with self.assertRaisesMessage(ValueError, TOO_MANY_DRPP_MESSAGE):
                    parse_drpp_upload_batch(path)

    def test_page_ocr_is_only_called_for_selected_representatives(self):
        pages = [
            {
                "file_name": "DRPP 00042 KW 00243.pdf",
                "page_number": number,
                "native_text": "",
                "is_representative": True,
                "type_hint": "KUITANSI",
                "page_hash": str(number),
                "_image": None,
                "_path": "unused.pdf",
            }
            for number in range(1, 11)
        ]
        with patch("apps.core.drpp_batch_parser._candidate_for_probe", side_effect=lambda page: page["page_number"] in {1, 7}), patch(
            "apps.core.drpp_batch_parser._ocr_page",
            return_value={"text": "KUITANSI", "confidence": 90, "words": [], "engine": "tesseract", "cache_hit": False},
        ) as ocr_page:
            classify_candidate_pages(pages, ocr=True)
        self.assertEqual(ocr_page.call_count, 2)
        self.assertLess(ocr_page.call_count, len(pages))

    def test_embedded_drpp_page_is_accepted_as_summary(self):
        page = {
            "file_name": "DRPP 00044 KW 00257.pdf",
            "_path": "embedded.pdf",
            "page_number": 3,
            "page_hash": "abc",
            "document_type": "DRPP_SUMMARY",
            "text": "DAFTAR RINCIAN PERMINTAAN PEMBAYARAN NOMOR DRPP 00044",
            "native_text": "",
        }
        parsed = {
            "metadata": {"nomor_drpp": "", "total": Decimal("6500000")},
            "items": [{"no_bukti": "00257/KW/019937/2026", "akun": "521211", "jumlah": Decimal("6500000")}],
        }
        with patch("apps.core.drpp_batch_parser.parse_drpp_pdf", return_value=parsed):
            result = parse_drpp_summary("00044", [page])
        self.assertEqual(result["metadata"]["nomor_drpp"], "00044")
        self.assertEqual(result["items"][0]["no_drpp"], "00044")
        self.assertEqual(result["file_name"], "DRPP 00044 KW 00257.pdf")

    def test_target_row_keeps_full_spm_suffix_and_fifteen_columns(self):
        drpp = {
            "metadata": {"nomor_drpp": "00042", "tahun": 2026},
            "items": [
                {
                    "akun": "522151",
                    "no_bukti": "00243/KW/019937/2026",
                    "jumlah": Decimal("1800000"),
                    "keperluan": "Honor Narasumber Rapat Pertemuan Pembinaan PPID 7 Mei 2026",
                    "pembebanan": "2886.EBD.961.051.522151",
                }
            ],
        }
        spm = {
            "metadata": {
                "nomor_spm": "00166T",
                "tanggal_spm": date(2026, 6, 15),
                "jenis_spm": "GUP",
                "bulan_sp2d": 6,
            }
        }
        row = build_transaction_items(drpp, spm)[0]
        expected = {
            "helper", "akun", "bulan_sp2d", "cara_pembayaran", "nomor_spm",
            "tanggal_spm", "jenis_spm", "no_kuitansi", "no_drpp", "deskripsi",
            "nilai_bruto", "nilai_netto", "pembebanan", "fp", "pph21",
        }
        self.assertTrue(expected.issubset(row))
        self.assertEqual(row["helper"], "52215100243/KW/019937/2026")
        self.assertEqual(row["nomor_spm"], "00166T")
        self.assertEqual(row["no_kuitansi"], "00243/KW/019937/2026")
        self.assertEqual(row["nilai_bruto"], Decimal("1800000"))
        self.assertEqual(row["pembebanan"], "2886.EBD.961.051.522151")
