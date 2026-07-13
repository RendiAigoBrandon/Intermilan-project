import datetime
import os
import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.parsers import make_json_safe, parse_spm_pdf
from apps.dk.models import TransactionDetail
from apps.dk.services import refresh_transaction_document_status
from apps.documents.models import ChecklistStatus, DocumentDriveLink
from apps.paket_spm.fixtures_test import FIXTURE_00074T_PAGES
from apps.paket_spm.models import PaketSPMUpload
from apps.paket_spm.services import build_package_decision, build_transaction_rows_from_package, link_paket_spm_source_document


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
        meta = parsed["spm"]["metadata"]
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
            "-",
            "Pembayaran Belanja Barang Berupa Honor PPNPN Bulan Maret Tahun 2026 untuk 3 Pegawai",
            Decimal("9744780.00"),
            Decimal("9646380.00"),
            "2886.EBA.994.002.522191",
            "FP-2026-019937-92000-507",
            Decimal("0"),
        ])

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

    def test_draft_remains_available_after_leaving_preview(self):
        self.client.login(username="operator", password="password")
        parsed = self.parsed_package()
        paket = self.paket_for(parsed)
        response = self.client.get(reverse("paket_spm:drafts"))
        self.assertContains(response, paket.original_filename)
