import os
import re
import hashlib
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median

import pandas as pd

from apps.core.ocr import configure_tesseract, extract_document_text, extract_pdf_pages, ocr_log


# ─── Regex SP2D 15-digit (contoh: 260100000013375) ───────────────────────────
# SP2D KPPN biasanya 15 digit numerik murni, atau bisa ada label eksplisit
_RE_SP2D_LABELED = re.compile(
    r"(?:NO\.?\s*SP2D|NOMOR\s+SP2D|SP2D\s+NOMOR)\s*[:\-]?\s*([0-9]{5,20}[A-Z0-9./\-]*)",
    re.IGNORECASE,
)
# Bare 15-digit — hanya dalam konteks halaman SP2D/detail pengeluaran
_RE_SP2D_BARE = re.compile(r"\b(2[0-9]{14})\b")  # dimulai dengan 2, 15 digit

# Regex Invoice/SPP-SPM format: nomor/satker/tahun
_RE_INVOICE = re.compile(
    r"(?:NOMOR\s+INVOICE|NO\.?\s*INVOICE|INVOICE\s+NO\.?|NO\.?\s*SPP[-/]SPM|SPP[-/]SPM)\s*[:\-]?\s*"
    r"([0-9]{3,6}[A-Z]?/[0-9]{3,9}/[0-9]{4})",
    re.IGNORECASE,
)
# Fallback bare pola tanpa label — format: digit(A)/digit/4digit
_RE_INVOICE_BARE = re.compile(r"\b([0-9]{3,6}[A-Z]/[0-9]{3,9}/[0-9]{4})\b")

# ─── Regex pembebanan/COA 16-segmen: AAAA.BBB.CCC.DDD.EEEEEE ────────────────
_RE_PEMBEBANAN = re.compile(
    r"\b([0-9]{3,6}\.[0-9A-Z]{2,6}\.[0-9A-Z]{2,6}\.[0-9A-Z]{2,6}\.[0-9]{3,6})\b"
)


MONTHS = {
    "januari": 1,
    "februari": 2,
    "maret": 3,
    "april": 4,
    "mei": 5,
    "juni": 6,
    "juli": 7,
    "agustus": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "desember": 12,
}


SP2D_HEADER_KEYWORDS = [
    "no sp2d",
    "tanggal selesai sp2d",
    "nilai sp2d",
    "nomor invoice",
    "jenis spm",
    "deskripsi",
]

SP2D_COLUMN_MAP = {
    "satker": "satker_code",
    "kode satker": "satker_code",
    "kdsatker": "satker_code",
    "nama satker": "satker_name",
    "no sp2d": "no_sp2d",
    "no. sp2d": "no_sp2d",
    "tanggal selesai sp2d": "tanggal_selesai_sp2d",
    "tgl sp2d": "tgl_sp2d",
    "mata uang": "mata_uang",
    "nilai spm": "nilai_spm",
    "potongan": "potongan",
    "nilai sp2d": "nilai_sp2d",
    "nilai sp2d ekuivalen": "nilai_sp2d_ekuivalen",
    "nomor invoice": "nomor_invoice",
    "tanggal invoice": "tanggal_invoice",
    "jenis spm": "jenis_spm",
    "jenis sp2d": "jenis_sp2d",
    "deskripsi": "deskripsi",
    "cek akun": "cek_akun",
}


def normalize_text(value):
    if value is None:
        return ""
    text = str(value).replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fix_ocr_numeric(text):
    if not text:
        return text
    # Only replace characters that are obviously misread numbers in numeric contexts
    text = re.sub(r'[OQD]', '0', text, flags=re.IGNORECASE)
    text = re.sub(r'[Il|]', '1', text)
    text = re.sub(r'Z', '2', text, flags=re.IGNORECASE)
    text = re.sub(r'B', '8', text, flags=re.IGNORECASE)
    return text


