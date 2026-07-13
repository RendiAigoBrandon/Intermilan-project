import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from apps.core.ocr import extract_document_text, extract_pdf_pages


# ─── Regex SP2D 15-digit (contoh: 260100000013375) ───────────────────────────
# SP2D KPPN biasanya 15 digit numerik murni, atau bisa ada label eksplisit
_RE_SP2D_LABELED = re.compile(
    r"(?:NO\.?\s*SP2D|NOMOR\s+SP2D|SP2D\s+NOMOR)\s*[:\-]?\s*([0-9]{5,20}[A-Z0-9./\-]*)",
    re.IGNORECASE,
)
# Bare 15-digit — hanya dalam konteks halaman SP2D/detail pengeluaran
_RE_SP2D_BARE = re.compile(r"\b(2[0-9]{14})\b")  # dimulai dengan 2, 15 digit

# ─── Regex Invoice/SPP-SPM format: 00074T/019937/2026 ────────────────────────
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
        text = text.replace(".", "").replace(",", ".")
    elif "." in text:
        parts = text.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3 and all(part.isdigit() for part in parts)):
            text = text.replace(".", "")
    else:
        text = text.replace(",", "")
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


def parse_spm_number_from_pages(page_details):
    """Ekstrak No SPM, No SPP, nilai keuangan secara terpisah berdasarkan konteks halaman.

    Aturan:
    - Halaman "SURAT PERINTAH MEMBAYAR" → No SPM + nilai pengeluaran/potongan/total dari halaman ini
    - Halaman "SURAT PERMINTAAN PEMBAYARAN" atau "NOMOR SPP" → No SPP dari halaman ini
    - Jika tidak ada konteks per-halaman, fallback ke regex global

    Returns dict: {no_spm, no_spp, spm_pages, spp_pages,
                   jumlah_pengeluaran, jumlah_potongan, total_pembayaran}
    """
    no_spm = ""
    no_spp = ""
    spm_page_nums = []
    spp_page_nums = []
    jumlah_pengeluaran = Decimal("0")
    jumlah_potongan = Decimal("0")
    total_pembayaran = Decimal("0")

    for page in page_details:
        page_text = page.get("text") or page.get("extracted_text") or ""
        upper = page_text.upper()
        page_num = page.get("page") or page.get("page_number") or "?"

        is_spm_page = "SURAT PERINTAH MEMBAYAR" in upper
        is_spp_page = "SURAT PERMINTAAN PEMBAYARAN" in upper or bool(
            re.search(r"(?:NOMOR|NO\.?)\s+SPP\s*[:\-]", upper)
        )

        if is_spm_page:
            spm_page_nums.append(page_num)
            if not no_spm:
                spm_match = re.search(
                    r"(?:NOMOR\s+SPM|SPM\s+NOMOR|NO\.?\s*SPM)\s*[:\-]?\s*([0-9A-Z./-]+)",
                    upper,
                )
                if spm_match:
                    cand = spm_match.group(1).upper()
                    if is_valid_doc_number(cand):
                        no_spm = cand
                if not no_spm:
                    no_spm = _extract_first_number_from_text(upper)

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
            if not no_spp:
                spp_match = re.search(
                    r"(?:NOMOR\s+SPP|SPP\s+NOMOR|NO\.?\s*SPP)\s*[:\-]?\s*([0-9A-Z./-]+)",
                    upper,
                )
                if spp_match:
                    cand = spp_match.group(1).upper()
                    if is_valid_doc_number(cand):
                        no_spp = cand
                if not no_spp:
                    no_spp = _extract_first_number_from_text(upper)

    return {
        "no_spm": no_spm,
        "no_spp": no_spp,
        "spm_pages": spm_page_nums,
        "spp_pages": spp_page_nums,
        "jumlah_pengeluaran": jumlah_pengeluaran,
        "jumlah_potongan": jumlah_potongan,
        "total_pembayaran": total_pembayaran,
    }


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


