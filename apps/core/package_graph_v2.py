"""Orkestrator Paket SPM v2 yang menjamin PDF pindai benar-benar melalui OCR.

Modul ini membungkus document graph v1 tanpa mengubah aturan bisnisnya. Fokus v2
ialah mencegah hasil ekstraksi native yang kosong dianggap sebagai hasil OCR yang
sah, menjalankan Tesseract lebih dahulu, menjalankan PaddleOCR hanya ketika hasil
transaksi masih kosong, dan memilih hasil parser berdasarkan bukti terstruktur.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Optional

from apps.core.package_graph import parse_uploaded_package as parse_uploaded_package_v1
from apps.core.parsers import extract_pdf_text, normalize_text


ARCHITECTURE_VERSION = "document-graph-v2"

STRUCTURE_ANCHORS = (
    "SURAT PERINTAH MEMBAYAR",
    "SURAT PERMINTAAN PEMBAYARAN",
    "DETAIL PENGELUARAN DAN POTONGAN",
    "DAFTAR RINCIAN PERMINTAAN PEMBAYARAN",
    "LAMPIRAN DAFTAR RINCIAN",
    "KUITANSI",
    "NOMOR SPM",
    "NOMOR SPP",
    "TOTAL PEMBAYARAN",
    "JUMLAH PENGELUARAN",
    "KODE AKUN",
    "PEMBEBANAN",
)


@contextmanager
def _temporary_environment(**values: str):
    previous = {name: os.environ.get(name) for name in values}
    try:
        for name, value in values.items():
            os.environ[name] = str(value)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _page_details(extracted: Optional[Dict[str, Any]]) -> list[Dict[str, Any]]:
    if not extracted:
        return []
    details = extracted.get("page_details") or extracted.get("pages") or []
    if details and isinstance(details[0], str):
        return [
            {
                "page": index,
                "page_number": index,
                "text": text,
                "extracted_text": text,
            }
            for index, text in enumerate(details, start=1)
        ]
    return [item for item in details if isinstance(item, dict)]


def _combined_text(extracted: Optional[Dict[str, Any]]) -> str:
    if not extracted:
        return ""
    combined = str(extracted.get("combined_text") or "").strip()
    if combined:
        return combined
    return "\n".join(
        str(page.get("text") or page.get("extracted_text") or "")
        for page in _page_details(extracted)
    ).strip()


def _has_structural_evidence(extracted: Optional[Dict[str, Any]]) -> bool:
    upper = normalize_text(_combined_text(extracted)).upper()
    if not upper:
        return False
    return any(anchor in upper for anchor in STRUCTURE_ANCHORS)


def _has_usable_extracted(extracted: Optional[Dict[str, Any]]) -> bool:
    """Dictionary nonkosong belum tentu berisi hasil OCR yang dapat dipakai."""
    if not extracted:
        return False
    status = normalize_text(extracted.get("status")).lower()
    if status == "failed":
        return False
    text = normalize_text(_combined_text(extracted))
    if not text:
        return False
    minimum = int(os.getenv("OCR_REUSE_MIN_TEXT_LENGTH", "80"))
    return len(text) >= minimum or _has_structural_evidence(extracted)


def _ocr_score(extracted: Optional[Dict[str, Any]]) -> tuple[int, int, int]:
    text = normalize_text(_combined_text(extracted))
    upper = text.upper()
    anchors = sum(1 for anchor in STRUCTURE_ANCHORS if anchor in upper)
    confidence = int(float((extracted or {}).get("confidence") or 0.0) * 10)
    return anchors, min(len(text), 1_000_000), confidence


def _parse_score(parsed: Optional[Dict[str, Any]]) -> tuple[int, int, int, int, int]:
    if not parsed:
        return 0, 0, 0, 0, 0
    rows = list(parsed.get("kw_items") or [])
    validation = parsed.get("validation") or {}
    status = normalize_text(validation.get("status")).upper()
    status_score = {
        "VALID": 4,
        "PERLU_REVIEW": 3,
        "GAGAL": 1,
        "GAGAL_OCR": 0,
    }.get(status, 2)
    total_match = 1 if validation.get("total_match") else 0
    spm_meta = ((parsed.get("spm") or {}).get("metadata") or {})
    metadata_count = sum(
        bool(spm_meta.get(field))
        for field in (
            "nomor_spm",
            "tanggal_spm",
            "jenis_spm",
            "jumlah_pengeluaran",
            "total_pembayaran",
            "satker_code",
            "satker_app_code",
        )
    )
    valid_fields = sum(
        int(bool(row.get("akun")))
        + int(bool(row.get("pembebanan")))
        + int(bool(row.get("keperluan") or row.get("deskripsi")))
        for row in rows
    )
    return len(rows), total_match, status_score, valid_fields, metadata_count


def _dedupe(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        clean = normalize_text(value)
        if clean and clean not in output:
            output.append(clean)
    return output


def _extract_with_engine_order(file_path: str, engine_order: str) -> Dict[str, Any]:
    with _temporary_environment(
        OCR_ENGINE_ORDER=engine_order,
        OCR_FORCE_IMAGE_FOR_SCANNED_DOCS="true",
        FLAGS_use_mkldnn="0",
        PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT="0",
    ):
        return extract_pdf_text(file_path, ocr=True)


def _primary_ocr(file_path: str) -> Dict[str, Any]:
    """Gunakan Tesseract dahulu agar PaddleOCR tidak memperlambat setiap upload."""
    order = normalize_text(os.getenv("OCR_PRIMARY_ENGINE_ORDER", "text,tesseract"))
    if "tesseract" not in order.lower():
        order = "text,tesseract"
    return _extract_with_engine_order(file_path, order)


def _paddle_ocr(file_path: str) -> Dict[str, Any]:
    order = normalize_text(os.getenv("OCR_PADDLE_FALLBACK_ENGINE_ORDER", "text,paddleocr"))
    if "paddleocr" not in order.lower():
        order = "text,paddleocr"
    return _extract_with_engine_order(file_path, order)


def _public_attempt(
    name: str,
    extracted: Dict[str, Any],
    parsed: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "name": name,
        "status": extracted.get("status"),
        "method": extracted.get("method") or extracted.get("best_engine"),
        "text_length": len(_combined_text(extracted)),
        "confidence": extracted.get("confidence", 0.0),
        "engines_tried": extracted.get("engines_tried", []),
        "usable": _has_usable_extracted(extracted),
        "structural_evidence": _has_structural_evidence(extracted),
        "row_count": len((parsed or {}).get("kw_items") or []),
        "validation": ((parsed or {}).get("validation") or {}).get("status", ""),
    }


def _mark_ocr_failure(parsed: Dict[str, Any], extracted: Dict[str, Any]) -> Dict[str, Any]:
    issue = (
        "OCR penuh tidak menghasilkan teks yang dapat digunakan. Sistem tidak membuat "
        "transaksi bernilai nol. Periksa interpreter Python, Tesseract, PaddleOCR, dan konfigurasi .env."
    )
    validation = dict(parsed.get("validation") or {})
    validation.update(
        {
            "status": "GAGAL_OCR",
            "row_count": 0,
            "total_match": False,
            "issues": _dedupe(list(validation.get("issues") or []) + [issue]),
        }
    )
    parsed["validation"] = validation
    parsed["ok"] = False
    parsed["kw_items"] = []
    parsed["warnings"] = _dedupe(
        list(parsed.get("warnings") or [])
        + list(extracted.get("warnings") or [])
        + [issue]
    )
    return parsed


def parse_uploaded_package(
    file_path: str,
    original_filename: str = "",
    *,
    kind: str = "",
    extracted: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Parse paket dengan OCR wajib untuk PDF scan dan fallback bertahap.

    Aturan utama:
    1. Hasil native kosong tidak pernah dipakai sebagai hasil OCR final.
    2. Tesseract menjadi percobaan utama.
    3. PaddleOCR hanya berjalan jika transaksi Tesseract tetap kosong.
    4. Hasil dengan transaksi terstruktur dan total cocok selalu diprioritaskan.
    5. Dokumen pendukung tetap mengikuti document graph v1 dan tidak menjadi
       transaksi baru.
    """
    resolved_kind = normalize_text(kind).lower()
    if not resolved_kind:
        resolved_kind = "zip" if str(original_filename or file_path).lower().endswith(".zip") else "pdf"

    if resolved_kind == "zip":
        parsed = parse_uploaded_package_v1(
            file_path,
            original_filename,
            kind=resolved_kind,
            extracted=extracted,
        )
        parsed["architecture"] = ARCHITECTURE_VERSION
        parsed.setdefault("ocr_summary", {})["orchestration"] = "zip-document-graph-v2"
        return parsed

    attempts: list[Dict[str, Any]] = []
    reusable = _has_usable_extracted(extracted)
    primary_extracted = extracted if reusable else _primary_ocr(file_path)
    primary_parsed = parse_uploaded_package_v1(
        file_path,
        original_filename,
        kind="pdf",
        extracted=primary_extracted,
    )
    attempts.append(
        _public_attempt(
            "reused_identity_ocr" if reusable else "tesseract_primary",
            primary_extracted,
            primary_parsed,
        )
    )

    selected_extracted = primary_extracted
    selected_parsed = primary_parsed

    paddle_enabled = str(os.getenv("OCR_ENABLE_PADDLEOCR", "false")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    needs_paddle = (
        paddle_enabled
        and not list(primary_parsed.get("kw_items") or [])
        and (
            not _has_usable_extracted(primary_extracted)
            or normalize_text(((primary_parsed.get("validation") or {}).get("status"))).upper() != "VALID"
        )
    )

    if needs_paddle:
        paddle_extracted = _paddle_ocr(file_path)
        paddle_parsed = parse_uploaded_package_v1(
            file_path,
            original_filename,
            kind="pdf",
            extracted=paddle_extracted,
        )
        attempts.append(_public_attempt("paddle_fallback", paddle_extracted, paddle_parsed))
        if (_parse_score(paddle_parsed), _ocr_score(paddle_extracted)) > (
            _parse_score(selected_parsed),
            _ocr_score(selected_extracted),
        ):
            selected_extracted = paddle_extracted
            selected_parsed = paddle_parsed

    selected_parsed["architecture"] = ARCHITECTURE_VERSION
    summary = dict(selected_parsed.get("ocr_summary") or {})
    summary.update(
        {
            "orchestration": "tesseract-first-paddle-on-empty",
            "extracted_reused": reusable,
            "full_ocr_called": not reusable,
            "usable_text": _has_usable_extracted(selected_extracted),
            "structural_evidence": _has_structural_evidence(selected_extracted),
            "selected_method": selected_extracted.get("method") or selected_extracted.get("best_engine"),
            "selected_text_length": len(_combined_text(selected_extracted)),
            "attempts": attempts,
        }
    )
    selected_parsed["ocr_summary"] = summary

    if not _has_usable_extracted(selected_extracted):
        selected_parsed = _mark_ocr_failure(selected_parsed, selected_extracted)

    selected_parsed["warnings"] = _dedupe(
        list(selected_parsed.get("warnings") or [])
        + list(selected_extracted.get("warnings") or [])
    )
    return selected_parsed
