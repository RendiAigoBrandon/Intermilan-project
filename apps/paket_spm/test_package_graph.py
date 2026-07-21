from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.core.package_graph import build_document_graph, parse_uploaded_package


class PackageGraphParserTests(SimpleTestCase):
    def extracted(self, pages):
        return {
            "method": "tesseract",
            "best_engine": "tesseract",
            "status": "parsed_ocr",
            "warnings": [],
            "confidence": 88.0,
            "page_count": len(pages),
            "engines_tried": ["text", "tesseract"],
            "page_details": [
                {
                    "page_number": index,
                    "text": text,
                    "extracted_text": text,
                    "confidence": 88.0,
                    "method": "tesseract",
                }
                for index, text in enumerate(pages, start=1)
            ],
        }

    def spm(self, rows, total="300"):
        return {
            "status": "parsed_ocr",
            "method": "tesseract",
            "warnings": [],
            "metadata": {
                "nomor_spm": "00140A",
                "jumlah_pengeluaran": Decimal(total),
                "total_pembayaran": Decimal(total),
                "jumlah_potongan": Decimal("0"),
            },
            "detail_items": rows,
            "akun_rows": [],
        }

    def drpp(self, rows):
        return {
            "status": "parsed_ocr",
            "method": "tesseract",
            "warnings": [],
            "metadata": {"nomor_drpp": "00001/DRPP/019937/2026", "total": Decimal("300")},
            "items": rows,
        }

    def test_graph_classifies_pages_and_keeps_ssp_as_support(self):
        graph = build_document_graph(
            self.extracted(
                [
                    "SURAT PERINTAH MEMBAYAR NOMOR SPM 00140A TOTAL PEMBAYARAN 300",
                    "SURAT PERMINTAAN PEMBAYARAN NOMOR SPP 00140T",
                    "SURAT SETORAN PAJAK KODE AKUN PAJAK MASA PAJAK",
                ]
            )["page_details"]
        )
        self.assertIn(3, graph["support_pages"])
        self.assertNotIn(3, graph["transaction_pages"])
        self.assertTrue(any(edge["relation"] == "supports" for edge in graph["edges"]))

    def test_drpp_items_have_priority_over_spm_detail_items(self):
        spm_rows = [{"akun": "521111", "jumlah": Decimal("300"), "pembebanan": "2886.EBA.994.001.521111", "keperluan": "Belanja operasional"}]
        drpp_rows = [
            {"akun": "521111", "jumlah": Decimal("100"), "no_bukti": "001/KW/019937/2026", "pembebanan": "2886.EBA.994.001.521111", "keperluan": "Belanja bagian satu"},
            {"akun": "521111", "jumlah": Decimal("200"), "no_bukti": "002/KW/019937/2026", "pembebanan": "2886.EBA.994.001.521111", "keperluan": "Belanja bagian dua"},
        ]
        pages = [
            "SURAT PERINTAH MEMBAYAR NOMOR SPM 00140A TOTAL PEMBAYARAN 300",
            "DAFTAR RINCIAN PERMINTAAN PEMBAYARAN BUKTI PENGELUARAN",
        ]
        with patch("apps.core.package_graph.extract_pdf_text", return_value=self.extracted(pages)), patch(
            "apps.core.package_graph.parse_spm_pdf", return_value=self.spm(spm_rows)
        ) as parse_spm, patch("apps.core.package_graph.parse_drpp_pdf", return_value=self.drpp(drpp_rows)):
            parsed = parse_uploaded_package("dummy.pdf", "scan-baru.pdf", kind="pdf")

        self.assertEqual(parsed["transaction_source"], "DRPP")
        self.assertEqual(len(parsed["kw_items"]), 2)
        self.assertEqual(parsed["validation"]["status"], "VALID")
        self.assertIs(parse_spm.call_args.kwargs.get("parse_details"), False)

    def test_total_mismatch_preserves_rows_and_marks_review(self):
        rows = [{"akun": "521111", "jumlah": Decimal("200"), "pembebanan": "2886.EBA.994.001.521111", "keperluan": "Belanja operasional"}]
        pages = ["SURAT PERINTAH MEMBAYAR NOMOR SPM 00140A TOTAL PEMBAYARAN 300"]
        with patch("apps.core.package_graph.extract_pdf_text", return_value=self.extracted(pages)), patch(
            "apps.core.package_graph.parse_spm_pdf", return_value=self.spm(rows, total="300")
        ):
            parsed = parse_uploaded_package("dummy.pdf", "SPM baru.pdf", kind="pdf")

        self.assertEqual(len(parsed["kw_items"]), 1)
        self.assertEqual(parsed["kw_items"][0]["jumlah"], Decimal("200"))
        self.assertEqual(parsed["validation"]["status"], "PERLU_REVIEW")
        self.assertEqual(parsed["validation"]["difference"], Decimal("-100"))

    def test_support_only_pdf_does_not_create_transaction(self):
        pages = ["SURAT SETORAN PAJAK KODE AKUN PAJAK MASA PAJAK JUMLAH PEMBAYARAN 100"]
        with patch("apps.core.package_graph.extract_pdf_text", return_value=self.extracted(pages)), patch(
            "apps.core.package_graph.parse_spm_pdf"
        ) as parse_spm:
            parsed = parse_uploaded_package("ssp.pdf", "ssp.pdf", kind="pdf")

        parse_spm.assert_not_called()
        self.assertEqual(parsed["kw_items"], [])
        self.assertEqual(parsed["validation"]["status"], "GAGAL")
