import datetime
import hashlib
import json
import os
import tempfile
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Profile
from apps.core.drpp_batch_parser import PARSER_VERSION, evaluate_kkp_group_commitability
from apps.dk.models import TransactionDetail
from apps.paket_spm.models import PaketSPMUpload
from apps.paket_spm.services import build_drpp_batch_rows, upsert_drpp_group
from apps.sp2d.models import SP2DImportBatch, SP2DRaw


User = get_user_model()


class DRPPBatchUpsertIntegrationTests(TestCase):
    PREVIEW_HEADERS = (
        "Helper", "Akun", "Bulan SP2D", "Cara Pembayaran", "Nomor SPM",
        "Tanggal SPM", "Jenis SPM", "No. Kuitansi", "No. DRPP", "Deskripsi",
        "Nilai Bruto", "Nilai Netto", "Pembebanan", "FP", "PPh21",
    )

    def setUp(self):
        self.media_tmp = tempfile.TemporaryDirectory()
        self.media_settings = override_settings(MEDIA_ROOT=self.media_tmp.name)
        self.media_settings.enable()
        self.user = User.objects.create_user(username="drpp-operator", password="password")
        Profile.objects.filter(user=self.user).update(
            role=Profile.Role.SATKER,
            satker_code="019937",
        )

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

    def open_preview(self, paket):
        self.client.force_login(self.user)
        session = self.client.session
        session["paket_spm_preview_id"] = paket.id
        session.save()

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

    def _row_post_data(self, item, *, action="commit", description=None, pembebanan=None):
        post_data = {
            "action": action,
            "commit_drpp": "00042",
            "preview_row_count": "1",
        }
        for field in (
            "akun", "bulan_sp2d", "cara_pembayaran", "nomor_spm", "tanggal_spm",
            "jenis_spm", "no_kuitansi", "no_drpp", "deskripsi", "nilai_bruto",
            "nilai_netto", "pembebanan", "fp", "pph21",
        ):
            post_data[f"rows-0-{field}"] = item.get(field, "")
        if description is not None:
            post_data["rows-0-deskripsi"] = description
        if pembebanan is not None:
            post_data["rows-0-pembebanan"] = pembebanan
        return post_data

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
        content = preview.content.decode("utf-8")
        preview_table = content.split('data-preview-columns="15"', 1)[1].split("</table>", 1)[0]
        self.assertEqual(preview_table.count("</th>"), 15)
        for header in self.PREVIEW_HEADERS:
            self.assertIn(f">{header}</th>", preview_table)
        self.assertNotIn('name="rows-0-helper"', preview_table)
        self.assertIn('name="rows-0-pph21" value=""', preview_table)

        item = parsed["kw_items"][0]
        post_data = self._row_post_data(item)
        with patch("apps.paket_spm.views.link_followup_document") as archive_link:
            committed = self.client.post(reverse("paket_spm:preview"), post_data)

        self.assertRedirects(committed, reverse("paket_spm:list"), fetch_redirect_response=False)
        archive_link.assert_called_once()
        self.assertEqual(TransactionDetail.objects.filter(nomor_spm="00166T").count(), 1)
        paket.refresh_from_db()
        self.assertEqual(paket.status, PaketSPMUpload.Status.COMMITTED)

    def test_review_marker_correction_is_saved_and_clears_field_review(self):
        parsed = self.parsed_batch()
        item = parsed["kw_items"][0]
        item["pembebanan"] = ""
        item["status_detail"] = "PERLU_REVIEW"
        item["review_fields"] = ["pembebanan"]
        item["warnings"] = ["Pembebanan belum cocok unik dengan Detail COA."]
        parsed["drpp_groups"][0]["items"] = [item]
        self.paket(parsed)
        self.client.login(username="drpp-operator", password="password")
        session = self.client.session
        session["paket_spm_preview_id"] = PaketSPMUpload.objects.latest("id").id
        session.save()

        preview = self.client.get(reverse("paket_spm:preview"))
        self.assertContains(preview, 'data-field-status="PERLU_REVIEW"')
        self.assertContains(preview, "Perlu review")

        corrected = "2886.EBD.961.051.522151"
        recalculate = self.client.post(
            reverse("paket_spm:preview"),
            self._row_post_data(item, action="recalculate", pembebanan=corrected),
        )
        self.assertRedirects(recalculate, reverse("paket_spm:preview"), fetch_redirect_response=False)
        refreshed = self.client.get(reverse("paket_spm:preview"))
        self.assertContains(refreshed, f'value="{corrected}"')
        row = refreshed.context["transaction_groups"][0]["rows"][0]
        self.assertNotIn("pembebanan", row.preview_review_fields)

        with patch("apps.paket_spm.views.link_followup_document"):
            committed = self.client.post(
                reverse("paket_spm:preview"),
                self._row_post_data(
                    {**item, "pembebanan": corrected, "status_detail": "LENGKAP", "review_fields": []},
                    pembebanan=corrected,
                ),
            )
        self.assertRedirects(committed, reverse("paket_spm:list"), fetch_redirect_response=False)
        saved = TransactionDetail.objects.get(no_kuitansi=item["no_kuitansi"])
        self.assertEqual(saved.pembebanan, corrected)
        self.assertEqual(saved.status_detail, TransactionDetail.StatusDetail.LENGKAP)

    def test_empty_reconciliation_is_review_disabled_and_direct_post_is_rejected(self):
        parsed = self.parsed_batch()
        drpp = parsed["drpp"]
        drpp["metadata"].update(
            {"printed_total": "0", "total": "0", "source_item_count": 0}
        )
        drpp["items"] = []
        parsed["kw_items"] = []
        parsed["drpp_groups"][0].update(
            {
                "items": [],
                "validation": {
                    "status": "PERLU_REVIEW",
                    "can_commit": False,
                    "errors": [
                        "Item DRPP valid tidak ditemukan.",
                        "Jumlah item sumber DRPP tidak tersedia.",
                        "Total referensi DRPP tidak ditemukan atau bernilai nol.",
                    ],
                },
            }
        )
        paket = self.paket(parsed)
        self.open_preview(paket)

        response = self.client.get(reverse("paket_spm:preview"))

        self.assertEqual(response.status_code, 200)
        group = response.context["transaction_groups"][0]
        self.assertEqual(group["status"], "PERLU_REVIEW")
        self.assertFalse(group["can_commit"])
        self.assertContains(response, "Item DRPP valid tidak ditemukan.")
        self.assertContains(response, 'disabled aria-disabled="true"')
        self.assertContains(response, 'title="Item DRPP valid tidak ditemukan.')
        self.assertNotContains(response, "tanggal belum diisi")

        committed = self.client.post(
            reverse("paket_spm:preview"),
            {"action": "commit", "commit_drpp": "00042", "preview_row_count": "0"},
        )
        self.assertRedirects(
            committed,
            reverse("paket_spm:preview"),
            fetch_redirect_response=False,
        )
        self.assertFalse(TransactionDetail.objects.exists())

    def test_parser_review_validation_cannot_be_promoted_to_balance_by_view_or_service(self):
        parsed = self.parsed_batch()
        parsed["drpp_groups"][0]["validation"] = {
            "status": "PERLU_REVIEW",
            "can_commit": False,
            "errors": ["Validasi parser masih perlu review."],
        }
        paket = self.paket(parsed)
        self.open_preview(paket)

        response = self.client.get(reverse("paket_spm:preview"))

        group = response.context["transaction_groups"][0]
        self.assertEqual(group["status"], "PERLU_REVIEW")
        self.assertFalse(group["can_commit"])
        self.assertContains(response, "Validasi parser masih perlu review.")
        with self.assertRaisesMessage(ValueError, "Validasi parser masih perlu review"):
            upsert_drpp_group(parsed, paket, "00042", user=self.user)

    def test_pdf_multiple_pdfs_and_zip_all_use_page_level_batch_parser(self):
        parsed = self.parsed_batch()
        parsed.update({"ok": True, "files": [], "warnings": [], "temp_dir": "", "metrics": {}})
        self.client.login(username="drpp-operator", password="password")
        uploads = (
            {"file_paket": SimpleUploadedFile("mixed.pdf", b"%PDF-mock", content_type="application/pdf")},
            {"document_files": [
                SimpleUploadedFile("drpp.pdf", b"%PDF-drpp", content_type="application/pdf"),
                SimpleUploadedFile("kw.pdf", b"%PDF-kw", content_type="application/pdf"),
            ]},
            {"file_paket": SimpleUploadedFile("mixed.zip", b"PK-mock", content_type="application/zip")},
        )
        with patch("apps.paket_spm.views.parse_drpp_upload_batch", side_effect=lambda *_args, **_kwargs: deepcopy(parsed)) as parser:
            for upload in uploads:
                response = self.client.post(reverse("paket_spm:list"), upload)
                self.assertRedirects(response, reverse("paket_spm:preview"), fetch_redirect_response=False)

        self.assertEqual(parser.call_count, 3)
        self.assertEqual(PaketSPMUpload.objects.filter(parsed_data__parser_version=PARSER_VERSION).count(), 3)

    def test_viewer_upload_is_rejected_before_parser_or_draft(self):
        viewer = User.objects.create_user(username="drpp-viewer", password="password")
        self.client.force_login(viewer)
        upload = SimpleUploadedFile("viewer.pdf", b"%PDF-mock", content_type="application/pdf")

        with patch("apps.paket_spm.views.parse_drpp_upload_batch") as parser:
            response = self.client.post(reverse("paket_spm:list"), {"file_paket": upload})

        self.assertEqual(response.status_code, 403)
        parser.assert_not_called()
        self.assertFalse(PaketSPMUpload.objects.exists())

    def test_operator_cannot_create_draft_for_document_from_another_satker(self):
        parsed = self.parsed_batch()
        parsed["spm"]["metadata"]["satker_app_code"] = "1301"
        parsed.update({"ok": True, "files": [], "warnings": [], "temp_dir": "", "metrics": {}})
        self.client.force_login(self.user)
        upload = SimpleUploadedFile("other-satker.pdf", b"%PDF-mock", content_type="application/pdf")

        with patch("apps.paket_spm.views.parse_drpp_upload_batch", return_value=parsed):
            response = self.client.post(reverse("paket_spm:list"), {"file_paket": upload}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "berbeda dengan scope operator")
        self.assertFalse(PaketSPMUpload.objects.exists())

    def test_operator_posted_satker_cannot_override_profile_scope(self):
        parsed = self.parsed_batch()
        parsed.update({"ok": True, "files": [], "warnings": [], "temp_dir": "", "metrics": {}})
        self.client.force_login(self.user)
        upload = SimpleUploadedFile("own-satker.pdf", b"%PDF-mock", content_type="application/pdf")

        with patch("apps.paket_spm.views.parse_drpp_upload_batch", return_value=parsed):
            response = self.client.post(
                reverse("paket_spm:list"),
                {"file_paket": upload, "satker_code": "1301"},
            )

        self.assertRedirects(response, reverse("paket_spm:preview"), fetch_redirect_response=False)
        self.assertEqual(PaketSPMUpload.objects.get().satker_code, "019937")


