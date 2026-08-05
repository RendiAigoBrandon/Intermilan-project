"""
dk_draft_adapter.py

Adapter that converts parser output to 15-column D_K drafts.

This module transforms parsed_data from parse_drpp_upload_batch() into D_K draft rows
with exactly 15 columns following the domain contract.

SOURCE OF TRUTH:
- docs/DOMAIN_CONTRACT_15_COLUMNS.md
- apps/core/dk_domain_contract.py

FUNCTIONS:
- build_dk_drafts_from_parsed_data(): Main adapter function
- build_empty_dk_draft(): From dk_domain_contract.py

STRUCTURE:
- "row": exactly 15 D_K columns (from dk_domain_contract)
- "review_metadata": separate metadata (NOT column 16)
- "source_transaction_index": index into parsed_data.kw_items
- "raw_evidence": original parser evidence (preserved separately)
"""

from decimal import Decimal
from typing import Any, Optional

from apps.core.dk_domain_contract import (
    build_empty_dk_draft,
    build_empty_dk_draft_row,
    build_empty_dk_review_metadata,
    DocumentType,
    SourcePriority,
)


# =============================================================================
# SOURCE PRIORITY CONSTANTS
# =============================================================================

class DraftSource:
    """Source of a field value, in priority order (highest first)."""
    MANUAL_CONFIRMED = "manual_confirmed"
    OCR_LABELED = "ocr_labeled"
    SP2D_ENRICHMENT = "sp2d_enrichment"
    DERIVED = "derived"
    NULL_REVIEW = "null_review"
    NOT_APPLICABLE = "not_applicable"


class DraftStatus:
    """Status of a field."""
    EXACT = "EXACT"
    DERIVED = "DERIVED"
    MANUAL_CONFIRMED = "MANUAL_CONFIRMED"
    REVIEW = "REVIEW"
    MISSING = "MISSING"
    NOT_APPLICABLE = "NOT_APPLICABLE"


# Canonical Jenis SPM values
CANONICAL_JENIS_SPM = {"GUP_REGULAR", "GUP_PNBP", "GUP_KKP"}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _is_forbidden(value: Any) -> bool:
    """Check if value is forbidden (empty string, dash, TANPA_DRPP)."""
    if value is None:
        return False
    text = str(value).strip()
    return text in ("", "-", "TANPA_DRPP", "N/A")


def _validate_jenis_spm(value: Optional[str]) -> tuple:
    """
    Validate jenis_spm against canonical values.

    Returns:
        (normalized_value, is_canonical)
        value=None if not canonical.
    """
    if not value:
        return None, False
    normalized = str(value).strip().upper()
    if normalized in CANONICAL_JENIS_SPM:
        return normalized, True
    # Not canonical - return None to trigger REVIEW
    return None, False


def _derive_helper_status(row: dict) -> DraftStatus:
    """
    Derive helper status.

    Returns REVIEW when exact key incomplete, NOT NOT_APPLICABLE.
    NOT_APPLICABLE only for fields that are domain-inapplicable (e.g., no_drpp for GUP_KKP).
    """
    akun = row.get("akun")
    kuitansi = row.get("no_kuitansi")
    if akun and kuitansi:
        return DraftStatus.DERIVED
    return DraftStatus.REVIEW


def _normalize_decimal(value: Any) -> Optional[Decimal]:
    """Convert value to Decimal or None."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _normalize_string(value: Any) -> Optional[str]:
    """Normalize string value or return None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in ("-", "TANPA_DRPP", "N/A"):
        return None
    return text


def _is_explicit_zero(value: Any) -> bool:
    """Check if value represents an explicit zero (Decimal(0))."""
    if isinstance(value, Decimal) and value == Decimal("0"):
        return True
    if isinstance(value, (int, float)) and value == 0:
        return True
    return False


# =============================================================================
# MAIN ADAPTER FUNCTION
# =============================================================================

