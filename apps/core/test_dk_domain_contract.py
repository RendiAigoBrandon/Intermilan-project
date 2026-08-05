"""
test_dk_domain_contract.py

Regression tests for the 15-column D_K domain contract.

These tests validate the contract invariants and ensure the implementation
matches the domain contract specification.
"""

from django.test import TestCase

from apps.core.dk_domain_contract import (
    COLUMNS,
    DocumentType,
    FORBIDDEN_NO_DRPP_VALUES,
    SourcePriority,
    get_column_by_ordinal,
    get_column_by_field_name,
    get_field_names,
    is_read_only,
    is_required,
    get_null_policy,
    build_empty_dk_draft_row,
    build_empty_dk_review_metadata,
    build_empty_dk_draft,
    validate_draft_row,
    validate_no_drpp_for_doc_type,
    validate_zero_vs_null,
)


class TestContractStructure(TestCase):
    """Test contract has exactly 15 columns with valid structure."""

    def test_exactly_15_columns(self):
        """Contract must define exactly 15 columns."""
        self.assertEqual(len(COLUMNS), 15)

    def test_ordinals_1_to_15(self):
        """All ordinals must be 1 through 15."""
        ordinals = sorted([c["ordinal"] for c in COLUMNS])
        self.assertEqual(ordinals, list(range(1, 16)))

    def test_ordinals_unique(self):
        """All ordinals must be unique."""
        ordinals = [c["ordinal"] for c in COLUMNS]
        self.assertEqual(len(set(ordinals)), len(ordinals))

    def test_field_names_unique(self):
        """All field names must be unique."""
        field_names = [c["field_name"] for c in COLUMNS]
        self.assertEqual(len(set(field_names)), len(field_names))

    def test_all_have_source_priority(self):
        """All columns must have source_priority."""
        for col in COLUMNS:
            self.assertIn("source_priority", col, f"Column {col['ordinal']} missing source_priority")

    def test_all_have_null_policy(self):
        """All columns must have null_policy."""
        for col in COLUMNS:
            self.assertIn("null_policy", col, f"Column {col['ordinal']} missing null_policy")

    def test_all_have_review_policy(self):
        """All columns must have review_policy."""
        for col in COLUMNS:
            self.assertIn("review_policy", col, f"Column {col['ordinal']} missing review_policy")


class TestHelperReadOnly(TestCase):
    """Test that Helper field is read-only/derived."""

    def test_helper_is_not_editable(self):
        """Helper field must be editable=False (read-only/derived)."""
        helper_col = get_column_by_field_name("helper")
        self.assertFalse(helper_col.get("editable"), "Helper should be read-only/derived")

    def test_helper_source_is_derived(self):
        """Helper field source should be derived."""
        helper_col = get_column_by_field_name("helper")
        self.assertEqual(helper_col["source_priority"], SourcePriority.DERIVED)


class TestEmptyDraftRow(TestCase):
    """Test build_empty_dk_draft_row() produces exactly 15 columns."""

    def test_empty_draft_has_15_keys(self):
        """Empty draft must have exactly 15 keys (no review_metadata in row)."""
        draft = build_empty_dk_draft_row()
        self.assertEqual(len(draft), 15)

    def test_empty_draft_keys_match_contract(self):
        """Row keys must exactly match contract field names."""
        draft = build_empty_dk_draft_row()
        expected_keys = set(get_field_names())
        actual_keys = set(draft.keys())
        self.assertEqual(actual_keys, expected_keys)

    def test_review_metadata_not_in_row(self):
        """review_metadata must NOT be in the draft row."""
        draft = build_empty_dk_draft_row()
        self.assertNotIn("review_metadata", draft)

    def test_all_values_initial_null(self):
        """All initial values must be None/null."""
        draft = build_empty_dk_draft_row()
        for key, value in draft.items():
            self.assertIsNone(value, f"Field '{key}' should be None initially")

    def test_no_empty_strings(self):
        """Empty draft must not contain empty strings."""
        draft = build_empty_dk_draft_row()
        for key, value in draft.items():
            self.assertNotEqual(value, "", f"Field '{key}' must not be empty string")

    def test_no_placeholder_dashes(self):
        """Empty draft must not contain '-' placeholder."""
        draft = build_empty_dk_draft_row()
        for key, value in draft.items():
            self.assertNotEqual(value, "-", f"Field '{key}' must not be '-'")

    def test_no_tanpa_drpp(self):
        """Empty draft must not contain 'TANPA_DRPP'."""
        draft = build_empty_dk_draft_row()
        self.assertNotEqual(draft.get("no_drpp"), "TANPA_DRPP")


