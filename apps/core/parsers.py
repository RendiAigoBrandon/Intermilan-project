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
    for label in labels:
        pattern = rf"{label}[^0-9]*(\d[\d.,]*)"
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
    nomor_match = re.search(r"(?:NOMOR\s+SPM|SPM\s+NOMOR|NO\.?\s*SPM)\s*[:\-]?\s*([0-9A-Z./-]+)", upper)
    drpp_match = re.search(r"(?:NOMOR\s+DRPP|DRPP\s+NOMOR|NO\.?\s*DRPP)\s*[:\-]?\s*([0-9A-Z./-]+)", upper)
    satker_match = re.search(r"(?:SATKER|KODE\s+SATKER)\s*[:\-]?\s*([0-9]{4,6})", upper)
    tanggal_spm = parse_date(parse_first_match(text, [r"(?:TANGGAL\s+SPM|TANGGAL)\s*[:\-]?\s*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})", r"\b([0-9]{4}-[0-9]{2}-[0-9]{2})\b"]))
    jenis_spm = parse_first_match(text, [r"(?:JENIS\s+SPM|JENIS\s+SPP)\s*[:\-]?\s*([A-Z0-9 /._-]{2,80})", r"\b(UP|GUP|TUP|PTUP|LS(?:\s+[A-Z ]{2,40})?)\b"])
    kppn = parse_first_match(text, [r"KPPN\s*[:\-]?\s*([A-Z0-9 ._-]{2,80})"])
    supplier = parse_first_match(text, [r"(?:SUPPLIER|PENERIMA|NAMA\s+PENERIMA)\s*[:\-]?\s*([A-Z0-9 .,'/-]{3,120})"])
    bank = parse_first_match(text, [r"(?:BANK)\s*[:\-]?\s*([A-Z0-9 .,'/-]{2,80})"])
    rekening = parse_first_match(text, [r"(?:REKENING|NO\.?\s*REK)\s*[:\-]?\s*([0-9 .-]{5,80})"])
    npwp_nik = parse_first_match(text, [r"(?:NPWP|NIK)\s*[:\-]?\s*([0-9 .-]{10,40})"])
    uraian = parse_first_match(text, [r"(?:URAIAN|KEPERLUAN)\s*[:\-]?\s*(.{10,300})"])
    akun_values = sorted(set(re.findall(r"\b(5[0-9]{5})\b", upper)))
    amount_values = re.findall(r"\b\d{1,3}(?:[.,]\d{3})+(?:,\d{2})?\b", text)
    total = parse_money_from_text(upper, ["JUMLAH PENGELUARAN", "TOTAL PEMBAYARAN", "NILAI SPM", "JUMLAH"])
    jumlah_pengeluaran = parse_money_from_text(upper, ["JUMLAH PENGELUARAN"])
    jumlah_potongan = parse_money_from_text(upper, ["JUMLAH POTONGAN", "POTONGAN"])
    status = parser_status(extracted)
    if extracted["method"] == "failed":
        status = "failed" if not extracted["warnings"] else "needs_manual_review"
    filename_spm = guess_number_from_filename(file_path, "SPM")
    text_spm = nomor_match.group(1) if nomor_match else ""
    warnings = list(extracted["warnings"])
    number_decision = resolve_spm_number(filename_spm, text_spm, extracted.get("confidence", 0.0), extracted.get("method", ""))
    if number_decision["warning"]:
        warnings.append(number_decision["warning"])
    if total <= 0:
        warnings.append("Parser gagal mengambil nilai SPM dari dokumen.")
    return {
        "file_name": os.path.basename(file_path),
        "page_count": extracted["page_count"],
        "method": extracted["method"],
        "best_engine": extracted.get("best_engine", extracted["method"]),
        "status": status,
        "warnings": warnings,
        "page_details": extracted.get("page_details", []),
        "confidence": extracted.get("confidence", 0.0),
        "engines_tried": extracted.get("engines_tried", []),
        "native_text_length": extracted.get("native_text_length", 0),
        "tesseract_called": extracted.get("tesseract_called", False),
        "tesseract_text_length": extracted.get("tesseract_text_length", 0),
        "tesseract_reason": extracted.get("tesseract_reason", ""),
        "metadata": {
            "nomor_spm": number_decision["final"],
            "nomor_spm_final": number_decision["final"],
            "nomor_spm_final_source": number_decision["source"],
            "nomor_spm_ocr": text_spm,
            "nomor_spm_filename": filename_spm,
            "nomor_spm_conflict": number_decision["conflict"],
            "nomor_spm_review_status": number_decision["review_status"],
            "nomor_spm_reason": number_decision["reason"],
            "nomor_drpp": drpp_match.group(1) if drpp_match else "",
            "satker_code": satker_match.group(1) if satker_match else "",
            "tanggal_spm": tanggal_spm,
            "jenis_spm": jenis_spm,
            "kppn": kppn,
            "supplier": supplier,
            "bank": bank,
            "rekening": rekening,
            "npwp_nik": npwp_nik,
            "uraian": uraian,
            "total_pembayaran": total,
            "jumlah_pengeluaran": jumlah_pengeluaran,
            "jumlah_potongan": jumlah_potongan,
        },
        "akun_rows": [{"akun": akun, "uraian": "", "nilai": ""} for akun in akun_values[:50]],
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
    fitz = optional_import("fitz")
    if not fitz:
        return 0
    try:
        doc = fitz.open(file_path)
        return doc.page_count
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
                parsed = parse_kw_filename_stub(item["path"], f"File KW {page_count} halaman; OCR otomatis dilewati agar preview tidak macet.")
            else:
                parsed = parse_drpp_pdf(item["path"], ocr=ocr)
            existing_keys = {normalized_bukti_key(row.get("no_bukti", "")) for row in kw_items if row.get("no_bukti")}
            drpp_number = parsed.get("metadata", {}).get("nomor_drpp", "")
            new_items = [
                {**row, "no_drpp": drpp_number, "source_file": item["file_name"]}
                for row in parsed.get("items", [])
                if normalized_bukti_key(row.get("no_bukti", "")) not in existing_keys
            ]
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
