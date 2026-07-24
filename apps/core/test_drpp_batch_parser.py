import os
import tempfile
import zipfile
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.core.exceptions import UploadTechnicalError, UploadBusinessLimitError
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
                with self.assertRaisesMessage(UploadBusinessLimitError, TOO_MANY_DRPP_MESSAGE):
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
