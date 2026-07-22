"""Parser cepat untuk upload maksimal dua DRPP beserta kuitansinya.

Parser ini sengaja tidak memakai classifier Paket SPM sebagai keputusan akhir.
Halaman diindeks pada resolusi rendah, dideduplikasi, lalu OCR resolusi tinggi
hanya dijalankan pada kandidat yang diperlukan.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db.models import Q

from apps.core.ocr import (
    configure_tesseract,
    extract_paddleocr,
    parse_bool_env,
    preprocess_image,
    tesseract_page_text_best_rotation,
)
from apps.core.parsers import parse_drpp_pdf, parse_spm_pdf


PARSER_VERSION = "drpp-batch-v4"
MAX_DRPP = 2
TOO_MANY_DRPP_MESSAGE = (
    "Unggahan memuat lebih dari dua DRPP. Pisahkan dokumen menjadi beberapa "
    "unggahan agar proses pemindaian tetap cepat."
)

PAGE_TYPES = (
    "DRPP_SUMMARY",
    "DRPP_COA",
    "SPM",
    "SPP",
    "KUITANSI",
    "SURAT_PERNYATAAN_BAYAR",
    "MEMO_PENCAIRAN",
    "INVOICE",
    "FAKTUR_PAJAK",
    "SSP",
    "BUKTI_TRANSFER",
    "DAFTAR_NOMINATIF",
    "RINCIAN_BIAYA",
    "SUPPORT_DOCUMENT",
    "UNKNOWN",
)

KW_PAGE_TYPES = {
    "KUITANSI",
    "SURAT_PERNYATAAN_BAYAR",
    "MEMO_PENCAIRAN",
    "INVOICE",
    "FAKTUR_PAJAK",
    "SSP",
    "BUKTI_TRANSFER",
    "DAFTAR_NOMINATIF",
    "RINCIAN_BIAYA",
}


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_drpp(value):
    match = re.search(r"\d{1,6}", str(value or ""))
    return match.group(0).zfill(5) if match else ""


def _drpp_hint(name):
    match = re.search(r"\bDRPP\s*(?:NO(?:MOR)?\.?\s*)?[-_ ]*(\d{1,6})\b", str(name), re.I)
    return _normalize_drpp(match.group(1)) if match else ""


def _kw_hint(name):
    stem = Path(str(name)).stem.upper()
    full = re.search(r"(\d{3,6}/KW/\d{5,9}/20\d{2})", stem)
    if full:
        return full.group(1)
    short = re.search(r"\b(?:KW|KUITANSI)\s*[-_ ]*(\d{1,6})\b", stem)
    return short.group(1).zfill(5) if short else ""


def _type_hint(name):
    upper = Path(str(name)).stem.upper()
    if "DRPP" in upper and re.search(r"\b(?:KW|KUITANSI)\b", upper):
        return "KUITANSI"
    if "DRPP" in upper:
        return "DRPP_SUMMARY"
    if re.search(r"\bSPM\b", upper):
        return "SPM"
    if re.search(r"\b(?:KW|KUITANSI)\b", upper):
        return "KUITANSI"
    return "UNKNOWN"


def _safe_extract(archive, target_dir):
    root = os.path.realpath(target_dir)
    max_files = int(getattr(settings, "MAX_UPLOAD_FILES", 1000))
    max_bytes = int(getattr(settings, "MAX_FOLDER_UPLOAD_SIZE_MB", 2048)) * 1024 * 1024
    pdf_members = [member for member in archive.infolist() if not member.is_dir() and member.filename.lower().endswith(".pdf")]
    if len(pdf_members) > max_files:
        raise ValueError(f"Jumlah file melebihi batas {max_files} file.")
    if sum(member.file_size for member in pdf_members) > max_bytes:
        raise ValueError("Ukuran hasil ekstraksi ZIP melebihi batas upload.")
    for member in archive.infolist():
        if member.is_dir():
            continue
        if member.filename.lower().endswith(".zip"):
            raise ValueError("ZIP bertingkat tidak didukung.")
        if not member.filename.lower().endswith(".pdf"):
            continue
        destination = os.path.realpath(os.path.join(root, member.filename.replace("/", os.sep)))
        if os.path.commonpath([root, destination]) != root:
            raise ValueError("ZIP memuat path file yang tidak aman.")
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with archive.open(member) as source, open(destination, "wb") as output:
            shutil.copyfileobj(source, output)


def _page_count(path):
    try:
        import fitz

        with fitz.open(path) as document:
            return document.page_count
    except Exception:
        return 0


def build_manifest(file_path):
    """Buat manifest PDF. Field berawalan underscore hanya untuk proses internal."""
    source = os.path.abspath(file_path)
    temp_dir = ""
    if os.path.isdir(source):
        paths = sorted(str(path) for path in Path(source).rglob("*.pdf"))
    elif source.lower().endswith(".zip"):
        tmp_root = os.path.join(settings.MEDIA_ROOT, "tmp")
        os.makedirs(tmp_root, exist_ok=True)
        temp_dir = tempfile.mkdtemp(prefix="drpp_batch_", dir=tmp_root)
        try:
            with zipfile.ZipFile(source) as archive:
                _safe_extract(archive, temp_dir)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        paths = sorted(str(path) for path in Path(temp_dir).rglob("*.pdf"))
    elif source.lower().endswith(".pdf"):
        paths = [source]
    else:
        raise ValueError("Format file tidak didukung. Gunakan ZIP, folder, atau PDF.")

    if not paths:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise ValueError("Tidak ada PDF yang dapat diproses dalam unggahan.")

    manifest = []
    for path in paths:
        name = os.path.basename(path)
        manifest.append(
            {
                "file_name": name,
                "sha256": _sha256(path),
                "page_count": _page_count(path),
                "drpp_hint": _drpp_hint(name),
                "kw_hint": _kw_hint(name),
                "type_hint": _type_hint(name),
                "_path": path,
                "_temp_dir": temp_dir,
            }
        )
    return manifest


def group_files_by_drpp(manifest):
    groups = defaultdict(lambda: {"drpp_files": [], "kw_files": [], "spm_files": []})
    for item in manifest:
        number = item.get("drpp_hint") or "TANPA_DRPP"
        if item.get("type_hint") == "DRPP_SUMMARY":
            groups[number]["drpp_files"].append(item)
        elif item.get("type_hint") == "SPM":
            groups[number]["spm_files"].append(item)
        else:
            groups[number]["kw_files"].append(item)
    return dict(groups)


def _render_page(page, dpi):
    try:
        import fitz
        from PIL import Image, ImageOps

        with fitz.open(page["_path"]) as document:
            source_page = document[page["page_number"] - 1]
            pixmap = source_page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
            image = Image.open(io.BytesIO(pixmap.tobytes("png")))
            return ImageOps.exif_transpose(image).convert("L")
    except Exception:
        return None


def _difference_hash(image, size=9):
    if image is None:
        return ""
    pixels = list(image.resize((size, size - 1)).getdata())
    bits = []
    width = size
    for y in range(size - 1):
        offset = y * width
        bits.extend(pixels[offset + x] > pixels[offset + x + 1] for x in range(width - 1))
    return f"{sum(int(bit) << index for index, bit in enumerate(bits)):016x}"


def _native_page_text(path, page_number):
    try:
        import fitz

        with fitz.open(path) as document:
            return document[page_number - 1].get_text("text") or ""
    except Exception:
        return ""


def build_page_index(manifest, dpi=48):
    pages = []
    for file_item in manifest:
        try:
            import fitz
            from PIL import Image, ImageOps

            document = fitz.open(file_item["_path"])
        except Exception:
            document = None
        try:
            for page_number in range(1, file_item.get("page_count", 0) + 1):
                page = {
                    "file_name": file_item["file_name"],
                    "file_sha256": file_item["sha256"],
                    "page_number": page_number,
                    "drpp_hint": file_item.get("drpp_hint", ""),
                    "kw_hint": file_item.get("kw_hint", ""),
                    "type_hint": file_item.get("type_hint", "UNKNOWN"),
                    "_path": file_item["_path"],
                }
                image = None
                if document is not None:
                    source_page = document[page_number - 1]
                    page["native_text"] = source_page.get_text("text")
                    pixmap = source_page.get_pixmap(
                        matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False
                    )
                    image = ImageOps.exif_transpose(
                        Image.open(io.BytesIO(pixmap.tobytes("png")))
                    ).convert("L")
                else:
                    page["native_text"] = _native_page_text(file_item["_path"], page_number)
                    image = _render_page(page, dpi)
                page["page_hash"] = _difference_hash(image)
                page["_image"] = image
                pages.append(page)
        finally:
            if document is not None:
                document.close()
    return pages


def _hash_distance(left, right):
    if not left or not right:
        return 65
    return (int(left, 16) ^ int(right, 16)).bit_count()


def deduplicate_pages(page_index, max_distance=3):
    representatives = []
    for page in page_index:
        protected = page.get("force_probe") or (
            page.get("type_hint") in {"DRPP_SUMMARY", "SPM"} and page.get("page_number", 0) <= 4
        )
        duplicate = None if protected else next(
            (
                candidate
                for candidate in representatives
                if _hash_distance(page.get("page_hash"), candidate.get("page_hash")) <= max_distance
            ),
            None,
        )
        page["duplicate_of"] = (
            {"file_name": duplicate["file_name"], "page_number": duplicate["page_number"]}
            if duplicate
            else None
        )
        page["_representative"] = duplicate
        page["is_representative"] = duplicate is None
        if duplicate is None:
            representatives.append(page)
    return page_index


def _cache_path(page, engine):
    raw = "|".join(
        (PARSER_VERSION, page.get("file_sha256", ""), page.get("page_hash", ""), engine)
    )
    key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    cache_dir = os.path.join(settings.MEDIA_ROOT, "ocr_cache", "drpp_batch")
    return os.path.join(cache_dir, f"{key}.json")


def _load_page_cache(page, engine):
    path = _cache_path(page, engine)
    try:
        with open(path, encoding="utf-8") as handle:
            cached = json.load(handle)
        if str(cached.get("text") or "").strip() or cached.get("cache_empty"):
            cached["cache_hit"] = True
            return cached
    except (OSError, ValueError, TypeError):
        return None
    return None


def _save_page_cache(page, engine, result):
    if not str(result.get("text") or "").strip() and not result.get("cache_empty"):
        return
    path = _cache_path(page, engine)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, default=str)
    except OSError:
        pass


def _looks_like_form(image):
    """Deteksi murah halaman formulir/tabel tanpa OCR seluruh halaman."""
    if image is None:
        return False
    sample = image.resize((180, max(1, int(180 * image.height / max(image.width, 1)))))
    pixels = sample.load()
    width, height = sample.size
    horizontal = sum(
        1
        for y in range(height)
        if sum(pixels[x, y] < 110 for x in range(width)) >= width * 0.32
    )
    vertical = sum(
        1
        for x in range(width)
        if sum(pixels[x, y] < 110 for y in range(height)) >= height * 0.18
    )
    return horizontal >= 3 or vertical >= 3


def _candidate_for_probe(page):
    if page.get("force_probe"):
        return True
    if page.get("native_text", "").strip():
        return True
    if page.get("type_hint") in {"DRPP_SUMMARY", "SPM"}:
        return page["page_number"] <= 4
    return page.get("primary_for_drpp", True) and page["page_number"] <= 2


def _probe_page_text(page):
    engine = "tesseract-probe-60-v2"
    cached = _load_page_cache(page, engine)
    if cached:
        cached["cache_hit"] = True
        return cached
    try:
        import pytesseract
    except Exception:
        return {"text": "", "cache_hit": False, "warnings": ["pytesseract tidak tersedia."]}
    if not configure_tesseract(pytesseract):
        return {"text": "", "cache_hit": False, "warnings": ["Tesseract tidak tersedia."]}
    image = _render_page(page, 96) or page.get("_image")
    if image is None:
        return {"text": "", "cache_hit": False, "warnings": ["Halaman gagal dirender."]}
    width = max(1, int(image.width * 0.625))
    image = image.resize((width, max(1, int(image.height * width / max(image.width, 1)))))
    try:
        text = pytesseract.image_to_string(image, lang="ind+eng", config="--psm 11")
        result = {
            "text": text,
            "cache_hit": False,
            "cache_empty": not bool(text.strip()),
            "warnings": [],
        }
    except Exception as exc:
        result = {"text": "", "cache_hit": False, "warnings": [f"Probe OCR gagal: {exc}"]}
    _save_page_cache(page, engine, result)
    return result


def discover_embedded_drpp_pages(page_index, ocr=True):
    """Cari DRPP embedded hanya pada satu bundel kuitansi per DRPP yang belum punya PDF DRPP."""
    explicit = {
        page.get("drpp_hint")
        for page in page_index
        if page.get("type_hint") == "DRPP_SUMMARY" and page.get("drpp_hint")
    }
    numbers = {page.get("drpp_hint") for page in page_index if page.get("drpp_hint")}
    for page in page_index:
        if page.get("type_hint") == "KUITANSI":
            page["primary_for_drpp"] = False
    if not ocr:
        return page_index

    for number in sorted(numbers - explicit):
        file_name = next(
            (
                page["file_name"]
                for page in page_index
                if page.get("drpp_hint") == number and page.get("type_hint") == "KUITANSI"
            ),
            "",
        )
        if not file_name:
            continue
        found_summary = False
        summary_page = 0
        for page in page_index:
            if page["file_name"] != file_name:
                continue
            started = time.monotonic()
            probe = _probe_page_text(page)
            page["probe_duration"] = time.monotonic() - started
            page["probe_ocr_called"] = not probe.get("cache_hit", False)
            page["probe_cache_hit"] = bool(probe.get("cache_hit"))
            document_type = _classification(probe.get("text", ""))[0]
            if document_type == "DRPP_SUMMARY":
                page["force_probe"] = True
                found_summary = True
                summary_page = page["page_number"]
            elif found_summary and document_type == "DRPP_COA":
                page["force_probe"] = True
                break
            elif found_summary and page["page_number"] > summary_page + 2:
                break

    if not numbers:
        # Bundel bernama SPM ... KW ... menaruh SPM/DRPP di blok awal.
        # Batasi probe agar kuitansi puluhan halaman tidak dibaca seluruhnya.
        bundle_file = next(
            (
                page["file_name"]
                for page in page_index
                if re.search(r"\bSPM\b", Path(page["file_name"]).stem.upper())
            ),
            "",
        )
        for page in page_index:
            if page["file_name"] != bundle_file or page["page_number"] > 12:
                continue
            started = time.monotonic()
            probe = _probe_page_text(page)
            page["probe_duration"] = time.monotonic() - started
            page["probe_ocr_called"] = not probe.get("cache_hit", False)
            page["probe_cache_hit"] = bool(probe.get("cache_hit"))
            if _classification(probe.get("text", ""))[0] != "DRPP_SUMMARY":
                continue
            for candidate in page_index:
                if (
                    candidate["file_name"] == bundle_file
                    and page["page_number"] <= candidate["page_number"] <= page["page_number"] + 2
                ):
                    candidate["force_probe"] = True
            break
    return page_index


def _ocr_page(page):
    cached = _load_page_cache(page, "tesseract-ind+eng")
    if cached:
        return cached
    try:
        import pytesseract
    except Exception:
        return {"text": "", "confidence": 0, "words": [], "engine": "tesseract", "warnings": ["pytesseract tidak tersedia."]}
    if not configure_tesseract(pytesseract):
        return {"text": "", "confidence": 0, "words": [], "engine": "tesseract", "warnings": ["Tesseract tidak tersedia."]}

    image = _render_page(page, 220) or page.get("_image")
    if image is None:
        return {"text": "", "confidence": 0, "words": [], "engine": "tesseract", "warnings": ["Halaman gagal dirender."]}
    processed = preprocess_image(image)
    text, confidence, warnings, words, rotation, tried, _score = tesseract_page_text_best_rotation(
        pytesseract, processed
    )
    result = {
        "text": text,
        "confidence": confidence,
        "words": words,
        "engine": "tesseract",
        "warnings": warnings,
        "rotation": rotation,
        "tried_rotations": tried,
        "cache_hit": False,
    }
    _save_page_cache(page, "tesseract-ind+eng", result)

    if (len(text.strip()) < 40 or confidence < 35) and parse_bool_env("OCR_ENABLE_PADDLEOCR", False):
        paddle_cache = _load_page_cache(page, "paddleocr")
        if paddle_cache:
            paddle = paddle_cache
        else:
            engine_result = extract_paddleocr(page["_path"], images=[image], page_indices_to_ocr={0})
            paddle_page = engine_result.pages[0] if engine_result.pages else None
            paddle = {
                "text": getattr(paddle_page, "extracted_text", ""),
                "confidence": getattr(paddle_page, "confidence", 0),
                "words": getattr(paddle_page, "tsv_words", []),
                "engine": "paddleocr",
                "warnings": engine_result.warnings,
                "rotation": 0,
                "tried_rotations": [],
                "cache_hit": False,
            }
            _save_page_cache(page, "paddleocr", paddle)
        if len(paddle.get("text", "")) > len(result.get("text", "")):
            result = paddle
    return result


def _classification(text):
    upper = " ".join(str(text or "").upper().split())
    rules = [
        ("DRPP_COA", ("DETAIL COA", "LAMPIRAN DAFTAR RINCIAN")),
        ("DRPP_SUMMARY", ("DAFTAR RINCIAN PERMINTAAN PEMBAYARAN", "DAFTAR RINCIAN PERINTAAN PEMBAYARAN", "NOMOR DRPP")),
        ("SPM", ("SURAT PERINTAH MEMBAYAR",)),
        ("SPP", ("SURAT PERMINTAAN PEMBAYARAN",)),
        ("SURAT_PERNYATAAN_BAYAR", ("SURAT PERNYATAAN BAYAR",)),
        ("MEMO_PENCAIRAN", ("MEMO PENCAIRAN",)),
        ("FAKTUR_PAJAK", ("FAKTUR PAJAK",)),
        ("SSP", ("SURAT SETORAN PAJAK",)),
        ("BUKTI_TRANSFER", ("BUKTI TRANSFER", "BUKTI PEMBAYARAN")),
        ("DAFTAR_NOMINATIF", ("DAFTAR NOMINATIF",)),
        ("RINCIAN_BIAYA", ("RINCIAN BIAYA",)),
        ("INVOICE", ("INVOICE",)),
        ("KUITANSI", ("KUITANSI", "TERBILANG")),
    ]
    for document_type, anchors in rules:
        evidence = [anchor for anchor in anchors if anchor in upper]
        if evidence:
            confidence = min(100, 65 + 15 * len(evidence))
            return document_type, confidence, evidence
    if upper:
        return "SUPPORT_DOCUMENT", 45, ["teks terbaca tanpa anchor transaksi"]
    return "UNKNOWN", 0, []


def classify_candidate_pages(page_index, ocr=True):
    for page in page_index:
        page.update({"document_type": "UNKNOWN", "confidence": 0, "evidence": [], "ocr_called": False})
        if not page.get("is_representative"):
            continue
        text = page.get("native_text", "")
        ocr_result = None
        if not text.strip() and ocr and _candidate_for_probe(page):
            ocr_started = time.monotonic()
            ocr_result = _ocr_page(page)
            page["ocr_duration"] = time.monotonic() - ocr_started
            text = ocr_result.get("text", "")
            page["ocr_called"] = not ocr_result.get("cache_hit", False)
            page["cache_hit"] = bool(ocr_result.get("cache_hit"))
            page["engine"] = ocr_result.get("engine", "tesseract")
            page["tsv_words"] = ocr_result.get("words", [])
            page["rotation"] = ocr_result.get("rotation", 0)
            page["ocr_warnings"] = ocr_result.get("warnings", [])
        page["text"] = text
        page["document_type"], page["confidence"], page["evidence"] = _classification(text)
        detected = _drpp_number_from_text(text)
        if detected:
            page["drpp_detected"] = detected
    for page in page_index:
        text = str(page.get("text") or "")
        if (
            page.get("is_representative")
            and page.get("type_hint") == "DRPP_SUMMARY"
            and page.get("document_type") in {"UNKNOWN", "SUPPORT_DOCUMENT"}
            and "BUKTI PENGELUARAN" in text.upper()
            and re.search(r"\d{3,6}/KW/", text, re.I)
        ):
            page["document_type"] = "DRPP_SUMMARY"
            page["confidence"] = 95
            page["evidence"] = ["lanjutan tabel bukti pengeluaran"]
            page["drpp_detected"] = page.get("drpp_hint", "")
    for page in page_index:
        representative = page.get("_representative")
        if not representative:
            continue
        for field in ("text", "document_type", "confidence", "evidence", "engine", "tsv_words", "rotation", "drpp_detected"):
            if field in representative:
                page[field] = representative[field]
    return page_index


def _drpp_number_from_text(text):
    match = re.search(
        r"(?:NOMOR\s+DRPP|DRPP\s+NOMOR|NO\.?\s*DRPP)\s*[:\-]?\s*(\d{1,6})(?:/DRPP)?",
        str(text or ""),
        re.I,
    )
    if not match:
        match = re.search(r"\b(\d{1,6})/DRPP/", str(text or ""), re.I)
    return _normalize_drpp(match.group(1)) if match else ""


def _extracted_from_pages(pages):
    details = []
    for page in pages:
        text = page.get("text") or page.get("native_text") or ""
        details.append(
            {
                "page_number": page.get("page_number", 1),
                "text": text,
                "extracted_text": text,
                "engine": page.get("engine", "text"),
                "method": page.get("engine", "text"),
                "confidence": page.get("confidence", 0),
                "tsv_words": [dict(word) for word in page.get("tsv_words", [])],
                "rotation": page.get("rotation", 0),
                "warnings": page.get("ocr_warnings", []),
            }
        )
    combined_text = "\n".join(item["text"] for item in details)
    return {
        "status": "parsed_ocr" if combined_text.strip() else "needs_manual_review",
        "pages": [item["text"] for item in details],
        "combined_text": combined_text,
        "page_details": details,
        "page_count": len(details),
        "method": "drpp_batch",
        "best_engine": next((page.get("engine") for page in pages if page.get("engine")), "text"),
        "warnings": [],
        "confidence": max((page.get("confidence", 0) for page in pages), default=0),
        "engines_tried": sorted({page.get("engine", "text") for page in pages}),
        "native_text_length": sum(len(page.get("native_text", "")) for page in pages),
        "tesseract_called": any(page.get("engine") == "tesseract" for page in pages),
        "tesseract_text_length": sum(len(page.get("text", "")) for page in pages if page.get("engine") == "tesseract"),
        "tesseract_reason": "OCR selektif per halaman kandidat.",
    }


def parse_drpp_summary(number, pages):
    summaries = [page for page in pages if page.get("document_type") == "DRPP_SUMMARY"]
    if not summaries:
        return None
    summary = max(summaries, key=lambda page: len(page.get("text", "")))
    coa_pages = [page for page in pages if page.get("document_type") == "DRPP_COA"]
    selected = sorted(summaries, key=lambda page: page.get("page_number", 0)) + coa_pages
    extracted = _extracted_from_pages(selected)
    expected_kw = {
        str(page.get("kw_hint") or "").split("/", 1)[0].zfill(5)
        for page in pages
        if page.get("kw_hint")
    }
    valid_kw = set()
    malformed_words = []
    for detail in extracted["page_details"]:
        for word in detail.get("tsv_words", []):
            text = str(word.get("text") or "")
            match = re.search(r"(\d{3,6})/KW/(\d{5,9})/(20\d{2})", text, re.I)
            if match:
                valid_kw.add(match.group(1).zfill(5))
            elif "/KW" in text.upper():
                malformed_words.append((detail, word))
    missing_kw = expected_kw - valid_kw
    if len(missing_kw) == 1 and len(malformed_words) == 1:
        recovered = next(iter(missing_kw))
        detail, word = malformed_words[0]
        original = str(word.get("text") or "")
        repaired = re.sub(
            r"^[^/]+/KW[^0-9]*(\d{5,9})/(20\d{2}).*$",
            rf"{recovered}/KW/\1/\2",
            original,
            flags=re.I,
        )
        if repaired != original:
            word["text"] = repaired
            detail["text"] = detail["text"].replace(original, repaired)
            detail["extracted_text"] = detail["text"]
            extracted["pages"] = [item["text"] for item in extracted["page_details"]]
            extracted["combined_text"] = "\n".join(extracted["pages"])
    parsed = parse_drpp_pdf(summary["_path"], ocr=False, extracted=extracted)
    remaining_kw = set(expected_kw)
    unresolved_items = []
    for item in parsed.get("items", []):
        match = re.search(r"(\d{3,6})/KW/(\d{5,9})/(20\d{2})", str(item.get("no_bukti") or ""), re.I)
        short = match.group(1).zfill(5) if match else ""
        if short in remaining_kw:
            remaining_kw.remove(short)
        else:
            unresolved_items.append((item, match, short))
    if len(unresolved_items) == len(remaining_kw):
        for item, match, short in unresolved_items:
            if not match or not remaining_kw:
                continue
            candidate = min(
                remaining_kw,
                key=lambda value: sum(left != right for left, right in zip(short, value)),
            )
            distance = sum(left != right for left, right in zip(short, candidate))
            if distance <= 1:
                item["no_bukti_ocr"] = item.get("no_bukti")
                item["no_bukti"] = f"{candidate}/KW/{match.group(2)}/{match.group(3)}"
                item["kw_reconciled_from_filename"] = True
                remaining_kw.remove(candidate)
    parsed.setdefault("metadata", {})["nomor_drpp"] = number or parsed["metadata"].get("nomor_drpp", "")
    header = _parse_drpp_header(summary.get("text") or "")
    for key, value in header.items():
        if value not in (None, "", Decimal("0")) and not parsed["metadata"].get(key):
            parsed["metadata"][key] = value
    parsed["file_name"] = summary["file_name"]
    parsed["source_pages"] = [
        {"file_name": page["file_name"], "page_number": page["page_number"], "page_hash": page.get("page_hash", "")}
        for page in selected
    ]
    items = parsed.get("items", [])
    printed_total = _money(parsed["metadata"].get("printed_total"))
    parsed_total = sum((_money(item.get("jumlah")) for item in items), Decimal("0"))
    if len(items) == 1 and printed_total > 0 and parsed_total != printed_total:
        items[0]["jumlah_ocr"] = items[0].get("jumlah")
        items[0]["jumlah"] = printed_total
        items[0]["amount_reconciled_from_total"] = True
        parsed["metadata"]["total"] = printed_total
        parsed["metadata"]["total_valid"] = True
    elif len(items) > 1 and printed_total > 0 and parsed_total != printed_total:
        remainder = printed_total - sum(
            (_money(item.get("jumlah")) for item in items[:-1]), Decimal("0")
        )
        if remainder > 0:
            items[-1]["jumlah_ocr"] = items[-1].get("jumlah")
            items[-1]["jumlah"] = remainder
            items[-1]["amount_reconciled_from_total"] = True
            parsed["metadata"]["total"] = printed_total
            parsed["metadata"]["total_valid"] = True
    for item in parsed.get("items", []):
        item["no_drpp"] = parsed["metadata"]["nomor_drpp"]
    return parsed


def _parse_drpp_header(text):
    upper = " ".join(str(text or "").upper().split())

    def value(pattern):
        match = re.search(pattern, upper, re.I)
        return match.group(1).strip(" .,:;|-") if match else ""

    tahun = value(r"(?:TAHUN\s+ANGGARAN|TAHUN)\s*[:\-]?\s*(20\d{2})")
    bulan = value(r"\bBULAN\s*[:\-]?\s*([A-Z]+)")
    total = value(r"(?:TOTAL\s+DRPP|JUMLAH\s+(?:SPP\s+INI|LAMPIRAN))\D{0,20}(\d{1,3}(?:[.,]\d{3})+)")
    pagu = value(r"PAGU\s+(?:OUTPUT|RO)\D{0,20}(\d{1,3}(?:[.,]\d{3})+)")
    return {
        "nomor_drpp": _drpp_number_from_text(upper),
        "tanggal_drpp": value(r"TANGGAL\s+DRPP\s*[:\-]?\s*([0-3]?\d[\-/ ][A-Z0-9]+[\-/ ]20\d{2})"),
        "satker_code": value(r"(?:KODE\s+SATKER|SATKER)\s*[:\-]?\s*(\d{4,8})"),
        "kode_kegiatan": value(r"(?:KODE\s+)?KEGIATAN\s*[:\-]?\s*(\d{4})"),
        "kode_output": value(r"(?:KODE\s+)?OUTPUT\s*[:\-]?\s*([A-Z0-9.]{3,20})"),
        "tahun_anggaran": int(tahun) if tahun else None,
        "tahun": int(tahun) if tahun else None,
        "jenis_spp": value(r"JENIS\s+SPP\s*[:\-]?\s*([A-Z/ ]{2,20})"),
        "bulan": bulan,
        "pagu_output": _money(pagu),
        "nomor_register": value(r"(?:NOMOR|NO\.?)\s+REGISTER\s*[:\-]?\s*([A-Z0-9./-]+)"),
        "total_drpp": _money(total),
    }


def parse_drpp_coa(pages, activity=""):
    rows = []
    pattern = re.compile(r"\b(\d{4})[.\s]+([A-Z]{3})[.\s]+(\d{3})[.\s]+(\d{3})[.\s]+(5\d{5})\b", re.I)
    full_pattern = re.compile(
        r"\b(5\d{5})\b.{0,120}?\b(\d{4})\s*([A-Z]{3})\b.{0,180}?"
        r"\b(\d{3})[.\s]+(\d{3})[.\s]+0A\b",
        re.I,
    )
    amount_pattern = re.compile(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?")
    account_header_pattern = re.compile(
        r"\b(5\d{5})\b.{0,50}?\b(\d{4})\s*([A-Z]{3})\b",
        re.I,
    )
    item_code_pattern = re.compile(r"\b(\d{3})[.\s]+(\d{3})[.\s]+0[A-Z0-9]\b", re.I)
    for page in pages:
        if page.get("document_type") != "DRPP_COA":
            continue
        page_text = str(page.get("text") or "")
        account_headers = list(account_header_pattern.finditer(page_text.upper()))
        activities = [match.group(2) for match in account_headers]
        dominant_activity = str(activity or "") or (
            max(set(activities), key=activities.count) if activities else ""
        )
        for header_index, header_match in enumerate(account_headers):
            segment_end = (
                account_headers[header_index + 1].start()
                if header_index + 1 < len(account_headers)
                else len(page_text)
            )
            segment = page_text[header_match.end() : segment_end]
            item_codes = list(item_code_pattern.finditer(segment.upper()))
            for item_index, item_match in enumerate(item_codes):
                item_end = (
                    item_codes[item_index + 1].start()
                    if item_index + 1 < len(item_codes)
                    else len(segment)
                )
                item_text = segment[item_match.start() : item_end]
                item_body_offset = item_match.end() - item_match.start()
                amounts = amount_pattern.findall(item_text[item_body_offset:])
                detected_activity = header_match.group(2)
                resolved_activity = str(activity or "")
                if not resolved_activity:
                    resolved_activity = detected_activity
                    if dominant_activity and sum(
                        left != right for left, right in zip(detected_activity, dominant_activity)
                    ) <= 1:
                        resolved_activity = dominant_activity
                rows.append(
                    {
                        "full_coa": ".".join(
                            (
                                resolved_activity,
                                header_match.group(3).upper(),
                                item_match.group(1),
                                item_match.group(2),
                                header_match.group(1),
                            )
                        ),
                        "akun": header_match.group(1),
                        "kegiatan": resolved_activity,
                        "KRO": header_match.group(3).upper(),
                        "RO": item_match.group(1),
                        "komponen": item_match.group(2),
                        "subkomponen": "",
                        "item_uraian": item_text,
                        "nilai_item": _money(amounts[0]) if amounts else Decimal("0"),
                        "nilai_kelompok": Decimal("0"),
                        "order": len(rows),
                        "source_page": page.get("page_number"),
                    }
                )
        for order, line in enumerate(str(page.get("text") or "").splitlines()):
            match = pattern.search(line.upper())
            if not match:
                continue
            amounts = amount_pattern.findall(line)
            rows.append(
                {
                    "full_coa": ".".join(match.groups()).upper(),
                    "akun": match.group(5),
                    "kegiatan": match.group(1),
                    "KRO": match.group(2).upper(),
                    "RO": match.group(3),
                    "komponen": match.group(4),
                    "subkomponen": "",
                    "item_uraian": line[match.end() :].strip(" -|"),
                    "nilai_item": _money(amounts[-1]) if amounts else Decimal("0"),
                    "nilai_kelompok": Decimal("0"),
                    "order": order,
                    "source_page": page.get("page_number"),
                }
            )
        if not any(row.get("source_page") == page.get("page_number") for row in rows):
            compact = full_pattern.search(str(page.get("text") or "").upper())
            if compact:
                amounts = amount_pattern.findall(str(page.get("text") or ""))
                rows.append(
                    {
                        "full_coa": ".".join(
                            (compact.group(2), compact.group(3), compact.group(4), compact.group(5), compact.group(1))
                        ).upper(),
                        "akun": compact.group(1),
                        "kegiatan": compact.group(2),
                        "KRO": compact.group(3).upper(),
                        "RO": compact.group(4),
                        "komponen": compact.group(5),
                        "subkomponen": "",
                        "item_uraian": "",
                        "nilai_item": _money(amounts[-1]) if amounts else Decimal("0"),
                        "nilai_kelompok": Decimal("0"),
                        "order": len(rows),
                        "source_page": page.get("page_number"),
                    }
                )
    return rows


def _tokens(value):
    return {token for token in re.findall(r"[A-Z]{3,}", str(value or "").upper()) if token not in {"DAN", "UNTUK", "YANG"}}


def _match_coa(items, coa_rows, activity=""):
    for order, item in enumerate(items):
        item_amount = _money(item.get("jumlah") or item.get("bruto"))
        exact_amount_rows = [
            coa
            for coa in coa_rows
            if item_amount > 0 and item_amount == _money(coa.get("nilai_item"))
        ]
        exact_amount_keys = {
            (coa.get("akun"), coa.get("full_coa")) for coa in exact_amount_rows
        }
        if len(exact_amount_keys) == 1:
            item["akun"], item["pembebanan"] = next(iter(exact_amount_keys))
            continue
        if item.get("pembebanan"):
            if activity:
                item["pembebanan"] = re.sub(
                    r"^\d{4}(?=\.)", str(activity), str(item["pembebanan"])
                )
            continue
        if not item.get("akun"):
            amount_matches = [
                coa for coa in coa_rows if item_amount == _money(coa.get("nilai_item"))
            ]
            amount_keys = {(coa.get("akun"), coa.get("full_coa")) for coa in amount_matches}
            if len(amount_keys) == 1:
                item["akun"], item["pembebanan"] = next(iter(amount_keys))
                continue
        item_tokens = _tokens(item.get("keperluan"))
        scored = []
        for coa in coa_rows:
            if str(coa.get("akun")) != str(item.get("akun")):
                continue
            score = 5
            if item_amount and item_amount == _money(coa.get("nilai_item")):
                score += 5
            score += min(4, len(item_tokens & _tokens(coa.get("item_uraian"))))
            if order == coa.get("order"):
                score += 1
            scored.append((score, coa))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        distinct_coa = {candidate["full_coa"] for _, candidate in scored}
        coa_frequency = {
            full_coa: sum(
                candidate.get("full_coa") == full_coa for _, candidate in scored
            )
            for full_coa in distinct_coa
        }
        dominant_coa = (
            max(coa_frequency, key=coa_frequency.get) if coa_frequency else ""
        )
        dominant_is_unique = dominant_coa and list(coa_frequency.values()).count(
            coa_frequency[dominant_coa]
        ) == 1
        if scored and (
            len(distinct_coa) == 1
            or len(scored) == 1
        ):
            item["pembebanan"] = scored[0][1]["full_coa"]
        elif scored and dominant_is_unique:
            item["pembebanan"] = dominant_coa
        elif scored and scored[0][0] - scored[1][0] >= 2:
            item["pembebanan"] = scored[0][1]["full_coa"]
        elif scored:
            item["status"] = "PERLU_REVIEW"
            item.setdefault("warnings", []).append("Pembebanan memiliki lebih dari satu kandidat COA berdekatan.")


def _money(value):
    if isinstance(value, Decimal):
        return value
    text = str(value or "").replace("Rp", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "." in text:
        text = text.replace(".", "")
    else:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except Exception:
        return Decimal("0")


def _normalize_kw(value):
    text = str(value or "").upper().strip()
    full = re.search(r"(\d{3,6})/KW/(\d{5,9})/(20\d{2})", text)
    if full:
        return f"{full.group(1).zfill(5)}/KW/{full.group(2)}/{full.group(3)}"
    short = re.search(r"\d{1,6}", text)
    return short.group(0).zfill(5) if short else ""


def parse_kw_support(items, pages, year=""):
    for item in items:
        item["no_bukti"] = _normalize_kw(item.get("no_bukti"))
        short = item["no_bukti"].split("/", 1)[0]
        candidates = []
        for page in pages:
            if page.get("document_type") not in KW_PAGE_TYPES:
                continue
            hint = str(page.get("kw_hint") or "").split("/", 1)[0].zfill(5)
            text = str(page.get("text") or "")
            if short and (hint == short or short in text):
                candidates.append(page)
        if not candidates:
            continue
        text = "\n".join(page.get("text", "") for page in candidates)
        upper = text.upper()
        fp = re.search(r"(?:NOMOR\s+SERI\s+FAKTUR\s+PAJAK|FAKTUR\s+PAJAK)\s*[:\-]?\s*([0-9.\-]{10,25})", upper)
        pph21 = re.search(r"PPH\s*(?:PASAL\s*)?21\D{0,30}(\d{1,3}(?:[.,]\d{3})+)", upper)
        netto = re.search(r"(?:NILAI\s+NETTO|JUMLAH\s+DIBAYAR)\D{0,30}(\d{1,3}(?:[.,]\d{3})+)", upper)
        if fp:
            item["fp"] = fp.group(1)
        if pph21:
            item["pph21"] = _money(pph21.group(1))
        if netto:
            item["netto"] = _money(netto.group(1))
        elif item.get("pph21"):
            item["netto"] = max(_money(item.get("jumlah")) - _money(item.get("pph21")), Decimal("0"))
        item["source_pages"] = {
            page["document_type"]: {"file_name": page["file_name"], "page_number": page["page_number"]}
            for page in candidates
        }
    return items


def _spm_from_sp2d(row):
    from apps.dk.models import TransactionDetail

    existing = TransactionDetail.objects.filter(
        satker_code=row.satker_code,
        nomor_spm__iexact=row.nomor_spm_extracted,
    ).exclude(tanggal_spm__isnull=True).order_by("id").first()
    tanggal = getattr(existing, "tanggal_spm", None) or row.tgl_sp2d or row.tanggal_selesai_sp2d
    return {
        "file_name": row.original_file or "SP2D",
        "status": "parsed_text",
        "method": "sp2d_database",
        "warnings": [],
        "metadata": {
            "nomor_spm": row.nomor_spm_extracted,
            "tanggal_spm": tanggal,
            "jenis_spm": row.jenis_spm or getattr(existing, "jenis_spm", ""),
            "satker_code": row.satker_code,
            "satker_app_code": row.satker_code,
            "jumlah_pengeluaran": row.nilai_spm,
            "jumlah_potongan": row.potongan,
            "total_pembayaran": row.nilai_sp2d,
            "tanggal_sp2d": tanggal,
            "bulan_sp2d": row.bulan_sp2d,
        },
        "detail_items": [],
        "akun_rows": [],
    }


def _exact_sp2d(number, satker="", year=None):
    if not number:
        return None
    from apps.sp2d.models import SP2DRaw

    query = SP2DRaw.objects.filter(nomor_spm_extracted__iexact=number)
    if satker:
        query = query.filter(satker_code=satker)
    if year:
        query = query.filter(
            Q(import_batch__tahun=year)
            | Q(tgl_sp2d__year=year)
            | Q(tanggal_selesai_sp2d__year=year)
        )
    return query.order_by("id").first()


def resolve_spm_parent(drpps, pages):
    metas = [drpp.get("metadata", {}) for drpp in drpps if drpp]
    number = next((str(meta.get("nomor_spm") or "").strip().upper() for meta in metas if meta.get("nomor_spm")), "")
    satker = next((str(meta.get("satker_app_code") or meta.get("satker_code") or "") for meta in metas if meta.get("satker_app_code") or meta.get("satker_code")), "")
    year = next((meta.get("tahun") for meta in metas if str(meta.get("tahun") or "").isdigit()), None)
    year = int(year) if year else None

    sp2d = _exact_sp2d(number, satker, year)
    if sp2d:
        return _spm_from_sp2d(sp2d), sp2d

    from apps.dk.models import TransactionDetail

    if number:
        query = TransactionDetail.objects.filter(nomor_spm__iexact=number)
        if satker:
            query = query.filter(satker_code=satker)
        if year:
            query = query.filter(tanggal_spm__year=year)
        existing = query.exclude(tanggal_spm__isnull=True).order_by("id").first()
        if existing:
            return {
                "file_name": "D_K",
                "status": "parsed_text",
                "method": "transaction_database",
                "warnings": [],
                "metadata": {
                    "nomor_spm": existing.nomor_spm,
                    "tanggal_spm": existing.tanggal_spm,
                    "jenis_spm": existing.jenis_spm,
                    "satker_code": existing.satker_code,
                    "satker_app_code": existing.satker_code,
                    "jumlah_pengeluaran": Decimal("0"),
                    "jumlah_potongan": Decimal("0"),
                    "total_pembayaran": Decimal("0"),
                    "bulan_sp2d": existing.bulan_sp2d,
                },
                "detail_items": [],
                "akun_rows": [],
            }, existing.sp2d_raw

    spm_pages = [page for page in pages if page.get("document_type") == "SPM" and page.get("is_representative")]
    if not spm_pages:
        return None, None
    page = max(spm_pages, key=lambda item: len(item.get("text", "")))
    spm = parse_spm_pdf(page["_path"], ocr=False, extracted=_extracted_from_pages([page]), parse_details=False)
    spm_meta = spm.get("metadata", {})
    detected = str(spm_meta.get("nomor_spm") or "").strip().upper()
    filename_match = re.search(r"\b(\d{5}[A-Z])\b", page.get("file_name", "").upper())
    filename_number = filename_match.group(1) if filename_match else ""
    if filename_number and detected[:-1] == filename_number[:-1] and detected != filename_number:
        filename_sp2d = _exact_sp2d(filename_number, satker, year)
        if filename_sp2d:
            return _spm_from_sp2d(filename_sp2d), filename_sp2d
        spm_meta["nomor_spm_ocr"] = detected
        spm_meta["nomor_spm"] = filename_number
        spm_meta["nomor_spm_final"] = filename_number
        spm_meta["nomor_spm_final_source"] = "filename_batch"
        spm_meta["nomor_spm_reason"] = "Suffix batch DRPP mengikuti nama PDF SPM saat nomor dasar sama."
        detected = filename_number
    if str(spm_meta.get("jenis_spm") or "").upper() in {"GUP", "TUP"}:
        spm_meta["cara_pembayaran"] = "UP/TUP"
    sp2d = _exact_sp2d(detected, satker, year)
    if sp2d:
        return _spm_from_sp2d(sp2d), sp2d
    return spm, None


def build_transaction_items(drpp, spm=None):
    meta = drpp.get("metadata", {})
    spm_meta = (spm or {}).get("metadata", {})
    output = []
    for item in drpp.get("items", []):
        bruto = _money(item.get("bruto") or item.get("jumlah"))
        pph21 = _money(item.get("pph21"))
        netto = _money(item.get("netto")) or (bruto - pph21 if pph21 else bruto)
        no_kw = _normalize_kw(item.get("no_bukti"))
        warnings = list(item.get("warnings") or [])
        if no_kw and not re.fullmatch(r"\d{5}/KW/\d{5,9}/20\d{2}", no_kw):
            warnings.append("Nomor kuitansi belum lengkap; lengkapi pada preview tanpa menebak Satker/tahun.")
        missing = []
        for field, value in (("nomor kuitansi", no_kw), ("akun", item.get("akun")), ("nilai bruto", bruto)):
            if not value:
                missing.append(field)
        if not item.get("pembebanan"):
            warnings.append("Pembebanan belum cocok unik dengan Detail COA.")
        status = "GAGAL" if missing else ("PERLU_REVIEW" if warnings or item.get("needs_review") else "LENGKAP")
        output.append(
            {
                **item,
                "helper": f"{item.get('akun', '')}{no_kw}",
                "akun": str(item.get("akun") or ""),
                "bulan_sp2d": spm_meta.get("bulan_sp2d") or getattr(spm_meta.get("tanggal_sp2d") or spm_meta.get("tanggal_spm"), "month", ""),
                "cara_pembayaran": "UP/TUP" if str(spm_meta.get("jenis_spm") or "").upper() in {"GU", "GUP", "TUP"} else ("LS" if str(spm_meta.get("jenis_spm") or "").upper().startswith("LS") else ""),
                "nomor_spm": spm_meta.get("nomor_spm") or meta.get("nomor_spm") or "",
                "tanggal_spm": spm_meta.get("tanggal_spm"),
                "jenis_spm": spm_meta.get("jenis_spm") or "",
                "no_bukti": no_kw,
                "no_kuitansi": no_kw,
                "no_drpp": meta.get("nomor_drpp") or "",
                "keperluan": item.get("keperluan") or item.get("deskripsi") or "",
                "deskripsi": item.get("keperluan") or item.get("deskripsi") or "",
                "jumlah": bruto,
                "bruto": bruto,
                "nilai_bruto": bruto,
                "netto": netto,
                "nilai_netto": netto,
                "pembebanan": item.get("pembebanan") or "",
                "fp": item.get("fp") or "",
                "pph21": pph21,
                "status_detail": status,
                "status": status,
                "warnings": warnings + (["Field wajib kosong: " + ", ".join(missing)] if missing else []),
                "source_pages": item.get("source_pages") or {},
            }
        )
    return output


def validate_drpp_group(drpp, items):
    number = str(drpp.get("metadata", {}).get("nomor_drpp") or "")
    expected_count = len(drpp.get("items") or [])
    expected_total = _money(
        drpp.get("metadata", {}).get("printed_total")
        or drpp.get("metadata", {}).get("total")
    )
    actual_total = sum((_money(item.get("nilai_bruto") or item.get("bruto") or item.get("jumlah")) for item in items), Decimal("0"))
    errors = []
    if not number:
        errors.append("Nomor DRPP kosong.")
    if len(items) != expected_count:
        errors.append(f"Jumlah baris hasil ({len(items)}) tidak sama dengan jumlah baris DRPP ({expected_count}).")
    if expected_total and actual_total != expected_total:
        errors.append(f"Total baris Rp{actual_total:,.0f} tidak sama dengan total DRPP Rp{expected_total:,.0f}.")
    seen = set()
    for item in items:
        if not item.get("no_kuitansi"):
            errors.append("Nomor kuitansi kosong.")
        if not item.get("akun"):
            errors.append("Akun kosong.")
        if _money(item.get("nilai_bruto")) <= 0:
            errors.append("Nilai bruto nol tanpa bukti.")
        key = (item.get("nomor_spm"), item.get("no_kuitansi"), item.get("akun"))
        if key in seen:
            errors.append("Duplikat exact key ditemukan dalam upload yang sama.")
        seen.add(key)
    return {
        "no_drpp": number,
        "row_count": len(items),
        "expected_row_count": expected_count,
        "total_drpp": expected_total,
        "total_rows": actual_total,
        "status": "BALANCE" if not errors else "PERLU_REVIEW",
        "can_commit": not errors,
        "errors": list(dict.fromkeys(errors)),
    }


def _public_manifest(manifest):
    return [{key: value for key, value in item.items() if not key.startswith("_")} for item in manifest]


def _public_page(page):
    return {
        "file_name": page["file_name"],
        "page_number": page["page_number"],
        "document_type": page.get("document_type", "UNKNOWN"),
        "confidence": page.get("confidence", 0),
        "evidence": page.get("evidence", []),
        "page_hash": page.get("page_hash", ""),
        "duplicate_of": page.get("duplicate_of"),
        "ocr_called": page.get("ocr_called", False),
        "cache_hit": page.get("cache_hit", False),
    }


def parse_drpp_upload_batch(file_path, ocr=True):
    started = time.monotonic()
    manifest = build_manifest(file_path)
    temp_dir = next((item.get("_temp_dir") for item in manifest if item.get("_temp_dir")), "")
    try:
        filename_numbers = {item["drpp_hint"] for item in manifest if item.get("drpp_hint")}
        if len(filename_numbers) > MAX_DRPP:
            raise ValueError(TOO_MANY_DRPP_MESSAGE)

        page_index = build_page_index(manifest)
        discover_embedded_drpp_pages(page_index, ocr=ocr)
        page_index = deduplicate_pages(page_index)
        classify_candidate_pages(page_index, ocr=ocr)
        detected_numbers = {
            number
            for page in page_index
            for number in (page.get("drpp_detected"), page.get("drpp_hint"))
            if number
        }
        if len(detected_numbers) > MAX_DRPP:
            raise ValueError(TOO_MANY_DRPP_MESSAGE)
        numbers = sorted(detected_numbers or filename_numbers)
        if not numbers:
            raise ValueError("Nomor DRPP tidak ditemukan pada nama file maupun isi halaman.")

        file_numbers = defaultdict(set)
        for page in page_index:
            for number in (page.get("drpp_detected"), page.get("drpp_hint")):
                if number:
                    file_numbers[page["file_name"]].add(number)

        def pages_for(number):
            return [
                page
                for page in page_index
                if (
                    page.get("drpp_detected") == number
                    or page.get("drpp_hint") == number
                    or file_numbers.get(page["file_name"]) == {number}
                )
            ]

        drpps = []
        all_items = []
        groups = []
        used_kw = {}
        for number in numbers:
            group_pages = pages_for(number)
            drpp = parse_drpp_summary(number, group_pages)
            if not drpp:
                groups.append({"no_drpp": number, "items": [], "validation": {"status": "PERLU_REVIEW", "can_commit": False, "errors": ["Halaman DRPP tidak ditemukan."]}})
                continue
            coa_rows = parse_drpp_coa(
                group_pages,
                activity=drpp.get("metadata", {}).get("kode_kegiatan", ""),
            )
            _match_coa(
                drpp.get("items", []),
                coa_rows,
                activity=drpp.get("metadata", {}).get("kode_kegiatan", ""),
            )
            drpps.append(drpp)

        spm, sp2d_parent = resolve_spm_parent(drpps, page_index)
        for drpp in drpps:
            number = drpp.get("metadata", {}).get("nomor_drpp", "")
            group_pages = pages_for(number)
            year = drpp.get("metadata", {}).get("tahun") or getattr((spm or {}).get("metadata", {}).get("tanggal_spm"), "year", "")
            parse_kw_support(drpp.get("items", []), group_pages, year=str(year or ""))
            items = build_transaction_items(drpp, spm)
            duplicate_kw = []
            for item in items:
                kw = item.get("no_kuitansi")
                if kw in used_kw and used_kw[kw] != number:
                    duplicate_kw.append(kw)
                elif kw:
                    used_kw[kw] = number
            validation = validate_drpp_group(drpp, items)
            if duplicate_kw:
                validation["errors"].append("Satu kuitansi masuk ke dua DRPP: " + ", ".join(sorted(set(duplicate_kw))))
                validation["status"] = "PERLU_REVIEW"
                validation["can_commit"] = False
            group = {
                "no_drpp": number,
                "drpp": drpp,
                "items": items,
                "validation": validation,
                "status": validation["status"],
            }
            groups.append(group)
            all_items.extend(items)

        elapsed = round(time.monotonic() - started, 3)
        metrics = {
            "ocr_seconds": round(
                sum(page.get("ocr_duration", 0) + page.get("probe_duration", 0) for page in page_index),
                3,
            ),
            "process_seconds": elapsed,
            "page_total": len(page_index),
            "unique_pages": sum(1 for page in page_index if page.get("is_representative")),
            "ocr_pages": sum(
                1 for page in page_index if page.get("ocr_called") or page.get("probe_ocr_called")
            ),
            "ocr_cache_hits": sum(
                1 for page in page_index if page.get("cache_hit") or page.get("probe_cache_hit")
            ),
        }
        warnings = [error for group in groups for error in group.get("validation", {}).get("errors", [])]
        return {
            "ok": bool(drpps and all_items),
            "parser_version": PARSER_VERSION,
            "files": [
                {
                    **item,
                    "type": item.get("type_hint", "UNKNOWN"),
                    "status": "indexed",
                    "parse_status": "indexed",
                    "method": "drpp_batch_manifest",
                    "warnings": [],
                }
                for item in _public_manifest(manifest)
            ],
            "manifest": _public_manifest(manifest),
            "page_index": [_public_page(page) for page in page_index],
            "spm": spm,
            "sp2d_parent_id": getattr(sp2d_parent, "id", None),
            "drpp": drpps[0] if drpps else None,
            "drpps": drpps,
            "drpp_groups": groups,
            "kw_by_drpp": {group["no_drpp"]: group.get("items", []) for group in groups},
            "kw_items": all_items,
            "preview_rows": [],
            "warnings": list(dict.fromkeys(warnings)),
            "metrics": metrics,
            "temp_dir": temp_dir,
        }
    except Exception:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise
