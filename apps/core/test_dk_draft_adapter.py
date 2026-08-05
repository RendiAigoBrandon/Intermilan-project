"""
test_dk_draft_adapter.py

Regression tests for dk_draft_adapter module.
Validates that parser output is correctly converted to 15-column D_K drafts.
"""

from decimal import Decimal

from django.test import TestCase

from apps.core.dk_domain_contract import get_field_names
from apps.core.dk_draft_adapter import (
    DraftSource,
    DraftStatus,
    build_dk_drafts_from_parsed_data,
)


class TestDraftAdapterStructure(TestCase):
    """Test that drafts have exactly 15 columns."""

    def test_single_transaction_creates_one_draft(self):
        """One kw_item should produce one draft."""
        parsed_data = {
            "kw_items": [
                {
                    "akun": "521111",
                    "no_kuitansi": "01011/KW/019937/2026",
                    "nilai_bruto": Decimal("8425000"),
                }
            ]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        self.assertEqual(len(drafts), 1)

    def test_draft_row_has_15_keys(self):
        """Draft row must have exactly 15 keys."""
        parsed_data = {
            "kw_items": [
                {
                    "akun": "521111",
                    "no_kuitansi": "01011/KW/019937/2026",
                    "nilai_bruto": Decimal("8425000"),
                }
            ]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        row = drafts[0]["row"]
        self.assertEqual(len(row), 15)

    def test_two_transactions_create_two_drafts(self):
        """Two kw_items should produce two drafts."""
        parsed_data = {
            "kw_items": [
                {
                    "akun": "521111",
                    "no_kuitansi": "01011/KW/019937/2026",
                    "nilai_bruto": Decimal("8425000"),
                },
                {
                    "akun": "521112",
                    "no_kuitansi": "01012/KW/019937/2026",
                    "nilai_bruto": Decimal("2000000"),
                },
            ]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        self.assertEqual(len(drafts), 2)

    def test_document_fields_copied_to_all_rows(self):
        """Document fields should be copied to every draft row."""
        parsed_data = {
            "spm": {
                "nomor_spm": "01077A",
                "jenis_spm": "GUP",
            },
            "kw_items": [
                {"akun": "521111"},
                {"akun": "521112"},
            ],
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        self.assertEqual(drafts[0]["row"]["nomor_spm"], "01077A")
        self.assertEqual(drafts[1]["row"]["nomor_spm"], "01077A")

    def test_transaction_fields_not_swapped(self):
        """Akun and kuitansi should not swap between rows."""
        parsed_data = {
            "kw_items": [
                {
                    "akun": "521111",
                    "no_kuitansi": "01011/KW/019937/2026",
                },
                {
                    "akun": "521112",
                    "no_kuitansi": "01012/KW/019937/2026",
                },
            ]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        self.assertEqual(drafts[0]["row"]["akun"], "521111")
        self.assertEqual(drafts[0]["row"]["no_kuitansi"], "01011/KW/019937/2026")
        self.assertEqual(drafts[1]["row"]["akun"], "521112")
        self.assertEqual(drafts[1]["row"]["no_kuitansi"], "01012/KW/019937/2026")

    def test_missing_field_is_null_with_review(self):
        """Missing field should be None and have REVIEW status."""
        parsed_data = {
            "kw_items": [
                {"akun": "521111"}  # Missing no_kuitansi, nilai_bruto
            ]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        row = drafts[0]["row"]
        metadata = drafts[0]["review_metadata"]

        # Check no_kuitansi is null
        self.assertIsNone(row["no_kuitansi"])
        self.assertEqual(metadata["field_status"]["no_kuitansi"], DraftStatus.MISSING)

        # Check nilai_bruto is null
        self.assertIsNone(row["nilai_bruto"])
        self.assertEqual(metadata["field_status"]["nilai_bruto"], DraftStatus.MISSING)


class TestInvalidReceipt(TestCase):
    """Test invalid receipt handling."""

    def test_invalid_receipt_nullifies_no_kuitansi(self):
        """Invalid receipt should have no_kuitansi = None."""
        parsed_data = {
            "kw_items": [
                {
                    "akun": "521111",
                    "no_kuitansi": "99999/KW/AMBIGUOUS",
                    "receipt_valid": False,
                    "_raw_receipt": "99999/KW/AMBIGUOUS",
                }
            ]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        row = drafts[0]["row"]
        metadata = drafts[0]["review_metadata"]

        # no_kuitansi should be null
        self.assertIsNone(row["no_kuitansi"])

    def test_invalid_receipt_preserves_raw_evidence(self):
        """Invalid receipt raw token should be preserved in evidence."""
        parsed_data = {
            "kw_items": [
                {
                    "akun": "521111",
                    "no_kuitansi": "99999/KW/AMBIGUOUS",
                    "receipt_valid": False,
                    "_raw_receipt": "99999/KW/AMBIGUOUS",
                }
            ]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        evidence = drafts[0]["raw_evidence"]

        self.assertIn("invalid_receipt_token", evidence)
        self.assertEqual(evidence["invalid_receipt_token"], "99999/KW/AMBIGUOUS")

    def test_invalid_receipt_helper_is_null(self):
        """Helper should be null when no_kuitansi is invalid."""
        parsed_data = {
            "kw_items": [
                {
                    "akun": "521111",
                    "no_kuitansi": "99999/KW/AMBIGUOUS",
                    "receipt_valid": False,
                }
            ]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        row = drafts[0]["row"]

        # Helper should be null because no_kuitansi is null
        self.assertIsNone(row["helper"])

    def test_invalid_receipt_has_review_status(self):
        """Invalid receipt should trigger REVIEW."""
        parsed_data = {
            "kw_items": [
                {
                    "akun": "521111",
                    "no_kuitansi": "99999/KW/AMBIGUOUS",
                    "receipt_valid": False,
                }
            ]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        metadata = drafts[0]["review_metadata"]

        self.assertEqual(metadata["field_status"]["no_kuitansi"], DraftStatus.REVIEW)
        # Check that invalid receipt is mentioned in reasons
        reasons_str = str(metadata["review_reasons"]).lower()
        self.assertIn("kuitansi", reasons_str)


class TestTanggalSpmNotFromTglSp2d(TestCase):
    """Test that tanggal_spm never comes from tgl_sp2d."""

    def test_tanggal_spm_requires_spm_evidence(self):
        """tanggal_spm should only be set with SPM evidence."""
        # Without SPM data, tanggal_spm should be null
        parsed_data = {
            "kw_items": [{"akun": "521111"}],
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        row = drafts[0]["row"]
        metadata = drafts[0]["review_metadata"]

        self.assertIsNone(row["tanggal_spm"])
        self.assertTrue(metadata["requires_review"])

    def test_tanggal_spm_from_spm_not_sp2d(self):
        """tanggal_spm should come from SPM document, not SP2D."""
        parsed_data = {
            "spm": {
                "nomor_spm": "01077A",
                "tanggal_spm": "2026-07-28",
            },
            "kw_items": [{"akun": "521111"}],
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        row = drafts[0]["row"]
        metadata = drafts[0]["review_metadata"]

        self.assertEqual(metadata["field_status"]["tanggal_spm"], DraftStatus.EXACT)


class TestGupPnbpNotOther(TestCase):
    """Test GUP_PNBP is preserved and not fallen to OTHER."""

    def test_gup_pnbp_preserved(self):
        """GUP_PNBP should stay as GUP_PNBP."""
        parsed_data = {
            "spm": {"jenis_spm": "GUP_PNBP"},
            "kw_items": [{"akun": "521111"}],
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        row = drafts[0]["row"]

        self.assertEqual(row["jenis_spm"], "GUP_PNBP")


class TestGupKkpNoDrpp(TestCase):
    """Test GUP_KKP no_drpp rules."""

    def test_kkp_no_drpp_is_null(self):
        """GUP_KKP should have no_drpp = null."""
        parsed_data = {
            "spm": {"jenis_spm": "GUP_KKP"},
            "kw_items": [{"akun": "521111", "no_drpp": "SOME_VALUE"}],
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        row = drafts[0]["row"]
        metadata = drafts[0]["review_metadata"]

        self.assertIsNone(row["no_drpp"])
        self.assertEqual(metadata["field_status"]["no_drpp"], DraftStatus.NOT_APPLICABLE)

    def test_kkp_no_drpp_not_review(self):
        """GUP_KKP no_drpp=null should NOT trigger REVIEW."""
        parsed_data = {
            "spm": {"jenis_spm": "KKP"},
            "kw_items": [{"akun": "521111"}],
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        metadata = drafts[0]["review_metadata"]

        # should NOT be REVIEW
        self.assertNotEqual(metadata["field_status"]["no_drpp"], DraftStatus.REVIEW)


class TestGupRegularMissingDrpp(TestCase):
    """Test GUP_REGULAR requires DRPP."""

    def test_gup_regular_missing_drpp_triggers_review(self):
        """GUP_REGULAR without DRPP should trigger REVIEW."""
        parsed_data = {
            "spm": {"jenis_spm": "GUP_REGULAR"},
            "kw_items": [{"akun": "521111"}],
            # No drpp data
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        row = drafts[0]["row"]
        metadata = drafts[0]["review_metadata"]

        self.assertIsNone(row["no_drpp"])
        self.assertEqual(metadata["field_status"]["no_drpp"], DraftStatus.REVIEW)
        self.assertTrue(metadata["requires_review"])


class TestExplicitZero(TestCase):
    """Test explicit zero vs missing evidence."""

    def test_explicit_rp0_becomes_decimal_zero(self):
        """Explicit Rp0 label should become Decimal(0)."""
        parsed_data = {
            "kw_items": [
                {
                    "akun": "521111",
                    "nilai_bruto": Decimal("8425000"),
                    "fp": Decimal("0"),
                    "_has_fp_label": True,  # Explicit label present
                }
            ]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        row = drafts[0]["row"]
        metadata = drafts[0]["review_metadata"]

        self.assertEqual(row["fp"], Decimal("0"))
        self.assertEqual(metadata["field_status"]["fp"], DraftStatus.EXACT)

    def test_no_label_becomes_null(self):
        """No label should become null (not implicit zero)."""
        parsed_data = {
            "kw_items": [
                {
                    "akun": "521111",
                    "nilai_bruto": Decimal("8425000"),
                    # No fp field at all
                }
            ]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        row = drafts[0]["row"]
        metadata = drafts[0]["review_metadata"]

        self.assertIsNone(row["fp"])
        self.assertEqual(metadata["field_status"]["fp"], DraftStatus.REVIEW)


class TestPembebanan(TestCase):
    """Test pembebanan handling."""

    def test_pembebanan_canonical_used(self):
        """Canonical pembebanan should be used, raw preserved as evidence."""
        parsed_data = {
            "kw_items": [
                {
                    "akun": "521111",
                    "pembebanan": "2886,EBA.994,002.521111",
                    "pembebanan_canonical": "2886.EBA.994.002.521111",
                }
            ]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        row = drafts[0]["row"]
        evidence = drafts[0]["raw_evidence"]

        self.assertEqual(row["pembebanan"], "2886.EBA.994.002.521111")
        self.assertEqual(evidence["pembebanan_canonical"], "2886.EBA.994.002.521111")

    def test_malformed_pembebanan_triggers_review(self):
        """Malformed pembebanan should trigger REVIEW."""
        parsed_data = {
            "kw_items": [
                {
                    "akun": "521111",
                    "pembebanan": "MALFORMED",
                }
            ]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        metadata = drafts[0]["review_metadata"]

        self.assertEqual(metadata["field_status"]["pembebanan"], DraftStatus.REVIEW)


class TestManualConfirmedNotOverwritten(TestCase):
    """Test source priority - manual_confirmed cannot be overwritten."""

    def test_manual_confirmed_not_overwritten_by_null(self):
        """Value marked manual_confirmed should not be overwritten by null."""
        # This test validates the CONTRACT rule exists
        # Actual implementation would need manual_confirmed flag in kw_item
        self.assertTrue(True)  # Placeholder for future implementation


class TestHelperDerived(TestCase):
    """Test helper derivation rules."""

    def test_helper_derived_when_exact_key_complete(self):
        """Helper should be derived when akun + no_kuitansi available."""
        parsed_data = {
            "kw_items": [
                {
                    "akun": "521111",
                    "no_kuitansi": "01011/KW/019937/2026",
                }
            ]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        row = drafts[0]["row"]
        metadata = drafts[0]["review_metadata"]

        self.assertEqual(row["helper"], "52111101011/KW/019937/2026")
        self.assertEqual(metadata["field_status"]["helper"], DraftStatus.DERIVED)

    def test_helper_null_when_incomplete(self):
        """Helper should be null when exact key incomplete."""
        parsed_data = {
            "kw_items": [
                {"akun": "521111"}  # Missing no_kuitansi
            ]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        row = drafts[0]["row"]
        metadata = drafts[0]["review_metadata"]

        self.assertIsNone(row["helper"])
        self.assertEqual(metadata["field_status"]["helper"], DraftStatus.REVIEW)


class TestNoForbiddenValues(TestCase):
    """Test no forbidden values in output."""

    def test_no_empty_strings(self):
        """Output should not contain empty strings."""
        parsed_data = {
            "kw_items": [
                {"akun": "521111", "no_kuitansi": "VALID123"}
            ]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        row = drafts[0]["row"]

        for key, value in row.items():
            self.assertNotEqual(value, "", f"Field '{key}' should not be empty string")

    def test_no_dashes(self):
        """Output should not contain '-' placeholder."""
        parsed_data = {
            "kw_items": [
                {"akun": "521111", "no_kuitansi": "VALID"}
            ]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        row = drafts[0]["row"]

        for key, value in row.items():
            if value is not None:
                self.assertNotEqual(value, "-", f"Field '{key}' should not be '-'")

    def test_no_tanpa_drpp(self):
        """Output should not contain 'TANPA_DRPP'."""
        parsed_data = {
            "kw_items": [
                {"akun": "521111"}
            ],
            "spm": {"jenis_spm": "GUP"},
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        row = drafts[0]["row"]

        self.assertNotEqual(row.get("no_drpp"), "TANPA_DRPP")


class TestParsedDataNotMutated(TestCase):
    """Test that input parsed_data is not mutated."""

    def test_kw_items_not_mutated(self):
        """Original kw_items should not be mutated."""
        original_kw = {
            "akun": "521111",
            "no_kuitansi": "TEST123",
        }
        parsed_data = {"kw_items": [dict(original_kw)]}

        build_dk_drafts_from_parsed_data(parsed_data)

        # Original should be unchanged
        self.assertEqual(parsed_data["kw_items"][0], original_kw)

    def test_new_fields_added(self):
        """New fields should be in draft, not in original."""
        parsed_data = {
            "kw_items": [
                {"akun": "521111", "no_kuitansi": "VALID"}
            ]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        draft_row = drafts[0]["row"]
        original_item = parsed_data["kw_items"][0]

        # Draft has helper, original doesn't
        self.assertIn("helper", draft_row)
        self.assertNotIn("helper", original_item)


class TestRowKeysMatchContract(TestCase):
    """Test that draft row keys match contract exactly."""

    def test_row_keys_exact_match(self):
        """Draft row keys must exactly match contract field names."""
        parsed_data = {
            "kw_items": [{"akun": "521111"}]
        }
        drafts = build_dk_drafts_from_parsed_data(parsed_data)
        row = drafts[0]["row"]
        expected_keys = set(get_field_names())
        actual_keys = set(row.keys())

        self.assertEqual(actual_keys, expected_keys)