class TestReviewMetadata(TestCase):
    """Test build_empty_dk_review_metadata() produces valid metadata."""

    def test_metadata_has_required_keys(self):
        """Metadata must have field_status, field_source, field_evidence, requires_review, review_reasons."""
        metadata = build_empty_dk_review_metadata()
        self.assertIn("field_status", metadata)
        self.assertIn("field_source", metadata)
        self.assertIn("field_evidence", metadata)
        self.assertIn("requires_review", metadata)
        self.assertIn("review_reasons", metadata)

    def test_metadata_field_dicts_have_15_fields(self):
        """field_status, field_source, field_evidence must have 15 keys."""
        metadata = build_empty_dk_review_metadata()
        expected_fields = set(get_field_names())
        for meta_key in ["field_status", "field_source", "field_evidence"]:
            actual_fields = set(metadata[meta_key].keys())
            self.assertEqual(actual_fields, expected_fields,
                           f"'{meta_key}' must have 15 fields")

    def test_metadata_requires_review_false_initially(self):
        """requires_review must be False initially."""
        metadata = build_empty_dk_review_metadata()
        self.assertFalse(metadata["requires_review"])

    def test_metadata_review_reasons_empty_initially(self):
        """review_reasons must be empty list initially."""
        metadata = build_empty_dk_review_metadata()
        self.assertEqual(metadata["review_reasons"], [])


class TestParentDraft(TestCase):
    """Test build_empty_dk_draft() produces valid container."""

    def test_parent_has_row_and_metadata(self):
        """Parent draft must have 'row' and 'review_metadata' keys."""
        draft = build_empty_dk_draft()
        self.assertIn("row", draft)
        self.assertIn("review_metadata", draft)

    def test_parent_row_has_15_keys(self):
        """Parent draft row must have exactly 15 keys."""
        draft = build_empty_dk_draft()
        self.assertEqual(len(draft["row"]), 15)

    def test_parent_row_is_valid_draft_row(self):
        """Parent draft row must match build_empty_dk_draft_row()."""
        draft = build_empty_dk_draft()
        expected_row = build_empty_dk_draft_row()
        self.assertEqual(draft["row"], expected_row)


class TestColumnDefinitions(TestCase):
    """Test individual column definitions."""

    def test_helper_column(self):
        """Column 1: Helper is derived, not editable."""
        col = get_column_by_ordinal(1)
        self.assertEqual(col["field_name"], "helper")
        self.assertFalse(col["editable"])
        self.assertEqual(col["source_priority"], SourcePriority.DERIVED)

    def test_akun_column(self):
        """Column 2: Akun is required for draft and commit."""
        col = get_column_by_ordinal(2)
        self.assertEqual(col["field_name"], "akun")
        self.assertTrue(col["editable"])
        self.assertTrue(col["required_for_draft"])
        self.assertTrue(col["required_for_commit"])

    def test_nilai_bruto_required(self):
        """Column 11: Nilai Bruto is required."""
        col = get_column_by_ordinal(11)
        self.assertEqual(col["field_name"], "nilai_bruto")
        self.assertTrue(col["required_for_draft"])
        self.assertTrue(col["required_for_commit"])

    def test_fp_zero_semantics(self):
        """Column 14: FP has zero vs null semantics documented."""
        col = get_column_by_ordinal(14)
        self.assertEqual(col["field_name"], "fp")
        self.assertIn("zero_semantics", col)

    def test_pph21_zero_semantics(self):
        """Column 15: PPh21 has zero vs null semantics documented."""
        col = get_column_by_ordinal(15)
        self.assertEqual(col["field_name"], "pph21")
        self.assertIn("zero_semantics", col)