def clean_description(value):
    text = normalize_text(value)
    text = re.sub(r"\bNPWP\s*[12]?\b\s*[:;|]?\s*[0-9 .-]*", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bNOP\b.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text


def title_with_acronyms(value):
    text = normalize_text(value).title()
    for acronym in ("SPM", "SPP", "SP2D", "PPNPN", "LS", "GUP", "TUP", "UP"):
        text = re.sub(rf"\b{acronym.title()}\b", acronym, text)
    return text


def extract_uraian(text):
    match = re.search(
        r"URAIAN\s*[:;]?\s*(Pembayaran\b.*?)(?:\s+NOP\b|\s+ALAMAT\b|\s+Semua\b|\s+Padang\b)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    return clean_description(match.group(1)) if match else ""


def extract_jenis_spm(text):
    match = re.search(
        r"Jenis\s+Tagihan\s*[:;]?\s*([A-Za-z0-9 /._-]+?)\s+Dasar\s+Pembayaran\s+([A-Za-z0-9 /._-]+?)\s+DIPA",
        text,
        re.IGNORECASE,
    )
    if match:
        return title_with_acronyms(f"{match.group(1)} {match.group(2)}")
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
    return text.startswith("LS") or "PENGHASILAN PPNPN" in text or "GAJI" in text


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


def parse_spm_pdf(file_path, ocr=False):
    extracted = extract_pdf_text(file_path, ocr=ocr)
    text = "\n".join(extracted["pages"])
    upper = text.upper()

    # ── Ekstraksi nomor + nilai per-halaman (pisahkan SPM dari SPP) ──────────
    page_details = extracted.get("page_details", [])
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
        upper,
        re.DOTALL,
    )
    spp_match_global = re.search(
        r"SURAT\s+PERMINTAAN\s+PEMBAYARAN.*?(?:NOMOR\s+SPP|SPP\s+NOMOR|NO\.?\s*SPP|NOMOR)\s*[:\-]?\s*([0-9A-Z./-]+)",
        upper,
        re.DOTALL,
    )
    # No SP2D — coba labeled dulu, lalu bare 15-digit dalam konteks halaman SP2D/detail
    sp2d_labeled = _RE_SP2D_LABELED.search(upper)
    sp2d_bare = None
    if not sp2d_labeled:
        # Cari di halaman yang terklasifikasi sebagai sp2d atau di teks full
        for page in page_details:
            page_class = page.get("page_classification", "")
            page_text_upper = (page.get("text") or page.get("extracted_text") or "").upper()
            if page_class == "sp2d" or "DETAIL PENGELUARAN" in page_text_upper or "DAFTAR SP2D" in page_text_upper:
                m = _RE_SP2D_BARE.search(page_text_upper)
                if m:
                    sp2d_bare = m
                    break
        if not sp2d_bare:
            sp2d_bare = _RE_SP2D_BARE.search(upper)
    text_sp2d = (
        sp2d_labeled.group(1) if sp2d_labeled
        else (sp2d_bare.group(1) if sp2d_bare else "")
    )
    tanggal_sp2d = extract_sp2d_date(text, text_sp2d)

    # No Invoice/SPP-SPM — labeled dulu, lalu bare
    invoice_labeled = _RE_INVOICE.search(upper)
    invoice_bare = _RE_INVOICE_BARE.search(upper) if not invoice_labeled else None
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
    drpp_match = re.search(r"(?:NOMOR\s+DRPP|DRPP\s+NOMOR|NO\.?\s*DRPP)\s*[:\-]?\s*([0-9A-Z./-]+)", upper)

    # ── Satker ────────────────────────────────────────────────────────
    satker_match = re.search(r"(?:SATKER|KODE\s+SATKER)\s*[:\-]?\s*([0-9]{4,6})", upper)
    satker_c = satker_match.group(1) if satker_match else ""

    if not satker_c:
        m2 = re.search(r"(?:SATUAN KERJA|UNIT KERJA|KANTOR|INSTANSI)[\s\S]{0,100}?([0-9]{6})", upper)
        if m2:
            satker_c = m2.group(1)

    satker_name_ocr = ""
    bps_match = re.search(r"(BADAN PUSAT STATISTIK\s+[A-Z\s.]+)", upper)
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
    tanggal_spm = parse_date(parse_first_match(text, [
        r"(?:TANGGAL\s+SPM|TANGGAL)\s*[:\-]?\s*([0-9]{1,2}[-/][a-zA-Z0-9]+[-/][0-9]{2,4})",
        r"\b([0-9]{4}-[0-9]{2}-[0-9]{2})\b",
    ]))
    jenis_spm = extract_jenis_spm(text)
    cara_pembayaran = extract_cara_pembayaran(text, jenis_spm)
    kppn = parse_first_match(text, [r"KPPN\s*[:\-]?\s*([A-Z0-9 ._-]{2,80})"])
    supplier = parse_first_match(text, [r"(?:SUPPLIER|PENERIMA|NAMA\s+PENERIMA)\s*[:\-]?\s*([A-Z0-9 .,'/-]{3,120})"])
    bank = parse_first_match(text, [r"(?:BANK)\s*[:\-]?\s*([A-Z0-9 .,'/-]{2,80})"])
    rekening = parse_first_match(text, [r"(?:REKENING|NO\.?\s*REK)\s*[:\-]?\s*([0-9 .-]{5,80})"])
    npwp_nik = parse_first_match(text, [r"(?:NPWP|NIK)\s*[:\-]?\s*([0-9 .-]{10,40})"])
    uraian = extract_uraian(text) or parse_first_match(text, [r"(?:URAIAN|KEPERLUAN)\s*[:\-]?\s*(.{10,300})"])
    amount_values = re.findall(r"\b\d{1,3}(?:[.,]\d{3})+(?:,\d{2})?\b", text)
    # Pembebanan/COA 16-segmen: AAAA.BBB.CCC.DDD.XXXXXX
    pembebanan_values = sorted(set(_RE_PEMBEBANAN.findall(upper)))

    # Ekstrak Akun dari pola COA dan teks bebas
    # Ekstrak Akun dari pola COA dan teks bebas
    coa_pattern = re.findall(r"\b\d{4,6}\.[0-9A-Z]{2,4}\.([4589]\d{5})\b", upper)
    dot_pattern = re.findall(r"\.([4589]\d{5})\.", upper)
    standalone = re.findall(r"\b([4589]\d{5})\b", upper)

    akun_pengeluaran = []
    akun_potongan = []

    # ── Potongan Block Parsing ──
    # Look for a "POTONGAN" section and extract COAs near it.
    potongan_blocks = []
    potongan_warnings = []

    for terminator_match in re.finditer(r"(?:JUMLAH\s+POTONGAN|TOTAL\s+POTONGAN)", upper):
        terminator_idx = terminator_match.start()
        # Find all POTONGAN before terminator
        potongan_matches = list(re.finditer(r"\bPOTONGAN\b", upper[:terminator_idx]))
        if potongan_matches:
            # Pick the last one (closest to terminator)
            start_idx = potongan_matches[-1].start()
            block = upper[start_idx:terminator_idx]

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
    pembebanan_utama = extract_pembebanan_value(text, akun_pengeluaran)
    if pembebanan_utama:
        pembebanan_values = [pembebanan_utama] + [item for item in pembebanan_values if item != pembebanan_utama]
    fp_number = extract_fp_number(text)

    # Format untuk backward compatibility
    akun_values = akun_pengeluaran.copy()
    if not akun_values and akun_potongan:
        akun_values = akun_potongan.copy()

    # ── Nilai keuangan ──────────────────────────────────────────────────
    # Prioritas: nilai dari halaman SPM > nilai global dari semua halaman
    jumlah_pengeluaran = jumlah_pengeluaran_per_page or parse_money_from_text(
        upper, ["JUMLAH PENGELUARAN", "NILAI PENGELUARAN", "NILAI SPM"]
    )
    jumlah_potongan = jumlah_potongan_per_page or parse_money_from_text(
        upper, ["JUMLAH POTONGAN", "TOTAL POTONGAN", "POTONGAN"]
    )
    total_pembayaran = total_pembayaran_per_page or parse_money_from_text(
        upper, ["TOTAL PEMBAYARAN", "JUMLAH YANG DIBAYARKAN", "NETO"]
    )
    # total fallback: pengeluaran - potongan, atau dari label generic
    if total_pembayaran <= 0 and jumlah_pengeluaran > 0 and jumlah_potongan > 0:
        total_pembayaran = jumlah_pengeluaran - jumlah_potongan
    if total_pembayaran <= 0:
        total_pembayaran = parse_money_from_text(
            upper, ["JUMLAH PENGELUARAN", "NILAI SPM", "JUMLAH"]
        )

    status = parser_status(extracted)
    if extracted["method"] == "failed":
        status = "failed" if not extracted["warnings"] else "needs_manual_review"

    # ── Resolusi nomor SPM Utama (D_K) ───────────────────────────────────────────────────
    filename_spm = guess_number_from_filename(file_path, "SPM")
    warnings = list(extracted["warnings"])

    # Prioritas Nomor SPM Utama D_K:
    # 1. Halaman SPM (text_spm)
    # 2. filename (sebagai low-confidence fallback)
    if text_spm:
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
            "pembebanan_list": pembebanan_values[:30],
            "akun_pengeluaran": akun_pengeluaran,
            "akun_potongan": akun_potongan,
        },
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
        r"(?P<akun>5\d{5})\s+"
        r"(?P<jumlah>\d{1,3}(?:[.,]\d{3})+(?:,\d{2})?)\s+"
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
                "akun": match.group("akun"),
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


