"""Parser paket SPM berbasis klasifikasi halaman dan hubungan antardokumen.

Modul ini menjadi orkestrator di atas engine OCR dan parser yang sudah ada.
Ia tidak menambah aturan khusus berdasarkan nomor SPM, nama berkas, warna kertas,
atau nilai transaksi tertentu. Setiap PDF dibaca per halaman, lalu halaman SPM,
SPP, detail SPP/SPM/SP2D, DRPP, kuitansi, dan dokumen pendukung dihubungkan
sebelum transaksi dibentuk dan divalidasi.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from apps.core.parsers import (
    classify_page_types,
    extract_pdf_text,
    normalize_text,
    parse_decimal,
    parse_drpp_pdf,
    parse_paket_spm_zip,
    parse_spm_pdf,
)

ARCHITECTURE_VERSION = "document-graph-v1"

SPM_CONTEXT_TYPES = {
    "SPM",
    "SPM_HEADER",
    "SPP",
    "DETAIL_SPP_SPM_SP2D",
    "SP2D_DETAIL",
}
DRPP_CONTEXT_TYPES = {"DRPP"}
TRANSACTION_TABLE_TYPES = {"DETAIL_SPP_SPM_SP2D", "SP2D_DETAIL", "DRPP"}
SUPPORT_DOCUMENT_TYPES = {
    "SSP",
    "FAKTUR",
    "INVOICE",
    "BAST",
    "FORM_FP",
    "KW",
    "KW_MAIN",
    "KW_SUPPORT",
    "SP2D",
    "LAMPIRAN_COA",
}

PAGE_EVIDENCE = {
    "SPM": ("SURAT PERINTAH MEMBAYAR", "NOMOR SPM", "TOTAL PEMBAYARAN"),
    "SPM_HEADER": ("SURAT PERINTAH MEMBAYAR", "NOMOR SPM"),
    "SPP": ("SURAT PERMINTAAN PEMBAYARAN", "NOMOR SPP", "NO SPP"),
    "DETAIL_SPP_SPM_SP2D": (
        "DETAIL PENGELUARAN DAN POTONGAN",
        "SPP/SPM/SP2D",
    ),
    "SP2D_DETAIL": ("DETAIL PENGELUARAN DAN POTONGAN", "SP2D"),
    "DRPP": ("DAFTAR RINCIAN PERMINTAAN PEMBAYARAN", "BUKTI PENGELUARAN"),
    "LAMPIRAN_COA": ("LAMPIRAN DAFTAR RINCIAN", "KODE AKUN", "PEMBEBANAN"),
    "SSP": ("SURAT SETORAN PAJAK", "KODE AKUN PAJAK", "MASA PAJAK"),
    "KW": ("KUITANSI", "KW/", "TERBILANG"),
    "KW_MAIN": ("BUKTI PENGELUARAN", "KUITANSI"),
    "KW_SUPPORT": ("KUITANSI", "KW/"),
    "FAKTUR": ("FAKTUR",),
    "INVOICE": ("INVOICE",),
    "BAST": ("BERITA ACARA SERAH TERIMA", "BAST"),
    "FORM_FP": ("FORMULIR PERMINTAAN BELANJA", "FORM-PENGHASILAN"),
    "SP2D": ("NO SP2D", "NOMOR SP2D", "DAFTAR SP2D"),
}


def _unique(values: Iterable[str]) -> List[str]:
    output: List[str] = []
    for value in values:
        text = normalize_text(value)
        if text and text not in output:
            output.append(text)
    return output


def _page_types(page: Dict[str, Any]) -> List[str]:
    text = page.get("text") or page.get("extracted_text") or ""
    types = classify_page_types(text)
    return list(dict.fromkeys(types or ["UNKNOWN"]))


def _page_evidence(text: str, page_types: Sequence[str]) -> Dict[str, List[str]]:
    upper = normalize_text(text).upper()
    evidence: Dict[str, List[str]] = {}
    for page_type in page_types:
        anchors = [anchor for anchor in PAGE_EVIDENCE.get(page_type, ()) if anchor in upper]
        if anchors:
            evidence[page_type] = anchors
    return evidence


def _page_node(page: Dict[str, Any], index: int) -> Dict[str, Any]:
    text = page.get("text") or page.get("extracted_text") or ""
    page_number = page.get("page_number") or page.get("page") or index
    page_types = _page_types(page)
    confidence = float(page.get("confidence") or page.get("ocr_confidence") or 0.0)
    primary = page.get("primary_page_type") or page.get("page_classification") or page_types[0]
    return {
        "id": f"page:{page_number}",
        "page_number": page_number,
        "document_type": primary,
        "document_types": page_types,
        "confidence": confidence,
        "method": page.get("method") or page.get("engine") or "",
        "rotation": page.get("selected_rotation") or page.get("rotation") or 0,
        "text_length": len(normalize_text(text)),
        "evidence": _page_evidence(text, page_types),
        "needs_review": primary == "UNKNOWN" or (confidence > 0 and confidence < 45),
    }


def build_document_graph(page_details: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Bangun graph halaman tanpa membuat transaksi dari dokumen pendukung."""
    nodes = [_page_node(page, index) for index, page in enumerate(page_details or [], start=1)]
    type_counts = Counter(
        page_type
        for node in nodes
        for page_type in node.get("document_types") or [node.get("document_type", "UNKNOWN")]
    )

    spm_root = next((node["id"] for node in nodes if "SPM" in node["document_types"]), "")
    drpp_root = next((node["id"] for node in nodes if "DRPP" in node["document_types"]), "")
    fallback_root = spm_root or drpp_root or (nodes[0]["id"] if nodes else "")

    edges: List[Dict[str, str]] = []
    for node in nodes:
        node_id = node["id"]
        types = set(node.get("document_types") or [])
        if node_id == fallback_root:
            continue
        if "SPP" in types and spm_root:
            edges.append({"from": node_id, "to": spm_root, "relation": "requests_payment_for"})
        elif types.intersection({"DETAIL_SPP_SPM_SP2D", "SP2D_DETAIL", "LAMPIRAN_COA"}) and spm_root:
            edges.append({"from": node_id, "to": spm_root, "relation": "details"})
        elif "DRPP" in types and spm_root:
            edges.append({"from": node_id, "to": spm_root, "relation": "details"})
        elif types.intersection({"KW", "KW_MAIN", "KW_SUPPORT"}):
            parent = drpp_root or spm_root
            if parent:
                edges.append({"from": node_id, "to": parent, "relation": "supports"})
        elif types.intersection(SUPPORT_DOCUMENT_TYPES) and spm_root:
            edges.append({"from": node_id, "to": spm_root, "relation": "supports"})

    support_pages = [
        node["page_number"]
        for node in nodes
        if set(node.get("document_types") or []).intersection(SUPPORT_DOCUMENT_TYPES)
        and not set(node.get("document_types") or []).intersection(TRANSACTION_TABLE_TYPES)
    ]
    transaction_pages = [
        node["page_number"]
        for node in nodes
        if set(node.get("document_types") or []).intersection(TRANSACTION_TABLE_TYPES)
    ]

    return {
        "version": ARCHITECTURE_VERSION,
        "nodes": nodes,
        "edges": edges,
        "root_document": spm_root or drpp_root,
        "page_type_counts": dict(type_counts),
        "transaction_pages": transaction_pages,
        "support_pages": support_pages,
    }


