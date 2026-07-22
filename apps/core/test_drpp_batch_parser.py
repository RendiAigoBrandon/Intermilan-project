import os
import tempfile
import zipfile
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.core.drpp_batch_parser import (
    TOO_MANY_DRPP_MESSAGE,
    _classification,
    _extracted_from_pages,
    _match_coa,
    _type_hint,
    build_transaction_items,
    classify_candidate_pages,
    discover_embedded_drpp_pages,
    parse_drpp_coa,
    parse_drpp_summary,
    parse_drpp_upload_batch,
)
from apps.core.parsers import clean_description


class DRPPBatchParserUnitTests(SimpleTestCase):
    def test_spm_kw_bundle_probes_embedded_drpp_prefix(self):
        file_name = "SPM NOMOR 00186A KW 00289.pdf"
        pages = [
            {
                "file_name": file_name,
                "page_number": number,
                "type_hint": "SPM",
                "drpp_hint": "",
            }
            for number in range(1, 14)
        ]

        with patch(
            "apps.core.drpp_batch_parser._probe_page_text",
            side_effect=lambda page: {
                "text": (
                    "DAFTAR RINCIAN PERINTAAN PEMBAYARAN"
                    if page["page_number"] == 8
                    else "dokumen pendukung"
                ),
                "cache_hit": False,
            },
        ) as probe:
            discover_embedded_drpp_pages(pages)

        self.assertEqual(_type_hint(file_name), "SPM")
        self.assertEqual(probe.call_count, 8)
        self.assertTrue(all(pages[index].get("force_probe") for index in (7, 8, 9)))
        self.assertFalse(pages[10].get("force_probe", False))

    def test_clean_description_removes_drpp_footer_and_trailing_ocr_noise(self):
        value = (
            "Honor Pengelola Sistem Akuntansi Instansi (SAI) di _/ BPS Provinsi "
            "Sumatera Barat bulan Mei 2026 n ЧЧЧ III PO a a n Jumlah Lampiran 2 "
            "Jumlah SPP ini : 2,800,000 Lembar"
        )

        self.assertEqual(
            clean_description(value),
            "Honor Pengelola Sistem Akuntansi Instansi (SAI) di BPS Provinsi "
            "Sumatera Barat bulan Mei 2026",
        )
        self.assertEqual(
            clean_description("Honor Narasumber Rapat Pembinaan PPID 7 Mei 2026 NC"),
            "Honor Narasumber Rapat Pembinaan PPID 7 Mei 2026",
        )

    def test_flattened_coa_header_fills_missing_account_and_pembebanan(self):
        rows = parse_drpp_coa(
            [{
                "document_type": "DRPP_COA",
                "page_number": 14,
                "text": (
                    "019937.010.521211.05401GG.2910BMA.A000000001 "
                    "007.051.08.000483-Perlengkapan Peserta Pelatihan 6.500.000,00"
                ),
            }],
            activity="2910",
        )
        items = [{"akun": "", "jumlah": Decimal("6500000"), "keperluan": "Pelatihan"}]

        _match_coa(items, rows, activity="2910")

        self.assertEqual(items[0]["akun"], "521211")
        self.assertEqual(items[0]["pembebanan"], "2910.BMA.007.051.521211")

    def test_coa_classification_wins_over_generic_drpp_heading(self):
        document_type, _, _ = _classification(
            "LAMPIRAN DAFTAR RINCIAN PERMINTAAN PEMBAYARAN Detail COA"
        )
        self.assertEqual(document_type, "DRPP_COA")

    def test_selected_page_payload_has_legacy_parser_status(self):
        extracted = _extracted_from_pages([{"page_number": 1, "text": "DRPP", "engine": "tesseract"}])
        self.assertEqual(extracted["status"], "parsed_ocr")
        self.assertEqual(extracted["combined_text"], "DRPP")

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