def build_dk_drafts_from_parsed_data(
    parsed_data: dict,
    *,
    satker: Optional[str] = None,
    tahun: Optional[int] = None,
    sp2d_match: Optional[dict] = None,
) -> list[dict]:
    """
    Convert parsed_data from parse_drpp_upload_batch() to 15-column D_K drafts.

    Args:
        parsed_data: Output from parse_drpp_upload_batch()
        satker: Satker code override
        tahun: Tahun override
        sp2d_match: SP2D exact-match enrichment data

    Returns:
        List of draft dicts, each containing:
        {
            "row": 15-column D_K row (build_empty_dk_draft_row structure),
            "review_metadata": separate metadata dict,
            "source_transaction_index": int,
            "raw_evidence": dict,
        }
    """
    drafts = []

    # Extract document-level fields
    document_fields = _extract_document_fields(parsed_data, satker=satker, tahun=tahun, sp2d_match=sp2d_match)

    # Process each transaction row
    kw_items = parsed_data.get("kw_items", [])
    for idx, kw_item in enumerate(kw_items):
        draft = _build_single_draft(
            kw_item=kw_item,
            document_fields=document_fields,
            source_index=idx,
        )
        drafts.append(draft)

    return drafts


def _extract_document_fields(
    parsed_data: dict,
    *,
    satker: Optional[str] = None,
    tahun: Optional[int] = None,
    sp2d_match: Optional[dict] = None,
) -> dict:
    """Extract document-level fields for enrichment."""
    fields = {
        "satker": satker or parsed_data.get("satker", ""),
        "tahun": tahun or parsed_data.get("tahun"),
        "nomor_spm": None,
        "tanggal_spm": None,
        "jenis_spm": None,
        "cara_pembayaran": None,
        "bulan_sp2d": None,
        "no_drpp": None,
        "spm_source": None,  # Track source of SPM fields
    }

    # Get SPM data - fields are in metadata dict
    spm_data = parsed_data.get("spm")
    spm_meta = (spm_data.get("metadata") or {}) if spm_data else {}
    if spm_meta:
        fields["nomor_spm"] = _normalize_string(spm_meta.get("nomor_spm"))
        fields["tanggal_spm"] = spm_meta.get("tanggal_spm")  # Date object
        fields["jenis_spm"] = _normalize_string(spm_meta.get("jenis_spm"))
        fields["cara_pembayaran"] = _normalize_string(spm_meta.get("cara_pembayaran"))
        fields["bulan_sp2d"] = spm_meta.get("bulan_sp2d")
        fields["spm_source"] = "parsed_data"
    elif spm_data:
        # Fallback to direct fields if no metadata
        fields["nomor_spm"] = _normalize_string(spm_data.get("nomor_spm"))
        fields["tanggal_spm"] = spm_data.get("tanggal_spm")
        fields["jenis_spm"] = _normalize_string(spm_data.get("jenis_spm"))
        fields["cara_pembayaran"] = _normalize_string(spm_data.get("cara_pembayaran"))
        fields["bulan_sp2d"] = spm_data.get("bulan_sp2d")
        fields["spm_source"] = "parsed_data"

    # Get DRPP data
    drpp_data = parsed_data.get("drpp")
    if drpp_data and isinstance(drpp_data, dict):
        drpp_meta = drpp_data.get("metadata") or {}
        drpp_items = drpp_data.get("items", [])
        if drpp_items:
            # Get DRPP number from first item (already canonical format)
            first_item = drpp_items[0]
            fields["no_drpp"] = _normalize_string(first_item.get("no_drpp"))
        elif drpp_meta.get("nomor_drpp"):
            # Fallback to metadata
            fields["no_drpp"] = _normalize_string(drpp_meta.get("nomor_drpp"))

    # Get bulan from SP2D match if available
    if sp2d_match and isinstance(sp2d_match, dict):
        fields["bulan_sp2d"] = sp2d_match.get("bulan")
        fields["cara_pembayaran"] = _normalize_string(sp2d_match.get("cara_pembayaran"))

    # CRITICAL: tanggal_spm MUST NOT come from tgl_sp2d
    # If only SP2D date available, leave tanggal_spm as None + REVIEW

    return fields


def _build_single_draft(
    kw_item: dict,
    document_fields: dict,
    source_index: int,
) -> dict:
    """Build a single 15-column D_K draft from a kw_item."""
    # Start with empty draft
    draft = build_empty_dk_draft()

    row = draft["row"]
    metadata = draft["review_metadata"]

    # Track evidence
    raw_evidence = {
        "source_transaction_index": source_index,
        "original_kw_item": dict(kw_item),  # Preserve original
    }

    # Set document-level fields
    _set_document_fields(row, metadata, document_fields)

    # Set transaction-level fields
    _set_transaction_fields(row, metadata, kw_item, document_fields, raw_evidence)

    # Derive helper (only if exact key components available)
    _derive_helper(row, metadata)

    # Determine requires_review
    metadata["requires_review"] = any(
        status in (DraftStatus.REVIEW, DraftStatus.MISSING)
        for status in metadata["field_status"].values()
    )

    return {
        "row": row,
        "review_metadata": metadata,
        "source_transaction_index": source_index,
        "raw_evidence": raw_evidence,
    }