def _meaningful_spm(spm: Optional[Dict[str, Any]], graph_types: set[str]) -> bool:
    if not spm:
        return False
    meta = spm.get("metadata") or {}
    return bool(
        meta.get("nomor_spm")
        or spm.get("detail_items")
        or (graph_types.intersection(SPM_CONTEXT_TYPES) and parse_decimal(meta.get("jumlah_pengeluaran")) > 0)
    )


def _meaningful_drpp(drpp: Optional[Dict[str, Any]]) -> bool:
    if not drpp:
        return False
    meta = drpp.get("metadata") or {}
    return bool(meta.get("nomor_drpp") or drpp.get("items"))


def _row_amount(row: Dict[str, Any]) -> Decimal:
    return parse_decimal(row.get("bruto") or row.get("jumlah") or row.get("nilai_bruto"))


def _row_identity(row: Dict[str, Any]) -> Tuple[str, ...]:
    return (
        normalize_text(row.get("no_bukti") or row.get("no_kuitansi") or "").upper(),
        normalize_text(row.get("no_drpp") or "").upper(),
        normalize_text(row.get("akun") or ""),
        normalize_text(row.get("pembebanan") or "").upper(),
        str(_row_amount(row)),
    )


def validate_transaction_rows(
    rows: Sequence[Dict[str, Any]],
    spm: Optional[Dict[str, Any]] = None,
    drpp: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Validasi bisnis tanpa menghapus atau mengubah nilai hasil OCR."""
    spm_meta = (spm or {}).get("metadata") or {}
    drpp_meta = (drpp or {}).get("metadata") or {}
    expected = parse_decimal(spm_meta.get("jumlah_pengeluaran"))
    if expected <= 0:
        expected = parse_decimal(spm_meta.get("total_pembayaran")) + parse_decimal(spm_meta.get("jumlah_potongan"))
    if expected <= 0:
        expected = parse_decimal(drpp_meta.get("total"))

    actual = sum((_row_amount(row) for row in rows), Decimal("0"))
    issues: List[str] = []
    invalid_rows: List[Dict[str, Any]] = []
    seen: Dict[Tuple[str, ...], int] = {}
    duplicate_keys: List[Tuple[str, ...]] = []

    for index, row in enumerate(rows, start=1):
        row_issues: List[str] = []
        akun = normalize_text(row.get("akun") or "")
        amount = _row_amount(row)
        pembebanan = normalize_text(row.get("pembebanan") or "")
        if not re.fullmatch(r"5\d{5}", akun):
            row_issues.append("akun_invalid")
        if amount <= 0:
            row_issues.append("nominal_invalid")
        if not pembebanan:
            row_issues.append("pembebanan_missing")
        elif not pembebanan.endswith(akun):
            row_issues.append("pembebanan_akun_mismatch")
        if not normalize_text(row.get("keperluan") or row.get("deskripsi") or ""):
            row_issues.append("deskripsi_missing")
        if row.get("needs_review"):
            row_issues.append("parser_needs_review")
        key = _row_identity(row)
        if key in seen:
            duplicate_keys.append(key)
            row_issues.append("duplicate_transaction")
        else:
            seen[key] = index
        if row_issues:
            invalid_rows.append({"row": index, "issues": sorted(set(row_issues))})

    if not rows:
        issues.append("Tidak ada transaksi terstruktur yang dapat dibentuk. Dokumen tetap disimpan sebagai PERLU_REVIEW.")
    if expected > 0 and rows and abs(actual - expected) > Decimal("1"):
        issues.append(
            f"Total bruto rincian {actual} tidak sama dengan bruto dokumen induk {expected}."
        )
    if invalid_rows:
        issues.append(f"Terdapat {len(invalid_rows)} baris yang memerlukan review field.")
    if duplicate_keys:
        issues.append("Terdapat identitas transaksi duplikat.")
    if not spm and not drpp:
        issues.append("Dokumen induk SPM atau DRPP tidak ditemukan.")

    if not spm and not drpp:
        status = "GAGAL"
    elif issues:
        status = "PERLU_REVIEW"
    else:
        status = "VALID"

    return {
        "status": status,
        "row_count": len(rows),
        "expected_bruto": expected,
        "actual_bruto": actual,
        "difference": actual - expected if expected > 0 else Decimal("0"),
        "total_match": bool(rows and expected > 0 and abs(actual - expected) <= Decimal("1")),
        "invalid_rows": invalid_rows,
        "duplicate_keys": [list(key) for key in duplicate_keys],
        "issues": issues,
    }


def _file_summary(
    original_filename: str,
    spm: Optional[Dict[str, Any]],
    drpp: Optional[Dict[str, Any]],
    validation: Dict[str, Any],
    graph: Dict[str, Any],
) -> Dict[str, Any]:
    parser = spm or drpp or {}
    if spm and drpp:
        document_type = "SPM_PACKAGE_WITH_DRPP"
    elif spm:
        document_type = "SPM_PACKAGE"
    elif drpp:
        document_type = "DRPP"
    else:
        document_type = "SUPPORT_OR_UNKNOWN"
    return {
        "file_name": original_filename,
        "type": document_type,
        "status": "extracted" if validation["status"] != "GAGAL" else "needs_manual_review",
        "parse_status": parser.get("status") or "needs_manual_review",
        "method": parser.get("method") or "document_graph",
        "warnings": _unique((parser.get("warnings") or []) + validation.get("issues", [])),
        "page_types": graph.get("page_type_counts", {}),
    }


def _parse_pdf_package(
    file_path: str,
    original_filename: str,
    extracted: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    # Identity probe sudah melakukan OCR penuh pada PDF scan. Gunakan hasil yang
    # sama agar satu upload tidak memindai semua halaman dua kali.
    extracted = extracted or extract_pdf_text(file_path, ocr=True)
    page_details = extracted.get("page_details") or []
    graph = build_document_graph(page_details)
    graph_types = {
        page_type
        for node in graph.get("nodes", [])
        for page_type in node.get("document_types") or []
    }
    filename_upper = os.path.basename(original_filename or file_path).upper()
    unknown_only = bool(graph.get("nodes")) and graph_types.issubset({"UNKNOWN"})
    support_only = bool(graph_types.intersection(SUPPORT_DOCUMENT_TYPES)) and not bool(
        graph_types.intersection(SPM_CONTEXT_TYPES | DRPP_CONTEXT_TYPES)
    )

    spm: Optional[Dict[str, Any]] = None
    drpp: Optional[Dict[str, Any]] = None

    should_parse_spm = bool(graph_types.intersection(SPM_CONTEXT_TYPES)) or "SPM" in filename_upper or "SPP" in filename_upper or unknown_only
    should_parse_drpp = bool(graph_types.intersection(DRPP_CONTEXT_TYPES)) or "DRPP" in filename_upper

    if should_parse_spm and not support_only:
        candidate = parse_spm_pdf(
            file_path,
            ocr=True,
            extracted=extracted,
            parse_details=not should_parse_drpp,
        )
        if _meaningful_spm(candidate, graph_types):
            spm = candidate
    if should_parse_drpp:
        candidate = parse_drpp_pdf(file_path, ocr=True, extracted=extracted)
        if _meaningful_drpp(candidate):
            drpp = candidate

    # Bila halaman berlabel DRPP ternyata tidak menghasilkan transaksi valid,
    # baru jalankan recovery tabel SPM yang lebih mahal. Ini menghindari OCR
    # 300-DPI berulang pada paket yang sudah memiliki sumber transaksi DRPP.
    if spm and should_parse_drpp and not (drpp or {}).get("items"):
        recovered_spm = parse_spm_pdf(
            file_path,
            ocr=True,
            extracted=extracted,
            parse_details=True,
        )
        if _meaningful_spm(recovered_spm, graph_types):
            spm = recovered_spm

    # Dokumen baru dengan nama generik tetap harus dicoba sebagai paket SPM.
    if not spm and not drpp and not support_only:
        candidate = parse_spm_pdf(file_path, ocr=True, extracted=extracted)
        if _meaningful_spm(candidate, graph_types):
            spm = candidate
        elif "DRPP" in graph_types:
            candidate_drpp = parse_drpp_pdf(file_path, ocr=True, extracted=extracted)
            if _meaningful_drpp(candidate_drpp):
                drpp = candidate_drpp

    drpp_items = list((drpp or {}).get("items") or [])
    spm_items = list((spm or {}).get("detail_items") or [])
    if drpp_items:
        kw_items = drpp_items
        transaction_source = "DRPP"
    else:
        kw_items = spm_items
        transaction_source = "DETAIL_SPP_SPM_SP2D" if spm_items else ""

    drpp_number = normalize_text(((drpp or {}).get("metadata") or {}).get("nomor_drpp") or "DRPP")
    kw_by_drpp = {drpp_number: drpp_items} if drpp and drpp_items else {}
    validation = validate_transaction_rows(kw_items, spm=spm, drpp=drpp)

    warnings = _unique(
        list(extracted.get("warnings") or [])
        + list((spm or {}).get("warnings") or [])
        + list((drpp or {}).get("warnings") or [])
        + list(validation.get("issues") or [])
    )
    if graph.get("support_pages"):
        warnings.append(
            "Halaman pendukung terdeteksi dan dihubungkan ke dokumen induk; halaman tersebut tidak dibuat sebagai transaksi baru."
        )

    return {
        "ok": validation["status"] != "GAGAL",
        "architecture": ARCHITECTURE_VERSION,
        "document_graph": graph,
        "transaction_source": transaction_source,
        "files": [_file_summary(original_filename, spm, drpp, validation, graph)],
        "spm": spm,
        "drpp": drpp,
        "drpps": [drpp] if drpp else [],
        "kw_by_drpp": kw_by_drpp,
        "kw_items": kw_items,
        "support_documents": [
            node for node in graph.get("nodes", []) if node.get("page_number") in graph.get("support_pages", [])
        ],
        "validation": validation,
        "warnings": _unique(warnings),
        "temp_dir": "",
        "ocr_summary": {
            "method": extracted.get("method") or extracted.get("best_engine"),
            "confidence": extracted.get("confidence", 0.0),
            "page_count": extracted.get("page_count", len(page_details)),
            "engines_tried": extracted.get("engines_tried", []),
        },
    }


def _zip_graph(parsed: Dict[str, Any]) -> Dict[str, Any]:
    nodes = []
    for index, item in enumerate(parsed.get("files") or [], start=1):
        doc_type = normalize_text(item.get("type") or "UNKNOWN").upper()
        nodes.append(
            {
                "id": f"file:{index}",
                "file_name": item.get("file_name") or "",
                "document_type": doc_type,
                "document_types": [doc_type],
                "confidence": item.get("confidence") or 0.0,
                "evidence": {},
                "needs_review": item.get("status") in {"needs_manual_review", "failed"},
            }
        )
    root = next((node["id"] for node in nodes if node["document_type"] == "SPM"), "")
    drpp_root = next((node["id"] for node in nodes if node["document_type"] == "DRPP"), "")
    edges = []
    for node in nodes:
        if node["id"] in {root, drpp_root}:
            continue
        parent = drpp_root if node["document_type"] in {"KW", "KUITANSI"} else root
        if parent:
            edges.append({"from": node["id"], "to": parent, "relation": "supports"})
    return {
        "version": ARCHITECTURE_VERSION,
        "nodes": nodes,
        "edges": edges,
        "root_document": root or drpp_root,
        "page_type_counts": dict(Counter(node["document_type"] for node in nodes)),
        "transaction_pages": [],
        "support_pages": [],
    }


def parse_uploaded_package(
    file_path: str,
    original_filename: str = "",
    *,
    kind: str = "",
    extracted: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Parse PDF atau ZIP dengan satu pintu masuk arsitektur document graph."""
    original_filename = original_filename or os.path.basename(file_path)
    resolved_kind = normalize_text(kind).lower()
    if not resolved_kind:
        resolved_kind = "zip" if original_filename.lower().endswith(".zip") else "pdf"

    if resolved_kind == "zip":
        parsed = parse_paket_spm_zip(file_path, ocr=True)
        parsed["architecture"] = ARCHITECTURE_VERSION
        parsed["document_graph"] = _zip_graph(parsed)
        drpp_list = parsed.get("drpps") or ([parsed.get("drpp")] if parsed.get("drpp") else [])
        drpp = drpp_list[0] if drpp_list else None
        parsed["validation"] = validate_transaction_rows(
            parsed.get("kw_items") or [],
            spm=parsed.get("spm"),
            drpp=drpp,
        )
        parsed.setdefault("warnings", [])
        parsed["warnings"] = _unique(parsed["warnings"] + parsed["validation"].get("issues", []))
        parsed["ok"] = parsed["validation"]["status"] != "GAGAL"
        return parsed

    return _parse_pdf_package(file_path, original_filename, extracted=extracted)