def normalize_column(value):

    text = normalize_text(value).lower()
    text = text.replace("invoice", "invoice").replace("invoice", "invoice")
    text = re.sub(r"[^a-z0-9 ._/()-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_decimal(value):
    if value in (None, ""):
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    text = normalize_text(value)
    if not text:
        return Decimal("0")
    text = text.replace("Rp", "").replace("rp", "").replace(" ", "")
    text = fix_ocr_numeric(text)
    if "," in text and "." in text:
        decimal_separator = "," if text.rfind(",") > text.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        parts = text.split(decimal_separator)
        integer = "".join(parts[:-1]).replace(thousands_separator, "")
        if len(parts[-1]) == 2 and integer.isdigit() and parts[-1].isdigit():
            text = f"{integer}.{parts[-1]}"
        else:
            text = text.replace(",", "").replace(".", "")
    elif "," in text or "." in text:
        separator = "," if "," in text else "."
        parts = text.split(separator)
        if len(parts[-1]) == 2 and all(part.isdigit() for part in parts):
            text = f"{''.join(parts[:-1])}.{parts[-1]}"
        else:
            text = "".join(parts)
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return Decimal("0")


def parse_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = normalize_text(value)
    month_pattern = "|".join(MONTHS)
    match = re.search(rf"\b([0-9]{{1,2}})[\s\-/]+({month_pattern})[\s\-/]+([0-9]{{2,4}})\b", text, re.IGNORECASE)
    if match:
        year = int(match.group(3))
        if year < 100:
            year += 2000
        return date(year, MONTHS[match.group(2).lower()], int(match.group(1)))
    try:
        parsed = pd.to_datetime(value, errors="coerce", format="%Y-%m-%d")
        if pd.isna(parsed):
            parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    return parsed.date()


def extract_spm_number(value):
    text = normalize_text(value)
    match = re.search(r"\b(\d{4,6}[A-Z]?)\b", text, re.IGNORECASE)
    return match.group(1).upper() if match else text[:100]


def _extract_first_number_from_text(text):
    """Ekstrak nomor pertama (format SPM/SPP) dari teks."""
    match = re.search(r"(?:NOMOR|NO\.?)\s*[:\-]?\s*([0-9]{3,6}[A-Z]?)", text, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    match = re.search(r"\b([0-9]{4,6}[A-Z]?)\b", text, re.IGNORECASE)
    return match.group(1).upper() if match else ""


def is_valid_doc_number(val):
    if not val:
        return False
    val = val.strip().upper()
    return any(c.isdigit() for c in val)


def _normalized_page_doc_number(value):
    candidate = normalize_doc_number(value)
    if not re.fullmatch(r"[0-9]{3,6}[A-Z]?", candidate):
        return ""
    if re.fullmatch(r"20[0-9]{2}", candidate):
        return ""
    return candidate


def _page_doc_number_candidate(page_text, document_type):
    upper = normalize_text(page_text).upper()
    label = document_type.upper()
    title = "SURAT PERINTAH MEMBAYAR" if label == "SPM" else "SURAT PERMINTAAN PEMBAYARAN"
    patterns = [
        rf"(?:NOMOR\s+{label}|{label}\s+NOMOR|NO\.?\s*{label})\s*[:\-]?\s*([0-9]{{3,6}}[A-Z]?)\b",
        rf"{title}.{{0,500}}?(?:NOMOR|NO\.?)\s*[:\-]?\s*([0-9]{{3,6}}[A-Z]?)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, upper, re.DOTALL)
        if match:
            candidate = _normalized_page_doc_number(match.group(1))
            if candidate:
                return candidate
    return ""


def _resolve_page_doc_number(header_candidates, support_candidates):
    candidates = [item for item in header_candidates + support_candidates if item]
    if not candidates:
        return ""
    primary = header_candidates[0] if header_candidates else candidates[0]
    if primary.isdigit():
        correction = next(
            (
                candidate
                for candidate in candidates[1:]
                if re.fullmatch(r"[0-9]{3,6}[A-Z]", candidate)
                and len(candidate) == len(primary)
                and candidate[:-1] == primary[:-1]
            ),
            "",
        )
        if correction:
            return correction
    return primary


def parse_spm_number_from_pages(page_details):
    """Ekstrak No SPM, No SPP, nilai keuangan secara terpisah berdasarkan konteks halaman.

    Aturan:
    - Halaman "SURAT PERINTAH MEMBAYAR" → No SPM + nilai pengeluaran/potongan/total dari halaman ini
    - Halaman "SURAT PERMINTAAN PEMBAYARAN" atau "NOMOR SPP" → No SPP dari halaman ini
    - Jika tidak ada konteks per-halaman, fallback ke regex global

    Returns dict: {no_spm, no_spp, spm_pages, spp_pages,
                   jumlah_pengeluaran, jumlah_potongan, total_pembayaran}
    """
    spm_header_candidates = []
    spm_support_candidates = []
    spp_header_candidates = []
    spp_support_candidates = []
    spm_page_nums = []
    spp_page_nums = []
    jumlah_pengeluaran = Decimal("0")
    jumlah_potongan = Decimal("0")
    total_pembayaran = Decimal("0")

    for page in annotate_page_details(page_details):
        page_text = page.get("text") or page.get("extracted_text") or ""
        upper = page_text.upper()
        page_num = page.get("page") or page.get("page_number") or "?"

        page_types = set(page.get("page_types") or [])
        is_spm_page = "SPM" in page_types
        is_spp_page = "SPP" in page_types

        if is_spm_page:
            spm_page_nums.append(page_num)
            spm_candidate = _page_doc_number_candidate(page_text, "SPM")
            if spm_candidate:
                target = spm_support_candidates if re.search(r"LAMPIRAN\s+(?:SURAT\s+PERINTAH\s+MEMBAYAR|SPM)", upper) else spm_header_candidates
                if spm_candidate not in target:
                    target.append(spm_candidate)

            # Ambil nilai keuangan dari halaman SPM (bukan halaman SPP)
            if jumlah_pengeluaran <= 0:
                jumlah_pengeluaran = parse_money_from_text(
                    upper, ["JUMLAH PENGELUARAN", "NILAI PENGELUARAN", "NILAI SPM"]
                )
            if jumlah_potongan <= 0:
                jumlah_potongan = parse_money_from_text(
                    upper, ["JUMLAH POTONGAN", "TOTAL POTONGAN", "POTONGAN"]
                )
            if total_pembayaran <= 0:
                total_pembayaran = parse_money_from_text(
                    upper, ["TOTAL PEMBAYARAN", "JUMLAH YANG DIBAYARKAN", "NETO"]
                )

        if is_spp_page:
            spp_page_nums.append(page_num)
            spp_candidate = _page_doc_number_candidate(page_text, "SPP")
            if spp_candidate:
                target = spp_support_candidates if re.search(r"LAMPIRAN\s+(?:SURAT\s+PERMINTAAN\s+PEMBAYARAN|SPP)", upper) else spp_header_candidates
                if spp_candidate not in target:
                    target.append(spp_candidate)

    return {
        "no_spm": _resolve_page_doc_number(spm_header_candidates, spm_support_candidates),
        "no_spp": _resolve_page_doc_number(spp_header_candidates, spp_support_candidates),
        "spm_pages": spm_page_nums,
        "spp_pages": spp_page_nums,
        "spm_candidates": spm_header_candidates + spm_support_candidates,
        "spp_candidates": spp_header_candidates + spp_support_candidates,
        "jumlah_pengeluaran": jumlah_pengeluaran,
        "jumlah_potongan": jumlah_potongan,
        "total_pembayaran": total_pembayaran,
    }


def classify_page_types(text):
    upper = normalize_text(text).upper()
    types = []
    is_ssp = "SURAT SETORAN PAJAK" in upper or sum(
        anchor in upper for anchor in ("KODE AKUN PAJAK", "KODE JENIS SETORAN", "MASA PAJAK")
    ) >= 2
    detail_anchor_count = sum(
        anchor in upper
        for anchor in ("SPP/SPM/SP2D", "NO SP2D", "KODE COA", "PENGELUARAN", "POTONGAN")
    )
    is_detail = "DETAIL PENGELUARAN DAN POTONGAN" in upper or detail_anchor_count >= 4
    if is_detail:
        types.extend(["DETAIL_SPP_SPM_SP2D", "SP2D_DETAIL"])
    if "SURAT PERINTAH MEMBAYAR" in upper:
        types.extend(["SPM_HEADER", "SPM"])
    if "SURAT PERMINTAAN PEMBAYARAN" in upper or re.search(r"(?:NOMOR|NO\.?)\s+SPP\s*[:\-]", upper):
        types.append("SPP")
    if not is_ssp and (
        "LAMPIRAN DAFTAR RINCIAN" in upper
        or "DETAIL COA" in upper
        or (
            re.search(r"\b\d{4,6}[\s.,]+\d{3}[\s.,]+5\d{5}[\s.,]", upper)
            and any(anchor in upper for anchor in ("COA", "PEMBEBANAN", "KODE AKUN"))
        )
    ):
        types.append("LAMPIRAN_COA")
    drpp_table_anchor_count = sum(
        anchor in upper for anchor in ("NO BUKTI", "NAMA PENERIMA", "PENERIMA", "NPWP", "AKUN", "JUMLAH KOTOR")
    )
    if (
        "DAFTAR RINCIAN PERMINTAAN PEMBAYARAN" in upper
        or re.search(r"(?:NOMOR\s+DRPP|\b\d{3,6}/DRPP/)", upper)
        or ("BUKTI PENGELUARAN" in upper and drpp_table_anchor_count >= 3)
    ):
        types.append("DRPP")
    if is_ssp:
        types.append("SSP")
    if "FAKTUR" in upper:
        types.append("FAKTUR")
    if "INVOICE" in upper:
        types.append("INVOICE")
    if "BAST" in upper or "BERITA ACARA SERAH TERIMA" in upper:
        types.append("BAST")
    kw_pattern = bool(re.search(r"\b\d{3,6}/KW/", upper))
    if not is_ssp and (kw_pattern or ("KUITANSI" in upper and "TERBILANG" in upper)):
        types.append("KW_MAIN" if "BUKTI PENGELUARAN" in upper and kw_pattern else "KW_SUPPORT")
        types.append("KW")
    if "FORMULIR PERMINTAAN BELANJA" in upper or re.search(r"\bFP\s*-\s*20\d{2}", upper):
        types.append("FORM_FP")
    if ("SP2D" in upper or re.search(r"\b26\d{13}\b", upper)) and "SP2D" not in types:
        types.append("SP2D")
    return list(dict.fromkeys(types)) or ["UNKNOWN"]


def annotate_page_details(page_details):
    annotated = []
    for index, page in enumerate(page_details or [], start=1):
        item = dict(page)
        text = item.get("text") or item.get("extracted_text") or ""
        types = classify_page_types(text)
        item["ocr_page_types"] = item.get("page_types") or []
        item["page_types"] = types
        item["primary_page_type"] = types[0] if types else "UNKNOWN"
        item["page_classification"] = item["primary_page_type"]
        item.setdefault("page_number", item.get("page") or index)
        item.setdefault("confidence", item.get("ocr_confidence") or item.get("confidence") or 0.0)
        annotated.append(item)
    return annotated


def text_for_page_types(page_details, wanted_types, fallback_all=False):
    wanted = set(wanted_types)
    chunks = []
    for page in page_details:
        if wanted.intersection(page.get("page_types") or []):
            chunks.append(page.get("text") or page.get("extracted_text") or "")
    if not chunks and fallback_all:
        chunks = [page.get("text") or page.get("extracted_text") or "" for page in page_details]
    return "\n".join(chunk for chunk in chunks if chunk)


def field_source(page_details, value, wanted_types=None):
    if not value:
        return {}
    value_text = normalize_text(str(value)).upper()
    wanted = set(wanted_types or [])
    for page in page_details:
        if wanted and not wanted.intersection(page.get("page_types") or []):
            continue
        page_text = normalize_text(page.get("text") or page.get("extracted_text") or "").upper()
        if value_text and value_text in page_text:
            return {
                "page": page.get("page_number") or page.get("page"),
                "types": page.get("page_types") or [],
                "confidence": page.get("confidence") or 0.0,
            }
    return {}


def detect_mismatched_lampiran_numbers(page_details, main_spm="", main_spp=""):
    warnings = []
    for page in page_details:
        page_text = page.get("text") or page.get("extracted_text") or ""
        upper = page_text.upper()
        page_num = page.get("page") or page.get("page_number") or "?"
        if "LAMPIRAN" not in upper:
            continue
        spm_match = re.search(
            r"LAMPIRAN\s+(?:SURAT\s+PERINTAH\s+MEMBAYAR|SPM).*?(?:NOMOR\s+SPM|NOMOR|NO\.?)\s*[:\-]?\s*([0-9]{3,6}[A-Z])",
            upper,
            re.DOTALL,
        )
        if spm_match:
            nomor = normalize_doc_number(spm_match.group(1))
            if main_spm and nomor and nomor != main_spm:
                warnings.append(f"Lampiran SPM halaman {page_num} bernomor {nomor}; tidak mengganti No SPM utama {main_spm}.")
        spp_match = re.search(
            r"LAMPIRAN\s+(?:SURAT\s+PERMINTAAN\s+PEMBAYARAN|SPP).*?(?:NOMOR\s+SPP|NOMOR|NO\.?)\s*[:\-]?\s*([0-9]{3,6}[A-Z])",
            upper,
            re.DOTALL,
        )
        if spp_match:
            nomor = normalize_doc_number(spp_match.group(1))
            if main_spp and nomor and nomor != main_spp:
                warnings.append(f"Lampiran SPP halaman {page_num} bernomor {nomor}; tidak mengganti No SPP utama {main_spp}.")
    return warnings


def parse_month(value):
    text = normalize_text(value).lower()
    if text.isdigit():
        number = int(text)
        return number if 1 <= number <= 12 else None
    return MONTHS.get(text)


def find_sp2d_header_row(excel_file, sheet_name):
    sample = pd.read_excel(excel_file, sheet_name=sheet_name, header=None, nrows=30, dtype=object)
    best_row = None
    best_score = 0
    for idx, row in sample.iterrows():
        cells = " | ".join(normalize_column(cell) for cell in row.tolist())
        score = sum(1 for keyword in SP2D_HEADER_KEYWORDS if keyword in cells)
        if score > best_score:
            best_score = score
            best_row = int(idx)
    if best_score >= 2:
        return best_row, best_score
    return None, best_score


def parse_sp2d_excel_file(file_path):
    attempts = []
    selected = None
    with pd.ExcelFile(file_path) as excel:
        sheet_names = list(excel.sheet_names)
    for sheet in sheet_names:
        header_row, score = find_sp2d_header_row(file_path, sheet)
        attempts.append({"sheet": sheet, "header_row": None if header_row is None else header_row + 1, "score": score})
        if header_row is not None and selected is None:
            selected = (sheet, header_row)
    if selected is None:
        return {
            "ok": False,
            "error": "Header tabel SP2D tidak ditemukan pada 30 baris awal workbook.",
            "sheet_attempts": attempts,
            "sheet": "",
            "header_row": None,
            "columns": [],
            "mapping": {},
            "rows": [],
            "raw_rows": 0,
            "valid_rows": 0,
        }

    sheet, header_row = selected
    df = pd.read_excel(file_path, sheet_name=sheet, header=header_row, dtype=object)
    df = df.where(pd.notna(df), "")
    original_columns = [normalize_text(col) for col in df.columns.tolist()]
    normalized_to_original = {normalize_column(col): col for col in df.columns.tolist()}
    mapping = {}
    for normalized, original in normalized_to_original.items():
        if normalized in SP2D_COLUMN_MAP:
            mapping[original] = SP2D_COLUMN_MAP[normalized]

    df = df.dropna(how="all")
    rows = []
    for _, row in df.iterrows():
        mapped = {}
        for original, field in mapping.items():
            value = row.get(original, "")
            if field in {"tanggal_selesai_sp2d", "tgl_sp2d", "tanggal_invoice"}:
                value = parse_date(value)
            elif field in {"nilai_spm", "potongan", "nilai_sp2d"}:
                value = parse_decimal(value)
            else:
                value = normalize_text(value)
            mapped[field] = value
        if not any(mapped.get(key) for key in ("no_sp2d", "nomor_invoice", "deskripsi")):
            continue
        mapped["nomor_spm_extracted"] = extract_spm_number(mapped.get("nomor_invoice", ""))
        rows.append(mapped)

    return {
        "ok": bool(rows),
        "error": "" if rows else "Tidak ada baris valid setelah mapping kolom.",
        "sheet_attempts": attempts,
        "sheet": sheet,
        "header_row": header_row + 1,
        "columns": original_columns,
        "mapping": mapping,
        "rows": rows,
        "raw_rows": int(len(df)),
        "valid_rows": len(rows),
    }


def optional_import(module_name):
    try:
        return __import__(module_name)
    except Exception as exc:
        return None


def extract_pdf_text(file_path, ocr=False):
    if ocr:
        extracted = extract_document_text(file_path)
    else:
        extracted = extract_pdf_pages(file_path, use_ocr=False)
    return {
        "method": extracted.get("best_engine") or extracted.get("method"),
        "best_engine": extracted.get("best_engine") or extracted.get("method"),
        "status": extracted.get("status"),
        "pages": extracted.get("texts", []),
        "combined_text": extracted.get("combined_text", ""),
        "page_details": extracted.get("pages", []),
        "warnings": extracted.get("warnings", []),
        "page_count": extracted.get("page_count", 0),
        "confidence": extracted.get("confidence", 0.0),
        "engines_tried": extracted.get("engines_tried", []),
        "native_text_length": extracted.get("native_text_length", 0),
        "tesseract_called": extracted.get("tesseract_called", False),
        "tesseract_text_length": extracted.get("tesseract_text_length", 0),
        "tesseract_reason": extracted.get("tesseract_reason", ""),
        "paddleocr_called": extracted.get("paddleocr_called", False),
        "paddleocr_text_length": extracted.get("paddleocr_text_length", 0),
    }


def parser_status(extracted):
    if extracted["status"] in {"parsed_text", "parsed_ocr", "needs_manual_review", "failed"}:
        return extracted["status"]
    text = extracted.get("combined_text") or "\n".join(extracted.get("pages", []))
    if extracted.get("method") == "text" and text.strip():
        return "parsed_text"
    if text.strip():
        return "parsed_ocr"
    return "needs_manual_review"


def parse_money_from_text(text, labels):
    # Fix OCR inside the search block allowing O/I/Z/B as digits in this context
    for label in labels:
        # We allow common letters that look like digits
        pattern = rf"{label}[^0-9OQDIlZ|B]*([\dOQDIlZ|B][\dOQDIlZ|B.,]*)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return parse_decimal(match.group(1))
    return Decimal("0")


def parse_first_match(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return normalize_text(match.group(1))
    return ""


def normalize_doc_number(value):
    text = normalize_text(value).upper()
    match = re.search(r"\b([0-9]{3,6}[A-Z]?)\b", text)
    return match.group(1) if match else text


def number_prefix(value):
    return normalize_doc_number(str(value or "").split("/", 1)[0])


def collect_spm_number_candidates(text_spm="", text_spp="", filename_spm="", text_invoice="", text=""):
    candidates = []
    for source, value in (
        ("spm_body", text_spm),
        ("spp_body", text_spp),
        ("filename", filename_spm),
        ("invoice", number_prefix(text_invoice)),
    ):
        number = normalize_doc_number(value)
        if number and is_valid_doc_number(number):
            candidates.append({"number": number, "source": source})
    seen_table = set()
    for invoice in _RE_INVOICE_BARE.findall(text or ""):
        number = number_prefix(invoice)
        if number and number not in seen_table:
            candidates.append({"number": number, "source": "table_detail"})
            seen_table.add(number)
    return candidates


def clean_description(value):
    text = normalize_text(value)
    text = re.sub(r"^Pembayaran\s*[:\-]\s*Pembayaran\b", "Pembayaran", text, flags=re.IGNORECASE)
    text = re.sub(r"\bNo,\s*(?=[A-Z0-9])", "No. ", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\s+Kode\s+Akun\s+Pajak\b.*?(?:\|\s*)?(makan\s+bulan\b)",
        r" \1",
        text,
        flags=re.IGNORECASE,
    )
    text = re.split(
        r"\b(?:Kode\s+Akun\s+Pajak|Masa\s+Pajak|Jumlah\s+Pembayaran|SURAT\s+SETORAN\s+PAJAK|Ruang\s+Validasi)\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    text = re.sub(r"\bNPWP\s*[12]?\b\s*[:;+|]?\s*[0-9 .-]*", " ", text, flags=re.IGNORECASE)
    # Pada OCR dua kolom, label NOP/ALAMAT dapat tersisip di tengah uraian.
    # Buang label/nilai alamatnya saja; jangan memotong kelanjutan uraian.
    text = re.sub(r"\bNOP\b\s*[:;|]?", " ", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\bALAMAT\b\s*[:;|]?\s*(?:J(?:L|I)\.?\s*)?[^,;|]*?\bNO\.?\s*\d+[A-Z]?\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"(\bsebanyak\s+\d+\s+pegawai\b).*$", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text


def title_with_acronyms(value):
    text = normalize_text(value).title()
    for acronym in ("SPM", "SPP", "SP2D", "PPNPN", "PPPK", "PNS", "TNI", "POLRI", "LS", "GUP", "TUP", "UP"):
        text = re.sub(rf"\b{acronym.title()}\b", acronym, text)
    return text


def extract_uraian(text):
    for match in re.finditer(
        # Scan SPM sering kehilangan huruf awal U ("raian") dan formulir SSP
        # memakai label "Uraian Pembayaran" sebelum isi yang juga diawali kata
        # Pembayaran. Keduanya tetap harus menghasilkan isi uraian, bukan baris
        # COA setelah header kolom "... - Uraian".
        r"(?:U?RAIAN|KEPERLUAN)(?:\s+PEMBAYARAN)?\s*[:;]?\s*"
        r"(Pembayaran\b.*?)(?="
        r"\s+(?:Semua|JUMLAH\s+PENGELUARAN|Kebenaran\s+perhitungan)\b)",
        text,
        re.IGNORECASE | re.DOTALL,
    ):
        description = clean_description(match.group(1))
        # OCR tabel kadang memasangkan header URAIAN dengan baris COA. Jangan
        # gunakan potongan itu sebagai deskripsi transaksi.
        if re.match(
            r"^(?:Pembayaran\s+)?\d{4,6}[.\s]+\d{3}[.\s]+[4589]\d{5}\b",
            description,
            re.IGNORECASE,
        ):
            continue
        if description:
            return description
    return ""


def prefer_richer_description(primary, fallback):
    """Prefer an explanatory payment narrative over a short account label."""
    primary = clean_description(primary)
    fallback = clean_description(fallback)
    if not primary:
        return fallback
    if not fallback:
        return primary

    primary_words = re.findall(r"[A-Z0-9]+", primary.upper())
    fallback_words = re.findall(r"[A-Z0-9]+", fallback.upper())
    primary_is_generic_label = (
        len(primary_words) <= 6
        and bool(re.match(r"^(?:BE[LT]ANJA|PEMBAYARAN|BIAYA)\b", primary.upper()))
    )
    fallback_is_explanatory = (
        len(fallback_words) >= len(primary_words) + 4
        and len(fallback) >= len(primary) * 2
    )
    if primary_is_generic_label and fallback_is_explanatory:
        return fallback
    return primary


def extract_jenis_spm(text):
    match = re.search(
        r"Jenis\s+Tagihan\s*[:;]?\s*([A-Za-z0-9 /._-]+?)\s+Dasar\s+Pembayaran\s+([A-Za-z0-9 /._-]+?)\s+DIPA",
        text,
        re.IGNORECASE,
    )
    if match:
        code = normalize_text(match.group(1))
        code = re.sub(r"\bGAJI\s*LAINNYA\b", "GAJI LAINNYA", code, flags=re.IGNORECASE)
        label = re.sub(
            r"\b(?:NOMOR|NOM|NEM)\s*$",
            "",
            normalize_text(match.group(2)),
            flags=re.IGNORECASE,
        ).strip()
        if code.isdigit():
            return f"{code} - {label.upper()}"
        return title_with_acronyms(f"{code} {label}")
    simple_match = re.search(
        r"Jenis\s+Tagihan\s*[:;]?\s*(.{2,100}?)(?=\s+(?:Dasar\s+Pembayaran|DIPA|Jatuh\s+Tempo|Cara\s+Bayar|Tanggal)\b)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if simple_match:
        value = normalize_text(simple_match.group(1)).strip(" |:;,-")
        value = re.sub(r"\bGAJIKE\b", "GAJI KE", value, flags=re.IGNORECASE)
        value = re.sub(r"\bGAJI\s*LAINNYA\b", "GAJI LAINNYA", value, flags=re.IGNORECASE)
        return title_with_acronyms(value)
    return parse_first_match(text, [
        r"(?:JENIS\s+SPM|JENIS\s+SPP)\s*[:\-]?\s*([A-Z0-9 /._-]{2,80})",
        r"\b(UP|GUP|TUP|PTUP|LS(?:\s+[A-Z ]{2,40})?)\b",
    ])


def extract_cara_pembayaran(text, jenis_spm=""):
    match = re.search(
        r"Cara\s+Pembayaran\s+(?:Pembayaran\s+[^.]{0,120}?\s+)?dilakukan\s+melalui\s+([A-Za-z ]+)",
        text,
        re.IGNORECASE,
    )
    if match:
        return title_with_acronyms(match.group(1))
    if "PENGHASILAN PPNPN" in normalize_text(jenis_spm).upper():
        return "LS Pegawai"
    return "LS Non Kontraktual" if is_ls_text(jenis_spm) else normalize_text(jenis_spm)


def is_ls_text(value):
    text = normalize_text(value).upper()
    return (
        text.startswith("LS")
        or "PENGHASILAN PPNPN" in text
        or "GAJI" in text
        or "TUNJANGAN" in text
        or "TUKIN" in text
    )


def extract_sp2d_date(text, nomor_sp2d=""):
    patterns = []
    if nomor_sp2d:
        escaped = re.escape(str(nomor_sp2d))
        patterns.append(rf"{escaped}\s*\|\s*([0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}})")
    patterns.extend([
        r"(?:TGL\.?\s*SP2D|TANGGAL\s+SP2D)\s*[:\-]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})",
        r"\b(20[0-9]{2}-[0-9]{2}-[0-9]{2})\b",
    ])
    return parse_date(parse_first_match(text, patterns))


def extract_fp_number(text):
    match = re.search(r"\b(FP\s*-\s*[0-9]{4}\s*-\s*[0-9]{3,6}\s*-\s*[0-9]{3,6}\s*-\s*[0-9 ]{1,6})\b", text, re.IGNORECASE)
    if not match:
        return ""
    return re.sub(r"\s*-\s*", "-", normalize_text(match.group(1).upper())).replace(" ", "")


def extract_pembebanan_value(text, akun_values):
    akun_values = [akun for akun in akun_values if akun]
    for akun in akun_values:
        direct = re.search(rf"\b(\d{{4}})\.([A-Z]{{3}})\.(\d{{3}})\.(\d{{3}})\.{re.escape(akun)}\b", text, re.IGNORECASE)
        if direct:
            return f"{direct.group(1)}.{direct.group(2).upper()}.{direct.group(3)}.{direct.group(4)}.{akun}"
        compact_coa = compact_pembebanan_from_coa(text, akun)
        if compact_coa:
            return compact_coa
        pattern = rf"\(({re.escape(akun)})\).*?\((\d{{4}})\).*?\((E[A-Z]{{2}})\).*?\((\d{{3}})\).*?\((\d{{3}})\)"
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            return f"{match.group(2)}.{match.group(3).upper()}.{match.group(4)}.{match.group(5)}.{match.group(1)}"
        compact = re.search(rf"(\d{{4}})\s*[\.,]?\s*(E[A-Z]{{2}})\s*[\.,]\s*(\d{{3}})\s*[\.,]\s*([0-9A-Z]{{2,3}}).*?({re.escape(akun)})", text, re.IGNORECASE)
        if compact:
            component = compact.group(4)
            if len(component) == 2 and component[0].isalpha():
                continue
            return f"{compact.group(1)}.{compact.group(2).upper()}.{compact.group(3)}.{component.zfill(3)}.{compact.group(5)}"
    return ""


def parse_spm_detail_items(text, default_description=""):
    compact = normalize_text(text).upper()
    starts = [match.start() for match in re.finditer(r"\b\d{4,6}[\s.,]+\d{3}[\s.,]+5\d{5}[\s.,]", compact)]
    items = []
    seen = set()
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else min(len(compact), start + 700)
        block = compact[start:end]
        akun_match = re.search(r"\b\d{4,6}[\s.,]+\d{3}[\s.,]+(5\d{5})[\s.,]", block)
        if not akun_match:
            continue
        akun = akun_match.group(1)
        item_code_match = re.search(r"\b(\d{3})[\s.](\d{3})[\s.]([0-9A-Z]{2})[\s.](\d{3,6})\b", block)
        coa_text = block[:item_code_match.end()] if item_code_match else block[:220]
        pembebanan = compact_pembebanan_from_coa(coa_text, akun)
        if not pembebanan:
            continue
        amount_text = block[item_code_match.end():] if item_code_match else block
        amount_matches = list(re.finditer(r"\b\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?\b", amount_text))
        amounts = [parse_decimal(match.group(0)) for match in amount_matches]
        amounts = [amount for amount in amounts if amount > 0]
        if not amounts:
            continue
        description_text = ""
        if amount_matches:
            description_text = amount_text[:amount_matches[0].start()]
        description_text = re.sub(r"^\s*[-:]\s*", "", description_text)
        description_text = re.sub(r"\b(?:JUMLAH|TOTAL)\s+PENGELUARAN\b.*$", "", description_text, flags=re.IGNORECASE)
        keperluan = clean_description(description_text) or default_description
        key = (pembebanan, item_code_match.group(4) if item_code_match else "", amounts[0], normalize_text(keperluan).upper())
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "akun": akun,
            "jumlah": amounts[0],
            "bruto": amounts[0],
            "netto": amounts[0],
            "no_bukti": item_code_match.group(4) if item_code_match else "",
            "keperluan": keperluan,
            "pembebanan": pembebanan,
        })
    return items


def parse_spm_detail_items_from_pages(page_details, default_description=""):
    items = []
    seen = set()
    source_types = {"DETAIL_SPP_SPM_SP2D", "LAMPIRAN_COA"}
    for page in page_details:
        if not source_types.intersection(page.get("page_types") or []):
            continue
        page_items = parse_spm_detail_items(page.get("text") or page.get("extracted_text") or "", default_description)
        for item in page_items:
            key = (
                normalize_text(item.get("pembebanan")).upper(),
                normalize_text(item.get("no_bukti")).upper(),
                money_value_for_key(item.get("jumlah")),
                normalize_text(item.get("keperluan")).upper(),
            )
            if key in seen:
                continue
            seen.add(key)
            items.append({
                **item,
                "source_page": page.get("page_number") or page.get("page"),
                "source_types": page.get("page_types") or [],
                "confidence": page.get("confidence") or 0.0,
            })
    return items


def money_value_for_key(value):
    amount = parse_decimal(value)
    return str(amount.quantize(Decimal("0.01")))


def resolve_spm_number(filename_spm, ocr_spm, confidence=0.0, method=""):
    filename_spm = normalize_text(filename_spm).upper()
    ocr_spm = normalize_text(ocr_spm).upper()
    confidence = confidence or 0.0
    if filename_spm and ocr_spm and filename_spm != ocr_spm:
        if confidence >= 70 or method == "text":
            final = ocr_spm
            source = "ocr"
            reason = "OCR/native text terbaca jelas; filename berbeda sehingga perlu review nomor."
        else:
            final = filename_spm
            source = "filename"
            reason = "OCR lemah dan filename tersedia; perlu review nomor sebelum commit final."
        return {
            "final": final,
            "source": source,
            "conflict": True,
            "review_status": "Perlu Review Nomor",
            "reason": reason,
            "warning": "Nomor SPM OCR dan filename berbeda. Pilih nomor yang benar sebelum commit.",
        }
    if ocr_spm:
        return {
            "final": ocr_spm,
            "source": "ocr",
            "conflict": False,
            "review_status": "OK",
            "reason": "Nomor SPM diambil dari OCR/native text.",
            "warning": "",
        }
    if filename_spm:
        return {
            "final": filename_spm,
            "source": "filename",
            "conflict": False,
            "review_status": "Perlu Review Nomor",
            "reason": "Nomor SPM hanya tersedia dari filename.",
            "warning": "Nomor SPM hanya terbaca dari filename; perlu review manual.",
        }
    return {
        "final": "",
        "source": "",
        "conflict": False,
        "review_status": "Perlu Review Nomor",
        "reason": "Nomor SPM belum terbaca.",
        "warning": "Parser gagal mengambil nomor SPM dari OCR maupun filename.",
    }


def reconcile_spm_suffix_with_filename(ocr_spm, filename_spm, nomor_spp=""):
    """Pulihkan suffix huruf SPM yang dibaca OCR sebagai angka.

    Filename tidak cukup dipercaya sendirian. Koreksi hanya dilakukan bila nomor
    SPP pada dokumen mengonfirmasi digit dasar yang sama dan ketiga nomor punya
    panjang yang sama. Ini menangani kelas kesalahan OCR seperti A -> 4 tanpa
    mengunci parser ke nomor dokumen tertentu.
    """
    ocr_spm = normalize_doc_number(ocr_spm)
    filename_spm = normalize_doc_number(filename_spm)
    nomor_spp = normalize_doc_number(nomor_spp)
    if not (ocr_spm and filename_spm and nomor_spp):
        return ""
    if not (len(ocr_spm) == len(filename_spm) == len(nomor_spp) and len(ocr_spm) >= 4):
        return ""
    base = ocr_spm[:-1]
    if (
        base == filename_spm[:-1] == nomor_spp[:-1]
        and ocr_spm[-1].isdigit()
        and filename_spm[-1].isalpha()
        and nomor_spp[-1].isalpha()
    ):
        return filename_spm
    return ""


def parse_spm_pdf(file_path, ocr=False, extracted=None, parse_details=True):
    extracted = extracted or extract_pdf_text(file_path, ocr=ocr)
    page_details = annotate_page_details(extracted.get("page_details", []))
    if not page_details:
        page_details = annotate_page_details([
            {"text": page_text, "extracted_text": page_text, "page_number": index}
            for index, page_text in enumerate(extracted.get("pages", []), start=1)
        ])
    if not ocr and not any(normalize_text(page.get("text") or page.get("extracted_text") or "") for page in page_details):
        extracted = extract_pdf_text(file_path, ocr=True)
        page_details = annotate_page_details(extracted.get("page_details", []))
        if not page_details:
            page_details = annotate_page_details([
                {"text": page_text, "extracted_text": page_text, "page_number": index}
                for index, page_text in enumerate(extracted.get("pages", []), start=1)
            ])
    text = "\n".join(page.get("text") or page.get("extracted_text") or "" for page in page_details)
    spm_text = text_for_page_types(page_details, ["SPM"], fallback_all=True)
    spp_text = text_for_page_types(page_details, ["SPP"])
    detail_text = text_for_page_types(page_details, ["DETAIL_SPP_SPM_SP2D", "LAMPIRAN_COA"])
    sp2d_text = text_for_page_types(page_details, ["SP2D", "DETAIL_SPP_SPM_SP2D"])
    drpp_text = text_for_page_types(page_details, ["DRPP"])
    ssp_text = text_for_page_types(page_details, ["SSP"])
    form_fp_text = text_for_page_types(page_details, ["FORM_FP"])
    field_text = "\n".join(chunk for chunk in [spm_text, spp_text, detail_text, sp2d_text, ssp_text, form_fp_text] if chunk)
    upper = text.upper()
    upper_spm = spm_text.upper()
    upper_spp = spp_text.upper()
    upper_detail = detail_text.upper()
    upper_sp2d = sp2d_text.upper()

    # ── Ekstraksi nomor + nilai per-halaman (pisahkan SPM dari SPP) ──────────
    per_page = parse_spm_number_from_pages(page_details)
    no_spm_per_page = normalize_doc_number(per_page["no_spm"])
    no_spp_per_page = normalize_doc_number(per_page["no_spp"])
    spm_page_nums = per_page["spm_pages"]
    spp_page_nums = per_page["spp_pages"]
    jumlah_pengeluaran_per_page = per_page["jumlah_pengeluaran"]
    jumlah_potongan_per_page = per_page["jumlah_potongan"]
    total_pembayaran_per_page = per_page["total_pembayaran"]

    # Deteksi apakah PDF ini adalah paket gabungan
    is_combined_package = bool(spm_page_nums and spp_page_nums)

    # ── Fallback regex global ────────────────────────────────────────────
    nomor_match_global = re.search(
        r"SURAT\s+PERINTAH\s+MEMBAYAR.*?(?:NOMOR\s+SPM|SPM\s+NOMOR|NO\.?\s*SPM|NOMOR)\s*[:\-]?\s*([0-9A-Z./-]+)",
        upper_spm,
        re.DOTALL,
    )
    spp_match_global = re.search(
        r"SURAT\s+PERMINTAAN\s+PEMBAYARAN.*?(?:NOMOR\s+SPP|SPP\s+NOMOR|NO\.?\s*SPP|NOMOR)\s*[:\-]?\s*([0-9A-Z./-]+)",
        upper_spp,
        re.DOTALL,
    )
    # No SP2D — coba labeled dulu, lalu bare 15-digit dalam konteks halaman SP2D/detail
    sp2d_labeled = _RE_SP2D_LABELED.search(upper_sp2d)
    sp2d_bare = None
    if not sp2d_labeled:
        # Cari di halaman yang terklasifikasi sebagai sp2d atau di teks full
        for page in page_details:
            page_types = set(page.get("page_types") or [])
            page_text_upper = (page.get("text") or page.get("extracted_text") or "").upper()
            if {"SP2D", "DETAIL_SPP_SPM_SP2D"}.intersection(page_types):
                m = _RE_SP2D_BARE.search(page_text_upper)
                if m:
                    sp2d_bare = m
                    break
    text_sp2d = (
        sp2d_labeled.group(1) if sp2d_labeled
        else (sp2d_bare.group(1) if sp2d_bare else "")
    )
    tanggal_sp2d = extract_sp2d_date(sp2d_text, text_sp2d)

    # No Invoice/SPP-SPM — labeled dulu, lalu bare
    invoice_scope = upper_sp2d or upper_detail or upper_spp
    invoice_labeled = _RE_INVOICE.search(invoice_scope)
    invoice_bare = _RE_INVOICE_BARE.search(invoice_scope) if not invoice_labeled else None
    text_invoice = (
        invoice_labeled.group(1) if invoice_labeled
        else (invoice_bare.group(1) if invoice_bare else "")
    )

    # No Invoice fallback: jika tidak ada label, cari pola di halaman SP2D
    if not text_invoice:
        for page in page_details:
            page_text_upper = (page.get("text") or page.get("extracted_text") or "").upper()
            if "DETAIL PENGELUARAN" in page_text_upper or "DAFTAR SP2D" in page_text_upper:
                m = _RE_INVOICE_BARE.search(page_text_upper)
                if m:
                    text_invoice = m.group(1)
                    break

    # Prioritas nomor: per-halaman > global regex
    text_spm = no_spm_per_page
    if not text_spm and nomor_match_global:
        cand = normalize_doc_number(nomor_match_global.group(1))
        if is_valid_doc_number(cand):
            text_spm = cand

    text_spp = no_spp_per_page
    if not text_spp and spp_match_global:
        cand = normalize_doc_number(spp_match_global.group(1))
        if is_valid_doc_number(cand):
            text_spp = cand

    # ── Field lain ─────────────────────────────────────────────────────
    drpp_match = re.search(r"(?:NOMOR\s+DRPP|DRPP\s+NOMOR|NO\.?\s*DRPP)\s*[:\-]?\s*([0-9A-Z./-]+)", (drpp_text or detail_text).upper())

    # ── Satker ────────────────────────────────────────────────────────
    satker_match = re.search(r"(?:SATKER|KODE\s+SATKER)\s*[:\-]?\s*([0-9]{4,6})", (spm_text or field_text).upper())
    satker_c = satker_match.group(1) if satker_match else ""

    if not satker_c:
        dipa_match = re.search(
            r"\bDIPA[-0-9.]*[./](0\d{5})\s*[/|]\s*20\d{2}\b",
            field_text.upper(),
        )
        if dipa_match:
            satker_c = dipa_match.group(1)
    if not satker_c:
        coa_satker_match = re.search(r"\b(0\d{5})[.,]\d{3}[.,][4589]\d{5}\b", field_text)
        if coa_satker_match:
            satker_c = coa_satker_match.group(1)
    if not satker_c:
        m2 = re.search(r"(?:SATUAN KERJA|UNIT KERJA|KANTOR|INSTANSI)[\s\S]{0,100}?([0-9]{6})", (spm_text or field_text).upper())
        if m2:
            satker_c = m2.group(1)
    if not satker_c:
        m3 = re.search(r"(?:^|[^\d])(0\d{5})(?:[./\s|]|$)", field_text)
        if m3:
            satker_c = m3.group(1)

    satker_name_ocr = ""
    bps_match = re.search(r"(BADAN PUSAT STATISTIK\s+[A-Z\s.,-]{4,120})", (spm_text or field_text).upper())
    if bps_match:
        satker_name_ocr = bps_match.group(1).strip()
        satker_name_ocr = re.sub(r"\s+", " ", satker_name_ocr)
        # Hentikan jika ketemu kata kunci yang tidak terkait satker
        stop_words = ["SPP", "SPM", "KUITANSI", "YANG", "TANGGAL", "NOMOR", "TAHUN"]
        for sw in stop_words:
            if f" {sw}" in satker_name_ocr:
                satker_name_ocr = satker_name_ocr.split(f" {sw}")[0]

    satker_app_code = ""
    satker_app_name = ""
    if satker_name_ocr or satker_c:
        from apps.core.satker import infer_satker_from_name
        code, name = infer_satker_from_name(satker_name_ocr)
        if code:
            satker_app_code = code
            satker_app_name = name
        elif satker_c:
            # Fallback code
            from apps.core.satker import fallback_satker_name
            fallback = fallback_satker_name(satker_c)
            if fallback:
                satker_app_code = satker_c
                satker_app_name = fallback
    tanggal_spm = parse_date(parse_first_match(spm_text, [
        r"(?:TANGGAL\s+SPM|TANGGAL)\s*[:\-]?\s*([0-9]{1,2}[\s\-/][a-zA-Z0-9]+[\s\-/][0-9]{2,4})",
        r"\b([0-9]{4}-[0-9]{2}-[0-9]{2})\b",
    ]))
    jenis_spm = extract_jenis_spm(spm_text)
    cara_pembayaran = extract_cara_pembayaran(spm_text, jenis_spm)
    kppn = parse_first_match(spm_text, [r"KPPN\s*[:\-]?\s*([A-Z0-9 ._-]{2,80})"])
    supplier = parse_first_match(spm_text, [r"(?:SUPPLIER|PENERIMA|NAMA\s+PENERIMA)\s*[:\-]?\s*([A-Z0-9 .,'/-]{3,120})"])
    bank = parse_first_match(spm_text, [r"(?:BANK)\s*[:\-]?\s*([A-Z0-9 .,'/-]{2,80})"])
    rekening = parse_first_match(spm_text, [r"(?:REKENING|NO\.?\s*REK)\s*[:\-]?\s*([0-9 .-]{5,80})"])
    npwp_nik = parse_first_match(spm_text, [r"(?:NPWP|NIK)\s*[:\-]?\s*([0-9 .-]{10,40})"])
    uraian = extract_uraian(spm_text) or parse_first_match(spm_text, [r"(?:URAIAN|KEPERLUAN)\s*[:\-]?\s*(.{10,300})"])
    amount_values = re.findall(r"\b\d{1,3}(?:[.,]\d{3})+(?:,\d{2})?\b", field_text)
    # Pembebanan/COA 16-segmen: AAAA.BBB.CCC.DDD.XXXXXX
    pembebanan_values = sorted(set(_RE_PEMBEBANAN.findall((detail_text or spm_text).upper())))

    # Ekstrak Akun dari pola COA dan teks bebas
    # Ekstrak Akun dari pola COA dan teks bebas
    coa_scope = (detail_text or spm_text).upper()
    coa_pattern = re.findall(r"\b\d{4,6}\.[0-9A-Z]{2,4}\.([4589]\d{5})\b", coa_scope)
    dot_pattern = re.findall(r"\.([4589]\d{5})\.", coa_scope)
    standalone = re.findall(r"\b([4589]\d{5})\b", coa_scope)

    akun_pengeluaran = []
    akun_potongan = []

    # ── Potongan Block Parsing ──
    # Look for a "POTONGAN" section and extract COAs near it.
    potongan_blocks = []
    potongan_warnings = []

    for terminator_match in re.finditer(r"(?:JUMLAH\s+POTONGAN|TOTAL\s+POTONGAN)", upper_spm):
        terminator_idx = terminator_match.start()
        # Find all POTONGAN before terminator
        potongan_matches = list(re.finditer(r"\bPOTONGAN\b", upper_spm[:terminator_idx]))
        if potongan_matches:
            # Pick the last one (closest to terminator)
            start_idx = potongan_matches[-1].start()
            block = upper_spm[start_idx:terminator_idx]

            # Validate the block isn't absurdly long (meaning it didn't find the real POTONGAN header but a random word far away)
            if len(block) < 3000:
                # Validate it contains at least one account code and one nominal
                has_akun = bool(re.search(r"\b([4589]\d{5})\b", block))
                has_nominal = bool(re.search(r"\d{3,}(?:[,.]\d{2})?", block))
                if has_akun and has_nominal:
                    potongan_blocks.append(block)
            else:
                potongan_warnings.append("Boundary potongan terlalu panjang, parsing potongan perlu direview.")

    if potongan_warnings:
        extracted.setdefault("warnings", []).extend(potongan_warnings)

    for block in potongan_blocks:
        block_coa = re.findall(r"\b\d{4,6}\.[0-9A-Z]{2,4}\.([4589]\d{5})\b", block)
        block_dot = re.findall(r"\.([4589]\d{5})\.", block)
        block_standalone = re.findall(r"\b([4589]\d{5})\b", block)
        for cand in block_coa + block_dot + block_standalone:
            if cand != satker_c and cand != text_sp2d:
                if cand not in akun_potongan:
                    akun_potongan.append(cand)

    # Prioritaskan coa_pattern dan dot_pattern untuk akun_pengeluaran
    for cand in coa_pattern + dot_pattern:
        if cand == satker_c or cand == text_sp2d:
            continue
        if cand.startswith("5"):
            if cand not in akun_pengeluaran:
                akun_pengeluaran.append(cand)

    # Tambah standalone hanya jika coa_pattern belum dapat akun 5
    if not akun_pengeluaran:
        for cand in standalone:
            if cand == satker_c or cand == text_sp2d:
                continue
            if cand.startswith("5") and cand not in akun_pengeluaran:
                akun_pengeluaran.append(cand)

    # Only if we didn't find any in the POTONGAN block, we can tentatively add 4 or 8 from coa_pattern
    if not akun_potongan:
        for cand in coa_pattern + dot_pattern:
            if cand == satker_c or cand == text_sp2d:
                continue
            if cand.startswith("4") or cand.startswith("8"):
                if cand not in akun_potongan:
                    akun_potongan.append(cand)

    akun_pengeluaran.sort()
    akun_potongan.sort()

    # Remove akun_potongan from akun_pengeluaran if it accidentally got added
    akun_pengeluaran = [a for a in akun_pengeluaran if a not in akun_potongan]
    pembebanan_utama = extract_pembebanan_value(coa_scope, akun_pengeluaran)
    if pembebanan_utama:
        pembebanan_values = [pembebanan_utama] + [item for item in pembebanan_values if item != pembebanan_utama]
    fp_number = extract_fp_number(field_text)
    detail_items = parse_spm_detail_items_from_pages(page_details, uraian) if parse_details else []
    if parse_details and not detail_items:
        detail_items = parse_spm_detail_items(detail_text or spm_text, uraian)

    # Format untuk backward compatibility
    akun_values = akun_pengeluaran.copy()
    if not akun_values and akun_potongan:
        akun_values = akun_potongan.copy()

    # ── Nilai keuangan ──────────────────────────────────────────────────
    # Prioritas: nilai dari halaman SPM > nilai global dari semua halaman
    jumlah_pengeluaran = jumlah_pengeluaran_per_page or parse_money_from_text(
        upper_spm, ["JUMLAH PENGELUARAN", "NILAI PENGELUARAN", "NILAI SPM"]
    )
    jumlah_potongan = jumlah_potongan_per_page or parse_money_from_text(
        upper_spm, ["JUMLAH POTONGAN", "TOTAL POTONGAN", "POTONGAN"]
    )
    total_pembayaran = total_pembayaran_per_page or parse_money_from_text(
        upper_spm, ["TOTAL PEMBAYARAN", "JUMLAH YANG DIBAYARKAN", "NETO"]
    )
    total_pembayaran_terbaca_langsung = total_pembayaran > 0
    # total fallback: pengeluaran - potongan, atau dari label generic
    if total_pembayaran <= 0 and jumlah_pengeluaran > 0 and jumlah_potongan > 0:
        total_pembayaran = jumlah_pengeluaran - jumlah_potongan
    if total_pembayaran <= 0:
        total_pembayaran = parse_money_from_text(
            upper_spm, ["JUMLAH PENGELUARAN", "NILAI SPM", "JUMLAH"]
        )
    # Pada scan tertentu label bruto tidak terbaca, sedangkan netto dan potongan
    # terbaca jelas. Target tabel tetap harus bruto, bukan netto. Nilai turunan ini
    # baru dipermanenkan setelah cocok dengan total baris tabel terstruktur.
    bruto_turunan = Decimal("0")
    if jumlah_pengeluaran <= 0 and total_pembayaran_terbaca_langsung:
        bruto_turunan = total_pembayaran + max(jumlah_potongan, Decimal("0"))
    expected_position_total = jumlah_pengeluaran or bruto_turunan or total_pembayaran
    if parse_details:
        position_items, position_summary = parse_position_detail_items(
            file_path,
            page_details,
            uraian,
            expected_total=expected_position_total,
        )
    else:
        position_items, position_summary = [], {
            "source": "DEFERRED_TO_DRPP",
            "rows_before_dedupe": 0,
            "rows_after_dedupe": 0,
            "total": Decimal("0"),
            "pages": {},
        }
    # Tabel resmi pada halaman scan landscape dapat gagal dikelompokkan oleh
    # Tesseract Windows walaupun lampiran COA portrait terbaca jelas. Jangan
    # mengubahnya menjadi baris 0: terima lampiran COA hanya bila akun, program,
    # item, pembebanan, dan total bruto semuanya tervalidasi secara terstruktur.
    if parse_details and not position_items and expected_position_total:
        validated_lampiran_rows = parse_validated_lampiran_coa_pages(
            page_details,
            expected_position_total,
            uraian,
        )
        if validated_lampiran_rows:
            validated_total = sum(
                (parse_decimal(item.get("jumlah")) for item in validated_lampiran_rows),
                Decimal("0"),
            )
            position_items = validated_lampiran_rows
            position_summary = {
                "source": "LAMPIRAN_COA_VALIDATED",
                "rows_before_dedupe": len(validated_lampiran_rows),
                "rows_after_dedupe": len(validated_lampiran_rows),
                "total": validated_total,
                "pages": position_summary.get("pages") or {},
            }
    if position_items and bruto_turunan > 0 and jumlah_pengeluaran <= 0:
        total_rincian_posisi = sum(
            (parse_decimal(item.get("jumlah")) for item in position_items),
            Decimal("0"),
        )
        if abs(total_rincian_posisi - bruto_turunan) <= Decimal("1"):
            jumlah_pengeluaran = bruto_turunan
            extracted.setdefault("warnings", []).append(
                "Bruto SPM tidak terbaca langsung; dihitung dari netto + potongan "
                "dan telah cocok dengan total rincian tabel."
            )
    table_ocr_text = "\n".join(
        chunk
        for page in (position_summary.get("pages") or {}).values()
        for chunk in [page.get("text") or ""] + [variant.get("text") or "" for variant in page.get("variants") or []]
        if chunk
    )
    if not text_sp2d and table_ocr_text:
        table_sp2d = _RE_SP2D_LABELED.search(table_ocr_text.upper()) or _RE_SP2D_BARE.search(table_ocr_text.upper())
        if table_sp2d:
            text_sp2d = table_sp2d.group(1)
    # Nomor SP2D kadang rusak oleh garis tabel, sedangkan tanggal ISO pada sel
    # sebelahnya tetap jelas. Tanggal tidak boleh ikut dibuang hanya karena
    # nomor 15 digit gagal dikenali.
    if not tanggal_sp2d and table_ocr_text:
        tanggal_sp2d = extract_sp2d_date(table_ocr_text, text_sp2d)
    # TSV tabel biasanya lebih stabil daripada OCR paragraf, terutama pada scan
    # yang diputar. Gunakan field terstruktur sebagai fallback metadata SP2D.
    if position_items:
        if not text_sp2d:
            text_sp2d = next(
                (normalize_text(item.get("nomor_sp2d")) for item in position_items if re.fullmatch(r"2\d{14}", normalize_text(item.get("nomor_sp2d")))),
                "",
            )
        if not tanggal_sp2d:
            tanggal_sp2d = next(
                (parse_date(item.get("tanggal_sp2d")) for item in position_items if parse_date(item.get("tanggal_sp2d"))),
                "",
            )
    if position_items:
        detail_items = position_items
        for item in detail_items:
            if len(detail_items) == 1 and total_pembayaran > 0:
                item["netto"] = total_pembayaran
                item.setdefault("field_provenance", {})["netto"] = {
                    "page": spm_page_nums[0] if spm_page_nums else None,
                    "method": "spm_header",
                    "confidence": extracted.get("confidence", 0.0),
                }
            if len(detail_items) == 1 and jumlah_potongan > 0 and "411121" in akun_potongan:
                item["pph21"] = jumlah_potongan
                item.setdefault("field_provenance", {})["pph21"] = {
                    "page": spm_page_nums[0] if spm_page_nums else None,
                    "method": "spm_header",
                    "confidence": extracted.get("confidence", 0.0),
                }
            if not is_valid_pembebanan(item.get("pembebanan"), item.get("jumlah")):
                item["needs_review"] = True
                item["review_note"] = "Pembebanan tidak valid atau mengandung nominal."
    elif position_summary.get("source") == "PERLU_REVIEW_PARSER_TABEL":
        detail_items = []
    detail_total = position_summary.get("total") or sum((parse_decimal(item.get("jumlah")) for item in detail_items), Decimal("0"))
    expected_detail_total = jumlah_pengeluaran or total_pembayaran
    detail_source = position_summary.get("source") or ("LAMPIRAN_COA" if detail_items else "fallback_total")
    if detail_items and expected_detail_total and abs(detail_total - expected_detail_total) > Decimal("1"):
        detail_source = "PERLU_REVIEW_PARSER_TABEL"
        extracted.setdefault("warnings", []).append(
            f"Total rincian tabel Rp{detail_total:,.0f} tidak sama dengan bruto SPM Rp{expected_detail_total:,.0f}."
        )
    detail_parse_summary = {
        "source": detail_source,
        "rows_before_dedupe": position_summary.get("rows_before_dedupe") or len(detail_items),
        "rows_after_dedupe": position_summary.get("rows_after_dedupe") or len(detail_items),
        "total": detail_total,
    }

    status = parser_status(extracted)
    if extracted["method"] == "failed":
        status = "failed" if not extracted["warnings"] else "needs_manual_review"

    # ── Resolusi nomor SPM Utama (D_K) ───────────────────────────────────────────────────
    filename_spm = guess_number_from_filename(file_path, "SPM")
    warnings = list(extracted["warnings"])

    # Prioritas Nomor SPM Utama D_K:
    # 1. Halaman SPM (text_spm)
    # 2. filename (sebagai low-confidence fallback)
    nomor_spm_candidates = collect_spm_number_candidates(text_spm, text_spp, filename_spm, text_invoice, detail_text)
    nomor_spm_terkoreksi = reconcile_spm_suffix_with_filename(text_spm, filename_spm, text_spp)
    if nomor_spm_terkoreksi:
        nomor_spm_utama = nomor_spm_terkoreksi
        source_utama = "filename_confirmed_by_spp_prefix"
        review_status = "OK"
        reason = "Suffix huruf filename dikonfirmasi oleh digit dasar nomor SPP pada dokumen."
        warning = ""
        warnings.append(
            f"Suffix Nomor SPM OCR {text_spm} dikoreksi menjadi {nomor_spm_terkoreksi}; "
            "digit dasarnya cocok dengan nomor SPP dan filename."
        )
    elif text_spm:
        nomor_spm_utama = text_spm
        source_utama = "ocr"
        review_status = "OK"
        reason = "Diambil dari dokumen OCR halaman SPM."
        warning = ""
    elif filename_spm:
        nomor_spm_utama = filename_spm
        source_utama = "filename"
        review_status = "Perlu Review Nomor"
        reason = "Nomor SPM tidak terbaca dari OCR, menggunakan filename sebagai fallback."
        warning = "Nomor SPM menggunakan fallback filename. Mohon pastikan kebenarannya."
    else:
        nomor_spm_utama = ""
        source_utama = ""
        review_status = "Perlu Review Nomor"
        reason = "Tidak terbaca."
        warning = "Gagal mengekstrak Nomor SPM dari OCR maupun filename."

    nomor_spm_res = {
        "final": nomor_spm_utama,
        "source": source_utama,
        "conflict": False,
        "review_status": review_status,
        "reason": reason,
        "warning": warning
    }

    if is_combined_package:
        warnings.append(
            f"PDF gabungan terdeteksi: halaman SPM={spm_page_nums}, halaman SPP={spp_page_nums}."
        )
    warnings.extend(detect_mismatched_lampiran_numbers(page_details, text_spm, text_spp))

    if total_pembayaran <= 0:
        warnings.append("Parser gagal mengambil nilai total pembayaran SPM dari dokumen.")

    return {
        "file_name": os.path.basename(file_path),
        "page_count": extracted["page_count"],
        "method": extracted["method"],
        "best_engine": extracted.get("best_engine", extracted["method"]),
        "status": status,
        "warnings": warnings,
        "page_details": page_details,
        "confidence": extracted.get("confidence", 0.0),
        "engines_tried": extracted.get("engines_tried", []),
        "native_text_length": extracted.get("native_text_length", 0),
        "tesseract_called": extracted.get("tesseract_called", False),
        "tesseract_text_length": extracted.get("tesseract_text_length", 0),
        "tesseract_reason": extracted.get("tesseract_reason", ""),
        "paddleocr_called": extracted.get("paddleocr_called", False),
        "paddleocr_text_length": extracted.get("paddleocr_text_length", 0),
        "is_combined_package": is_combined_package,
        "metadata": {
            "nomor_spm": nomor_spm_res["final"],
            "nomor_spm_final": nomor_spm_res["final"],
            "nomor_spm_final_source": nomor_spm_res["source"],
            "nomor_spm_ocr": text_spm,  # Nomor SPM resmi DJPb dari OCR
            "nomor_spm_filename": filename_spm,
            "nomor_spm_conflict": nomor_spm_res["conflict"],
            "nomor_spm_review_status": nomor_spm_res["review_status"],
            "nomor_spm_reason": nomor_spm_res["reason"],
            "nomor_spm_candidates": nomor_spm_candidates,
            "nomor_spp": text_spp,
            "nomor_spp_per_page": no_spp_per_page,
            "nomor_spp_global": normalize_doc_number(spp_match_global.group(1)) if spp_match_global else "",
            "nomor_sp2d": text_sp2d,
            "tanggal_sp2d": tanggal_sp2d,
            "nomor_invoice": text_invoice,
            "nomor_drpp": drpp_match.group(1) if drpp_match else "",
            "satker_code": satker_c,
            "satker_djpb_code": satker_c,
            "satker_name_ocr": satker_name_ocr,
            "satker_app_code": satker_app_code,
            "satker_app_name": satker_app_name,
            "tanggal_spm": tanggal_spm,
            "jenis_spm": jenis_spm,
            "cara_pembayaran": cara_pembayaran,
            "kppn": kppn,
            "supplier": supplier,
            "bank": bank,
            "rekening": rekening,
            "npwp_nik": npwp_nik,
            "uraian": uraian,
            "fp": fp_number,
            "total_pembayaran": total_pembayaran,
            "jumlah_pengeluaran": jumlah_pengeluaran,
            "jumlah_potongan": jumlah_potongan,
            "spm_page_nums": spm_page_nums,
            "spp_page_nums": spp_page_nums,
            "field_sources": {
                "nomor_spm": field_source(page_details, text_spm, ["SPM"]),
                "nomor_spp": field_source(page_details, text_spp, ["SPP"]),
                "nomor_sp2d": field_source(page_details, text_sp2d, ["SP2D", "DETAIL_SPP_SPM_SP2D"]),
                "tanggal_spm": field_source(page_details, tanggal_spm, ["SPM"]),
                "tanggal_sp2d": field_source(page_details, tanggal_sp2d, ["SP2D", "DETAIL_SPP_SPM_SP2D"]),
                "jumlah_pengeluaran": field_source(page_details, jumlah_pengeluaran, ["SPM"]),
                "total_pembayaran": field_source(page_details, total_pembayaran, ["SPM"]),
            },
            "pembebanan_list": pembebanan_values[:30],
            "akun_pengeluaran": akun_pengeluaran,
            "akun_potongan": akun_potongan,
            "detail_parse_summary": detail_parse_summary,
        },
        "detail_items": detail_items[:300],
        "akun_rows": [
            {"akun": akun, "uraian": uraian, "nilai": "", "pembebanan": next(
                (p for p in pembebanan_values if p.endswith(akun)), ""
            ), "fp": fp_number}
            for akun in akun_values[:50]
        ],
        "amount_samples": amount_values[:20],
        "text_sample": text[:2000],
    }


DRPP_STOP_KEYWORDS = [
    "LAMPIRAN DAFTAR",
    "PEJABAT PEMBUAT",
    "LEMBAR",
    "JUMLAH LAMPIRAN",
    "JUMLAH SPP INI",
]


def extract_drpp_expense_block(text):
    upper = text.upper()
    start = upper.find("BUKTI PENGELUARAN")
    if start < 0:
        start = upper.find("DAFTAR RINCIAN PERMINTAAN PEMBAYARAN")
    block = text[start:] if start >= 0 else text
    upper_block = block.upper()
    stop_positions = [upper_block.find(keyword) for keyword in DRPP_STOP_KEYWORDS if upper_block.find(keyword) > 0]
    if stop_positions:
        block = block[: min(stop_positions)]
    return block


def compact_drpp_lines(text):
    lines = [normalize_text(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    if len(lines) <= 1:
        lines = re.split(r"\s{2,}", normalize_text(text))
    return [line for line in lines if normalize_text(line)]


def parse_drpp_items_from_text(text):
    block = extract_drpp_expense_block(text)
    compact_block = normalize_text(block).replace("§", "5")
    rich_pattern = re.compile(
        r"(?P<no>\d{1,3})\s+"
        r"(?P<bukti>\d{3,6}/KW/[0-9A-Z./-]+)\s+"
        r"(?P<penerima>.*?)\s+"
        r"(?P<npwp>\d{12,20})\s+"
        r"(?P<akun>5\d{5,7})\s+"
        r"(?P<jumlah>\d{1,3}(?:[.,]\d{3})+(?:,\d{2})?)\s*[.;:]?\s+"
        r"(?P<tanggal>\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\s+"
        r"(?P<keperluan>.*?)(?=\s+\d{1,3}\s+\d{3,6}/KW/|\s+JUMLAH SPP INI|\s+LEMBAR|\Z)",
        re.IGNORECASE,
    )
    rich_items = []
    for match in rich_pattern.finditer(compact_block):
        rich_items.append(
            {
                "no_urut": int(match.group("no")),
                "no_bukti": normalize_text(match.group("bukti")),
                "tanggal_bukti": match.group("tanggal"),
                "penerima": normalize_text(match.group("penerima"))[:200],
                "npwp": match.group("npwp"),
                "akun": match.group("akun")[:8],
                "jumlah": parse_decimal(match.group("jumlah")),
                "keperluan": normalize_text(match.group("keperluan"))[:500],
            }
        )
    if rich_items:
        return rich_items

    lines = compact_drpp_lines(block)
    items = []
    pending = ""
    row_pattern = re.compile(
        r"^\s*(?P<no>\d{1,3})\s+"
        r"(?P<bukti>[0-9A-Z./-]{3,})?\s*"
        r"(?P<body>.*?)"
        r"(?P<akun>5\d{5})\s+"
        r"(?P<jumlah>\d{1,3}(?:[.,]\d{3})+(?:,\d{2})?)\s*$",
        re.IGNORECASE,
    )
    for line in lines:
        upper = line.upper()
        if any(keyword in upper for keyword in DRPP_STOP_KEYWORDS):
            break
        if "AKUN" in upper and "JUMLAH" in upper:
            continue
        candidate = f"{pending} {line}".strip() if pending else line
        match = row_pattern.search(candidate)
        if not match:
            if re.match(r"^\d{1,3}\b", line) or pending:
                pending = candidate
            continue
        body = normalize_text(match.group("body"))
        tanggal_match = re.search(r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}-\d{2}-\d{2})\b", body)
        npwp_match = re.search(r"\b\d{2}[.\d-]{10,}\b", body)
        no_bukti = normalize_text(match.group("bukti"))
        if not no_bukti:
            kw_match = re.search(r"(?:KW|KUITANSI|BUKTI)\s*[:\-]?\s*([0-9A-Z./-]{3,})", body, re.IGNORECASE)
            no_bukti = kw_match.group(1) if kw_match else ""
        items.append(
            {
                "no_urut": int(match.group("no")),
                "no_bukti": no_bukti,
                "tanggal_bukti": tanggal_match.group(1) if tanggal_match else "",
                "penerima": "",
                "npwp": npwp_match.group(0) if npwp_match else "",
                "akun": match.group("akun"),
                "jumlah": parse_decimal(match.group("jumlah")),
                "keperluan": body[:500],
            }
        )
        pending = ""
    return items


def _to_tsv_word(raw_word):
    text = normalize_text(raw_word.get("text", ""))
    if not text:
        return None
    try:
        left = int(float(raw_word.get("left", 0)))
        top = int(float(raw_word.get("top", 0)))
        width = int(float(raw_word.get("width", 0)))
        height = int(float(raw_word.get("height", 0)))
    except (TypeError, ValueError):
        return None
    raw_conf = raw_word.get("confidence", raw_word.get("conf", 100))
    try:
        confidence = float(raw_conf)
    except (TypeError, ValueError):
        confidence = 100.0
    return {
        "text": text,
        "left": left,
        "top": top,
        "width": width,
        "height": height,
        "right": left + width,
        "bottom": top + height,
        "center_x": left + (width / 2),
        "center_y": top + (height / 2),
        "confidence": confidence,
    }


def _group_tsv_words_by_line(words):
    rows = []
    for word in sorted(words, key=lambda item: (item["top"], item["left"])):
        if not rows or abs(rows[-1]["center_y"] - word["center_y"]) > max(10, word["height"] * 0.8):
            rows.append({"center_y": word["center_y"], "words": [word]})
        else:
            rows[-1]["words"].append(word)
            rows[-1]["center_y"] = sum(item["center_y"] for item in rows[-1]["words"]) / len(rows[-1]["words"])
    return rows


def _drpp_header_columns(header_words):
    lookup = {}
    for word in header_words:
        token = normalize_text(word["text"]).upper().strip(".,:")
        lookup.setdefault(token, word["left"])
    anchors = {
        "no": lookup.get("NO") or lookup.get("NO."),
        "bukti": lookup.get("TGL") or lookup.get("BUKTI"),
        "nama": lookup.get("NAMA") or lookup.get("PENERIMA") or lookup.get("KEPERLUAN"),
        "npwp": lookup.get("NPWP"),
        "akun": lookup.get("AKUN"),
        "jumlah": lookup.get("JUMLAH"),
    }
    if not all(anchors.get(key) is not None for key in ("bukti", "nama", "npwp", "akun", "jumlah")):
        return None
    right_edge = max(word["right"] for word in header_words) + 200
    no_left = min(word["left"] for word in header_words) - 20
    ordered = [no_left, anchors.get("bukti"), anchors["nama"], anchors["npwp"], anchors["akun"], anchors["jumlah"], right_edge]
    if any(right <= left for left, right in zip(ordered, ordered[1:])):
        return None
    table_width = right_edge - no_left
    min_width = max(18, table_width * 0.02)
    if any((right - left) < min_width for left, right in zip(ordered, ordered[1:])):
        return None
    return [
        ("no", no_left, anchors.get("bukti")),
        ("bukti", anchors["bukti"], anchors["nama"]),
        ("nama", anchors["nama"], anchors["npwp"]),
        ("npwp", anchors["npwp"], anchors["akun"]),
        ("akun", anchors["akun"], anchors["jumlah"]),
        ("jumlah", anchors["jumlah"], right_edge),
    ]


def _field_meta(words, normalized_value, page_number):
    if not words:
        return {
            "raw_text": "",
            "normalized_value": normalized_value,
            "page": page_number,
            "bounding_box": None,
            "method": "tsv_cell",
            "confidence": 0.0,
        }
    return {
        "raw_text": normalize_text(" ".join(word["text"] for word in words)),
        "normalized_value": normalized_value,
        "page": page_number,
        "bounding_box": [
            min(word["left"] for word in words),
            min(word["top"] for word in words),
            max(word["right"] for word in words),
            max(word["bottom"] for word in words),
        ],
        "method": "tsv_cell",
        "confidence": round(sum(word["confidence"] for word in words) / len(words), 2),
    }


def _drpp_item_review_reasons(item):
    reasons = []
    no_bukti = normalize_text(item.get("no_bukti", ""))
    akun = normalize_text(item.get("akun", ""))
    keperluan = normalize_text(item.get("keperluan", "")).upper()
    amount = item.get("jumlah") or Decimal("0")
    if not re.match(r"^[0-9A-Z]{3,6}/KW/[0-9A-Z./-]+$", no_bukti, re.IGNORECASE):
        reasons.append("no_bukti_invalid")
    if not re.match(r"^5\d{5}$", akun):
        reasons.append("akun_invalid")
    if amount <= 0:
        reasons.append("jumlah_invalid")
    if any(marker in keperluan for marker in ("JUMLAH", "TOTAL", "PAGU", "OUTPUT", "PEJABAT", "BENDAHARA", "LEMBAR", "TANGGAL CETAK")):
        reasons.append("teks_luar_cell")
    if item.get("needs_review"):
        reasons.extend(item.get("review_fields") or ["confidence_low"])
    return sorted(set(reasons))


def parse_drpp_items_from_tsv(raw_words, page_number=1, confidence_threshold=55):
    words = [word for word in (_to_tsv_word(raw_word) for raw_word in (raw_words or [])) if word]
    lines = _group_tsv_words_by_line(words)
    header_index = None
    columns = None
    for index, line in enumerate(lines):
        line_text = normalize_text(" ".join(word["text"] for word in sorted(line["words"], key=lambda item: item["left"]))).upper()
        if all(token in line_text for token in ("BUKTI", "PENERIMA", "NPWP", "AKUN", "JUMLAH")):
            columns = _drpp_header_columns(line["words"])
            if columns:
                header_index = index
                break
    if header_index is None or not columns:
        return []

    rows = []
    current = None
    stop_words = ("JUMLAH SPP", "JUMLAH LAMPIRAN", "PEJABAT", "BENDAHARA", "LEMBAR", "TANGGAL CETAK")
    for line in lines[header_index + 1:]:
        sorted_words = sorted(line["words"], key=lambda item: item["left"])
        line_text = normalize_text(" ".join(word["text"] for word in sorted_words))
        upper = line_text.upper()
        if not line_text:
            continue
        if any(stop in upper for stop in stop_words):
            if current:
                rows.append(current)
                current = None
            continue
        if "NPWP" in upper and "AKUN" in upper and "JUMLAH" in upper and any(token in upper for token in ("NO", "TGL", "NAMA", "BUKTI")):
            continue

        cells = {name: [] for name, _, _ in columns}
        for word in sorted_words:
            for name, left, right in columns:
                if left <= word["center_x"] < right:
                    cells[name].append(word)
                    break
        no_text = normalize_text(" ".join(word["text"] for word in cells["no"]))
        starts_row = bool(re.match(r"^\d{1,3}\b", no_text))
        has_table_content = any(cells[name] for name in ("bukti", "nama", "npwp", "akun", "jumlah"))
        if not has_table_content:
            continue
        if starts_row:
            if current:
                rows.append(current)
            current = {"cells": cells, "words": sorted_words}
        elif current:
            for name, cell_words in cells.items():
                current["cells"][name].extend(cell_words)
            current["words"].extend(sorted_words)
    if current:
        rows.append(current)

    items = []
    for row in rows:
        cells = row["cells"]
        raw_cells = {
            name: normalize_text(" ".join(word["text"] for word in sorted(cell_words, key=lambda item: (item["top"], item["left"]))))
            for name, cell_words in cells.items()
        }
        row_text = " ".join(raw_cells.values()).upper()
        if "JUMLAH" in row_text and not re.search(r"\d{3,6}/KW/", row_text):
            continue
        no_match = re.search(r"\d{1,3}", raw_cells["no"])
        bukti_text = raw_cells["bukti"]
        no_bukti_match = re.search(r"\b[0-9A-Z]{3,6}/KW/[0-9A-Z./-]+", bukti_text, re.IGNORECASE)
        tanggal_match = re.search(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b", bukti_text)
        akun_match = re.search(r"\b5\d{5}\b", raw_cells["akun"])
        amount_match = re.search(r"\d{1,3}(?:[.,]\d{3})+(?:,\d{2})?|\d+", raw_cells["jumlah"])
        low_conf_fields = [
            name for name, cell_words in cells.items()
            if cell_words and min(word["confidence"] for word in cell_words) < confidence_threshold
        ]
        missing_fields = [
            name for name, value in {
                "no_bukti": no_bukti_match.group(0) if no_bukti_match else "",
                "akun": akun_match.group(0) if akun_match else "",
                "jumlah": amount_match.group(0) if amount_match else "",
            }.items()
            if not value
        ]
        needs_review = bool(low_conf_fields or missing_fields)
        jumlah = parse_decimal(amount_match.group(0)) if amount_match else Decimal("0")
        normalized = {
            "no": int(no_match.group(0)) if no_match else None,
            "no_bukti": no_bukti_match.group(0) if no_bukti_match else "",
            "tanggal_bukti": tanggal_match.group(0) if tanggal_match else "",
            "penerima": raw_cells["nama"][:200],
            "npwp": re.sub(r"\D", "", raw_cells["npwp"])[:30],
            "akun": akun_match.group(0) if akun_match else "",
            "jumlah": jumlah,
            "keperluan": raw_cells["nama"][:500],
        }
        item = {
            "no_urut": normalized["no"] or len(items) + 1,
            "no_bukti": normalized["no_bukti"],
            "tanggal_bukti": normalized["tanggal_bukti"],
            "penerima": normalized["penerima"],
            "npwp": normalized["npwp"],
            "akun": normalized["akun"],
            "jumlah": normalized["jumlah"],
            "keperluan": normalized["keperluan"],
            "source_page": page_number,
            "source_row_id": f"page:{page_number}:y:{int(min(word['top'] for word in row['words']))}",
            "bounding_box": [
                min(word["left"] for word in row["words"]),
                min(word["top"] for word in row["words"]),
                max(word["right"] for word in row["words"]),
                max(word["bottom"] for word in row["words"]),
            ],
            "method": "tsv_cell",
            "confidence": round(sum(word["confidence"] for word in row["words"]) / len(row["words"]), 2),
            "raw_fields": raw_cells,
            "field_meta": {
                "no_bukti": _field_meta(cells["bukti"], normalized["no_bukti"], page_number),
                "tanggal_bukti": _field_meta(cells["bukti"], normalized["tanggal_bukti"], page_number),
                "penerima": _field_meta(cells["nama"], normalized["penerima"], page_number),
                "npwp": _field_meta(cells["npwp"], normalized["npwp"], page_number),
                "akun": _field_meta(cells["akun"], normalized["akun"], page_number),
                "jumlah": _field_meta(cells["jumlah"], str(normalized["jumlah"]), page_number),
                "keperluan": _field_meta(cells["nama"], normalized["keperluan"], page_number),
            },
            "needs_review": needs_review,
            "review_fields": sorted(set(low_conf_fields + missing_fields)),
            "status": "Perlu Review" if needs_review else "Terbaca",
        }
        review_reasons = _drpp_item_review_reasons(item)
        if review_reasons:
            item["needs_review"] = True
            item["review_fields"] = review_reasons
            item["status"] = "Perlu Review"
        items.append(item)
    return items


def _normalize_ocr_kw_number(value):
    parts = normalize_text(value).upper().strip("—–-|:;,. ").split("/")
    if len(parts) < 4 or parts[1] != "KW":
        return ""
    for index in (0, 2, 3):
        parts[index] = parts[index].replace("O", "0").replace("I", "1").replace("L", "1")
    normalized = "/".join(parts[:4])
    return normalized if re.fullmatch(r"\d{3,6}/KW/\d{5,9}/20\d{2}", normalized) else ""


def parse_drpp_items_from_tsv_rows(raw_words, page_number=1, confidence_threshold=45):
    """Recovery baris DRPP berbasis koordinat ketika judul kolom rusak OCR.

    Baris hanya dimulai bila satu kelompok-Y memuat nomor KW, akun/nominal, dan
    nominal terformat. Teks lanjutan pada kelompok-Y berikutnya digabung sebagai
    tanggal/deskripsi sampai anchor KW baris berikutnya. Ini tetap parser TSV
    terstruktur, bukan fallback dari flat text seluruh halaman.
    """
    words = [word for word in (_to_tsv_word(raw_word) for raw_word in (raw_words or [])) if word]
    lines = _group_tsv_words_by_line(words)
    kw_re = re.compile(r"[0-9OIL]{3,6}/KW/[0-9OIL]{5,9}/20[0-9OIL]{2}", re.IGNORECASE)
    amount_re = re.compile(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?")
    row_starts = []
    for index, line in enumerate(lines):
        line_words = sorted(line["words"], key=lambda item: item["left"])
        text = normalize_text(" ".join(word["text"] for word in line_words))
        kw_match = kw_re.search(text)
        amounts = list(amount_re.finditer(text))
        if kw_match and amounts:
            row_starts.append((index, line_words, text, kw_match, amounts[-1]))

    items = []
    for row_index, (line_index, line_words, text, kw_match, amount_match) in enumerate(row_starts):
        next_line_index = row_starts[row_index + 1][0] if row_index + 1 < len(row_starts) else len(lines)
        continuation_lines = lines[line_index + 1 : next_line_index]
        continuation_words = [
            word
            for line in continuation_lines
            for word in sorted(line["words"], key=lambda item: item["left"])
        ]
        continuation_text = normalize_text(" ".join(word["text"] for word in continuation_words))
        date_match = re.search(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b", continuation_text)
        description = continuation_text
        if date_match:
            description = normalize_text(continuation_text[date_match.end() :])
        description = clean_description(description)

        prefix = text[: kw_match.start()]
        no_match = re.search(r"(?:^|\s)(\d{1,3})(?:\s|$)", prefix)
        suffix_before_amount = text[kw_match.end() : amount_match.start()]
        account_matches = list(re.finditer(r"\b(5\d{5})\b", suffix_before_amount))
        account = account_matches[-1].group(1) if account_matches else ""
        npwp_matches = list(re.finditer(r"\b\d{14,16}\b", suffix_before_amount))
        npwp = npwp_matches[-1].group(0) if npwp_matches else ""
        receiver_end = npwp_matches[-1].start() if npwp_matches else (
            account_matches[-1].start() if account_matches else len(suffix_before_amount)
        )
        receiver = clean_description(suffix_before_amount[:receiver_end])[:200]
        all_words = line_words + continuation_words
        confidence_values = [word["confidence"] for word in all_words if word["confidence"] >= 0]
        confidence = round(sum(confidence_values) / len(confidence_values), 2) if confidence_values else 0.0
        no_bukti = _normalize_ocr_kw_number(kw_match.group(0))
        amount = parse_decimal(amount_match.group(0))
        review_fields = []
        if not no_bukti:
            review_fields.append("no_bukti_invalid")
        if not account:
            review_fields.append("akun_invalid")
        if amount <= 0:
            review_fields.append("jumlah_invalid")
        if not description:
            review_fields.append("deskripsi_missing")
        if confidence and confidence < confidence_threshold:
            review_fields.append("confidence_low")
        top = min((word["top"] for word in all_words), default=0)
        item = {
            "no_urut": int(no_match.group(1)) if no_match else len(items) + 1,
            "no_bukti": no_bukti,
            "tanggal_bukti": date_match.group(0) if date_match else "",
            "penerima": receiver,
            "npwp": npwp,
            "akun": account,
            "jumlah": amount,
            "keperluan": description[:500],
            "source_page": page_number,
            "source_row_id": f"page:{page_number}:y:{top}",
            "bounding_box": [
                min((word["left"] for word in all_words), default=0),
                top,
                max((word["right"] for word in all_words), default=0),
                max((word["bottom"] for word in all_words), default=0),
            ],
            "method": "tsv_row_anchor",
            "confidence": confidence,
            "raw_fields": {"row": text, "continuation": continuation_text},
            "needs_review": bool(review_fields),
            "review_fields": review_fields,
            "status": "Perlu Review" if review_fields else "Terbaca",
        }
        items.append(item)
    return items


def compact_pembebanan_from_coa(coa, akun=""):
    text = normalize_text(coa).upper().replace(",", ".")
    dotted_text = re.sub(r"\s+", ".", text)
    akun_match = re.search(rf"(?:^|[.\s])({re.escape(akun)})(?:[.\s]|$)", dotted_text) if akun else re.search(r"(?:^|[.\s])(5\d{5})(?:[.\s]|$)", dotted_text)
    output_match = re.search(r"(?:^|[.\s])(\d{4})\.?([A-Z]{3})(?:[.\s]|$)", dotted_text)
    component_candidates = re.findall(r"(?:^|[.\s])(\d{3})[.\s]+(\d{3})(?:[.\s]|$)", dotted_text)
    component_match = next(
        ((left, right) for left, right in component_candidates if left == "994" and right != "000"),
        next(((left, right) for left, right in component_candidates if left != "000" and right != "000"), None),
    )
    if not (akun_match and output_match and component_match):
        return ""
    account = akun or akun_match.group(1)
    return f"{output_match.group(1)}.{output_match.group(2)}.{component_match[0]}.{component_match[1]}.{account}"


def normalize_coa_text(value):
    text = normalize_text(value).upper().replace(",", ".")
    text = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
    text = re.sub(r"\s*\.\s*", ".", text)
    return text


def pembebanan_from_full_coa(value, akun=""):
    text = normalize_coa_text(value)
    akun_match = re.search(r"\d{4,6}\.\d{3}\.(5\d{5})\.", text) or re.search(r"\b(5\d{5})\b", text)
    account = akun or (akun_match.group(1) if akun_match else "")
    program_match = re.search(r"\.(\d{4})([A-Z]{3})\.", text)
    item_matches = re.findall(r"\.(\d{3})\.(\d{3})\.([0-9A-Z]{2})\.(\d{3,6})\b", text)
    item_match = next(
        ((left, right, code, number) for left, right, code, number in reversed(item_matches) if left != "000" and right != "000"),
        None,
    )
    if not (account and program_match and item_match):
        return ""
    return f"{program_match.group(1)}.{program_match.group(2)}.{item_match[0]}.{item_match[1]}.{account}"


def parse_validated_lampiran_coa_pages(page_details, expected_total, default_description=""):
    """Recover rows from a readable SPM COA attachment with strict validation.

    This is intentionally independent from page number, paper colour, filename,
    and document-specific amounts. A row is accepted only when the OCR text
    contains the account, output/program, item code, a valid compact charging
    code, and the resulting row total equals the SPM gross amount.
    """
    expected = parse_decimal(expected_total)
    if expected <= 0:
        return []

    output = []
    amount_pattern = re.compile(r"\d{1,3}(?:[.,]\d{3}){2,}(?:[.,]\d{2})?")
    item_pattern = re.compile(
        r"(?<![A-Z0-9])(\d{3})[.\s]+(\d{3})[.\s]+([0-9A-Z]{2})[.\s]+(\d{3,6})\b",
        re.IGNORECASE,
    )
    account_pattern = re.compile(r"\b\d{4,6}[.\s]+\d{3}[.\s]+(5\d{5})[.\s]", re.IGNORECASE)
    program_pattern = re.compile(r"(?:^|[.\s])(\d{4})[.\s]*([A-Z]{3})(?:[.\s]|$)", re.IGNORECASE)

    for page_index, page in enumerate(page_details or [], start=1):
        page_output = []
        raw_text = normalize_text(page.get("text") or page.get("extracted_text") or "")
        # Keep whitespace between two numeric groups. The general COA
        # normalizer intentionally removes it, but on flattened OCR that can
        # merge the end of one segment with the next item code.
        normalized = raw_text.upper().replace(",", ".")
        normalized = re.sub(r"\s*\.\s*", ".", normalized)
        if not re.search(r"\b(?:LAMPIRAN|RO[.\s]*KOMP|KODE[.\s]+COA)\b", normalized):
            continue

        item_matches = list(item_pattern.finditer(normalized))
        for item_index, item_match in enumerate(item_matches):
            prefix = normalized[: item_match.start()]
            account_matches = list(account_pattern.finditer(prefix))
            program_matches = list(program_pattern.finditer(prefix))
            if not (account_matches and program_matches):
                continue

            account = account_matches[-1].group(1)
            program = program_matches[-1]
            item_parts = tuple(part.upper() for part in item_match.groups())
            if item_parts[0] == "000" or item_parts[1] == "000":
                continue

            next_item_start = (
                item_matches[item_index + 1].start()
                if item_index + 1 < len(item_matches)
                else min(len(normalized), item_match.end() + 600)
            )
            after_item = normalized[item_match.end() : next_item_start]
            amount_matches = list(amount_pattern.finditer(after_item))
            amount_candidates = [
                (match, parse_decimal(match.group(0)))
                for match in amount_matches
                if parse_decimal(match.group(0)) > 0
            ]

            # A one-row attachment commonly prints its gross total only once.
            # Prefer the value confirmed by the SPM header; otherwise use the
            # first grouped monetary value following this item.
            selected_amount_match = next(
                ((match, amount) for match, amount in amount_candidates if amount == expected),
                amount_candidates[0] if amount_candidates else None,
            )
            if selected_amount_match is None and len(item_matches) == 1:
                page_amounts = [
                    (match, parse_decimal(match.group(0)))
                    for match in amount_pattern.finditer(normalized)
                ]
                selected_amount_match = next(
                    ((match, amount) for match, amount in page_amounts if amount == expected),
                    None,
                )
            if selected_amount_match is None:
                continue

            amount_match, amount = selected_amount_match
            pembebanan = (
                f"{program.group(1)}.{program.group(2).upper()}."
                f"{item_parts[0]}.{item_parts[1]}.{account}"
            )
            if not is_valid_pembebanan(pembebanan, amount):
                continue

            description = ""
            if amount_match in amount_matches:
                description_text = after_item[: amount_match.start()]
                description_text = re.sub(r"^[\s|:;,.\-–]+", "", description_text)
                description = clean_description(description_text)
            if not description or not re.search(r"[A-Z]{3}", description.upper()):
                description = clean_description(default_description) or "Perlu Review Uraian"
            else:
                description = prefer_richer_description(description, default_description)

            item_code = ".".join(item_parts)
            page_output.append({
                "akun": account,
                "jumlah": amount,
                "bruto": amount,
                "netto": amount,
                "no_bukti": item_parts[-1],
                "keperluan": description,
                "pembebanan": pembebanan,
                "source_page": page.get("page_number") or page.get("page") or page_index,
                "source_types": list(page.get("page_types") or []),
                "source_row_id": item_code,
                "ocr_rotation": int(page.get("rotation") or 0),
                "source_priority": "LAMPIRAN_COA_VALIDATED",
                "needs_review": description == "Perlu Review Uraian",
                "review_note": "" if description != "Perlu Review Uraian" else "Uraian lampiran perlu review.",
            })

        page_rows = dedupe_detail_items(page_output)
        page_total = sum((parse_decimal(item.get("jumlah")) for item in page_rows), Decimal("0"))
        if page_rows and page_total == expected:
            return page_rows
        output.extend(page_rows)

    rows = dedupe_detail_items(output)
    total = sum((parse_decimal(item.get("jumlah")) for item in rows), Decimal("0"))
    return rows if rows and total == expected else []


def is_valid_pembebanan(value, jumlah=None):
    text = normalize_text(value).upper()
    if not re.match(r"^\d{4}\.[A-Z]{3}\.\d{3}\.\d{3}\.5\d{5}$", text):
        return False
    if jumlah:
        groups = text.split(".")
        if len(groups) >= 4:
            pair_amount = parse_decimal(f"{groups[2]}.{groups[3]}")
            if pair_amount == parse_decimal(jumlah):
                return False
    return True


def normalize_ocr_amount_text(value):
    text = normalize_text(value)
    text = re.sub(r"([.,]\d{3}),00\b", r"\1,00", text)
    text = re.sub(r"(\d)\s+([.,]\d{3})", r"\1\2", text)
    return text


def detail_line_rows_from_tsv(words):
    rows = []
    for word in sorted(words, key=lambda item: (item["top"], item["left"])):
        if not rows or abs(rows[-1]["top"] - word["top"]) > 22:
            rows.append({"top": word["top"], "words": [word]})
        else:
            rows[-1]["words"].append(word)
    output = []
    for row in rows:
        sorted_words = sorted(row["words"], key=lambda item: item["left"])
        text = normalize_ocr_amount_text(" ".join(item["text"] for item in sorted_words))
        if text:
            output.append({"top": row["top"], "text": text, "words": sorted_words})
    return output


def score_detail_tsv(text, confidence):
    upper = normalize_text(text).upper()
    coa_count = len(re.findall(r"\b\d{4,6}[\s.]+\d{3}[\s.]+5\d\s*\d{4}", upper))
    item_count = len(re.findall(r"\b\d{3}\.\d{3}\.[0-9A-Z]{2}\.\d{3,6}\b", upper))
    item_desc_count = len(re.findall(r"\b\d{3}\.\d{3}\.[0-9A-Z]{2}\.\d{3,6}\s*[-–]\s*[A-Z]", upper))
    amount_count = len(re.findall(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?", upper))
    keyword_score = 20 if "DETAIL PENGELUARAN" in upper else 0
    return keyword_score + coa_count * 20 + item_count * 12 + item_desc_count * 25 + amount_count * 5 + (confidence / 10)


def ordered_table_rotations(selected_rotation=0):
    """Coba orientasi pilihan engine lebih dulu, lalu semua orientasi lain."""
    output = []
    for rotation in (int(selected_rotation or 0), 0, 90, 180, 270):
        if rotation not in output:
            output.append(rotation)
    return tuple(output)


def tesseract_table_language_attempts(preferred=None):
    """Return a deterministic language fallback order for table OCR.

    A Windows Tesseract installation often only ships ``eng`` and ``osd``.
    The regular page OCR already tolerates that installation, so the table
    parser must not fail merely because ``ind.traineddata`` is unavailable.
    """
    configured = os.getenv("OCR_TESSERACT_LANGS", "")
    requested = list(preferred or ())
    if configured:
        requested = [item.strip() for item in re.split(r"[,;]", configured) if item.strip()] + requested
    requested.extend(("ind+eng", "eng", ""))
    output = []
    for language in requested:
        if language not in output:
            output.append(language)
    return tuple(output)


def tesseract_image_to_data_with_fallback(pytesseract, image, *, config, languages=None):
    errors = []
    for language in tesseract_table_language_attempts(languages):
        kwargs = {"config": config, "output_type": pytesseract.Output.DICT}
        if language:
            kwargs["lang"] = language
        try:
            return pytesseract.image_to_data(image, **kwargs), language or "default", errors
        except Exception as exc:
            errors.append({"language": language or "default", "error": str(exc)[:300]})
    return None, "", errors


def tesseract_image_to_string_with_fallback(pytesseract, image, *, config, languages=None):
    errors = []
    for language in tesseract_table_language_attempts(languages):
        kwargs = {"config": config}
        if language:
            kwargs["lang"] = language
        try:
            return pytesseract.image_to_string(image, **kwargs), language or "default", errors
        except Exception as exc:
            errors.append({"language": language or "default", "error": str(exc)[:300]})
    return "", "", errors


def ocr_page_table_variants(file_path, page_number, rotations):
    try:
        import io
        import fitz
        import pytesseract
        from PIL import Image, ImageOps, ImageFilter
    except Exception:
        return []
    if not configure_tesseract(pytesseract):
        return []

    try:
        doc = fitz.open(file_path)
        page = doc[page_number - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), alpha=False)
        doc.close()
        base = Image.open(io.BytesIO(pix.tobytes("png")))
        base = ImageOps.autocontrast(ImageOps.exif_transpose(base).convert("L")).filter(ImageFilter.SHARPEN)
    except Exception:
        return []

    variants = []
    for rotation in rotations:
        for psm in (4, 11, 12, 6):
            image = base.rotate(rotation, expand=True) if rotation else base
            data, language, language_errors = tesseract_image_to_data_with_fallback(
                pytesseract,
                image,
                config=f"--psm {psm}",
            )
            if data is None:
                if language_errors:
                    ocr_log(
                        f"table OCR page={page_number} rotation={rotation} psm={psm} gagal untuk semua bahasa: "
                        f"{language_errors[-1]['error']}"
                    )
                continue
            words = []
            confs = []
            for index, raw_word in enumerate(data.get("text", [])):
                word = normalize_text(raw_word)
                if not word:
                    continue
                try:
                    conf = float(data.get("conf", [])[index])
                except (TypeError, ValueError, IndexError):
                    conf = -1.0
                if conf >= 0:
                    confs.append(conf)
                words.append({
                    "text": word,
                    "left": int(data["left"][index]),
                    "top": int(data["top"][index]),
                    "width": int(data["width"][index]),
                    "height": int(data["height"][index]),
                    "confidence": conf,
                })
            text = " ".join(item["text"] for item in words)
            confidence = round(sum(confs) / len(confs), 2) if confs else 0.0
            variant = {
                "page": page_number,
                "psm": psm,
                "rotation": rotation,
                "language": language,
                "language_fallback_used": bool(language_errors),
                "language_errors": language_errors,
                "confidence": confidence,
                "score": score_detail_tsv(text, confidence),
                "text": text,
                "lines": detail_line_rows_from_tsv(words),
            }
            variants.append(variant)
    return variants


def table_variant_from_page_tsv(page, page_types):
    words = page.get("tsv_words") or page.get("words") or page.get("ocr_words") or []
    normalized_words = [word for word in (_to_tsv_word(raw_word) for raw_word in words) if word]
    if not normalized_words:
        return None
    confidence_values = [word["confidence"] for word in normalized_words if word["confidence"] >= 0]
    confidence = round(sum(confidence_values) / len(confidence_values), 2) if confidence_values else 0.0
    text = " ".join(word["text"] for word in sorted(normalized_words, key=lambda item: (item["top"], item["left"])))
    return {
        "page": page.get("page_number") or page.get("page"),
        "psm": "existing_tsv",
        "rotation": int(page.get("rotation") or page.get("selected_rotation") or 0),
        "confidence": confidence,
        "score": score_detail_tsv(text, confidence),
        "text": text,
        "lines": detail_line_rows_from_tsv(normalized_words),
        "source_types": list(page_types),
        "source": "existing_tsv",
        "tsv_word_count": len(normalized_words),
    }


def full_coa_key(value):
    text = normalize_coa_text(value)
    matches = re.findall(r"\.(\d{3})\.(\d{3})\.([0-9A-Z]{2})\.(\d{3,6})\b", text)
    match = next(((left, right, code, number) for left, right, code, number in reversed(matches) if left != "000" and right != "000"), None)
    return ".".join(match) if match else ""


def parse_full_coa_rows(lines, source_page, source_types):
    rows = []
    for row in lines:
        text = normalize_coa_text(row["text"])
        coa_match = re.search(r"(\d{4,6})\.\d{3}\.(5\d{5})\.[^\s|]+", text)
        if not coa_match:
            continue
        coa = coa_match.group(0)
        akun = coa_match.group(2)
        item_code = full_coa_key(coa)
        pembebanan = pembebanan_from_full_coa(coa, akun)
        amount_matches = re.findall(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?", row["text"])
        amount = parse_decimal(amount_matches[-1]) if amount_matches else Decimal("0")
        if item_code and pembebanan:
            rows.append({
                "item_code": item_code,
                "akun": akun,
                "pembebanan": pembebanan,
                "jumlah": amount,
                "source_page": source_page,
                "source_types": source_types,
                "rotation": row.get("rotation", 0),
            })
    return rows


def parse_lampiran_detail_rows(lines, coa_by_item, source_page, source_types):
    rows = []
    current = None
    item_re = re.compile(r"\b(\d{3}\.\d{3}\.[0-9A-Z]{2}\.\d{3,6})\s*[-–]?\s*(.*)", re.IGNORECASE)
    for row in lines:
        text = normalize_ocr_amount_text(row["text"])
        match = item_re.search(text)
        if match:
            if current:
                rows.append(current)
            current = {
                "item_code": match.group(1).upper(),
                "buffer": match.group(2),
                "source_page": source_page,
                "source_types": source_types,
                "rotation": row.get("rotation", 0),
            }
            continue
        if current:
            current["buffer"] = f"{current['buffer']} {text}".strip()
    if current:
        rows.append(current)
    output = []
    for row in rows:
        coa = coa_by_item.get(row["item_code"])
        if not coa:
            continue
        amounts = re.findall(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?", row["buffer"])
        amounts = [parse_decimal(amount) for amount in amounts]
        amounts = [amount for amount in amounts if amount > 0]
        if not amounts:
            continue
        amount = amounts[-1]
        desc = re.split(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?", row["buffer"], maxsplit=1)[0]
        desc = clean_description(desc)
        output.append({
            "akun": coa["akun"],
            "jumlah": amount,
            "bruto": amount,
            "netto": amount,
            "no_bukti": row["item_code"].split(".")[-1],
            "keperluan": desc,
            "pembebanan": coa["pembebanan"],
            "source_page": row["source_page"],
            "source_types": row["source_types"],
            "source_row_id": row["item_code"],
            "ocr_rotation": row["rotation"],
            "source_priority": "DETAIL_SPP_SPM_SP2D",
        })
    return output


def best_amount_from_text(value, allow_bare=True):
    amounts = re.findall(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?", normalize_ocr_amount_text(value))
    parsed = [parse_decimal(amount) for amount in amounts]
    if allow_bare and not amounts:
        parsed.extend(Decimal(amount) for amount in re.findall(r"\b\d{1,12}\b", normalize_text(value)))
    parsed = [amount for amount in parsed if amount > 0]
    if not parsed:
        return Decimal("0")
    counts = {}
    for amount in parsed:
        counts[amount] = counts.get(amount, 0) + 1
    repeated = [amount for amount, count in counts.items() if count > 1]
    return repeated[0] if repeated else parsed[-1]


def extract_lampiran_descriptions(best_by_page):
    desc_by_item = {}
    item_desc_re = re.compile(r"\b(\d{3}\.\d{3}\.[0-9A-Z]{2}\.\d{3,6})\s*[-–]\s*([^|]{3,180})", re.IGNORECASE)

    def clean_lampiran_desc(value):
        desc = clean_description(value)
        desc = re.split(
            r"(?:\b(?:Padang|a\.n\.|Pejabat|Dokumen ini|NIP|Halaman|Tanggal|Detail Coa|BADAN PUSAT|LAMPIRAN|Nomor SPP|Nomor SPM)\b|\[\s*Kode\b)",
            desc,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        desc = re.split(r"\s+019(?:937)?\b", desc, maxsplit=1)[0]
        desc = re.split(r"\s+(?:cieer|ieer|iaer|oiaer|iser|veer|aos)[a-z0-9]{6,}", desc, maxsplit=1, flags=re.IGNORECASE)[0]
        desc = re.split(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?", desc, maxsplit=1)[0]
        desc = re.split(r"\b(?:\d{4,6}\.\d{3}\.5\d{5}|\d{3}\.\d{3}\.[0-9A-Z]{2}\.\d{3,6}\s*-)", desc, maxsplit=1, flags=re.IGNORECASE)[0]
        desc = re.sub(r"[_|]+", " ", desc)
        desc = re.sub(r"\b(?:uma|awa|fume|pumay|mh|al|Ea|pm|fm|iv|re)\b", " ", desc, flags=re.IGNORECASE)
        desc = re.sub(r"\bPemelihara\s+an\b", "Pemeliharaan", desc, flags=re.IGNORECASE)
        desc = desc.replace("\u2018", "'").replace("\u2019", "'").replace("â€™", "'")
        desc = re.sub(r"\s+(?::\s*)?'\s*S$", "", desc, flags=re.IGNORECASE)
        desc = re.sub(r"\s+\d+\s*'\s*x\s*\d+\s*Ml$", "", desc, flags=re.IGNORECASE)
        desc = re.sub(r"\s+um$", "", desc, flags=re.IGNORECASE)
        desc = re.sub(r"\s+(?:b\]|J\s+S|y|aE\s*,?\s*S\)|a|eee!?)+$", "", desc, flags=re.IGNORECASE)
        desc = re.sub(r"\s+(?:SS|IE|LE|L!|:\s*[’']\s*S|[’']\s*x\s*8\s*LE|,\s*S\)\s*aE\s*[’']\s*S\s*el)$", "", desc, flags=re.IGNORECASE)
        desc = re.sub(r"(?:\s+(?:a|aE|eee|tlh|Lt|wma|J|S|b|L!|[’'`\"()\[\]{}|\\/.,;:!?)=-]+|\d+))+$", "", desc)
        desc = re.sub(r"\b(Belanja\s+Peralatan\s+dan\s+mesin)\s+(Ekstrakomtabel\b)", r"\1 - \2", desc, flags=re.IGNORECASE)
        desc = re.sub(r"\s+", " ", desc).strip(" .,-;:|_")
        return desc if len(desc) >= 5 and re.search(r"[A-Za-z]", desc) else ""

    def description_quality(desc):
        words = re.findall(r"[A-Za-zÀ-ÿ/]+", desc)
        gibberish = re.findall(r"[A-Za-z]{16,}", desc)
        bad_markers = re.findall(r"\b(?:BADAN|LAMPIRAN|Nomor|\d{4,6}|Dokumen|Pejabat)\b", desc, re.IGNORECASE)
        return len(words) * 4 - len(gibberish) * 20 - len(bad_markers) * 25 - max(0, len(desc) - 90)

    def keep_description(item_code, desc):
        desc = clean_lampiran_desc(desc)
        for suffix in (" aE , S)", " eee!", " b]", " J S", " y", " a"):
            if desc.endswith(suffix):
                desc = desc[: -len(suffix)].strip()
        if not desc or re.match(r"^[|_\W\d\s]+$", desc):
            return
        old = desc_by_item.get(item_code)
        if not old or (description_quality(desc), -len(desc)) > (description_quality(old), -len(old)):
            desc_by_item[item_code] = desc

    def scan_text_blob(text):
        normalized = normalize_ocr_amount_text(text)
        pattern = re.compile(
            r"\b(\d{3}\.\d{3}\.[0-9A-Z]{2}\.\d{3,6})\s*[-â€“]\s*(.*?)"
            r"(?=\s+\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?|\s+\d{4,6}\.\d{3}\.5\d{5}|\s+\d{3}\.\d{3}\.[0-9A-Z]{2}\.\d{3,6}\s*[-â€“]|$)",
            re.IGNORECASE,
        )
        for match in pattern.finditer(normalized):
            keep_description(match.group(1).upper(), match.group(2))
    item_re = re.compile(r"\b(\d{3}\.\d{3}\.[0-9A-Z]{2}\.\d{3,6})\s*[-–]\s*(.*)", re.IGNORECASE)
    for page in best_by_page.values():
        if not set(page.get("source_types") or []).intersection({"SPM", "SPP", "LAMPIRAN_COA"}):
            continue
        scan_text_blob(page.get("text") or "")
        for variant in page.get("variants") or []:
            scan_text_blob(variant.get("text") or "")
        current = None
        lines = list(page.get("lines") or [])
        for variant in page.get("variants") or []:
            lines.extend(variant.get("lines") or [])
        for line in lines:
            text = normalize_ocr_amount_text(line["text"])
            match = item_re.search(text)
            if match:
                if current:
                    item_code, buffer = current
                    keep_description(item_code, buffer)
                current = (match.group(1).upper(), match.group(2))
            elif current:
                current = (current[0], f"{current[1]} {text}")
        if current:
            item_code, buffer = current
            keep_description(item_code, buffer)
    return desc_by_item


def table_line_groups(image, axis="horizontal", min_ratio=0.2, threshold=120):
    """Detect dark table ruling lines with plain PIL; OpenCV is not required."""
    width, height = image.size
    pixels = image.load()
    limit = (width if axis == "horizontal" else height) * min_ratio
    hits = []
    outer = height if axis == "horizontal" else width
    inner = width if axis == "horizontal" else height
    for pos in range(outer):
        count = 0
        for offset in range(inner):
            x, y = (offset, pos) if axis == "horizontal" else (pos, offset)
            if pixels[x, y] < threshold:
                count += 1
        if count >= limit:
            hits.append((pos, count))

    groups = []
    for pos, count in hits:
        if not groups or pos > groups[-1][-1][0] + 1:
            groups.append([])
        groups[-1].append((pos, count))
    return [
        {
            "start": group[0][0],
            "end": group[-1][0],
            "center": (group[0][0] + group[-1][0]) // 2,
            "strength": max(count for _, count in group),
        }
        for group in groups
    ]


def ocr_cell_text(image, pytesseract, *, numeric=False):
    try:
        from PIL import ImageOps
    except Exception:
        return ""
    if image.width <= 2 or image.height <= 2:
        return ""
    crop = ImageOps.autocontrast(image).resize((image.width * 2, image.height * 2))
    config = "--psm 7"
    languages = ("ind+eng", "eng", "")
    if numeric:
        config += " -c tessedit_char_whitelist=0123456789.,"
        languages = ("eng", "")
    text, _language, _errors = tesseract_image_to_string_with_fallback(
        pytesseract,
        crop,
        config=config,
        languages=languages,
    )
    return normalize_text(text)


def table_row_bands(horizontal_groups):
    if not horizontal_groups:
        return []
    groups = sorted(horizontal_groups, key=lambda item: item["start"])
    gaps = [groups[index + 1]["start"] - group["start"] for index, group in enumerate(groups[:-1])]
    row_height = int(median(gaps)) if gaps else 56
    bands = []
    for index, group in enumerate(groups):
        top = group["end"] + 1
        bottom = groups[index + 1]["start"] - 1 if index + 1 < len(groups) else group["start"] + row_height - 1
        if bottom - top >= 30:
            bands.append((top, bottom))
    return bands


def table_column_bands(vertical_groups):
    groups = sorted(vertical_groups, key=lambda item: item["start"])
    return [
        (groups[index]["end"] + 1, groups[index + 1]["start"] - 1)
        for index in range(len(groups) - 1)
        if groups[index + 1]["start"] - groups[index]["end"] > 20
    ]


def parse_detail_sp2d_rows_by_grid(image, pytesseract, page_number, rotation, source_types, desc_by_item):
    horizontal = table_line_groups(image, axis="horizontal", min_ratio=0.25)
    # Tabel DJPb sering hanya memakai sebagian kecil tinggi halaman landscape.
    # Rasio tinggi yang lebih rendah tetap aman karena baris baru diterima jika
    # sel COA, akun, item, pembebanan, dan nominal semuanya valid.
    vertical = table_line_groups(image, axis="vertical", min_ratio=0.04)
    row_bands = table_row_bands(horizontal)
    column_bands = table_column_bands(vertical)
    if len(row_bands) < 2 or len(column_bands) < 8:
        return [], {"grid_rows": len(row_bands), "grid_columns": len(column_bands)}

    widths = [right - left for left, right in column_bands]
    coa_col = max(range(len(widths)), key=lambda index: widths[index])
    rows = []
    for row_index, (top, bottom) in enumerate(row_bands, start=1):
        row_cells = []
        for left, right in column_bands:
            pad_x, pad_y = 2, 2
            row_cells.append(image.crop((left + pad_x, top + pad_y, right - pad_x, bottom - pad_y)))

        coa_text = normalize_coa_text(ocr_cell_text(row_cells[coa_col], pytesseract))
        coa_text = re.sub(r"(?<=\d)\s+(?=\d)", "", coa_text)
        coa_text = coa_text.replace("{", ".").replace("}", ".")
        coa_text = re.sub(r"\.+", ".", coa_text)
        akun_match = re.search(r"\d{4,6}\.\d{3}\.(5\d{5})\.", coa_text)
        item_match = re.search(r"\.(\d{3})\.(\d{3})\.([0-9A-Z]{2})\.(\d{3,6})\b", coa_text)
        if not (akun_match and item_match):
            continue

        amounts = []
        for cell in row_cells[coa_col + 1:]:
            text = ocr_cell_text(cell, pytesseract, numeric=True)
            amount = best_amount_from_text(text)
            if amount:
                amounts.append(amount)
        amount_counts = {candidate: amounts.count(candidate) for candidate in amounts}
        repeated_amounts = [candidate for candidate, count in amount_counts.items() if count > 1]
        amount = repeated_amounts[0] if repeated_amounts else (max(amounts) if amounts else Decimal("0"))
        akun = akun_match.group(1)
        item_code = ".".join(part.upper() for part in item_match.groups())
        pembebanan = pembebanan_from_full_coa(f"{coa_text}.{item_code}", akun)
        if not (amount and pembebanan and is_valid_pembebanan(pembebanan, amount)):
            continue

        # Jumlah garis tepi kiri berbeda antar hasil scan/driver, sehingga
        # indeks kolom dapat bergeser satu. Kenali metadata dari isi seluruh
        # sel sebelum COA alih-alih mengandalkan posisi kolom tetap.
        identity_text = " | ".join(
            ocr_cell_text(cell, pytesseract)
            for cell in row_cells[:coa_col]
        )
        satker_match = re.search(r"\b(\d{6})\b", identity_text)
        no_spm_match = re.search(r"\b([0-9]{3,6}[A-Z]?/\d{4,6}/20\d{2})\b", identity_text.upper())
        no_sp2d_match = _RE_SP2D_BARE.search(identity_text)
        tanggal_sp2d_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", identity_text)
        description = desc_by_item.get(item_code, "")
        rows.append({
            "akun": akun,
            "jumlah": amount,
            "bruto": amount,
            "netto": amount,
            "no_bukti": item_code.split(".")[-1],
            "keperluan": description or "Perlu Review Uraian",
            "pembebanan": pembebanan,
            "source_page": page_number,
            "source_types": source_types,
            "source_row_id": item_code,
            "ocr_rotation": rotation,
            "source_priority": "DETAIL_SPP_SPM_SP2D",
            "needs_review": not bool(description),
            "review_note": "" if description else "Uraian Lampiran COA belum cocok pasti.",
            "satker": satker_match.group(1) if satker_match else "",
            "nomor_spm_detail": no_spm_match.group(1) if no_spm_match else "",
            "nomor_sp2d": no_sp2d_match.group(1) if no_sp2d_match else "",
            "tanggal_sp2d": parse_date(tanggal_sp2d_match.group(1)) if tanggal_sp2d_match else None,
            "grid_row": row_index,
        })
    return dedupe_detail_items(rows), {"grid_rows": len(row_bands), "grid_columns": len(column_bands)}


def parse_detail_sp2d_rows_by_crop(file_path, page_number, rotation, source_types, desc_by_item):
    try:
        import io
        import fitz
        import pytesseract
        from PIL import Image, ImageOps, ImageFilter
    except Exception:
        return []
    if not configure_tesseract(pytesseract):
        return []

    try:
        doc = fitz.open(file_path)
        page = doc[page_number - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), alpha=False)
        doc.close()
        image = Image.open(io.BytesIO(pix.tobytes("png")))
        image = ImageOps.autocontrast(ImageOps.exif_transpose(image).convert("L")).filter(ImageFilter.SHARPEN)
        image = image.rotate(rotation, expand=True) if rotation else image
        crop_image = image
        grid_rows, _grid_info = parse_detail_sp2d_rows_by_grid(
            image,
            pytesseract,
            page_number,
            rotation,
            source_types,
            desc_by_item,
        )
        if grid_rows:
            return grid_rows
        data, _language, _language_errors = tesseract_image_to_data_with_fallback(
            pytesseract,
            image,
            config="--psm 4",
        )
        if data is None:
            return []
    except Exception:
        return []

    words = []
    for index, raw_word in enumerate(data.get("text", [])):
        word = normalize_text(raw_word)
        if not word:
            continue
        words.append({
            "text": word,
            "left": int(data["left"][index]),
            "top": int(data["top"][index]),
            "width": int(data["width"][index]),
            "height": int(data["height"][index]),
        })

    candidate_rows = []
    for row in detail_line_rows_from_tsv(words):
        row_text = normalize_coa_text(row["text"])
        row_text = re.sub(r"(5\d{4})\s+(\d)", r"\1\2", row_text)
        if not re.search(r"\d{4,6}\.\d{3}\.5\d{5}\.", row_text):
            continue
        y = row["top"]
        crop = crop_image.crop((
            int(crop_image.width * 0.60),
            max(0, y - 30),
            crop_image.width - 10,
            min(crop_image.height, y + 55),
        ))
        crop = ImageOps.autocontrast(crop).resize((crop.width * 2, crop.height * 2))
        crop_text = ""
        for variant in (crop, crop.point(lambda pixel: 255 if pixel > 170 else 0)):
            try:
                crop_text = pytesseract.image_to_string(
                    variant,
                    lang="eng",
                    config="--psm 7 -c tessedit_char_whitelist=0123456789.,|",
                )
            except Exception:
                crop_text = ""
            if best_amount_from_text(crop_text):
                break
        item_match = re.search(r"\b(\d{3})[\s.](\d{3})[\s.]([0-9A-Z]{2})[\s.](\d{3,6})\b", f"{row_text} {crop_text}", re.IGNORECASE)
        akun_match = re.search(r"\d{4,6}\.\d{3}\.(5\d{5})\.", row_text)
        if not (item_match and akun_match):
            continue
        item_code = ".".join(part.upper() for part in item_match.groups())
        akun = akun_match.group(1)
        pembebanan = pembebanan_from_full_coa(f"{row_text}.{item_code}", akun)
        amount = best_amount_from_text(crop_text)
        if not (pembebanan and amount):
            continue
        description = desc_by_item.get(item_code, "")
        candidate_rows.append({
            "akun": akun,
            "jumlah": amount,
            "bruto": amount,
            "netto": amount,
            "no_bukti": item_code.split(".")[-1],
            "keperluan": description or "Perlu Review Uraian",
            "pembebanan": pembebanan,
            "source_page": page_number,
            "source_types": source_types,
            "source_row_id": item_code,
            "ocr_rotation": rotation,
            "source_priority": "DETAIL_SPP_SPM_SP2D",
            "needs_review": not bool(description),
            "review_note": "" if description else "Uraian Lampiran COA belum cocok pasti.",
        })
    return dedupe_detail_items(candidate_rows)


def render_ocr_page_image(file_path, page_number, rotation=0):
    try:
        import io
        import fitz
        from PIL import Image, ImageOps, ImageFilter
    except Exception:
        return None

    try:
        doc = fitz.open(file_path)
        page = doc[page_number - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), alpha=False)
        doc.close()
        image = Image.open(io.BytesIO(pix.tobytes("png")))
        image = ImageOps.autocontrast(ImageOps.exif_transpose(image).convert("L")).filter(ImageFilter.SHARPEN)
        return image.rotate(rotation, expand=True) if rotation else image
    except Exception:
        return None


def ocr_amount_word_crop(image, word):
    if image is None:
        return Decimal("0")
    try:
        import pytesseract
        from PIL import ImageOps
    except Exception:
        return Decimal("0")
    if not configure_tesseract(pytesseract):
        return Decimal("0")

    left = max(0, int(word.get("left", 0)) - 12)
    top = max(0, int(word.get("top", 0)) - 10)
    right = min(image.width, int(word.get("left", 0)) + int(word.get("width", 0)) + 14)
    bottom = min(image.height, int(word.get("top", 0)) + int(word.get("height", 0)) + 10)
    if right <= left or bottom <= top:
        return Decimal("0")

    crop = ImageOps.autocontrast(image.crop((left, top, right, bottom))).resize(((right - left) * 3, (bottom - top) * 3))
    for psm in (7, 6, 8):
        try:
            text = pytesseract.image_to_string(
                crop,
                lang="eng",
                config=f"--psm {psm} -c tessedit_char_whitelist=0123456789.,",
            )
        except Exception:
            text = ""
        amount = best_amount_from_text(text)
        if amount:
            return amount
    return Decimal("0")


def parse_detail_sp2d_rows_from_tsv_lines(file_path, page_number, rotation, lines, source_types, desc_by_item):
    image = None
    rows = []
    all_words = [word for row in lines for word in (row.get("words") or [])]
    page_width = max(
        (int(word.get("left", 0)) + int(word.get("width", 0)) for word in all_words),
        default=1,
    )
    amount_x_min = page_width * 0.68
    for row in lines:
        row_words = row.get("words") or []
        full_row_text = normalize_text(
            row.get("text") or " ".join(str(word.get("text", "")) for word in row_words)
        )
        coa_words = [word for word in row_words if int(word.get("left", 0)) < amount_x_min]
        row_text = " ".join(str(word.get("text", "")) for word in coa_words) if coa_words else (row.get("text") or "")
        row_text = normalize_coa_text(row_text)
        row_text = re.sub(r"(5\d{4})\s+(\d)", r"\1\2", row_text)
        coa_match = re.search(r"(\d{4,6})\.\d{3}\.(5\d{5})\.[^\s|]+", row_text)
        item_match = re.search(r"\.(\d{3})\.(\d{3})\.([0-9A-Z]{2})\.(\d{3,6})\b", row_text, re.IGNORECASE)
        if not (coa_match and item_match):
            continue

        amount_words = [
            word for word in row.get("words", [])
            if int(word.get("left", 0)) >= amount_x_min and re.search(r"\d{1,3}(?:[.,]\d{3})+", str(word.get("text", "")))
        ]
        amounts = []
        if amount_words:
            for word in amount_words:
                amount = best_amount_from_text(str(word.get("text", "")))
                if not amount:
                    image = image or render_ocr_page_image(file_path, page_number, rotation)
                    amount = ocr_amount_word_crop(image, word)
                if amount:
                    amounts.append(amount)
        if not amounts:
            amounts = [best_amount_from_text(row.get("text") or "", allow_bare=False)]
        amounts = [amount for amount in amounts if amount > 0]
        if not amounts:
            continue

        akun = coa_match.group(2)
        item_code = ".".join(part.upper() for part in item_match.groups())
        pembebanan = pembebanan_from_full_coa(row_text, akun)
        amount = amounts[0]
        if not (pembebanan and is_valid_pembebanan(pembebanan, amount)):
            continue
        description = desc_by_item.get(item_code, "")
        if row_words:
            left = min(int(word.get("left", 0)) for word in row_words)
            top = min(int(word.get("top", 0)) for word in row_words)
            right = max(int(word.get("left", 0)) + int(word.get("width", 0)) for word in row_words)
            bottom = max(int(word.get("top", 0)) + int(word.get("height", 0)) for word in row_words)
            source_bbox = [left, top, right, bottom]
        else:
            source_bbox = []
        sp2d_match = _RE_SP2D_BARE.search(full_row_text)
        tanggal_sp2d_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", full_row_text)
        nomor_spm_detail_match = re.search(
            r"\b([0-9]{3,6}[A-Z0-9]?)/\d{4,6}/20\d{2}\b",
            full_row_text.upper(),
        )
        rows.append({
            "akun": akun,
            "jumlah": amount,
            "bruto": amount,
            "netto": amount,
            "no_bukti": item_code.split(".")[-1],
            "keperluan": description or "Perlu Review Uraian",
            "pembebanan": pembebanan,
            "satker": coa_match.group(1),
            "nomor_sp2d": sp2d_match.group(1) if sp2d_match else "",
            "tanggal_sp2d": parse_date(tanggal_sp2d_match.group(1)) if tanggal_sp2d_match else "",
            "nomor_spm_detail": normalize_doc_number(nomor_spm_detail_match.group(1)) if nomor_spm_detail_match else "",
            "source_page": page_number,
            "source_types": source_types,
            "source_row_id": item_code,
            "ocr_rotation": rotation,
            "source_bbox": source_bbox,
            "field_provenance": {
                "akun": {"page": page_number, "bbox": source_bbox, "method": "tsv", "confidence": row.get("confidence", 0)},
                "bruto": {"page": page_number, "bbox": source_bbox, "method": "tsv", "confidence": row.get("confidence", 0)},
                "pembebanan": {"page": page_number, "bbox": source_bbox, "method": "tsv", "confidence": row.get("confidence", 0)},
            },
            "source_priority": "DETAIL_SPP_SPM_SP2D",
            "needs_review": not bool(description),
            "review_note": "" if description else "Uraian Lampiran COA belum cocok pasti.",
        })
    return dedupe_detail_items(rows)


def dedupe_detail_items(items):
    output = []
    seen = set()
    for item in items:
        key = (
            normalize_text(item.get("source_row_id") or item.get("no_bukti")).upper(),
            normalize_text(item.get("pembebanan")).upper(),
            money_value_for_key(item.get("jumlah")),
            normalize_text(item.get("keperluan")).upper(),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def fallback_detail_candidate_pages(page_details):
    """Pilih sedikit halaman UNKNOWN ber-confidence rendah untuk rotasi tabel.

    Fallback hanya aktif ketika classifier tidak menemukan halaman detail sama
    sekali. Kandidat diprioritaskan berdasarkan kedekatan dengan blok SPM/SPP,
    bukan berdasarkan nomor halaman tetap atau warna kertas.
    """
    if any("DETAIL_SPP_SPM_SP2D" in set(page.get("page_types") or []) for page in page_details):
        return []

    reference_pages = [
        int(page.get("page_number") or page.get("page") or 0)
        for page in page_details
        if set(page.get("page_types") or []).intersection({"SPM", "SPP"})
    ]
    max_confidence = float(os.getenv("OCR_TABLE_FALLBACK_MAX_CONFIDENCE", "55"))
    max_pages = max(1, int(os.getenv("OCR_TABLE_FALLBACK_MAX_PAGES", "3")))
    allowed_types = {"UNKNOWN", "SP2D", "SP2D_DETAIL"}
    candidates = []
    for page in page_details:
        page_types = set(page.get("page_types") or ["UNKNOWN"])
        if page_types - allowed_types:
            continue
        try:
            confidence = float(page.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence > max_confidence:
            continue
        page_number = int(page.get("page_number") or page.get("page") or 0)
        distance = min((abs(page_number - reference) for reference in reference_pages), default=9999)
        candidates.append((distance, confidence, page_number, page))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in candidates[:max_pages]]


def parse_position_detail_items(file_path, page_details, default_description="", expected_total=None):
    best_by_page = {}
    saw_detail_candidate = False
    regular_pages = [
        page for page in page_details
        if set(page.get("page_types") or []).intersection({"DETAIL_SPP_SPM_SP2D", "LAMPIRAN_COA", "SPM", "SPP"})
    ]
    fallback_pages = fallback_detail_candidate_pages(page_details) if expected_total else []
    fallback_page_ids = {id(page) for page in fallback_pages}
    for page in regular_pages + fallback_pages:
        page_types = set(page.get("page_types") or [])
        is_fallback_candidate = id(page) in fallback_page_ids
        if is_fallback_candidate:
            page_types.update({"DETAIL_SPP_SPM_SP2D", "SP2D_DETAIL"})
            page["table_candidate_reason"] = "low_confidence_unknown_near_spm_spp"
        if "DETAIL_SPP_SPM_SP2D" in page_types:
            saw_detail_candidate = True
        page_rotation = int(page.get("rotation") or 0)
        existing_tsv_variant = table_variant_from_page_tsv(page, page_types)
        is_detail_page = "DETAIL_SPP_SPM_SP2D" in page_types
        if existing_tsv_variant:
            variants = [existing_tsv_variant]
        else:
            variants = ocr_page_table_variants(
                file_path,
                page.get("page_number") or page.get("page"),
                (page_rotation,),
            )
        if not variants and not is_detail_page:
            continue

        variant_rows_cache = {}
        structured_rows_by_rotation = {}

        def rows_for_variant(variant):
            cache_key = id(variant)
            if cache_key not in variant_rows_cache:
                rows = parse_detail_sp2d_rows_from_tsv_lines(
                    file_path,
                    variant.get("page"),
                    variant.get("rotation", 0),
                    variant.get("lines") or [],
                    list(page_types),
                    {},
                )
                # Windows and Linux can produce different TSV word grouping for
                # the same scan.  Do not choose a rotation from text score alone:
                # when TSV rows are empty, validate that rotation through the
                # ruled-table/grid parser before rejecting it.
                if not rows and is_detail_page:
                    rotation_key = (
                        variant.get("page"),
                        int(variant.get("rotation") or 0),
                    )
                    if rotation_key not in structured_rows_by_rotation:
                        structured_rows_by_rotation[rotation_key] = parse_detail_sp2d_rows_by_crop(
                            file_path,
                            rotation_key[0],
                            rotation_key[1],
                            list(page_types),
                            {},
                        )
                    rows = structured_rows_by_rotation[rotation_key]
                variant_rows_cache[cache_key] = rows
            return variant_rows_cache[cache_key]

        def exact_total_variants(candidates):
            if not expected_total:
                return []
            expected = parse_decimal(expected_total)
            return [
                variant
                for variant in candidates
                if rows_for_variant(variant)
                and sum(
                    (parse_decimal(item.get("jumlah")) for item in rows_for_variant(variant)),
                    Decimal("0"),
                ) == expected
            ]

        def retry_needed(candidates, exact_candidates):
            if not is_detail_page:
                return False
            if expected_total:
                return not exact_candidates
            return not any(rows_for_variant(variant) for variant in candidates)

        exact_variants = exact_total_variants(variants) if is_detail_page else []
        # TSV dari cache adalah fast path. OCR 300 DPI pada rotasi terpilih baru
        # dijalankan jika TSV cache tidak menghasilkan total yang tervalidasi.
        if existing_tsv_variant and retry_needed(variants, exact_variants):
            variants.extend(ocr_page_table_variants(
                file_path,
                page.get("page_number") or page.get("page"),
                (page_rotation,),
            ))
            exact_variants = exact_total_variants(variants)

        if retry_needed(variants, exact_variants):
            remaining_rotations = tuple(
                rotation for rotation in ordered_table_rotations(page_rotation)
                if rotation != page_rotation
            )
            if remaining_rotations:
                variants.extend(ocr_page_table_variants(
                    file_path,
                    page.get("page_number") or page.get("page"),
                    remaining_rotations,
                ))
                exact_variants = exact_total_variants(variants)

        if not variants:
            continue

        variants_with_rows = [variant for variant in variants if rows_for_variant(variant)] if is_detail_page else []
        # Nilai total adalah validator utama. Keyword/score hanya menjadi pemilih
        # di antara varian yang sama-sama menghasilkan baris tabel.
        best = max(exact_variants or variants_with_rows or variants, key=lambda item: item["score"])
        if best["score"] <= 0:
            continue
        best_by_page[best["page"]] = {
            **best,
            "source_types": list(page_types),
            "variants": variants,
            # rows_for_variant dapat memulihkan tabel melalui parser grid saat
            # TSV Windows kosong. Simpan hasil valid itu; jangan hitung ulang
            # dari TSV kosong pada tahap final.
            "validated_rows": rows_for_variant(best) if is_detail_page else [],
        }
        page["table_ocr_rotation"] = best["rotation"]
        page["table_ocr_language"] = best.get("language") or "cached_tsv"
        page["table_ocr_language_fallback_used"] = bool(best.get("language_fallback_used"))
        page["table_ocr_language_errors"] = best.get("language_errors") or []
        page["table_ocr_confidence"] = best["confidence"]
        page["table_ocr_score"] = round(best["score"], 2)
        if is_fallback_candidate and exact_variants:
            # Kandidat fallback berikutnya tidak perlu di-OCR setelah satu tabel
            # menghasilkan total yang sama dengan bruto tervalidasi.
            break

    coa_rows = []
    for page in best_by_page.values():
        tagged_lines = [{**line, "rotation": page["rotation"]} for line in page["lines"]]
        coa_rows.extend(parse_full_coa_rows(tagged_lines, page["page"], page["source_types"]))
    coa_by_item = {}
    for row in coa_rows:
        coa_by_item.setdefault(row["item_code"], row)

    desc_by_item = extract_lampiran_descriptions(best_by_page)
    detail_page_rows = []
    has_detail_page = False
    for page in best_by_page.values():
        if "DETAIL_SPP_SPM_SP2D" not in set(page.get("source_types") or []):
            continue
        has_detail_page = True
        tsv_rows = [dict(row) for row in page.get("validated_rows") or []]
        if not tsv_rows:
            tsv_rows = parse_detail_sp2d_rows_from_tsv_lines(
                file_path,
                page["page"],
                page["rotation"],
                page.get("lines") or [],
                page["source_types"],
                desc_by_item,
            )
        for row in tsv_rows:
            item_code = normalize_text(row.get("source_row_id")).upper()
            description = prefer_richer_description(
                desc_by_item.get(item_code) or row.get("keperluan"),
                default_description,
            )
            if description:
                row["keperluan"] = description
                row["needs_review"] = description == "Perlu Review Uraian"
                row["review_note"] = "" if not row["needs_review"] else "Uraian Lampiran COA belum cocok pasti."
        detail_page_rows.extend(tsv_rows)
        if not tsv_rows and page.get("source") != "existing_tsv":
            detail_page_rows.extend(parse_detail_sp2d_rows_by_crop(
                file_path,
                page["page"],
                page["rotation"],
                page["source_types"],
                desc_by_item,
            ))
    detail_page_rows = dedupe_detail_items(detail_page_rows)
    if expected_total:
        detail_total = sum((parse_decimal(item.get("jumlah")) for item in detail_page_rows), Decimal("0"))
        if detail_page_rows and detail_total == parse_decimal(expected_total):
            return detail_page_rows, {
                "source": "DETAIL_SPP_SPM_SP2D",
                "rows_before_dedupe": len(detail_page_rows),
                "rows_after_dedupe": len(detail_page_rows),
                "total": detail_total,
                "pages": best_by_page,
            }
        if has_detail_page:
            return [], {
                "source": "PERLU_REVIEW_PARSER_TABEL",
                "review_reason": "DETAIL_TSV_SCHEMA_NO_ROWS" if any((page.get("source") == "existing_tsv" and not detail_page_rows) for page in best_by_page.values()) else "DETAIL_SCHEMA_NO_ROWS_OR_TOTAL_MISMATCH",
                "rows_before_dedupe": len(detail_page_rows),
                "rows_after_dedupe": len(detail_page_rows),
                "total": detail_total,
                "pages": best_by_page,
            }

    detail_rows = []
    for page in best_by_page.values():
        tagged_lines = [{**line, "rotation": page["rotation"]} for line in page["lines"]]
        detail_rows.extend(parse_lampiran_detail_rows(tagged_lines, coa_by_item, page["page"], page["source_types"]))

    detail_rows = dedupe_detail_items(detail_rows)
    if expected_total:
        total = sum((parse_decimal(item.get("jumlah")) for item in detail_rows), Decimal("0"))
        if detail_rows and total == parse_decimal(expected_total):
            return detail_rows, {
                "source": "DETAIL_SPP_SPM_SP2D",
                "rows_before_dedupe": len(detail_rows),
                "rows_after_dedupe": len(detail_rows),
                "total": total,
                "pages": best_by_page,
            }
    if saw_detail_candidate:
        return [], {
            "source": "PERLU_REVIEW_PARSER_TABEL",
            "review_reason": "DETAIL_TSV_SCHEMA_NO_ROWS" if any((page.get("source") == "existing_tsv") for page in best_by_page.values()) else "DETAIL_SCHEMA_NO_ROWS",
            "rows_before_dedupe": len(detail_rows) or len(detail_page_rows),
            "rows_after_dedupe": len(detail_rows) or len(detail_page_rows),
            "total": sum((parse_decimal(item.get("jumlah")) for item in (detail_rows or detail_page_rows)), Decimal("0")),
            "pages": best_by_page,
        }
    return [], {
        "source": "DETAIL_SPP_SPM_SP2D_REVIEW" if detail_rows or detail_page_rows else "",
        "rows_before_dedupe": len(detail_rows) or len(detail_page_rows),
        "rows_after_dedupe": len(detail_rows) or len(detail_page_rows),
        "total": sum((parse_decimal(item.get("jumlah")) for item in (detail_rows or detail_page_rows)), Decimal("0")),
        "pages": best_by_page,
    }


def extract_drpp_pembebanan_by_amount(text):
    """Ambil mapping (akun, nominal) -> pembebanan ringkas dari lampiran COA DRPP/SPM."""
    mapping = {}
    amount_totals = {}
    compact = normalize_text(text)
    starts = [match.start() for match in re.finditer(r"\d{4,6}\.\d{3}\.5\d{5}\.", compact)]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(compact)
        block = compact[start:end]
        akun_match = re.search(r"\d{4,6}\.\d{3}\.(5\d{5})\.", block)
        if not akun_match:
            continue
        akun = akun_match.group(1)
        pembebanan = compact_pembebanan_from_coa(block, akun)
        if not pembebanan:
            continue
        for amount_text in re.findall(r"\b\d{1,3}(?:\.\d{3})+(?:[.,]\d{2})?\b", block):
            amount = parse_decimal(amount_text)
            if amount > 0:
                mapping.setdefault((akun, amount), pembebanan)
                amount_totals.setdefault((akun, pembebanan), Decimal("0"))
                amount_totals[(akun, pembebanan)] += amount
    for (akun, pembebanan), total in amount_totals.items():
        if total > 0:
            mapping.setdefault((akun, total), pembebanan)
    return mapping


def parse_drpp_number(value):
    text = normalize_text(value).upper()
    match = re.search(r"\b([0-9A-Z]{3,6})/DRPP/([0-9]{3,9})/([0-9]{4})\b", text)
    if not match:
        return {"nomor_drpp": text[:100], "no_drpp": text[:100], "satker_code": "", "tahun": None}
    return {
        "nomor_drpp": match.group(0),
        "no_drpp": match.group(1),
        "satker_code": match.group(2),
        "tahun": int(match.group(3)),
    }


def extract_drpp_printed_total(text):
    candidates = []
    patterns = [
        r"JUM(?:LAH|IAH)\s+SPP\s+INI\s*[:\-]?\s*(\d{1,3}(?:[.,]\d{3})+(?:,\d{2})?)",
        r"JUM(?:LAH|IAH)\s+LAMPIRAN\s*[:\-]?\s*(\d{1,3}(?:[.,]\d{3})+(?:,\d{2})?)",
        r"TOTAL\s*[:\-]?\s*(\d{1,3}(?:[.,]\d{3})+(?:,\d{2})?)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            amount = parse_decimal(match.group(1))
            if amount > 0:
                candidates.append(amount)
    return candidates[-1] if candidates else Decimal("0")


def extract_drpp_date(text):
    match = re.search(r"(?:TANGGAL|TGL)\s*[:\-]?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{1,2}\s+[A-Za-zÀ-ÿ]+\s+\d{4})", text, re.IGNORECASE)
    return parse_date(match.group(1)) if match else None


def parse_drpp_pdf(file_path, ocr=False, extracted=None):
    extracted = extracted or extract_pdf_text(file_path, ocr=ocr)
    page_details = annotate_page_details(extracted.get("page_details", []))
    if not page_details:
        page_details = annotate_page_details([
            {"text": page_text, "extracted_text": page_text, "page_number": index}
            for index, page_text in enumerate(extracted.get("pages", []), start=1)
        ])
    native_text = "\n".join(page.get("text") or page.get("extracted_text") or "" for page in page_details)
    has_tsv_words = any(page.get("tsv_words") or page.get("words") or page.get("ocr_words") for page in page_details)
    forced_tsv_ocr = False
    if not ocr and not normalize_text(native_text) and not has_tsv_words:
        extracted = extract_pdf_text(file_path, ocr=True)
        page_details = annotate_page_details(extracted.get("page_details", []))
        forced_tsv_ocr = True
    drpp_pages = [
        page for page in page_details
        if "DRPP" in set(page.get("page_types") or [])
    ]
    drpp_total_pages = [
        page for page in page_details
        if re.search(r"JUM(?:LAH|IAH)\s+SPP\s+INI|JUM(?:LAH|IAH)\s+LAMPIRAN", (page.get("text") or page.get("extracted_text") or ""), re.IGNORECASE)
    ]
    coa_pages = [
        page for page in page_details
        if "LAMPIRAN_COA" in set(page.get("page_types") or [])
    ]
    item_pages = []
    seen_item_pages = set()
    for page in [*drpp_pages, *drpp_total_pages]:
        page_key = page.get("page_number") or page.get("page") or id(page)
        if page_key in seen_item_pages:
            continue
        seen_item_pages.add(page_key)
        item_pages.append(page)
    item_text = "\n".join((page.get("text") or page.get("extracted_text") or "") for page in item_pages) or "\n".join(extracted["pages"])
    header_text = "\n".join((page.get("text") or page.get("extracted_text") or "") for page in (drpp_pages + coa_pages)) or item_text
    text = "\n".join(extracted["pages"])
    upper = header_text.upper()
    nomor_match = re.search(r"(?:NOMOR\s+DRPP|DRPP\s+NOMOR|NO\.?\s*DRPP)\s*[:\-]?\s*([0-9A-Z./-]+)", upper)
    nomor_bare_match = re.search(r"\b([0-9A-Z]{3,6}/DRPP/[0-9]{3,9}/[0-9]{4})\b", upper)
    spm_match = re.search(r"(?:NOMOR\s+SPM|SPM\s+NOMOR|NO\.?\s*SPM)\s*[:\-]?\s*([0-9A-Z./-]+)", upper)
    kw_numbers = sorted(set(re.findall(r"(?:KW|KUITANSI)\s*[:\-]?\s*([0-9A-Z./-]{3,})", item_text.upper())))
    akun_values = sorted(set(re.findall(r"\b(5[0-9]{5})\b", upper)))
    amounts = re.findall(r"\b\d{1,3}(?:[.,]\d{3})+(?:,\d{2})?\b", item_text)
    ocr_trace = []
    items = []
    for page in item_pages:
        page_words = page.get("tsv_words") or page.get("words") or page.get("ocr_words") or []
        page_items = parse_drpp_items_from_tsv(page_words, page_number=page.get("page_number") or page.get("page") or 1)
        if not page_items and page_words:
            page_items = parse_drpp_items_from_tsv_rows(
                page_words,
                page_number=page.get("page_number") or page.get("page") or 1,
            )
        items.extend(page_items)
        ocr_trace.append({
            "file": os.path.basename(file_path),
            "page": page.get("page_number") or page.get("page") or 1,
            "page_type": ",".join(page.get("page_types") or []),
            "native_text_length": len(page.get("text") or page.get("extracted_text") or ""),
            "ocr_requested": bool(ocr or forced_tsv_ocr),
            "ocr_called": page.get("engine") == "tesseract" or page.get("method") == "tesseract",
            "rotation": next((word.get("rotation", 0) for word in page_words if isinstance(word, dict)), 0),
            "tsv_word_count": len(page_words),
            "parser_method": (page_items[0].get("method") if page_items else "tsv_cell") if page_words else "",
            "parsed_item_count": len(page_items),
            "total": sum((item.get("jumlah") or Decimal("0") for item in page_items), Decimal("0")),
            "fallback": "",
            "review_reason": "" if page_items else ("OCR TSV tidak tersedia." if not page_words else "Parser TSV tidak menemukan item."),
            "duration_ms": 0,
        })
    tsv_attempted = any(trace["tsv_word_count"] > 0 for trace in ocr_trace)
    tsv_review_warning = ""
    if not items and not tsv_attempted:
        items = parse_drpp_items_from_text(item_text)
        for trace in ocr_trace:
            trace["fallback"] = "legacy_text" if items else "none"
            trace["parser_method"] = trace["parser_method"] or "legacy_text"
    elif not items and tsv_attempted:
        tsv_review_warning = "OCR TSV sudah dicoba, tetapi parser tabel DRPP tidak menemukan item valid; perlu review parser."
    pembebanan_by_amount = extract_drpp_pembebanan_by_amount(header_text)
    for item in items:
        amount = item.get("jumlah") or Decimal("0")
        current_akun = normalize_text(item.get("akun", ""))
        match_key = (current_akun, amount)
        pembebanan = pembebanan_by_amount.get(match_key)
        if not pembebanan:
            # OCR kadang membaca digit awal akun 5 sebagai 6; pakai lampiran COA bila nominalnya unik.
            candidates = [(akun, value) for (akun, value), pem in pembebanan_by_amount.items() if value == amount]
            if len(candidates) == 1:
                corrected_akun, _ = candidates[0]
                item["akun"] = corrected_akun
                pembebanan = pembebanan_by_amount.get((corrected_akun, amount))
        if pembebanan:
            item["pembebanan"] = pembebanan
            if re.fullmatch(r"5\d{5}", normalize_text(item.get("akun"))):
                item["review_fields"] = [
                    field for field in (item.get("review_fields") or []) if field != "akun_invalid"
                ]
                item["needs_review"] = bool(item["review_fields"])
                item["status"] = "Perlu Review" if item["needs_review"] else "Terbaca"
    allow_legacy_item_fallback = not tsv_attempted
    if not items and allow_legacy_item_fallback:
        for idx, kw in enumerate(kw_numbers[:100], start=1):
            items.append({
                "no_urut": idx,
                "no_bukti": kw,
                "tanggal_bukti": "",
                "penerima": "",
                "npwp": "",
                "akun": akun_values[idx - 1] if idx - 1 < len(akun_values) else "",
                "jumlah": parse_decimal(amounts[idx - 1]) if idx - 1 < len(amounts) else Decimal("0"),
                "keperluan": "",
            })
    if not items and allow_legacy_item_fallback and amounts:
        for idx, amount in enumerate(amounts[:20], start=1):
            items.append({"no_urut": idx, "no_bukti": "", "tanggal_bukti": "", "penerima": "", "npwp": "", "akun": "", "jumlah": parse_decimal(amount), "keperluan": ""})
    drpp_number = nomor_match.group(1) if nomor_match else (nomor_bare_match.group(1) if nomor_bare_match else guess_number_from_filename(file_path, "DRPP"))
    drpp_parts = parse_drpp_number(drpp_number)
    tanggal_drpp = extract_drpp_date(header_text)
    satker_app_code = ""
    satker_app_name = ""
    try:
        from apps.core.satker import infer_satker_from_name

        satker_name_match = re.search(r"(BADAN PUSAT STATISTIK\s+[A-Z\s.]+)", header_text.upper())
        if satker_name_match:
            satker_app_code, satker_app_name = infer_satker_from_name(satker_name_match.group(1))
    except Exception:
        pass
    for item in items:
        item["no_drpp"] = drpp_parts["nomor_drpp"]
    total = sum((item["jumlah"] for item in items), Decimal("0"))
    printed_total = extract_drpp_printed_total(item_text)
    status = parser_status(extracted)
    warnings = list(extracted["warnings"])
    if tsv_review_warning:
        status = "needs_manual_review"
        warnings.append(tsv_review_warning)
    item_review_reasons = sorted({reason for item in items for reason in (item.get("review_fields") or []) if item.get("needs_review")})
    if item_review_reasons:
        status = "needs_manual_review"
        warnings.append("Sebagian item DRPP perlu review: " + ", ".join(item_review_reasons[:8]) + ".")
    if printed_total and total and abs(printed_total - total) > Decimal("1"):
        status = "needs_manual_review"
        warnings.append(f"Total DRPP tercetak Rp{printed_total:,.0f} tidak sama dengan total item Rp{total:,.0f}.")
    return {
        "file_name": os.path.basename(file_path),
        "page_count": extracted["page_count"],
        "method": extracted["method"],
        "best_engine": extracted.get("best_engine", extracted["method"]),
        "status": status,
        "warnings": warnings,
        "page_details": page_details,
        "confidence": extracted.get("confidence", 0.0),
        "engines_tried": extracted.get("engines_tried", []),
        "native_text_length": extracted.get("native_text_length", 0),
        "tesseract_called": extracted.get("tesseract_called", False),
        "tesseract_text_length": extracted.get("tesseract_text_length", 0),
        "tesseract_reason": extracted.get("tesseract_reason", ""),
        "metadata": {
            "nomor_drpp": drpp_parts["nomor_drpp"],
            "no_drpp": drpp_parts["no_drpp"],
            "satker_code": drpp_parts["satker_code"],
            "satker_app_code": satker_app_code,
            "satker_app_name": satker_app_name,
            "tahun": drpp_parts["tahun"],
            "tanggal_drpp": tanggal_drpp,
            "nomor_spm": spm_match.group(1) if spm_match else "",
            "total": total,
            "printed_total": printed_total,
            "total_valid": not printed_total or abs(printed_total - total) <= Decimal("1"),
        },
        "items": items,
        "ocr_trace": ocr_trace,
        "text_sample": text[:2000],
    }


def _lite_ocr_pages(file_path, max_pages=15, dpi=160):
    timeout = int(os.getenv("OCR_LITE_PAGE_TIMEOUT_SECONDS", "20"))
    try:
        import fitz
        import pytesseract
        from PIL import Image, ImageFilter, ImageOps
        import io
    except Exception as exc:
        return "", [f"OCR lite KW tidak tersedia: {exc}"]
    if not configure_tesseract(pytesseract):
        return "", ["Tesseract OCR binary tidak ditemukan; isi OCR_TESSERACT_CMD atau PATH."]

    warnings = []
    texts = []
    try:
        doc = fitz.open(file_path)
        page_total = min(doc.page_count, max_pages)
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        for index in range(page_total):
            pixmap = doc[index].get_pixmap(matrix=matrix, alpha=False)
            image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("L")
            image = ImageOps.autocontrast(image).filter(ImageFilter.SHARPEN)
            try:
                texts.append(pytesseract.image_to_string(image, lang="ind+eng", config="--psm 6", timeout=timeout))
            except Exception as exc:
                warnings.append(f"OCR lite KW halaman {index + 1} gagal: {exc}")
        doc.close()
    except Exception as exc:
        warnings.append(f"OCR lite KW gagal membuka/render PDF: {exc}")
    return "\n".join(texts), warnings


def parse_kw_pdf_fast(file_path, ocr=False, max_pages=13):
    max_pages = int(os.getenv("OCR_LITE_KW_MAX_PAGES", str(max_pages)))
    parsed = parse_kw_filename_stub(file_path)
    item = parsed["items"][0] if parsed["items"] else {
        "no_urut": 1,
        "no_bukti": "",
        "tanggal_bukti": "",
        "penerima": "",
        "npwp": "",
        "akun": "",
        "jumlah": Decimal("0"),
        "keperluan": "Perlu review manual dari file KW/lampiran.",
    }
    if not ocr:
        return parsed

    text, warnings = _lite_ocr_pages(file_path, max_pages=max_pages)
    upper = text.upper()
    if warnings:
        parsed["warnings"].extend(warnings)
    if not text.strip():
        parsed["warnings"].append("OCR lite KW kosong; metadata KW perlu review.")
        return parsed

    memo = re.search(r"PEMBAYARAN\s+SEJUMLAH\s+RP\.?\s*[:\-]?\s*[\-—–]?\s*([0-9.,]+)", upper)
    if memo:
        item["netto"] = parse_decimal(memo.group(1))

    fp_match = re.search(r"(?:NOMOR\s+SERI\s+FAKTUR\s+PAJAK|FAKTUR\s+PAJAK)\s*[:\-]?\s*([0-9]{10,25})", upper)
    if not fp_match:
        fp_match = re.search(r"\b(0[0-9]{16,20})\b", upper)
    if fp_match:
        item["fp"] = fp_match.group(1)

    rekap_match = re.search(
        r"JUMLAH\s+(?P<bruto>\d{1,3}(?:[.,]\d{3})+)\s*\|\s*(?P<pajak>\d{1,3}(?:[.,]\d{3})+)\s*\|\s*(?P<netto>\d{1,3}(?:[.,]\d{3})+)",
        upper,
    )
    if rekap_match:
        item["bruto"] = parse_decimal(rekap_match.group("bruto"))
        item["pph21"] = parse_decimal(rekap_match.group("pajak"))
        item["netto"] = parse_decimal(rekap_match.group("netto"))

    parsed["method"] = "ocr_lite"
    parsed["best_engine"] = "ocr_lite"
    parsed["status"] = "parsed_ocr" if item.get("netto") or item.get("fp") or item.get("pph21") else "needs_manual_review"
    parsed["text_sample"] = text[:2000]
    parsed["items"] = [item]
    return parsed


def guess_number_from_filename(file_path, keyword):
    name = Path(file_path).stem.upper()
    match = re.search(rf"{keyword}\s*(?:NOMOR)?\s*([0-9A-Z]+)", name)
    if match:
        return match.group(1)
    match = re.search(r"\b([0-9]{3,6}[A-Z]?)\b", name)
    return match.group(1) if match else ""


def classify_document(file_name, text=""):
    name = file_name.upper()
    upper = text.upper()
    haystack = f"{name}\n{upper[:1000]}"
    content_hint = upper[:3000]
    if "DETAIL PENGELUARAN DAN POTONGAN PADA SPP/SPM/SP2D" in content_hint:
        return "SPM"
    if "SURAT PERINTAH MEMBAYAR" in content_hint or "SURAT PERMINTAAN PEMBAYARAN" in content_hint:
        return "SPM"
    if "LAMPIRAN DAFTAR RINCIAN PERMINTAAN PEMBAYARAN" in content_hint or "RO.KOMP.SUBKOMP" in content_hint:
        return "LAMPIRAN_COA"
    if "DAFTAR RINCIAN PERMINTAAN PEMBAYARAN" in content_hint or "BUKTI PENGELUARAN" in content_hint:
        return "DRPP"
    if "SURAT PERINTAH PENCAIRAN DANA" in content_hint:
        return "SP2D"
    if "FAKTUR" in haystack:
        return "FAKTUR"
    if "INVOICE" in haystack:
        return "INVOICE"
    if "BAST" in haystack or "BERITA ACARA SERAH TERIMA" in haystack:
        return "BAST"
    if "SURAT SETORAN PAJAK" in haystack or re.search(r"\bSSP\b", haystack):
        return "SSP"
    if "DRPP" in name:
        return "DRPP"
    if "KW" in name or "KUITANSI" in name:
        return "KW"
    if "SPM" in name or "SPP" in name:
        return "SPM"
    if "DRPP" in upper:
        return "DRPP"
    if "KW" in haystack or "KUITANSI" in haystack:
        return "KW"
    if "SPM" in haystack or "SPP" in haystack:
        return "SPM"
    if "LAMPIRAN" in haystack:
        return "LAMPIRAN_COA"
    return "UNKNOWN"


def validate_parsed_document(parsed):
    status = (parsed or {}).get("status", "")
    return {
        "ok": status in {"parsed_text", "parsed_ocr", "needs_manual_review"},
        "status": status or "needs_manual_review",
        "warnings": (parsed or {}).get("warnings", []),
    }


def preview_parsed_document(parsed):
    meta = (parsed or {}).get("metadata", {})
    return {
        "file_name": (parsed or {}).get("file_name", ""),
        "status": (parsed or {}).get("status", ""),
        "method": (parsed or {}).get("method", ""),
        "nomor_spm": meta.get("nomor_spm", ""),
        "nomor_drpp": meta.get("nomor_drpp", ""),
        "total": meta.get("total") or meta.get("total_pembayaran") or Decimal("0"),
        "item_count": len((parsed or {}).get("items") or (parsed or {}).get("detail_items") or []),
    }


def _parse_unknown_document(file_path, ocr=False):
    extracted = extract_pdf_text(file_path, ocr=False)
    return {
        "file_name": os.path.basename(file_path),
        "status": "needs_manual_review",
        "method": extracted.get("method", "text"),
        "warnings": ["Jenis dokumen belum dikenali."],
        "metadata": {},
        "items": [],
        "text_sample": (extracted.get("combined_text") or "")[:2000],
    }


DOCUMENT_PARSER_REGISTRY = {
    "SPM": {
        "classifier": classify_document,
        "extractor": parse_spm_pdf,
        "validator": validate_parsed_document,
        "preview": preview_parsed_document,
    },
    "SPP": {
        "classifier": classify_document,
        "extractor": parse_spm_pdf,
        "validator": validate_parsed_document,
        "preview": preview_parsed_document,
    },
    "DRPP": {
        "classifier": classify_document,
        "extractor": parse_drpp_pdf,
        "validator": validate_parsed_document,
        "preview": preview_parsed_document,
    },
    "KW": {
        "classifier": classify_document,
        "extractor": lambda file_path, ocr=False: parse_kw_pdf_fast(file_path, ocr=ocr),
        "validator": validate_parsed_document,
        "preview": preview_parsed_document,
    },
    "LAMPIRAN_COA": {
        "classifier": classify_document,
        "extractor": _parse_unknown_document,
        "validator": validate_parsed_document,
        "preview": preview_parsed_document,
    },
    "SSP": {
        "classifier": classify_document,
        "extractor": _parse_unknown_document,
        "validator": validate_parsed_document,
        "preview": preview_parsed_document,
    },
    "INVOICE": {
        "classifier": classify_document,
        "extractor": _parse_unknown_document,
        "validator": validate_parsed_document,
        "preview": preview_parsed_document,
    },
    "FAKTUR": {
        "classifier": classify_document,
        "extractor": _parse_unknown_document,
        "validator": validate_parsed_document,
        "preview": preview_parsed_document,
    },
    "BAST": {
        "classifier": classify_document,
        "extractor": _parse_unknown_document,
        "validator": validate_parsed_document,
        "preview": preview_parsed_document,
    },
    "SP2D": {
        "classifier": classify_document,
        "extractor": _parse_unknown_document,
        "validator": validate_parsed_document,
        "preview": preview_parsed_document,
    },
    "UNKNOWN": {
        "classifier": classify_document,
        "extractor": _parse_unknown_document,
        "validator": validate_parsed_document,
        "preview": preview_parsed_document,
    },
}


def parse_document_with_registry(file_path, file_name="", doc_type="", ocr=False):
    doc_type = (doc_type or classify_document(file_name or os.path.basename(file_path), "") or "UNKNOWN").upper()
    entry = DOCUMENT_PARSER_REGISTRY.get(doc_type, DOCUMENT_PARSER_REGISTRY["UNKNOWN"])
    parsed = entry["extractor"](file_path, ocr=ocr)
    parsed["document_type"] = doc_type
    parsed["validation"] = entry["validator"](parsed)
    parsed["preview"] = entry["preview"](parsed)
    return parsed


def pdf_page_count(file_path):
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(file_path)
        count = doc.page_count
        doc.close()
        return count
    except Exception:
        return 0


def parse_kw_filename_stub(file_path, warning=""):
    kw_number = guess_number_from_filename(file_path, "KW")
    if not kw_number:
        match = re.search(r"\bKW\s*([0-9A-Z]+)\b", Path(file_path).stem.upper())
        kw_number = match.group(1) if match else ""
    return {
        "file_name": os.path.basename(file_path),
        "page_count": pdf_page_count(file_path),
        "method": "filename",
        "best_engine": "filename",
        "status": "needs_manual_review",
        "warnings": [warning] if warning else [],
        "page_details": [],
        "confidence": 0.0,
        "engines_tried": ["filename"],
        "metadata": {"nomor_drpp": guess_number_from_filename(file_path, "DRPP"), "nomor_spm": guess_number_from_filename(file_path, "SPM"), "total": Decimal("0")},
        "items": [{"no_urut": 1, "no_bukti": kw_number, "tanggal_bukti": "", "penerima": "", "npwp": "", "akun": "", "jumlah": Decimal("0"), "keperluan": "Perlu review manual dari file KW/lampiran."}] if kw_number else [],
        "text_sample": "",
    }


def normalized_bukti_key(value):
    text = normalize_text(value).upper()
    match = re.search(r"\b(\d{3,6})\b", text)
    return match.group(1) if match else text


def safe_extract_zip(zip_path):
    from django.conf import settings

    temp_dir = tempfile.mkdtemp(prefix="intermilan_paket_")
    extracted = []
    seen_sha = {}
    with zipfile.ZipFile(zip_path) as archive:
        members = [member for member in archive.infolist() if not member.is_dir()]
        if len(members) > settings.MAX_ZIP_FILES:
            raise ValueError(f"Jumlah file ZIP melebihi batas {settings.MAX_ZIP_FILES} file.")
        total_uncompressed = sum(member.file_size for member in members)
        max_uncompressed = settings.MAX_ZIP_TOTAL_UNCOMPRESSED_MB * 1024 * 1024
        if total_uncompressed > max_uncompressed:
            raise ValueError(f"Total ukuran ekstraksi ZIP melebihi batas {settings.MAX_ZIP_TOTAL_UNCOMPRESSED_MB} MB.")
        for member in archive.infolist():
            name = member.filename
            if member.is_dir():
                continue
            if name.lower().endswith(".zip"):
                raise ValueError(f"Nested ZIP tidak didukung: {name}")
            target = Path(temp_dir) / name
            resolved_target = target.resolve()
            if not str(resolved_target).startswith(str(Path(temp_dir).resolve())):
                raise ValueError(f"ZIP tidak aman: {name}")
            if not name.lower().endswith(".pdf"):
                extracted.append({
                    "file_name": os.path.basename(name),
                    "relative_path": name,
                    "path": "",
                    "size": member.file_size,
                    "sha256": "",
                    "type": "SKIPPED",
                    "status": "skipped",
                    "skip_reason": "non_pdf",
                })
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with archive.open(member) as src, target.open("wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    dst.write(chunk)
            sha256 = digest.hexdigest()
            duplicate_of = seen_sha.get(sha256, "")
            if not duplicate_of:
                seen_sha[sha256] = name
            extracted.append({
                "file_name": os.path.basename(name),
                "relative_path": name,
                "path": str(target),
                "size": member.file_size,
                "sha256": sha256,
                "type": "",
                "status": "duplicate" if duplicate_of else "extracted",
                "skip_reason": "",
                "duplicate_of": duplicate_of,
            })
    return temp_dir, extracted


def classify_pdf_by_content_or_name(file_path, file_name):
    text_probe = {"method": "filename", "warnings": [], "pages": []}
    try:
        text_probe = extract_pdf_text(file_path, ocr=False)
        content_type = classify_document("", "\n".join(text_probe.get("pages", [])))
        if content_type != "UNKNOWN":
            return content_type, text_probe
    except Exception:
        pass
    return classify_document(file_name, ""), text_probe


def parse_paket_spm_zip(zip_path, ocr=False):
    temp_dir, files = safe_extract_zip(zip_path)
    parsed_files = []
    spm_data = None
    drpp_data = None
    drpp_list = []
    kw_by_drpp = {}
    kw_items = []
    fatal_errors = []
    classified_types = []
    for item in files:
        if item["status"] != "extracted":
            parsed_files.append(item)
            continue
        doc_type, _ = classify_pdf_by_content_or_name(item["path"], item["file_name"])
        item["type"] = doc_type
        classified_types.append(doc_type)
    has_drpp_file = "DRPP" in classified_types
    for item in files:
        if item["status"] != "extracted":
            continue
        doc_type = item.get("type") or "UNKNOWN"
        _, text_probe = classify_pdf_by_content_or_name(item["path"], item["file_name"])
        if doc_type == "SPM":
            parsed = parse_document_with_registry(item["path"], item["file_name"], doc_type, ocr=ocr)
            spm_data = spm_data or parsed
        elif doc_type == "DRPP":
            parsed = parse_document_with_registry(item["path"], item["file_name"], doc_type, ocr=ocr)
            drpp_data = drpp_data or parsed
            drpp_list.append(parsed)
            drpp_number = parsed.get("metadata", {}).get("nomor_drpp", "")
            drpp_items = []
            for row in parsed.get("items", []):
                row = {**row, "no_drpp": drpp_number, "source_file": item["file_name"]}
                drpp_items.append(row)
            kw_by_drpp.setdefault(drpp_number or f"DRPP-{len(drpp_list)}", []).extend(drpp_items)
            kw_items.extend(drpp_items)
        elif doc_type == "KW":
            if not has_drpp_file:
                warning = "KW/Bukti wajib diunggah bersama DRPP; file tidak dijadikan transaksi."
                parsed_files.append({**item, "type": doc_type, "parse_status": "needs_manual_review", "method": "classifier", "warnings": [warning]})
                fatal_errors.append(warning)
                continue
            parsed = parse_document_with_registry(item["path"], item["file_name"], doc_type, ocr=ocr)
            existing_keys = {normalized_bukti_key(row.get("no_bukti", "")) for row in kw_items if row.get("no_bukti")}
            drpp_number = parsed.get("metadata", {}).get("nomor_drpp", "")
            new_items = []
            for row in parsed.get("items", []):
                row_key = normalized_bukti_key(row.get("no_bukti", ""))
                row = {**row, "no_drpp": drpp_number, "source_file": item["file_name"]}
                if row_key in existing_keys:
                    for existing in kw_items:
                        if normalized_bukti_key(existing.get("no_bukti", "")) == row_key:
                            for field in ("netto", "bruto", "pph21", "fp", "pembebanan"):
                                if row.get(field) not in (None, "", Decimal("0")):
                                    existing[field] = row[field]
                            existing["source_file_detail"] = item["file_name"]
                            break
                else:
                    new_items.append(row)
            if new_items:
                kw_by_drpp.setdefault(drpp_number or "TANPA_DRPP", []).extend(new_items)
            kw_items.extend(new_items)
        else:
            parsed = {"status": "needs_manual_review", "method": text_probe["method"], "warnings": text_probe["warnings"]}
        parsed_files.append({
            **item,
            "type": doc_type,
            "parse_status": parsed.get("status"),
            "method": parsed.get("method"),
            "warnings": parsed.get("warnings", []),
            "ocr_trace": parsed.get("ocr_trace", []),
        })
    can_commit = bool(spm_data or drpp_data) and not fatal_errors
    return {
        "ok": can_commit,
        "temp_dir": temp_dir,
        "files": parsed_files,
        "spm": spm_data,
        "drpp": drpp_data,
        "drpps": drpp_list,
        "kw_by_drpp": kw_by_drpp,
        "kw_items": kw_items[:200],
        "warnings": fatal_errors,
    }

def make_json_safe(data):
    """
    Recursively converts non-serializable objects (datetime, date, Decimal, set, tuple, etc.)
    into JSON-safe primitive types.
    """
    import datetime
    import uuid
    from decimal import Decimal
    from pathlib import Path
    from django.db.models import Model
    from django.db.models.query import QuerySet
    from django.core.files.base import File

    if data is None:
        return None
    elif isinstance(data, (str, int, float, bool)):
        return data
    elif isinstance(data, dict):
        return {str(k): make_json_safe(v) for k, v in data.items()}
    elif isinstance(data, (list, tuple, set, QuerySet)):
        return [make_json_safe(v) for v in data]
    elif isinstance(data, (datetime.datetime, datetime.date)):
        return data.isoformat()
    elif isinstance(data, Decimal):
        return str(data)
    elif isinstance(data, (uuid.UUID, Path)):
        return str(data)
    elif isinstance(data, Model):
        if hasattr(data, 'id'):
            return str(data.id)
        return str(data)
    elif isinstance(data, File):
        if hasattr(data, 'name') and data.name:
            return str(data.name)
        return str(data)
    else:
        try:
            # Check if it can be JSON serialized natively
            import json
            json.dumps(data)
            return data
        except (TypeError, ValueError):
            return str(data)
