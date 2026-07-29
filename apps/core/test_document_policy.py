from django.test import SimpleTestCase

from .document_policy import (
    DocumentRequirement,
    SPMFamily,
    allows_empty_drpp,
    document_requirement_policy,
    is_drpp_required,
    normalize_spm_family,
)


class DocumentPolicyTests(SimpleTestCase):
    def test_normalizes_supported_families_without_batch_suffix(self):
        cases = {
            "GUP 1": SPMFamily.GUP_REGULAR,
            "GUP 17": SPMFamily.GUP_REGULAR,
            "GUP": SPMFamily.GUP_REGULAR,
            "GUP 2 (PNBP)": SPMFamily.GUP_PNBP,
            "GUP KKP 1": SPMFamily.GUP_KKP,
            "GU KKP 9": SPMFamily.GUP_KKP,
            "UP": SPMFamily.UP,
            "TUP": SPMFamily.TUP,
            "GTUP NIHIL": SPMFamily.GTUP_NIHIL,
            "GAJI INDUK": SPMFamily.GAJI,
            "GAJI PPPK INDUK": SPMFamily.GAJI,
            "PENGHASILAN PPNPN INDUK": SPMFamily.PENGHASILAN_PPNPN,
            "SPM THR PPPK": SPMFamily.THR,
            "SPM Gaji 13 PPPK": SPMFamily.GAJI_13,
            "NON GAJI": SPMFamily.NON_GAJI,
            "NON GAJI KONTRAKTUAL": SPMFamily.NON_GAJI_KONTRAKTUAL,
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(normalize_spm_family(label), expected)

    def test_policy_matrix(self):
        expected = {
            SPMFamily.GUP_REGULAR: DocumentRequirement.DRPP_REQUIRED,
            SPMFamily.GUP_PNBP: DocumentRequirement.DRPP_REQUIRED,
            SPMFamily.GUP_KKP: DocumentRequirement.KKP_PAYMENT_LIST_REQUIRED,
            SPMFamily.UP: DocumentRequirement.HEADER_ONLY,
            SPMFamily.TUP: DocumentRequirement.HEADER_ONLY,
            SPMFamily.GTUP_NIHIL: DocumentRequirement.CONTEXT_DEPENDENT,
            SPMFamily.GAJI: DocumentRequirement.NOMINATIVE_REQUIRED,
            SPMFamily.PENGHASILAN_PPNPN: DocumentRequirement.NOMINATIVE_REQUIRED,
            SPMFamily.TUNJANGAN_KINERJA: DocumentRequirement.NOMINATIVE_REQUIRED,
            SPMFamily.THR: DocumentRequirement.NOMINATIVE_REQUIRED,
            SPMFamily.GAJI_13: DocumentRequirement.NOMINATIVE_REQUIRED,
            SPMFamily.NON_GAJI: DocumentRequirement.CONTEXT_DEPENDENT,
            SPMFamily.NON_GAJI_KONTRAKTUAL: DocumentRequirement.SOURCE_DOCUMENT_REQUIRED,
            SPMFamily.UNKNOWN: DocumentRequirement.UNSUPPORTED_REVIEW,
        }
        for family, policy in expected.items():
            with self.subTest(family=family):
                self.assertEqual(document_requirement_policy(family), policy)

    def test_unknown_is_not_treated_as_free_of_drpp(self):
        self.assertFalse(allows_empty_drpp("tidak dikenal"))
        self.assertFalse(is_drpp_required("tidak dikenal"))
        self.assertTrue(is_drpp_required("GUP 17"))
        self.assertTrue(allows_empty_drpp("GUP KKP 8"))
