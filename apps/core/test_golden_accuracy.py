import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from .golden_accuracy import (
    DK_COLUMNS,
    GoldenCorpusMissing,
    GoldenValidationError,
    actual_value,
    build_annotation_draft,
    build_report,
    compare_layer,
    computed_helper,
    resolve_fixture,
    validate_manifest,
)


def expected_columns(**overrides):
    values = {
        "akun": "521111",
        "bulan_sp2d": 6,
        "cara_pembayaran": "LS",
        "nomor_spm": "00999A",
        "tanggal_spm": "2026-06-01",
        "jenis_spm": "LS",
        "no_kuitansi": None,
        "no_drpp": None,
        "deskripsi": "Belanja contoh",
        "nilai_bruto": "1000",
        "nilai_netto": "900",
        "pembebanan": "2886.EBA.994.001.521111",
        "fp": None,
        "pph21": "100",
    }
    values.update(overrides)
    return {
        field: {
            "value": value,
            "availability": "PRESENT" if value is not None else "CONFIRMED_ABSENT",
            "source_file": "synthetic.pdf",
            "source_page": 1,
            "document_type": "SYNTHETIC",
            "locator": field,
            "reason": "Nilai fixture sintetis yang dikontrol oleh test.",
        }
        for field, value in values.items()
    }


def manifest(*transactions):
    return {
        "schema_version": 2,
        "fixture": {
            "id": "synthetic",
            "filename": "synthetic.pdf",
            "sha256": "a" * 64,
            "pipeline": "synthetic",
        },
        "transactions": list(transactions),
    }


def actual_columns(**overrides):
    values = {field: cell["value"] for field, cell in expected_columns().items()}
    values.update(overrides)
    columns = {
        field: actual_value(
            value,
            "PARSER_STRUCTURAL",
            engine="native_pdf",
            extraction_method="synthetic_cell",
            confidence=None,
            source_file="synthetic.pdf",
            source_page=1,
            document_type="SYNTHETIC",
            locator=field,
        )
        for field, value in values.items()
    }
    columns["helper"] = actual_value(
        computed_helper(values["akun"], values["no_kuitansi"]),
        "COMPUTED",
        extraction_method="concatenate",
        inputs=["akun", "no_kuitansi"],
    )
    return columns