def compact_pembebanan_from_coa(coa, akun=""):
    text = normalize_text(coa).upper()
    akun_match = re.search(r"\.(5\d{5})\.", text)
    output_match = re.search(r"\.(\d{4})([A-Z]{3})\.", text)
    component_match = re.search(r"\b(\d{3})\.(\d{3})\.", text)
    if not (akun_match and output_match and component_match):
        return ""
    account = akun or akun_match.group(1)
    return f"{output_match.group(1)}.{output_match.group(2)}.{component_match.group(1)}.{component_match.group(2)}.{account}"


def extract_drpp_pembebanan_by_amount(text):
    """Ambil mapping (akun, nominal) -> pembebanan ringkas dari lampiran COA DRPP/SPM."""
    mapping = {}
    amount_totals = {}
    compact = normalize_text(text)
    starts = [match.start() for match in re.finditer(r"019937\.\d{3}\.5\d{5}\.", compact)]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(compact)
        block = compact[start:end]
        akun_match = re.search(r"019937\.\d{3}\.(5\d{5})\.", block)
        if not akun_match:
            continue
        akun = akun_match.group(1)
        pembebanan = compact_pembebanan_from_coa(block, akun)
        if not pembebanan:
            continue
        for amount_text in re.findall(r"\b\d{1,3}(?:\.\d{3})+(?:[.,]\d{2})?\b", block):
            amount = parse_decimal(amount_text)
            if amount >= Decimal("100000"):
                mapping.setdefault((akun, amount), pembebanan)
                amount_totals.setdefault((akun, pembebanan), Decimal("0"))
                amount_totals[(akun, pembebanan)] += amount
    for (akun, pembebanan), total in amount_totals.items():
        if total > 0:
            mapping.setdefault((akun, total), pembebanan)
    return mapping


