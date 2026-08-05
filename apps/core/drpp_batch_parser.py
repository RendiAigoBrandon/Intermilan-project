"""Parser cepat untuk upload DRPP beserta kuitansinya.

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
from apps.sp2d.models import SP2DRaw
import shutil
import tempfile
import threading
import time
import zipfile
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db.models import Q

from apps.core.exceptions import UploadTechnicalError
from apps.core.document_policy import (
    DocumentRequirement,
    SPMFamily,
    document_requirement_policy,
    normalize_spm_family,
)

from apps.core.ocr import (
    configure_tesseract,
    extract_paddleocr,
    parse_bool_env,
    preprocess_image,
    tesseract_page_text_best_rotation,
)
from apps.core.parsers import (
    compact_pembebanan_from_coa,
    extract_drpp_printed_total,
    extract_drpp_total_candidates,
    parse_decimal,
    parse_date,
    parse_drpp_pdf,
    parse_spm_pdf,
    select_drpp_printed_total_candidate,
)


PARSER_VERSION = "drpp-batch-v5"

PAGE_TYPES = (
    "KKP_PAYMENT_LIST",
    "KKP_CARD_STATEMENT",
    "KKP_PAYMENT_ORDER",
    "DRPP_SUMMARY",
    "DRPP_COA",
    "SPM",
    "SPP",
    "SP2D",
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
        raise UploadTechnicalError(f"Jumlah file melebihi batas {max_files} file.")
    if sum(member.file_size for member in pdf_members) > max_bytes:
        raise UploadTechnicalError("Ukuran hasil ekstraksi ZIP melebihi batas upload.")
    for member in archive.infolist():
        if member.is_dir():
            continue
        if member.filename.lower().endswith(".zip"):
            raise UploadTechnicalError("ZIP bertingkat tidak didukung.")
        if not member.filename.lower().endswith(".pdf"):
            continue
        destination = os.path.realpath(os.path.join(root, member.filename.replace("/", os.sep)))
        if os.path.commonpath([root, destination]) != root:
            raise UploadTechnicalError("ZIP memuat path file yang tidak aman.")
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
        raise UploadTechnicalError("Format file tidak didukung. Gunakan ZIP, folder, atau PDF.")

    if not paths:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise UploadTechnicalError("Tidak ada PDF yang dapat diproses dalam unggahan.")

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
                page["page_content_hash"] = (
                    hashlib.sha256(image.tobytes()).hexdigest() if image is not None else ""
                )
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


def deduplicate_pages(page_index):
    representatives = []
    for page in page_index:
        # dHash is only a visual-similarity hint. Financial forms with different
        # rows can have a tiny dHash distance, so deduplication requires exact
        # rendered content (or exact legacy page_hash in synthetic callers).
        exact_hash = page.get("page_content_hash") or page.get("page_hash")
        duplicate = next(
            (
                candidate
                for candidate in representatives
                if exact_hash
                and exact_hash == (candidate.get("page_content_hash") or candidate.get("page_hash"))
                and candidate.get("type_hint") == page.get("type_hint")
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
            
    # Cross mapping: if page A is a duplicate of B, items parsed from B belong to A's file context?
    # No, parser just extracts items from B. But wait, if two PDFs have the same DRPP, it will only OCR B.
    # The KW pages from A are still unique pages! They will be extracted and put into the same drpp_group!
    return page_index


_local = threading.local()

def _cache_path(page, engine):
    raw = "|".join(
        (PARSER_VERSION, page.get("file_sha256", ""), page.get("page_hash", ""), engine)
    )
    key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    cache_dir = os.path.join(settings.MEDIA_ROOT, "ocr_cache", "drpp_batch")
    return os.path.join(cache_dir, f"{key}.json")


def _load_page_cache(page, engine):
    path = _cache_path(page, engine)
    mem_cache = getattr(_local, "ocr_cache", {})
    if path in mem_cache:
        cached = mem_cache[path]
        if str(cached.get("text") or "").strip() or cached.get("cache_empty"):
            cached = dict(cached)
            cached["cache_hit"] = True
            return cached
    try:
        with open(path, encoding="utf-8") as handle:
            cached = json.load(handle)
        if str(cached.get("text") or "").strip() or cached.get("cache_empty"):
            mem_cache = getattr(_local, "ocr_cache", None)
            if mem_cache is not None:
                mem_cache[path] = cached
            cached = dict(cached)
            cached["cache_hit"] = True
            return cached
    except (OSError, ValueError, TypeError):
        return None
    return None


def _save_page_cache(page, engine, result):
    if not str(result.get("text") or "").strip() and not result.get("cache_empty"):
        return
    path = _cache_path(page, engine)
    mem_cache = getattr(_local, "ocr_cache", None)
    if mem_cache is not None:
        mem_cache[path] = result
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
    if page.get("type_hint") == "SPM":
        return page["page_number"] <= int(
            getattr(settings, "DRPP_BATCH_SPM_IDENTITY_SCAN_PAGES", 12)
        )
    if page.get("type_hint") == "DRPP_SUMMARY":
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
        coa_page = 0
        for page in page_index:
            if page["file_name"] != file_name:
                continue
            started = time.monotonic()
            probe = _probe_page_text(page)
            page["probe_duration"] = time.monotonic() - started
            page["probe_ocr_called"] = not probe.get("cache_hit", False)
            page["probe_cache_hit"] = bool(probe.get("cache_hit"))
            document_type = _classification(probe.get("text", ""))[0]
            if page.get("type_hint") == "KUITANSI" and page.get("page_number") == 1:
                page["force_probe"] = True
            if document_type in {"SPM", "SPP", "SP2D"}:
                page["force_probe"] = True
            if document_type == "DRPP_SUMMARY":
                page["force_probe"] = True
                found_summary = True
                summary_page = page["page_number"]
            elif found_summary and document_type == "DRPP_COA":
                page["force_probe"] = True
                coa_page = page["page_number"]
                continue
            elif coa_page:
                page["force_probe"] = True
                page["type_hint"] = "DRPP_COA"
                page["drpp_continuation"] = True
                if page["page_number"] >= coa_page + 1:
                    break
            elif found_summary:
                page["force_probe"] = True
                page["type_hint"] = "DRPP_SUMMARY"
                page["drpp_continuation"] = True
                if page["page_number"] >= summary_page + 2:
                    break

    if not numbers:
        bundle_files = {
            page["file_name"]
            for page in page_index
            if re.search(r"\bSPM\b", Path(page["file_name"]).stem.upper())
        }
        for bundle_file in bundle_files:
            log_stats = {
                "file": bundle_file,
                "total_pages": 0,
                "unique_pages": 0,
                "native_pages": 0,
                "candidate_pages": [],
                "ocr_pages": [],
                "drpp_pages": [],
                "kw_pages": [],
                "cache_hits": 0,
                "duration_probe": 0.0,
            }
            file_pages = [p for p in page_index if p["file_name"] == bundle_file]
            log_stats["total_pages"] = len(file_pages)
            unique_pages = [p for p in file_pages if p.get("is_representative", True)]
            log_stats["unique_pages"] = len(unique_pages)
            
            for page in unique_pages:
                if page.get("native_text", "").strip():
                    log_stats["native_pages"] += 1
                    
                if page["page_number"] > 12:
                    continue
                    
                log_stats["candidate_pages"].append(page["page_number"])
                started = time.monotonic()
                probe = _probe_page_text(page)
                probe_dur = time.monotonic() - started
                log_stats["duration_probe"] += probe_dur
                
                page["probe_duration"] = probe_dur
                page["probe_ocr_called"] = not probe.get("cache_hit", False)
                page["probe_cache_hit"] = bool(probe.get("cache_hit"))
                
                if probe.get("cache_hit"):
                    log_stats["cache_hits"] += 1
                
                cls_type = _classification(probe.get("text", ""))[0]
                if cls_type == "DRPP_SUMMARY":
                    log_stats["drpp_pages"].append(page["page_number"])
                    page["primary_for_drpp"] = True
                    page["type_hint"] = "DRPP_SUMMARY"
                    for candidate in page_index:
                        if candidate["file_name"] == bundle_file and page["page_number"] <= candidate["page_number"] <= page["page_number"] + 2:
                            candidate["force_probe"] = True
                    break
                    
            print(f"[DRPP PAGE DISCOVERY] file={log_stats['file']} total_pages={log_stats['total_pages']} unique_pages={log_stats['unique_pages']} native_pages={log_stats['native_pages']} candidate_pages={log_stats['candidate_pages']} drpp_pages={log_stats['drpp_pages']} cache_hits={log_stats['cache_hits']} duration_probe={log_stats['duration_probe']:.3f}s")
            
    return page_index


def _ocr_page(page, use_cache=True, rotations=None, dpi=220, timeout=None, configs=None, lang_attempts=None):
    # Versi ini membandingkan kedua orientasi landscape sebelum menerima hasil.
    # Versi cache dipisahkan agar hasil lama yang terbalik tidak digunakan lagi.
    cache_engine = "tesseract-ind+eng-v3"
    if use_cache:
        cached = _load_page_cache(page, cache_engine)
        if cached:
            return cached
    try:
        import pytesseract
    except Exception:
        return {"text": "", "confidence": 0, "words": [], "engine": "tesseract", "warnings": ["pytesseract tidak tersedia."]}
    if not configure_tesseract(pytesseract):
        return {"text": "", "confidence": 0, "words": [], "engine": "tesseract", "warnings": ["Tesseract tidak tersedia."]}

    image = _render_page(page, dpi) or page.get("_image")
    if image is None:
        return {"text": "", "confidence": 0, "words": [], "engine": "tesseract", "warnings": ["Halaman gagal dirender."]}
    processed = preprocess_image(image)
    text, confidence, warnings, words, rotation, tried, _score = tesseract_page_text_best_rotation(
        pytesseract,
        processed,
        rotations=rotations,
        timeout=timeout,
        configs=configs,
        lang_attempts=lang_attempts,
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
        "cache_empty": not bool(text.strip()),
    }
    if use_cache:
        _save_page_cache(page, cache_engine, result)

    if (len(text.strip()) < 40 or confidence < 35) and parse_bool_env("OCR_ENABLE_PADDLEOCR", False):
        paddle_cache = _load_page_cache(page, "paddleocr") if use_cache else None
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
                "cache_empty": not bool(getattr(paddle_page, "extracted_text", "").strip()),
            }
            if use_cache:
                _save_page_cache(page, "paddleocr", paddle)
        if len(paddle.get("text", "")) > len(result.get("text", "")):
            result = paddle
    return result


def _looks_like_drpp_summary_text(text):
    """Kenali halaman DRPP dari OCR probe yang sering salah baca huruf."""
    upper = " ".join(str(text or "").upper().split())
    if not upper:
        return False
    compact = re.sub(r"[^A-Z0-9]", "", upper)
    title_score = sum(
        any(variant in upper for variant in variants)
        for variants in (
            ("DAFTAR", "OAFTAR"),
            ("RINCIAN", "RINGIAN", "RINCIAAN"),
            ("PERMINTAAN", "PERINTAAN"),
            ("PEMBAYARAN", "PEMBAYARAR"),
        )
    )
    has_drpp_marker = "DRPP" in compact
    has_receipt_table = "BUKTI PENGELUARAN" in upper and re.search(r"\d{3,6}\s*/\s*KW", upper, re.I)
    return (title_score >= 3 and has_drpp_marker) or (title_score >= 3 and has_receipt_table)


def _looks_like_spm_text(text):
    """Probe OCR scan kadang menjatuhkan huruf pada judul SPM."""
    upper = " ".join(str(text or "").upper().split())
    compact = re.sub(r"[^A-Z0-9]", "", upper)
    if not upper:
        return False
    has_letterhead = "BADAN PUSAT STAT" in upper or "KEMENTERIAN" in upper
    has_surat = "SURAT" in upper
    has_perintah = "PERINTAH" in upper or "PERNTAH" in upper or "PERINTA" in upper
    has_membayar = "MEMBAYAR" in upper or "EMBAYAR" in upper or "EMOAYAR" in upper or "MEMBAYAR" in compact
    has_spm_fields = any(anchor in upper for anchor in ("JENIS TAGIHAN", "CARA BAYAR", "DIPA", "KPPN"))
    return has_surat and has_perintah and has_membayar and (has_letterhead or has_spm_fields)


def _classification(text):
    raw_text = str(text or "")
    upper = " ".join(raw_text.upper().split())
    if "DAFTAR PEMBAYARAN TAGIHAN KARTU KREDIT PEMERINTAH" in upper:
        return "KKP_PAYMENT_LIST", 100, ["judul daftar pembayaran KKP"]
    if re.search(r"LEMBAR\s+PENAGIHAN(?:\s+\w+){0,3}\s+KARTU\s+KREDIT\s+PEMERINTAH", upper):
        return "KKP_CARD_STATEMENT", 100, ["judul lembar penagihan KKP"]
    if "PERINTAH BAYAR" in upper and (
        "KARTU KREDIT PEMERINTAH" in upper or re.search(r"/PB/KKP/", upper)
    ):
        return "KKP_PAYMENT_ORDER", 95, ["surat perintah bayar KKP"]
    coa_evidence = [anchor for anchor in ("DETAIL COA", "LAMPIRAN DAFTAR RINCIAN") if anchor in upper]
    if coa_evidence:
        return "DRPP_COA", min(100, 65 + 15 * len(coa_evidence)), coa_evidence
    receipt_number = re.search(
        r"(?:NO\.?\s*KUITANSI|NOMOR)\s*[:\-]?\s*\d{3,6}/KW/\d{5,9}/20\d{2}",
        upper,
    )
    if re.search(r"K[WU]ITANSI\s*/\s*BUKTI PEMBAYARAN", upper):
        return "KUITANSI", 95, ["struktur kuitansi"]
    table_anchors = ("KUITANSI", "AKUN", "DESKRIPSI", "BRUTO", "NETTO", "PEMBEBANAN")
    has_table_header = any(
        sum(anchor in " ".join(line.upper().split()) for anchor in table_anchors) >= 4
        for line in raw_text.splitlines()
    )
    if (
        "DAFTAR RINCIAN PERMINTAAN PEMBAYARAN" in upper
        or "DAFTAR RINCIAN PERINTAAN PEMBAYARAN" in upper
        or has_table_header
    ):
        return "DRPP_SUMMARY", 95, ["struktur tabel DRPP"]
    if _looks_like_drpp_summary_text(raw_text):
        return "DRPP_SUMMARY", 80, ["struktur DRPP dari OCR probe"]
    if _looks_like_spm_text(raw_text):
        return "SPM", 75, ["struktur SPM dari OCR probe"]
    receipt_anchors = (
        "UNTUK PEMBAYARAN", "NILAI BRUTO", "JUMLAH BRUTO", "BRUTO",
        "NILAI NETTO", "JUMLAH DIBAYAR", "NETTO", "POTONGAN",
        "AKUN", "PEMBEBANAN",
    )
    if receipt_number and (
        "NO KUITANSI" in upper
        or "NO. KUITANSI" in upper
        or sum(anchor in upper for anchor in receipt_anchors) >= 2
    ):
        return "KUITANSI", 95, ["struktur kuitansi"]
    rules = [
        ("DRPP_COA", ("DETAIL COA", "LAMPIRAN DAFTAR RINCIAN")),
        ("SPM", ("SURAT PERINTAH MEMBAYAR",)),
        ("SPP", ("SURAT PERMINTAAN PEMBAYARAN",)),
        (
            "SP2D",
            (
                "SURAT PERINTAH PENCAIRAN DANA",
                "DETAIL PENGELUARAN DAN POTONGAN PADA SPP/SPM/SP2D",
            ),
        ),
        ("SURAT_PERNYATAAN_BAYAR", ("SURAT PERNYATAAN BAYAR",)),
        ("MEMO_PENCAIRAN", ("MEMO PENCAIRAN",)),
        ("FAKTUR_PAJAK", ("FAKTUR PAJAK",)),
        ("SSP", ("SURAT SETORAN PAJAK",)),
        ("BUKTI_TRANSFER", ("BUKTI TRANSFER", "BUKTI PEMBAYARAN")),
        ("DAFTAR_NOMINATIF", ("DAFTAR NOMINATIF",)),
        ("RINCIAN_BIAYA", ("RINCIAN BIAYA",)),
        ("INVOICE", ("INVOICE",)),
    ]
    for document_type, anchors in rules:
        evidence = [anchor for anchor in anchors if anchor in upper]
        if evidence:
            confidence = min(100, 65 + 15 * len(evidence))
            return document_type, confidence, evidence
            
    if re.search(r"\b\d{4,6}\s*[/|:]\s*DRPP\s*[/|:]\s*\d{4,6}\s*[/|:]\s*20\d{2}\b", upper):
        return "DRPP_SUMMARY", 80, ["pola nomor drpp"]
        
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
            and (
                page.get("drpp_continuation")
                or ("BUKTI PENGELUARAN" in text.upper() and re.search(r"\d{3,6}/KW/", text, re.I))
            )
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


def verify_drpp_rows_high_res(items, pages, printed_total, dpi=360):
    """Verifikasi ulang baris tabel hanya saat hasil awal tidak balance/review."""
    if not items or not printed_total:
        return []
    try:
        import pytesseract
        from PIL import ImageOps
    except Exception:
        return []
    if not configure_tesseract(pytesseract):
        return []

    pages_by_number = {page.get("page_number"): page for page in pages}
    images = {}
    candidates = []
    for item in items:
        page = pages_by_number.get(item.get("source_page"))
        box = item.get("bounding_box") or []
        if not page or len(box) != 4 or int(page.get("rotation") or 0) != 0:
            candidates.append({})
            continue
        page_number = page.get("page_number")
        if page_number not in images:
            low_image = _render_page(page, 220)
            high_image = _render_page(page, dpi)
            images[page_number] = (low_image, high_image)
        low_image, high_image = images[page_number]
        if low_image is None or high_image is None:
            candidates.append({})
            continue
        scale = high_image.width / max(low_image.width, 1)
        top = max(0, int((float(box[1]) - 8) * scale))
        bottom = min(high_image.height, int((float(box[1]) + 52) * scale))
        row_image = ImageOps.autocontrast(high_image.crop((0, top, high_image.width, bottom)))
        try:
            text = pytesseract.image_to_string(row_image, lang="ind+eng", config="--psm 6")
        except Exception:
            candidates.append({})
            continue
        kw_match = re.search(r"(\d{3,6})\s*/\s*KW\s*/\s*(\d{5,9})\s*/\s*(20\d{2})", text, re.I)
        akun_match = re.search(r"\b(5\d{5})\b", text)
        amounts = re.findall(r"\b\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?\b", text)
        candidates.append({
            "text": " ".join(text.split()),
            "no_bukti": (
                f"{kw_match.group(1).zfill(5)}/KW/{kw_match.group(2)}/{kw_match.group(3)}"
                if kw_match else ""
            ),
            "akun": akun_match.group(1) if akun_match else "",
            "jumlah": _money(re.sub(r"\D", "", amounts[-1])) if amounts else Decimal("0"),
        })

    kw_parts = []
    for item, candidate in zip(items, candidates):
        for value in (candidate.get("no_bukti"), item.get("no_bukti")):
            match = re.fullmatch(r"(\d{5})/KW/(\d{5,9})/(20\d{2})", str(value or ""), re.I)
            if match:
                kw_parts.append(match.groups())
    dominant_satker = Counter(part[1] for part in kw_parts).most_common(1)
    dominant_year = Counter(part[2] for part in kw_parts).most_common(1)
    if dominant_satker and dominant_year:
        satker = dominant_satker[0][0]
        year = dominant_year[0][0]
        for candidate in candidates:
            match = re.fullmatch(r"(\d{5})/KW/(\d{5,9})/(20\d{2})", str(candidate.get("no_bukti") or ""), re.I)
            if match:
                candidate["no_bukti"] = f"{match.group(1)}/KW/{satker}/{year}"

    candidate_total = sum(
        (
            candidate.get("jumlah") or _money(item.get("jumlah"))
            for item, candidate in zip(items, candidates)
        ),
        Decimal("0"),
    )
    amounts_verified = candidate_total == printed_total
    verification = []
    for item, candidate in zip(items, candidates):
        if not candidate:
            continue
        changed = []
        for field in ("no_bukti", "akun"):
            value = candidate.get(field)
            if value and value != item.get(field):
                item[f"{field}_ocr"] = item.get(field)
                item[field] = value
                changed.append(field)
        if amounts_verified and candidate.get("jumlah") and candidate["jumlah"] != _money(item.get("jumlah")):
            item["jumlah_ocr"] = item.get("jumlah")
            item["jumlah"] = candidate["jumlah"]
            item["amount_verified_high_res"] = True
            changed.append("jumlah")
        if candidate.get("akun"):
            item["review_fields"] = [
                field for field in (item.get("review_fields") or [])
                if field not in {"akun", "akun_invalid"}
            ]
            item["needs_review"] = bool(item["review_fields"])
            item["status"] = "Perlu Review" if item["needs_review"] else "Terbaca"
        verification.append({
            "source_page": item.get("source_page"),
            "fields": changed,
            "method": "high_res_row_ocr",
            "candidate_amount": candidate.get("jumlah"),
            "amounts_verified": amounts_verified,
        })
    return verification


def _resolve_drpp_printed_total(number, pages, structural_total=Decimal("0"), structural_count=0):
    candidates = []
    for page in pages:
        candidates.extend(extract_drpp_total_candidates(
            page.get("text") or page.get("native_text") or "",
            file_name=page.get("file_name", ""),
            page_number=page.get("page_number"),
            document_type=page.get("document_type", ""),
            nomor_drpp=number,
        ))
    current = next((item for item in candidates if item.get("accepted") and item.get("kind") == "explicit_current"), None)
    previous = next((item for item in candidates if item.get("kind") == "cumulative_previous"), None)
    through = next((item for item in candidates if item.get("kind") == "cumulative_through_current"), None)
    current_rejected_as_inconsistent = False
    if current and previous and through:
        if _money(current.get("value")) + _money(previous.get("value")) != _money(through.get("value")):
            current["accepted"] = False
            current["reason"] = "inconsistent_with_cumulative_totals"
            current_rejected_as_inconsistent = True
    if current_rejected_as_inconsistent and structural_count > 0 and _money(structural_total) > 0:
        summary_page = next(
            (
                page for page in pages
                if page.get("document_type") == "DRPP_SUMMARY"
                and (not number or page.get("drpp_detected") in {"", None, number} or number in str(page.get("text") or ""))
            ),
            {},
        )
        candidates.append({
            "raw_label": "SUM(DRPP item rows)",
            "raw_money_token": str(structural_total),
            "normalized_value": _money(structural_total),
            "value": _money(structural_total),
            "file": summary_page.get("file_name", ""),
            "page": summary_page.get("page_number"),
            "document_type": "DRPP_SUMMARY",
            "nomor_drpp": number,
            "expected_drpp": number,
            "extraction_method": "drpp_structural_row_sum",
            "confidence": 90,
            "source_rank": 90,
            "kind": "structural_rows_after_bad_current_label",
            "selected": False,
            "accepted": True,
            "reason": "eligible_after_rejecting_inconsistent_current_label",
        })
    selected = select_drpp_printed_total_candidate(candidates)
    conflict = False
    if selected:
        selected_value = _money(selected.get("value"))
        accepted = [item for item in candidates if item.get("accepted") and _money(item.get("value")) > 0]
        conflict = any(_money(item.get("value")) != selected_value for item in accepted)
        if conflict:
            for item in accepted:
                if item is not selected and _money(item.get("value")) != selected_value:
                    item["accepted"] = False
                    item["reason"] = "conflicts_with_selected_total"
    return {
        "selected": selected,
        "candidates": candidates,
        "rejected": [item for item in candidates if not item.get("accepted")],
        "conflict": conflict,
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
    selected_text = "\n".join(str(page.get("text") or "") for page in selected)
    header = _parse_drpp_header(selected_text or summary.get("text") or "")
    for key, value in header.items():
        if value not in (None, "", Decimal("0")) and not parsed["metadata"].get(key):
            parsed["metadata"][key] = value
    structural_total = sum((_money(item.get("jumlah")) for item in parsed.get("items", [])), Decimal("0"))
    total_evidence = _resolve_drpp_printed_total(
        number,
        pages,
        structural_total=structural_total,
        structural_count=len(parsed.get("items", [])),
    )
    evidence_total = _money((total_evidence.get("selected") or {}).get("value"))
    if evidence_total > 0:
        parsed["metadata"]["printed_total"] = evidence_total
        parsed["metadata"]["total_drpp"] = evidence_total
        parsed["metadata"]["printed_total_provenance"] = total_evidence["selected"]
        parsed["metadata"]["printed_total_candidates"] = total_evidence["candidates"]
        parsed["metadata"]["printed_total_rejected_candidates"] = total_evidence["rejected"]
        parsed["metadata"]["printed_total_conflict"] = total_evidence["conflict"]
    parsed["file_name"] = summary["file_name"]
    parsed["source_pages"] = [
        {"file_name": page["file_name"], "page_number": page["page_number"], "page_hash": page.get("page_hash", "")}
        for page in selected
    ]
    items = parsed.get("items", [])
    printed_total = _money(parsed["metadata"].get("printed_total"))
    parsed_total = sum((_money(item.get("jumlah")) for item in items), Decimal("0"))
    if printed_total > 0 and (
        parsed_total != printed_total
        or any(item.get("needs_review") for item in items)
    ):
        parsed["row_verification"] = verify_drpp_rows_high_res(items, summaries, printed_total)
        parsed_total = sum((_money(item.get("jumlah")) for item in items), Decimal("0"))
        parsed["metadata"]["total"] = parsed_total
        parsed["metadata"]["total_valid"] = parsed_total == printed_total
    # Total cetak hanya alat rekonsiliasi. Nominal baris tidak boleh dibuat
    # seimbang dengan mengisi selisih ke baris terakhir tanpa bukti sumber.
    if printed_total > 0 and parsed_total != printed_total:
        parsed["metadata"]["total_valid"] = False
        parsed.setdefault("warnings", []).append(
            "Total item tidak sama dengan total cetak DRPP; nominal tidak dikoreksi otomatis."
        )
    for item in parsed.get("items", []):
        item["no_drpp"] = parsed["metadata"].get("nomor_drpp_full") or parsed["metadata"].get("nomor_drpp")
    return parsed


def _parse_drpp_header(text):
    upper = " ".join(str(text or "").upper().split())

    def value(pattern):
        match = re.search(pattern, upper, re.I)
        return match.group(1).strip(" .,:;|-") if match else ""

    tahun = value(r"(?:TAHUN\s+ANGGARAN|TAHUN)\s*[:\-]?\s*(20\d{2})")
    bulan = value(r"\bBULAN\s*[:\-]?\s*([A-Z]+)")
    total = extract_drpp_printed_total(upper)
    pagu = value(r"PAGU\s+(?:OUTPUT|RO)\D{0,20}(\d{1,3}(?:[.,]\d{3})+)")
    nomor_drpp_bare = _drpp_number_from_text(upper)
    satker = value(r"(?:KODE\s+)?SATUAN\s+KERJA\s*[:\-]?\s*(\d{4,8})") or value(r"(?:KODE\s+SATKER|SATKER)\s*[:\-]?\s*(\d{4,8})")
    # Construct full canonical DRPP number
    nomor_drpp_full = f"{nomor_drpp_bare}/DRPP/{satker}/{tahun}" if nomor_drpp_bare and satker and tahun else nomor_drpp_bare
    return {
        "nomor_drpp": nomor_drpp_bare,
        "nomor_drpp_full": nomor_drpp_full,
        "tanggal_drpp": value(r"TANGGAL\s+DRPP\s*[:\-]?\s*([0-3]?\d[\-/ ][A-Z0-9]+[\-/ ]20\d{2})"),
        "satker_code": satker,
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
    full = re.search(r"(\d{3,6})\s*/\s*KW\s*/\s*([0-9\s]{5,12})\s*/\s*(20\d{2})", text)
    if full:
        satker = re.sub(r"\s+", "", full.group(2))
        return f"{full.group(1).zfill(5)}/KW/{satker}/{full.group(3)}"
    short = re.search(r"\d{1,6}", text)
    return short.group(0).zfill(5) if short else ""


def _receipt_number(text):
    match = re.search(
        r"(?:NO\.?\s*KUITANSI|NOMOR)\s*[:\-]?\s*(\d{3,6})\s*/\s*KW\s*/\s*([0-9\s]{5,12})\s*/\s*(20\d{2})",
        str(text or ""),
        re.I,
    )
    if not match:
        match = re.search(r"\b(\d{3,6})\s*/\s*KW\s*/\s*([0-9\s]{5,12})\s*/\s*(20\d{2})\b", str(text or ""), re.I)
    if not match:
        return ""
    satker = re.sub(r"\s+", "", match.group(2))
    return f"{match.group(1).zfill(5)}/KW/{satker}/{match.group(3)}"


def _kw_short(value):
    normalized = _normalize_kw(value)
    return normalized.split("/", 1)[0] if normalized else ""


def _text_has_labeled_kw_short(text, short):
    if not short:
        return False
    number = re.escape(short.lstrip("0") or short)
    return bool(re.search(
        rf"\b(?:NO\.?\s*KUITANSI|NOMOR\s*KUITANSI|KUITANSI|KW|NO\.?)\D{{0,25}}0*{number}\b",
        str(text or ""),
        re.I,
    ))


def _labeled_money(text, labels):
    label_pattern = "|".join(labels)
    normalized = (
        str(text or "")
        .upper()
        .replace("JUMTAH", "JUMLAH")
        .replace("JUMIAH", "JUMLAH")
        .replace("JUM1AH", "JUMLAH")
    )
    match = re.search(
        rf"(?:{label_pattern})\s*[:\-]?\s*(?:RP\.?\s*)?([0-9OIL]{{1,3}}(?:[.,][0-9OIL]{{3}})*(?:[.,][0-9OIL]{{2}})?)(?![0-9OIL])",
        normalized,
        re.I,
    )
    if not match:
        return None
    value = match.group(1).replace("O", "0").replace("I", "1").replace("L", "1")
    return _money(value)


def _money_after_account(text, account):
    account = str(account or "").strip()
    if not account:
        return None
    money_pattern = r"[0-9OIL]{1,3}(?:[.,][0-9OIL]{3})*(?:[.,][0-9OIL]{2})?(?![0-9OIL])"
    match = re.search(
        rf"\b{re.escape(account)}\b\D{{0,40}}(?:RP\.?\s*)?({money_pattern})",
        str(text or "").upper(),
        re.I,
    )
    if not match:
        return None
    return _money(match.group(1).replace("O", "0").replace("I", "1").replace("L", "1"))


def _receipt_description(text):
    normalized = " ".join(str(text or "").split())
    match = re.search(
        r"(?:UNTUK\s+PEMBAYARAN|URAIAN|KEPERLUAN)\s*[:\-]?\s*(.*?)"
        r"(?=\s+(?:JUMLAH\s+BRUTO|NILAI\s+BRUTO|NILAI\s+NETTO|PPh(?:\s+PASAL)?\s*21|PAJAK|PENERIMA|BENDAHARA|TANDA\s+TANGAN|CAP|HALAMAN|TIDAK\s+BERLAKU|LAMPIRAN)\b|$)",
        normalized,
        re.I,
    )
    return match.group(1).strip(" .,:;|-") if match else ""


def _receipt_item_from_page(page):
    """Bangun kandidat item tanpa menganggap kuitansi sebagai total DRPP."""
    text = str(page.get("text") or "")
    no_bukti = _receipt_number(text)
    bruto = _labeled_money(
        text,
        (r"NILAI\s+BRUTO", r"JUMLAH\s+BRUTO", r"JUMLAH\s+PENGELUARAN", r"\bBRUTO\b"),
    )
    if not no_bukti or bruto is None or bruto <= 0:
        return None
    account_match = re.search(r"\bAKUN\s*[:\-]?\s*(5\d{5})\b", text, re.I)
    charges = set(re.findall(r"\b\d{4}\.[A-Z]{3}\.\d{3}\.\d{3}\.5\d{5}\b", text.upper()))
    account = account_match.group(1) if account_match else ""
    if not account and len(charges) == 1:
        account = next(iter(charges)).rsplit(".", 1)[-1]
    netto = _labeled_money(
        text,
        (r"NILAI\s+NETTO", r"JUMLAH\s+DIBAYAR", r"JUMLAH\s+BERSIH", r"YANG\s+DIBAYARKAN", r"\bNETTO\b"),
    )
    pph21 = _labeled_money(
        text,
        (r"PPH\s*(?:PASAL\s*)?21", r"JUMLAH\s+POTONGAN", r"\bPOTONGAN\b"),
    ) or Decimal("0")
    fp = _labeled_money(text, (r"\bFP\b",)) or Decimal("0")
    return {
        "no_bukti": no_bukti,
        "akun": account,
        "jumlah": bruto,
        "bruto": bruto,
        "netto": netto if netto is not None else max(bruto - pph21 - fp, Decimal("0")),
        "fp": fp,
        "pph21": pph21,
        "pembebanan": next((value for value in charges if value.endswith(f".{account}")), ""),
        "keperluan": _receipt_description(text),
        "source_page": page.get("page_number"),
        "method": "receipt_recovery",
        "needs_review": True,
        "review_fields": ["drpp_summary_missing"],
        "warnings": ["Item dibangun dari kuitansi; total referensi DRPP tetap wajib diverifikasi."],
        "status": "Perlu Review",
    }


def _receipt_page_score(item, page):
    if page.get("document_type") not in KW_PAGE_TYPES and page.get("document_type") != "SUPPORT_DOCUMENT":
        return 0
    text = str(page.get("text") or "")
    receipt = _receipt_number(text)
    item_kw = _normalize_kw(item.get("no_bukti"))
    score = 6 if receipt and receipt == item_kw else 0
    if not score and _text_has_labeled_kw_short(text, _kw_short(item_kw)):
        score += 3
    item_account = str(item.get("akun") or "")
    account_match = re.search(r"\bAKUN\s*[:\-]?\s*(5\d{5})\b", text, re.I)
    if account_match and account_match.group(1) == item_account:
        score += 2
    elif item_account and re.search(rf"\b{re.escape(item_account)}\b", text):
        score += 2
    bruto = _labeled_money(
        text,
        (r"NILAI\s+BRUTO", r"JUMLAH\s+BRUTO", r"JUMLAH\s+PENGELUARAN", r"\bBRUTO\b"),
    )
    if bruto is None:
        bruto = _money_after_account(text, item_account)
    if bruto is not None and bruto > 0 and bruto == _money(item.get("bruto") or item.get("jumlah")):
        score += 2
    return score


def _matching_receipt_pages(item, pages):
    scored = [(page, _receipt_page_score(item, page)) for page in pages]
    exact = [page for page, score in scored if score >= 6 and _receipt_number(page.get("text")) == _normalize_kw(item.get("no_bukti"))]
    if exact:
        return exact
    best = max((score for _, score in scored), default=0)
    candidates = [page for page, score in scored if score == best]
    return candidates if best >= 4 and len(candidates) == 1 else []


def parse_kw_support(items, pages, year=""):
    for item in items:
        item["no_bukti"] = _normalize_kw(item.get("no_bukti"))
        candidates = _matching_receipt_pages(item, pages)
        if not candidates:
            continue
        text = "\n".join(page.get("text", "") for page in candidates)
        upper = text.upper()
        fp = re.search(r"(?:NOMOR\s+SERI\s+FAKTUR\s+PAJAK|FAKTUR\s+PAJAK)\s*[:\-]?\s*([0-9.\-]{10,25})", upper)
        fp_amount = _labeled_money(upper, (r"\bFP\b",))
        pph21 = _labeled_money(
            upper,
            (r"PPH\s*(?:PASAL\s*)?21", r"JUMLAH\s+POTONGAN", r"\bPOTONGAN\b"),
        )
        bruto = _labeled_money(
            upper,
            (r"NILAI\s+BRUTO", r"JUMLAH\s+BRUTO", r"JUMLAH\s+PENGELUARAN", r"\bBRUTO\b"),
        )
        if bruto is None:
            bruto = _money_after_account(upper, item.get("akun"))
        netto = _labeled_money(
            upper,
            (r"NILAI\s+NETTO", r"JUMLAH\s+DIBAYAR", r"JUMLAH\s+BERSIH", r"YANG\s+DIBAYARKAN", r"\bNETTO\b"),
        )
        receipt_numbers = {_receipt_number(page.get("text")) for page in candidates}
        receipt_numbers.discard("")
        if len(receipt_numbers) == 1:
            item["no_bukti"] = next(iter(receipt_numbers))
        charges = set(re.findall(r"\b\d{4}\.[A-Z]{3}\.\d{3}\.\d{3}\.5\d{5}\b", upper))
        account_match = re.search(r"\bAKUN\s*[:\-]?\s*(5\d{5})\b", upper)
        if not account_match and not item.get("akun") and len(charges) == 1:
            item["akun"] = next(iter(charges)).rsplit(".", 1)[-1]
        warnings = item.setdefault("warnings", [])
        if account_match and item.get("akun") and account_match.group(1) != str(item.get("akun")):
            warnings.append("Akun kuitansi berbeda dengan akun tabel DRPP.")
        elif account_match:
            item["akun"] = account_match.group(1)
        if bruto is not None and bruto > 0:
            item["jumlah"] = bruto
            item["bruto"] = bruto
        if fp_amount is not None:
            item["fp"] = fp_amount
        elif fp:
            item["fp"] = fp.group(1)
        if pph21 is not None:
            item["pph21"] = pph21
        if netto is not None:
            item["netto"] = netto
        elif bruto is not None and pph21 is not None:
            item["netto"] = max(bruto - pph21 - (fp_amount or Decimal("0")), Decimal("0"))
        elif pph21 is not None and pph21 > 0:
            item["netto"] = max(_money(item.get("bruto") or item.get("jumlah")) - pph21, Decimal("0"))
        description = _receipt_description(text)
        if description:
            item["keperluan"] = description
            item["deskripsi"] = description
        matching_charges = {value for value in charges if value.endswith(f".{item.get('akun')}")}
        if len(matching_charges) == 1:
            item["pembebanan"] = next(iter(matching_charges))
        elif charges:
            warnings.append(
                "Pembebanan kuitansi konflik atau akun terakhir tidak cocok dengan akun transaksi."
            )
        item["source_pages"] = {
            page["document_type"]: {"file_name": page["file_name"], "page_number": page["page_number"]}
            for page in candidates
        }
    return items


def _recovery_page_key(page):
    """Identitas halaman stabil selama satu upload, terpisah dari perceptual hash."""
    return (
        str(page.get("file_sha256") or page.get("_path") or page.get("file_name") or ""),
        int(page.get("page_number") or 0),
        str(page.get("page_content_hash") or ""),
    )


def _recover_missing_candidate_pages(
    drpps,
    page_index,
    ocr=True,
    processed_page_keys=None,
    diagnostics=None,
):
    """OCR kandidat lanjutan hanya ketika bukti sumber belum rekonsiliasi."""
    if not ocr:
        return False
    processed_page_keys = processed_page_keys if processed_page_keys is not None else set()
    diagnostics = diagnostics if diagnostics is not None else {}
    for key in (
        "recovery_pages_considered",
        "recovery_pages_ocr",
        "recovery_pages_skipped_processed",
    ):
        diagnostics.setdefault(key, 0)
    structural_page_found = False
    for drpp in drpps:
        meta = drpp.get("metadata", {})
        items = drpp.setdefault("items", [])
        number = str(meta.get("nomor_drpp") or "")
        target_files = {
            source.get("file_name")
            for source in drpp.get("source_pages") or []
            if source.get("file_name")
        }
        if not target_files:
            target_files = {
                page.get("file_name") for page in page_index
                if number and number in {page.get("drpp_hint"), page.get("drpp_detected")}
            }
        group_pages = [page for page in page_index if page.get("file_name") in target_files]
        recover_from_receipts = not items
        source_page_numbers = [
            int(source.get("page_number") or 0)
            for source in drpp.get("source_pages") or []
            if source.get("page_number")
        ]
        first_source_page = min(source_page_numbers, default=0)
        receipt_recovery_window = max(1, int(os.getenv("DRPP_RECEIPT_RECOVERY_PAGE_WINDOW", "8")))

        def cache_identity_collides(page):
            page_hash = page.get("page_hash")
            content_hashes = {
                candidate.get("page_content_hash")
                for candidate in group_pages
                if page_hash and candidate.get("page_hash") == page_hash
            }
            content_hashes.discard(None)
            content_hashes.discard("")
            return len(content_hashes) > 1

        def unresolved():
            printed = _money(meta.get("printed_total"))
            parsed = sum((_money(item.get("bruto") or item.get("jumlah")) for item in items), Decimal("0"))
            source_count = int(meta.get("source_item_count") or len(items))
            missing_receipts = [item for item in items if not _matching_receipt_pages(item, group_pages)]
            unread_receipt_candidates = any(
                page.get("is_representative") is not False
                and int(page.get("page_number") or 0) > first_source_page
                and int(page.get("page_number") or 0) <= first_source_page + receipt_recovery_window
                and page.get("type_hint") in {"SPM", "KUITANSI", "DRPP_SUMMARY"}
                and page.get("document_type") not in {"SPM", "SPP", "SP2D", "DRPP_SUMMARY", "DRPP_COA"}
                and not str(page.get("text") or page.get("native_text") or "").strip()
                for page in group_pages
            )
            rows_complete = (
                bool(items)
                and source_count <= len(items)
                and not (missing_receipts and unread_receipt_candidates)
                and all(
                    _normalize_kw(item.get("no_bukti") or item.get("no_kuitansi"))
                    and item.get("akun")
                    and _money(item.get("bruto") or item.get("jumlah")) > 0
                    for item in items
                )
            )
            if rows_complete:
                return False
            is_balanced = printed > 0 and parsed == printed and source_count <= len(items)
            if is_balanced and not missing_receipts:
                return False

            return (
                printed <= 0
                or parsed != printed
                or source_count > len(items)
                or bool(missing_receipts)
            )

        if not unresolved():
            continue
        for page in sorted(group_pages, key=lambda item: item.get("page_number", 0)):
            if page.get("is_representative") is False:
                continue
            if items and int(page.get("page_number") or 0) <= first_source_page:
                continue
            if items and int(page.get("page_number") or 0) > first_source_page + receipt_recovery_window:
                continue
            if items and page.get("type_hint") not in {"SPM", "KUITANSI", "DRPP_SUMMARY"}:
                continue
            if items and page.get("document_type") in {"SPM", "SPP", "SP2D", "DRPP_SUMMARY", "DRPP_COA"}:
                continue
            page_key = _recovery_page_key(page)
            if page_key in processed_page_keys:
                diagnostics["recovery_pages_considered"] += 1
                diagnostics["recovery_pages_skipped_processed"] += 1
                continue
            if str(page.get("text") or page.get("native_text") or "").strip():
                continue
            diagnostics["recovery_pages_considered"] += 1
            processed_page_keys.add(page_key)
            diagnostics["recovery_pages_ocr"] += 1
            started = time.monotonic()
            # Cache global tetap memakai kontrak lama. Pada recovery saja,
            # bypass hasil bila satu perceptual hash mewakili konten halaman
            # yang berbeda agar kuitansi berikutnya tidak memakai teks halaman
            # sebelumnya.
            try:
                use_recovery_cache = not cache_identity_collides(page)
                if items and not str(page.get("text") or page.get("native_text") or "").strip():
                    use_recovery_cache = False
                if use_recovery_cache:
                    cached_recovery = _load_page_cache(page, "tesseract-ind+eng-v3")
                    if cached_recovery and (
                        cached_recovery.get("cache_empty")
                        or not str(cached_recovery.get("text") or "").strip()
                    ):
                        use_recovery_cache = False
                recovery_rotations = (0,) if items else None
                recovery_dpi = 180 if items else 220
                recovery_timeout = int(os.getenv("OCR_RECEIPT_RECOVERY_TIMEOUT_SECONDS", "3")) if items else None
                recovery_configs = ("--psm 6",) if items else None
                recovery_langs = ("eng", "ind+eng", "") if items else None
                result = _ocr_page(
                    page,
                    use_cache=use_recovery_cache,
                    rotations=recovery_rotations,
                    dpi=recovery_dpi,
                    timeout=recovery_timeout,
                    configs=recovery_configs,
                    lang_attempts=recovery_langs,
                )
            except Exception as exc:
                page["ocr_duration"] = time.monotonic() - started
                page["ocr_called"] = True
                page["cache_hit"] = False
                page["engine"] = "tesseract"
                page["ocr_warnings"] = [f"Recovery OCR gagal ({type(exc).__name__})."]
                continue
            page["ocr_duration"] = time.monotonic() - started
            page["text"] = result.get("text", "")
            page["ocr_called"] = not result.get("cache_hit", False)
            page["cache_hit"] = bool(result.get("cache_hit"))
            page["engine"] = result.get("engine", "tesseract")
            page["tsv_words"] = result.get("words", [])
            page["rotation"] = result.get("rotation", 0)
            page["ocr_warnings"] = result.get("warnings", [])
            page["document_type"], page["confidence"], page["evidence"] = _classification(page["text"])
            detected = _drpp_number_from_text(page["text"])
            if detected:
                page["drpp_detected"] = detected
            if page["document_type"] in {"DRPP_SUMMARY", "DRPP_COA"}:
                structural_page_found = True
            if items and (page["document_type"] in KW_PAGE_TYPES or page["document_type"] == "SUPPORT_DOCUMENT"):
                parse_kw_support(items, [page], year=str(meta.get("tahun") or ""))
            if recover_from_receipts and page["document_type"] in KW_PAGE_TYPES:
                recovered_item = _receipt_item_from_page(page)
                if recovered_item and recovered_item["no_bukti"] not in {
                    item.get("no_bukti") for item in items
                }:
                    items.append(recovered_item)
                    meta["source_item_count"] = len(items)
            if not unresolved():
                break
    return structural_page_found


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


def _normalize_date(date_val):
    if not date_val:
        return None
    if isinstance(date_val, date):
        return date_val
    if isinstance(date_val, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_val.strip()):
        try:
            return date.fromisoformat(date_val.strip())
        except ValueError:
            return None
    return date_val


def _apply_group_date_consensus(drpps, spm):
    """Isi tanggal kosong hanya bila semua tanggal transaksi yang terbaca sama."""
    spm_meta = (spm or {}).get("metadata", {})
    if _normalize_date(spm_meta.get("tanggal_spm")):
        return
    candidates = []
    for drpp in drpps:
        meta = drpp.get("metadata", {})
        values = [meta.get("tanggal_spm")]
        values.extend(
            item.get("tanggal_spm") or item.get("tanggal_bukti")
            for item in drpp.get("items", [])
        )
        candidates.extend(parsed for value in values if (parsed := parse_date(value)))
    distinct = set(candidates)
    if len(distinct) != 1:
        return
    consensus = distinct.pop()
    if spm is not None:
        spm_meta["tanggal_spm"] = consensus
    for drpp in drpps:
        drpp.setdefault("metadata", {}).setdefault("tanggal_spm", consensus)

def _determine_cara_pembayaran(jenis_spm):
    jenis = str(jenis_spm or "").upper()
    if any(k in jenis for k in ["GUP", "KKP", "UP", "TUP", "NIHIL"]):
        return "UP/TUP"
    if "LS" in jenis:
        if "NON" in jenis:
            return "LS Non Kontraktual"
        return "LS Kontraktual"
    return ""
def _exact_sp2d(number, satker="", year=None):
    if not number or not satker or not year:
        return None
    from apps.sp2d.models import SP2DRaw

    query = SP2DRaw.objects.filter(nomor_spm_extracted__iexact=number)
    query = query.filter(satker_code=satker)
    query = query.filter(
        Q(import_batch__tahun=year)
        | Q(tgl_sp2d__year=year)
        | Q(tanggal_selesai_sp2d__year=year)
    )
    results = list(query[:2])
    if len(results) == 1:
        return results[0]
    return None


def resolve_spm_parent(drpps, page_index):
    metas = [drpp.get("metadata", {}) for drpp in drpps if drpp]
    number = next((str(meta.get("nomor_spm") or "").strip().upper() for meta in metas if meta.get("nomor_spm")), "")
    satker = next((str(meta.get("satker_code") or "").strip() for meta in metas if meta.get("satker_code")), "")
    year = next((meta.get("tahun") for meta in metas if meta.get("tahun")), None)
    
    # Identitas berlabel pada halaman SPM lebih kuat daripada metadata DRPP,
    # database fallback, dan nama arsip/member.
    for candidate in page_index:
        if candidate.get("document_type") == "SPM":
            identity_cache_engine = "spm-detail-v3"
            spm = _load_page_cache(candidate, identity_cache_engine)
            if not spm:
                summary_boundaries = [
                    page.get("page_number")
                    for page in page_index
                    if page.get("file_name") == candidate.get("file_name")
                    and page.get("document_type") == "DRPP_SUMMARY"
                ]
                first_summary_page = min(summary_boundaries) if summary_boundaries else None
                identity_pages = [
                    page
                    for page in page_index
                    if page.get("is_representative", True)
                    and page.get("file_name") == candidate.get("file_name")
                    and (
                        page.get("document_type") in {"SPM", "SPP", "SP2D"}
                        or (first_summary_page and page.get("page_number", 0) < first_summary_page)
                    )
                ]
                extracted = _extracted_from_pages(identity_pages or [candidate])
                spm = parse_spm_pdf(
                    file_path=candidate.get("_path") or candidate["file_name"], 
                    ocr=False, 
                    extracted=extracted, 
                    parse_details=False
                )
                _save_page_cache(candidate, identity_cache_engine, spm)
            
            if spm:
                spm_meta = spm.get("metadata", {})
                detected = str(spm_meta.get("nomor_spm") or "").strip().upper()
                spm_meta["tanggal_spm"] = _normalize_date(spm_meta.get("tanggal_spm"))
                spm_meta["cara_pembayaran"] = _determine_cara_pembayaran(spm_meta.get("jenis_spm"))
                
                sp2d = _exact_sp2d(detected, satker, year)
                if sp2d:
                    return _spm_from_sp2d(sp2d), sp2d
                
                spm_meta["bulan_sp2d"] = None # explicitly clear if no SP2D found
                return spm, None

    if number:
        sp2d = _exact_sp2d(number, satker, year)
        if sp2d:
            return _spm_from_sp2d(sp2d), sp2d

        query = SP2DRaw.objects.filter(nomor_spm_extracted__iexact=number)
        if satker:
            query = query.filter(satker_code=satker)
        existing = query.first() if query.count() == 1 else None
        if existing:
            return {
                "file_name": "DATABASE",
                "status": "parsed_text",
                "method": "transaction_database",
                "confidence": None,
                "metadata": {
                    "nomor_spm": existing.nomor_spm_extracted,
                    "tanggal_spm": _normalize_date(existing.tgl_sp2d or existing.tanggal_selesai_sp2d),
                    "jenis_spm": existing.jenis_spm,
                    "kppn": "",
                    "supplier": "",
                    "bank": "",
                    "rekening": "",
                    "total_pembayaran": Decimal("0"),
                    "tanggal_sp2d": None,
                    "bulan_sp2d": existing.bulan_sp2d,
                },
                "sp2d_raw_id": existing.id,
            }, existing

    return None, None


def _populate_drpp_metadata(drpp, spm, sp2d):
    meta = drpp.get("metadata", {})
    spm_meta = (spm or {}).get("metadata", {})
    sp2d_meta = sp2d if isinstance(sp2d, dict) else {}
    
    # Use canonical keys
    updates = {
        "satker": meta.get("satker_code") or spm_meta.get("satker_code"),
        "tahun": meta.get("tahun") or (str(spm_meta.get("tanggal_spm"))[:4] if spm_meta.get("tanggal_spm") else ""),
        "bulan_sp2d": spm_meta.get("bulan_sp2d"), # Exact match from SP2D
        "cara_pembayaran": spm_meta.get("cara_pembayaran") or _determine_cara_pembayaran(spm_meta.get("jenis_spm")),
        "nomor_spm": spm_meta.get("nomor_spm") or meta.get("nomor_spm") or "",
        "tanggal_spm": spm_meta.get("tanggal_spm") or meta.get("tanggal_spm"),
        "jenis_spm": spm_meta.get("jenis_spm") or "",
    }
    
    for key, value in updates.items():
        if value:
            meta[key] = value
            
    # Also propagate to all items
    for item in drpp.get("items", []):
        for key in ["nomor_spm", "tanggal_spm", "jenis_spm", "cara_pembayaran", "bulan_sp2d", "satker", "tahun"]:
            if meta.get(key) and not item.get(key):
                item[key] = meta[key]


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
                "bulan_sp2d": spm_meta.get("bulan_sp2d") or getattr(spm_meta.get("tanggal_sp2d"), "month", ""),
                "cara_pembayaran": "UP/TUP" if str(spm_meta.get("jenis_spm") or "").upper() in {"GU", "GUP", "TUP"} else ("LS" if str(spm_meta.get("jenis_spm") or "").upper().startswith("LS") else ""),
                "nomor_spm": spm_meta.get("nomor_spm") or meta.get("nomor_spm") or "",
                "tanggal_spm": spm_meta.get("tanggal_spm") or meta.get("tanggal_spm"),
                "jenis_spm": spm_meta.get("jenis_spm") or "",
                "no_bukti": no_kw,
                "no_kuitansi": no_kw,
                "no_drpp": meta.get("nomor_drpp_full") or meta.get("nomor_drpp") or "",
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


def _layout_lines(words, tolerance=16):
    """Kelompokkan word-level OCR berdasarkan posisi baris, bukan urutan teks."""
    normalized = []
    for raw in words or []:
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        try:
            top = int(float(raw.get("top", 0)))
            left = int(float(raw.get("left", 0)))
            width = int(float(raw.get("width", 0)))
            height = int(float(raw.get("height", 0)))
        except (TypeError, ValueError):
            continue
        normalized.append({**raw, "text": text, "top": top, "left": left, "width": width, "height": height})
    lines = []
    for word in sorted(normalized, key=lambda item: (item["top"] + item["height"] // 2, item["left"])):
        center = word["top"] + word["height"] // 2
        line = next((candidate for candidate in reversed(lines[-6:]) if abs(candidate["center"] - center) <= tolerance), None)
        if line is None:
            line = {"center": center, "words": []}
            lines.append(line)
        line["words"].append(word)
        line["center"] = round(
            sum(item["top"] + item["height"] // 2 for item in line["words"]) / len(line["words"])
        )
    for line in lines:
        line["words"].sort(key=lambda item: item["left"])
        line["text"] = " ".join(item["text"] for item in line["words"])
    return lines


_KKP_MONEY_RE = re.compile(
    r"(?<![0-9A-Z.])(?:RP\.?\s*)?("
    r"(?:[0-9S§IL|]{1,3}(?:[.\s][0-9S§IL|]{3})+(?:,[0-9]{2})?)"
    r"|(?:[0-9S§IL|]{4,12}(?:,[0-9]{2})?)"
    r")(?![0-9A-Z.])",
    re.I,
)


def _kkp_money_evidence(value):
    output = []
    for match in _KKP_MONEY_RE.finditer(str(value or "")):
        raw = match.group(0)
        numeric = re.sub(r"^RP\.?\s*", "", raw, flags=re.I)
        numeric = numeric.upper().translate(
            str.maketrans({"S": "5", "§": "5", "I": "1", "L": "1", "|": "1"})
        )
        amount = parse_decimal(numeric)
        if amount > 0:
            output.append(
                {
                    "raw_token": raw,
                    "normalized_value": amount,
                    "start": match.start(),
                    "end": match.end(),
                }
            )
    return output


def _kkp_money_tokens(value):
    return [item["normalized_value"] for item in _kkp_money_evidence(value)]


def _page_source(page, method, locator="", inputs=None):
    page = page or {}
    return {
        "source": "OCR" if page.get("engine") and page.get("engine") != "native_pdf" else "PARSER_STRUCTURAL",
        "engine": page.get("engine") or ("native_pdf" if page.get("native_text") else ""),
        "extraction_method": method,
        # page["confidence"] adalah confidence classifier, bukan confidence OCR
        # per-field yang sah, sehingga provenance tidak boleh mengarang nilainya.
        "confidence": None,
        "source_file": page.get("file_name") or "",
        "source_page": page.get("page_number"),
        "document_type": page.get("document_type") or "",
        "locator": locator,
        "inputs": list(inputs or []),
    }


def _kkp_total_provenance(page, method, raw_token, normalized_value, *, inputs=None):
    return {
        **_page_source(page, method, "total", inputs=inputs),
        "method": method,
        "raw_token": raw_token,
        "normalized_value": normalized_value,
        "suspect": False,
    }


def _sanitize_kkp_description(value):
    text = str(value or "")
    text = re.sub(r"\bNON\s*[.|$\-–—]+\s*[A-Z]\b", " ", text, flags=re.I)
    text = re.sub(r"[$|]+", " ", text)
    text = re.sub(r"(?:\s*[–—_-]\s*){2,}", " ", text)
    tokens = []
    for token in text.split():
        cleaned = token.strip(".,;:!?()[]{}<>–—_-|$")
        if not cleaned or re.fullmatch(r"[^A-Za-z0-9]+", cleaned):
            continue
        if len(cleaned) == 1 and cleaned.isalpha() and cleaned.isupper():
            continue
        tokens.append(cleaned)
    return " ".join(tokens)




def _kkp_payment_rows(page):
    """Baca baris tabel pembayaran KKP dari koordinat TSV."""
    words = page.get("tsv_words") or []
    lines = _layout_lines(words)
    if not lines:
        return []
    header_lines = [
        line for line in lines
        if "PEMBAYARAN" in line["text"].upper() and (
            "AKUN" in line["text"].upper() or "KODE" in line["text"].upper()
        )
    ]
    header_y = max((line["center"] for line in header_lines), default=0)
    right_edge = max((word["left"] + word["width"] for word in words), default=0)
    ordinal_words = [
        word for word in words
        if re.fullmatch(r"\d{1,2}", word["text"])
        and word["left"] <= right_edge * 0.2
        and word["top"] + word["height"] // 2 > header_y
    ]
    ordinal_words.sort(key=lambda word: word["top"] + word["height"] // 2)
    rows = []
    for index, ordinal in enumerate(ordinal_words):
        center = ordinal["top"] + ordinal["height"] // 2
        lower = (header_y + center) / 2 if index == 0 else (
            ordinal_words[index - 1]["top"] + ordinal_words[index - 1]["height"] // 2 + center
        ) / 2
        upper = float("inf") if index + 1 == len(ordinal_words) else (
            center + ordinal_words[index + 1]["top"] + ordinal_words[index + 1]["height"] // 2
        ) / 2
        row_words = [
            word for word in words
            if lower <= word["top"] + word["height"] // 2 < upper
        ]
        row_text = " ".join(word["text"] for word in sorted(row_words, key=lambda item: (item["top"], item["left"])))
        accounts = re.findall(r"\b5\d{5}\b", row_text)
        right_amounts = [
            (
                abs(word["top"] + word["height"] // 2 - center),
                -(word["left"] + word["width"]),
                evidence,
            )
            for word in row_words
            if word["left"] >= right_edge * 0.72
            for evidence in _kkp_money_evidence(word["text"])
            if evidence["normalized_value"] >= Decimal("10000")
        ]
        if not accounts or not right_amounts:
            continue
        description_words = [
            word["text"] for word in row_words
            if right_edge * 0.48 <= word["left"] < right_edge * 0.72
            and not re.fullmatch(r"(?:5\d{5}|\d{4})", word["text"])
        ]
        raw_description = " ".join(description_words).strip(" -|")
        description = _sanitize_kkp_description(raw_description)
        amount_evidence = min(right_amounts, key=lambda item: (item[0], item[1]))[2]
        rows.append(
            {
                "ordinal": int(ordinal["text"]),
                "akun": accounts[-1],
                "jumlah": amount_evidence["normalized_value"],
                "raw_amount": amount_evidence["raw_token"],
                "keperluan": description,
                "raw_keperluan": raw_description,
                "source_page": page.get("page_number"),
                "source_file": page.get("file_name"),
                "source_locator": f"table-row:{ordinal['text']}:y:{center}",
            }
        )
    return rows


def _kkp_statement_amounts(page):
    """Ambil transaksi tagihan dan netralkan baris reversal/pembayaran."""
    lines = _layout_lines(page.get("tsv_words") or [])
    if not lines:
        lines = [{"text": line, "center": index, "words": []} for index, line in enumerate(str(page.get("text") or "").splitlines())]
    transaction_right_edge = None
    for line in lines:
        if "INFORMASI KREDIT" not in line["text"].upper():
            continue
        info_word = next(
            (word for word in line["words"] if word["text"].upper().startswith("INFORMASI")),
            None,
        )
        if info_word:
            transaction_right_edge = info_word["left"]
            break
    charges = []
    credits = Counter()
    for line in lines:
        upper = line["text"].upper()
        if "TOTAL" in upper or "TAGIHAN BULAN" in upper or "PEMBAYARAN MINIMUM" in upper:
            continue
        positioned_amounts = [
            (word["left"] + word["width"], item)
            for word in line["words"]
            if transaction_right_edge is None or word["left"] < transaction_right_edge
            for item in _kkp_money_evidence(word["text"])
            if item["normalized_value"] >= Decimal("10000")
        ]
        if positioned_amounts:
            amounts = [max(positioned_amounts, key=lambda item: item[0])[1]]
        else:
            amounts = [
                item for item in _kkp_money_evidence(line["text"])
                if item["normalized_value"] >= Decimal("10000")
            ]
        if not amounts:
            continue
        if "PEMBAYARAN" in upper or re.search(r"\bCR\b", upper):
            credits[amounts[-1]["normalized_value"]] += 1
            continue
        if len(re.findall(r"\b\d{1,2}[-/]\d{1,2}[-/]20\d{2}\b", line["text"])) >= 1:
            charges.append(
                {
                    "amount": amounts[-1]["normalized_value"],
                    "raw_amount": amounts[-1]["raw_token"],
                    "line": line,
                }
            )
    remaining = []
    for charge in charges:
        if credits[charge["amount"]] > 0:
            credits[charge["amount"]] -= 1
        else:
            remaining.append(charge)
    return remaining


def _kkp_coa_evidence(pages, account):
    candidates = []
    for page in pages:
        text = str(page.get("text") or "")
        if account not in text or "COA" not in text.upper():
            continue
        compact = compact_pembebanan_from_coa(text, account)
        if compact:
            candidates.append((compact, page))
    distinct = {value for value, _page in candidates}
    if len(distinct) == 1:
        value = next(iter(distinct))
        return value, next(page for candidate, page in candidates if candidate == value)
    return "", None


def _kkp_detail_total_evidence(pages):
    """Jumlah lampiran COA per jenis dokumen; halaman BAST tidak ikut voting."""
    grouped = defaultdict(list)
    seen = defaultdict(set)
    for page in pages:
        text = str(page.get("text") or "")
        upper = text.upper()
        if "COA" not in upper:
            continue
        if re.search(r"LAMPIRAN\s+(?:SURAT\s+)?PERINTAH\s+MEMBAYAR", upper):
            source_name = "SPM_DETAIL"
        elif re.search(r"LAMPIRAN\s+(?:SURAT\s+)?PERMINTAAN\s+PEMBAYARAN", upper):
            source_name = "SPP_DETAIL"
        else:
            continue
        page_fingerprint = page.get("page_hash") or page.get("page_content_hash") or hashlib.sha256(
            " ".join(text.split()).encode("utf-8", errors="ignore")
        ).hexdigest()
        if page_fingerprint in seen[source_name]:
            continue
        seen[source_name].add(page_fingerprint)
        amounts = [
            item for item in _kkp_money_evidence(text)
            if re.search(r"[.,\s]", item["raw_token"])
            and item["normalized_value"] >= Decimal("1000")
        ]
        if amounts:
            grouped[source_name].append((page, amounts))

    output = {}
    for source_name, page_rows in grouped.items():
        page = page_rows[0][0]
        amounts = [item for _source_page, items in page_rows for item in items]
        total = sum((item["normalized_value"] for item in amounts), Decimal("0"))
        output[source_name] = {
            "value": total,
            "amounts": [item["normalized_value"] for item in amounts],
            "amount_evidence": amounts,
            "page": page,
            "provenance": _kkp_total_provenance(
                page,
                "detail_coa_sum",
                " + ".join(item["raw_token"] for item in amounts),
                total,
                inputs=[str(source_page.get("page_number") or "") for source_page, _items in page_rows],
            ),
        }
    return output


def _kkp_coa_description(page, amount):
    text = str((page or {}).get("text") or "")
    for evidence in _kkp_money_evidence(text):
        if evidence["normalized_value"] != amount:
            continue
        prefix = text[max(0, evidence["start"] - 240):evidence["start"]]
        parts = re.split(
            r"\b\d{3}\.\d{3}\.[A-Z0-9.]{3,}\s*[-–—_]?\s*",
            prefix,
            flags=re.I,
        )
        candidate = _sanitize_kkp_description(parts[-1])
        if candidate:
            return candidate, parts[-1]
    return "", ""


def _kkp_spm_header_evidence(pages, spm_meta):
    raw_value = spm_meta.get("jumlah_pengeluaran") or spm_meta.get("total_pembayaran")
    normalized = _money(raw_value)
    page = next(
        (
            item for item in pages
            if item.get("document_type") == "SPM"
            and "LAMPIRAN" not in str(item.get("text") or "").upper()
        ),
        next((item for item in pages if item.get("document_type") == "SPM"), None),
    )
    raw_token = str(raw_value or "")
    if page and normalized > 0:
        upper = str(page.get("text") or "").upper()
        for label in (
            "JUMLAH PENGELUARAN",
            "NILAI PENGELUARAN",
            "NILAI SPM",
            "TOTAL PEMBAYARAN",
            "JUMLAH YANG DIBAYARKAN",
        ):
            position = upper.find(label)
            if position < 0:
                continue
            candidates = _kkp_money_evidence(upper[position + len(label):position + len(label) + 160])
            match = next((item for item in candidates if item["normalized_value"] == normalized), None)
            if match:
                raw_token = match["raw_token"]
                break
    return {
        "value": normalized,
        "page": page,
        "provenance": _kkp_total_provenance(
            page,
            "spm_header_metadata",
            raw_token,
            normalized,
        ),
    }


def _resolve_kkp_canonical_total(candidates):
    votes = defaultdict(list)
    for source_name, evidence in candidates.items():
        value = _money((evidence or {}).get("value"))
        if value > 0:
            votes[value].append(source_name)
    consensus = [
        (value, sources)
        for value, sources in votes.items()
        if len(set(sources)) >= 2
    ]
    if not consensus:
        return Decimal("0"), [], "PERLU_REVIEW"
    strongest = max(len(set(sources)) for _value, sources in consensus)
    winners = [(value, sources) for value, sources in consensus if len(set(sources)) == strongest]
    if len(winners) != 1:
        return Decimal("0"), [], "PERLU_REVIEW"
    value, sources = winners[0]
    return value, list(dict.fromkeys(sources)), "CONSENSUS"


def _kkp_payment_orders(pages):
    output = []
    seen = set()
    for page in pages:
        if page.get("document_type") != "KKP_PAYMENT_ORDER":
            continue
        text = str(page.get("text") or "")
        receipt = re.search(r"\b\d{3,6}/KW/KKP/\d{5,9}/20\d{2}\b", text, re.I)
        account = re.search(
            r"\b(?:(?:KOD(?:E)?|KD)(?:\s+AKUN)?|AKUN)\.?\s*[:\-]?\s*(5\d{5})\b",
            text,
            re.I,
        )
        amount = re.search(r"RP\.?\s*([0-9S§][0-9S§IL|.]+(?:,[0-9]{2})?)", text, re.I)
        if not (receipt and amount):
            continue
        description = re.search(r"\bUNTUK\s*:\s*(.*?)(?=\bATAS\s+DASAR\b|$)", text, re.I | re.S)
        recipient = re.search(r"\bKEPADA\s*[>:]?\s*(.*?)(?=\bUNTUK\s*:|$)", text, re.I | re.S)
        item = {
            "no_kuitansi": receipt.group(0).upper(),
            "akun": account.group(1) if account else "",
            "jumlah": _money(amount.group(1).translate(str.maketrans({"S": "5", "§": "5"}))),
            "description": " ".join((description.group(1) if description else "").split()),
            "recipient": " ".join((recipient.group(1) if recipient else "").split()),
            "page": page,
        }
        key = (item["no_kuitansi"], item["akun"], item["jumlah"])
        if key not in seen:
            output.append(item)
            seen.add(key)
    return output


def _match_kkp_receipts(rows, amounts, orders):
    """Pasangkan kuitansi hanya bila evidence menghasilkan satu kandidat unik."""
    matches = {}
    ambiguous = set()
    for order in orders:
        candidates = [
            index for index, amount in enumerate(amounts)
            if amount == order.get("jumlah") and index not in matches
        ]
        if order.get("akun"):
            candidates = [index for index in candidates if rows[index].get("akun") == order["akun"]]
        if len(candidates) > 1:
            evidence_tokens = _tokens(f"{order.get('description', '')} {order.get('recipient', '')}")
            scores = {
                index: len(evidence_tokens & _tokens(rows[index].get("keperluan")))
                for index in candidates
            }
            best_score = max(scores.values(), default=0)
            best = [index for index, score in scores.items() if score == best_score and score > 0]
            if len(best) == 1:
                candidates = best
        if len(candidates) > 1:
            distances = {
                index: abs(
                    int(order["page"].get("page_number") or 0)
                    - int(rows[index].get("source_page") or 0)
                )
                for index in candidates
            }
            nearest = [index for index, distance in distances.items() if distance == min(distances.values())]
            if len(nearest) == 1:
                candidates = nearest
        if len(candidates) == 1:
            matches[candidates[0]] = order
        elif candidates:
            ambiguous.update(candidates)
    return matches, ambiguous


def evaluate_kkp_group_commitability(reference, items, *, parser_validation=None, extra_errors=None):
    """Validator alternatif KKP; validator DRPP reguler tidak diubah."""
    reference = reference or {}
    metadata = reference.get("metadata") or {}
    expected_count = int(metadata.get("source_item_count") or 0)
    expected_total = _money(metadata.get("printed_total"))
    actual_total = sum(
        (_money(_group_item_value(item, "nilai_bruto", "bruto", "jumlah")) for item in items),
        Decimal("0"),
    )
    errors = []
    if reference.get("reference_type") != "KKP_PAYMENT_LIST":
        errors.append("Daftar pembayaran KKP tidak ditemukan.")
    if not metadata.get("parent_is_gup_kkp"):
        errors.append("Parent SPM belum terbukti GUP-KKP.")
    if expected_count <= 0 or not items:
        errors.append("Item referensi KKP valid tidak ditemukan.")
    if len(items) != expected_count:
        errors.append(f"Jumlah baris hasil ({len(items)}) tidak sama dengan daftar pembayaran KKP ({expected_count}).")
    if expected_total <= 0:
        errors.append("Total referensi KKP tidak ditemukan atau bernilai nol.")
    elif actual_total != expected_total:
        errors.append(f"Total baris Rp{actual_total:,.0f} tidak sama dengan total referensi KKP Rp{expected_total:,.0f}.")
    payment_list_total = _money(metadata.get("payment_list_total"))
    if payment_list_total <= 0 or expected_total != payment_list_total:
        errors.append("Total referensi KKP tidak sama dengan total daftar pembayaran KKP.")
    canonical_total = _money(metadata.get("canonical_total"))
    resolution_sources = set(metadata.get("total_resolution_sources") or [])
    if (
        metadata.get("total_resolution_status") != "CONSENSUS"
        or canonical_total <= 0
        or len(resolution_sources) < 2
    ):
        errors.append("Total KKP belum didukung minimal dua sumber independen.")
    elif expected_total != canonical_total:
        errors.append("Total daftar pembayaran KKP tidak sama dengan total canonical.")
    seen = set()
    for item in items:
        receipt = str(_group_item_value(item, "no_kuitansi", "no_bukti") or "")
        account = str(_group_item_value(item, "akun") or "")
        charge = str(_group_item_value(item, "pembebanan") or "")
        if not receipt and not (
            _group_item_value(item, "receipt_policy") == "not_available_from_source"
            and _group_item_value(item, "receipt_not_available_from_source") is True
        ):
            errors.append("Nomor kuitansi kosong tanpa provenance sumber.")
        if not account:
            errors.append("Akun kosong.")
        if not charge:
            errors.append("Pembebanan kosong.")
        elif "0000" in charge or (account and not charge.endswith(account)):
            errors.append("Pembebanan tidak cocok dengan Akun.")
        if not _group_item_value(item, "nomor_spm"):
            errors.append("Nomor SPM kosong.")
        if not _group_item_value(item, "tanggal_spm"):
            errors.append("Tanggal SPM kosong.")
        if _money(_group_item_value(item, "nilai_bruto", "bruto", "jumlah")) <= 0:
            errors.append("Nilai bruto nol tanpa bukti.")
        status_detail = str(
            _group_item_value(item, "status_detail", "batch_status", "status") or ""
        ).upper()
        if status_detail in {"GAGAL", "PERLU_REVIEW", "PERLU REVIEW"}:
            errors.append("Terdapat field transaksi KKP yang masih perlu review.")
        key = (_group_item_value(item, "nomor_spm"), receipt, account)
        if key in seen:
            errors.append("Duplikat exact key ditemukan dalam upload yang sama.")
        seen.add(key)
    if parser_validation and (
        parser_validation.get("status") != "BALANCE" or parser_validation.get("can_commit") is False
    ):
        errors.extend(parser_validation.get("errors") or ["Validasi parser KKP masih perlu review."])
    errors.extend(extra_errors or [])
    errors = list(dict.fromkeys(errors))
    return {
        "group_key": metadata.get("group_key") or "",
        "no_drpp": "",
        "expected_count": expected_count,
        "parsed_count": len(items),
        "expected_total": expected_total,
        "parsed_total": actual_total,
        "row_count": len(items),
        "expected_row_count": expected_count,
        "total_drpp": expected_total,
        "total_rows": actual_total,
        "status": "BALANCE" if not errors else "PERLU_REVIEW",
        "can_commit": not errors,
        "errors": errors,
        "warnings": list(metadata.get("total_resolution_warnings") or []),
    }


def parse_kkp_reference(page_index, spm, file_sha):
    spm_meta = (spm or {}).get("metadata") or {}
    raw_jenis_spm = spm_meta.get("jenis_spm") or spm_meta.get("jenis_tagihan") or ""
    family = normalize_spm_family(raw_jenis_spm)
    payment_page = next((page for page in page_index if page.get("document_type") == "KKP_PAYMENT_LIST"), None)
    if family != SPMFamily.GUP_KKP or not payment_page:
        return None
    source_rows = _kkp_payment_rows(payment_page)
    statement_page = next((page for page in page_index if page.get("document_type") == "KKP_CARD_STATEMENT"), None)
    statement_rows = _kkp_statement_amounts(statement_page) if statement_page else []
    statement_amounts = [row["amount"] for row in statement_rows]
    list_amounts = [row["jumlah"] for row in source_rows]
    detail_evidence = _kkp_detail_total_evidence(page_index)
    spm_header = _kkp_spm_header_evidence(page_index, spm_meta)
    raw_list_total = sum(list_amounts, Decimal("0"))
    statement_total = sum(statement_amounts, Decimal("0"))
    total_candidates = {
        "PAYMENT_LIST": {
            "value": raw_list_total,
            "page": payment_page,
            "provenance": _kkp_total_provenance(
                payment_page,
                "payment_list_raw_sum",
                " + ".join(row.get("raw_amount") or str(row["jumlah"]) for row in source_rows),
                raw_list_total,
            ),
        },
        "CARD_STATEMENT": {
            "value": statement_total,
            "page": statement_page,
            "provenance": _kkp_total_provenance(
                statement_page,
                "card_statement_net_charges",
                " + ".join(row.get("raw_amount") or str(row["amount"]) for row in statement_rows),
                statement_total,
            ),
        },
        **detail_evidence,
        "SPM_HEADER": spm_header,
    }
    canonical_total, resolution_sources, resolution_status = _resolve_kkp_canonical_total(
        total_candidates
    )

    amount_options = [
        (
            "PAYMENT_LIST",
            list_amounts,
            payment_page,
            [row.get("raw_amount") or str(row["jumlah"]) for row in source_rows],
        ),
        (
            "CARD_STATEMENT",
            statement_amounts,
            statement_page,
            [row.get("raw_amount") or str(row["amount"]) for row in statement_rows],
        ),
    ]
    for source_name in ("SPM_DETAIL", "SPP_DETAIL"):
        evidence = detail_evidence.get(source_name) or {}
        amount_options.append(
            (
                source_name,
                evidence.get("amounts") or [],
                evidence.get("page"),
                [item["raw_token"] for item in evidence.get("amount_evidence") or []],
            )
        )
    amount_source = ""
    amount_page = payment_page
    amount_raw_tokens = []
    reconciled_amounts = []
    if canonical_total > 0:
        for source_name, amounts, source_page, raw_tokens in amount_options:
            if (
                len(amounts) == len(source_rows)
                and sum(amounts, Decimal("0")) == canonical_total
            ):
                amount_source = source_name
                amount_page = source_page or payment_page
                amount_raw_tokens = raw_tokens
                reconciled_amounts = amounts
                break
    payment_list_total = (
        sum(reconciled_amounts, Decimal("0"))
        if len(reconciled_amounts) == len(source_rows) and source_rows
        else Decimal("0")
    )
    printed_total = payment_list_total

    total_warnings = []
    header_total = _money(spm_header.get("value"))
    if canonical_total > 0:
        for evidence in total_candidates.values():
            provenance = (evidence or {}).get("provenance") or {}
            value = _money((evidence or {}).get("value"))
            if value > 0 and value != canonical_total:
                provenance["suspect"] = True
        if header_total > 0 and header_total != canonical_total:
            total_warnings.append(
                "Nilai header SPM terindikasi outlier OCR dan digantikan oleh konsensus "
                "sumber independen KKP."
            )
    else:
        total_warnings.append(
            "Total KKP belum memiliki konsensus minimal dua sumber independen."
        )

    total_provenance = {
        "payment_list_raw_total": total_candidates["PAYMENT_LIST"]["provenance"],
        "card_statement_total": total_candidates["CARD_STATEMENT"]["provenance"],
        "spm_header_total_raw": spm_header["provenance"],
    }
    if detail_evidence.get("SPM_DETAIL"):
        total_provenance["spm_detail_total"] = detail_evidence["SPM_DETAIL"]["provenance"]
    if detail_evidence.get("SPP_DETAIL"):
        total_provenance["spp_detail_total"] = detail_evidence["SPP_DETAIL"]["provenance"]
    total_provenance["payment_list_total"] = _kkp_total_provenance(
        amount_page,
        "payment_list_rows_reconciled" if amount_source != "PAYMENT_LIST" else "payment_list_row_sum",
        " + ".join(amount_raw_tokens),
        payment_list_total,
        inputs=[amount_source] if amount_source else [],
    )
    total_provenance["canonical_total"] = {
        **_page_source({}, "independent_source_consensus", "kkp-total", inputs=resolution_sources),
        "source": "PARSER_STRUCTURAL",
        "method": "independent_source_consensus",
        "raw_token": "",
        "normalized_value": canonical_total,
        "suspect": False,
    }
    total_provenance["printed_total"] = _kkp_total_provenance(
        payment_page,
        "validated_payment_list_total",
        "",
        printed_total,
        inputs=["payment_list_total", "canonical_total"],
    )

    orders = _kkp_payment_orders(page_index)
    order_matches, ambiguous_receipts = _match_kkp_receipts(
        source_rows, reconciled_amounts, orders
    )
    canonical_jenis_spm = "GUP-KKP"
    spm_meta["jenis_spm_raw"] = raw_jenis_spm
    spm_meta["jenis_spm"] = canonical_jenis_spm
    spm_page = next((page for page in page_index if page.get("document_type") == "SPM"), payment_page)
    group_key = f"KKP:{file_sha}:{spm_meta.get('nomor_spm') or ''}:1"
    items = []
    for index, row in enumerate(source_rows):
        amount = reconciled_amounts[index] if index < len(reconciled_amounts) else Decimal("0")
        order = order_matches.get(index)
        charge, charge_page = _kkp_coa_evidence(page_index, row["akun"])
        coa_description, raw_coa_description = _kkp_coa_description(charge_page, amount)
        description_candidates = [
            (row.get("keperluan") or "", row.get("raw_keperluan") or "", payment_page, "payment_list_description"),
            (coa_description, raw_coa_description, charge_page, "coa_description"),
            (
                _sanitize_kkp_description((order or {}).get("description")),
                (order or {}).get("description") or "",
                (order or {}).get("page"),
                "payment_order_description",
            ),
            (
                _sanitize_kkp_description(spm_meta.get("uraian")),
                spm_meta.get("uraian") or "",
                spm_page,
                "spm_description",
            ),
            (
                _sanitize_kkp_description((order or {}).get("recipient")),
                (order or {}).get("recipient") or "",
                (order or {}).get("page"),
                "payment_order_recipient",
            ),
        ]
        description, raw_description, description_page, description_method = next(
            (candidate for candidate in description_candidates if candidate[0]),
            ("", "", payment_page, "description_not_proven"),
        )
        receipt = order["no_kuitansi"] if order else ""
        receipt_ambiguous = index in ambiguous_receipts
        receipt_absent = not receipt and not receipt_ambiguous
        field_provenance = {
            "akun": _page_source(payment_page, "tsv_layout_columns", row["source_locator"]),
            "bruto": _page_source(
                amount_page or payment_page,
                "reconciled_layout_amount",
                f"transaction-row:{index + 1}",
                inputs=[amount_source, "canonical_total"],
            ),
            "deskripsi": {
                **_page_source(description_page, description_method, row["source_locator"]),
                "raw_value": raw_description,
            },
            "pembebanan": _page_source(charge_page or payment_page, "coa_16_segment", "account-and-coa"),
            "jenis_spm": _page_source(
                spm_page,
                "family_normalization",
                "jenis-tagihan",
                inputs=[raw_jenis_spm],
            ),
            "no_kuitansi": (
                _page_source(order["page"], "labeled_receipt", "kuitansi/bukti")
                if order else {
                    **_page_source(payment_page, "confirmed_absent", row["source_locator"]),
                    "source": "PARSER_STRUCTURAL",
                }
            ),
            "pph21": {
                **_page_source(payment_page, "confirmed_zero", row["source_locator"]),
                "source": "PARSER_STRUCTURAL",
                "inputs": ["payment_list_without_pph21_deduction"],
            },
        }
        items.append(
            {
                "group_key": group_key,
                "akun": row["akun"],
                "nomor_spm": spm_meta.get("nomor_spm") or "",
                "tanggal_spm": spm_meta.get("tanggal_spm"),
                "jenis_spm": canonical_jenis_spm,
                "cara_pembayaran": "UP/TUP",
                "bulan_sp2d": spm_meta.get("bulan_sp2d"),
                "no_bukti": receipt,
                "no_kuitansi": receipt,
                "no_drpp": "",
                "keperluan": description,
                "deskripsi": description,
                "jumlah": amount,
                "bruto": amount,
                "nilai_bruto": amount,
                "netto": amount,
                "nilai_netto": amount,
                "pembebanan": charge,
                "fp": "",
                "pph21": Decimal("0"),
                "receipt_policy": (
                    "ambiguous_source" if receipt_ambiguous
                    else ("not_available_from_source" if receipt_absent else "source_document")
                ),
                "receipt_not_available_from_source": receipt_absent,
                "field_provenance": field_provenance,
                "source_pages": {
                    "payment_list": payment_page.get("page_number"),
                    "amount": (amount_page or payment_page).get("page_number"),
                    "payment_order": order["page"].get("page_number") if order else None,
                    "coa": charge_page.get("page_number") if charge_page else None,
                },
                "status": "LENGKAP" if amount and charge and description and not receipt_ambiguous else "PERLU_REVIEW",
                "status_detail": "LENGKAP" if amount and charge and description and not receipt_ambiguous else "PERLU_REVIEW",
                "warnings": (
                    ["Kuitansi memiliki lebih dari satu kandidat transaksi dengan evidence setara."]
                    if receipt_ambiguous
                    else ([] if amount and charge and description else ["Nominal, pembebanan, atau deskripsi KKP belum terbukti."])
                ),
            }
        )
    spp_page = next((page for page in page_index if page.get("document_type") == "SPP"), None)
    spp_match = re.search(r"\bNOMOR\s*[:\-]?\s*([0-9]{3,6}[A-Z])\b", str((spp_page or {}).get("text") or ""), re.I)
    metadata = {
        "nomor_drpp": "",
        "group_key": group_key,
        "source_item_count": len(source_rows),
        "total": printed_total,
        "payment_list_raw_total": raw_list_total,
        "payment_list_total": payment_list_total,
        "card_statement_total": statement_total,
        "spm_detail_total": _money((detail_evidence.get("SPM_DETAIL") or {}).get("value")),
        "spp_detail_total": _money((detail_evidence.get("SPP_DETAIL") or {}).get("value")),
        "spm_header_total_raw": header_total,
        "canonical_total": canonical_total,
        "printed_total": printed_total,
        "spm_total": header_total,
        "total_resolution_status": resolution_status,
        "total_resolution_sources": resolution_sources,
        "total_resolution_warnings": total_warnings,
        "total_provenance": total_provenance,
        "parent_is_gup_kkp": True,
        "nomor_spm": spm_meta.get("nomor_spm") or "",
        "nomor_spp": spp_match.group(1).upper() if spp_match else "",
        "tanggal_spm": spm_meta.get("tanggal_spm"),
        "jenis_spm": canonical_jenis_spm,
        "jenis_spm_raw": raw_jenis_spm,
        "cara_pembayaran": "UP/TUP",
        "reference_sources": [
            _page_source(payment_page, "tsv_layout_columns", "payment-table"),
            *([_page_source(statement_page, "statement_reconciliation", "transaction-table")] if statement_page else []),
        ],
    }
    reference = {
        "reference_type": "KKP_PAYMENT_LIST",
        "metadata": metadata,
        "items": items,
        "source_pages": [payment_page.get("page_number")],
        "warnings": total_warnings,
        "status": "parsed_text",
    }
    validation = evaluate_kkp_group_commitability(reference, items)
    for item in items:
        if not validation["can_commit"] and item["status"] == "LENGKAP":
            item["status"] = item["status_detail"] = "PERLU_REVIEW"
    return reference, validation


def _group_item_value(item, *names):
    for name in names:
        value = item.get(name) if isinstance(item, dict) else getattr(item, name, None)
        if value not in (None, ""):
            return value
    return None


def evaluate_drpp_group_commitability(
    drpp,
    items,
    *,
    parser_validation=None,
    extra_errors=None,
):
    """Satu keputusan rekonsiliasi untuk parser, preview, dan commit server."""
    drpp = drpp or {}
    metadata = drpp.get("metadata", {})
    number = str(metadata.get("nomor_drpp") or metadata.get("no_drpp") or "")
    source_count = metadata.get("source_item_count")
    expected_count = int(source_count if source_count is not None else len(drpp.get("items") or []))
    expected_total = _money(metadata.get("printed_total"))
    actual_total = sum(
        (
            _money(_group_item_value(item, "nilai_bruto", "bruto", "jumlah"))
            for item in items
        ),
        Decimal("0"),
    )
    errors = []
    if not drpp:
        errors.append("Halaman DRPP tidak ditemukan.")
    if not re.fullmatch(r"\d{3,6}", number):
        errors.append("Nomor DRPP tidak valid.")
    if not items:
        errors.append("Item DRPP valid tidak ditemukan.")
    if expected_count <= 0:
        errors.append("Jumlah item sumber DRPP tidak tersedia.")
    if len(items) != expected_count:
        errors.append(f"Jumlah baris hasil ({len(items)}) tidak sama dengan jumlah baris DRPP ({expected_count}).")
    if expected_total <= 0:
        errors.append("Total referensi DRPP tidak ditemukan atau bernilai nol.")
    elif actual_total != expected_total:
        errors.append(f"Total baris Rp{actual_total:,.0f} tidak sama dengan total DRPP Rp{expected_total:,.0f}.")
    if metadata.get("printed_total_conflict"):
        errors.append("Kandidat total referensi DRPP saling berbeda.")
    if metadata.get("identity_conflict") or metadata.get("nomor_spm_conflict"):
        errors.append("Konflik identitas utama ditemukan.")
    seen = set()
    row_detail_errors = []
    for item in items:
        no_kuitansi = _group_item_value(item, "no_kuitansi", "no_bukti")
        akun = str(_group_item_value(item, "akun") or "")
        nomor_spm = _group_item_value(item, "nomor_spm")
        tanggal_spm = _group_item_value(item, "tanggal_spm")
        pembebanan = str(_group_item_value(item, "pembebanan") or "")
        status_detail = str(
            _group_item_value(item, "status_detail", "batch_status", "status") or ""
        ).upper()
        if not no_kuitansi:
            row_detail_errors.append("Nomor kuitansi kosong.")
        if not akun:
            row_detail_errors.append("Akun kosong.")
        if _money(_group_item_value(item, "nilai_bruto", "bruto", "jumlah")) <= 0:
            row_detail_errors.append("Nilai bruto nol tanpa bukti.")
        if not nomor_spm:
            row_detail_errors.append("Nomor SPM kosong.")
        if not tanggal_spm:
            row_detail_errors.append("Tanggal SPM kosong.")
        if not pembebanan:
            row_detail_errors.append("Pembebanan kosong.")
        elif "0000" in pembebanan:
            row_detail_errors.append("Pembebanan mengandung 0000.")
        elif akun and not pembebanan.endswith(akun):
            row_detail_errors.append("Akhiran Pembebanan tidak sama dengan Akun.")
        if status_detail in {"GAGAL", "PERLU_REVIEW", "PERLU REVIEW"}:
            row_detail_errors.append("Terdapat field transaksi yang masih perlu review.")
        key = (nomor_spm, no_kuitansi, akun)
        if key in seen:
            row_detail_errors.append("Duplikat exact key ditemukan dalam upload yang sama.")
        seen.add(key)
    errors.extend(row_detail_errors)
    missing_receipts = metadata.get("missing_receipt_count") or 0
    rows_are_reconciled = (
        bool(items)
        and expected_count > 0
        and len(items) == expected_count
        and expected_total > 0
        and actual_total == expected_total
    )
    if missing_receipts and row_detail_errors:
        errors.append(f"Terdapat {missing_receipts} kuitansi sumber yang belum memiliki detail.")
    if parser_validation and (
        parser_validation.get("status") != "BALANCE"
        or parser_validation.get("can_commit") is False
    ):
        parser_errors = parser_validation.get("errors") or [
            parser_validation.get("status_message") or "Validasi parser masih perlu review."
        ]
        if missing_receipts and not row_detail_errors:
            parser_errors = [
                error for error in parser_errors
                if not re.search(r"kuitansi\s+sumber.*belum\s+memiliki\s+detail", str(error), re.I)
            ]
        errors.extend(parser_errors)
    errors.extend(extra_errors or [])
    errors = list(dict.fromkeys(errors))
    return {
        "no_drpp": number,
        "expected_count": expected_count,
        "parsed_count": len(items),
        "expected_total": expected_total,
        "parsed_total": actual_total,
        "row_count": len(items),
        "expected_row_count": expected_count,
        "total_drpp": expected_total,
        "total_rows": actual_total,
        "status": "BALANCE" if not errors else "PERLU_REVIEW",
        "can_commit": not errors,
        "errors": errors,
    }


def validate_drpp_group(drpp, items):
    return evaluate_drpp_group_commitability(drpp, items)


def _public_manifest(manifest):
    return [{key: value for key, value in item.items() if not key.startswith("_")} for item in manifest]


def _public_page(page):
    return {
        "file_name": page["file_name"],
        "page_number": page["page_number"],
        "document_type": page.get("document_type", "UNKNOWN"),
        "type_hint": page.get("type_hint", "UNKNOWN"),
        "drpp_hint": page.get("drpp_hint", ""),
        "drpp_detected": page.get("drpp_detected", ""),
        "confidence": page.get("confidence", 0),
        "evidence": page.get("evidence", []),
        "page_hash": page.get("page_hash", ""),
        "page_content_hash": page.get("page_content_hash", ""),
        "duplicate_of": page.get("duplicate_of"),
        "ocr_called": page.get("ocr_called", False),
        "cache_hit": page.get("cache_hit", False),
        "engine": page.get("engine", "native_pdf" if page.get("native_text") else ""),
        "extraction_method": "native_text" if page.get("native_text") else ("page_ocr" if page.get("ocr_called") else "page_probe"),
    }


def _batch_metrics(page_index, started, recovery_diagnostics):
    return {
        "ocr_seconds": round(
            sum(page.get("ocr_duration", 0) + page.get("probe_duration", 0) for page in page_index),
            3,
        ),
        "process_seconds": round(time.monotonic() - started, 3),
        "page_total": len(page_index),
        "unique_pages": sum(1 for page in page_index if page.get("is_representative")),
        "ocr_pages": sum(
            1 for page in page_index if page.get("ocr_called") or page.get("probe_ocr_called")
        ),
        "ocr_cache_hits": sum(
            1 for page in page_index if page.get("cache_hit") or page.get("probe_cache_hit")
        ),
        **recovery_diagnostics,
    }


def parse_drpp_upload_batch(file_path, ocr=True):
    _local.ocr_cache = {}
    processed_page_keys = set()
    recovery_diagnostics = {
        "recovery_pages_considered": 0,
        "recovery_pages_ocr": 0,
        "recovery_pages_skipped_processed": 0,
    }
    started = time.monotonic()
    manifest = build_manifest(file_path)
    temp_dir = next((item.get("_temp_dir") for item in manifest if item.get("_temp_dir")), "")
    try:
        filename_numbers = {item["drpp_hint"] for item in manifest if item.get("drpp_hint")}

        page_index = build_page_index(manifest)
        discover_embedded_drpp_pages(page_index, ocr=ocr)
        page_index = deduplicate_pages(page_index)
        classify_candidate_pages(page_index, ocr=ocr)

        base_spm, base_sp2d_parent = resolve_spm_parent([], page_index)
        base_meta = (base_spm or {}).get("metadata") or {}
        spm_family = normalize_spm_family(base_meta.get("jenis_spm") or base_meta.get("jenis_tagihan"))
        policy = document_requirement_policy(spm_family)

        if spm_family == SPMFamily.GUP_KKP and ocr:
            if not any(p.get("document_type") == "KKP_PAYMENT_LIST" for p in page_index):
                for page in page_index:
                    if page.get("is_representative") and page.get("document_type") in {"UNKNOWN", "SUPPORT_DOCUMENT"}:
                        started_probe = time.monotonic()
                        probe = _probe_page_text(page)
                        page["probe_duration"] = time.monotonic() - started_probe
                        page["probe_ocr_called"] = not probe.get("cache_hit", False)
                        page["probe_cache_hit"] = bool(probe.get("cache_hit"))
                        page["text"] = probe.get("text", "")
                        page["document_type"], page["confidence"], page["evidence"] = _classification(page["text"])
                        if page["document_type"] == "KKP_PAYMENT_LIST":
                            break

        if any(
            page.get("document_type") in {"KKP_PAYMENT_LIST", "KKP_CARD_STATEMENT", "KKP_PAYMENT_ORDER"}
            for page in page_index
        ):
            kkp_spm = base_spm
            kkp_sp2d_parent = base_sp2d_parent
            family = spm_family
            if family == SPMFamily.GUP_KKP:
                package_sha = hashlib.sha256(
                    "|".join(sorted(item["sha256"] for item in manifest)).encode("utf-8")
                ).hexdigest()
                parsed_reference = parse_kkp_reference(page_index, kkp_spm, package_sha)
                if parsed_reference:
                    reference, validation = parsed_reference
                    kkp_meta["spm_family"] = family.value
                    kkp_meta["document_requirement_policy"] = policy.value
                    kkp_meta["nomor_spp"] = reference["metadata"].get("nomor_spp") or ""
                    items = reference.get("items") or []
                    group_key = reference["metadata"]["group_key"]
                    group = {
                        "group_key": group_key,
                        "no_drpp": "",
                        "reference_type": "KKP_PAYMENT_LIST",
                        "is_kkp": True,
                        "drpp": reference,
                        "items": items,
                        "validation": validation,
                        "status": validation["status"],
                    }
                    return {
                        "ok": bool(items),
                        "parser_version": PARSER_VERSION,
                        "spm_family": family.value,
                        "document_requirement_policy": policy.value,
                        "reference_type": "KKP_PAYMENT_LIST",
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
                        "spm": kkp_spm,
                        "sp2d_parent_id": getattr(kkp_sp2d_parent, "id", None),
                        "drpp": reference,
                        "drpps": [reference],
                        "drpp_groups": [group],
                        "kw_by_drpp": {group_key: items},
                        "kw_items": items,
                        "preview_rows": [],
                        "warnings": list(validation.get("errors") or []),
                        "metrics": _batch_metrics(page_index, started, recovery_diagnostics),
                        "temp_dir": temp_dir,
                    }
        detected_numbers = {
            number
            for page in page_index
            for number in (page.get("drpp_detected"), page.get("drpp_hint"))
            if number
        }
        numbers = sorted(detected_numbers or filename_numbers)
        if not numbers:
            numbers = ["TANPA_DRPP"]

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

        all_items = []
        groups = []
        used_kw = {}
        def read_drpps():
            parsed, missing = [], []
            for number in numbers:
                group_pages = pages_for(number)
                drpp = parse_drpp_summary(number, group_pages)
                if not drpp:
                    missing.append(number)
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
                parse_kw_support(
                    drpp.get("items", []),
                    group_pages,
                    year=str(drpp.get("metadata", {}).get("tahun") or ""),
                )
                parsed.append(drpp)
            return parsed, missing

        drpps, missing_numbers = read_drpps()

        if policy == DocumentRequirement.DRPP_REQUIRED or drpps:
            recovery_targets = drpps or [
                {"metadata": {"nomor_drpp": number, "printed_total": Decimal("0")}, "items": []}
                for number in missing_numbers
            ]
            if _recover_missing_candidate_pages(
                recovery_targets,
                page_index,
                ocr=ocr,
                processed_page_keys=processed_page_keys,
                diagnostics=recovery_diagnostics,
            ):
                drpps, missing_numbers = read_drpps()

            if policy == DocumentRequirement.DRPP_REQUIRED:
                for number in missing_numbers:
                    groups.append({"no_drpp": number, "items": [], "validation": {"status": "PERLU_REVIEW", "can_commit": False, "errors": ["Halaman DRPP tidak ditemukan."]}})
            else:
                for number in missing_numbers:
                    groups.append({"no_drpp": number, "items": [], "validation": {"status": "PERLU_REVIEW", "can_commit": False, "errors": []}})
        else:
            for number in missing_numbers:
                groups.append({"no_drpp": number, "items": [], "validation": {"status": "PERLU_REVIEW", "can_commit": False, "errors": []}})

        spm, sp2d_parent = resolve_spm_parent(drpps, page_index)
        _apply_group_date_consensus(drpps, spm)
        for drpp in drpps:
            # Use canonical no_drpp format for group key matching
            number = drpp.get("metadata", {}).get("nomor_drpp_full") or drpp.get("metadata", {}).get("nomor_drpp", "")
            group_pages = pages_for(number)
            year = drpp.get("metadata", {}).get("tahun") or getattr((spm or {}).get("metadata", {}).get("tanggal_spm"), "year", "")
            parse_kw_support(drpp.get("items", []), group_pages, year=str(year or ""))
            missing_receipts = sum(
                1 for item in drpp.get("items", [])
                if not _matching_receipt_pages(item, group_pages)
            )
            drpp.setdefault("metadata", {})["missing_receipt_count"] = missing_receipts
            drpp["metadata"]["detail_receipt_count"] = len(drpp.get("items", [])) - missing_receipts
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

        metrics = _batch_metrics(page_index, started, recovery_diagnostics)
        family = normalize_spm_family(((spm or {}).get("metadata") or {}).get("jenis_spm"))
        policy = document_requirement_policy(family)
        if spm:
            spm.setdefault("metadata", {})["spm_family"] = family.value
            spm["metadata"]["document_requirement_policy"] = policy.value
        if not drpps and family != SPMFamily.UNKNOWN and policy != DocumentRequirement.DRPP_REQUIRED:
            message = (
                "Daftar pembayaran KKP yang valid belum ditemukan."
                if family == SPMFamily.GUP_KKP
                else f"Jenis SPM dikenali sebagai {family.value}, tetapi parser {policy.value} belum diaktifkan."
            )
            for group in groups:
                group["validation"] = {
                    "status": "PERLU_REVIEW",
                    "can_commit": False,
                    "errors": [message],
                }
                group["status"] = "PERLU_REVIEW"
        warnings = [error for group in groups for error in group.get("validation", {}).get("errors", [])]
        return {
            "ok": bool(drpps and all_items),
            "parser_version": PARSER_VERSION,
            "spm_family": family.value,
            "document_requirement_policy": policy.value,
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
