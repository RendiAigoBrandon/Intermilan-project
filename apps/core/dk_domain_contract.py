"""
dk_domain_contract.py

Machine-readable domain contract for 15-column D_K draft.

This module defines the canonical structure for a D_K draft row with exactly 15 columns,
along with metadata, source priority rules, and validation constants.

SOURCE OF TRUTH ORDER:
1. docs/DOMAIN_CONTRACT_15_COLUMNS.md
2. This file (dk_domain_contract.py)
3. Regression tests (test_dk_domain_contract.py)
4. Implementation code
5. Legacy documentation
"""

# =============================================================================
# CONTRACT CONSTANTS
# =============================================================================

EXACT_KEY_FIELDS = [
    "satker",
    "tahun",
    "nomor_spm",
    "no_kuitansi",
    "akun",
]

# SP2D Document Types
class DocumentType:
    GUP_REGULAR = "GUP_REGULAR"
    GUP_PNBP = "GUP_PNBP"
    GUP_KKP = "GUP_KKP"

# Valid payment methods
VALID_CARA_PEMBAYARAN = ["UP", "TUP", "GUP", "KKP"]

# Forbidden values
FORBIDDEN_NO_DRPP_VALUES = ["-", "TANPA_DRPP", "N/A", ""]

# Source Priority (highest to lowest)
class SourcePriority:
    MANUAL_CONFIRMED = "manual_confirmed"
    OCR_LABELED = "ocr_labeled"
    SP2D_ENRICHMENT = "sp2d_enrichment"
    DERIVED = "derived"
    NULL_REVIEW = "null_review"

# Field Status
class FieldStatus:
    EXACT = "EXACT"
    REVIEW = "REVIEW"
    MISSING = "MISSING"
    WRONG = "WRONG"
    UNRESOLVED = "UNRESOLVED"
    EXTRA = "EXTRA"

# =============================================================================
# 15 COLUMN DEFINITIONS
# =============================================================================