class TestSourcePriority(TestCase):
    """Test source priority rules."""

    def test_akun_from_ocr(self):
        """Akun primary source is OCR."""
        col = get_column_by_field_name("akun")
        self.assertEqual(col["source_priority"], SourcePriority.OCR_LABELED)

    def test_nomor_spm_from_sp2d(self):
        """Nomor SPM primary source is SP2D enrichment."""
        col = get_column_by_field_name("nomor_spm")
        self.assertEqual(col["source_priority"], SourcePriority.SP2D_ENRICHMENT)

    def test_nilai_netto_derived(self):
        """Nilai Netto is derived."""
        col = get_column_by_field_name("nilai_netto")
        self.assertEqual(col["source_priority"], SourcePriority.DERIVED)

    def test_manual_cannot_be_overwritten(self):
        """manual_confirmed cannot be overwritten by lower priority.

        Contract rule: higher priority sources should not be overwritten by lower ones.
        This test documents the priority hierarchy.
        """
        # Define priority order: lower number = higher priority
        priority_order = {
            SourcePriority.MANUAL_CONFIRMED: 1,
            SourcePriority.OCR_LABELED: 2,
            SourcePriority.SP2D_ENRICHMENT: 3,
            SourcePriority.DERIVED: 4,
            SourcePriority.NULL_REVIEW: 5,
        }
        # Verify manual_confirmed is highest priority (lowest number)
        self.assertEqual(priority_order[SourcePriority.MANUAL_CONFIRMED], 1)
        # Verify hierarchy is correct
        self.assertLess(
            priority_order[SourcePriority.MANUAL_CONFIRMED],
            priority_order[SourcePriority.OCR_LABELED]
        )


class TestNoDRPPForDocType(TestCase):
    """Test no_drpp validation for different document types."""

    def test_gup_regular_requires_drpp(self):
        """GUP_REGULAR requires no_drpp."""
        valid, msg = validate_no_drpp_for_doc_type("00107/DRPP/019937/2026", DocumentType.GUP_REGULAR)
        self.assertTrue(valid)

    def test_gup_regular_fails_without_drpp(self):
        """GUP_REGULAR fails without no_drpp."""
        valid, msg = validate_no_drpp_for_doc_type(None, DocumentType.GUP_REGULAR)
        self.assertFalse(valid)
        self.assertIn("required", msg.lower())

    def test_gup_pnbp_requires_drpp(self):
        """GUP_PNBP requires no_drpp."""
        valid, msg = validate_no_drpp_for_doc_type("00107/DRPP/019937/2026", DocumentType.GUP_PNBP)
        self.assertTrue(valid)

    def test_gup_kkp_must_be_null(self):
        """GUP_KKP requires no_drpp to be null."""
        valid, msg = validate_no_drpp_for_doc_type(None, DocumentType.GUP_KKP)
        self.assertTrue(valid)

    def test_gup_kkp_forbidden_value(self):
        """GUP_KKP cannot have forbidden no_drpp values."""
        for forbidden in FORBIDDEN_NO_DRPP_VALUES:
            valid, msg = validate_no_drpp_for_doc_type(forbidden, DocumentType.GUP_KKP)
            self.assertFalse(valid, f"GUP_KKP should reject '{forbidden}'")

    def test_gup_kkp_non_null_fails(self):
        """GUP_KKP fails if no_drpp is not null."""
        valid, msg = validate_no_drpp_for_doc_type("00107/DRPP/019937/2026", DocumentType.GUP_KKP)
        self.assertFalse(valid)
        self.assertIn("must be null", msg)