def parse_drpp_pdf(file_path, ocr=False):
    extracted = extract_pdf_text(file_path, ocr=ocr)
    text = "\n".join(extracted["pages"])
    upper = text.upper()
    nomor_match = re.search(r"(?:NOMOR\s+DRPP|DRPP\s+NOMOR|NO\.?\s*DRPP)\s*[:\-]?\s*([0-9A-Z./-]+)", upper)
    spm_match = re.search(r"(?:NOMOR\s+SPM|SPM\s+NOMOR|NO\.?\s*SPM)\s*[:\-]?\s*([0-9A-Z./-]+)", upper)
    kw_numbers = sorted(set(re.findall(r"(?:KW|KUITANSI)\s*[:\-]?\s*([0-9A-Z./-]{3,})", upper)))
    akun_values = sorted(set(re.findall(r"\b(5[0-9]{5})\b", upper)))
    amounts = re.findall(r"\b\d{1,3}(?:[.,]\d{3})+(?:,\d{2})?\b", text)
    items = parse_drpp_items_from_text(text)
    pembebanan_by_amount = extract_drpp_pembebanan_by_amount(text)
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
    if not items:
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
    if not items and amounts:
        for idx, amount in enumerate(amounts[:20], start=1):
            items.append({"no_urut": idx, "no_bukti": "", "tanggal_bukti": "", "penerima": "", "npwp": "", "akun": "", "jumlah": parse_decimal(amount), "keperluan": ""})
    total = sum((item["jumlah"] for item in items), Decimal("0"))
    status = parser_status(extracted)
    return {
        "file_name": os.path.basename(file_path),
        "page_count": extracted["page_count"],
        "method": extracted["method"],
        "best_engine": extracted.get("best_engine", extracted["method"]),
        "status": status,
        "warnings": extracted["warnings"],
        "page_details": extracted.get("page_details", []),
        "confidence": extracted.get("confidence", 0.0),
        "engines_tried": extracted.get("engines_tried", []),
        "native_text_length": extracted.get("native_text_length", 0),
        "tesseract_called": extracted.get("tesseract_called", False),
        "tesseract_text_length": extracted.get("tesseract_text_length", 0),
        "tesseract_reason": extracted.get("tesseract_reason", ""),
        "metadata": {
            "nomor_drpp": nomor_match.group(1) if nomor_match else guess_number_from_filename(file_path, "DRPP"),
            "nomor_spm": spm_match.group(1) if spm_match else guess_number_from_filename(file_path, "SPM"),
            "total": total,
        },
        "items": items,
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
    if "KW" in name or "KUITANSI" in name:
        return "KW"
    if "DRPP" in name:
        return "DRPP"
    if "SPM" in name or "SPP" in name:
        return "SPM"
    if "DRPP" in upper:
        return "DRPP"
    if "KW" in haystack or "KUITANSI" in haystack:
        return "KW"
    if "SPM" in haystack or "SPP" in haystack:
        return "SPM"
    if "LAMPIRAN" in haystack:
        return "LAMPIRAN"
    return "UNKNOWN"


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
            target = Path(temp_dir) / name
            resolved_target = target.resolve()
            if not str(resolved_target).startswith(str(Path(temp_dir).resolve())):
                raise ValueError(f"ZIP tidak aman: {name}")
            if not name.lower().endswith(".pdf"):
                extracted.append({"file_name": name, "path": "", "type": "SKIPPED", "status": "non_pdf"})
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            extracted.append({"file_name": name, "path": str(target), "type": "", "status": "extracted"})
    return temp_dir, extracted


def parse_paket_spm_zip(zip_path, ocr=False):
    temp_dir, files = safe_extract_zip(zip_path)
    parsed_files = []
    spm_data = None
    drpp_data = None
    drpp_list = []
    kw_by_drpp = {}
    kw_items = []
    fatal_errors = []
    for item in files:
        if item["status"] != "extracted":
            parsed_files.append(item)
            continue
        doc_type = classify_document(item["file_name"], "")
        text_probe = {"method": "filename", "warnings": [], "pages": []}
        if doc_type == "UNKNOWN":
            text_probe = extract_pdf_text(item["path"], ocr=False)
            doc_type = classify_document(item["file_name"], "\n".join(text_probe["pages"]))
        if doc_type == "SPM":
            parsed = parse_spm_pdf(item["path"], ocr=ocr)
            spm_data = spm_data or parsed
        elif doc_type == "DRPP":
            parsed = parse_drpp_pdf(item["path"], ocr=ocr)
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
            page_count = pdf_page_count(item["path"])
            if ocr and page_count and page_count > 5:
                parsed = parse_kw_pdf_fast(item["path"], ocr=True)
                parsed["warnings"].append(f"File KW {page_count} halaman; OCR lite dibatasi untuk preview cepat.")
            else:
                parsed = parse_drpp_pdf(item["path"], ocr=ocr)
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
        parsed_files.append({**item, "type": doc_type, "parse_status": parsed.get("status"), "method": parsed.get("method"), "warnings": parsed.get("warnings", [])})
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
