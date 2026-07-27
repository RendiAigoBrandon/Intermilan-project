import json
import os
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.core.golden_accuracy import GoldenCorpusMissing, GoldenValidationError, sha256_file
from apps.core.golden_runner import probe_fixture, probe_spm


class GoldenRunnerTests(SimpleTestCase):
    def test_probe_removes_only_cache_created_by_current_run(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            fixture = Path(directory, "fixture.pdf")
            fixture.write_bytes(b"fixture")
            cache_dir = Path(directory, "ocr_cache", "drpp_batch")
            cache_dir.mkdir(parents=True)
            existing = cache_dir / "existing.json"
            existing.write_text("{}", encoding="utf-8")
            created = cache_dir / "created.json"

            def fake_probe(_path, *, ocr):
                created.write_text("{}", encoding="utf-8")
                return {"metrics": {}, "ocr": ocr}

            with patch("apps.core.golden_runner.probe_spm", side_effect=fake_probe):
                result = probe_fixture(fixture, ocr=True)

            self.assertTrue(existing.exists())
            self.assertFalse(created.exists())
            self.assertEqual(len(probe_fixture.last_removed_cache_files), 1)
            self.assertEqual(result["fixture"]["sha256"], sha256_file(fixture))

    def test_spm_probe_exposes_complete_provenance_and_canonical_blank_receipt(self):
        parsed = {
            "file_name": "fixture.pdf",
            "page_count": 1,
            "page_details": [{
                "page_number": 1,
                "page_types": ["SPM", "DETAIL_SPP_SPM_SP2D"],
                "engine": "tesseract",
                "method": "tesseract",
                "confidence": 87.0,
            }],
            "metadata": {
                "nomor_spp": "00001T",
                "nomor_spm": "00001A",
                "nomor_sp2d": "260100000000001",
                "tanggal_spm": date(2026, 1, 2),
                "tanggal_sp2d": date(2026, 1, 3),
                "jenis_spm": "LS",
                "cara_pembayaran": "LS",
                "spm_page_nums": [1],
                "detail_parse_summary": {},
            },
            "detail_items": [{"source_page": 1, "field_provenance": {}}],
            "akun_rows": [],
            "warnings": [],
        }
        row = SimpleNamespace(
            akun="511111", bulan_sp2d=1, cara_pembayaran="LS",
            nomor_spm="00001A", tanggal_spm=date(2026, 1, 2), jenis_spm="LS",
            no_kuitansi="", no_drpp="", deskripsi="Belanja contoh",
            nilai_bruto=Decimal("100"), nilai_netto=Decimal("100"),
            pembebanan="0001.ABC.001.001.511111", fp="", pph21=Decimal("0"),
        )

        with patch("apps.core.golden_runner.parse_spm_pdf", return_value=parsed), patch(
            "apps.core.golden_runner.build_transaction_rows_from_package", return_value=[row]
        ):
            report = probe_spm(Path("fixture.pdf"), ocr=True)

        columns = report["rows"][0]["columns"]
        required = {
            "value", "source", "engine", "extraction_method", "confidence",
            "source_file", "source_page", "document_type", "locator", "inputs",
        }
        self.assertEqual(set(columns), set((
            "helper", "akun", "bulan_sp2d", "cara_pembayaran", "nomor_spm",
            "tanggal_spm", "jenis_spm", "no_kuitansi", "no_drpp", "deskripsi",
            "nilai_bruto", "nilai_netto", "pembebanan", "fp", "pph21",
        )))
        self.assertTrue(required.issubset(columns["akun"]))
        self.assertIsNone(columns["no_kuitansi"]["value"])
        self.assertEqual(columns["helper"]["value"], "511111")
        self.assertIsNone(columns["helper"]["confidence"])
        self.assertEqual(report["actual_layers"]["extraction"]["transaction_count"], 1)
        self.assertEqual(report["actual_layers"]["enrichment"]["transaction_count"], 1)


class GoldenOCRCorpusPolicyTests(SimpleTestCase):
    manifest_path = Path(__file__).resolve().parents[2] / "golden" / "corpus_manifest.json"

    def test_corpus_registry_has_explicit_ci_and_local_policy(self):
        registry = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(registry["corpus_policy"]["local_acceptance"], "required")
        self.assertIn("explicit_skip", registry["corpus_policy"]["ordinary_ci"])
        self.assertEqual(registry["corpus_policy"]["pii_in_reports"], "redacted")

    def test_canonical_missing_fixture_cannot_be_substituted(self):
        registry = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        pending = next(item for item in registry["fixtures"] if item["sha256"] is None)
        self.assertFalse(pending["canonical_substitution_allowed"])
        self.assertIn("missing_canonical", pending["annotation_status"])

    def test_golden_ocr_is_explicitly_skipped_without_external_corpus(self):
        corpus_dir = os.environ.get("GOLDEN_OCR_CORPUS_DIR")
        required = os.environ.get("GOLDEN_OCR_REQUIRED") == "1"
        if not corpus_dir and not required:
            self.skipTest("golden_ocr skipped explicitly: GOLDEN_OCR_CORPUS_DIR is not configured")
        if not corpus_dir:
            raise GoldenCorpusMissing("Local acceptance requires GOLDEN_OCR_CORPUS_DIR.")

        registry = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        for fixture in registry["fixtures"]:
            expected_hash = fixture.get("sha256")
            path = Path(corpus_dir, fixture["filename"])
            if not path.is_file():
                raise GoldenCorpusMissing(f"Fixture wajib tidak tersedia: {path}")
            if not expected_hash:
                raise GoldenValidationError(
                    f"SHA-256 canonical belum dibekukan: {fixture['filename']}"
                )
            actual_hash = sha256_file(path)
            if actual_hash != expected_hash:
                raise GoldenValidationError(
                    f"Hash fixture berubah: {fixture['filename']}; "
                    f"expected={expected_hash}; actual={actual_hash}"
                )