class GoldenAccuracyFrameworkTests(SimpleTestCase):
    def transaction(self, row_key="row-1", case_id="case-1", **overrides):
        columns = expected_columns(**overrides)
        candidate = actual_columns(**overrides)
        return {
            "row_key": row_key,
            "case_id": case_id,
            "document_id": "synthetic-document",
            "parser_candidate": {"extraction": candidate, "enrichment": candidate},
            "reviewer_expected": {"extraction": columns, "enrichment": columns},
            "reviewer_status": "APPROVED",
        }

    def test_manifest_rejects_typed_helper(self):
        tx = self.transaction()
        tx["reviewer_expected"]["extraction"]["helper"] = {
            "value": "wrong", "availability": "PRESENT"
        }
        with self.assertRaisesMessage(GoldenValidationError, "Helper tidak boleh diketik"):
            validate_manifest(manifest(tx))

    def test_confidence_is_null_when_method_has_no_valid_confidence(self):
        envelope = actual_value("x", "PARSER_STRUCTURAL", confidence=None)
        self.assertIsNone(envelope["confidence"])
        with self.assertRaisesMessage(GoldenValidationError, "Confidence tanpa engine"):
            actual_value("x", "PARSER_STRUCTURAL", confidence=99)

    def test_ls_receipt_is_canonical_null_and_helper_is_computed(self):
        tx = self.transaction()
        validated = validate_manifest(manifest(tx))
        rows = [{"row_key": "row-1", "columns": actual_columns(no_kuitansi="")}]
        comparison = compare_layer(validated, rows, "enrichment")
        by_field = {item["field"]: item for item in comparison}
        self.assertEqual(by_field["no_kuitansi"]["status"], "EXACT")
        self.assertEqual(by_field["helper"]["actual"], "521111")
        self.assertEqual(by_field["helper"]["provenance"]["source"], "COMPUTED")

    def test_equal_helpers_do_not_merge_distinct_transactions(self):
        tx1 = self.transaction("source-row-1", "case-1")
        tx2 = self.transaction("source-row-2", "case-2")
        validated = validate_manifest(manifest(tx1, tx2))
        rows = [
            {"row_key": "source-row-2", "columns": actual_columns(no_kuitansi="")},
            {"row_key": "source-row-1", "columns": actual_columns(no_kuitansi="")},
        ]
        comparison = compare_layer(validated, rows, "enrichment")
        helper_cells = [item for item in comparison if item["field"] == "helper"]
        self.assertEqual(len(helper_cells), 2)
        self.assertEqual({item["row_key"] for item in helper_cells}, {"source-row-1", "source-row-2"})
        self.assertTrue(all(item["actual"] == "521111" for item in helper_cells))

    def test_report_separates_extraction_from_enrichment_sources(self):
        tx = self.transaction()
        validated = validate_manifest(manifest(tx))
        extraction = [{"row_key": "row-1", "columns": actual_columns(bulan_sp2d=None)}]
        enriched_columns = actual_columns()
        enriched_columns["bulan_sp2d"] = actual_value(
            6,
            "SP2D_IMPORT",
            extraction_method="exact_join",
            inputs=["satker_code", "nomor_spm", "tahun"],
        )
        report = build_report(
            validated,
            extraction,
            [{"row_key": "row-1", "columns": enriched_columns}],
        )
        self.assertEqual(report["extraction"]["per_column"]["bulan_sp2d"]["counts"], {"MISSING": 1})
        self.assertEqual(report["enrichment"]["per_column"]["bulan_sp2d"]["counts"], {"EXACT": 1})

    def test_ambiguous_field_requires_explicit_review(self):
        tx = self.transaction(fp=None)
        tx["reviewer_expected"]["extraction"]["fp"]["availability"] = "AMBIGUOUS"
        validated = validate_manifest(manifest(tx))
        columns = actual_columns(fp=None)
        columns["fp"]["review"] = True
        comparison = compare_layer(validated, [{"row_key": "row-1", "columns": columns}], "extraction")
        self.assertEqual(next(item for item in comparison if item["field"] == "fp")["status"], "REVIEW")

    def test_sensitive_mismatch_is_redacted(self):
        tx = self.transaction()
        validated = validate_manifest(manifest(tx))
        report = build_report(
            validated,
            [{"row_key": "row-1", "columns": actual_columns(deskripsi="Nama pegawai rahasia")}],
            [{"row_key": "row-1", "columns": actual_columns()}],
        )
        mismatch = next(item for item in report["mismatches"] if item["field"] == "deskripsi")
        self.assertEqual(mismatch["expected"], "[REDACTED]")
        self.assertEqual(mismatch["actual"], "[REDACTED]")
        self.assertNotIn("Nama pegawai", json.dumps(report))

    def test_local_required_corpus_missing_fails_but_optional_returns_none(self):
        validated = validate_manifest(manifest())
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(resolve_fixture(validated, directory, required=False))
            with self.assertRaises(GoldenCorpusMissing):
                resolve_fixture(validated, directory, required=True)

    def test_hash_change_is_failure_even_when_corpus_optional(self):
        validated = validate_manifest(manifest())
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "synthetic.pdf").write_bytes(b"changed")
            with self.assertRaisesMessage(GoldenValidationError, "Hash fixture berubah"):
                resolve_fixture(validated, directory, required=False)

    def test_all_fifteen_columns_are_reported(self):
        tx = self.transaction()
        validated = validate_manifest(manifest(tx))
        rows = [{"row_key": "row-1", "columns": actual_columns()}]
        report = build_report(validated, rows, rows)
        self.assertEqual(tuple(report["extraction"]["per_column"]), DK_COLUMNS)
        self.assertEqual(report["extraction"]["exact_accuracy"], 100.0)
        self.assertEqual(report["per_document"]["extraction"]["synthetic-document"]["exact_accuracy"], 100.0)

    def test_comparator_is_order_independent_and_does_not_use_case_id_as_identity(self):
        tx1 = self.transaction("member-a:row-1", "opaque-b")
        tx2 = self.transaction("member-b:row-1", "opaque-a", akun="522111")
        validated = validate_manifest(manifest(tx1, tx2))
        first = {"row_key": "member-a:row-1", "columns": actual_columns()}
        second = {"row_key": "member-b:row-1", "columns": actual_columns(akun="522111")}
        forward = compare_layer(validated, [first, second], "extraction")
        reverse = compare_layer(validated, [second, first], "extraction")
        key = lambda item: (item["row_key"], item["field"], item["status"])
        self.assertEqual(sorted(map(key, forward)), sorted(map(key, reverse)))

    def test_pending_or_rejected_annotation_cannot_enter_acceptance(self):
        for status in ("PENDING", "REJECTED"):
            tx = self.transaction(case_id=f"case-{status.lower()}")
            tx["reviewer_status"] = status
            validated = validate_manifest(manifest(tx))
            with self.assertRaisesMessage(GoldenValidationError, "APPROVED"):
                compare_layer(validated, [], "extraction")

    def test_annotation_draft_keeps_candidate_separate_from_reviewer_truth(self):
        fixture = manifest()["fixture"]
        rows = [{"row_key": "row-1", "columns": actual_columns(akun="522222")}]
        draft = build_annotation_draft(fixture, rows, rows)
        transaction = draft["transactions"][0]
        self.assertEqual(transaction["reviewer_status"], "PENDING")
        self.assertEqual(
            transaction["parser_candidate"]["extraction"]["akun"]["value"], "522222"
        )
        expected = transaction["reviewer_expected"]["extraction"]["akun"]
        self.assertIsNone(expected["value"])
        self.assertEqual(expected["availability"], "AMBIGUOUS")
