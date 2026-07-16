import datetime
import os
import re
import shutil
import tempfile
import zipfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.core.ocr import classify_page, extract_tesseract, ocr_cache_key
from apps.core.parsers import DOCUMENT_PARSER_REGISTRY, classify_document, classify_page_types, extract_lampiran_descriptions, make_json_safe, parse_detail_sp2d_rows_by_crop, parse_detail_sp2d_rows_from_tsv_lines, parse_drpp_items_from_tsv, parse_drpp_pdf, parse_paket_spm_zip, parse_position_detail_items, parse_spm_pdf
from apps.dk.models import TransactionDetail
from apps.dk.services import refresh_transaction_document_status
from apps.documents.models import ChecklistStatus, DocumentDriveLink
from apps.paket_spm.fixtures_test import FIXTURE_00074T_PAGES
from apps.paket_spm.models import PaketSPMUpload
from apps.paket_spm.services import build_package_decision, build_transaction_rows_from_package, link_existing_package_documents, link_paket_spm_source_document, merge_followup_into_existing_dk


User = get_user_model()


def fake_extract(pages, method="text"):
    return {
        "method": method,
        "best_engine": method,
        "status": "parsed_text" if method == "text" else "parsed_ocr",
        "warnings": [],
        "page_count": len(pages),
        "pages": pages,
        "combined_text": "\n".join(pages),
        "page_details": [
            {"text": text, "extracted_text": text, "page_number": index}
            for index, text in enumerate(pages, start=1)
        ],
        "confidence": 90.0,
        "engines_tried": [method],
        "native_text_length": len("\n".join(pages)) if method == "text" else 0,
        "tesseract_called": method != "text",
        "tesseract_text_length": len("\n".join(pages)) if method != "text" else 0,
    }


