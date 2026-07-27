"""Read-only probes for real golden fixtures.

Fixture-specific expectations stay in ``golden/corpus_manifest.json``.  This
module only dispatches by document container type and never branches on a
sample name, hash, document number, amount, page, or case id.
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from django.conf import settings

from apps.core.drpp_batch_parser import parse_drpp_upload_batch
from apps.core.golden_accuracy import DK_COLUMNS, actual_value, computed_helper, sha256_file
from apps.core.parsers import parse_spm_pdf
from apps.paket_spm.models import PaketSPMUpload
from apps.paket_spm.services import build_transaction_rows_from_package


def _fingerprint(value):
    if not value:
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _json_value(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _cache_roots(path):
    return (
        Path(settings.MEDIA_ROOT) / "ocr_cache" / "drpp_batch",
        Path(path).resolve().parent / ".ocr_cache",
    )


def cache_snapshot(path):
    return {
        item.resolve()
        for root in _cache_roots(path)
        if root.is_dir()
        for item in root.rglob("*.json")
        if item.is_file()
    }


def remove_new_cache_files(path, before):
    after = cache_snapshot(path)
    created = sorted(after - before)
    removed = []
    for item in created:
        item.unlink(missing_ok=True)
        removed.append(str(item))
    return removed


def _page_source(parsed, item, drpp):
    source_pages = item.get("source_pages") or {}
    candidates = []
    if isinstance(source_pages, dict):
        candidates.extend(page for page in source_pages.values() if isinstance(page, dict))
    if not candidates:
        candidates.extend(page for page in drpp.get("source_pages", []) if isinstance(page, dict))
    source = candidates[0] if candidates else {}
    file_name = source.get("file_name") or drpp.get("file_name") or ""
    page_number = source.get("page_number")
    indexed = next(
        (
            page for page in parsed.get("page_index", [])
            if page.get("file_name") == file_name and page.get("page_number") == page_number
        ),
        {},
    )
    engine = indexed.get("engine") or ""
    source_name = "OCR" if engine and engine != "native_pdf" else "PARSER_STRUCTURAL"
    method = indexed.get("extraction_method") or ("page_ocr" if source_name == "OCR" else "document_structure")
    confidence = indexed.get("confidence") if source_name == "OCR" else None
    return {
        "source": source_name,
        "engine": engine,
        "extraction_method": method,
        "confidence": confidence if isinstance(confidence, (int, float)) else None,
        "source_file": file_name,
        "source_page": page_number,
        "document_type": indexed.get("document_type") or "DRPP",
    }


def _source_from_index(parsed, *, file_name="", page_number=None, document_type="", extraction_method=""):
    indexed = next(
        (
            page for page in parsed.get("page_index", []) or parsed.get("page_details", [])
            if (not file_name or page.get("file_name") == file_name)
            and (page.get("page_number") or page.get("page")) == page_number
        ),
        {},
    )
    engine = indexed.get("engine") or indexed.get("method") or ""
    source = "OCR" if engine not in {"", "text", "native_pdf", "pymupdf", "pdfplumber", "pypdf"} else "PARSER_STRUCTURAL"
    confidence = indexed.get("confidence") if source == "OCR" else None
    return {
        "source": source,
        "engine": engine,
        "extraction_method": extraction_method or indexed.get("extraction_method") or indexed.get("method") or "document_structure",
        "confidence": confidence if isinstance(confidence, (int, float)) else None,
        "source_file": file_name,
        "source_page": page_number,
        "document_type": document_type or indexed.get("document_type") or "",
    }


def _batch_spm_source(parsed, field):
    spm = parsed.get("spm") or {}
    metadata = spm.get("metadata") or {}
    source_hint = (metadata.get("field_sources") or {}).get(field) or {}
    page_number = source_hint.get("page") if isinstance(source_hint, dict) else None
    if not page_number:
        page_number = next(
            (
                page.get("page_number") for page in parsed.get("page_index", [])
                if page.get("document_type") == "SPM"
            ),
            None,
        )
    return _source_from_index(
        parsed,
        file_name=spm.get("file_name") or "",
        page_number=page_number,
        document_type="SPM",
        extraction_method=(source_hint.get("method") if isinstance(source_hint, dict) else "") or "spm_parent_resolution",
    )


def _batch_columns(parsed, group, item):
    drpp = group.get("drpp") or {}
    detail_source = _page_source(parsed, item, drpp)
    values = {
        "akun": item.get("akun"),
        "bulan_sp2d": item.get("bulan_sp2d"),
        "cara_pembayaran": item.get("cara_pembayaran"),
        "nomor_spm": item.get("nomor_spm"),
        "tanggal_spm": item.get("tanggal_spm"),
        "jenis_spm": item.get("jenis_spm"),
        "no_kuitansi": item.get("no_kuitansi"),
        "no_drpp": item.get("no_drpp"),
        "deskripsi": item.get("deskripsi"),
        "nilai_bruto": item.get("nilai_bruto"),
        "nilai_netto": item.get("nilai_netto"),
        "pembebanan": item.get("pembebanan"),
        "fp": item.get("fp"),
        "pph21": item.get("pph21"),
    }
    spm_fields = {"cara_pembayaran", "nomor_spm", "tanggal_spm", "jenis_spm"}
    columns = {}
    for field, value in values.items():
        if field in spm_fields:
            source = _batch_spm_source(parsed, field)
        elif field == "bulan_sp2d":
            source = {
                "source": "SP2D_IMPORT" if value not in {None, ""} else "PARSER_STRUCTURAL",
                "engine": "",
                "extraction_method": "sp2d_parent_month" if value not in {None, ""} else "confirmed_absent_sp2d",
                "confidence": None,
                "source_file": "",
                "source_page": None,
                "document_type": "SP2D",
            }
        else:
            source = detail_source
        columns[field] = actual_value(value, locator=field, **source)
    columns["helper"] = actual_value(
        computed_helper(values["akun"], values["no_kuitansi"]),
        "COMPUTED",
        extraction_method="concatenate",
        locator="akun + no_kuitansi",
        inputs=["akun", "no_kuitansi"],
    )
    return columns


def _source_counts(rows):
    return dict(Counter(
        envelope["source"]
        for row in rows
        for envelope in row["columns"].values()
    ))


def _redacted_rows(rows):
    output = []
    source_counts = Counter()
    for row in rows:
        columns = row["columns"]
        for envelope in columns.values():
            source_counts[envelope["source"]] += 1
        public_columns = {}
        for field in DK_COLUMNS:
            envelope = dict(columns[field])
            envelope["value"] = _json_value(envelope.get("value"))
            if field == "deskripsi":
                envelope["value_fingerprint"] = _fingerprint(envelope.get("value"))
                envelope["value"] = "[REDACTED]" if envelope.get("value") else None
                envelope["locator"] = "[REDACTED]"
            public_columns[field] = envelope
        output.append({"row_key": row["row_key"], "columns": public_columns})
    return output, dict(source_counts)


def _spm_field_source(parsed, detail_item, field):
    metadata = parsed.get("metadata") or {}
    detail_map = {
        "akun": "akun", "deskripsi": "keperluan", "nilai_bruto": "bruto",
        "nilai_netto": "netto", "pembebanan": "pembebanan", "pph21": "pph21",
    }
    detail_key = detail_map.get(field)
    detail_provenance = (detail_item.get("field_provenance") or {}).get(detail_key) if detail_key else None
    if detail_key:
        page_number = (detail_provenance or {}).get("page") or detail_item.get("source_page")
        source = _source_from_index(
            parsed,
            page_number=page_number,
            document_type="DETAIL_SPP_SPM_SP2D",
            extraction_method=(detail_provenance or {}).get("method") or detail_item.get("source_priority") or "structured_detail_row",
        )
        if detail_provenance and detail_provenance.get("confidence") is None:
            source["confidence"] = None
        return source

    if field == "bulan_sp2d":
        return {
            "source": "COMPUTED",
            "engine": "",
            "extraction_method": "month_from_sp2d_date",
            "confidence": None,
            "source_file": "",
            "source_page": None,
            "document_type": "SP2D",
        }
    if field in {"no_kuitansi", "no_drpp"}:
        return {
            "source": "PARSER_STRUCTURAL",
            "engine": "",
            "extraction_method": "confirmed_absent_spm_only",
            "confidence": None,
            "source_file": parsed.get("file_name") or "",
            "source_page": None,
            "document_type": "SPM",
        }

    metadata_keys = {
        "cara_pembayaran": "cara_pembayaran", "nomor_spm": "nomor_spm",
        "tanggal_spm": "tanggal_spm", "jenis_spm": "jenis_spm", "fp": "fp",
    }
    metadata_key = metadata_keys.get(field, field)
    source_hint = (metadata.get("field_sources") or {}).get(metadata_key) or {}
    page_number = source_hint.get("page") if isinstance(source_hint, dict) else None
    if not page_number:
        page_number = next(iter(metadata.get("spm_page_nums") or []), None)
    return _source_from_index(
        parsed,
        file_name=parsed.get("file_name") or "",
        page_number=page_number,
        document_type="SPM",
        extraction_method=(source_hint.get("method") if isinstance(source_hint, dict) else "") or "spm_field",
    )


def _spm_columns(parsed, row, detail_item):
    values = {
        "akun": row.akun,
        "bulan_sp2d": row.bulan_sp2d,
        "cara_pembayaran": row.cara_pembayaran,
        "nomor_spm": row.nomor_spm,
        "tanggal_spm": row.tanggal_spm,
        "jenis_spm": row.jenis_spm,
        "no_kuitansi": row.no_kuitansi or None,
        "no_drpp": row.no_drpp or None,
        "deskripsi": row.deskripsi,
        "nilai_bruto": row.nilai_bruto,
        "nilai_netto": row.nilai_netto,
        "pembebanan": row.pembebanan,
        "fp": row.fp or None,
        "pph21": row.pph21,
    }
    columns = {
        field: actual_value(value, locator=field, **_spm_field_source(parsed, detail_item, field))
        for field, value in values.items()
    }
    columns["helper"] = actual_value(
        computed_helper(values["akun"], values["no_kuitansi"]),
        "COMPUTED",
        extraction_method="concatenate",
        locator="akun + no_kuitansi",
        inputs=["akun", "no_kuitansi"],
    )
    return columns


def probe_drpp_batch(path, *, ocr):
    parsed = parse_drpp_upload_batch(str(path), ocr=ocr)
    rows = []
    group_summary = []
    for group_index, group in enumerate(parsed.get("drpp_groups") or [], start=1):
        items = group.get("items") or []
        group_summary.append({
            "no_drpp": group.get("no_drpp"),
            "row_count": len(items),
            "total": _json_value(sum((Decimal(str(item.get("nilai_bruto") or 0)) for item in items), Decimal("0"))),
            "status": group.get("status"),
        })
        for item_index, item in enumerate(items, start=1):
            source = _page_source(parsed, item, group.get("drpp") or {})
            row_key = "|".join((
                source.get("source_file") or f"group:{group_index}",
                f"page:{source.get('source_page') or 0}",
                f"drpp:{group.get('no_drpp') or ''}",
                f"row:{item_index}",
            ))
            rows.append({"row_key": row_key, "columns": _batch_columns(parsed, group, item)})
    public_rows, source_counts = _redacted_rows(rows)
    spm_meta = (parsed.get("spm") or {}).get("metadata", {})
    return {
        "pipeline": "drpp_batch",
        "document_identity": {
            "spp": spm_meta.get("nomor_spp") or None,
            "spm": spm_meta.get("nomor_spm") or None,
            "sp2d": spm_meta.get("nomor_sp2d") or None,
        },
        "transaction_count": len(rows),
        "total_nominal": _json_value(sum((Decimal(str(item["columns"]["nilai_bruto"]["value"] or 0)) for item in rows), Decimal("0"))),
        "groups": group_summary,
        "metrics": parsed.get("metrics") or {},
        "provenance_source_counts": source_counts,
        "actual_layers": {
            "extraction": {"transaction_count": len(rows), "provenance_source_counts": source_counts},
            "enrichment": {"transaction_count": len(rows), "provenance_source_counts": source_counts},
        },
        "rows": public_rows,
        "enrichment_rows": public_rows,
        "warnings": parsed.get("warnings") or [],
    }


def _spm_metrics(parsed, elapsed):
    pages = [page for page in parsed.get("page_details") or [] if isinstance(page, dict)]
    return {
        "process_seconds": round(elapsed, 3),
        "page_total": parsed.get("page_count") or len(pages),
        "unique_pages": parsed.get("page_count") or len(pages),
        "ocr_pages": sum(1 for page in pages if page.get("engine") not in {None, "", "text", "native_pdf"}),
        "ocr_cache_hits": sum(1 for page in pages if page.get("cache_hit")),
    }


def probe_spm(path, *, ocr):
    started = time.monotonic()
    parsed = parse_spm_pdf(str(path), ocr=ocr)
    elapsed = time.monotonic() - started
    meta = parsed.get("metadata") or {}
    paket = PaketSPMUpload(
        original_filename=Path(path).name,
        nomor_spm=meta.get("nomor_spm") or "",
        satker_code=meta.get("satker_app_code") or meta.get("satker_code") or "",
        tanggal_spm=meta.get("tanggal_spm"),
        tahun=getattr(meta.get("tanggal_spm"), "year", None),
        bulan=getattr(meta.get("tanggal_sp2d"), "month", None),
        jenis_spm_asli=meta.get("jenis_spm") or "",
        jenis_spm_label=meta.get("jenis_spm") or "",
    )
    wrapped = {"spm": parsed, "drpps": [], "kw_items": [], "files": []}
    rows = []
    materialization_error = None
    try:
        rows = build_transaction_rows_from_package(
            wrapped, paket, save=False, skip_existing=False
        )
    except Exception as exc:
        materialization_error = str(exc)
    detail_items = parsed.get("detail_items") or []
    actual_rows = []
    for index, row in enumerate(rows):
        detail_item = detail_items[index] if index < len(detail_items) else {}
        source_page = detail_item.get("source_page")
        actual_rows.append({
            "row_key": f"page:{source_page or 0}|row:{index + 1}|account:{row.akun}",
            "columns": _spm_columns(parsed, row, detail_item),
        })
    public_rows, source_counts = _redacted_rows(actual_rows)
    return {
        "pipeline": "spm",
        "document_identity": {
            "spp": meta.get("nomor_spp") or None,
            "spm": meta.get("nomor_spm") or None,
            "sp2d": meta.get("nomor_sp2d") or None,
        },
        "transaction_count": len(rows),
        "total_nominal": _json_value(sum((row.nilai_bruto for row in rows), Decimal("0"))),
        "metrics": _spm_metrics(parsed, elapsed),
        "provenance_source_counts": source_counts,
        "actual_layers": {
            "extraction": {"transaction_count": len(actual_rows), "provenance_source_counts": source_counts},
            "enrichment": {"transaction_count": len(actual_rows), "provenance_source_counts": source_counts},
        },
        "rows": public_rows,
        "enrichment_rows": public_rows,
        "materialization_error": materialization_error,
        "parser_diagnostics": {
            "detail_parse_summary": meta.get("detail_parse_summary") or {},
            "detail_item_count": len(parsed.get("detail_items") or []),
            "account_row_count": len(parsed.get("akun_rows") or []),
        },
        "warnings": parsed.get("warnings") or [],
    }


def probe_fixture(path, *, ocr=True, cleanup_cache=True):
    path = Path(path).resolve()
    before = cache_snapshot(path)
    started = time.monotonic()
    try:
        if path.suffix.lower() == ".zip":
            result = probe_drpp_batch(path, ocr=ocr)
        elif path.suffix.lower() == ".pdf":
            result = probe_spm(path, ocr=ocr)
        else:
            raise ValueError("Fixture harus PDF atau ZIP.")
        result["fixture"] = {
            "filename": path.name,
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        result.setdefault("metrics", {})["run_seconds"] = round(time.monotonic() - started, 3)
        return result
    finally:
        removed = remove_new_cache_files(path, before) if cleanup_cache else []
        # The caller can independently verify that no run-created cache remains.
        probe_fixture.last_removed_cache_files = removed


probe_fixture.last_removed_cache_files = []
