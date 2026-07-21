import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

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
    def test_filename_can_link_one_unique_existing_dk_group_without_form_context(self, _extract):
        first = self.make_row(satker="1300", number="00777T", amount="100")
        second = self.make_row(satker="1300", number="00777T", amount="200")

        probe = probe_package_identity("scan.pdf", "SPM NOMOR 00777T.pdf", kind="pdf")

        self.assertFalse(probe["needs_review"])
        self.assertEqual(probe["exact_transaction_ids"], [first.id, second.id])
        self.assertEqual(probe["satker_code"], "1300")
        self.assertEqual(probe["tahun"], 2026)
        parsed = parsed_from_identity_probe(probe, "SPM NOMOR 00777T.pdf")
        self.assertEqual(parsed["spm"]["metadata"]["total_pembayaran"], Decimal("300"))

    @patch("apps.paket_spm.services.extract_pdf_text", return_value={"pages": [], "page_details": []})
    def test_filename_never_auto_links_ambiguous_existing_dk_groups(self, _extract):
        self.make_row(satker="1300", number="00777T")
        self.make_row(satker="1400", number="00777T")

        probe = probe_package_identity("scan.pdf", "SPM NOMOR 00777T.pdf", kind="pdf")

        self.assertTrue(probe["needs_review"])
        self.assertEqual(probe["exact_transaction_ids"], [])

    @patch("apps.paket_spm.services.extract_pdf_text", return_value={"pages": [], "page_details": []})
    def test_operator_satker_scope_resolves_otherwise_ambiguous_number(self, _extract):
        expected = self.make_row(satker="1300", number="00777T")
        self.make_row(satker="1400", number="00777T")

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