class PaketSPMRegressionTests(TestCase):
    def setUp(self):
        self.media_tmp = tempfile.TemporaryDirectory()
        self.media_settings = override_settings(MEDIA_ROOT=self.media_tmp.name, MEDIA_URL="/media/")
        self.media_settings.enable()
        self.user = User.objects.create_user(username="operator", password="password")

    def tearDown(self):
        self.media_settings.disable()
        self.media_tmp.cleanup()

    def parse_fixture(self, pages=None, method="text", filename="SPM NOMOR 00074T.pdf"):
        with patch("apps.core.parsers.extract_pdf_text", return_value=fake_extract(pages or FIXTURE_00074T_PAGES, method)):
            return parse_spm_pdf(filename, ocr=(method != "text"))

    def parsed_package(self, spm=None):
        spm = spm or self.parse_fixture()
        return make_json_safe({
            "ok": True,
            "files": [{"file_name": spm["file_name"], "type": "SPM"}],
            "spm": spm,
            "drpp": None,
            "drpps": [],
            "kw_items": [],
        })

    def paket_for(self, parsed, with_file=False, **overrides):
        drpps = parsed.get("drpps") or ([parsed.get("drpp")] if parsed.get("drpp") else [])
        meta = ((parsed.get("spm") or {}).get("metadata") or ((drpps[0] or {}).get("metadata", {}) if drpps else {}))
        raw_tanggal_spm = meta.get("tanggal_spm")
        tanggal_spm = datetime.date.fromisoformat(raw_tanggal_spm) if isinstance(raw_tanggal_spm, str) and raw_tanggal_spm else raw_tanggal_spm
        defaults = {
            "original_filename": "SPM NOMOR 00074T.pdf",
            "status": PaketSPMUpload.Status.PREVIEW,
            "uploaded_by": self.user,
            "nomor_spm": meta.get("nomor_spm", ""),
            "nomor_invoice": meta.get("nomor_invoice", ""),
            "satker_code": meta.get("satker_app_code") or meta.get("satker_code") or "",
            "tanggal_spm": tanggal_spm,
            "tahun": 2026,
            "bulan": 4,
            "jenis_spm_asli": meta.get("jenis_spm", ""),
            "jenis_spm_label": meta.get("jenis_spm", ""),
            "parsed_data": parsed,
        }
        defaults.update(overrides)
        paket = PaketSPMUpload.objects.create(**defaults)
        if with_file:
            paket.zip_file.save(defaults["original_filename"], ContentFile(b"%PDF-1.4\nsource paket spm\n"), save=True)
        return paket

    def post_preview_recalculate(self, paket, rows):
        self.client.login(username="operator", password="password")
        session = self.client.session
        session["paket_spm_preview_id"] = paket.id
        session.save()
        data = {"action": "recalculate", "preview_row_count": str(len(rows))}
        for index, row in enumerate(rows):
            for field, value in row.items():
                data[f"rows-{index}-{field}"] = value
        return self.client.post(reverse("paket_spm:preview"), data)

    def followup_parsed(self, nomor_spm="00084A", satker="1300", tahun=2026):
        return make_json_safe({
            "ok": True,
            "files": [{"file_name": "DRPP 00084A.zip", "type": "DRPP"}],
            "spm": None,
            "drpp": None,
            "drpps": [{
                "file_name": "DRPP 00009.pdf",
                "status": "parsed_ocr",
                "metadata": {
                    "nomor_drpp": "00009/DRPP/019937/2026",
                    "nomor_spm": nomor_spm,
                    "satker_app_code": satker,
                    "tanggal_spm": f"{tahun}-04-06",
                    "jenis_spm": "229 - GAJI LAINNYA",
                    "cara_pembayaran": "LS Non Kontraktual",
                    "total": "33201000",
                },
                "items": [],
            }],
            "kw_items": [
                {"akun": "511129", "jumlah": "10000000", "netto": "9000000", "pph21": "1000000", "no_bukti": "00084A", "no_drpp": "00009/DRPP/019937/2026", "keperluan": "Uang makan bagian 1", "pembebanan": "2886.EBA.994.001.511129"},
                {"akun": "511129", "jumlah": "23201000", "netto": "21840900", "pph21": "1360100", "no_bukti": "00084B", "no_drpp": "00009/DRPP/019937/2026", "keperluan": "Uang makan bagian 2", "pembebanan": "2886.EBA.994.001.511129"},
            ],
            "paket_context": {"tahun": tahun, "satker_code": satker},
        })

    def commit_package_with_document(self, parsed=None, paket=None):
        parsed = parsed or self.parsed_package()
        paket = paket or self.paket_for(parsed, with_file=True)
        with transaction.atomic():
            rows = build_transaction_rows_from_package(parsed, paket, self.user, document_status="Lengkap", save=True)
            link_paket_spm_source_document(paket, rows, user=self.user, parsed=parsed, document_status="Lengkap")
            paket.status = PaketSPMUpload.Status.COMMITTED
            paket.save(update_fields=["status"])
        return paket, rows

    def test_spm_and_spp_numbers_are_separate(self):
        spm = self.parse_fixture()
        meta = spm["metadata"]
        self.assertEqual(meta["nomor_spm"], "00074A")
        self.assertEqual(meta["nomor_spp"], "00074T")
        self.assertEqual(meta["nomor_invoice"], "00074T/019937/2026")

    def test_filename_does_not_override_labeled_spm_number(self):
        spm = self.parse_fixture(filename="SPM NOMOR 00074T.pdf")
        self.assertEqual(spm["metadata"]["nomor_spm"], "00074A")
        self.assertEqual(spm["metadata"]["nomor_spm_filename"], "00074T")

    def test_inserted_lampiran_number_is_non_blocking_warning(self):
        pages = [
            "SURAT PERINTAH MEMBAYAR Nomor 00033A Tanggal 26-Feb-2026 Jumlah Pengeluaran 35.766.000,00 TOTAL PEMBAYARAN 35.766.000,00 Jenis Tagihan : GUP",
            "LAMPIRAN SURAT PERINTAH MEMBAYAR Nomor SPM : 00031A",
            "SURAT PERMINTAAN PEMBAYARAN Nomor 00033T Tanggal 25-Feb-2026 TOTAL PEMBAYARAN 35.766.000,00",
            "LAMPIRAN SURAT PERMINTAAN PEMBAYARAN Nomor SPP : 00031T",
        ]
        spm = self.parse_fixture(pages=pages, filename="SPM NOMOR 00033T.pdf")
        meta = spm["metadata"]
        self.assertEqual(meta["nomor_spm"], "00033A")
        self.assertEqual(meta["nomor_spp"], "00033T")
        self.assertFalse(meta["nomor_spm_conflict"])
        self.assertTrue(any("00031A" in warning for warning in spm["warnings"]))
        self.assertTrue(any("00031T" in warning for warning in spm["warnings"]))

    def test_spm_without_spp_keeps_spp_empty(self):
        spm = self.parse_fixture(pages=FIXTURE_00074T_PAGES[1:4], filename="SPM 00074A.pdf")
        self.assertEqual(spm["metadata"]["nomor_spm"], "00074A")
        self.assertEqual(spm["metadata"]["nomor_spp"], "")

    def test_single_account_single_receipt_builds_one_row(self):
        parsed = self.parsed_package()
        paket = self.paket_for(parsed)
        rows = build_transaction_rows_from_package(parsed, paket, self.user, save=False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].akun, "522191")
        self.assertEqual(rows[0].no_kuitansi, "00074A")

    def test_multiple_receipts_create_multiple_rows(self):
        parsed = self.parsed_package()
        parsed["kw_items"] = [
            {"akun": "521111", "bruto": "100", "jumlah": "100", "no_bukti": "KW01", "pembebanan": "A"},
            {"akun": "522191", "bruto": "200", "jumlah": "200", "no_bukti": "KW02", "pembebanan": "B"},
        ]
        parsed["spm"]["metadata"]["jumlah_pengeluaran"] = "300"
        parsed["spm"]["metadata"]["total_pembayaran"] = "300"
        parsed["spm"]["metadata"]["jumlah_potongan"] = "0"
        paket = self.paket_for(parsed)
        rows = build_transaction_rows_from_package(parsed, paket, self.user, save=False)
        self.assertEqual(len(rows), 2)

    def test_same_dk_identity_is_grouped(self):
        parsed = self.parsed_package()
        parsed["kw_items"] = [
            {"akun": "522191", "bruto": "100", "jumlah": "100", "no_bukti": "KW01", "no_drpp": "-", "pembebanan": "P"},
            {"akun": "522191", "bruto": "200", "jumlah": "200", "no_bukti": "KW01", "no_drpp": "-", "pembebanan": "P"},
        ]
        parsed["spm"]["metadata"]["jumlah_pengeluaran"] = "300"
        parsed["spm"]["metadata"]["total_pembayaran"] = "300"
        parsed["spm"]["metadata"]["jumlah_potongan"] = "0"
        paket = self.paket_for(parsed)
        rows = build_transaction_rows_from_package(parsed, paket, self.user, save=False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].nilai_bruto, Decimal("300"))

    def test_non_pph21_deduction_reduces_netto_but_not_pph21(self):
        parsed = self.parsed_package()
        paket = self.paket_for(parsed)
        row = build_transaction_rows_from_package(parsed, paket, self.user, save=False)[0]
        self.assertEqual(row.nilai_netto, Decimal("9646380.00"))
        self.assertEqual(row.pph21, Decimal("0"))

    def test_pph21_deduction_sets_pph21(self):
        parsed = self.parsed_package()
        parsed["spm"]["metadata"]["akun_potongan"] = ["411121"]
        paket = self.paket_for(parsed)
        row = build_transaction_rows_from_package(parsed, paket, self.user, save=False)[0]
        self.assertEqual(row.pph21, Decimal("98400.00"))

    def test_multi_row_deduction_marks_ambiguous_without_zeroing_rows(self):
        parsed = self.parsed_package()
        parsed["kw_items"] = [
            {"akun": "521111", "jumlah": "500", "no_bukti": "KW01"},
            {"akun": "522191", "jumlah": "500", "no_bukti": "KW02"},
        ]
        parsed["spm"]["metadata"]["jumlah_pengeluaran"] = "1000"
        parsed["spm"]["metadata"]["total_pembayaran"] = "900"
        parsed["spm"]["metadata"]["jumlah_potongan"] = "100"
        paket = self.paket_for(parsed)
        rows = build_transaction_rows_from_package(parsed, paket, self.user, save=False)
        self.assertTrue(getattr(paket, "alokasi_potongan_ambigu", False))
        self.assertEqual([row.nilai_bruto for row in rows], [Decimal("500"), Decimal("500")])
        self.assertEqual([row.nilai_netto for row in rows], [Decimal("500"), Decimal("500")])

    def test_kw_jumlah_field_becomes_dk_bruto_and_netto(self):
        parsed = self.parsed_package()
        parsed["kw_items"] = [
            {"akun": "521211", "jumlah": "3211000", "no_bukti": "00040", "no_drpp": "00003"},
        ]
        parsed["spm"]["metadata"]["jumlah_pengeluaran"] = "3211000"
        parsed["spm"]["metadata"]["total_pembayaran"] = "3211000"
        parsed["spm"]["metadata"]["jumlah_potongan"] = "0"
        paket = self.paket_for(parsed)
        row = build_transaction_rows_from_package(parsed, paket, self.user, save=False)[0]
        self.assertEqual(row.nilai_bruto, Decimal("3211000"))
        self.assertEqual(row.nilai_netto, Decimal("3211000"))

    def test_kw_explicit_netto_and_pph21_are_used_in_dk_row(self):
        parsed = self.parsed_package()
        parsed["kw_items"] = [
            {"akun": "521213", "jumlah": "7659000", "netto": "7020750", "pph21": "638250", "no_bukti": "00055", "no_drpp": "00003"},
        ]
        parsed["spm"]["metadata"]["jumlah_pengeluaran"] = "7659000"
        parsed["spm"]["metadata"]["total_pembayaran"] = "7020750"
        parsed["spm"]["metadata"]["jumlah_potongan"] = "638250"
        paket = self.paket_for(parsed)
        row = build_transaction_rows_from_package(parsed, paket, self.user, save=False)[0]
        self.assertEqual(row.nilai_bruto, Decimal("7659000"))
        self.assertEqual(row.nilai_netto, Decimal("7020750"))
        self.assertEqual(row.pph21, Decimal("638250"))

    def test_00033a_preview_rows_use_short_kw_and_lampiran_warning_status(self):
        parsed = make_json_safe({
            "ok": True,
            "files": [{"file_name": "SPM NOMOR 00033T.pdf", "type": "SPM"}],
            "spm": {
                "file_name": "SPM NOMOR 00033T.pdf",
                "status": "parsed_ocr",
                "metadata": {
                    "nomor_spm": "00033A",
                    "nomor_spp": "00033T",
                    "nomor_sp2d": "260100000006735",
                    "satker_app_code": "1300",
                    "tanggal_spm": "2026-02-26",
                    "jenis_spm": "GUP",
                    "cara_pembayaran": "GUP",
                    "jumlah_pengeluaran": "35766000",
                    "total_pembayaran": "32959061",
                    "jumlah_potongan": "2806939",
                },
                "warnings": [
                    "Lampiran SPM 00031A terdeteksi sebagai lampiran sisipan; tidak mengganti SPM utama 00033A.",
                    "Lampiran SPP 00031T terdeteksi sebagai lampiran sisipan; tidak mengganti SPP utama 00033T.",
                ],
            },
            "drpps": [{
                "file_name": "DRPP NOMOR 00003.pdf",
                "status": "parsed_ocr",
                "metadata": {"nomor_drpp": "00003/DRPP/019937/2026", "nomor_spm": "00003", "total": "35766000"},
                "items": [],
            }],
            "kw_items": [
                {"akun": "521211", "jumlah": "3211000", "netto": "3211000", "no_bukti": "00040/KW/019937/2026", "no_drpp": "00003/DRPP/019937/2026", "pembebanan": "2897.BMA.004.055.521211"},
                {"akun": "521811", "jumlah": "18996000", "netto": "16856811", "no_bukti": "00041/KW/019937/2026", "no_drpp": "00003/DRPP/019937/2026", "pembebanan": "2897.BMA.004.055.521811", "fp": "02002600006358182"},
                {"akun": "521211", "jumlah": "5900000", "netto": "5870500", "no_bukti": "00042/KW/019937/2026", "no_drpp": "00003/DRPP/019937/2026", "pembebanan": "2897.BMA.004.055.521211"},
                {"akun": "521213", "jumlah": "7659000", "netto": "7020750", "pph21": "638250", "no_bukti": "00055/KW/019937/2026", "no_drpp": "00003/DRPP/019937/2026", "pembebanan": "2897.BMA.004.055.521213"},
            ],
        })
        paket = self.paket_for(parsed, original_filename="SPM NOMOR 00033A.zip", nomor_spm="00033A", bulan=2)
        decision = build_package_decision(parsed, current_paket_id=paket.id)
        rows = build_transaction_rows_from_package(parsed, paket, self.user, document_status=decision["document_status"], save=False)

        self.assertEqual(decision["document_status"], "Lengkap dengan Peringatan Lampiran")
        self.assertEqual([row.akun + row.no_kuitansi for row in rows], ["52121100040", "52181100041", "52121100042", "52121300055"])
        self.assertEqual([row.no_kuitansi for row in rows], ["00040", "00041", "00042", "00055"])
        self.assertEqual({row.cara_pembayaran for row in rows}, {"UP/TUP"})
        self.assertEqual({row.jenis_spm for row in rows}, {"GUP"})
        self.assertEqual(rows[1].fp, "02002600006358182")
        self.assertEqual(rows[3].pph21, Decimal("638250"))

        from apps.paket_spm.views import build_drpp_rows

        self.assertEqual(build_drpp_rows(parsed)[0]["nomor_spm"], "00033A")
        self.client.login(username="operator", password="password")
        session = self.client.session
        session["paket_spm_preview_id"] = paket.id
        session.save()
        response = self.client.get(reverse("paket_spm:preview"))
        self.assertEqual(response.context["summary_document_status"], "Lengkap dengan Peringatan Lampiran")
        self.assertEqual(response.context["preview_summary"]["document_status"], "Lengkap dengan Peringatan Lampiran")
        self.assertContains(response, "Lengkap dengan Peringatan Lampiran")

    def test_satker_mapping_uses_app_code_and_name(self):
        spm = self.parse_fixture()
        meta = spm["metadata"]
        self.assertEqual(meta["satker_code"], "019937")
        self.assertEqual(meta["satker_app_code"], "1300")
        self.assertEqual(meta["satker_app_name"], "BPS Provinsi Sumatera Barat")

    def test_exact_match_links_existing(self):
        parsed = self.parsed_package()
        paket = self.paket_for(parsed)
        TransactionDetail.objects.create(
            satker_code="1300",
            nomor_spm="00074A",
            tanggal_spm=datetime.date(2026, 3, 27),
            akun="522191",
            no_kuitansi="00074A",
            no_drpp="-",
            pembebanan="2886.EBA.994.002.522191",
        )
        decision = build_package_decision(parsed, current_paket_id=paket.id)
        self.assertEqual(decision["commit_action"], "link_existing")
        rows = build_transaction_rows_from_package(parsed, paket, self.user, save=False)
        self.assertEqual(rows, [])

    def test_different_year_is_not_duplicate(self):
        parsed = self.parsed_package()
        paket = self.paket_for(parsed)
        TransactionDetail.objects.create(
            satker_code="1300",
            nomor_spm="00074A",
            tanggal_spm=datetime.date(2025, 3, 27),
            akun="522191",
        )
        decision = build_package_decision(parsed, current_paket_id=paket.id)
        self.assertEqual(decision["commit_action"], "create_from_package")

    def test_missing_required_date_blocks_commit(self):
        parsed = self.parsed_package()
        parsed["spm"]["metadata"]["tanggal_spm"] = ""
        paket = self.paket_for(parsed, tanggal_spm=None, tahun=None)
        with self.assertRaisesMessage(ValueError, "Tanggal SPM belum valid"):
            build_transaction_rows_from_package(parsed, paket, self.user, save=False)

    def test_scanned_ocr_fixture_uses_same_parser_path(self):
        spm = self.parse_fixture(method="tesseract")
        self.assertEqual(spm["metadata"]["nomor_spm"], "00074A")
        self.assertTrue(spm["tesseract_called"])

    def test_fixture_00074t_produces_target_15_columns(self):
        parsed = self.parsed_package()
        paket = self.paket_for(parsed)
        row = build_transaction_rows_from_package(parsed, paket, self.user, save=False)[0]
        self.assertEqual([
            row.akun + row.no_kuitansi,
            row.akun,
            row.bulan_sp2d,
            row.cara_pembayaran,
            row.nomor_spm,
            row.tanggal_spm,
            row.jenis_spm,
            row.no_kuitansi,
            row.no_drpp,
            row.deskripsi,
            row.nilai_bruto,
            row.nilai_netto,
            row.pembebanan,
            row.fp,
            row.pph21,
        ], [
            "52219100074A",
            "522191",
            4,
            "LS Pegawai",
            "00074A",
            datetime.date(2026, 3, 27),
            "Penghasilan PPNPN Induk",
            "00074A",
            "",
            "Pembayaran Belanja Barang Berupa Honor PPNPN Bulan Maret Tahun 2026 untuk 3 Pegawai",
            Decimal("9744780.00"),
            Decimal("9646380.00"),
            "2886.EBA.994.002.522191",
            "FP-2026-019937-92000-507",
            Decimal("0"),
        ])

    def test_fixture_00084a_produces_target_15_columns_without_duplicate_pages(self):
        pages = [
            "SURAT PERINTAH MEMBAYAR Nomor SPM : 00084A Tanggal 06-04-2026 "
            "Kode Satker 019937 BADAN PUSAT STATISTIK PROVINSI SUMATERA BARAT "
            "Jenis Tagihan : 229 Dasar Pembayaran GAJI LAINNYA DIPA "
            "URAIAN Pembayaran : Pembayaran Belanja Pegawai berupa uang Kode Akun Pajak Kode Jenis Setoran | "
            "makan bulan Maret 2026 sebanyak 53 pegawai |i {olo| Masa Pajak pt txt | | tT | "
            "Jumlah Pembayaran: Rp. 2.360.100,00 SURAT SETORAN PAJAK Untuk KPPN NOP "
            "NO SP2D 260100000014604 TGL SP2D 2026-04-06 NOMOR INVOICE 00084T/019937/2026 "
            "COA 019937.010.511129.05401WA.2886EBA.A000000001.00000,2.0800.2.000000,000000.994,001.0A,000434 "
            "FP-2026-019937-92000-434 "
            "POTONGAN 411121 2.360.100 JUMLAH PENGELUARAN 33.201.000 JUMLAH POTONGAN 2.360.100 TOTAL PEMBAYARAN 30.840.900",
            "SURAT PERMINTAAN PEMBAYARAN Nomor SPP : 00084T",
            "SURAT PERINTAH MEMBAYAR Nomor SPM : 00084A Tanggal 06-04-2026 "
            "COA 019937 010 511129 05401WA 2886EBA A000000001 00000 2 0800 2 000000 000000 994 001 0A 000434",
            "SURAT PERMINTAAN PEMBAYARAN Nomor SPP : 00084T",
        ]
        spm = self.parse_fixture(pages=pages, filename="SPM NOMOR 00084T.pdf")
        parsed = self.parsed_package(spm)
        paket = self.paket_for(parsed, original_filename="SPM NOMOR 00084T.pdf", bulan=4)
        rows = build_transaction_rows_from_package(parsed, paket, self.user, save=False)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual([
            row.akun + row.no_kuitansi,
            row.akun,
            row.bulan_sp2d,
            row.cara_pembayaran,
            row.nomor_spm,
            row.tanggal_spm,
            row.jenis_spm,
            row.no_kuitansi,
            row.no_drpp,
            row.deskripsi,
            row.nilai_bruto,
            row.nilai_netto,
            row.pembebanan,
            row.fp,
            row.pph21,
        ], [
            "51112900084A",
            "511129",
            4,
            "LS Non Kontraktual",
            "00084A",
            datetime.date(2026, 4, 6),
            "229 - GAJI LAINNYA",
            "00084A",
            "",
            "Pembayaran Belanja Pegawai berupa uang makan bulan Maret 2026 sebanyak 53 pegawai",
            Decimal("33201000.00"),
            Decimal("30840900.00"),
            "2886.EBA.994.001.511129",
            "FP-2026-019937-92000-434",
            Decimal("2360100.00"),
        ])

    def test_scan_00135t_matches_existing_dk_and_builds_detail_rows(self):
        amounts = [Decimal(value) for value in [
            "2264000", "320000", "2775000", "770000", "3558750", "2213200",
            "15383780", "2936505", "777000", "11700000", "3175000", "350937",
        ]]

        def rupiah(value):
            return f"{int(value):,}".replace(",", ".")

        detail_rows = " ".join(
            f"019937.010.521211.05401WA.2886EBA.A000000001.00000.2.0800.2.000000.000000.994.001.0A.{300 + index:06d} "
            f"Belanja operasional {index} {rupiah(amount)}{',00' if index == 12 else ''} {rupiah(amount)}"
            for index, amount in enumerate(amounts, start=1)
        )
        pages = [
            "BADAN PUSAT STATISTIK PROVINSI SUMATERA BARAT SURAT PERINTAH MEMBAYAR "
            "Nomor SPM : 00135A Tanggal : 22-Mei-2026 Jenis SPM : GUP "
            "Uraian : Pembayaran uang persediaan kegiatan Mei 2026 NOP "
            "JUMLAH PENGELUARAN 46.224.172 TOTAL PEMBAYARAN 46.224.172",
            "SURAT PERMINTAAN PEMBAYARAN Nomor SPP : 00135T Tanggal : 22-Mei-2026",
            "DETAIL PENGELUARAN DAN POTONGAN PADA SPP/SPM/SP2D "
            "No. SPP/SPM 00135T/019937/2026 NO SP2D 260100000024403 TGL SP2D 2026-05-25 "
            + detail_rows
            + " JUMLAH PENGELUARAN 46.224.172",
            "LAMPIRAN SPP RINCIAN SAMA "
            + detail_rows
            + " JUMLAH PENGELUARAN 46.224.172",
        ]
        spm = self.parse_fixture(pages=pages, filename="SPM NOMOR 00135T.pdf")
        parsed = self.parsed_package(spm)
        TransactionDetail.objects.create(
            satker_code="1300",
            nomor_spm="00135T",
            tanggal_spm=None,
            akun="",
            nilai_bruto=Decimal("46224172"),
        )
        paket = self.paket_for(parsed, original_filename="SPM NOMOR 00135T.pdf", bulan=5)

        decision = build_package_decision(parsed, paket.original_filename, current_paket_id=paket.id)
        self.assertEqual(decision["commit_action"], "link_existing")
        self.assertEqual(parsed["spm"]["metadata"]["nomor_spm"], "00135T")
        self.assertIn("00135A", " ".join(decision["notes"]))
        self.assertEqual(parsed["spm"]["metadata"]["tanggal_spm"], "2026-05-22")
        self.assertEqual(parsed["spm"]["metadata"]["nomor_sp2d"], "260100000024403")
        self.assertEqual(parsed["spm"]["metadata"]["tanggal_sp2d"], "2026-05-25")

        rows = build_transaction_rows_from_package(
            parsed,
            paket,
            self.user,
            document_status=decision["document_status"],
            save=False,
            skip_existing=False,
        )
        self.assertEqual(len(rows), 12)
        self.assertEqual([row.nilai_bruto for row in rows], amounts)
        self.assertEqual(sum((row.nilai_bruto for row in rows), Decimal("0")), Decimal("46224172"))
        self.assertTrue(all(row.nilai_netto == row.nilai_bruto for row in rows))
        self.assertTrue(all(row.nomor_spm == "00135T" for row in rows))
        self.assertTrue(all("350.937" not in row.pembebanan for row in rows))
        self.assertIn("OPERASIONAL 1", rows[0].deskripsi)

    def test_page_aware_pipeline_handles_random_pages_spacing_and_rupiah(self):
        detail_row = (
            "019937 010 521211 05401WA 2886EBA A000000001 00000 2 0800 2 000000 000000 "
            "994 001 0A 000777 Belanja ATK satuan kerja 350.937,00 350.937,00 "
            "JUMLAH PENGELUARAN 46.224.172"
        )
        pages = [
            "DETAIL PENGELUARAN DAN POTONGAN PADA SPP/SPM/SP2D "
            "No. SPP/SPM 00999T/019937/2026 NO SP2D 260100000099999 TGL SP2D 2026-05-25 "
            + detail_row,
            "SURAT PERMINTAAN PEMBAYARAN Nomor SPP : 00999T Tanggal : 22 Mei 2026",
            "SURAT PERINTAH MEMBAYAR Nomor SPM : 00999A Tanggal : 22-Mei-2026 "
            "Kode Satker 019937 Jenis SPM : GUP JUMLAH PENGELUARAN 350.937,00 TOTAL PEMBAYARAN 350.937,00",
            "LAMPIRAN SPP HALAMAN GANDA " + detail_row,
        ]
        spm = self.parse_fixture(pages=pages, method="tesseract", filename="SPM NOMOR 00999T.pdf")
        parsed = self.parsed_package(spm)
        paket = self.paket_for(parsed, original_filename="SPM NOMOR 00999T.pdf", bulan=5)
        rows = build_transaction_rows_from_package(parsed, paket, self.user, save=False, skip_existing=False)

        self.assertEqual(spm["metadata"]["nomor_spm"], "00999A")
        self.assertEqual(spm["metadata"]["nomor_spp"], "00999T")
        self.assertEqual(spm["metadata"]["tanggal_spm"], datetime.date(2026, 5, 22))
        self.assertEqual(spm["metadata"]["nomor_sp2d"], "260100000099999")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].nilai_bruto, Decimal("350937"))
        self.assertEqual(rows[0].nilai_netto, Decimal("350937"))
        self.assertEqual(rows[0].pembebanan, "2886.EBA.994.001.521211")
        self.assertNotIn("46.224.172", str(rows[0].nilai_bruto))
        page_types = [page["page_types"] for page in spm["page_details"]]
        self.assertTrue(any("DETAIL_SPP_SPM_SP2D" in types for types in page_types))
        self.assertTrue(any("SPM" in types for types in page_types))

    def test_position_ocr_detail_page_uses_real_tesseract(self):
        if not shutil.which("tesseract"):
            self.skipTest("tesseract binary tidak tersedia")
        if os.getenv("RUN_SLOW_OCR_UPLOAD_TESTS") != "1":
            self.skipTest("duplikat OCR real 00135T; set RUN_SLOW_OCR_UPLOAD_TESTS=1 untuk menjalankan")
        pdf_path = os.path.join(os.getcwd(), "media", "tmp", "SPM NOMOR 00135T.pdf")
        self.assertTrue(os.path.exists(pdf_path), "PDF asli 00135T harus tersedia untuk integration test lokal.")

        parsed_rows = parse_detail_sp2d_rows_by_crop(pdf_path, 1, 270, ["DETAIL_SPP_SPM_SP2D"], {})

        self.assertEqual(len(parsed_rows), 12)
        self.assertEqual(sum((row["jumlah"] for row in parsed_rows), Decimal("0")), Decimal("46224172"))
        self.assertIn("522113", [row["akun"] for row in parsed_rows])
        self.assertIn(Decimal("350937"), [row["jumlah"] for row in parsed_rows])

    def test_real_00135t_detail_table_uses_grid_ocr(self):
        if not shutil.which("tesseract"):
            self.skipTest("tesseract binary tidak tersedia")
        pdf_path = os.path.join(
            os.getcwd(),
            "media",
            "tmp",
            "SPM NOMOR 00135T.pdf",
        )
        self.assertTrue(os.path.exists(pdf_path), "PDF asli 00135T harus tersedia untuk integration test lokal.")

        parsed_rows = parse_detail_sp2d_rows_by_crop(pdf_path, 1, 270, ["DETAIL_SPP_SPM_SP2D"], {})
        total = sum((row["jumlah"] for row in parsed_rows), Decimal("0"))

        self.assertEqual(len(parsed_rows), 12)
        self.assertEqual(total, Decimal("46224172"))
        self.assertEqual({row["nomor_sp2d"] for row in parsed_rows}, {"260100000024403"})
        self.assertEqual({str(row["tanggal_sp2d"]) for row in parsed_rows}, {"2026-05-25"})
        self.assertFalse(any(re.search(r"\d{1,3}\.\d{3}$", row["pembebanan"]) for row in parsed_rows))

    def test_real_00195a_ls_detail_page_uses_high_res_tsv(self):
        if not shutil.which("tesseract"):
            self.skipTest("tesseract binary tidak tersedia")
        pdf_path = os.path.join(os.getcwd(), "media", "tmp", "SPM NOMOR 00195A.pdf")
        self.assertTrue(os.path.exists(pdf_path), "PDF asli 00195A harus tersedia untuk targeted regression test lokal.")

        spm = parse_spm_pdf(pdf_path, ocr=True)
        items = spm.get("detail_items") or []

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["akun"], "512414")
        self.assertEqual(item["bruto"], Decimal("16437279"))
        self.assertEqual(item["netto"], Decimal("16299408.00"))
        self.assertEqual(item["pph21"], Decimal("137871.00"))
        self.assertEqual(item["pembebanan"], "2886.EBA.994.001.512414")
        self.assertEqual(item["source_page"], 1)
        self.assertTrue(item.get("source_bbox"))
        self.assertEqual(spm["metadata"]["jumlah_pengeluaran"], Decimal("16437279.00"))
        self.assertEqual(spm["metadata"]["jumlah_potongan"], Decimal("137871.00"))
        self.assertEqual(spm["metadata"]["total_pembayaran"], Decimal("16299408.00"))
        self.assertEqual(spm["metadata"]["detail_parse_summary"]["source"], "DETAIL_SPP_SPM_SP2D")

    def test_real_drpp_00029_and_00030_use_production_ocr_without_support_as_items(self):
        if not shutil.which("tesseract"):
            self.skipTest("tesseract binary tidak tersedia")
        corpus = [
            ("DRPP 00029.pdf", "00029/DRPP/019937/2026", 12, Decimal("30195422")),
            ("DRPP 00030 KW 00209.pdf", "00030/DRPP/019937/2026", 1, Decimal("3558750")),
        ]
        self.assertEqual(classify_document("DRPP 00030 KW 00209.pdf", ""), "DRPP")
        for filename, no_drpp, expected_count, expected_total in corpus:
            with self.subTest(filename=filename):
                pdf_path = os.path.join(os.getcwd(), "media", "tmp", filename)
                self.assertTrue(os.path.exists(pdf_path), f"PDF asli {filename} harus tersedia untuk integration test lokal.")

                parsed = parse_drpp_pdf(pdf_path, ocr=True)
                items = parsed.get("items") or []
                total = sum((item["jumlah"] for item in items), Decimal("0"))

                self.assertEqual(parsed["metadata"]["nomor_drpp"], no_drpp)
                self.assertEqual(len(items), expected_count)
                self.assertEqual(total, expected_total)
                self.assertTrue(parsed["metadata"]["total_valid"])
                self.assertTrue(all(item["no_drpp"] == no_drpp for item in items))
        item_00030 = parse_drpp_pdf(os.path.join(os.getcwd(), "media", "tmp", "DRPP 00030 KW 00209.pdf"), ocr=True)["items"][0]
        self.assertEqual(item_00030["no_bukti"], "00209/KW/019937/2026")
        self.assertEqual(item_00030["akun"], "521811")

    def test_real_drpp_00030_upload_uses_production_path_and_not_kw_standalone(self):
        if not shutil.which("tesseract"):
            self.skipTest("tesseract binary tidak tersedia")
        if os.getenv("RUN_SLOW_OCR_UPLOAD_TESTS") != "1":
            self.skipTest("set RUN_SLOW_OCR_UPLOAD_TESTS=1 untuk test upload OCR penuh 31 halaman")
        pdf_path = os.path.join(os.getcwd(), "media", "tmp", "DRPP 00030 KW 00209.pdf")
        self.assertTrue(os.path.exists(pdf_path), "PDF asli DRPP 00030 harus tersedia untuk integration test lokal.")
        self.client.login(username="operator", password="password")

        with open(pdf_path, "rb") as handle:
            response = self.client.post(
                reverse("paket_spm:list"),
                {
                    "document_files": SimpleUploadedFile(
                        "DRPP 00030 KW 00209.pdf",
                        handle.read(),
                        content_type="application/pdf",
                    ),
                    "satker_code": "1300",
                    "tahun": "2026",
                    "use_ocr": "on",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        paket = PaketSPMUpload.objects.latest("id")
        parsed = paket.parsed_data
        self.assertEqual(parsed["files"][0]["type"], "DRPP")
        self.assertEqual(len(parsed["kw_items"]), 1)
        self.assertEqual(parsed["kw_items"][0]["no_bukti"], "00209/KW/019937/2026")
        self.assertEqual(Decimal(str(parsed["kw_items"][0]["jumlah"])), Decimal("3558750"))

    def test_00135t_lampiran_description_enrichment_from_cached_ocr_fixture(self):
        fixture_pages = {
            3: {
                "source_types": ["LAMPIRAN_COA"],
                "text": "",
                "lines": [],
                "variants": [{
                    "text": (
                        "004.056.0A.000054-Pengadaan software pendukung perwajahan publikasi BPS Provinsi 11.700.000,00 "
                        "006.052.0A.000014-Perlengkapan Pembinaan Agen Statistik 770.000,00 "
                        "006.523.0A.000372-Belanja Persediaan Bahan Sosialisasi SE2026 tlh 3.558.750,00 "
                        "994.002.0A.000245-Pemeliharaan PC/laptop/notebook b] "
                        "994.002.0A.000299-Kebutuhan dasar Perkantoran , |: 019937.010.522119.05401WA "
                        "994.002.0A.000303-Langganan Internet J S "
                        "994.002.0A.000306-Jasa Pelayanan Hygiene Toilet dan Ruang Kerja y "
                        "994.002.0A.000307-Jasa Pengendali Hama Lingkungan aE , S) "
                        "994.002.0A.000308-Pemeliharaan Jaringan Listrik/Telepon/LAN 2.213.200,00 "
                        "994.002.0A.000313-Langganan Koran a [Kode : PEM003]"
                    ),
                    "lines": [],
                }, {
                    "text": (
                        "004.056.0A.000054-Pengadaan software pendukung perwajahan publikasi BPS Provinsi IE "
                        "994.002.0A.000299-Kebutuhan dasar Perkantoran SS "
                        "994.002.0A.000303-Langganan Internet "
                        "994.002.0A.000306-Jasa Pelayanan Hygiene Toilet dan Ruang Kerja "
                        "994.002.0A.000307-Jasa Pengendali Hama Lingkungan , S) aE ’ S el "
                        "994.002.0A.000308-Pemeliharaan Jaringan Listrik/Telepon/LAN ’ x 8 LE "
                        "994.002.0A.000313-Langganan Koran : ’ S"
                    ),
                    "lines": [],
                }, {
                    "text": (
                        "994.002.0A.000299-Kebutuhan dasar Perkantoran ’ S "
                        "994.002.0A.000313-Langganan Koran : ’ S "
                        "994.002.0A.000308-Pemeliharaan Jaringan Listrik/Telepon/LAN 4 ’ x 8 Ml "
                        "994.002.0A.000307-Jasa Pengendali Hama Lingkungan um"
                    ),
                    "lines": [],
                }],
            },
            4: {
                "source_types": ["LAMPIRAN_COA"],
                "text": "",
                "lines": [],
                "variants": [{
                    "text": "994.002.0A.000323-Belanja Peralatan dan mesin - Ekstrakomtabel Lainnya eee! Padang a.n. Kuasa Pengguna Anggaran",
                    "lines": [],
                }],
            },
            7: {
                "source_types": ["LAMPIRAN_COA"],
                "text": "",
                "lines": [],
                "variants": [{
                    "text": "994.002.0A.000234-Biaya langganan air a iserorasaxiaosmwazearonowowooroneozowozonnon",
                    "lines": [],
                }],
            },
        }
        expected = {
            "004.056.0A.000054": "Pengadaan software pendukung perwajahan publikasi BPS Provinsi",
            "006.052.0A.000014": "Perlengkapan Pembinaan Agen Statistik",
            "006.523.0A.000372": "Belanja Persediaan Bahan Sosialisasi SE2026",
            "994.002.0A.000234": "Biaya langganan air",
            "994.002.0A.000245": "Pemeliharaan PC/laptop/notebook",
            "994.002.0A.000299": "Kebutuhan dasar Perkantoran",
            "994.002.0A.000303": "Langganan Internet",
            "994.002.0A.000306": "Jasa Pelayanan Hygiene Toilet dan Ruang Kerja",
            "994.002.0A.000307": "Jasa Pengendali Hama Lingkungan",
            "994.002.0A.000308": "Pemeliharaan Jaringan Listrik/Telepon/LAN",
            "994.002.0A.000313": "Langganan Koran",
            "994.002.0A.000323": "Belanja Peralatan dan mesin - Ekstrakomtabel Lainnya",
        }

        descriptions = extract_lampiran_descriptions(fixture_pages)

        self.assertEqual(descriptions, expected)
        joined = " ".join(descriptions.values())
        self.assertNotIn("Rincian", joined)
        self.assertFalse(re.search(r"(?:cieer|ieer|iaer|iser|womowen|eee!|aE|\\bb\\]|\\bJ S\\b)", joined))

    def test_sp2d_month_prefers_tanggal_sp2d_from_detail_table(self):
        parsed = self.parsed_package()
        parsed["spm"]["metadata"]["tanggal_sp2d"] = ""
        parsed["spm"]["detail_items"] = [{
            "akun": "522113",
            "jumlah": "350937",
            "netto": "350937",
            "no_bukti": "000234",
            "keperluan": "Biaya langganan air",
            "pembebanan": "2886.EBA.994.002.522113",
            "source_row_id": "994.002.0A.000234",
            "tanggal_sp2d": "2026-05-25",
        }]
        paket = self.paket_for(parsed, original_filename="SPM NOMOR 00135T.pdf", bulan=4)

        rows = build_transaction_rows_from_package(parsed, paket, self.user, save=False, skip_existing=False)

        self.assertEqual(rows[0].bulan_sp2d, 5)

    def test_preview_row_edit_persists_helper_and_commit_values(self):
        parsed = self.parsed_package()
        paket = self.paket_for(parsed, with_file=True)
        edited = [{
            "akun": "511129",
            "bulan_sp2d": "April",
            "cara_pembayaran": "LS Non Kontraktual",
            "nomor_spm": "00084A",
            "tanggal_spm": "2026-04-06",
            "jenis_spm": "229 - GAJI LAINNYA",
            "no_kuitansi": "00084A",
            "no_drpp": "-",
            "deskripsi": "Pembayaran edit",
            "nilai_bruto": "33.201.000",
            "nilai_netto": "30.840.900",
            "pembebanan": "2886.EBA.994.001.511129",
            "fp": "FP-2026-019937-92000-434",
            "pph21": "2.360.100",
        }]
        response = self.post_preview_recalculate(paket, edited)
        self.assertEqual(response.status_code, 302)
        paket.refresh_from_db()
        rows = build_transaction_rows_from_package(paket.parsed_data, paket, self.user, save=False)
        self.assertEqual(rows[0].akun + rows[0].no_kuitansi, "51112900084A")
        self.assertEqual(rows[0].nilai_bruto, Decimal("33201000"))
        self.assertEqual(rows[0].pph21, Decimal("2360100"))
        committed = build_transaction_rows_from_package(paket.parsed_data, paket, self.user, save=True)
        self.assertEqual(committed[0].pembebanan, "2886.EBA.994.001.511129")

    def test_editing_second_preview_row_does_not_change_first(self):
        parsed = self.parsed_package()
        parsed["kw_items"] = [
            {"akun": "521111", "jumlah": "100", "no_bukti": "KW01", "pembebanan": "P1"},
            {"akun": "522191", "jumlah": "200", "no_bukti": "KW02", "pembebanan": "P2"},
        ]
        parsed["spm"]["metadata"]["jumlah_pengeluaran"] = "300"
        parsed["spm"]["metadata"]["total_pembayaran"] = "300"
        paket = self.paket_for(parsed)
        rows = build_transaction_rows_from_package(parsed, paket, self.user, save=False)
        form_rows = []
        for row in rows:
            form_rows.append({
                "akun": row.akun,
                "bulan_sp2d": "April",
                "cara_pembayaran": row.cara_pembayaran,
                "nomor_spm": row.nomor_spm,
                "tanggal_spm": row.tanggal_spm.isoformat(),
                "jenis_spm": row.jenis_spm,
                "no_kuitansi": row.no_kuitansi,
                "no_drpp": row.no_drpp,
                "deskripsi": row.deskripsi,
                "nilai_bruto": str(row.nilai_bruto),
                "nilai_netto": str(row.nilai_netto),
                "pembebanan": row.pembebanan,
                "fp": row.fp,
                "pph21": str(row.pph21),
            })
        form_rows[1]["akun"] = "533111"
        form_rows[1]["no_kuitansi"] = "KW99"
        self.post_preview_recalculate(paket, form_rows)
        paket.refresh_from_db()
        edited_rows = build_transaction_rows_from_package(paket.parsed_data, paket, self.user, save=False)
        self.assertEqual(edited_rows[0].akun + edited_rows[0].no_kuitansi, "521111KW01")
        self.assertEqual(edited_rows[1].akun + edited_rows[1].no_kuitansi, "533111KW99")

    def test_double_click_commit_does_not_create_duplicate_rows(self):
        parsed = self.parsed_package()
        paket = self.paket_for(parsed)
        build_transaction_rows_from_package(parsed, paket, self.user, save=True)
        build_transaction_rows_from_package(parsed, paket, self.user, save=True)
        self.assertEqual(TransactionDetail.objects.count(), 1)

    def test_commit_new_package_creates_transaction_and_spm_document(self):
        _, rows = self.commit_package_with_document()
        self.assertEqual(len(rows), 1)
        tx = rows[0]
        tx.refresh_from_db()
        self.assertEqual(TransactionDetail.objects.filter(id=tx.id).count(), 1)
        link = DocumentDriveLink.objects.get(transaction_detail=tx)
        self.assertEqual(link.jenis_dokumen, "SPM")
        self.assertEqual(link.nama_file, "SPM NOMOR 00074T.pdf")
        self.assertEqual(link.status, DocumentDriveLink.Status.AKTIF)
        self.assertEqual(tx.status_detail, TransactionDetail.StatusDetail.LENGKAP)

    def test_committed_source_file_is_permanent_after_temp_upload_removed(self):
        paket, rows = self.commit_package_with_document()
        source_path = paket.zip_file.path
        link = DocumentDriveLink.objects.get(transaction_detail=rows[0])
        marker = "local_path="
        local_path = link.catatan.split(marker, 1)[1].split(";", 1)[0]
        os.remove(source_path)
        self.assertFalse(os.path.exists(source_path))
        self.assertTrue(os.path.exists(local_path))

    def test_spm_document_linked_to_correct_transaction_and_checklist_only_spm(self):
        _, rows = self.commit_package_with_document()
        tx = rows[0]
        self.assertTrue(DocumentDriveLink.objects.filter(transaction_detail=tx, jenis_dokumen="SPM").exists())
        self.assertTrue(ChecklistStatus.objects.filter(transaction_detail=tx, nama_dokumen="SPM", status=ChecklistStatus.Status.ADA).exists())
        self.assertFalse(ChecklistStatus.objects.filter(transaction_detail=tx, nama_dokumen__icontains="DRPP", status=ChecklistStatus.Status.ADA).exists())
        _, completion = refresh_transaction_document_status(tx)
        self.assertEqual(completion["percent"], 100)

    def test_spm_document_appears_in_saved_docs_list(self):
        _, rows = self.commit_package_with_document()
        self.client.login(username="operator", password="password")
        response = self.client.get(reverse("documents:checklist_detail", args=[rows[0].id]))
        self.assertContains(response, "Dokumen Sudah Tersimpan")
        self.assertContains(response, "SPM NOMOR 00074T.pdf")

    def test_missing_sp2d_only_affects_reconciliation_status(self):
        _, rows = self.commit_package_with_document()
        self.client.login(username="operator", password="password")
        response = self.client.get(reverse("dk:list"))
        self.assertContains(response, "Lengkap")
        self.assertContains(response, "Belum ada SP2D pembanding")

    def test_save_checklist_recalculates_dk_document_status(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        tx = TransactionDetail.objects.create(
            satker_code="1300",
            nomor_spm="00074A",
            tanggal_spm=datetime.date(2026, 3, 27),
            akun="522191",
            status_detail=TransactionDetail.StatusDetail.PERLU_REVIEW,
        )
        status = ChecklistStatus.objects.create(
            transaction_detail=tx,
            nama_dokumen="SPM",
            wajib=True,
            status=ChecklistStatus.Status.BELUM,
        )
        self.client.login(username="operator", password="password")
        response = self.client.post(
            reverse("documents:checklist_detail", args=[tx.id]),
            {"action": "save_checklist", f"checklist_status_{status.id}": ChecklistStatus.Status.ADA},
        )
        self.assertEqual(response.status_code, 302)
        tx.refresh_from_db()
        self.assertEqual(tx.status_detail, TransactionDetail.StatusDetail.LENGKAP)

    def test_missing_required_document_stays_review(self):
        tx = TransactionDetail.objects.create(
            satker_code="1300",
            nomor_spm="00074A",
            tanggal_spm=datetime.date(2026, 3, 27),
            akun="522191",
            status_detail=TransactionDetail.StatusDetail.LENGKAP,
        )
        ChecklistStatus.objects.create(
            transaction_detail=tx,
            nama_dokumen="SPM",
            wajib=True,
            status=ChecklistStatus.Status.BELUM,
        )
        refresh_transaction_document_status(tx)
        tx.refresh_from_db()
        self.assertEqual(tx.status_detail, TransactionDetail.StatusDetail.PERLU_REVIEW)

    def test_dk_list_shows_refreshed_document_status(self):
        _, rows = self.commit_package_with_document()
        rows[0].refresh_from_db()
        self.client.login(username="operator", password="password")
        response = self.client.get(reverse("dk:list"), {"q": "00074A"})
        self.assertContains(response, "Lengkap")
        self.assertNotContains(response, "Perlu Review")

    def test_exact_match_links_pdf_without_adding_dk_or_duplicate_document(self):
        parsed = self.parsed_package()
        paket = self.paket_for(parsed, with_file=True)
        tx = TransactionDetail.objects.create(
            satker_code="1300",
            nomor_spm="00074A",
            tanggal_spm=datetime.date(2026, 3, 27),
            akun="522191",
            no_kuitansi="00074A",
            no_drpp="-",
            pembebanan="2886.EBA.994.002.522191",
        )
        link_paket_spm_source_document(paket, [tx], user=self.user, parsed=parsed, document_status="Lengkap")
        link_paket_spm_source_document(paket, [tx], user=self.user, parsed=parsed, document_status="Lengkap")
        self.assertEqual(TransactionDetail.objects.count(), 1)
        self.assertEqual(DocumentDriveLink.objects.filter(transaction_detail=tx, jenis_dokumen="SPM").count(), 1)

    def test_file_archive_failure_rolls_back_transaction_commit(self):
        parsed = self.parsed_package()
        paket = self.paket_for(parsed, with_file=True)
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                rows = build_transaction_rows_from_package(parsed, paket, self.user, save=True)
                with patch("apps.paket_spm.services.archive_file_link", side_effect=RuntimeError("disk full")):
                    link_paket_spm_source_document(paket, rows, user=self.user, parsed=parsed)
        self.assertEqual(TransactionDetail.objects.count(), 0)
        self.assertEqual(DocumentDriveLink.objects.count(), 0)

    def test_cancel_deletes_only_active_draft(self):
        self.client.login(username="operator", password="password")
        parsed = self.parsed_package()
        active = self.paket_for(parsed, with_file=True, original_filename="active.pdf")
        other = self.paket_for(parsed, with_file=True, original_filename="other.pdf")
        other_tx = TransactionDetail.objects.create(
            satker_code="1300",
            nomor_spm="00074A",
            tanggal_spm=datetime.date(2026, 3, 27),
            akun="522191",
            no_kuitansi="00074A",
        )
        link_paket_spm_source_document(other, [other_tx], user=self.user, parsed=parsed)
        active_path = active.zip_file.path
        other_source_path = other.zip_file.path
        other_link = DocumentDriveLink.objects.get(transaction_detail=other_tx)
        other_archive_path = other_link.catatan.split("local_path=", 1)[1].split(";", 1)[0]
        session = self.client.session
        session["paket_spm_preview_id"] = active.id
        session.save()
        response = self.client.post(reverse("paket_spm:preview"), {"action": "cancel"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(PaketSPMUpload.objects.filter(id=active.id).exists())
        self.assertTrue(PaketSPMUpload.objects.filter(id=other.id).exists())
        self.assertFalse(os.path.exists(active_path))
        self.assertTrue(os.path.exists(other_source_path))
        self.assertTrue(os.path.exists(other_archive_path))

    def test_followup_drpp_kw_exact_match_updates_existing_placeholder_and_is_idempotent(self):
        tx = TransactionDetail.objects.create(
            satker_code="1300",
            nomor_spm="00084A",
            tanggal_spm=datetime.date(2026, 4, 6),
            akun="511129",
            cara_pembayaran="LS Non Kontraktual",
            jenis_spm="229 - GAJI LAINNYA",
            no_kuitansi="00084A",
            nilai_bruto=Decimal("33201000"),
            nilai_netto=Decimal("30840900"),
            pembebanan="2886.EBA.994.001.511129",
        )
        DocumentDriveLink.objects.create(
            transaction_detail=tx,
            satker_code="1300",
            nomor_spm="00084A",
            no_kuitansi="00084A",
            jenis_dokumen="SPM",
            nama_file="SPM NOMOR 00084T.pdf",
            google_drive_url="local://spm-00084",
            created_by=self.user,
        )
        ChecklistStatus.objects.create(transaction_detail=tx, nama_dokumen="SPM", status=ChecklistStatus.Status.ADA)
        parsed = self.followup_parsed()
        paket = self.paket_for(parsed, with_file=True, original_filename="DRPP 00084A.zip", nomor_spm="00084A", satker_code="1300")
        decision = build_package_decision(parsed, current_paket_id=paket.id)
        self.assertEqual(decision["commit_action"], "update_existing")
        self.assertEqual(decision["commit_label"], "PERBARUI D_K EXISTING")

        rows = merge_followup_into_existing_dk(parsed, paket, user=self.user, document_status=decision["document_status"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(TransactionDetail.objects.filter(nomor_spm="00084A").count(), 2)
        self.assertEqual(sum(TransactionDetail.objects.values_list("nilai_bruto", flat=True)), Decimal("33201000.00"))
        self.assertTrue(DocumentDriveLink.objects.filter(transaction_detail=rows[0], jenis_dokumen="SPM").exists())
        self.assertTrue(DocumentDriveLink.objects.filter(transaction_detail=rows[1], jenis_dokumen="SPM").exists())
        self.assertTrue(ChecklistStatus.objects.filter(transaction_detail=rows[0], nama_dokumen__icontains="DRPP", status=ChecklistStatus.Status.ADA).exists())
        self.assertTrue(ChecklistStatus.objects.filter(transaction_detail=rows[1], nama_dokumen="KW", status=ChecklistStatus.Status.ADA).exists())

        paket2 = self.paket_for(parsed, with_file=True, original_filename="DRPP 00084A ulang.zip", nomor_spm="00084A", satker_code="1300")
        merge_followup_into_existing_dk(parsed, paket2, user=self.user, document_status=decision["document_status"])
        self.assertEqual(TransactionDetail.objects.filter(nomor_spm="00084A").count(), 2)

    def test_followup_drpp_kw_different_satker_or_year_does_not_auto_link(self):
        TransactionDetail.objects.create(
            satker_code="1301",
            nomor_spm="00084A",
            tanggal_spm=datetime.date(2026, 4, 6),
            akun="511129",
            nilai_bruto=Decimal("33201000"),
        )
        parsed = self.followup_parsed(satker="1300", tahun=2026)
        paket = self.paket_for(parsed, original_filename="DRPP 00084A.zip", nomor_spm="00084A", satker_code="1300")
        self.assertEqual(build_package_decision(parsed, current_paket_id=paket.id)["commit_action"], "review_only")

        TransactionDetail.objects.all().delete()
        TransactionDetail.objects.create(
            satker_code="1300",
            nomor_spm="00084A",
            tanggal_spm=datetime.date(2025, 4, 6),
            akun="511129",
            nilai_bruto=Decimal("33201000"),
        )
        self.assertEqual(build_package_decision(parsed, current_paket_id=paket.id)["commit_action"], "review_only")

    def test_followup_drpp_kw_without_exact_match_is_blocked_from_auto_create(self):
        parsed = self.followup_parsed()
        paket = self.paket_for(parsed, original_filename="DRPP 00084A.zip", nomor_spm="00084A", satker_code="1300")
        decision = build_package_decision(parsed, current_paket_id=paket.id)
        self.assertEqual(decision["commit_action"], "review_only")
        self.assertFalse(decision["can_commit"])
        self.assertEqual(build_transaction_rows_from_package(parsed, paket, self.user, save=False)[0].nomor_spm, "00084A")

    def test_draft_remains_available_after_leaving_preview(self):
        self.client.login(username="operator", password="password")
        parsed = self.parsed_package()
        paket = self.paket_for(parsed)
        response = self.client.get(reverse("paket_spm:drafts"))
        self.assertContains(response, paket.original_filename)

    def test_existing_dk_identity_probe_skips_full_ocr_and_row_builder(self):
        TransactionDetail.objects.create(
            satker_code="1300",
            akun="521211",
            nomor_spm="00999A",
            tanggal_spm=datetime.date(2026, 5, 22),
            no_kuitansi="00001",
            nilai_bruto=Decimal("1000"),
            nilai_netto=Decimal("1000"),
            deskripsi="Existing row 1",
            created_by=self.user,
        )
        TransactionDetail.objects.create(
            satker_code="1300",
            akun="521811",
            nomor_spm="00999A",
            tanggal_spm=datetime.date(2026, 5, 22),
            no_kuitansi="00002",
            nilai_bruto=Decimal("2000"),
            nilai_netto=Decimal("2000"),
            deskripsi="Existing row 2",
            created_by=self.user,
        )
        self.client.login(username="operator", password="password")
        upload = SimpleUploadedFile("SPM NOMOR 00999A.pdf", b"%PDF-1.4\nprobe only\n", content_type="application/pdf")

        with patch("apps.paket_spm.views.parse_spm_pdf", side_effect=AssertionError("full SPM OCR/parser must not run")), \
             patch("apps.paket_spm.views.parse_paket_spm_zip", side_effect=AssertionError("full ZIP parser must not run")), \
             patch("apps.paket_spm.views.build_transaction_rows_from_package", side_effect=AssertionError("row builder must not run for existing D_K")):
            response = self.client.post(
                reverse("paket_spm:list"),
                {"file_paket": upload, "satker_code": "1300", "tahun": "2026"},
                follow=True,
            )
            self.assertEqual(response.status_code, 200)
            paket = PaketSPMUpload.objects.latest("id")
            self.assertEqual(paket.parsed_data["identity_probe"]["exact_transaction_ids"], list(TransactionDetail.objects.order_by("id").values_list("id", flat=True)))
            self.assertContains(response, "Existing row 1")

            commit_response = self.client.post(
                reverse("paket_spm:preview"),
                {
                    "action": "commit",
                    "commit_choice": "link_existing",
                    "matched_transaction_id": paket.parsed_data["identity_probe"]["exact_transaction_ids"][0],
                },
                follow=True,
            )

        self.assertEqual(commit_response.status_code, 200)
        self.assertEqual(TransactionDetail.objects.filter(nomor_spm="00999A").count(), 2)
        self.assertEqual(DocumentDriveLink.objects.filter(nomor_spm="00999A", jenis_dokumen="SPM").count(), 2)

    def test_drpp_parser_registry_and_invariants_do_not_guess_spm_from_drpp(self):
        self.assertIn("DRPP", DOCUMENT_PARSER_REGISTRY)
        pages = [
            (
                "LAMPIRAN DAFTAR RINCIAN PERMINTAAN PEMBAYARAN "
                "Nomor DRPP : 00029/DRPP/019937/2026 Tanggal 01-05-2026 "
                "BUKTI PENGELUARAN "
                "1 00001/KW/019937/2026 PENERIMA SATU 123456789012345 521211 10.000,00 01-05-2026 Belanja ATK "
                "2 00002/KW/019937/2026 PENERIMA DUA 123456789012346 521811 20.000,00 02-05-2026 Belanja konsumsi "
                "JUMLAH SPP INI 30.000,00"
            )
        ]
        with patch("apps.core.parsers.extract_pdf_text", return_value=fake_extract(pages)):
            parsed = parse_drpp_pdf("DRPP NOMOR 00029.pdf", ocr=False)

        self.assertEqual(parsed["metadata"]["nomor_drpp"], "00029/DRPP/019937/2026")
        self.assertEqual(parsed["metadata"]["no_drpp"], "00029")
        self.assertEqual(parsed["metadata"]["satker_code"], "019937")
        self.assertEqual(parsed["metadata"]["tahun"], 2026)
        self.assertEqual(parsed["metadata"]["nomor_spm"], "")
        self.assertEqual(len(parsed["items"]), 2)
        self.assertTrue(all(item["no_drpp"] == "00029/DRPP/019937/2026" for item in parsed["items"]))
        self.assertEqual(sum((item["jumlah"] for item in parsed["items"]), Decimal("0")), Decimal("30000"))
        self.assertTrue(parsed["metadata"]["total_valid"])

    def test_drpp_noisy_ocr_keeps_rows_separate_and_uses_parent_drpp(self):
        pages = [
            (
                "DAFTAR RINCIAN PERMINTAAN PEMBAYARAN Nomor : 00029/DRPP/019937/2026 "
                "BUKTI PENGELUARAN No Tgl dan No Bukti Nama Penerima dan Keperluan NPWP Akun Jumlah Kotor "
                "1 00206/KW/019937/2026 PT. Sinar Mulia Andalas Infocom 028788826201000 523121 3,175,000 08-05-2026 Pengadaan pemeliharaan Laptop HP Envy "
                "2 00207/KW/019937/2026 CV. Mairo Musafir Abadi 001858539201000 521111 364,000 08-05-2026 Pengadaan AMDK "
                "3 00210/KW/019937/2026 PT. Genta Singgalang Press 011357175201000 5211714 320,000 18-05-2026 Biaya tagihan langganan koran "
                "4 00211/KW/019937/2026 PT. Calmic Indonesia 0015675051056000 522191 2,936,505 18-05-2026 Pengadaan jasa pelayanan hygiene "
                "5 00212/KW/019937/2026 Toko Kaca Empat Saudara 001858539201000 521111 1,900,000 18-05-2026 Pengadaan kebutuhan perkantoran "
                "6 00213/KW/019937/2026 PT. Rentokil Indonesia 0010001790058000 522191 777,000 18-05-2026 Pengadaan jasa pembasmi hama "
                "7 00214/KW/019937/2026 SUPRA PRIMATAMA NUSANTARA 019670248073000 522119 11,482,890 18-05-2026 Belanja langganan Biznet "
                "8 00215/KW/019937/2026 A. FAUZI 001858539201000 521252 2,775,000 21-05-2026 Pengadaan Miyako Water Dispenser "
                "9 00216/KW/019937/2026 SMS ELEKTRIK 001858539201000 523121 2,213,200 21-05-2026 Pengadaan pemeliharaan jaringan listrik "
                "10 00217/KW/019937/2026 PDAM 001858539201000 522113 350,937 20-05-2026 Belanja tagihan PDAM "
                "11 00218/KW/019937/2026 PT. Telkom 001858539201000 522119 1,666,390 . 20-05-2026 Biaya langganan indibiz "
                "12 00219/KW/019937/2026 PT. Indonesia Comnets Plus 010611903051000 522119 2,234,500 22-05-2026 Biaya tagihan internet Icon+ "
                "Jumlah Lampiran 12 Jumiah SPP ini : 30,195,422"
            ),
            (
                "LAMPIRAN DAFTAR RINCIAN PERMINTAAN PEMBAYARAN Nomor : 00029/DRPP/019937/2026 "
                "019937.010.521111.05401WA.2886EBA.A000000001.00000.2.0800.2.000000.000000 "
                "994.002.04.000299-Kebutuhan dasar Perkantoran 2.264.000,00 "
                "994.002.0A.000313- Langganan Koran 320.000,00 "
                "019937.010.522119.05401WA.2886EBA.A000000001.00000.2.0800.2.000000.000000 "
                "994.002.0A.000303-Langganan Internet 15.383.780,00 "
            ),
        ]
        with patch("apps.core.parsers.extract_pdf_text", return_value=fake_extract(pages)):
            parsed = parse_drpp_pdf("DRPP NOMOR 00029.pdf", ocr=False)

        self.assertEqual(parsed["metadata"]["nomor_drpp"], "00029/DRPP/019937/2026")
        self.assertEqual(parsed["metadata"]["satker_code"], "019937")
        self.assertEqual(parsed["metadata"]["tahun"], 2026)
        self.assertEqual(parsed["metadata"]["nomor_spm"], "")
        self.assertEqual(len(parsed["items"]), 12)
        self.assertEqual(sum((item["jumlah"] for item in parsed["items"]), Decimal("0")), Decimal("30195422"))
        self.assertTrue(parsed["metadata"]["total_valid"])
        self.assertTrue(all(item["no_drpp"] == "00029/DRPP/019937/2026" for item in parsed["items"]))
        self.assertEqual(parsed["items"][2]["akun"], "521111")
        self.assertEqual(parsed["items"][10]["jumlah"], Decimal("1666390"))

    def test_kw_standalone_is_review_only_and_cannot_create_dk(self):
        parsed = make_json_safe({
            "ok": False,
            "files": [{"file_name": "KW.pdf", "type": "KW"}],
            "spm": None,
            "drpp": None,
            "drpps": [],
            "kw_items": [],
            "warnings": ["KW/Bukti wajib diunggah bersama DRPP."],
        })
        paket = self.paket_for(parsed, original_filename="KW.pdf", nomor_spm="", satker_code="1300")

        decision = build_package_decision(parsed, current_paket_id=paket.id)

        self.assertFalse(decision["can_commit"])
        self.assertEqual(decision["commit_action"], "review_only")
        with self.assertRaisesMessage(ValueError, "KW/Bukti tunggal wajib diunggah bersama DRPP"):
            build_transaction_rows_from_package(parsed, paket, self.user, save=False)

    def test_spm_parser_v2_failure_blocks_legacy_fallback_create(self):
        spm = self.parse_fixture()
        spm["detail_items"] = []
        spm["metadata"]["detail_parse_summary"] = {
            "source": "PERLU_REVIEW_PARSER_TABEL",
            "rows_before_dedupe": 0,
            "rows_after_dedupe": 0,
            "total": "0",
        }
        parsed = self.parsed_package(spm)
        paket = self.paket_for(parsed)

        decision = build_package_decision(parsed, current_paket_id=paket.id)

        self.assertFalse(decision["can_commit"])
        self.assertEqual(decision["commit_action"], "review_only")
        with self.assertRaisesMessage(ValueError, "Parser tabel v2 belum valid"):
            build_transaction_rows_from_package(parsed, paket, self.user, save=False)

    def test_zip_container_manifest_routes_files_without_parsing_outer_zip_as_spm(self):
        zip_path = os.path.join(self.media_tmp.name, "spm 00135t.zip")
        pdf_names = [
            "SPM NOMOR 00135T.pdf",
            "DRPP 00029.pdf",
            "sub/DRPP 00030.pdf",
            "DRPP 00031.pdf",
            "DRPP 00032.pdf",
        ]
        with zipfile.ZipFile(zip_path, "w") as archive:
            for index, name in enumerate(pdf_names, start=1):
                archive.writestr(name, f"%PDF-1.4\nfixture {index}\n".encode())

        def fake_registry(path, file_name, doc_type, ocr=False):
            if doc_type == "SPM":
                return {
                    "file_name": file_name,
                    "status": "parsed_text",
                    "method": "mock",
                    "warnings": [],
                    "metadata": {"nomor_spm": "00135T", "satker_app_code": "1300", "tanggal_spm": "2026-05-22", "jenis_spm": "GUP", "total_pembayaran": Decimal("46224172")},
                    "detail_items": [],
                    "akun_rows": [],
                }
            number = re.search(r"(\d{5})", file_name).group(1)
            amount = {"00029": Decimal("30195422"), "00030": Decimal("3558750"), "00031": Decimal("11700000"), "00032": Decimal("770000")}[number]
            return {
                "file_name": file_name,
                "status": "parsed_text",
                "method": "mock",
                "warnings": [],
                "metadata": {"nomor_drpp": f"{number}/DRPP/019937/2026", "satker_app_code": "1300", "tahun": 2026, "total": amount},
                "items": [{"no_bukti": f"{number}/KW/019937/2026", "akun": "521811", "jumlah": amount, "no_drpp": f"{number}/DRPP/019937/2026", "keperluan": f"KW {number}", "pembebanan": f"COA-{number}"}],
            }

        with patch("apps.core.parsers.parse_document_with_registry", side_effect=fake_registry), \
             patch("apps.core.parsers.parse_spm_pdf", side_effect=AssertionError("outer ZIP must not enter SPM parser")), \
             patch("apps.core.parsers.extract_pdf_text", side_effect=AssertionError("known ZIP members must not probe outer text")):
            parsed = parse_paket_spm_zip(zip_path, ocr=False)

        self.assertEqual(len([row for row in parsed["files"] if row["status"] == "extracted"]), 5)
        self.assertEqual(sum(1 for row in parsed["files"] if row["type"] == "SPM"), 1)
        self.assertEqual(sum(1 for row in parsed["files"] if row["type"] == "DRPP"), 4)
        self.assertEqual(len(parsed["kw_items"]), 4)
        self.assertTrue(all(row.get("relative_path") for row in parsed["files"]))
        self.assertTrue(all(row.get("sha256") for row in parsed["files"]))

    def test_zip_manifest_marks_duplicate_pdf_by_hash_without_parsing_duplicate(self):
        zip_path = os.path.join(self.media_tmp.name, "paket_duplikat.zip")
        pdf_bytes = b"%PDF-1.4\nsame-file\n"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("SPM.pdf", pdf_bytes)
            archive.writestr("sub/SPM copy.pdf", pdf_bytes)

        calls = []

        def fake_registry(path, file_name, doc_type, ocr=False):
            calls.append(file_name)
            return {
                "file_name": file_name,
                "status": "parsed_text",
                "method": "mock",
                "warnings": [],
                "metadata": {"nomor_spm": "00001T", "satker_app_code": "1300", "tanggal_spm": "2026-01-02", "jenis_spm": "LS", "total_pembayaran": Decimal("1")},
                "detail_items": [],
                "akun_rows": [],
            }

        with patch("apps.core.parsers.parse_document_with_registry", side_effect=fake_registry), \
             patch("apps.core.parsers.extract_pdf_text", return_value={"method": "text", "warnings": [], "pages": ["SURAT PERINTAH MEMBAYAR"], "page_details": []}):
            parsed = parse_paket_spm_zip(zip_path, ocr=False)

        duplicates = [row for row in parsed["files"] if row["status"] == "duplicate"]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0]["duplicate_of"], "SPM.pdf")
        self.assertEqual(calls, ["SPM.pdf"])

    def test_classifier_prioritizes_document_anchors_over_support_keywords(self):
        self.assertEqual(
            classify_document(
                "",
                "SURAT PERINTAH MEMBAYAR\nLampiran faktur pajak tersedia sebagai dokumen pendukung.",
            ),
            "SPM",
        )
        self.assertEqual(
            classify_document(
                "",
                "LAMPIRAN DAFTAR RINCIAN PERMINTAAN PEMBAYARAN\nRo.Komp.Subkomp.Item - Uraian",
            ),
            "LAMPIRAN_COA",
        )

    def test_spm_scan_without_native_text_auto_calls_ocr(self):
        ocr_text = (
            "SURAT PERINTAH MEMBAYAR\n"
            "NOMOR 00140T\n"
            "TANGGAL 01 Juni 2026\n"
            "SATKER 019937\n"
            "JUMLAH PENGELUARAN 1.000,00\n"
            "TOTAL PEMBAYARAN 1.000,00\n"
        )
        native_empty = {
            "method": "text",
            "best_engine": "text",
            "status": "failed",
            "pages": [],
            "combined_text": "",
            "page_details": [],
            "warnings": [],
            "page_count": 1,
            "confidence": 0,
            "engines_tried": ["text"],
            "native_text_length": 0,
            "tesseract_called": False,
            "tesseract_text_length": 0,
            "tesseract_reason": "Tesseract tidak dipanggil.",
        }
        ocr_result = {
            "method": "tesseract",
            "best_engine": "tesseract",
            "status": "parsed_ocr",
            "pages": [ocr_text],
            "combined_text": ocr_text,
            "page_details": [{
                "text": ocr_text,
                "extracted_text": ocr_text,
                "page_number": 1,
                "method": "tesseract",
                "engine": "tesseract",
            }],
            "warnings": [],
            "page_count": 1,
            "confidence": 75,
            "engines_tried": ["text", "tesseract"],
            "native_text_length": 0,
            "tesseract_called": True,
            "tesseract_text_length": len(ocr_text),
            "tesseract_reason": "Native text kosong; Tesseract dipanggil.",
        }

        with patch("apps.core.parsers.extract_pdf_text", side_effect=[native_empty, ocr_result]) as mocked:
            parsed = parse_spm_pdf("SPM NOMOR 00140T.pdf", ocr=False)

        self.assertEqual(mocked.call_count, 2)
        self.assertFalse(mocked.call_args_list[0].kwargs["ocr"])
        self.assertTrue(mocked.call_args_list[1].kwargs["ocr"])
        self.assertTrue(parsed["tesseract_called"])
        self.assertEqual(parsed["metadata"]["nomor_spm"], "00140T")

    def test_tesseract_adaptive_rotation_stops_after_strong_anchor(self):
        class FakeImage:
            def __init__(self, name, rotation=0):
                self.name = name
                self.rotation = rotation

            def rotate(self, rotation, expand=True):
                return FakeImage(self.name, rotation)

        attempts = []

        def fake_tesseract_page_text(_pytesseract, image):
            attempts.append((image.name, image.rotation))
            expected_rotation = {"header": 0, "detail": 270}[image.name]
            if image.rotation == expected_rotation:
                text = (
                    "SURAT PERINTAH MEMBAYAR 00100T TOTAL PEMBAYARAN JUMLAH PENGELUARAN POTONGAN KPPN"
                    if image.name == "header"
                    else "DETAIL PENGELUARAN DAN POTONGAN PADA SPP/SPM/SP2D NO SP2D 260100000024403 521211 1.000.000"
                )
                return text, 90.0, [], [{"text": text.split()[0], "confidence": 90, "left": 1, "top": 1, "width": 10, "height": 10}]
            return "noise", 20.0, [], [{"text": "noise", "confidence": 20, "left": 1, "top": 1, "width": 10, "height": 10}]

        with patch("apps.core.ocr.optional_import", return_value=object()), \
             patch("apps.core.ocr.shutil.which", return_value="tesseract"), \
             patch("apps.core.ocr.preprocess_image", side_effect=lambda image: image), \
             patch("apps.core.ocr.tesseract_page_text", side_effect=fake_tesseract_page_text), \
             patch.dict("os.environ", {"OCR_ROTATION_STRONG_SCORE": "12"}):
            result = extract_tesseract("dummy.pdf", images=[FakeImage("header"), FakeImage("detail")])

        self.assertEqual([page.rotation for page in result.pages], [0, 270])
        self.assertEqual([page.tsv_words[0]["rotation"] for page in result.pages], [0, 270])
        self.assertEqual([page.tried_rotations for page in result.pages], [[0], [0, 90, 180, 270]])
        self.assertEqual(attempts, [
            ("header", 0),
            ("detail", 0),
            ("detail", 90),
            ("detail", 180),
            ("detail", 270),
        ])
        self.assertIn("SURAT PERINTAH MEMBAYAR", result.pages[0].extracted_text)
        self.assertIn("DETAIL PENGELUARAN", result.pages[1].extracted_text)

    def test_adaptive_rotation_21_upright_pages_does_not_make_84_calls(self):
        class FakeImage:
            rotation = 0

            def rotate(self, rotation, expand=True):
                clone = FakeImage()
                clone.rotation = rotation
                return clone

        calls = []

        def fake_tesseract_page_text(_pytesseract, image):
            calls.append(image.rotation)
            text = "SURAT PERINTAH MEMBAYAR 00100T TOTAL PEMBAYARAN JUMLAH PENGELUARAN POTONGAN KPPN"
            return text, 90.0, [], [{"text": "SURAT", "confidence": 90, "left": 1, "top": 1, "width": 10, "height": 10}]

        with patch("apps.core.ocr.optional_import", return_value=object()), \
             patch("apps.core.ocr.shutil.which", return_value="tesseract"), \
             patch("apps.core.ocr.preprocess_image", side_effect=lambda image: image), \
             patch("apps.core.ocr.tesseract_page_text", side_effect=fake_tesseract_page_text), \
             patch.dict("os.environ", {"OCR_ROTATION_STRONG_SCORE": "12"}):
            result = extract_tesseract("dummy.pdf", images=[FakeImage() for _ in range(21)])

        self.assertEqual(len(result.pages), 21)
        self.assertEqual(len(calls), 21)
        self.assertEqual(set(calls), {0})
        self.assertTrue(all(page.high_res_ocr_called is False for page in result.pages))

    def test_detail_parser_uses_selected_rotation_without_retrying_all_rotations(self):
        calls = []

        def fake_variants(_file_path, _page_number, rotations):
            calls.append(tuple(rotations))
            return []

        page_details = [{
            "page_number": 3,
            "page_types": ["DETAIL_SPP_SPM_SP2D"],
            "confidence": 95,
            "rotation": 270,
        }]

        with patch("apps.core.parsers.ocr_page_table_variants", side_effect=fake_variants):
            rows, summary = parse_position_detail_items("dummy.pdf", page_details, expected_total=Decimal("1000"))

        self.assertEqual(rows, [])
        self.assertEqual(calls, [(270,)])
        self.assertEqual(summary["source"], "PERLU_REVIEW_PARSER_TABEL")

    def test_classifier_prefers_detail_anchor_over_sp2d_token_and_keeps_multilabel(self):
        text = "DETAIL PENGELUARAN DAN POTONGAN PADA SPP/SPM/SP2D No. SP2D 260100000024403"

        self.assertEqual(classify_page(text), "DETAIL_SPP_SPM_SP2D")
        self.assertEqual(classify_page_types(text)[0], "DETAIL_SPP_SPM_SP2D")
        self.assertIn("SP2D", classify_page_types(text))

    def test_classifier_ssp_is_not_kw_from_payment_words(self):
        text = "SURAT SETORAN PAJAK Kode Akun Pajak Kode Jenis Setoran Masa Pajak Bukti pembayaran"

        self.assertEqual(classify_page(text), "SSP")
        page_types = classify_page_types(text)
        self.assertIn("SSP", page_types)
        self.assertNotIn("KW", page_types)

    def test_ocr_cache_key_uses_file_content_not_temp_filename(self):
        left = os.path.join(self.media_tmp.name, "SPM NOMOR 001.pdf")
        right = os.path.join(self.media_tmp.name, "SPM NOMOR 001_copy.pdf")
        with open(left, "wb") as handle:
            handle.write(b"same pdf content")
        with open(right, "wb") as handle:
            handle.write(b"same pdf content")

        self.assertEqual(ocr_cache_key(left), ocr_cache_key(right))

    def test_detail_parser_uses_existing_tsv_without_reocr_and_rejects_withholding_as_row(self):
        words = [
            {"text": "DETAIL", "left": 10, "top": 10, "width": 80, "height": 10, "confidence": 90},
            {"text": "PENGELUARAN", "left": 100, "top": 10, "width": 120, "height": 10, "confidence": 90},
            {"text": "019937.010.521211.0540ABC.001.002.AA.000001", "left": 100, "top": 100, "width": 850, "height": 20, "confidence": 90},
            {"text": "1.000.000", "left": 2400, "top": 100, "width": 120, "height": 20, "confidence": 90},
            {"text": "019937.010.411121.0540ABC.001.002.AA.000002", "left": 100, "top": 140, "width": 850, "height": 20, "confidence": 90},
            {"text": "100.000", "left": 2400, "top": 140, "width": 120, "height": 20, "confidence": 90},
        ]
        page_details = [{
            "page_number": 1,
            "page_types": ["DETAIL_SPP_SPM_SP2D", "SP2D"],
            "primary_page_type": "DETAIL_SPP_SPM_SP2D",
            "rotation": 270,
            "tsv_words": words,
        }]

        with patch("apps.core.parsers.ocr_page_table_variants") as mocked_ocr_variants, \
             patch("apps.core.parsers.parse_detail_sp2d_rows_by_crop") as mocked_crop:
            rows, summary = parse_position_detail_items("dummy.pdf", page_details, expected_total=Decimal("1000000"))

        mocked_ocr_variants.assert_not_called()
        mocked_crop.assert_not_called()
        self.assertEqual(summary["source"], "DETAIL_SPP_SPM_SP2D")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["akun"], "521211")
        self.assertEqual(rows[0]["jumlah"], Decimal("1000000"))
        self.assertEqual(rows[0]["ocr_rotation"], 270)

    def test_upload_route_reaches_preview_with_mocked_parser(self):
        self.client.login(username="operator", password="password")
        parsed_spm = {
            "status": "needs_manual_review",
            "method": "mock",
            "warnings": [],
            "metadata": {
                "nomor_spm": "00999T",
                "satker_app_code": "1300",
                "tahun": 2026,
                "jenis_spm": "GUP",
                "total_pembayaran": Decimal("1000"),
            },
            "akun_rows": [],
            "detail_items": [],
        }
        with patch("apps.paket_spm.views.probe_package_identity", return_value={"needs_review": False}), \
             patch("apps.paket_spm.views.extract_pdf_text", return_value={"pages": ["SURAT PERINTAH MEMBAYAR"], "page_details": []}), \
             patch("apps.paket_spm.views.classify_document", return_value="SPM"), \
             patch("apps.paket_spm.views.parse_spm_pdf", return_value=parsed_spm):
            response = self.client.post(
                reverse("paket_spm:list"),
                {"document_files": [SimpleUploadedFile("SPM NOMOR 00999T.pdf", b"%PDF-mock", content_type="application/pdf")]},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("paket_spm:preview"))
        paket = PaketSPMUpload.objects.latest("id")
        self.assertEqual(paket.status, PaketSPMUpload.Status.PREVIEW)
        self.assertEqual(self.client.session["paket_spm_preview_id"], paket.id)

    def test_upload_form_initial_context_fields_are_detected_in_preview_not_visible_inputs(self):
        self.client.login(username="operator", password="password")

        response = self.client.get(reverse("paket_spm:list"))

        self.assertNotContains(response, 'name="satker_code"')
        self.assertNotContains(response, 'name="tahun"')
        self.assertNotContains(response, 'name="bulan"')
        self.assertContains(response, "Satker, tahun, dan bulan dideteksi otomatis")

    def test_nested_zip_is_rejected_before_document_parser(self):
        zip_path = os.path.join(self.media_tmp.name, "outer.zip")
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("nested.zip", b"PK nested")
        with self.assertRaisesMessage(ValueError, "Nested ZIP tidak didukung"):
            parse_paket_spm_zip(zip_path, ocr=False)

    def test_drpp_kw_matching_finds_existing_dk_without_spm_date_validation(self):
        tx = TransactionDetail.objects.create(
            satker_code="1300",
            nomor_spm="00135T",
            tanggal_spm=datetime.date(2026, 5, 22),
            akun="521811",
            no_kuitansi="00209",
            no_drpp="00030/DRPP/019937/2026",
            nilai_bruto=Decimal("3558750"),
            nilai_netto=Decimal("3558750"),
            pembebanan="COA-521811",
            created_by=self.user,
        )
        parsed = make_json_safe({
            "ok": True,
            "files": [{"file_name": "DRPP 00030.pdf", "type": "DRPP"}],
            "spm": None,
            "drpp": None,
            "drpps": [{
                "file_name": "DRPP 00030.pdf",
                "status": "parsed_text",
                "metadata": {"nomor_drpp": "00030/DRPP/019937/2026", "satker_app_code": "1300", "tahun": 2026, "nomor_spm": "", "total": "3558750"},
                "items": [],
            }],
            "kw_items": [{"no_bukti": "00209/KW/019937/2026", "akun": "521811", "jumlah": "3558750", "pembebanan": "COA-521811", "no_drpp": "00030/DRPP/019937/2026"}],
        })
        paket = self.paket_for(parsed, original_filename="DRPP 00030.pdf", nomor_spm="", satker_code="1300", tanggal_spm=None)

        decision = build_package_decision(parsed, current_paket_id=paket.id)

        self.assertEqual(decision["commit_action"], "update_existing")
        self.assertEqual(decision["matched_transaction"]["id"], tx.id)
        self.assertEqual(parsed["drpps"][0]["metadata"]["nomor_spm"], "00135T")
        self.assertNotIn("Tanggal SPM belum valid", " ".join(decision.get("notes", [])))

    def test_preview_uses_existing_dk_rows_instead_of_aggregate_spm_rows(self):
        parsed = make_json_safe({
            "ok": True,
            "files": [{"file_name": "SPM NOMOR 00135T.pdf", "type": "SPM"}],
            "spm": {
                "file_name": "SPM NOMOR 00135T.pdf",
                "status": "parsed_text",
                "metadata": {"nomor_spm": "00135T", "satker_app_code": "1300", "tanggal_spm": "2026-05-22", "jenis_spm": "GUP", "total_pembayaran": "46224172"},
                "detail_items": [{"akun": "521111", "jumlah": "46224172"}] * 12,
            },
            "drpps": [],
            "kw_items": [],
        })
        for index in range(15):
            TransactionDetail.objects.create(
                satker_code="1300",
                nomor_spm="00135T",
                tanggal_spm=datetime.date(2026, 5, 22),
                akun="521811",
                no_kuitansi=f"{index + 1:05d}",
                nilai_bruto=Decimal("1000"),
                nilai_netto=Decimal("1000"),
                created_by=self.user,
            )
        paket = self.paket_for(parsed, original_filename="SPM NOMOR 00135T.pdf", nomor_spm="00135T", satker_code="1300", tanggal_spm=datetime.date(2026, 5, 22))
        self.client.login(username="operator", password="password")
        session = self.client.session
        session["paket_spm_preview_id"] = paket.id
        session.save()

        response = self.client.get(reverse("paket_spm:preview"))

        self.assertEqual(len(response.context["transaction_rows"]), 15)
        self.assertNotContains(response, "SIMPAN KE D_K</button>")
        self.assertContains(response, "KAITKAN KE D_K EXISTING")

    def test_drpp_parent_and_kw_item_edits_persist_to_parsed_data(self):
        parsed = make_json_safe({
            "ok": True,
            "files": [{"file_name": "DRPP 00030.pdf", "type": "DRPP"}],
            "spm": None,
            "drpps": [{
                "file_name": "DRPP 00030.pdf",
                "status": "parsed_text",
                "metadata": {"nomor_drpp": "00030/DRPP/019937/2026", "satker_app_code": "1300", "tahun": 2026, "tanggal_drpp": "", "nomor_spm": ""},
                "items": [{"no_bukti": "00209/KW/019937/2026", "akun": "521811", "jumlah": "3558750", "no_drpp": "00030/DRPP/019937/2026", "keperluan": "lama"}],
            }],
            "kw_items": [{"no_bukti": "00209/KW/019937/2026", "akun": "521811", "jumlah": "3558750", "no_drpp": "00030/DRPP/019937/2026", "keperluan": "lama"}],
        })
        paket = self.paket_for(parsed, original_filename="DRPP 00030.pdf", satker_code="1300", tanggal_spm=None)
        self.client.login(username="operator", password="password")
        session = self.client.session
        session["paket_spm_preview_id"] = paket.id
        session.save()

        response = self.client.post(reverse("paket_spm:preview"), {
            "action": "recalculate",
            "preview_row_count": "0",
            "drpp_row_count": "1",
            "drpp-0-nomor_drpp": "00030/DRPP/019937/2026",
            "drpp-0-satker": "1300",
            "drpp-0-tahun": "2026",
            "drpp-0-tanggal_drpp": "2026-05-22",
            "drpp-0-nomor_spm": "00135T",
            "kw_row_count": "1",
            "kw-0-no_drpp": "00030/DRPP/019937/2026",
            "kw-0-no_bukti": "00209/KW/019937/2026",
            "kw-0-tanggal_bukti": "2026-05-18",
            "kw-0-penerima": "CV Lorena Store",
            "kw-0-npwp": "413616673325000",
            "kw-0-akun": "521811",
            "kw-0-jumlah": "3.558.750",
            "kw-0-keperluan": "Pengadaan plakat",
            "kw-0-pembebanan": "COA-521811",
        })

        self.assertEqual(response.status_code, 302)
        paket.refresh_from_db()
        self.assertEqual(paket.parsed_data["drpps"][0]["metadata"]["nomor_spm"], "00135T")
        self.assertEqual(paket.parsed_data["drpps"][0]["metadata"]["tanggal_drpp"], "2026-05-22")
        self.assertEqual(paket.parsed_data["kw_items"][0]["keperluan"], "Pengadaan plakat")
        self.assertEqual(paket.parsed_data["kw_items"][0]["pembebanan"], "COA-521811")

    def test_drpp_tsv_cell_parser_handles_multiline_noise_headers_totals_and_review(self):
        def word(text, left, top, width=None, confidence=92):
            return {
                "text": text,
                "left": left,
                "top": top,
                "width": width or max(18, len(text) * 7),
                "height": 10,
                "confidence": confidence,
            }

        words = [
            word("No.", 20, 20),
            word("Tgl", 80, 20),
            word("dan", 118, 20),
            word("No", 150, 20),
            word("Bukti", 178, 20),
            word("Nama", 250, 20),
            word("Penerima", 292, 20),
            word("dan", 365, 20),
            word("Keperluan", 395, 20),
            word("NPWP", 560, 20),
            word("Akun", 690, 20),
            word("Jumlah", 790, 20),
            word("Kotor", 846, 20),
            word("luar-kiri", -120, 50),
            word("1", 24, 50),
            word("01-05-2026", 80, 50),
            word("001/KW/019937/2026", 80, 64),
            word("CV", 250, 50),
            word("Satu", 275, 50),
            word("Belanja", 250, 64),
            word("peralatan", 310, 64),
            word("kantor", 382, 64),
            word("123456789012345", 560, 50),
            word("521211", 690, 50),
            word("2.000.000,00", 790, 50),
            word("noise-kanan", 1150, 50),
            word("No.", 20, 90),
            word("Tgl", 80, 90),
            word("Nama", 250, 90),
            word("NPWP", 560, 90),
            word("Akun", 690, 90),
            word("Jumlah", 790, 90),
            word("2", 24, 120),
            word("02-05-2026", 80, 120),
            word("002/KW/019937/2026", 80, 134),
            word("PT", 250, 120),
            word("Dua", 276, 120, confidence=31),
            word("Jasa", 250, 134),
            word("internet", 292, 134),
            word("123456789012346", 560, 120),
            word("522119", 690, 120),
            word("1.500.000,00", 790, 120),
            word("3", 24, 160),
            word("03-05-2026", 80, 160),
            word("003/KW/019937/2026", 80, 174),
            word("CV", 250, 160),
            word("Tiga", 275, 160),
            word("Belanja", 250, 174),
            word("air", 310, 174),
            word("123456789012347", 560, 160),
            word("522113", 690, 160),
            word("350.937,00", 790, 160),
            word("Jumlah", 250, 205),
            word("SPP", 310, 205),
            word("INI", 350, 205),
            word("3.850.937,00", 790, 205),
            word("Pejabat", 250, 240),
            word("Pembuat", 305, 240),
        ]

        items = parse_drpp_items_from_tsv(words, page_number=2)

        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["no_bukti"], "001/KW/019937/2026")
        self.assertIn("Belanja peralatan kantor", items[0]["keperluan"])
        self.assertNotIn("luar-kiri", items[0]["keperluan"])
        self.assertNotIn("noise-kanan", items[0]["keperluan"])
        self.assertEqual(items[0]["jumlah"], Decimal("2000000"))
        self.assertTrue(items[1]["needs_review"])
        self.assertIn("nama", items[1]["review_fields"])
        self.assertEqual(items[2]["no_bukti"], "003/KW/019937/2026")
        self.assertEqual(items[2]["akun"], "522113")
        self.assertEqual(items[2]["jumlah"], Decimal("350937"))
        self.assertTrue(all("Jumlah SPP" not in item["keperluan"] for item in items))
        for item in items:
            self.assertEqual(item["method"], "tsv_cell")
            self.assertEqual(item["source_page"], 2)
            self.assertIn("field_meta", item)
            self.assertIn("bounding_box", item)

    def test_detail_sp2d_tsv_uses_amount_cell_crop_not_wide_row_text(self):
        lines = [{
            "text": "019937.010.523121.05401WA.2886EBA.A000000001.00000.2.0800.2.000000.000000.994.002.0A.000242 4.255.956 1.255.956",
            "words": [
                {
                    "text": "019937.010.523121.05401WA.2886EBA.A000000001.00000.2.0800.2.000000.000000.994.002.0A.000242",
                    "left": 1059,
                    "top": 899,
                    "width": 1199,
                    "height": 21,
                },
                {"text": "4.255.956", "left": 2598, "top": 898, "width": 110, "height": 20},
            ],
        }]
        with patch("apps.core.parsers.render_ocr_page_image", return_value=object()), patch(
            "apps.core.parsers.ocr_amount_word_crop",
            return_value=Decimal("1255956"),
        ):
            rows = parse_detail_sp2d_rows_from_tsv_lines(
                "dummy.pdf",
                1,
                90,
                lines,
                ["DETAIL_SPP_SPM_SP2D"],
                {"994.002.0A.000242": "Pemeliharaan kendaraan dinas eselon ii"},
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["akun"], "523121")
        self.assertEqual(rows[0]["jumlah"], Decimal("1255956"))
        self.assertEqual(rows[0]["pembebanan"], "2886.EBA.994.002.523121")

    def test_link_existing_updates_all_group_checklists_idempotently_without_changing_dk(self):
        parsed = make_json_safe({
            "ok": True,
            "files": [
                {"file_name": "SPM 00135T.pdf", "type": "SPM"},
                {"file_name": "DRPP 00030.pdf", "type": "DRPP"},
                {"file_name": "KW 00209.pdf", "type": "KW"},
            ],
            "spm": {
                "file_name": "SPM 00135T.pdf",
                "metadata": {
                    "nomor_spm": "00135T",
                    "satker_app_code": "1300",
                    "tanggal_spm": "2026-05-22",
                    "jenis_spm": "GUP",
                },
            },
            "drpps": [{
                "file_name": "DRPP 00030.pdf",
                "metadata": {"nomor_drpp": "00030/DRPP/019937/2026", "nomor_spm": "00135T", "satker_app_code": "1300", "tahun": 2026},
                "items": [{"no_bukti": "00209/KW/019937/2026", "akun": "521811", "jumlah": "1000"}],
            }],
            "kw_items": [{"no_bukti": "00209/KW/019937/2026", "akun": "521811", "jumlah": "1000", "no_drpp": "00030/DRPP/019937/2026"}],
        })
        transactions = []
        for index in range(15):
            transactions.append(TransactionDetail.objects.create(
                satker_code="1300",
                akun="521811",
                kategori="Belanja Barang",
                bulan_sp2d=5,
                cara_pembayaran="UP/TUP",
                nomor_spm="00135T",
                tanggal_spm=datetime.date(2026, 5, 22),
                jenis_spm="GUP",
                no_kuitansi=f"{index + 1:05d}",
                no_drpp="00030/DRPP/019937/2026",
                deskripsi=f"Existing row {index + 1}",
                nilai_bruto=Decimal("1000"),
                nilai_netto=Decimal("1000"),
                pembebanan=f"2886.EBA.994.{index + 1:03d}.521811",
                fp=f"FP-{index + 1:02d}",
                pph21=Decimal("0"),
                status_detail=TransactionDetail.StatusDetail.DRAFT,
                drpp_status=TransactionDetail.DRPPStatus.BELUM_ADA,
                created_by=self.user,
            ))
        fields = [
            "satker_code", "akun", "kategori", "bulan_sp2d", "cara_pembayaran", "nomor_spm",
            "tanggal_spm", "jenis_spm", "no_kuitansi", "no_drpp", "deskripsi", "nilai_bruto",
            "nilai_netto", "pembebanan", "fp", "pph21",
        ]
        before = {
            tx.id: {field: getattr(tx, field) for field in fields}
            for tx in transactions
        }
        paket = self.paket_for(
            parsed,
            with_file=True,
            original_filename="SPM 00135T lengkap.zip",
            nomor_spm="00135T",
            satker_code="1300",
            tanggal_spm=datetime.date(2026, 5, 22),
        )

        first = link_existing_package_documents(paket, transactions, user=self.user, parsed=parsed, document_status="Lengkap")
        first_count = DocumentDriveLink.objects.count()
        second = link_existing_package_documents(paket, transactions, user=self.user, parsed=parsed, document_status="Lengkap")

        self.assertEqual(first["status"], "created")
        self.assertEqual(second["status"], "exists")
        self.assertEqual(first_count, 45)
        self.assertEqual(DocumentDriveLink.objects.count(), first_count)
        self.assertEqual(DocumentDriveLink.objects.filter(jenis_dokumen="SPM").count(), 15)
        self.assertEqual(DocumentDriveLink.objects.filter(jenis_dokumen="DRPP").count(), 15)
        self.assertEqual(DocumentDriveLink.objects.filter(jenis_dokumen="Kuitansi/Bukti Pembayaran").count(), 15)
        for tx in transactions:
            names = set(ChecklistStatus.objects.filter(transaction_detail=tx, status=ChecklistStatus.Status.ADA).values_list("nama_dokumen", flat=True))
            self.assertIn("SPM", names)
            self.assertIn("DRPP", names)
            self.assertIn("Kuitansi/Bukti Pembayaran", names)
            tx.refresh_from_db()
            self.assertEqual({field: getattr(tx, field) for field in fields}, before[tx.id])

    def test_zip_drpp_scan_wires_tesseract_tsv_words_into_parser_and_preview_data(self):
        zip_path = os.path.join(self.media_tmp.name, "spm 00135t.zip")
        pdf_names = [
            "SPM NOMOR 00135T.pdf",
            "DRPP 00029.pdf",
            "DRPP 00030.pdf",
            "DRPP 00031.pdf",
            "DRPP 00032.pdf",
        ]
        with zipfile.ZipFile(zip_path, "w") as archive:
            for name in pdf_names:
                archive.writestr(name, b"%PDF-1.4\nscan fixture\n")

        drpp_numbers = iter(["00029", "00030", "00031", "00032"])

        def tsv_word(text, left, top, width=None, conf=92):
            return text, left, top, width or max(18, len(text) * 7), 10, str(conf)

        def fake_image_to_data(*args, **kwargs):
            number = next(drpp_numbers)
            amount = {
                "00029": "30.195.422,00",
                "00030": "3.558.750,00",
                "00031": "11.700.000,00",
                "00032": "770.000,00",
            }[number]
            kw = {"00029": "00206", "00030": "00209", "00031": "00210", "00032": "00211"}[number]
            words = [
                tsv_word("DAFTAR", 20, 10),
                tsv_word("RINCIAN", 80, 10),
                tsv_word("PERMINTAAN", 150, 10),
                tsv_word("PEMBAYARAN", 250, 10),
                tsv_word("Nomor", 20, 24),
                tsv_word(":", 64, 24),
                tsv_word(f"{number}/DRPP/019937/2026", 80, 24),
                tsv_word("Tanggal", 260, 24),
                tsv_word("01-05-2026", 320, 24),
                tsv_word("No.", 20, 45),
                tsv_word("Tgl", 80, 45),
                tsv_word("dan", 118, 45),
                tsv_word("No", 150, 45),
                tsv_word("Bukti", 178, 45),
                tsv_word("Nama", 250, 45),
                tsv_word("Penerima", 292, 45),
                tsv_word("dan", 365, 45),
                tsv_word("Keperluan", 395, 45),
                tsv_word("NPWP", 560, 45),
                tsv_word("Akun", 690, 45),
                tsv_word("Jumlah", 790, 45),
                tsv_word("Kotor", 846, 45),
                tsv_word("1", 24, 75),
                tsv_word("01-05-2026", 80, 75),
                tsv_word(f"{kw}/KW/019937/2026", 80, 89),
                tsv_word("CV", 250, 75),
                tsv_word("Penerima", 275, 75),
                tsv_word(number, 342, 75),
                tsv_word("Belanja", 250, 89),
                tsv_word("operasional", 310, 89),
                tsv_word("123456789012345", 560, 75),
                tsv_word("521811", 690, 75),
                tsv_word(amount, 790, 75),
                tsv_word("Jumlah", 250, 120),
                tsv_word("SPP", 310, 120),
                tsv_word("INI", 350, 120),
                tsv_word(amount, 790, 120),
            ]
            return {
                "text": [word[0] for word in words],
                "left": [word[1] for word in words],
                "top": [word[2] for word in words],
                "width": [word[3] for word in words],
                "height": [word[4] for word in words],
                "conf": [word[5] for word in words],
            }

        def fake_spm(path, ocr=False):
            return {
                "file_name": os.path.basename(path),
                "status": "needs_manual_review",
                "method": "mock",
                "warnings": ["Nomor badan SPM 00135A dan SPP 00135T perlu resolusi."],
                "metadata": {
                    "nomor_spm": "00135A",
                    "nomor_spp": "00135T",
                    "nomor_spm_final": "",
                    "nomor_spm_review_status": "Perlu Review Nomor",
                    "satker_app_code": "1300",
                    "tanggal_spm": "2026-05-22",
                    "total_pembayaran": Decimal("46224172"),
                },
                "detail_items": [],
                "akun_rows": [],
            }

        with patch.dict(DOCUMENT_PARSER_REGISTRY["SPM"], {"extractor": fake_spm}), \
             patch("apps.core.ocr.render_pdf_pages", return_value=[Image.new("RGB", (100, 100), "white")]), \
             patch("apps.core.ocr.auto_rotate_for_ocr", side_effect=lambda pytesseract, image: image), \
             patch("apps.core.ocr.shutil.which", return_value="C:/Tesseract/tesseract.exe"), \
             patch("pytesseract.image_to_data", side_effect=fake_image_to_data) as image_to_data:
            parsed = parse_paket_spm_zip(zip_path, ocr=False)

        self.assertEqual(image_to_data.call_count, 4)
        self.assertEqual(sum(1 for row in parsed["files"] if row["type"] == "DRPP"), 4)
        self.assertEqual(len(parsed["drpps"]), 4)
        self.assertEqual(len(parsed["kw_items"]), 4)
        self.assertEqual(sum((item["jumlah"] for item in parsed["kw_items"]), Decimal("0")), Decimal("46224172"))
        self.assertEqual(parsed["spm"]["metadata"]["nomor_spm"], "00135A")
        self.assertEqual(parsed["spm"]["metadata"]["nomor_spp"], "00135T")
        for drpp in parsed["drpps"]:
            meta = drpp["metadata"]
            self.assertEqual(meta["satker_code"], "019937")
            self.assertEqual(meta["tahun"], 2026)
            self.assertEqual(meta["tanggal_drpp"], datetime.date(2026, 5, 1))
            self.assertGreater(meta["total"], Decimal("0"))
            self.assertEqual(len(drpp["items"]), 1)
            self.assertGreater(len(drpp["page_details"][0]["tsv_words"]), 0)
            self.assertEqual(drpp["ocr_trace"][0]["tsv_word_count"], len(drpp["page_details"][0]["tsv_words"]))
            self.assertEqual(drpp["ocr_trace"][0]["parser_method"], "tsv_cell")
            self.assertEqual(drpp["ocr_trace"][0]["parsed_item_count"], 1)
            self.assertTrue(drpp["ocr_trace"][0]["ocr_called"])

    def test_drpp_tsv_attempt_failure_does_not_fallback_to_flat_text_items(self):
        extracted = {
            "method": "tesseract",
            "best_engine": "tesseract",
            "status": "parsed_ocr",
            "warnings": [],
            "page_count": 1,
            "pages": [
                "DAFTAR RINCIAN PERMINTAAN PEMBAYARAN Nomor : 00999/DRPP/019937/2026 "
                "001/KW/019937/2026 521811 1.000.000,00 JUMLAH SPP INI 1.000.000,00"
            ],
            "combined_text": "",
            "page_details": [{
                "text": (
                    "DAFTAR RINCIAN PERMINTAAN PEMBAYARAN Nomor : 00999/DRPP/019937/2026 "
                    "001/KW/019937/2026 521811 1.000.000,00 JUMLAH SPP INI 1.000.000,00"
                ),
                "extracted_text": "",
                "page_number": 1,
                "method": "tesseract",
                "engine": "tesseract",
                "confidence": 85,
                "tsv_words": [
                    {"text": "noise", "left": 10, "top": 10, "width": 20, "height": 10, "confidence": 90},
                    {"text": "tanpa", "left": 50, "top": 10, "width": 20, "height": 10, "confidence": 90},
                    {"text": "header", "left": 90, "top": 10, "width": 20, "height": 10, "confidence": 90},
                ],
            }],
            "confidence": 85,
            "engines_tried": ["text", "tesseract"],
            "native_text_length": 0,
            "tesseract_called": True,
            "tesseract_text_length": 130,
            "tesseract_reason": "test",
        }

        with patch("apps.core.parsers.extract_pdf_text", return_value=extracted):
            parsed = parse_drpp_pdf("DRPP 00999.pdf", ocr=True)

        self.assertEqual(parsed["status"], "needs_manual_review")
        self.assertEqual(parsed["items"], [])
        self.assertTrue(any("OCR TSV sudah dicoba" in warning for warning in parsed["warnings"]))