def _set_document_fields(
    row: dict,
    metadata: dict,
    doc_fields: dict,
) -> None:
    """Set document-level fields into the draft row."""
    fname = "nomor_spm"
    value = doc_fields.get("nomor_spm")
    if value:
        row[fname] = value
        metadata["field_status"][fname] = DraftStatus.EXACT
        metadata["field_source"][fname] = DraftSource.SP2D_ENRICHMENT
    else:
        metadata["field_status"][fname] = DraftStatus.MISSING
        metadata["field_source"][fname] = DraftSource.NULL_REVIEW
        metadata["review_reasons"].append(f"{fname}: no evidence")

    fname = "tanggal_spm"
    value = doc_fields.get("tanggal_spm")
    # CRITICAL: tanggal_spm must NOT come from tgl_sp2d
    spm_source = doc_fields.get("spm_source")
    if value and spm_source == "parsed_data":
        row[fname] = value
        metadata["field_status"][fname] = DraftStatus.EXACT
        metadata["field_source"][fname] = DraftSource.SP2D_ENRICHMENT
        metadata["field_evidence"][fname] = "from SPM document"
    else:
        metadata["field_status"][fname] = DraftStatus.MISSING
        metadata["field_source"][fname] = DraftSource.NULL_REVIEW
        metadata["review_reasons"].append(f"{fname}: no valid SPM date")

    fname = "jenis_spm"
    raw_value = doc_fields.get("jenis_spm")
    canonical_value, is_canonical = _validate_jenis_spm(raw_value)
    if canonical_value:
        row[fname] = canonical_value
        metadata["field_status"][fname] = DraftStatus.EXACT
        metadata["field_source"][fname] = DraftSource.SP2D_ENRICHMENT
    else:
        metadata["field_status"][fname] = DraftStatus.MISSING
        metadata["field_source"][fname] = DraftSource.NULL_REVIEW
        metadata["review_reasons"].append(f"{fname}: not canonical value (got '{raw_value}')")

    fname = "cara_pembayaran"
    value = doc_fields.get("cara_pembayaran")
    if value:
        row[fname] = value
        metadata["field_status"][fname] = DraftStatus.EXACT
        metadata["field_source"][fname] = DraftSource.SP2D_ENRICHMENT
    else:
        metadata["field_status"][fname] = DraftStatus.MISSING
        metadata["field_source"][fname] = DraftSource.NULL_REVIEW
        metadata["review_reasons"].append(f"{fname}: no evidence")

    fname = "bulan_sp2d"
    value = doc_fields.get("bulan_sp2d")
    if value:
        row[fname] = value
        metadata["field_status"][fname] = DraftStatus.EXACT
        metadata["field_source"][fname] = DraftSource.SP2D_ENRICHMENT
    # bulan_sp2d can be null without REVIEW (optional field)

    fname = "no_drpp"
    raw_value = doc_fields.get("no_drpp")
    jenis_spm_raw = doc_fields.get("jenis_spm")
    jenis_spm, _ = _validate_jenis_spm(jenis_spm_raw)

    if jenis_spm == "GUP_KKP":
        row[fname] = None  # GUP_KKP always null
        metadata["field_status"][fname] = DraftStatus.NOT_APPLICABLE
        metadata["field_source"][fname] = DraftSource.NOT_APPLICABLE
    elif raw_value:
        row[fname] = raw_value
        metadata["field_status"][fname] = DraftStatus.EXACT
        metadata["field_source"][fname] = DraftSource.OCR_LABELED
    else:
        row[fname] = None
        if jenis_spm in ("GUP_REGULAR", "GUP_PNBP"):
            metadata["field_status"][fname] = DraftStatus.REVIEW
            metadata["field_source"][fname] = DraftSource.NULL_REVIEW
            metadata["review_reasons"].append(f"{fname}: required for {jenis_spm}, no evidence")