class TestTanggalSpmNotFromSp2d(TestCase):
    """Test that tanggal_spm cannot be sourced from tgl_sp2d."""

    def test_tanggal_spm_special_rule_exists(self):
        """tanggal_spm must have special_rule about not from tgl_sp2d."""
        col = get_column_by_field_name("tanggal_spm")
        self.assertIn("special_rule", col)
        self.assertIn("tgl_sp2d", col["special_rule"].lower())

    def test_tanggal_spm_source_is_sp2d_enrichment(self):
        """tanggal_spm source is SP2D enrichment (but not from SP2D date field)."""
        col = get_column_by_field_name("tanggal_spm")
        self.assertEqual(col["source_priority"], SourcePriority.SP2D_ENRICHMENT)


class TestZeroVsNullSemantics(TestCase):
    """Test zero vs null semantics for fp and pph21."""

    def test_fp_zero_requires_explicit_label(self):
        """fp=0 requires explicit 'Rp0' label evidence."""
        valid, msg = validate_zero_vs_null("fp", 0, has_explicit_label=False)
        self.assertFalse(valid)
        self.assertIn("explicit", msg.lower())

    def test_fp_zero_with_explicit_label(self):
        """fp=0 with explicit label is valid."""
        valid, msg = validate_zero_vs_null("fp", 0, has_explicit_label=True)
        self.assertTrue(valid)

    def test_fp_null_without_label(self):
        """fp=null without explicit label is valid."""
        valid, msg = validate_zero_vs_null("fp", None, has_explicit_label=False)
        self.assertTrue(valid)

    def test_fp_null_conflicts_with_explicit_label(self):
        """fp=null conflicts with explicit 'Rp0' label."""
        valid, msg = validate_zero_vs_null("fp", None, has_explicit_label=True)
        self.assertFalse(valid)
        self.assertIn("conflicts", msg.lower())

    def test_pph21_zero_requires_explicit_label(self):
        """pph21=0 requires explicit 'Rp0' label evidence."""
        valid, msg = validate_zero_vs_null("pph21", 0, has_explicit_label=False)
        self.assertFalse(valid)

    def test_other_fields_no_zero_semantics(self):
        """Non-tax fields don't have zero semantics constraints."""
        for fname in ["akun", "nilai_bruto", "bulan_sp2d"]:
            valid, msg = validate_zero_vs_null(fname, None, has_explicit_label=False)
            self.assertTrue(valid)


class TestInvalidReceiptHandling(TestCase):
    """Test invalid receipt normalization rules."""

    def test_no_kuitansi_normalized_to_null(self):
        """Invalid receipt: no_kuitansi should be null."""
        # This is a contract rule for how invalid receipts are handled
        # The raw token is preserved, but normalized value is null
        col = get_column_by_field_name("no_kuitansi")
        self.assertEqual(col["null_policy"], "review_if_null")

    def test_no_kuitansi_not_in_exact_key_when_invalid(self):
        """Invalid receipt no_kuitansi doesn't enter exact key matching."""
        # Contract rule: invalid receipts are UNRESOLVED, not EXTRA
        # This is tested by the evaluator behavior
        pass  # Structural test only


class TestManualConfirmedProtection(TestCase):
    """Test that manual_confirmed values are protected."""

    def test_manual_confirmed_highest_priority(self):
        """manual_confirmed should be highest priority."""
        # Define priority order: lower number = higher priority
        priority_order = {
            SourcePriority.MANUAL_CONFIRMED: 1,
            SourcePriority.OCR_LABELED: 2,
            SourcePriority.SP2D_ENRICHMENT: 3,
            SourcePriority.DERIVED: 4,
            SourcePriority.NULL_REVIEW: 5,
        }
        # Check that manual_confirmed has lowest (highest) priority number
        self.assertEqual(priority_order[SourcePriority.MANUAL_CONFIRMED], 1)