COLUMNS = [
    {
        "ordinal": 1,
        "field_name": "helper",
        "label": "Helper",
        "data_type": "string",
        "editable": False,  # Derived field
        "source_priority": SourcePriority.DERIVED,
        "required_for_draft": False,
        "required_for_commit": False,
        "null_policy": "not_applicable",
        "review_policy": "never",
        "description": "Derived concatenation of akun + no_kuitansi for display",
    },
    {
        "ordinal": 2,
        "field_name": "akun",
        "label": "Akun",
        "data_type": "string",
        "editable": True,
        "source_priority": SourcePriority.OCR_LABELED,
        "required_for_draft": True,
        "required_for_commit": True,
        "null_policy": "not_allowed",
        "review_policy": "not_allowed",
        "description": "Account code (Kode Akun)",
    },
    {
        "ordinal": 3,
        "field_name": "bulan_sp2d",
        "label": "Bulan SP2D",
        "data_type": "integer",
        "editable": True,
        "source_priority": SourcePriority.SP2D_ENRICHMENT,
        "required_for_draft": False,
        "required_for_commit": True,
        "null_policy": "review_if_null",
        "review_policy": "if_null",
        "description": "Month of SP2D (1-12)",
    },
    {
        "ordinal": 4,
        "field_name": "cara_pembayaran",
        "label": "Cara Pembayaran",
        "data_type": "string",
        "editable": True,
        "source_priority": SourcePriority.SP2D_ENRICHMENT,
        "required_for_draft": True,
        "required_for_commit": True,
        "null_policy": "not_allowed",
        "review_policy": "if_null",
        "description": "Payment method: UP, TUP, GUP, or KKP",
    },
    {
        "ordinal": 5,
        "field_name": "nomor_spm",
        "label": "Nomor SPM",
        "data_type": "string",
        "editable": True,
        "source_priority": SourcePriority.SP2D_ENRICHMENT,
        "required_for_draft": True,
        "required_for_commit": True,
        "null_policy": "not_allowed",
        "review_policy": "if_null",
        "description": "SPM document number",
    },
    {
        "ordinal": 6,
        "field_name": "tanggal_spm",
        "label": "Tanggal SPM",
        "data_type": "date",
        "editable": True,
        "source_priority": SourcePriority.SP2D_ENRICHMENT,
        "required_for_draft": True,
        "required_for_commit": True,
        "null_policy": "not_allowed",
        "review_policy": "if_null",
        "description": "SPM date (NOT from tgl_sp2d)",
        "special_rule": "tanggal_spm MUST NOT be sourced from tgl_sp2d",
    },
    {
        "ordinal": 7,
        "field_name": "jenis_spm",
        "label": "Jenis SPM",
        "data_type": "string",
        "editable": True,
        "source_priority": SourcePriority.SP2D_ENRICHMENT,
        "required_for_draft": True,
        "required_for_commit": True,
        "null_policy": "not_allowed",
        "review_policy": "if_null",
        "description": "SPM type: GUP, UP, TUP",
    },
    {
        "ordinal": 8,
        "field_name": "no_kuitansi",
        "label": "No Kuitansi",
        "data_type": "string",
        "editable": True,
        "source_priority": SourcePriority.OCR_LABELED,
        "required_for_draft": False,
        "required_for_commit": True,
        "null_policy": "review_if_null",
        "review_policy": "if_null",
        "description": "Receipt number (NNNNN/KW/XXXXXX/YYYY)",
        "special_rule": "For KKP: typically null (no receipt)",
    },
    {
        "ordinal": 9,
        "field_name": "no_drpp",
        "label": "No DRPP",
        "data_type": "string",
        "editable": True,
        "source_priority": SourcePriority.OCR_LABELED,
        "required_for_draft": False,
        "required_for_commit": True,
        "null_policy": "conditional",
        "review_policy": "conditional",
        "description": "DRPP document number",
        "special_rules": {
            DocumentType.GUP_REGULAR: "required - REVIEW if null",
            DocumentType.GUP_PNBP: "required - REVIEW if null",
            DocumentType.GUP_KKP: "MUST BE NULL - forbidden values: -, TANPA_DRPP, N/A, empty",
        },
    },
    {
        "ordinal": 10,
        "field_name": "deskripsi",
        "label": "Deskripsi",
        "data_type": "text",
        "editable": True,
        "source_priority": SourcePriority.OCR_LABELED,
        "required_for_draft": False,
        "required_for_commit": True,
        "null_policy": "review_if_null",
        "review_policy": "if_null",
        "description": "Transaction description",
    },
    {
        "ordinal": 11,
        "field_name": "nilai_bruto",
        "label": "Nilai Bruto",
        "data_type": "decimal",
        "editable": True,
        "source_priority": SourcePriority.OCR_LABELED,
        "required_for_draft": True,
        "required_for_commit": True,
        "null_policy": "not_allowed",
        "review_policy": "not_allowed",
        "description": "Gross amount (before deductions)",
        "validation": ">= 0",
    },
    {
        "ordinal": 12,
        "field_name": "nilai_netto",
        "label": "Nilai Netto",
        "data_type": "decimal",
        "editable": True,
        "source_priority": SourcePriority.DERIVED,
        "required_for_draft": True,
        "required_for_commit": True,
        "null_policy": "not_allowed",
        "review_policy": "not_allowed",
        "description": "Net amount (after fp and pph21)",
        "derivation": "nilai_bruto - fp - pph21",
    },
    {
        "ordinal": 13,
        "field_name": "pembebanan",
        "label": "Pembebanan",
        "data_type": "string",
        "editable": True,
        "source_priority": SourcePriority.OCR_LABELED,
        "required_for_draft": True,
        "required_for_commit": True,
        "null_policy": "review_if_null",
        "review_policy": "if_null",
        "description": "Budget code (NNNN.MMM.XXX.XXX.XXXXXX)",
        "normalization": "Mixed separators normalized to dots",
    },
    {
        "ordinal": 14,
        "field_name": "fp",
        "label": "FP",
        "data_type": "decimal",
        "editable": True,
        "source_priority": SourcePriority.OCR_LABELED,
        "required_for_draft": False,
        "required_for_commit": True,
        "null_policy": "review_if_null",
        "review_policy": "if_null",
        "description": "Faktur Pajak (tax invoice) amount",
        "zero_semantics": "0 = explicit Rp0 label found; null = no evidence",
    },
    {
        "ordinal": 15,
        "field_name": "pph21",
        "label": "PPh21",
        "data_type": "decimal",
        "editable": True,
        "source_priority": SourcePriority.OCR_LABELED,
        "required_for_draft": False,
        "required_for_commit": True,
        "null_policy": "review_if_null",
        "review_policy": "if_null",
        "description": "Income tax amount",
        "zero_semantics": "0 = explicit Rp0 label found; null = no evidence",
    },
]

# =============================================================================
# IMPORT-TIME ASSERTIONS
# =============================================================================