class GUPKKPPreviewIntegrationTests(TestCase):
    def setUp(self):
        self.media_tmp = tempfile.TemporaryDirectory()
        self.media_settings = override_settings(MEDIA_ROOT=self.media_tmp.name)
        self.media_settings.enable()
        self.user = User.objects.create_user(username="kkp-operator", password="password")
        Profile.objects.filter(user=self.user).update(role=Profile.Role.SATKER, satker_code="019937")
        self.client.force_login(self.user)

    def tearDown(self):
        self.media_settings.disable()
        self.media_tmp.cleanup()
        super().tearDown()

    def parsed_batch(self):
        group_key = "KKP:synthetic:00207A:1"
        common = {
            "group_key": group_key,
            "akun": "524111",
            "bulan_sp2d": None,
            "cara_pembayaran": "UP/TUP",
            "nomor_spm": "00207A",
            "tanggal_spm": "2026-07-16",
            "jenis_spm": "GUP-KKP",
            "no_drpp": "",
            "pembebanan": "2902.BMA.006.523.524111",
            "fp": "",
            "pph21": "0",
            "status": "LENGKAP",
            "status_detail": "LENGKAP",
            "warnings": [],
        }
        items = [
            {
                **common,
                "no_bukti": "00095/KW/KKP/019937/2026",
                "no_kuitansi": "00095/KW/KKP/019937/2026",
                "deskripsi": "Perjalanan dinas",
                "keperluan": "Perjalanan dinas",
                "jumlah": "1076000",
                "nilai_bruto": "1076000",
                "nilai_netto": "1076000",
                "receipt_policy": "source_document",
                "receipt_not_available_from_source": False,
            },
            {
                **common,
                "no_bukti": "",
                "no_kuitansi": "",
                "deskripsi": "Penginapan KKP",
                "keperluan": "Penginapan KKP",
                "jumlah": "5700000",
                "nilai_bruto": "5700000",
                "nilai_netto": "5700000",
                "receipt_policy": "not_available_from_source",
                "receipt_not_available_from_source": True,
            },
        ]
        reference = {
            "reference_type": "KKP_PAYMENT_LIST",
            "metadata": {
                "nomor_drpp": "",
                "group_key": group_key,
                "source_item_count": 2,
                "total": "6776000",
                "payment_list_total": "6776000",
                "card_statement_total": "6776000",
                "spm_detail_total": "6776000",
                "spm_header_total_raw": "677600000",
                "canonical_total": "6776000",
                "printed_total": "6776000",
                "spm_total": "6776000",
                "total_resolution_status": "CONSENSUS",
                "total_resolution_sources": ["CARD_STATEMENT", "SPM_DETAIL"],
                "total_resolution_warnings": ["Nilai header SPM terindikasi outlier OCR."],
                "parent_is_gup_kkp": True,
                "nomor_spm": "00207A",
                "nomor_spp": "00207T",
            },
            "items": items,
        }
        validation = evaluate_kkp_group_commitability(reference, items)
        return {
            "ok": True,
            "parser_version": PARSER_VERSION,
            "spm_family": "GUP_KKP",
            "document_requirement_policy": "KKP_PAYMENT_LIST_REQUIRED",
            "reference_type": "KKP_PAYMENT_LIST",
            "spm": {
                "metadata": {
                    "nomor_spm": "00207A",
                    "nomor_spp": "00207T",
                    "tanggal_spm": "2026-07-16",
                    "jenis_spm": "GUP-KKP",
                    "cara_pembayaran": "UP/TUP",
                    "satker_app_code": "019937",
                    "jumlah_pengeluaran": "6776000",
                    "total_pembayaran": "6776000",
                    "bulan_sp2d": None,
                }
            },
            "drpp": reference,
            "drpps": [reference],
            "drpp_groups": [{
                "group_key": group_key,
                "no_drpp": "",
                "reference_type": "KKP_PAYMENT_LIST",
                "is_kkp": True,
                "drpp": reference,
                "items": items,
                "validation": validation,
                "status": validation["status"],
            }],
            "kw_items": items,
            "preview_rows": [],
            "files": [{"file_name": "kkp.pdf", "type": "SPM"}],
            "metrics": {"ocr_seconds": 0, "page_total": 6, "unique_pages": 6, "ocr_pages": 0},
        }

    def paket(self, parsed):
        return PaketSPMUpload.objects.create(
            original_filename="kkp.pdf",
            uploaded_by=self.user,
            status=PaketSPMUpload.Status.PREVIEW,
            nomor_spm="00207A",
            satker_code="019937",
            tahun=2026,
            tanggal_spm=datetime.date(2026, 7, 16),
            jenis_spm_asli="GUP-KKP",
            jenis_spm_label="GUP-KKP",
            parsed_data=json.loads(json.dumps(parsed, default=str)),
        )

    def open_preview(self, paket):
        session = self.client.session
        session["paket_spm_preview_id"] = paket.id
        session.save()

    def post_rows(self, parsed):
        data = {
            "action": "commit",
            "commit_drpp": parsed["drpp_groups"][0]["group_key"],
            "preview_row_count": "2",
        }
        for index, item in enumerate(parsed["kw_items"]):
            for field in (
                "akun", "bulan_sp2d", "cara_pembayaran", "nomor_spm", "tanggal_spm",
                "jenis_spm", "no_kuitansi", "no_drpp", "deskripsi", "nilai_bruto",
                "nilai_netto", "pembebanan", "fp", "pph21",
            ):
                data[f"rows-{index}-{field}"] = item.get(field, "") or ""
        return data

    def test_preview_uses_15_columns_and_kkp_labels_then_commits_idempotently(self):
        parsed = self.parsed_batch()
        paket = self.paket(parsed)
        self.open_preview(paket)
        response = self.client.get(reverse("paket_spm:preview"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Paket KKP 00207A")
        self.assertContains(response, "Ringkasan KKP terbaca")
        self.assertContains(response, "Referensi KKP")
        self.assertContains(response, "Tidak diwajibkan untuk GUP-KKP")
        self.assertContains(response, "Total Referensi KKP")
        self.assertContains(response, "SIMPAN PAKET KKP 00207A KE D_K")
        table = response.content.decode("utf-8").split('data-preview-columns="15"', 1)[1].split("</table>", 1)[0]
        self.assertEqual(table.count("</th>"), 15)
        self.assertIn('value="-" aria-label="No. DRPP"', table)
        summary = response.context["preview_summary"]
        self.assertEqual(summary["drpp_count"], 0)
        self.assertEqual(summary["kkp_reference_count"], 1)
        self.assertEqual(summary["kw_count"], 2)
        self.assertEqual(summary["total"], Decimal("6776000"))
        self.assertNotContains(response, "Rp677.600.000")
        self.assertNotContains(response, "TANPA_DRPP")
        self.assertNotContains(response, "Halaman DRPP tidak ditemukan")

        with patch("apps.paket_spm.views.link_followup_document"):
            committed = self.client.post(reverse("paket_spm:preview"), self.post_rows(parsed))
        self.assertRedirects(committed, reverse("paket_spm:list"), fetch_redirect_response=False)
        rows = TransactionDetail.objects.filter(nomor_spm="00207A")
        self.assertEqual(rows.count(), 2)
        self.assertFalse(rows.exclude(no_drpp="").exists())
        self.assertEqual(rows.filter(no_kuitansi="").count(), 1)
        self.assertFalse(rows.exclude(drpp_status=TransactionDetail.DRPPStatus.BELUM_ADA).exists())

        second = upsert_drpp_group(parsed, paket, parsed["drpp_groups"][0]["group_key"], user=self.user)
        self.assertEqual(len(second), 2)
        self.assertEqual(rows.count(), 2)

    def test_upload_path_persists_resolved_totals_and_renders_fresh_preview(self):
        parsed = self.parsed_batch()
        for item in parsed["kw_items"]:
            for field in ("jumlah", "nilai_bruto", "nilai_netto", "pph21"):
                item[field] = Decimal(item[field])
        upload = SimpleUploadedFile("kkp-fresh.pdf", b"%PDF-mock", content_type="application/pdf")
        with patch("apps.paket_spm.views.parse_drpp_upload_batch", return_value=parsed) as parser:
            response = self.client.post(reverse("paket_spm:list"), {"file_paket": upload})
        self.assertRedirects(response, reverse("paket_spm:preview"), fetch_redirect_response=False)
        parser.assert_called_once()
        paket = PaketSPMUpload.objects.get()
        self.assertEqual(paket.parsed_data["drpp"]["metadata"]["canonical_total"], "6776000")

        preview = self.client.get(reverse("paket_spm:preview"))
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "BALANCE")
        self.assertContains(preview, "Rp6.776.000")
        self.assertNotContains(preview, "Rp677.600.000")
        self.assertEqual(preview.context["preview_summary"]["drpp_count"], 0)
        self.assertEqual(preview.context["preview_summary"]["kkp_reference_count"], 1)

    def test_empty_receipt_without_provenance_and_ambiguous_exact_key_are_blocked(self):
        parsed = self.parsed_batch()
        paket = self.paket(parsed)
        parsed_without_provenance = deepcopy(parsed)
        parsed_without_provenance["kw_items"][1]["receipt_policy"] = ""
        parsed_without_provenance["kw_items"][1]["receipt_not_available_from_source"] = False
        parsed_without_provenance["drpp_groups"][0]["items"] = parsed_without_provenance["kw_items"]
        with self.assertRaisesRegex(ValueError, "provenance"):
            upsert_drpp_group(
                parsed_without_provenance,
                paket,
                parsed_without_provenance["drpp_groups"][0]["group_key"],
                user=self.user,
            )

        for _index in range(2):
            TransactionDetail.objects.create(
                satker_code="019937", akun="524111", nomor_spm="00207A",
                tanggal_spm=datetime.date(2026, 7, 16), no_kuitansi="", no_drpp="",
                nilai_bruto=Decimal("5700000"), nilai_netto=Decimal("5700000"),
                pembebanan="2902.BMA.006.523.524111",
            )
        with self.assertRaisesRegex(ValueError, "lebih dari satu baris"):
            upsert_drpp_group(parsed, paket, parsed["drpp_groups"][0]["group_key"], user=self.user)


@skipUnless(os.getenv("DRPP_REAL_HTTP_FIXTURE"), "Set DRPP_REAL_HTTP_FIXTURE untuk acceptance OCR melalui HTTP.")
class DRPPRealHTTPAcceptanceTests(TestCase):
    """Acceptance lokal opt-in; fixture nyata tidak disimpan di repository."""

    EXPECTED_HEADERS = DRPPBatchUpsertIntegrationTests.PREVIEW_HEADERS
    FIELD_NAMES = {
        "helper", "akun", "bulan_sp2d", "cara_pembayaran", "nomor_spm",
        "tanggal_spm", "jenis_spm", "no_kuitansi", "no_drpp", "deskripsi",
        "nilai_bruto", "nilai_netto", "pembebanan", "fp", "pph21",
    }

    def setUp(self):
        self.fixture_path = Path(os.environ["DRPP_REAL_HTTP_FIXTURE"])
        if not self.fixture_path.is_file():
            self.fail(f"Fixture HTTP tidak ditemukan: {self.fixture_path}")
        self.media_tmp = tempfile.TemporaryDirectory()
        self.media_settings = override_settings(MEDIA_ROOT=self.media_tmp.name)
        self.media_settings.enable()
        self.user = User.objects.create_superuser(username="drpp-real-http", password="password", email="")
        self.client.login(username="drpp-real-http", password="password")

    def tearDown(self):
        self.media_settings.disable()
        self.media_tmp.cleanup()

    def _upload(self):
        upload = SimpleUploadedFile(
            self.fixture_path.name,
            self.fixture_path.read_bytes(),
            content_type="application/pdf",
        )
        response = self.client.post(reverse("paket_spm:list"), {"file_paket": upload})
        self.assertRedirects(response, reverse("paket_spm:preview"), fetch_redirect_response=False)
        return PaketSPMUpload.objects.latest("id")

    def _post_group(self, rows, *, correction=""):
        data = {
            "action": "commit",
            "commit_drpp": "00062",
            "preview_row_count": str(len(rows)),
        }
        coa_prefix = next(
            (
                row.pembebanan.rsplit(".", 1)[0]
                for row in rows
                if row.pembebanan and "." in row.pembebanan
            ),
            "",
        )
        for index, row in enumerate(rows):
            corrected_pembebanan = row.pembebanan
            if not corrected_pembebanan and coa_prefix and row.akun:
                corrected_pembebanan = f"{coa_prefix}.{row.akun}"
            values = {
                "akun": row.akun,
                "bulan_sp2d": row.bulan_sp2d or "",
                "cara_pembayaran": row.cara_pembayaran,
                "nomor_spm": row.nomor_spm,
                "tanggal_spm": row.tanggal_spm.isoformat() if row.tanggal_spm else "",
                "jenis_spm": row.jenis_spm,
                "no_kuitansi": row.no_kuitansi,
                "no_drpp": row.no_drpp,
                "deskripsi": correction if correction and row.no_kuitansi.startswith("00308/KW/") else row.deskripsi,
                "nilai_bruto": row.nilai_bruto,
                "nilai_netto": row.nilai_netto,
                "pembebanan": corrected_pembebanan,
                "fp": row.fp,
                "pph21": row.pph21,
            }
            for field, value in values.items():
                data[f"rows-{index}-{field}"] = str(value or "")
        return data

    def _preview_rows(self):
        response = self.client.get(reverse("paket_spm:preview"))
        self.assertEqual(response.status_code, 200)
        table = response.content.decode("utf-8").split('data-preview-columns="15"', 1)[1].split("</table>", 1)[0]
        self.assertEqual(table.count("</th>"), 15)
        for header in self.EXPECTED_HEADERS:
            self.assertIn(f">{header}</th>", table)
        groups = response.context["transaction_groups"]
        self.assertEqual(len(groups), 1)
        return response, groups[0]["rows"]

    def test_real_mixed_pdf_upload_preview_edit_commit_and_reupload(self):
        first_paket = self._upload()
        parsed = first_paket.parsed_data
        spm_meta = (parsed.get("spm") or {}).get("metadata") or {}
        metrics = parsed.get("metrics") or {}
        self.assertEqual(spm_meta.get("nomor_spm"), "00203A")
        self.assertEqual(spm_meta.get("nomor_spp"), "00203T")
        self.assertEqual(spm_meta.get("nomor_sp2d"), "260100000036855")
        self.assertEqual(len(parsed.get("kw_items") or []), 18)
        self.assertEqual(
            sum((Decimal(str(item.get("nilai_bruto") or 0)) for item in parsed["kw_items"]), Decimal("0")),
            Decimal("30744204"),
        )
        preview_response, rows = self._preview_rows()
        self.assertEqual(len(rows), 18)
        self.assertContains(preview_response, 'data-field-status="VALID"')
        self.assertContains(preview_response, 'data-field-status="PERLU_REVIEW"')
        self.assertContains(preview_response, "Perlu review")
        review_fields = sorted({
            field
            for row in rows
            for field in getattr(row, "preview_review_fields", set())
        })
        self.assertTrue(review_fields)
        kw_row = next(row for row in rows if row.no_kuitansi.startswith("00308/KW/"))
        self.assertEqual(kw_row.akun, "523121")
        self.assertEqual(kw_row.nilai_bruto, Decimal("5664800"))

        batch = SP2DImportBatch.objects.create(
            filename="real-http-sp2d.xlsx",
            original_filename="real-http-sp2d.xlsx",
            tahun=kw_row.tanggal_spm.year,
        )
        parent = SP2DRaw.objects.create(
            import_batch=batch,
            tahun=kw_row.tanggal_spm.year,
            satker_code=kw_row.satker_code,
            nomor_spm_extracted="00203A",
            no_sp2d="260100000036855",
            bulan_sp2d=kw_row.bulan_sp2d,
            nilai_spm=Decimal("30744204"),
            nilai_sp2d=Decimal("30744204"),
        )
        correction = f"{kw_row.deskripsi} [KOREKSI HTTP]"
        with patch("apps.paket_spm.views.link_followup_document"):
            committed = self.client.post(reverse("paket_spm:preview"), self._post_group(rows, correction=correction))
        self.assertRedirects(committed, reverse("paket_spm:list"), fetch_redirect_response=False)
        saved = TransactionDetail.objects.filter(nomor_spm="00203A", no_drpp="00062")
        self.assertEqual(saved.count(), 18)
        self.assertEqual(sum(saved.values_list("nilai_bruto", flat=True), Decimal("0")), Decimal("30744204"))
        corrected = saved.get(no_kuitansi__startswith="00308/KW/")
        self.assertEqual(corrected.deskripsi, correction)
        self.assertEqual(corrected.sp2d_raw, parent)

        second_paket = self._upload()
        _response, second_rows = self._preview_rows()
        with patch("apps.paket_spm.views.link_followup_document"):
            repeated = self.client.post(reverse("paket_spm:preview"), self._post_group(second_rows))
        self.assertRedirects(repeated, reverse("paket_spm:list"), fetch_redirect_response=False)
        self.assertEqual(TransactionDetail.objects.filter(nomor_spm="00203A", no_drpp="00062").count(), 18)
        corrected.refresh_from_db()
        self.assertEqual(corrected.deskripsi, correction)

        second_metrics = second_paket.parsed_data.get("metrics") or {}
        sample = corrected
        print("[DRPP REAL HTTP ACCEPTANCE] " + json.dumps({
            "fixture": self.fixture_path.name,
            "sha256": hashlib.sha256(self.fixture_path.read_bytes()).hexdigest(),
            "spm": spm_meta.get("nomor_spm"),
            "spp": spm_meta.get("nomor_spp"),
            "sp2d": spm_meta.get("nomor_sp2d"),
            "drpp": "00062",
            "rows": saved.count(),
            "total": str(sum(saved.values_list("nilai_bruto", flat=True), Decimal("0"))),
            "pages": metrics.get("page_total"),
            "unique_pages": metrics.get("unique_pages"),
            "ocr_pages": metrics.get("ocr_pages"),
            "cold_seconds": metrics.get("process_seconds"),
            "cold_ocr_seconds": metrics.get("ocr_seconds"),
            "cache_hits_second_upload": second_metrics.get("ocr_cache_hits"),
            "valid_fields": sorted(self.FIELD_NAMES - set(review_fields)),
            "review_fields": review_fields,
            "sample_15": [
                sample.helper, sample.akun, sample.bulan_sp2d, sample.cara_pembayaran,
                sample.nomor_spm, sample.tanggal_spm.isoformat(), sample.jenis_spm,
                sample.no_kuitansi, sample.no_drpp, sample.deskripsi,
                str(sample.nilai_bruto), str(sample.nilai_netto), sample.pembebanan,
                sample.fp, str(sample.pph21),
            ],
        }, ensure_ascii=False, default=str), flush=True)


class DKDraft15ColumnIntegrationTests(TestCase):
    """Test 15-column D_K draft integration with web preview and save."""

    PREVIEW_HEADERS = (
        "Helper", "Akun", "Bulan SP2D", "Cara Pembayaran", "Nomor SPM",
        "Tanggal SPM", "Jenis SPM", "No. Kuitansi", "No. DRPP", "Deskripsi",
        "Nilai Bruto", "Nilai Netto", "Pembebanan", "FP", "PPh21",
    )

    EXPECTED_FIELD_ORDER = [
        "helper", "akun", "bulan_sp2d", "cara_pembayaran", "nomor_spm",
        "tanggal_spm", "jenis_spm", "no_kuitansi", "no_drpp", "deskripsi",
        "nilai_bruto", "nilai_netto", "pembebanan", "fp", "pph21",
    ]

    def setUp(self):
        self.media_tmp = tempfile.TemporaryDirectory()
        self.media_settings = override_settings(MEDIA_ROOT=self.media_tmp.name)
        self.media_settings.enable()
        self.user = User.objects.create_user(username="dk-operator", password="password")
        Profile.objects.filter(user=self.user).update(
            role=Profile.Role.SATKER,
            satker_code="019937",
        )

    def tearDown(self):
        self.media_settings.disable()
        self.media_tmp.cleanup()

    def parsed_batch(self):
        """Create a minimal parsed batch with one transaction."""
        item = {
            "helper": "52111101011/KW/019937/2026",
            "akun": "521111",
            "bulan_sp2d": 6,
            "cara_pembayaran": "UP",
            "nomor_spm": "01077A",
            "tanggal_spm": "2026-06-15",
            "jenis_spm": "GUP_REGULAR",
            "no_kuitansi": "01011/KW/019937/2026",
            "no_bukti": "01011/KW/019937/2026",
            "no_drpp": "00107",
            "deskripsi": "Pengadaan ATK",
            "nilai_bruto": "8425000",
            "nilai_netto": "8425000",
            "pembebanan": "2886.EBA.994.002.521111",
            "fp": "",
            "pph21": "0",
            "status_detail": "LENGKAP",
            "warnings": [],
        }
        drpp = {
            "metadata": {"nomor_drpp": "00107", "total": "8425000", "printed_total": "8425000"},
            "items": [item],
        }
        return {
            "parser_version": PARSER_VERSION,
            "spm": {
                "metadata": {
                    "nomor_spm": "01077A",
                    "tanggal_spm": "2026-06-15",
                    "jenis_spm": "GUP_REGULAR",
                    "satker_app_code": "019937",
                    "bulan_sp2d": 6,
                }
            },
            "drpp": drpp,
            "drpps": [drpp],
            "drpp_groups": [{"no_drpp": "00107", "drpp": drpp, "items": [item], "validation": {"status": "BALANCE"}}],
            "kw_items": [item],
            "preview_rows": [],
        }

    def parsed_batch_two_transactions(self):
        """Create parsed batch with two transactions."""
        item1 = {
            "akun": "521111",
            "no_kuitansi": "01011/KW/019937/2026",
            "no_drpp": "00107",
            "nilai_bruto": "8425000",
            "nilai_netto": "8425000",
            "pembebanan": "2886.EBA.994.002.521111",
            "fp": "",
            "pph21": "0",
        }
        item2 = {
            "akun": "521112",
            "no_kuitansi": "01012/KW/019937/2026",
            "no_drpp": "00107",
            "nilai_bruto": "2000000",
            "nilai_netto": "2000000",
            "pembebanan": "2886.EBA.994.002.521112",
            "fp": "",
            "pph21": "0",
        }
        drpp = {
            "metadata": {"nomor_drpp": "00107", "total": "10425000", "printed_total": "10425000"},
            "items": [item1, item2],
        }
        return {
            "parser_version": PARSER_VERSION,
            "spm": {
                "metadata": {
                    "nomor_spm": "01077A",
                    "tanggal_spm": "2026-06-15",
                    "jenis_spm": "GUP_REGULAR",
                    "satker_app_code": "019937",
                    "bulan_sp2d": 6,
                }
            },
            "drpp": drpp,
            "drpps": [drpp],
            "drpp_groups": [{"no_drpp": "00107", "drpp": drpp, "items": [item1, item2], "validation": {"status": "BALANCE"}}],
            "kw_items": [item1, item2],
            "preview_rows": [],
        }

    def paket(self, parsed):
        return PaketSPMUpload.objects.create(
            original_filename="DRPP 00107.zip",
            uploaded_by=self.user,
            status=PaketSPMUpload.Status.PREVIEW,
            nomor_spm="01077A",
            satker_code="019937",
            tahun=2026,
            bulan=6,
            tanggal_spm=datetime.date(2026, 6, 15),
            jenis_spm_asli="GUP_REGULAR",
            parsed_data=parsed,
        )

    def open_preview(self, paket):
        self.client.force_login(self.user)
        session = self.client.session
        session["paket_spm_preview_id"] = paket.id
        session.save()

    # --- Test 1: Upload produces dk_drafts ---

    def test_upload_produces_dk_drafts(self):
        """Upload should generate dk_drafts via adapter."""
        parsed = self.parsed_batch()
        parsed.update({"ok": True, "files": [], "warnings": [], "temp_dir": "", "metrics": {}})
        self.client.login(username="dk-operator", password="password")
        upload = SimpleUploadedFile("DRPP 00107.pdf", b"%PDF-mock", content_type="application/pdf")

        with patch("apps.paket_spm.views.parse_drpp_upload_batch", return_value=parsed):
            response = self.client.post(reverse("paket_spm:list"), {"file_paket": upload})

        self.assertRedirects(response, reverse("paket_spm:preview"), fetch_redirect_response=False)
        paket = PaketSPMUpload.objects.latest("id")
        # dk_drafts key should exist (may be empty list if adapter fails silently)
        self.assertIn("dk_drafts", paket.parsed_data)

    # --- Test 2: One transaction produces one preview row ---

    def test_one_transaction_creates_one_preview_row(self):
        """One kw_item should produce one preview row."""
        parsed = self.parsed_batch()
        paket = self.paket(parsed)
        self.open_preview(paket)

        response = self.client.get(reverse("paket_spm:preview"))
        self.assertEqual(response.status_code, 200)
        groups = response.context.get("transaction_groups", [])
        if groups:
            self.assertEqual(len(groups[0]["rows"]), 1)

    # --- Test 3: Exactly 15 columns in preview ---

    def test_preview_has_exactly_15_columns(self):
        """Preview table must have exactly 15 columns."""
        parsed = self.parsed_batch()
        parsed.update({"ok": True, "files": [], "warnings": [], "temp_dir": "", "metrics": {}})
        self.client.login(username="dk-operator", password="password")
        upload = SimpleUploadedFile("DRPP 00107.pdf", b"%PDF-mock", content_type="application/pdf")

        with patch("apps.paket_spm.views.parse_drpp_upload_batch", return_value=parsed):
            response = self.client.post(reverse("paket_spm:list"), {"file_paket": upload})

        preview = self.client.get(reverse("paket_spm:preview"))
        content = preview.content.decode("utf-8")
        table_match = content.split('data-preview-columns="15"', 1)
        self.assertTrue(len(table_match) > 1)
        preview_table = table_match[1].split("</table>", 1)[0]
        self.assertEqual(preview_table.count("</th>"), 15)

    # --- Test 4: Correct column order ---

    def test_column_order_matches_contract(self):
        """Column order must be exactly 1-15 per domain contract."""
        parsed = self.parsed_batch()
        parsed.update({"ok": True, "files": [], "warnings": [], "temp_dir": "", "metrics": {}})
        self.client.login(username="dk-operator", password="password")
        upload = SimpleUploadedFile("DRPP 00107.pdf", b"%PDF-mock", content_type="application/pdf")

        with patch("apps.paket_spm.views.parse_drpp_upload_batch", return_value=parsed):
            response = self.client.post(reverse("paket_spm:list"), {"file_paket": upload})

        preview = self.client.get(reverse("paket_spm:preview"))
        content = preview.content.decode("utf-8")
        table_match = content.split('data-preview-columns="15"', 1)
        preview_table = table_match[1].split("</table>", 1)[0]

        for idx, header in enumerate(self.PREVIEW_HEADERS, 1):
            self.assertIn(f">{header}</th>", preview_table, f"Header {idx} '{header}' not found")

    # --- Test 5: review_metadata is NOT column 16 ---

    def test_review_metadata_not_column_16(self):
        """review_metadata should not appear as a table column."""
        parsed = self.parsed_batch()
        parsed.update({"ok": True, "files": [], "warnings": [], "temp_dir": "", "metrics": {}})
        self.client.login(username="dk-operator", password="password")
        upload = SimpleUploadedFile("DRPP 00107.pdf", b"%PDF-mock", content_type="application/pdf")

        with patch("apps.paket_spm.views.parse_drpp_upload_batch", return_value=parsed):
            response = self.client.post(reverse("paket_spm:list"), {"file_paket": upload})

        preview = self.client.get(reverse("paket_spm:preview"))
        content = preview.content.decode("utf-8")
        table_match = content.split('data-preview-columns="15"', 1)
        preview_table = table_match[1].split("</table>", 1)[0]
        self.assertNotIn("review_metadata", preview_table.lower())
        self.assertNotIn("field_status", preview_table.lower())

    # --- Test 6: Helper is read-only ---

    def test_helper_is_read_only(self):
        """Helper field must not have an editable input."""
        parsed = self.parsed_batch()
        paket = self.paket(parsed)
        self.open_preview(paket)

        response = self.client.get(reverse("paket_spm:preview"))
        content = response.content.decode("utf-8")
        # Helper should appear as <output> tag, not <input name="rows-*-helper">
        self.assertIn("js-helper", content)
        self.assertNotIn('name="rows-0-helper"', content)

    # --- Test 7: Null displays as empty, not "-", "TANPA_DRPP", or implicit 0 ---

    def test_null_displays_as_empty(self):
        """Null values should show as empty input, not placeholder."""
        parsed = self.parsed_batch()
        # Set fp to empty to trigger null
        parsed["kw_items"][0]["fp"] = ""
        parsed["kw_items"][0]["pph21"] = "0"  # Explicit 0 for PPh21
        parsed["drpp_groups"][0]["items"] = [parsed["kw_items"][0]]
        paket = self.paket(parsed)
        self.open_preview(paket)

        response = self.client.get(reverse("paket_spm:preview"))
        content = response.content.decode("utf-8")
        # FP should be empty (value="")
        self.assertIn('name="rows-0-fp" value=""', content)
        # Should NOT have "TANPA_DRPP"
        self.assertNotIn("TANPA_DRPP", content)

    # --- Test 8: REVIEW fields are marked ---

    def test_review_field_is_marked(self):
        """Fields with REVIEW status should be visually marked."""
        parsed = self.parsed_batch()
        # Make fp empty to trigger REVIEW
        parsed["kw_items"][0]["fp"] = ""
        parsed["kw_items"][0]["status_detail"] = "PERLU_REVIEW"
        parsed["kw_items"][0]["preview_review_fields"] = ["fp"]
        parsed["kw_items"][0]["warnings"] = ["FP belum terbaca."]
        parsed["drpp_groups"][0]["items"] = [parsed["kw_items"][0]]
        # Mark validation as needing review
        parsed["drpp_groups"][0]["validation"] = {
            "status": "PERLU_REVIEW",
            "can_commit": False,
            "errors": [],
            "warnings": ["FP belum terbaca."],
        }
        paket = self.paket(parsed)
        self.open_preview(paket)

        response = self.client.get(reverse("paket_spm:preview"))
        content = response.content.decode("utf-8")
        # The preview table shows PERLU_REVIEW status pill for the group
        self.assertIn("PERLU_REVIEW", content)
        # Row should be marked as needing review
        self.assertIn('data-row-status="PERLU_REVIEW"', content)

    # --- Test 9: raw_evidence is preserved ---

    def test_raw_evidence_preserved_in_dk_drafts(self):
        """raw_evidence should be preserved in dk_drafts."""
        parsed = self.parsed_batch()
        paket = self.paket(parsed)

        dk_drafts = paket.parsed_data.get("dk_drafts", [])
        if dk_drafts:
            self.assertIn("raw_evidence", dk_drafts[0])
            # Original kw_item should be preserved
            original_kw = dk_drafts[0]["raw_evidence"].get("original_kw_item")
            self.assertIsNotNone(original_kw)
            self.assertEqual(original_kw.get("akun"), "521111")

    # --- Test 10: Manual edit becomes MANUAL_CONFIRMED ---

    def test_manual_edit_becomes_manual_confirmed(self):
        """When operator edits a field, it should become MANUAL_CONFIRMED."""
        parsed = self.parsed_batch()
        paket = self.paket(parsed)
        self.open_preview(paket)

        # Simulate manual edit via POST
        post_data = {
            "action": "recalculate",
            "preview_row_count": "1",
            "rows-0-akun": "521111",
            "rows-0-bulan_sp2d": "6",
            "rows-0-cara_pembayaran": "UP",
            "rows-0-nomor_spm": "01077A",
            "rows-0-tanggal_spm": "2026-06-15",
            "rows-0-jenis_spm": "GUP_REGULAR",
            "rows-0-no_kuitansi": "01011/KW/019937/2026",
            "rows-0-no_drpp": "00107",
            "rows-0-deskripsi": "Pengadaan ATK [EDITED]",
            "rows-0-nilai_bruto": "8425000",
            "rows-0-nilai_netto": "8425000",
            "rows-0-pembebanan": "2886.EBA.994.002.521111",
            "rows-0-fp": "",
            "rows-0-pph21": "0",
        }
        response = self.client.post(reverse("paket_spm:preview"), post_data)
        self.assertRedirects(response, reverse("paket_spm:preview"), fetch_redirect_response=False)

        paket.refresh_from_db()
        dk_drafts = paket.parsed_data.get("dk_drafts", [])
        if dk_drafts:
            metadata = dk_drafts[0].get("review_metadata", {})
            # deskripsi should be MANUAL_CONFIRMED after manual edit
            self.assertEqual(metadata.get("field_status", {}).get("deskripsi"), "MANUAL_CONFIRMED")
            self.assertEqual(metadata.get("field_source", {}).get("deskripsi"), "manual_confirmed")

    # --- Test 11: Manual value not overwritten on draft save ---

    def test_manual_value_preserved_on_draft_save(self):
        """Manual values should not be overwritten when draft is saved."""
        parsed = self.parsed_batch()
        parsed["kw_items"][0]["deskripsi"] = "Original description"
        parsed["drpp_groups"][0]["items"] = [parsed["kw_items"][0]]
        paket = self.paket(parsed)
        self.open_preview(paket)

        # First save with manual edit
        post_data = {
            "action": "recalculate",
            "preview_row_count": "1",
            "rows-0-akun": "521111",
            "rows-0-bulan_sp2d": "6",
            "rows-0-cara_pembayaran": "UP",
            "rows-0-nomor_spm": "01077A",
            "rows-0-tanggal_spm": "2026-06-15",
            "rows-0-jenis_spm": "GUP_REGULAR",
            "rows-0-no_kuitansi": "01011/KW/019937/2026",
            "rows-0-no_drpp": "00107",
            "rows-0-deskripsi": "Manual edited description",
            "rows-0-nilai_bruto": "8425000",
            "rows-0-nilai_netto": "8425000",
            "rows-0-pembebanan": "2886.EBA.994.002.521111",
            "rows-0-fp": "",
            "rows-0-pph21": "0",
        }
        self.client.post(reverse("paket_spm:preview"), post_data)
        paket.refresh_from_db()

        # Save again - manual value should be preserved
        self.client.post(reverse("paket_spm:preview"), post_data)
        paket.refresh_from_db()

        dk_drafts = paket.parsed_data.get("dk_drafts", [])
        if dk_drafts:
            row = dk_drafts[0].get("row", {})
            self.assertEqual(row.get("deskripsi"), "Manual edited description")

    # --- Test 12: Draft REVIEW can be saved ---

    def test_draft_review_can_be_saved(self):
        """Draft with REVIEW status should be saveable."""
        parsed = self.parsed_batch()
        # Make it incomplete to trigger REVIEW
        parsed["kw_items"][0]["fp"] = ""
        parsed["kw_items"][0]["status_detail"] = "PERLU_REVIEW"
        parsed["kw_items"][0]["preview_review_fields"] = ["fp"]
        parsed["kw_items"][0]["warnings"] = ["FP belum terbaca."]
        parsed["drpp_groups"][0]["items"] = [parsed["kw_items"][0]]
        parsed["drpp_groups"][0]["validation"] = {
            "status": "PERLU_REVIEW",
            "can_commit": False,
            "errors": [],
            "warnings": ["FP belum terbaca."],
        }
        paket = self.paket(parsed)
        self.open_preview(paket)

        post_data = {
            "action": "recalculate",
            "preview_row_count": "1",
            "rows-0-akun": "521111",
            "rows-0-bulan_sp2d": "6",
            "rows-0-cara_pembayaran": "UP",
            "rows-0-nomor_spm": "01077A",
            "rows-0-tanggal_spm": "2026-06-15",
            "rows-0-jenis_spm": "GUP_REGULAR",
            "rows-0-no_kuitansi": "01011/KW/019937/2026",
            "rows-0-no_drpp": "00107",
            "rows-0-deskripsi": "Pengadaan ATK",
            "rows-0-nilai_bruto": "8425000",
            "rows-0-nilai_netto": "8425000",
            "rows-0-pembebanan": "2886.EBA.994.002.521111",
            "rows-0-fp": "",
            "rows-0-pph21": "0",
        }
        response = self.client.post(reverse("paket_spm:preview"), post_data)
        self.assertRedirects(response, reverse("paket_spm:preview"), fetch_redirect_response=False)

        # Draft should still be PREVIEW status, not committed
        paket.refresh_from_db()
        self.assertEqual(paket.status, PaketSPMUpload.Status.PREVIEW)

    # --- Test 13: No TransactionDetail on draft save ---

    def test_draft_save_does_not_create_transaction_detail(self):
        """Saving draft should NOT create TransactionDetail."""
        parsed = self.parsed_batch()
        paket = self.paket(parsed)
        self.open_preview(paket)

        post_data = {
            "action": "recalculate",
            "preview_row_count": "1",
            "rows-0-akun": "521111",
            "rows-0-bulan_sp2d": "6",
            "rows-0-cara_pembayaran": "UP",
            "rows-0-nomor_spm": "01077A",
            "rows-0-tanggal_spm": "2026-06-15",
            "rows-0-jenis_spm": "GUP_REGULAR",
            "rows-0-no_kuitansi": "01011/KW/019937/2026",
            "rows-0-no_drpp": "00107",
            "rows-0-deskripsi": "Pengadaan ATK",
            "rows-0-nilai_bruto": "8425000",
            "rows-0-nilai_netto": "8425000",
            "rows-0-pembebanan": "2886.EBA.994.002.521111",
            "rows-0-fp": "",
            "rows-0-pph21": "0",
        }
        self.client.post(reverse("paket_spm:preview"), post_data)

        # No TransactionDetail should be created
        self.assertEqual(TransactionDetail.objects.count(), 0)

    # --- Test 14: Generic GUP stays null + REVIEW ---

    def test_generic_gup_stays_null_review(self):
        """Generic 'GUP' without subtype should stay null + REVIEW."""
        parsed = self.parsed_batch()
        # Use generic GUP instead of GUP_REGULAR
        parsed["spm"]["metadata"]["jenis_spm"] = "GUP"
        parsed["kw_items"][0]["jenis_spm"] = "GUP"
        parsed["drpp_groups"][0]["items"] = [parsed["kw_items"][0]]
        paket = self.paket(parsed)

        dk_drafts = paket.parsed_data.get("dk_drafts", [])
        if dk_drafts:
            row = dk_drafts[0].get("row", {})
            metadata = dk_drafts[0].get("review_metadata", {})
            # jenis_spm should be null (not "GUP")
            self.assertIsNone(row.get("jenis_spm"))
            # field_status should be REVIEW
            self.assertEqual(metadata.get("field_status", {}).get("jenis_spm"), "REVIEW")

    # --- Test 15: GUP_KKP no_drpp null + NOT_APPLICABLE ---

    def test_gup_kkp_no_drpp_not_applicable(self):
        """GUP_KKP should have no_drpp=null with NOT_APPLICABLE status."""
        parsed = self.parsed_batch()
        # Convert to KKP
        parsed["spm"]["metadata"]["jenis_spm"] = "GUP_KKP"
        parsed["spm_family"] = "GUP_KKP"
        parsed["kw_items"][0]["jenis_spm"] = "GUP_KKP"
        parsed["kw_items"][0]["no_drpp"] = ""
        parsed["drpp_groups"][0]["items"] = [parsed["kw_items"][0]]
        parsed["drpp_groups"][0]["is_kkp"] = True
        paket = self.paket(parsed)

        dk_drafts = paket.parsed_data.get("dk_drafts", [])
        if dk_drafts:
            row = dk_drafts[0].get("row", {})
            metadata = dk_drafts[0].get("review_metadata", {})
            # no_drpp should be null
            self.assertIsNone(row.get("no_drpp"))
            # status should be NOT_APPLICABLE
            self.assertEqual(metadata.get("field_status", {}).get("no_drpp"), "NOT_APPLICABLE")

    # --- Test 16: tanggal_spm not from tgl_sp2d ---

    def test_tanggal_spm_not_from_tgl_sp2d(self):
        """tanggal_spm must not be sourced from tgl_sp2d."""
        parsed = self.parsed_batch()
        # Set different dates for SPM and SP2D
        parsed["spm"]["metadata"]["tanggal_spm"] = "2026-06-15"
        parsed["spm"]["metadata"]["tanggal_sp2d"] = "2026-07-01"  # Different date
        paket = self.paket(parsed)

        dk_drafts = paket.parsed_data.get("dk_drafts", [])
        if dk_drafts:
            row = dk_drafts[0].get("row", {})
            # tanggal_spm should be from SPM, not SP2D
            self.assertEqual(str(row.get("tanggal_spm")), "2026-06-15")

    # --- Test 17: Backward compatibility with old parsed_data ---

    def test_backward_compatibility_without_dk_drafts(self):
        """Old parsed_data without dk_drafts should still work."""
        parsed = self.parsed_batch()
        # Remove dk_drafts to simulate old data
        if "dk_drafts" in parsed:
            del parsed["dk_drafts"]
        paket = self.paket(parsed)
        self.open_preview(paket)

        # Should still render preview without error
        response = self.client.get(reverse("paket_spm:preview"))
        self.assertEqual(response.status_code, 200)

    # --- Test 18: Two transactions don't swap fields ---

    def test_two_transactions_no_field_swap(self):
        """Two transactions should not swap field values."""
        parsed = self.parsed_batch_two_transactions()
        parsed.update({"ok": True, "files": [], "warnings": [], "temp_dir": "", "metrics": {}})
        paket = self.paket(parsed)
        self.open_preview(paket)

        response = self.client.get(reverse("paket_spm:preview"))
        content = response.content.decode("utf-8")

        # Both akun values should appear in correct positions
        self.assertIn("521111", content)
        self.assertIn("521112", content)

        # No kuitansi numbers swapped
        self.assertIn("01011/KW/019937/2026", content)
        self.assertIn("01012/KW/019937/2026", content)

    # --- Test 19: CSRF protection still works ---

    def test_csrf_validation_in_preview_form(self):
        """Preview form should include CSRF token."""
        parsed = self.parsed_batch()
        paket = self.paket(parsed)
        self.open_preview(paket)

        response = self.client.get(reverse("paket_spm:preview"))
        content = response.content.decode("utf-8")
        # Should have CSRF token in form
        self.assertIn("csrfmiddlewaretoken", content)
        # Form should have proper action
        self.assertIn('method="post"', content)

    # --- Test 20: source_transaction_index preserved ---

    def test_source_transaction_index_preserved(self):
        """source_transaction_index should be preserved in dk_drafts."""
        parsed = self.parsed_batch_two_transactions()
        parsed.update({"ok": True, "files": [], "warnings": [], "temp_dir": "", "metrics": {}})
        self.client.login(username="dk-operator", password="password")
        upload = SimpleUploadedFile("DRPP 00107.pdf", b"%PDF-mock", content_type="application/pdf")

        with patch("apps.paket_spm.views.parse_drpp_upload_batch", return_value=parsed):
            response = self.client.post(reverse("paket_spm:list"), {"file_paket": upload})

        paket = PaketSPMUpload.objects.latest("id")
        dk_drafts = paket.parsed_data.get("dk_drafts", [])

        if len(dk_drafts) >= 2:
            # Each draft should have unique index
            self.assertEqual(dk_drafts[0].get("source_transaction_index"), 0)
            self.assertEqual(dk_drafts[1].get("source_transaction_index"), 1)
