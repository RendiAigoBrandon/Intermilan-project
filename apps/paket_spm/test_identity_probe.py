import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from apps.core.package_graph import parse_uploaded_package
from apps.dk.models import TransactionDetail
from apps.paket_spm.services import parsed_from_identity_probe, probe_package_identity


class PackageIdentityProbeTests(TestCase):
    def make_row(self, *, satker, number, year=2026, amount="100"):
        return TransactionDetail.objects.create(
            satker_code=satker,
            nomor_spm=number,
            tanggal_spm=datetime.date(year, 6, 30),
            bulan_sp2d=7,
            jenis_spm="GUP",
            akun="521111",
            nilai_bruto=Decimal(amount),
            nilai_netto=Decimal(amount),
        )

    @patch("apps.paket_spm.services.extract_pdf_text", return_value={"pages": [], "page_details": []})
    def test_filename_never_links_existing_dk_without_document_evidence(self, _extract):
        self.make_row(satker="1300", number="00777T", amount="100")

        probe = probe_package_identity("scan.pdf", "SPM NOMOR 00777T.pdf", kind="pdf")

        self.assertFalse(probe["needs_review"])
        self.assertEqual(probe["exact_transaction_ids"], [])
        self.assertEqual(probe["matched_number"], "")
        self.assertEqual(probe["candidates"][0]["source"], "filename_hint")

    def test_document_identity_never_auto_links_ambiguous_existing_dk_groups(self):
        self.make_row(satker="1300", number="00777T")
        self.make_row(satker="1400", number="00777T")
        native = {
            "pages": ["SURAT PERMINTAAN PEMBAYARAN Nomor SPP 00777T"],
            "page_details": [{
                "page_number": 1,
                "text": "SURAT PERMINTAAN PEMBAYARAN Nomor SPP 00777T",
                "page_types": ["SPP"],
            }],
        }

        with patch("apps.paket_spm.services.extract_pdf_text", return_value=native):
            probe = probe_package_identity("scan.pdf", "SPM NOMOR 00777T.pdf", kind="pdf")

        self.assertTrue(probe["needs_review"])
        self.assertEqual(probe["exact_transaction_ids"], [])

    def test_operator_satker_scope_resolves_document_number(self):
        expected = self.make_row(satker="1300", number="00777T")
        self.make_row(satker="1400", number="00777T")
        native = {
            "pages": ["SURAT PERMINTAAN PEMBAYARAN Nomor SPP 00777T"],
            "page_details": [{
                "page_number": 1,
                "text": "SURAT PERMINTAAN PEMBAYARAN Nomor SPP 00777T",
                "page_types": ["SPP"],
            }],
        }

        with patch("apps.paket_spm.services.extract_pdf_text", return_value=native):
            probe = probe_package_identity(
                "scan.pdf",
                "SPM NOMOR 00777T.pdf",
                input_satker="1300",
                kind="pdf",
            )

        self.assertFalse(probe["needs_review"])
        self.assertEqual(probe["exact_transaction_ids"], [expected.id])

    def test_suffix_alias_requires_ocr_document_evidence_before_linking(self):
        expected = self.make_row(satker="1300", number="00777T")
        native = {"pages": [], "page_details": []}
        ocr = {
            "pages": [],
            "page_details": [
                {
                    "page_number": 1,
                    "text": "SURAT PERINTAH MEMBAYAR Nomor SPM 00777A Tanggal 30 Juni 2026",
                    "page_types": ["SPM"],
                },
                {
                    "page_number": 2,
                    "text": "SURAT PERMINTAAN PEMBAYARAN Nomor SPP 00777T",
                    "page_types": ["SPP"],
                },
            ],
        }
        with patch("apps.paket_spm.services.extract_pdf_text", side_effect=[native, ocr]) as extract:
            probe = probe_package_identity("scan.pdf", "SPM NOMOR 00777A.pdf", kind="pdf")

        self.assertEqual(probe["exact_transaction_ids"], [expected.id])
        self.assertEqual(probe["matched_number"], "00777T")
        self.assertEqual(probe["method"], "identity_probe_ocr")
        self.assertEqual(extract.call_args_list[-1].kwargs["ocr"], True)

    def test_document_total_selects_spp_group_when_spm_suffix_differs(self):
        wrong = self.make_row(satker="1300", number="00777A", amount="170000")
        first = self.make_row(satker="1300", number="00777T", amount="200000")
        second = self.make_row(satker="1300", number="00777T", amount="300000")
        native = {"pages": [], "page_details": []}
        ocr = {
            "pages": [],
            "page_details": [
                {
                    "page_number": 1,
                    "text": (
                        "SURAT PERINTAH MEMBAYAR Nomor SPM 00777A "
                        "JUMLAH PENGELUARAN 500.000 TOTAL PEMBAYARAN 500.000"
                    ),
                    "page_types": ["SPM"],
                },
                {
                    "page_number": 2,
                    "text": "SURAT PERMINTAAN PEMBAYARAN Nomor SPP 00777T",
                    "page_types": ["SPP"],
                },
            ],
        }
        with patch("apps.paket_spm.services.extract_pdf_text", side_effect=[native, ocr]):
            probe = probe_package_identity("scan.pdf", "SPM NOMOR 00777T.pdf", kind="pdf")

        self.assertNotIn(wrong.id, probe["exact_transaction_ids"])
        self.assertEqual(probe["exact_transaction_ids"], [first.id, second.id])
        self.assertEqual(probe["matched_number"], "00777T")

    def test_document_total_mismatch_blocks_existing_dk_link(self):
        self.make_row(satker="1300", number="00777T", amount="170000")
        ocr = {
            "pages": [],
            "page_details": [{
                "page_number": 1,
                "text": (
                    "SURAT PERINTAH MEMBAYAR Nomor SPM 00777T "
                    "JUMLAH PENGELUARAN 500.000 TOTAL PEMBAYARAN 500.000"
                ),
                "page_types": ["SPM"],
            }],
        }
        with patch("apps.paket_spm.services.extract_pdf_text", side_effect=[{"pages": [], "page_details": []}, ocr]):
            probe = probe_package_identity("scan.pdf", "SPM NOMOR 00777T.pdf", kind="pdf")

        self.assertEqual(probe["exact_transaction_ids"], [])
        self.assertTrue(probe["needs_review"])
        self.assertTrue(probe["total_mismatch"])

    def test_summary_row_does_not_double_existing_group_total(self):
        summary = self.make_row(satker="1300", number="00777T", amount="500000")
        first = self.make_row(satker="1300", number="00777T", amount="200000")
        second = self.make_row(satker="1300", number="00777T", amount="300000")
        first.no_kuitansi = "00001/KW/1300/2026"
        first.save(update_fields=["no_kuitansi"])
        second.no_kuitansi = "00002/KW/1300/2026"
        second.save(update_fields=["no_kuitansi"])
        ocr = {
            "pages": [],
            "page_details": [{
                "page_number": 1,
                "text": (
                    "SURAT PERINTAH MEMBAYAR Nomor SPM 00777T "
                    "JUMLAH PENGELUARAN 500.000 TOTAL PEMBAYARAN 500.000"
                ),
                "page_types": ["SPM"],
            }],
        }
        with patch("apps.paket_spm.services.extract_pdf_text", side_effect=[{"pages": [], "page_details": []}, ocr]):
            probe = probe_package_identity("scan.pdf", "SPM NOMOR 00777T.pdf", kind="pdf")

        self.assertEqual(probe["exact_transaction_ids"], [summary.id, first.id, second.id])
        self.assertEqual(probe["matched_total_bruto"], "500000.00")
        parsed = parsed_from_identity_probe(probe, "SPM NOMOR 00777T.pdf")
        self.assertEqual(parsed["spm"]["metadata"]["total_pembayaran"], Decimal("500000.00"))

    def test_full_parser_reuses_identity_ocr_result(self):
        extracted = {
            "method": "tesseract",
            "page_count": 1,
            "page_details": [{
                "page_number": 1,
                "text": "SURAT PERINTAH MEMBAYAR Nomor SPM 00777T",
                "confidence": 90,
            }],
        }
        spm = {
            "status": "needs_manual_review",
            "method": "tesseract",
            "warnings": [],
            "metadata": {"nomor_spm": "00777T", "total_pembayaran": Decimal("500000")},
            "detail_items": [],
        }
        with patch("apps.core.package_graph.extract_pdf_text") as full_ocr, \
             patch("apps.core.package_graph.parse_spm_pdf", return_value=spm) as parser:
            parsed = parse_uploaded_package(
                "scan.pdf",
                "SPM NOMOR 00777T.pdf",
                kind="pdf",
                extracted=extracted,
            )

        full_ocr.assert_not_called()
        self.assertTrue(parser.called)
        self.assertEqual(parsed["ocr_summary"]["method"], "tesseract")
