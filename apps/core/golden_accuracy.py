"""Generic golden-dataset validation for the 15 D_K columns.

This module deliberately knows nothing about fixture names, document numbers,
hashes, nominal values, or page positions.  Those belong in external manifests.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


DK_COLUMNS = (
    "helper",
    "akun",
    "bulan_sp2d",
    "cara_pembayaran",
    "nomor_spm",
    "tanggal_spm",
    "jenis_spm",
    "no_kuitansi",
    "no_drpp",
    "deskripsi",
    "nilai_bruto",
    "nilai_netto",
    "pembebanan",
    "fp",
    "pph21",
)

PROVENANCE_SOURCES = {
    "OCR",
    "PARSER_STRUCTURAL",
    "SP2D_IMPORT",
    "MASTER_AKUN",
    "MANUAL_CORRECTION",
    "COMPUTED",
}

CELL_STATUSES = {"EXACT", "MISMATCH", "MISSING", "EXTRA", "REVIEW"}
AVAILABILITY = {"PRESENT", "CONFIRMED_ABSENT", "AMBIGUOUS", "NOT_APPLICABLE"}
REVIEWER_STATUSES = {"PENDING", "APPROVED", "REJECTED"}
MONEY_COLUMNS = {"nilai_bruto", "nilai_netto", "pph21"}
SENSITIVE_COLUMNS = {"deskripsi"}
EXPECTED_CELL_FIELDS = {
    "value",
    "availability",
    "source_file",
    "source_page",
    "document_type",
    "locator",
    "reason",
}


class GoldenValidationError(ValueError):
    pass


class GoldenCorpusMissing(GoldenValidationError):
    pass


def _blank_to_none(value):
    return None if value is None or (isinstance(value, str) and not value.strip()) else value


def canonical_value(field, value):
    value = _blank_to_none(value)
    if value is None:
        return None
    if field in MONEY_COLUMNS:
        try:
            return Decimal(str(value).replace(" ", ""))
        except InvalidOperation as exc:
            raise GoldenValidationError(f"Nilai {field} bukan Decimal valid: {value!r}") from exc
    if field == "bulan_sp2d":
        try:
            month = int(value)
        except (TypeError, ValueError) as exc:
            raise GoldenValidationError(f"bulan_sp2d bukan angka valid: {value!r}") from exc
        if not 1 <= month <= 12:
            raise GoldenValidationError(f"bulan_sp2d di luar 1..12: {month}")
        return month
    if field == "tanggal_spm":
        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, date):
            return value.isoformat()
        text = str(value).strip()
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise GoldenValidationError(f"tanggal_spm bukan ISO date: {value!r}") from exc
    text = re.sub(r"\s+", " ", str(value)).strip()
    if field in {"akun", "cara_pembayaran", "nomor_spm", "jenis_spm", "no_kuitansi", "no_drpp", "pembebanan", "fp"}:
        text = text.upper()
    return text or None


def computed_helper(akun, no_kuitansi):
    return f"{canonical_value('akun', akun) or ''}{canonical_value('no_kuitansi', no_kuitansi) or ''}"


def actual_value(
    value,
    source,
    *,
    engine="",
    extraction_method="",
    confidence=None,
    source_file="",
    source_page=None,
    document_type="",
    locator="",
    inputs=None,
    review=False,
):
    envelope = {
        "value": value,
        "source": source,
        "engine": engine or "",
        "extraction_method": extraction_method or "",
        "confidence": confidence,
        "source_file": source_file or "",
        "source_page": source_page,
        "document_type": document_type or "",
        "locator": locator or "",
        "inputs": list(inputs or []),
        "review": bool(review),
    }
    validate_actual_envelope(envelope)
    return envelope


def validate_actual_envelope(envelope):
    missing = {
        "value", "source", "engine", "extraction_method", "confidence",
        "source_file", "source_page", "document_type", "locator", "inputs",
    } - set(envelope)
    if missing:
        raise GoldenValidationError("Provenance actual tidak lengkap: " + ", ".join(sorted(missing)))
    if envelope["source"] not in PROVENANCE_SOURCES:
        raise GoldenValidationError(f"Source provenance tidak valid: {envelope['source']!r}")
    confidence = envelope.get("confidence")
    if confidence is not None:
        if not envelope.get("engine") and not envelope.get("extraction_method"):
            raise GoldenValidationError("Confidence tanpa engine/extraction_method tidak sah; gunakan null.")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 100:
            raise GoldenValidationError(f"Confidence harus null atau angka 0..100: {confidence!r}")
    if not isinstance(envelope.get("inputs"), list):
        raise GoldenValidationError("inputs provenance harus list.")


def _validate_expected_columns(columns, *, location):
    if not isinstance(columns, dict):
        raise GoldenValidationError(f"{location} harus object.")
    if "helper" in columns:
        raise GoldenValidationError("Helper tidak boleh diketik di golden; nilainya selalu COMPUTED.")
    missing_columns = set(DK_COLUMNS[1:]) - set(columns)
    if missing_columns:
        raise GoldenValidationError(
            f"{location} kurang kolom: " + ", ".join(sorted(missing_columns))
        )
    for field in DK_COLUMNS[1:]:
        cell = columns[field]
        if not isinstance(cell, dict):
            raise GoldenValidationError(f"{location}.{field} harus object.")
        missing_cell_fields = EXPECTED_CELL_FIELDS - set(cell)
        if missing_cell_fields:
            raise GoldenValidationError(
                f"{location}.{field} kurang metadata: "
                + ", ".join(sorted(missing_cell_fields))
            )
        availability = cell.get("availability", "PRESENT")
        if availability not in AVAILABILITY:
            raise GoldenValidationError(f"Availability tidak valid: {availability!r}")
        if availability == "PRESENT" and canonical_value(field, cell["value"]) is None:
            raise GoldenValidationError(f"{location}.{field} PRESENT tetapi kosong.")
        if availability != "PRESENT" and canonical_value(field, cell["value"]) is not None:
            raise GoldenValidationError(f"{location}.{field} {availability} tetapi berisi nilai.")
        if not str(cell.get("source_file") or "").strip():
            raise GoldenValidationError(f"{location}.{field}.source_file wajib diisi.")
        source_page = cell.get("source_page")
        if source_page is not None and (
            isinstance(source_page, bool) or not isinstance(source_page, int) or source_page < 1
        ):
            raise GoldenValidationError(f"{location}.{field}.source_page tidak valid.")
        for key in ("document_type", "locator", "reason"):
            if not str(cell.get(key) or "").strip():
                raise GoldenValidationError(f"{location}.{field}.{key} wajib diisi.")


def _validate_parser_candidate(candidate, *, location):
    if not isinstance(candidate, dict):
        raise GoldenValidationError(f"{location} harus object.")
    for layer in ("extraction", "enrichment"):
        columns = candidate.get(layer)
        if not isinstance(columns, dict):
            raise GoldenValidationError(f"{location}.{layer} harus object.")
        missing = set(DK_COLUMNS) - set(columns)
        if missing:
            raise GoldenValidationError(
                f"{location}.{layer} kurang kolom: " + ", ".join(sorted(missing))
            )
        for field in DK_COLUMNS:
            validate_actual_envelope(columns[field])
        helper = columns["helper"]
        if helper["source"] != "COMPUTED":
            raise GoldenValidationError(f"{location}.{layer}.helper wajib COMPUTED.")


def validate_manifest(manifest):
    if manifest.get("schema_version") != 2:
        raise GoldenValidationError("schema_version golden annotation harus 2.")
    fixture = manifest.get("fixture") or {}
    for key in ("id", "filename", "sha256", "pipeline"):
        if not fixture.get(key):
            raise GoldenValidationError(f"fixture.{key} wajib diisi.")
    digest = str(fixture["sha256"]).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise GoldenValidationError("fixture.sha256 harus SHA-256 hex 64 karakter.")
    transactions = manifest.get("transactions")
    if not isinstance(transactions, list):
        raise GoldenValidationError("transactions harus list.")
    row_keys = set()
    case_ids = set()
    for transaction in transactions:
        row_key = transaction.get("row_key")
        case_id = transaction.get("case_id")
        document_id = transaction.get("document_id")
        if not row_key or row_key in row_keys:
            raise GoldenValidationError(f"row_key kosong/duplikat: {row_key!r}")
        if not case_id or case_id in case_ids:
            raise GoldenValidationError(f"case_id kosong/duplikat: {case_id!r}")
        if not document_id:
            raise GoldenValidationError(f"{case_id}.document_id wajib diisi.")
        row_keys.add(row_key)
        case_ids.add(case_id)
        reviewer_status = transaction.get("reviewer_status")
        if reviewer_status not in REVIEWER_STATUSES:
            raise GoldenValidationError(f"{case_id}.reviewer_status tidak valid: {reviewer_status!r}")
        _validate_parser_candidate(
            transaction.get("parser_candidate"), location=f"{case_id}.parser_candidate"
        )
        reviewer_expected = transaction.get("reviewer_expected")
        if not isinstance(reviewer_expected, dict):
            raise GoldenValidationError(f"{case_id}.reviewer_expected harus object.")
        for layer in ("extraction", "enrichment"):
            _validate_expected_columns(
                reviewer_expected.get(layer), location=f"{case_id}.reviewer_expected.{layer}"
            )
    return manifest


def approved_expectations(manifest):
    """Return comparator input, rejecting parser candidates not approved by a human."""
    validate_manifest(manifest)
    pending = [
        transaction["case_id"] for transaction in manifest["transactions"]
        if transaction["reviewer_status"] != "APPROVED"
    ]
    if pending:
        raise GoldenValidationError(
            "Golden acceptance hanya boleh membaca reviewer_expected APPROVED: "
            + ", ".join(pending)
        )
    return [
        {
            "row_key": transaction["row_key"],
            "case_id": transaction["case_id"],
            "document_id": transaction["document_id"],
            "extraction": transaction["reviewer_expected"]["extraction"],
            "enrichment": transaction["reviewer_expected"]["enrichment"],
        }
        for transaction in manifest["transactions"]
    ]


def load_manifest(path):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_manifest(manifest)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_fixture(manifest, fixture_dir, *, required):
    path = Path(fixture_dir) / manifest["fixture"]["filename"]
    if not path.is_file():
        if required:
            raise GoldenCorpusMissing(f"Fixture wajib tidak tersedia: {path}")
        return None
    expected_hash = manifest["fixture"]["sha256"].lower()
    actual_hash = sha256_file(path)
    if actual_hash != expected_hash:
        raise GoldenValidationError(
            f"Hash fixture berubah: {path.name}; expected={expected_hash}; actual={actual_hash}"
        )
    return path


def _expected_helper(columns):
    return {
        "value": computed_helper(columns["akun"]["value"], columns["no_kuitansi"]["value"]),
        "availability": "PRESENT",
    }


def _actual_columns(row):
    columns = dict(row.get("columns") or {})
    for field, envelope in columns.items():
        if field != "helper":
            validate_actual_envelope(envelope)
    akun = (columns.get("akun") or {}).get("value")
    kuitansi = (columns.get("no_kuitansi") or {}).get("value")
    columns["helper"] = actual_value(
        computed_helper(akun, kuitansi),
        "COMPUTED",
        extraction_method="concatenate",
        inputs=["akun", "no_kuitansi"],
    )
    return columns


def _compare_cell(field, expected, actual):
    availability = expected.get("availability", "PRESENT")
    expected_value = canonical_value(field, expected.get("value"))
    actual_value_normalized = canonical_value(field, (actual or {}).get("value"))
    if actual:
        validate_actual_envelope(actual)
    if availability == "AMBIGUOUS":
        if actual_value_normalized is None and actual and actual.get("review"):
            status = "REVIEW"
        elif actual_value_normalized is None:
            status = "MISSING"
        else:
            status = "EXTRA"
    elif availability in {"CONFIRMED_ABSENT", "NOT_APPLICABLE"}:
        status = "EXACT" if actual_value_normalized is None else "EXTRA"
    elif actual_value_normalized is None:
        status = "MISSING"
    elif expected_value == actual_value_normalized:
        status = "EXACT"
    else:
        status = "MISMATCH"
    return {
        "status": status,
        "expected": expected_value,
        "actual": actual_value_normalized,
        "availability": availability,
        "provenance": actual or None,
    }


def compare_layer(manifest, actual_rows, layer):
    if layer not in {"extraction", "enrichment"}:
        raise GoldenValidationError(f"Layer tidak valid: {layer}")
    expected_by_key = {row["row_key"]: row for row in approved_expectations(manifest)}
    actual_by_key = {}
    for row in actual_rows:
        row_key = row.get("row_key")
        if not row_key or row_key in actual_by_key:
            raise GoldenValidationError(f"Actual row_key kosong/duplikat: {row_key!r}")
        actual_by_key[row_key] = row

    comparisons = []
    for row_key in sorted(set(expected_by_key) | set(actual_by_key)):
        expected_row = expected_by_key.get(row_key)
        actual_row = actual_by_key.get(row_key)
        if expected_row:
            expected_columns = dict(expected_row[layer])
            expected_columns["helper"] = _expected_helper(expected_columns)
        else:
            expected_columns = {}
        actual_columns = _actual_columns(actual_row) if actual_row else {}
        case_id = expected_row.get("case_id") if expected_row else None
        document_id = expected_row.get("document_id") if expected_row else row_key
        for field in DK_COLUMNS:
            if not expected_row:
                actual = actual_columns.get(field)
                if actual and canonical_value(field, actual.get("value")) is not None:
                    result = {
                        "status": "EXTRA", "expected": None,
                        "actual": canonical_value(field, actual.get("value")),
                        "availability": "NOT_APPLICABLE", "provenance": actual,
                    }
                else:
                    continue
            elif not actual_row:
                expected = expected_columns[field]
                availability = expected.get("availability", "PRESENT")
                result = {
                    "status": "REVIEW" if availability == "AMBIGUOUS" else "MISSING",
                    "expected": canonical_value(field, expected.get("value")),
                    "actual": None,
                    "availability": availability,
                    "provenance": None,
                }
            else:
                result = _compare_cell(field, expected_columns[field], actual_columns.get(field))
            comparisons.append({
                "layer": layer,
                "row_key": row_key,
                "case_id": case_id,
                "document_id": document_id,
                "field": field,
                **result,
            })
    return comparisons


def _accuracy(comparisons):
    eligible = sum(item["status"] in {"EXACT", "MISMATCH", "MISSING"} for item in comparisons)
    exact = sum(item["status"] == "EXACT" for item in comparisons)
    return round(exact / eligible * 100, 4) if eligible else None


def summarize(comparisons):
    per_column = {}
    grouped = defaultdict(list)
    for item in comparisons:
        grouped[item["field"]].append(item)
    for field in DK_COLUMNS:
        cells = grouped.get(field, [])
        per_column[field] = {
            "counts": dict(Counter(cell["status"] for cell in cells)),
            "exact_accuracy": _accuracy(cells),
        }
    return {
        "counts": dict(Counter(item["status"] for item in comparisons)),
        "exact_accuracy": _accuracy(comparisons),
        "per_column": per_column,
    }


def _fingerprint(value):
    if value is None:
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def redact_comparison(item):
    output = dict(item)
    provenance = dict(output.get("provenance") or {})
    provenance.pop("value", None)
    output["provenance"] = provenance or None
    if item["field"] in SENSITIVE_COLUMNS:
        output["expected_fingerprint"] = _fingerprint(item.get("expected"))
        output["actual_fingerprint"] = _fingerprint(item.get("actual"))
        output["expected"] = "[REDACTED]" if item.get("expected") is not None else None
        output["actual"] = "[REDACTED]" if item.get("actual") is not None else None
        if output.get("provenance"):
            output["provenance"]["locator"] = "[REDACTED]"
    else:
        output["expected"] = _json_scalar(output.get("expected"))
        output["actual"] = _json_scalar(output.get("actual"))
    return output


def _json_scalar(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def summarize_by_document(comparisons):
    grouped = defaultdict(list)
    for item in comparisons:
        grouped[item["document_id"]].append(item)
    return {document_id: summarize(cells) for document_id, cells in sorted(grouped.items())}


def build_report(manifest, extraction_rows, enrichment_rows, *, run_metrics=None):
    extraction = compare_layer(manifest, extraction_rows, "extraction")
    enrichment = compare_layer(manifest, enrichment_rows, "enrichment")
    mismatches = [
        redact_comparison(item)
        for item in extraction + enrichment
        if item["status"] in {"MISMATCH", "MISSING", "EXTRA"}
    ]
    return {
        "schema_version": 2,
        "fixture": {
            "id": manifest["fixture"]["id"],
            "filename": manifest["fixture"]["filename"],
            "sha256": manifest["fixture"]["sha256"].lower(),
            "pipeline": manifest["fixture"]["pipeline"],
        },
        "transactions": {
            "expected": len(manifest["transactions"]),
            "actual_extraction": len(extraction_rows),
            "actual_enrichment": len(enrichment_rows),
        },
        "extraction": summarize(extraction),
        "enrichment": summarize(enrichment),
        "per_document": {
            "extraction": summarize_by_document(extraction),
            "enrichment": summarize_by_document(enrichment),
        },
        "mismatches": mismatches,
        "metrics": dict(run_metrics or {}),
    }


def _pending_expected_cell(field, candidate, fixture):
    candidate = candidate or {}
    return {
        "value": None,
        "availability": "AMBIGUOUS",
        "source_file": candidate.get("source_file") or fixture["filename"],
        "source_page": candidate.get("source_page"),
        "document_type": candidate.get("document_type") or str(fixture["pipeline"]).upper(),
        "locator": candidate.get("locator") or field,
        "reason": "PENDING_REVIEW: verify value and evidence against the source document.",
    }


def build_annotation_draft(fixture, extraction_rows, enrichment_rows):
    """Create an external reviewer worksheet without promoting parser output to truth."""
    extraction_by_key = {row["row_key"]: row for row in extraction_rows}
    enrichment_by_key = {row["row_key"]: row for row in enrichment_rows}
    row_keys = sorted(set(extraction_by_key) | set(enrichment_by_key))
    transactions = []
    for index, row_key in enumerate(row_keys, start=1):
        extraction = _actual_columns(extraction_by_key[row_key])
        enrichment = _actual_columns(enrichment_by_key[row_key])
        reviewer_expected = {}
        for layer, columns in (("extraction", extraction), ("enrichment", enrichment)):
            reviewer_expected[layer] = {
                field: _pending_expected_cell(field, columns.get(field), fixture)
                for field in DK_COLUMNS[1:]
            }
        transactions.append({
            "row_key": row_key,
            "case_id": f"review-row-{index:04d}",
            "document_id": fixture["id"],
            "parser_candidate": {"extraction": extraction, "enrichment": enrichment},
            "reviewer_expected": reviewer_expected,
            "reviewer_status": "PENDING",
        })
    draft = {"schema_version": 2, "fixture": dict(fixture), "transactions": transactions}
    return validate_manifest(draft)
