import datetime
import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.drpp_batch_parser import PARSER_VERSION
from apps.dk.models import TransactionDetail
from apps.paket_spm.models import PaketSPMUpload
from apps.paket_spm.services import build_drpp_batch_rows, upsert_drpp_group
from apps.sp2d.models import SP2DImportBatch, SP2DRaw


User = get_user_model()


class DRPPBatchUpsertIntegrationTests(TestCase):
    def setUp(self):
        self.media_tmp = tempfile.TemporaryDirectory()
        self.media_settings = override_settings(MEDIA_ROOT=self.media_tmp.name)
        self.media_settings.enable()
        self.user = User.objects.create_user(username="drpp-operator", password="password")

    def tearDown(self):
        self.media_settings.disable()
        self.media_tmp.cleanup()

    def parsed_batch(self):
        item = {
            "helper": "52215100243/KW/019937/2026",
            "akun": "522151",
            "bulan_sp2d": 6,
            "cara_pembayaran": "UP/TUP",
            "nomor_spm": "00166T",
            "tanggal_spm": "2026-06-15",
            "jenis_spm": "GUP",
            "no_kuitansi": "00243/KW/019937/2026",
            "no_bukti": "00243/KW/019937/2026",
            "no_drpp": "00042",
            "deskripsi": "Honor Narasumber Rapat Pertemuan Pembinaan PPID 7 Mei 2026",
            "nilai_bruto": "1800000",
            "nilai_netto": "1800000",
            "pembebanan": "2886.EBD.961.051.522151",
            "fp": "",
            "pph21": "0",
            "status_detail": "LENGKAP",
            "warnings": [],
        }
        drpp = {
            "metadata": {"nomor_drpp": "00042", "total": "1800000", "printed_total": "1800000"},
            "items": [item],
        }
        return {
            "parser_version": PARSER_VERSION,
            "spm": {
                "metadata": {
                    "nomor_spm": "00166T",
                    "tanggal_spm": "2026-06-15",
                    "jenis_spm": "GUP",
                    "satker_app_code": "019937",
                    "bulan_sp2d": 6,
                }
            },
            "drpp": drpp,
            "drpps": [drpp],
            "drpp_groups": [{"no_drpp": "00042", "drpp": drpp, "items": [item], "validation": {"status": "BALANCE"}}],
            "kw_items": [item],
            "preview_rows": [],
        }

    def paket(self, parsed):
        return PaketSPMUpload.objects.create(
            original_filename="DRPP 00042.zip",
            uploaded_by=self.user,
            status=PaketSPMUpload.Status.PREVIEW,
            nomor_spm="00166T",
            satker_code="019937",
            tahun=2026,
            bulan=6,
            tanggal_spm=datetime.date(2026, 6, 15),
            jenis_spm_asli="GUP",
            jenis_spm_label="GUP",
            parsed_data=parsed,
        )

    def test_reupload_upserts_exact_key_without_duplicate_and_keeps_suffix(self):
        parsed = self.parsed_batch()
        paket = self.paket(parsed)
        batch = SP2DImportBatch.objects.create(filename="sp2d.xlsx", original_filename="sp2d.xlsx", tahun=2026)
        parent = SP2DRaw.objects.create(
            import_batch=batch,
            satker_code="019937",
            nomor_spm_extracted="00166T",
            nilai_spm=Decimal("1800000"),
            nilai_sp2d=Decimal("1800000"),
        )

        first = upsert_drpp_group(parsed, paket, "00042", user=self.user)
        second = upsert_drpp_group(parsed, paket, "00042", user=self.user)

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].pk, second[0].pk)
        self.assertEqual(TransactionDetail.objects.count(), 1)
        row = TransactionDetail.objects.get()
        self.assertEqual(row.nomor_spm, "00166T")
        self.assertEqual(row.no_kuitansi, "00243/KW/019937/2026")
        self.assertEqual(row.sp2d_raw, parent)
        self.assertEqual(row.pembebanan, "2886.EBD.961.051.522151")

    def test_existing_manual_values_are_not_overwritten(self):
        parsed = self.parsed_batch()
        paket = self.paket(parsed)
        existing = TransactionDetail.objects.create(
            satker_code="019937",
            akun="522151",
            nomor_spm="00166T",
            tanggal_spm=datetime.date(2026, 6, 15),
            no_kuitansi="00243/KW/019937/2026",
            no_drpp="00042",
            deskripsi="Deskripsi manual operator",
            nilai_bruto=Decimal("1800000"),
            nilai_netto=Decimal("1800000"),
            pembebanan="2886.EBD.961.051.522151",
        )
        result = upsert_drpp_group(parsed, paket, "00042", user=self.user)
        existing.refresh_from_db()
        self.assertEqual(result[0].pk, existing.pk)
        self.assertEqual(existing.deskripsi, "Deskripsi manual operator")

    def test_preview_rows_retain_full_kuitansi_number(self):
        parsed = self.parsed_batch()
        row = build_drpp_batch_rows(parsed, self.paket(parsed), self.user)[0]
        self.assertEqual(row.helper, "52215100243/KW/019937/2026")
        self.assertEqual(row.no_kuitansi, "00243/KW/019937/2026")

    def test_upload_route_uses_drpp_batch_parser_and_creates_editable_draft(self):
        parsed = self.parsed_batch()
        parsed.update(
            {
                "ok": True,
                "files": [{"file_name": "DRPP 00042.pdf", "type": "DRPP_SUMMARY"}],
                "warnings": [],
                "temp_dir": "",
                "metrics": {"ocr_seconds": 1, "page_total": 4, "unique_pages": 2, "ocr_pages": 1},
            }
        )
        self.client.login(username="drpp-operator", password="password")
        upload = SimpleUploadedFile("DRPP 00042.pdf", b"%PDF-mock", content_type="application/pdf")
        with patch("apps.paket_spm.views.parse_drpp_upload_batch", return_value=parsed) as parser:
            response = self.client.post(reverse("paket_spm:list"), {"file_paket": upload})

        self.assertRedirects(response, reverse("paket_spm:preview"), fetch_redirect_response=False)
        parser.assert_called_once()
        self.assertTrue(parser.call_args.kwargs["ocr"])
        paket = PaketSPMUpload.objects.latest("id")
        self.assertEqual(paket.parsed_data["parser_version"], PARSER_VERSION)
        self.assertEqual(paket.status, PaketSPMUpload.Status.PREVIEW)

        preview = self.client.get(reverse("paket_spm:preview"))
        self.assertContains(preview, "DRPP 00042")
        self.assertContains(preview, "SIMPAN DRPP 00042 KE D_K")

        item = parsed["kw_items"][0]
        post_data = {
            "action": "commit",
            "commit_drpp": "00042",
            "preview_row_count": "1",
        }
        for field in (
            "akun", "bulan_sp2d", "cara_pembayaran", "nomor_spm", "tanggal_spm",
            "jenis_spm", "no_kuitansi", "no_drpp", "deskripsi", "nilai_bruto",
            "nilai_netto", "pembebanan", "fp", "pph21",
        ):
            post_data[f"rows-0-{field}"] = item.get(field, "")
        with patch("apps.paket_spm.views.link_followup_document") as archive_link:
            committed = self.client.post(reverse("paket_spm:preview"), post_data)

        self.assertRedirects(committed, reverse("paket_spm:list"), fetch_redirect_response=False)
        archive_link.assert_called_once()
        self.assertEqual(TransactionDetail.objects.filter(nomor_spm="00166T").count(), 1)
        paket.refresh_from_db()
        self.assertEqual(paket.status, PaketSPMUpload.Status.COMMITTED)