def _assert_contract_invariants():
    """Assert contract invariants at import time."""
    errors = []

    # 1. Exactly 15 columns
    if len(COLUMNS) != 15:
        errors.append(f"Expected exactly 15 columns, got {len(COLUMNS)}")

    # 2. Ordinals 1-15
    ordinals = [c["ordinal"] for c in COLUMNS]
    if sorted(ordinals) != list(range(1, 16)):
        errors.append(f"Ordinals must be 1-15, got {ordinals}")

    # 3. Unique ordinals
    if len(set(ordinals)) != len(ordinals):
        errors.append(f"Duplicate ordinals found: {ordinals}")

    # 4. Unique field names
    field_names = [c["field_name"] for c in COLUMNS]
    if len(set(field_names)) != len(field_names):
        errors.append(f"Duplicate field names found: {field_names}")

    # 5. Each field has source, null_policy, review_policy
    required_keys = ["source_priority", "null_policy", "review_policy"]
    for c in COLUMNS:
        for key in required_keys:
            if key not in c:
                errors.append(f"Column {c['ordinal']} missing '{key}'")

    # 6. Helper is read-only
    helper_col = next((c for c in COLUMNS if c["field_name"] == "helper"), None)
    if helper_col and helper_col.get("editable") is not False:
        errors.append("Helper field must be editable=False (read-only/derived)")

    # 7. fp and pph21 have zero_semantics
    for fname in ["fp", "pph21"]:
        col = next((c for c in COLUMNS if c["field_name"] == fname), None)
        if col and "zero_semantics" not in col:
            errors.append(f"{fname} column missing 'zero_semantics' documentation")

    if errors:
        raise ImportError(f"Contract invariant violations:\n" + "\n".join(f"  - {e}" for e in errors))


# Run assertions at import time
_assert_contract_invariants()


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_column_by_ordinal(ordinal: int) -> dict:
    """Get column definition by ordinal (1-15)."""
    for col in COLUMNS:
        if col["ordinal"] == ordinal:
            return col
    raise ValueError(f"Invalid ordinal: {ordinal}. Must be 1-15.")


def get_column_by_field_name(field_name: str) -> dict:
    """Get column definition by field_name."""
    for col in COLUMNS:
        if col["field_name"] == field_name:
            return col
    raise ValueError(f"Invalid field_name: {field_name}")


def get_field_names() -> list:
    """Get list of all 15 field names in ordinal order."""
    return [c["field_name"] for c in COLUMNS]


def is_read_only(field_name: str) -> bool:
    """Check if a field is read-only (derived)."""
    col = get_column_by_field_name(field_name)
    return not col.get("editable", True)


def is_required(field_name: str, for_commit: bool = False) -> bool:
    """Check if a field is required for draft or commit."""
    col = get_column_by_field_name(field_name)
    key = "required_for_commit" if for_commit else "required_for_draft"
    return col.get(key, False)


def get_null_policy(field_name: str) -> str:
    """Get null policy for a field."""
    col = get_column_by_field_name(field_name)
    return col.get("null_policy", "not_allowed")


def get_source_priority(field_name: str) -> str:
    """Get primary source priority for a field."""
    col = get_column_by_field_name(field_name)
    return col.get("source_priority", SourcePriority.NULL_REVIEW)


# =============================================================================
# BUILD EMPTY DRAFT ROW
# =============================================================================

def build_empty_dk_draft_row() -> dict:
    """
    Build an empty D_K draft row with exactly 15 columns.

    All values start as None/null.
    - No empty strings ""
    - No placeholder "-"
    - No "TANPA_DRPP"
    - No 0 without explicit evidence

    IMPORTANT: This returns ONLY the 15 D_K columns.
    Review metadata is stored SEPARATELY via build_empty_dk_review_metadata().

    Returns:
        dict: Draft row with exactly 15 top-level keys
    """
    return {
        "helper": None,
        "akun": None,
        "bulan_sp2d": None,
        "cara_pembayaran": None,
        "nomor_spm": None,
        "tanggal_spm": None,
        "jenis_spm": None,
        "no_kuitansi": None,
        "no_drpp": None,
        "deskripsi": None,
        "nilai_bruto": None,
        "nilai_netto": None,
        "pembebanan": None,
        "fp": None,
        "pph21": None,
    }


def build_empty_dk_review_metadata() -> dict:
    """
    Build empty review metadata for a D_K draft.

    This is SEPARATE from the 15-column D_K row.
    Consumer must NOT count this as column 16.

    Returns:
        dict: Review metadata with status/source/evidence for 15 fields
    """
    field_names = get_field_names()
    return {
        "field_status": {fname: None for fname in field_names},
        "field_source": {fname: None for fname in field_names},
        "field_evidence": {fname: None for fname in field_names},
        "requires_review": False,
        "review_reasons": [],
    }