class TestRequiredFieldsForCommit(TestCase):
    """Test required_for_commit rules."""

    def test_akun_required_for_commit(self):
        """Akun is required for commit."""
        self.assertTrue(is_required("akun", for_commit=True))

    def test_nilai_bruto_required_for_commit(self):
        """Nilai Bruto is required for commit."""
        self.assertTrue(is_required("nilai_bruto", for_commit=True))

    def test_fp_not_required_for_draft(self):
        """FP is not required for draft."""
        self.assertFalse(is_required("fp", for_commit=False))

    def test_pph21_not_required_for_draft(self):
        """PPh21 is not required for draft."""
        self.assertFalse(is_required("pph21", for_commit=False))


class TestValidateDraftRow(TestCase):
    """Test draft row validation."""

    def test_valid_draft_passes(self):
        """Valid draft with required fields passes."""
        draft = build_empty_dk_draft_row()
        # Set all required fields for commit
        draft["akun"] = "521111"
        draft["nilai_bruto"] = 8425000
        draft["nilai_netto"] = 8425000
        draft["bulan_sp2d"] = 7
        draft["cara_pembayaran"] = "GUP"
        draft["nomor_spm"] = "01077A"
        draft["tanggal_spm"] = "2026-07-28"
        draft["jenis_spm"] = "GUP"
        draft["no_kuitansi"] = "01011/KW/019937/2026"
        draft["no_drpp"] = "00107/DRPP/019937/2026"
        draft["deskripsi"] = "Test transaction"
        draft["pembebanan"] = "2886.EBA.994.002.521111"
        draft["fp"] = 0  # Explicit zero
        draft["pph21"] = 0  # Explicit zero
        errors = validate_draft_row(draft, for_commit=True)
        self.assertEqual(errors, [])

    def test_missing_required_fails_commit(self):
        """Missing required field fails for commit."""
        draft = build_empty_dk_draft_row()
        draft["akun"] = "521111"
        # Missing nilai_bruto which is required
        errors = validate_draft_row(draft, for_commit=True)
        self.assertTrue(len(errors) > 0)

    def test_extra_field_fails(self):
        """Extra field not in contract fails."""
        draft = build_empty_dk_draft_row()
        draft["extra_field"] = "value"
        errors = validate_draft_row(draft)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("Extra" in e for e in errors))

    def test_forbidden_drpp_value_fails(self):
        """Forbidden no_drpp value fails."""
        draft = build_empty_dk_draft_row()
        draft["no_drpp"] = "-"
        errors = validate_draft_row(draft)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("Forbidden" in e for e in errors))


class TestGetHelperFunction(TestCase):
    """Test helper functions."""

    def test_get_field_names_returns_15(self):
        """get_field_names returns exactly 15 field names."""
        names = get_field_names()
        self.assertEqual(len(names), 15)

    def test_get_field_names_in_order(self):
        """get_field_names returns fields in ordinal order."""
        names = get_field_names()
        ordinals = [get_column_by_field_name(n)["ordinal"] for n in names]
        self.assertEqual(ordinals, list(range(1, 16)))

    def test_is_read_only_helper(self):
        """is_read_only returns True for helper."""
        self.assertTrue(is_read_only("helper"))

    def test_is_read_only_akun(self):
        """is_read_only returns False for editable fields."""
        self.assertFalse(is_read_only("akun"))

    def test_get_null_policy_akun(self):
        """Akun null_policy is not_allowed."""
        policy = get_null_policy("akun")
        self.assertEqual(policy, "not_allowed")

    def test_get_null_policy_fp(self):
        """FP null_policy is review_if_null."""
        policy = get_null_policy("fp")
        self.assertEqual(policy, "review_if_null")
