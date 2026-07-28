import os
import tempfile
import zipfile
from datetime import date
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.core.exceptions import UploadTechnicalError
from apps.core.drpp_batch_parser import (
    _classification,
    _apply_group_date_consensus,
    _extracted_from_pages,
    _match_coa,
    _recover_missing_candidate_pages,
    _type_hint,
    build_transaction_items,
    classify_candidate_pages,
    discover_embedded_drpp_pages,
    parse_drpp_coa,
    parse_drpp_summary,
    parse_drpp_upload_batch,
    parse_kw_support,
    validate_drpp_group,
)
from apps.core.parsers import clean_description, consensus_sp2d_from_pages


class DRPPBatchParserUnitTests(SimpleTestCase):
    def test_spm_identity_probe_reaches_spp_pages_but_stays_bounded(self):
        from apps.core.drpp_batch_parser import _candidate_for_probe

        self.assertTrue(_candidate_for_probe({"type_hint": "SPM", "page_number": 8}))
        self.assertFalse(_candidate_for_probe({"type_hint": "SPM", "page_number": 13}))

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

    def test_mixed_pdf_selects_identity_prefix_and_drpp_continuation_pages(self):
        pages = [
            {
                "file_name": "mixed.pdf",
                "page_number": number,
                "type_hint": "KUITANSI",
                "drpp_hint": "00123",
            }
            for number in range(1, 6)
        ]
        texts = {
            1: "SURAT PERINTAH MEMBAYAR",
            2: "dokumen pendukung",
            3: "DAFTAR RINCIAN PERMINTAAN PEMBAYARAN",
            4: "BUKTI PENGELUARAN 00123/KW/000001/2026",
            5: "DETAIL COA",
        }
        with patch(
            "apps.core.drpp_batch_parser._probe_page_text",
            side_effect=lambda page: {"text": texts[page["page_number"]], "cache_hit": False},
        ):
            discover_embedded_drpp_pages(pages)

        self.assertTrue(all(page.get("force_probe") for page in pages))
        self.assertEqual(pages[3]["type_hint"], "DRPP_SUMMARY")

    def test_sp2d_page_has_distinct_document_type(self):
        document_type, _, _ = _classification("SURAT PERINTAH PENCAIRAN DANA")
        self.assertEqual(document_type, "SP2D")
        detail_type, _, _ = _classification(
            "DETAIL PENGELUARAN DAN POTONGAN PADA SPP/SPM/SP2D"
        )
        self.assertEqual(detail_type, "SP2D")

    def test_sp2d_number_uses_repeated_table_consensus(self):
        pages = [{
            "page_types": ["DETAIL_SPP_SPM_SP2D", "SP2D"],
            "text": " ".join([
                "260100000036855",
                "260100000036885",
                "260100000036855",
                "260100000036855",
            ]),
        }]

        self.assertEqual(consensus_sp2d_from_pages(pages), "260100000036855")

    def test_sp2d_number_does_not_use_unclassified_support_page(self):
        pages = [{
            "page_types": ["SUPPORT_DOCUMENT"],
            "text": "260100000036855 260100000036855",
        }]

        self.assertEqual(consensus_sp2d_from_pages(pages), "")

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

    def test_receipt_financial_fields_do_not_turn_receipt_into_drpp_table(self):
        text = (
            "KUITANSI / BUKTI PEMBAYARAN Nomor: 00456/KW/012345/2028 "
            "Akun 521219 Pembebanan 4001.ABC.010.020.521219 "
            "Nilai Bruto Rp9.750.000 Nilai Netto Rp9.700.000"
        )
        self.assertEqual(_classification(text)[0], "KUITANSI")

    def test_control_page_that_mentions_receipts_is_not_a_receipt(self):
        text = "Lampiran kontrol. Parser mengambil detail dari kuitansi dan tabel DRPP."
        self.assertEqual(_classification(text)[0], "SUPPORT_DOCUMENT")

    def test_missing_receipt_fallback_stops_after_matching_structural_page(self):
        pages = [
            {
                "file_name": "mixed.pdf", "page_number": 2, "is_representative": True,
                "text": "DAFTAR RINCIAN PERMINTAAN PEMBAYARAN", "document_type": "DRPP_SUMMARY",
            },
            {
                "file_name": "mixed.pdf", "page_number": 3, "is_representative": True,
                "text": "", "native_text": "", "document_type": "UNKNOWN", "_path": "unused.pdf",
            },
            {
                "file_name": "mixed.pdf", "page_number": 4, "is_representative": True,
                "text": "", "native_text": "", "document_type": "UNKNOWN", "_path": "unused.pdf",
            },
        ]
        drpp = {
            "metadata": {"nomor_drpp": "00456", "printed_total": Decimal("9750000"), "source_item_count": 1},
            "source_pages": [{"file_name": "mixed.pdf", "page_number": 2}],
            "items": [{"no_bukti": "00456/KW/012345/2028", "akun": "521219", "jumlah": Decimal("9750000")}],
        }
        receipt = {
            "text": (
                "KUITANSI / BUKTI PEMBAYARAN Nomor: 00456/KW/012345/2028 "
                "Akun 521219 Nilai Bruto Rp9.750.000"
            ),
            "words": [], "confidence": 90, "engine": "tesseract", "cache_hit": False,
        }
        with patch("apps.core.drpp_batch_parser._ocr_page", return_value=receipt) as ocr_page:
            _recover_missing_candidate_pages([drpp], pages)

        self.assertEqual(ocr_page.call_count, 1)
        self.assertEqual(pages[1]["document_type"], "KUITANSI")
        self.assertEqual(pages[2]["text"], "")

    def test_mismatched_total_never_reports_false_balance(self):
        drpp = {
            "metadata": {
                "nomor_drpp": "00456", "printed_total": Decimal("1000000"),
                "source_item_count": 1, "missing_receipt_count": 0,
            },
            "items": [{}],
        }
        items = [{
            "nomor_spm": "00789A", "no_kuitansi": "00456/KW/012345/2028",
            "akun": "521219", "nilai_bruto": Decimal("900000"), "status_detail": "LENGKAP",
        }]

        validation = validate_drpp_group(drpp, items)

        self.assertEqual(validation["status"], "PERLU_REVIEW")
        self.assertFalse(validation["can_commit"])
        self.assertIn("tidak sama", " ".join(validation["errors"]))

    def test_missing_or_zero_total_and_missing_source_item_never_balance(self):
        base_item = {
            "nomor_spm": "00789A", "no_kuitansi": "00456/KW/012345/2028",
            "akun": "521219", "nilai_bruto": Decimal("900000"), "status_detail": "LENGKAP",
        }
        cases = [
            {"printed_total": Decimal("0"), "source_item_count": 1},
            {"printed_total": None, "source_item_count": 1},
            {"printed_total": Decimal("900000"), "source_item_count": 2},
        ]
        for metadata in cases:
            with self.subTest(metadata=metadata):
                drpp = {"metadata": {"nomor_drpp": "00456", **metadata}, "items": [{}]}
                result = validate_drpp_group(drpp, [base_item])
                self.assertEqual(result["status"], "PERLU_REVIEW")
                self.assertFalse(result["can_commit"])

    def test_group_date_uses_consensus_only_when_spm_date_is_missing(self):
        drpps = [{
            "metadata": {"nomor_drpp": "00456"},
            "items": [{"tanggal_bukti": "28/08/2028"}, {"tanggal_bukti": "28/08/2028"}],
        }]
        spm = {"metadata": {"nomor_spm": "00789A", "tanggal_spm": None}}

        _apply_group_date_consensus(drpps, spm)

        self.assertEqual(spm["metadata"]["tanggal_spm"], date(2028, 8, 28))
        self.assertEqual(drpps[0]["metadata"]["tanggal_spm"], date(2028, 8, 28))

    def test_receipt_charge_must_end_with_transaction_account(self):
        items = [{
            "no_bukti": "00456/KW/012345/2028", "akun": "521219",
            "jumlah": Decimal("9750000"), "pembebanan": "",
        }]
        pages = [{
            "document_type": "KUITANSI", "file_name": "mixed.pdf", "page_number": 3,
            "text": (
                "KUITANSI / BUKTI PEMBAYARAN Nomor: 00456/KW/012345/2028 "
                "Akun 521219 Pembebanan 4001.ABC.010.020.523123 "
                "Nilai Bruto Rp9.750.000"
            ),
        }]

        parse_kw_support(items, pages)

        self.assertEqual(items[0]["pembebanan"], "")
        self.assertIn("konflik", " ".join(items[0]["warnings"]))

    def test_duplicate_candidate_receipt_pages_do_not_duplicate_transactions(self):
        items = [{
            "no_bukti": "00456/KW/012345/2028", "akun": "521219",
            "jumlah": Decimal("9750000"),
        }]
        page = {
            "document_type": "KUITANSI", "file_name": "mixed.pdf", "page_number": 3,
            "text": (
                "KUITANSI / BUKTI PEMBAYARAN Nomor: 00456/KW/012345/2028 "
                "Akun 521219 Pembebanan 4001.ABC.010.020.521219 "
                "Untuk pembayaran: Belanja operasional kantor. "
                "Nilai Bruto Rp9.750.000 Nilai Netto Rp9.700.000 PPh21 Rp50.000"
            ),
        }

        parse_kw_support(items, [page, {**page, "page_number": 4}])

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["no_bukti"], "00456/KW/012345/2028")
        self.assertEqual(items[0]["netto"], Decimal("9700000"))

    def test_selected_page_payload_has_legacy_parser_status(self):
        extracted = _extracted_from_pages([{"page_number": 1, "text": "DRPP", "engine": "tesseract"}])
        self.assertEqual(extracted["status"], "parsed_ocr")
        self.assertEqual(extracted["combined_text"], "DRPP")

    def test_three_drpp_are_processed_in_one_package(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            path = os.path.join(media_root, "three.zip")
            with zipfile.ZipFile(path, "w") as archive:
                for number in ("00042", "00043", "00044"):
                    archive.writestr(f"DRPP {number}.pdf", b"not-a-real-pdf")
            pages = [
                {
                    "file_name": f"DRPP {number}.pdf",
                    "page_number": 1,
                    "drpp_hint": number,
                    "drpp_detected": number,
                    "is_representative": True,
                }
                for number in ("00042", "00043", "00044")
            ]

            def parsed_drpp(number, _pages):
                return {
                    "metadata": {"nomor_drpp": number, "printed_total": Decimal("100")},
                    "items": [{
                        "akun": "521111",
                        "no_bukti": f"{number}/KW/019937/2026",
                        "jumlah": Decimal("100"),
                    }],
                }

            with patch("apps.core.drpp_batch_parser.build_page_index", return_value=pages), patch(
                "apps.core.drpp_batch_parser.discover_embedded_drpp_pages"
            ), patch("apps.core.drpp_batch_parser.deduplicate_pages", return_value=pages), patch(
                "apps.core.drpp_batch_parser.classify_candidate_pages"
            ), patch("apps.core.drpp_batch_parser.parse_drpp_summary", side_effect=parsed_drpp), patch(
                "apps.core.drpp_batch_parser.parse_drpp_coa", return_value=[]
            ), patch("apps.core.drpp_batch_parser.parse_kw_support"):
                parsed = parse_drpp_upload_batch(path, ocr=False)

            self.assertEqual([group["no_drpp"] for group in parsed["drpp_groups"]], ["00042", "00043", "00044"])
            self.assertEqual(len(parsed["kw_items"]), 3)

    def test_labeled_spm_number_is_not_overwritten_by_member_filename(self):
        from apps.core.drpp_batch_parser import resolve_spm_parent

        page = {
            "file_name": "batch/SPM NOMOR 00999T.pdf",
            "page_number": 1,
            "document_type": "SPM",
            "text": "SURAT PERINTAH MEMBAYAR Nomor SPM 00999A",
            "_path": "unused.pdf",
            "is_representative": True,
        }
        spp_page = {
            **page,
            "page_number": 2,
            "document_type": "SPP",
            "text": "SURAT PERMINTAAN PEMBAYARAN Nomor SPP 00999T",
        }
        parsed_spm = {
            "metadata": {
                "nomor_spm": "00999A",
                "tanggal_spm": date(2026, 6, 1),
                "jenis_spm": "LS",
            }
        }
        def parse_spm_with_valid_payload(*args, **kwargs):
            self.assertIn(kwargs["extracted"]["status"], {"parsed_text", "parsed_ocr"})
            self.assertEqual(len(kwargs["extracted"]["page_details"]), 2)
            return parsed_spm

        with patch("apps.core.drpp_batch_parser._load_page_cache", return_value=None), patch(
            "apps.core.drpp_batch_parser._save_page_cache"
        ), patch("apps.core.drpp_batch_parser.parse_spm_pdf", side_effect=parse_spm_with_valid_payload), patch(
            "apps.core.drpp_batch_parser._exact_sp2d", return_value=None
        ):
            spm, sp2d = resolve_spm_parent([], [page, spp_page])

        self.assertIsNone(sp2d)
        self.assertEqual(spm["metadata"]["nomor_spm"], "00999A")

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

    def test_printed_total_never_overwrites_unverified_row_amount(self):
        page = {
            "file_name": "mixed.pdf", "_path": "mixed.pdf", "page_number": 2,
            "page_hash": "abc", "document_type": "DRPP_SUMMARY",
            "text": "DAFTAR RINCIAN PERMINTAAN PEMBAYARAN Nomor DRPP 00456",
            "native_text": "",
        }
        parsed = {
            "metadata": {"nomor_drpp": "00456", "printed_total": Decimal("1000000")},
            "items": [{
                "no_bukti": "00456/KW/012345/2028", "akun": "521219",
                "jumlah": Decimal("900000"), "needs_review": False,
            }],
        }
        with patch("apps.core.drpp_batch_parser.parse_drpp_pdf", return_value=parsed), patch(
            "apps.core.drpp_batch_parser.verify_drpp_rows_high_res", return_value=[]
        ):
            result = parse_drpp_summary("00456", [page])

        self.assertEqual(result["items"][0]["jumlah"], Decimal("900000"))
        self.assertFalse(result["metadata"]["total_valid"])
        self.assertNotIn("amount_reconciled_from_total", result["items"][0])

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

from django.test import TestCase, RequestFactory
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.auth.models import User
from apps.sp2d.models import SP2DRaw, SP2DImportBatch
from apps.paket_spm.models import PaketSPMUpload
from apps.paket_spm.views import paket_spm_preview

class DRPPBatchIntegrationTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="test", password="123")
        self.user.profile.role = self.user.profile.Role.SATKER
        self.user.profile.satker_code = "411222"
        self.user.profile.save(update_fields=["role", "satker_code"])

    @skipUnless(os.getenv("DRPP_DUMMY_FIXTURE"), "Set DRPP_DUMMY_FIXTURE for local OCR acceptance.")
    def test_real_mixed_scan_recovers_three_reconciled_transactions(self):
        parsed = parse_drpp_upload_batch(os.environ["DRPP_DUMMY_FIXTURE"])

        self.assertTrue(parsed["ok"])
        self.assertEqual(len(parsed["drpp_groups"]), 1)
        group = parsed["drpp_groups"][0]
        self.assertEqual(group["no_drpp"], "00999")
        self.assertEqual(group["validation"]["status"], "BALANCE")
        self.assertEqual(len(group["items"]), 3)
        self.assertEqual(
            [item["no_kuitansi"] for item in group["items"]],
            [
                "00901/KW/019937/2026",
                "00902/KW/019937/2026",
                "00903/KW/019937/2026",
            ],
        )
        self.assertEqual(
            sum((item["nilai_bruto"] for item in group["items"]), Decimal("0")),
            Decimal("6450000"),
        )
        self.assertEqual(
            sum((item["nilai_netto"] for item in group["items"]), Decimal("0")),
            Decimal("6370000"),
        )
        self.assertEqual(
            sum((item["pph21"] for item in group["items"]), Decimal("0")),
            Decimal("80000"),
        )
        spm = parsed["spm"]["metadata"]
        self.assertEqual(spm["nomor_spm"], "00999A")
        self.assertEqual(spm["nomor_spp"], "00999T")
        self.assertEqual(spm["nomor_sp2d"], "260100000099999")
        self.assertEqual(spm["tanggal_spm"], date(2026, 7, 27))
        self.assertEqual(spm["jenis_spm"], "GUP")
        self.assertNotIn("Nomor SPP", spm["jenis_spm"])
        self.assertNotIn("Nomor SP2D", spm["jenis_spm"])
        self.assertNotIn("Cara Pembayaran", spm["jenis_spm"])
        self.assertEqual(spm["tanggal_spm"].strftime("%d/%m/%Y"), "27/07/2026")
        self.assertEqual(parsed["drpps"][0]["metadata"]["printed_total"], Decimal("6450000"))
        third = group["items"][2]
        self.assertEqual(third["nilai_bruto"], Decimal("3200000"))
        self.assertEqual(third["nilai_netto"], Decimal("3120000"))
        self.assertEqual(third["pph21"], Decimal("80000"))
        self.assertEqual(third["pembebanan"], "2886.EBA.994.002.523121")
        forbidden_description_parts = (
            "Nilai Bruto", "Nilai Netto", "Penerima", "Bendahara", "Tanda tangan",
            "Halaman", "Pembebanan", "Nomor SPM", "Nomor DRPP",
        )
        for item in group["items"]:
            for forbidden in forbidden_description_parts:
                self.assertNotIn(forbidden, item["deskripsi"])
        self.assertEqual([page["document_type"] for page in parsed["page_index"]], [
            "SPM", "DRPP_SUMMARY", "KUITANSI", "KUITANSI", "KUITANSI", "UNKNOWN",
        ])
        
    def test_A_preview_post_preserves_metadata(self):
        """Test A: POST preview hanya mengubah sebagian data, metadata parent/index tetap utuh."""
        paket = PaketSPMUpload.objects.create(
            uploaded_by=self.user,
            status=PaketSPMUpload.Status.PREVIEW,
            original_filename="dummy.zip",
            parsed_data={
                "parser_version": 2,
                "spm": {
                    "metadata": {
                        "nomor_spm": "00186A",
                        "tanggal_spm": "2026-06-30",
                        "jenis_spm": "GUP",
                        "cara_pembayaran": "UP/TUP",
                        "satker_code": "411222"
                    },
                    "metrics": {"duration": 1.2}
                },
                "page_index": [{"page_number": 1, "file_name": "SPM 001.pdf"}]
            }
        )
        
        request = self.factory.post("/paket-spm/preview/", {
            "action": "recalculate",
            "nomor_spm": "00186A-EDITED",
            "satker_code": "411222 - KPPN JAKARTA",
        })
        request.user = self.user
        request.session = {"paket_spm_preview_id": paket.id}
        setattr(request, "session", request.session)
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)
        
        response = paket_spm_preview(request)
        self.assertEqual(response.status_code, 302)
        
        paket.refresh_from_db()
        spm_meta = paket.parsed_data["spm"]["metadata"]
        
        self.assertEqual(paket.parsed_data["spm"]["metrics"]["duration"], 1.2)
        self.assertEqual(len(paket.parsed_data["page_index"]), 1)
        
        self.assertEqual(spm_meta["nomor_spm"], "00186A-EDITED")
        self.assertEqual(spm_meta["satker_code"], "411222") # Was splitting raw_satker
        
        self.assertEqual(spm_meta["tanggal_spm"], "2026-06-30")
        self.assertEqual(spm_meta["jenis_spm"], "GUP")

    @patch("apps.core.drpp_batch_parser.parse_spm_pdf")
    @patch("apps.core.drpp_batch_parser._classification")
    def test_B_multiple_pdf_same_drpp_deduplication(self, mock_classification, mock_parse_spm):
        """Test B: Dua PDF memiliki parent SPM & halaman DRPP yang sama, kuitansi berbeda."""
        mock_classification.return_value = ("UNKNOWN", 0, [])
        mock_parse_spm.return_value = {"metadata": {"nomor_spm": "00186A", "tanggal_spm": "2026-06-30", "jenis_spm": "GUP"}}
        
        page_index = [
            {"file_name": "SPM NOMOR 00186A KW 1.pdf", "page_number": 1, "page_hash": "0f", "text": "SURAT PERINTAH MEMBAYAR", "type_hint": "SPM", "is_representative": True},
            {"file_name": "SPM NOMOR 00186A KW 1.pdf", "page_number": 2, "page_hash": "f0", "text": "DAFTAR RINCIAN PERMINTAAN PEMBAYARAN", "type_hint": "DRPP_SUMMARY", "is_representative": True},
            {"file_name": "SPM NOMOR 00186A KW 1.pdf", "page_number": 3, "page_hash": "ffff", "text": "KUITANSI 1", "type_hint": "KUITANSI", "is_representative": True},
            
            {"file_name": "SPM NOMOR 00186A KW 2.pdf", "page_number": 1, "page_hash": "0f", "text": "SURAT PERINTAH MEMBAYAR", "type_hint": "SPM", "is_representative": False}, # duplicate
            {"file_name": "SPM NOMOR 00186A KW 2.pdf", "page_number": 2, "page_hash": "f0", "text": "DAFTAR RINCIAN PERMINTAAN PEMBAYARAN", "type_hint": "DRPP_SUMMARY", "is_representative": False}, # duplicate
            {"file_name": "SPM NOMOR 00186A KW 2.pdf", "page_number": 3, "page_hash": "0000", "text": "KUITANSI 2", "type_hint": "KUITANSI", "is_representative": True},
        ]
        
        from apps.core.drpp_batch_parser import deduplicate_pages
        page_index = deduplicate_pages(page_index)
        
        kept_spms = [p for p in page_index if p["type_hint"] == "SPM" and p["is_representative"]]
        kept_drpps = [p for p in page_index if p["type_hint"] == "DRPP_SUMMARY" and p["is_representative"]]
        kept_kws = [p for p in page_index if p["type_hint"] == "KUITANSI" and p["is_representative"]]
        
        self.assertEqual(len(kept_spms), 1)
        self.assertEqual(len(kept_drpps), 1)
        self.assertEqual(len(kept_kws), 2)

    def test_visually_similar_financial_pages_are_not_deduplicated(self):
        from apps.core.drpp_batch_parser import deduplicate_pages

        pages = [
            {"file_name": "DRPP A.pdf", "page_number": 1, "page_hash": "f0", "type_hint": "DRPP_SUMMARY"},
            {"file_name": "DRPP B.pdf", "page_number": 1, "page_hash": "f1", "type_hint": "DRPP_SUMMARY"},
        ]
        result = deduplicate_pages(pages)
        self.assertTrue(all(page["is_representative"] for page in result))
        
    def test_C_jenis_spm_gup_cara_pembayaran_uptup(self):
        """Test C: Jenis SPM GUP menghasilkan cara_pembayaran UP/TUP."""
        from apps.core.drpp_batch_parser import _determine_cara_pembayaran
        self.assertEqual(_determine_cara_pembayaran("GUP Reguler"), "UP/TUP")
        self.assertEqual(_determine_cara_pembayaran("GUP-KKP"), "UP/TUP")
        self.assertEqual(_determine_cara_pembayaran("TUP"), "UP/TUP")
        self.assertEqual(_determine_cara_pembayaran("PTUP"), "UP/TUP")
        self.assertEqual(_determine_cara_pembayaran("GTUP Nihil"), "UP/TUP")
        self.assertEqual(_determine_cara_pembayaran("LS Non Kontraktual"), "LS Non Kontraktual")
        self.assertEqual(_determine_cara_pembayaran("LS Kontraktual"), "LS Kontraktual")

    def test_D_sp2d_exact_match_juli(self):
        """Test D: SPM Juni dan SP2D Juli exact match -> sp2d_bulan Juli."""
        batch = SP2DImportBatch.objects.create(tahun="2026")
        SP2DRaw.objects.create(
            nomor_spm_extracted="00186A",
            satker_code="411222",
            tgl_sp2d="2026-07-02",
            bulan_sp2d=7,
            import_batch=batch,
        )
        
        from apps.core.drpp_batch_parser import resolve_spm_parent
        drpps = [{"metadata": {"nomor_spm": "00186A", "satker_code": "411222", "tahun": "2026"}}]
        
        spm, sp2d = resolve_spm_parent(drpps, [])
        self.assertIsNotNone(sp2d)
        self.assertEqual(spm["metadata"]["bulan_sp2d"], 7)
        
    def test_E_sp2d_ambiguous_review(self):
        """Test E: Lebih dari satu SP2D cocok, sp2d_bulan kosong dan review."""
        batch = SP2DImportBatch.objects.create(tahun="2026")
        SP2DRaw.objects.create(nomor_spm_extracted="00195A", satker_code="411222", tgl_sp2d="2026-07-02", bulan_sp2d=7, import_batch=batch)
        SP2DRaw.objects.create(nomor_spm_extracted="00195A", satker_code="411222", tgl_sp2d="2026-08-02", bulan_sp2d=8, import_batch=batch)
        
        from apps.core.drpp_batch_parser import resolve_spm_parent
        drpps = [{"metadata": {"nomor_spm": "00195A", "satker_code": "411222", "tahun": "2026"}}]
        
        spm, sp2d = resolve_spm_parent(drpps, [])
        self.assertIsNone(sp2d)
        
    def test_F_probe_discovers_multiple_pdf(self):
        """Test F: Probe mampu mendeteksi DRPP di dalam PDF kedua (tidak cuma PDF pertama)."""
        page_index = [
            {"file_name": "SPM 001.pdf", "page_number": 1, "is_representative": True},
            {"file_name": "SPM 001.pdf", "page_number": 2, "is_representative": True},
            {"file_name": "SPM 002.pdf", "page_number": 1, "is_representative": True},
            {"file_name": "SPM 002.pdf", "page_number": 2, "is_representative": True},
        ]
        
        with patch("apps.core.drpp_batch_parser._classification") as mock_cls:
            with patch("apps.core.drpp_batch_parser._probe_page_text") as mock_probe:
                def mock_class_fn(text):
                    if text == "TARGET": return ("DRPP_SUMMARY", 100, [])
                    return ("UNKNOWN", 0, [])
                mock_cls.side_effect = mock_class_fn
                
                def mock_probe_fn(page):
                    if page["file_name"] == "SPM 002.pdf" and page["page_number"] == 2:
                        return {"text": "TARGET", "cache_hit": False}
                    return {"text": "BLANK", "cache_hit": False}
                mock_probe.side_effect = mock_probe_fn
                
                from apps.core.drpp_batch_parser import discover_embedded_drpp_pages
                page_index = discover_embedded_drpp_pages(page_index)
                
                drpp_page = next((p for p in page_index if p.get("type_hint") == "DRPP_SUMMARY"), None)
                print('\n=== PAGE INDEX ===\n', page_index, '\n======\n')
                self.assertIsNotNone(drpp_page)
                self.assertEqual(drpp_page["file_name"], "SPM 002.pdf")
                self.assertEqual(drpp_page["page_number"], 2)
