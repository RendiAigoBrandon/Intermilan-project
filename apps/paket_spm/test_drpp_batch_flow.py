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

from apps.core.drpp_batch_parser import PARSER_VERSION
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