def build_empty_dk_draft() -> dict:
    """
    Build a complete D_K draft with row and metadata.

    Returns:
        dict: Container with:
            - "row": 15-column D_K draft
            - "review_metadata": Review metadata (NOT a 16th column)
    """
    return {
        "row": build_empty_dk_draft_row(),
        "review_metadata": build_empty_dk_review_metadata(),
    }


def validate_draft_row(draft: dict, for_commit: bool = False) -> list:
    """
    Validate a draft row against the contract.

    Args:
        draft: Draft row dict
        for_commit: If True, check commit requirements

    Returns:
        list: List of validation error messages (empty if valid)
    """
    errors = []

    # Check exactly 15 columns
    field_names = get_field_names()
    draft_fields = {k: v for k, v in draft.items() if k != "review_metadata"}

    missing_fields = set(field_names) - set(draft_fields.keys())
    extra_fields = set(draft_fields.keys()) - set(field_names)

    if missing_fields:
        errors.append(f"Missing fields: {missing_fields}")
    if extra_fields:
        errors.append(f"Extra fields: {extra_fields}")

    # Check forbidden values
    for fname in ["no_drpp", "no_kuitansi"]:
        if fname in draft_fields and draft_fields[fname] in FORBIDDEN_NO_DRPP_VALUES:
            errors.append(f"Forbidden value for {fname}: {draft_fields[fname]}")

    # Check required fields
    for fname in field_names:
        col = get_column_by_field_name(fname)
        key = "required_for_commit" if for_commit else "required_for_draft"
        if col.get(key, False) and draft_fields.get(fname) is None:
            errors.append(f"Required field '{fname}' is null")

    # Check helper is derived
    if "helper" in draft_fields and draft_fields["helper"] is not None:
        # Helper should be derived from akun + no_kuitansi
        expected_helper = f"{draft_fields.get('akun', '')}{draft_fields.get('no_kuitansi', '')}"
        if draft_fields["helper"] != expected_helper:
            errors.append(f"Helper should be derived: expected '{expected_helper}', got '{draft_fields['helper']}'")

    return errors


# =============================================================================
# DOCUMENT TYPE HELPERS
# =============================================================================

def validate_no_drpp_for_doc_type(no_drpp: str, doc_type: str) -> tuple:
    """
    Validate no_drpp value against document type.

    Args:
        no_drpp: The no_drpp value (may be None)
        doc_type: Document type (GUP_REGULAR, GUP_PNBP, GUP_KKP)

    Returns:
        tuple: (is_valid, error_message)
    """
    if doc_type == DocumentType.GUP_KKP:
        if no_drpp is not None:
            return False, "GUP_KKP: no_drpp must be null"
        if no_drpp in FORBIDDEN_NO_DRPP_VALUES:
            return False, f"GUP_KKP: no_drpp cannot be forbidden value: {no_drpp}"

    elif doc_type in [DocumentType.GUP_REGULAR, DocumentType.GUP_PNBP]:
        if no_drpp is None:
            return False, f"{doc_type}: no_drpp is required"

    return True, None


def validate_zero_vs_null(field_name: str, value, has_explicit_label: bool) -> tuple:
    """
    Validate zero vs null semantics.

    Args:
        field_name: Field name (fp, pph21, etc.)
        value: Current value
        has_explicit_label: Whether an explicit "Rp0" label was found

    Returns:
        tuple: (is_valid, error_message)
    """
    if field_name not in ["fp", "pph21"]:
        return True, None

    if value == 0 and not has_explicit_label:
        return False, f"{field_name}=0 requires explicit 'Rp0' label evidence"

    if value is None and has_explicit_label:
        return False, f"{field_name}=null conflicts with explicit 'Rp0' label found"

    return True, None


# =============================================================================
# EXPORT FOR TESTING
# =============================================================================

__all__ = [
    "COLUMNS",
    "EXACT_KEY_FIELDS",
    "DocumentType",
    "VALID_CARA_PEMBAYARAN",
    "FORBIDDEN_NO_DRPP_VALUES",
    "SourcePriority",
    "FieldStatus",
    "get_column_by_ordinal",
    "get_column_by_field_name",
    "get_field_names",
    "is_read_only",
    "is_required",
    "get_null_policy",
    "get_source_priority",
    "build_empty_dk_draft_row",
    "build_empty_dk_review_metadata",
    "build_empty_dk_draft",
    "validate_draft_row",
    "validate_no_drpp_for_doc_type",
    "validate_zero_vs_null",
]