def _set_transaction_fields(
    row: dict,
    metadata: dict,
    kw_item: dict,
    doc_fields: dict,
    raw_evidence: dict,
) -> None:
    """Set transaction-level fields from kw_item."""
    # Akun
    fname = "akun"
    value = _normalize_string(kw_item.get("akun"))
    if value:
        row[fname] = value
        metadata["field_status"][fname] = DraftStatus.EXACT
        metadata["field_source"][fname] = DraftSource.OCR_LABELED
    else:
        metadata["field_status"][fname] = DraftStatus.MISSING
        metadata["field_source"][fname] = DraftSource.NULL_REVIEW
        metadata["review_reasons"].append(f"{fname}: no evidence")

    # No Kuitansi
    fname = "no_kuitansi"
    raw_receipt = kw_item.get("no_kuitansi", "")
    raw_receipt_raw = kw_item.get("_raw_receipt") or raw_receipt

    # Check if receipt is valid
    receipt_valid = kw_item.get("receipt_valid")
    if receipt_valid is False:
        # Invalid receipt - preserve raw token as evidence, normalized to null
        raw_evidence["invalid_receipt_token"] = raw_receipt_raw
        row[fname] = None
        metadata["field_status"][fname] = DraftStatus.REVIEW
        metadata["field_source"][fname] = DraftSource.NULL_REVIEW
        metadata["field_evidence"][fname] = f"invalid_raw: {raw_receipt_raw}"
        metadata["review_reasons"].append(f"{fname}: invalid receipt, raw={raw_receipt_raw}")
    else:
        value = _normalize_string(raw_receipt)
        if value:
            row[fname] = value
            metadata["field_status"][fname] = DraftStatus.EXACT
            metadata["field_source"][fname] = DraftSource.OCR_LABELED
            raw_evidence["receipt_token"] = value
        else:
            metadata["field_status"][fname] = DraftStatus.MISSING
            metadata["field_source"][fname] = DraftSource.NULL_REVIEW
            metadata["review_reasons"].append(f"{fname}: no evidence")

    # Deskripsi
    fname = "deskripsi"
    value = _normalize_string(kw_item.get("deskripsi"))
    if value:
        row[fname] = value
        metadata["field_status"][fname] = DraftStatus.EXACT
        metadata["field_source"][fname] = DraftSource.OCR_LABELED
    else:
        metadata["field_status"][fname] = DraftStatus.REVIEW
        metadata["field_source"][fname] = DraftSource.NULL_REVIEW
        metadata["review_reasons"].append(f"{fname}: no evidence")

    # Nilai Bruto
    fname = "nilai_bruto"
    raw_value = kw_item.get("nilai_bruto")
    decimal_value = _normalize_decimal(raw_value)
    if decimal_value is not None:
        row[fname] = decimal_value
        metadata["field_status"][fname] = DraftStatus.EXACT
        metadata["field_source"][fname] = DraftSource.OCR_LABELED
    else:
        metadata["field_status"][fname] = DraftStatus.MISSING
        metadata["field_source"][fname] = DraftSource.NULL_REVIEW
        metadata["review_reasons"].append(f"{fname}: no evidence")

    # Nilai Netto (derived from bruto - fp - pph21)
    fname = "nilai_netto"
    bruto = _normalize_decimal(kw_item.get("nilai_bruto"))
    fp_val = _normalize_decimal(kw_item.get("fp"))
    pph_val = _normalize_decimal(kw_item.get("pph21"))

    if bruto is not None:
        deduction = Decimal("0")
        if fp_val:
            deduction += fp_val
        if pph_val:
            deduction += pph_val
        netto = bruto - deduction
        row[fname] = netto
        metadata["field_status"][fname] = DraftStatus.DERIVED
        metadata["field_source"][fname] = DraftSource.DERIVED
        metadata["field_evidence"][fname] = f"bruto={bruto} - fp={fp_val or 0} - pph21={pph_val or 0}"
    else:
        metadata["field_status"][fname] = DraftStatus.MISSING
        metadata["field_source"][fname] = DraftSource.NULL_REVIEW
        metadata["review_reasons"].append(f"{fname}: no bruto evidence")


    # Pembebanan
    fname = "pembebanan"
    raw_pemb = kw_item.get("pembebanan")
    canonical_pemb = kw_item.get("pembebanan_canonical") or raw_pemb
    normalized_pemb = _normalize_string(canonical_pemb)

    # Validate pembebanan format (NNNN.MMM.XXX.XXX.XXXXXX)
    # Must match KODE_BELANJA pattern
    import re
    pembebanan_pattern = re.compile(r'^\d{4}\.[A-Z]+\.\d+\.\d+\.\d+$')
    is_valid_pembebanan = bool(normalized_pemb and pembebanan_pattern.match(normalized_pemb.upper()))

    if normalized_pemb and not _is_forbidden(normalized_pemb) and is_valid_pembebanan:
        row[fname] = normalized_pemb
        metadata["field_status"][fname] = DraftStatus.EXACT
        metadata["field_source"][fname] = DraftSource.OCR_LABELED
        raw_evidence["pembebanan_canonical"] = normalized_pemb
        if raw_pemb and raw_pemb != normalized_pemb:
            raw_evidence["pembebanan_raw"] = raw_pemb
    else:
        metadata["field_status"][fname] = DraftStatus.REVIEW
        metadata["field_source"][fname] = DraftSource.NULL_REVIEW
        metadata["review_reasons"].append(f"{fname}: no valid evidence")
        raw_evidence["pembebanan_raw"] = raw_pemb

    # FP (Faktur Pajak)
    fname = "fp"
    raw_fp = kw_item.get("fp")
    has_fp_label = kw_item.get("_has_fp_label")  # Evidence of explicit "Rp0" label

    if raw_fp is not None:
        decimal_fp = _normalize_decimal(raw_fp)
        if decimal_fp is not None:
            if decimal_fp == 0 and not has_fp_label:
                # Value is 0 but no explicit label - treat as missing
                row[fname] = None
                metadata["field_status"][fname] = DraftStatus.REVIEW
                metadata["field_source"][fname] = DraftSource.NULL_REVIEW
                metadata["review_reasons"].append(f"{fname}: value 0 without explicit label")
            elif decimal_fp == Decimal("0") and has_fp_label:
                # Explicit Rp0 label
                row[fname] = Decimal("0")
                metadata["field_status"][fname] = DraftStatus.EXACT
                metadata["field_source"][fname] = DraftSource.OCR_LABELED
                metadata["field_evidence"][fname] = "explicit Rp0 label"
            else:
                row[fname] = decimal_fp
                metadata["field_status"][fname] = DraftStatus.EXACT
                metadata["field_source"][fname] = DraftSource.OCR_LABELED
    else:
        metadata["field_status"][fname] = DraftStatus.REVIEW
        metadata["field_source"][fname] = DraftSource.NULL_REVIEW
        metadata["review_reasons"].append(f"{fname}: no evidence")

    # PPh21
    fname = "pph21"
    raw_pph = kw_item.get("pph21")
    has_pph_label = kw_item.get("_has_pph21_label")

    if raw_pph is not None:
        decimal_pph = _normalize_decimal(raw_pph)
        if decimal_pph is not None:
            if decimal_pph == 0 and not has_pph_label:
                row[fname] = None
                metadata["field_status"][fname] = DraftStatus.REVIEW
                metadata["field_source"][fname] = DraftSource.NULL_REVIEW
                metadata["review_reasons"].append(f"{fname}: value 0 without explicit label")
            elif decimal_pph == Decimal("0") and has_pph_label:
                row[fname] = Decimal("0")
                metadata["field_status"][fname] = DraftStatus.EXACT
                metadata["field_source"][fname] = DraftSource.OCR_LABELED
                metadata["field_evidence"][fname] = "explicit Rp0 label"
            else:
                row[fname] = decimal_pph
                metadata["field_status"][fname] = DraftStatus.EXACT
                metadata["field_source"][fname] = DraftSource.OCR_LABELED
    else:
        metadata["field_status"][fname] = DraftStatus.REVIEW
        metadata["field_source"][fname] = DraftSource.NULL_REVIEW
        metadata["review_reasons"].append(f"{fname}: no evidence")


def _derive_helper(
    row: dict,
    metadata: dict,
) -> None:
    """
    Derive helper field only when exact key components are available.

    Helper = akun + no_kuitansi
    But only if BOTH are valid (not None, not missing).
    """
    akun = row.get("akun")
    no_kuitansi = row.get("no_kuitansi")

    if akun and no_kuitansi:
        helper = f"{akun}{no_kuitansi}"
        row["helper"] = helper
        metadata["field_status"]["helper"] = DraftStatus.DERIVED
        metadata["field_source"]["helper"] = DraftSource.DERIVED
    else:
        # Helper uses REVIEW when key incomplete (NOT_APPLICABLE reserved for domain-inapplicable)
        row["helper"] = None
        metadata["field_status"]["helper"] = DraftStatus.REVIEW
        metadata["field_source"]["helper"] = DraftSource.NULL_REVIEW
        metadata["review_reasons"].append("helper: akun or kuitansi incomplete")


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "DraftSource",
    "DraftStatus",
    "build_dk_drafts_from_parsed_data",
]
