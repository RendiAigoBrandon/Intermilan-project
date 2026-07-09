import io
import os
import shutil
import sys
from dataclasses import dataclass, field

from PIL import Image, ImageOps, ImageFilter


# ─── Klasifikasi halaman dokumen ─────────────────────────────────────────────
PAGE_CLASS_KEYWORDS = {
    "spm": [
        "SURAT PERINTAH MEMBAYAR",
        "NOMOR SPM",
        "SPM NOMOR",
    ],
    "spp": [
        "SURAT PERMINTAAN PEMBAYARAN",
        "NOMOR SPP",
        "NO SPP",
        "NO. SPP",
    ],
    "sp2d": [
        "DETAIL PENGELUARAN DAN POTONGAN",
        "DAFTAR SP2D",
        "NO SP2D",
        "NO. SP2D",
        "NOMOR SP2D",
    ],
    "drpp": [
        "DAFTAR RINCIAN PERMINTAAN PEMBAYARAN",
        "BUKTI PENGELUARAN",
        "NO BUKTI",
    ],
    "kw": [
        "KUITANSI",
        "TERBILANG",
    ],
    "coa": [
        "KODE AKUN",
        "COA",
        "PEMBEBANAN",
        "SEGMEN",
    ],
    "lampiran_spm": [
        "LAMPIRAN SPM",
        "LAMPIRAN SURAT PERINTAH",
    ],
    "lampiran_spp": [
        "LAMPIRAN SPP",
        "LAMPIRAN SURAT PERMINTAAN",
    ],
}

DOCUMENT_KEYWORDS = {
    "spm": [
        "SURAT PERINTAH MEMBAYAR",
        "NOMOR",
        "TANGGAL",
        "TOTAL PEMBAYARAN",
        "JUMLAH PENGELUARAN",
        "POTONGAN",
        "KPPN",
        "SUPPLIER",
        "BANK",
        "REKENING",
    ],
    "drpp": [
        "DAFTAR RINCIAN PERMINTAAN PEMBAYARAN",
        "BUKTI PENGELUARAN",
        "NO BUKTI",
        "TANGGAL",
        "PENERIMA",
        "NPWP",
        "AKUN",
        "JUMLAH",
    ],
    "kw": [
        "KUITANSI",
        "KW",
        "TERBILANG",
        "PENERIMA",
        "JUMLAH",
        "URAIAN",
    ],
}


@dataclass
class OCRPage:
    page_number: int
    engine: str
    extracted_text: str
    status: str
    confidence: float = 0.0
    warnings: list = field(default_factory=list)
    page_classification: str = ""

    @property
    def method(self):
        return self.engine


@dataclass
class EngineResult:
    engine: str
    pages: list
    warnings: list = field(default_factory=list)

    @property
    def combined_text(self):
        return "\n".join(page.extracted_text for page in self.pages if page.extracted_text)

    @property
    def confidence(self):
        values = [page.confidence for page in self.pages if page.confidence]
        return round(sum(values) / len(values), 2) if values else 0.0


def classify_page(text):
    """Klasifikasikan satu halaman berdasarkan keyword yang ditemukan."""
    upper = (text or "").upper()
    scores = {}
    for page_type, keywords in PAGE_CLASS_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in upper)
        if score > 0:
            scores[page_type] = score
    if not scores:
        return "lampiran"
    return max(scores, key=scores.get)


def optional_import(module_name):
    try:
        return __import__(module_name)
    except Exception:
        return None


def ocr_log(message):
    print(f"[INTERMILAN OCR] {message}", flush=True)


def pdf_page_count(file_path):
    fitz = optional_import("fitz")
    if not fitz:
        return None
    try:
        doc = fitz.open(file_path)
        count = doc.page_count
        doc.close()
        return count
    except Exception as exc:
        ocr_log(f"page_count failed: {exc}")
        return None


def log_file_diagnostics(file_path, phase="start", extra=None):
    exists = os.path.exists(file_path)
    size = os.path.getsize(file_path) if exists else 0
    page_count = pdf_page_count(file_path) if exists else None
    ocr_log(
        f"{phase}; python={sys.executable}; path={file_path}; "
        f"exists={exists}; size={size}; page_count={page_count}; extra={extra or '-'}"
    )


def parse_bool_env(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def engine_order():
    raw = os.getenv("OCR_ENGINE_ORDER", "text,tesseract,paddleocr")
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def detect_document_type(text, document_type=None):
    if document_type:
        return str(document_type).lower()
    upper = text.upper()
    scores = {}
    for doc_type, keywords in DOCUMENT_KEYWORDS.items():
        scores[doc_type] = sum(1 for keyword in keywords if keyword in upper)
    if not scores:
        return None
    best_type, best_score = max(scores.items(), key=lambda item: item[1])
    return best_type if best_score else None


def score_text(text, document_type=None, confidence=0.0):
    normalized = " ".join((text or "").split())
    if not normalized:
        return 0.0
    upper = normalized.upper()
    doc_type = detect_document_type(upper, document_type)
    keywords = DOCUMENT_KEYWORDS.get(doc_type or "", [])
    keyword_score = sum(5 for keyword in keywords if keyword in upper)
    number_score = min(len(__import__("re").findall(r"\b\d{3,}[A-Z]?\b", upper)), 10) * 1.5
    money_score = min(len(__import__("re").findall(r"\b\d{1,3}(?:[.,]\d{3})+\b", upper)), 10) * 2
    length_score = min(len(normalized) / 120, 20)
    return round(length_score + keyword_score + number_score + money_score + (confidence / 10), 2)


def has_usable_text(result, document_type=None):
    text = result.combined_text if result else ""
    if not text.strip():
        return False
    normalized = " ".join(text.split())
    min_chars = int(os.getenv("OCR_TESSERACT_MIN_TEXT_LENGTH", os.getenv("OCR_NATIVE_MIN_CHARS", "120")))
    if len(normalized) >= min_chars:
        return True
    return result_score(result, document_type) >= float(os.getenv("OCR_NATIVE_MIN_SCORE", "18"))


def result_score(result, document_type=None):
    return score_text(result.combined_text, document_type, result.confidence)


def is_low_confidence(result):
    """Cek apakah confidence hasil OCR di bawah threshold."""
    threshold = float(os.getenv("OCR_TESSERACT_MIN_CONFIDENCE", "60"))
    return result.confidence < threshold


def page_dict(page):
    return {
        "page": page.page_number,
        "page_number": page.page_number,
        "engine": page.engine,
        "method": page.engine,
        "text": page.extracted_text,
        "extracted_text": page.extracted_text,
        "confidence": page.confidence,
        "status": page.status,
        "warnings": page.warnings,
        "page_classification": page.page_classification,
    }


def build_public_result(best, warnings=None, engines_tried=None, status=None,
                        paddleocr_called=False, paddleocr_text_length=0):
    warnings = warnings or []
    engines_tried = engines_tried or []
    combined_text = best.combined_text if best else ""
    best_engine = best.engine if best else "failed"
    if status is None:
        if not combined_text.strip():
            status = "needs_manual_review" if best_engine != "failed" else "failed"
        elif best_engine == "text":
            status = "parsed_text"
        else:
            status = "parsed_ocr"
    if not combined_text.strip() and "OCR kosong: tidak ada teks yang berhasil diekstrak." not in warnings:
        warnings.append("OCR kosong: tidak ada teks yang berhasil diekstrak.")
    pages = [page_dict(page) for page in best.pages] if best else []
    all_warnings = warnings + [warning for page in pages for warning in page.get("warnings", [])]
    return {
        "status": status,
        "best_engine": best_engine,
        "method": best_engine,
        "pages": pages,
        "texts": [page["text"] for page in pages],
        "combined_text": combined_text,
        "confidence": best.confidence if best else 0.0,
        "warnings": all_warnings,
        "engines_tried": engines_tried,
        "page_count": len(pages),
        "paddleocr_called": paddleocr_called,
        "paddleocr_text_length": paddleocr_text_length,
    }


def extract_text_native(file_path):
    warnings = []
    candidates = []

    fitz = optional_import("fitz")
    if fitz:
        try:
            ocr_log(f"native text engine=PyMuPDF file={file_path}")
            doc = fitz.open(file_path)
            pages = []
            for index, page in enumerate(doc, start=1):
                text = page.get_text("text") or ""
                page_class = classify_page(text)
                pages.append(OCRPage(index, "text", text, "parsed_text" if text.strip() else "empty",
                                     100.0 if text.strip() else 0.0, [], page_class))
            doc.close()
            candidates.append(EngineResult("text", pages, []))
        except Exception as exc:
            warning = f"PyMuPDF text gagal: {exc}"
            warnings.append(warning)
            ocr_log(warning)
    else:
        warnings.append("PyMuPDF tidak ada: package fitz belum terinstall.")

    pdfplumber = optional_import("pdfplumber")
    if pdfplumber:
        try:
            ocr_log(f"native text engine=pdfplumber file={file_path}")
            with pdfplumber.open(file_path) as pdf:
                pages = []
                for index, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text() or ""
                    page_class = classify_page(text)
                    pages.append(OCRPage(index, "text", text, "parsed_text" if text.strip() else "empty",
                                         95.0 if text.strip() else 0.0, [], page_class))
            candidates.append(EngineResult("text", pages, []))
        except Exception as exc:
            warning = f"pdfplumber text gagal: {exc}"
            warnings.append(warning)
            ocr_log(warning)
    else:
        warnings.append("pdfplumber tidak ada: package pdfplumber belum terinstall.")

    pypdf = optional_import("pypdf") or optional_import("PyPDF2")
    if pypdf:
        try:
            ocr_log(f"native text engine=pypdf file={file_path}")
            reader = pypdf.PdfReader(file_path)
            pages = []
            for index, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                page_class = classify_page(text)
                pages.append(OCRPage(index, "text", text, "parsed_text" if text.strip() else "empty",
                                     90.0 if text.strip() else 0.0, [], page_class))
            candidates.append(EngineResult("text", pages, []))
        except Exception as exc:
            warning = f"pypdf/PyPDF2 text gagal: {exc}"
            warnings.append(warning)
            ocr_log(warning)
    else:
        warnings.append("pypdf tidak ada: package pypdf/PyPDF2 belum terinstall.")

    if not candidates:
        return EngineResult("text", [], warnings)
    best = max(candidates, key=lambda result: len(result.combined_text))
    best.warnings.extend(warnings)
    return best


def render_pdf_pages(file_path, dpi=250):
    fitz = optional_import("fitz")
    if not fitz:
        raise RuntimeError("PyMuPDF tidak ada: package fitz belum terinstall untuk render PDF scan.")
    ocr_log(f"render PDF for OCR; dpi={dpi}; file={file_path}")
    doc = fitz.open(file_path)
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    images = []
    for page in doc:
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        images.append(Image.open(io.BytesIO(pixmap.tobytes("png"))))
    doc.close()
    return images


def preprocess_image(image):
    processed = image.convert("L")
    processed = ImageOps.autocontrast(processed)
    processed = processed.filter(ImageFilter.SHARPEN)
    if parse_bool_env("OCR_ENABLE_THRESHOLD", False):
        processed = processed.point(lambda pixel: 255 if pixel > 180 else 0)
    return processed


def tesseract_page_text(pytesseract, image):
    configs = ["--psm 6", "--psm 4", "--psm 11"]
    lang_attempts = ["ind+eng", "eng", ""]
    warnings = []
    for config in configs:
        for lang in lang_attempts:
            try:
                kwargs = {"config": config, "output_type": pytesseract.Output.DICT}
                if lang:
                    kwargs["lang"] = lang
                data = pytesseract.image_to_data(image, **kwargs)
                words = []
                confidences = []
                for word, conf in zip(data.get("text", []), data.get("conf", [])):
                    if word and str(word).strip():
                        words.append(str(word))
                        try:
                            conf_value = float(conf)
                        except (TypeError, ValueError):
                            conf_value = -1.0
                        if conf_value >= 0:
                            confidences.append(conf_value)
                text = " ".join(words).strip()
                ocr_log(f"tesseract attempt lang={lang or 'default'} config={config} raw_text_length={len(text)}")
                if text:
                    confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0.0
                    return text, confidence, warnings
            except Exception as exc:
                warning = f"Tesseract lang {lang or 'default'} config {config} gagal: {exc}"
                warnings.append(warning)
                ocr_log(warning)
    return "", 0.0, warnings


def extract_tesseract(file_path, images=None):
    """Jalankan Tesseract. Jika images sudah disediakan (dari render sebelumnya), gunakan langsung."""
    warnings = []
    pytesseract = optional_import("pytesseract")
    if not pytesseract:
        return EngineResult("tesseract", [], ["package pytesseract tidak ada di environment Python aktif."])
    if not shutil.which("tesseract"):
        return EngineResult("tesseract", [], ["tesseract.exe tidak ditemukan di PATH Windows. Install Tesseract OCR binary terpisah."])

    if images is None:
        try:
            images = render_pdf_pages(file_path)
        except Exception as exc:
            warning = f"PDF scan gagal render: {exc}"
            ocr_log(warning)
            return EngineResult("tesseract", [], [warning])

    pages = []
    for index, image in enumerate(images, start=1):
        page_warnings = []
        try:
            text, confidence, page_warnings = tesseract_page_text(pytesseract, preprocess_image(image))
            status = "parsed_ocr" if text.strip() else "empty"
            page_class = classify_page(text)
            pages.append(OCRPage(index, "tesseract", text, status, confidence, page_warnings, page_class))
        except Exception as exc:
            pages.append(OCRPage(index, "tesseract", "", "failed", 0.0, [f"Tesseract halaman {index} gagal: {exc}"], ""))
    if not any(page.extracted_text.strip() for page in pages):
        warnings.append("OCR kosong: Tesseract tidak menghasilkan teks yang cukup untuk dipakai.")
    return EngineResult("tesseract", pages, warnings)


def extract_paddleocr(file_path, images=None):
    if not parse_bool_env("OCR_ENABLE_PADDLEOCR", False):
        return EngineResult("paddleocr", [], ["PaddleOCR dilewati karena OCR_ENABLE_PADDLEOCR=false."])
    try:
        paddleocr_module = optional_import("paddleocr")
        if not paddleocr_module:
            return EngineResult("paddleocr", [], ["PaddleOCR belum terpasang."])
        ocr = paddleocr_module.PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
        if images is None:
            images = render_pdf_pages(file_path)
    except Exception as exc:
        return EngineResult("paddleocr", [], [f"PaddleOCR gagal disiapkan: {exc}"])

    pages = []
    for index, image in enumerate(images, start=1):
        try:
            result = ocr.ocr(preprocess_image(image), cls=True)
            lines = []
            confidences = []
            for block in result or []:
                for row in block or []:
                    if len(row) >= 2 and row[1]:
                        lines.append(str(row[1][0]))
                        if len(row[1]) > 1:
                            confidences.append(float(row[1][1]) * 100)
            text = "\n".join(lines)
            confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0.0
            page_class = classify_page(text)
            pages.append(OCRPage(index, "paddleocr", text, "parsed_ocr" if text.strip() else "empty",
                                 confidence, [], page_class))
        except Exception as exc:
            pages.append(OCRPage(index, "paddleocr", "", "failed", 0.0, [f"PaddleOCR halaman {index} gagal: {exc}"], ""))
    return EngineResult("paddleocr", pages, [])


def extract_cloud_ocr(file_path):
    if not parse_bool_env("OCR_ENABLE_CLOUD", False):
        return EngineResult("cloud", [], ["Cloud OCR dilewati karena OCR_ENABLE_CLOUD=false."])
    provider = os.getenv("OCR_CLOUD_PROVIDER", "").strip().lower()
    if provider == "google_document_ai" and parse_bool_env("GOOGLE_DOCUMENT_AI_ENABLED", False):
        return EngineResult("google_document_ai", [], ["Google Document AI belum diaktifkan pada implementasi lokal ini."])
    if provider == "azure_document_intelligence" and parse_bool_env("AZURE_DOCUMENT_INTELLIGENCE_ENABLED", False):
        return EngineResult("azure_document_intelligence", [], ["Azure Document Intelligence belum diaktifkan pada implementasi lokal ini."])
    return EngineResult("cloud", [], ["Cloud OCR belum dikonfigurasi."])


def check_ocr_environment():
    fitz = optional_import("fitz")
    pdfplumber = optional_import("pdfplumber")
    pytesseract = optional_import("pytesseract")
    paddleocr = optional_import("paddleocr")
    tesseract_path = shutil.which("tesseract")
    warnings = []
    tesseract_version = ""

    if not fitz:
        warnings.append("PyMuPDF belum terinstall, PDF scan tidak bisa dirender.")
    if not pdfplumber:
        warnings.append("pdfplumber belum terinstall, fallback text extraction berkurang.")
    if not pytesseract:
        warnings.append("Package pytesseract belum terinstall di virtualenv.")
    elif not tesseract_path:
        warnings.append("Tesseract OCR binary belum terinstall atau belum masuk PATH.")
    else:
        try:
            tesseract_version = str(pytesseract.get_tesseract_version())
        except Exception as exc:
            warnings.append(f"Tesseract OCR binary terdeteksi tetapi tidak bisa dibaca versinya: {exc}")
    if not parse_bool_env("OCR_ENABLE_PADDLEOCR", False):
        warnings.append("PaddleOCR nonaktif.")
    elif not paddleocr:
        warnings.append("PaddleOCR diaktifkan tetapi package belum terinstall.")

    return {
        "python_executable": sys.executable,
        "pymupdf_available": bool(fitz),
        "pdfplumber_available": bool(pdfplumber),
        "pytesseract_package_available": bool(pytesseract),
        "tesseract_binary_available": bool(tesseract_path),
        "tesseract_version": tesseract_version,
        "paddleocr_available": bool(paddleocr),
        "paddleocr_enabled": parse_bool_env("OCR_ENABLE_PADDLEOCR", False),
        "force_image_for_scanned": parse_bool_env("OCR_FORCE_IMAGE_FOR_SCANNED_DOCS", True),
        "tesseract_min_confidence": float(os.getenv("OCR_TESSERACT_MIN_CONFIDENCE", "60")),
        "tesseract_min_text_length": int(os.getenv("OCR_TESSERACT_MIN_TEXT_LENGTH", "50")),
        "warnings": warnings,
    }


def extract_document_text(file_path, document_type=None):
    """Engine terpusat OCR.

    Alur:
    1. Text extraction cepat (Level 1) — hanya untuk cek ada tidaknya text layer.
    2. Selalu render PDF ke image karena dokumen INTERMILAN hampir pasti scan kertas.
       Dikontrol oleh OCR_FORCE_IMAGE_FOR_SCANNED_DOCS (default: true).
    3. Tesseract (Level 2) sebagai engine utama.
    4. PaddleOCR (Level 3) jika Tesseract kosong/confidence rendah dan PaddleOCR aktif.
    5. Pilih hasil terbaik.
    """
    warnings = []
    tried = []
    candidates = []
    native_result = None
    tesseract_result = None
    paddleocr_result = None
    force_image = parse_bool_env("OCR_FORCE_IMAGE_FOR_SCANNED_DOCS", True)
    tesseract_min_confidence = float(os.getenv("OCR_TESSERACT_MIN_CONFIDENCE", "60"))
    log_file_diagnostics(file_path, "start")

    # ── Step 1: Text extraction cepat ────────────────────────────────────────
    if "text" in engine_order():
        tried.append("text")
        native_result = extract_text_native(file_path)
        warnings.extend(native_result.warnings)
        native_text_len = len(native_result.combined_text)
        ocr_log(f"engine=text raw_text_length={native_text_len}")

        # Jika tidak force image DAN native text cukup baik, gunakan text saja
        if not force_image and native_result.combined_text.strip() and has_usable_text(native_result, document_type):
            candidates.append(native_result)
            output = build_public_result(native_result, warnings, tried,
                                         paddleocr_called=False, paddleocr_text_length=0)
            output["native_text_length"] = native_text_len
            output["tesseract_called"] = False
            output["tesseract_text_length"] = 0
            output["tesseract_reason"] = "Native text cukup dan OCR_FORCE_IMAGE_FOR_SCANNED_DOCS=false; Tesseract tidak dipanggil."
            log_file_diagnostics(file_path, "done", f"best=text raw_text_length={native_text_len}")
            return output

        if native_result.combined_text.strip():
            candidates.append(native_result)
            if force_image:
                warnings.append(
                    f"Native text ditemukan ({native_text_len} karakter) tetapi OCR_FORCE_IMAGE_FOR_SCANNED_DOCS=true; "
                    "Tesseract tetap dijalankan karena dokumen kemungkinan hasil scan."
                )
            else:
                warnings.append(
                    f"Native text terlalu pendek ({native_text_len} karakter); fallback Tesseract dipanggil."
                )

    # ── Step 2: Pre-render PDF ke image (agar tidak render 2x) ───────────────
    rendered_images = None
    if "tesseract" in engine_order() or (parse_bool_env("OCR_ENABLE_PADDLEOCR", False) and "paddleocr" in engine_order()):
        try:
            rendered_images = render_pdf_pages(file_path)
        except Exception as exc:
            warning = f"PDF render gagal: {exc}. OCR tidak bisa dijalankan."
            warnings.append(warning)
            ocr_log(warning)

    # ── Step 3: Tesseract (Level 2) ───────────────────────────────────────────
    if "tesseract" in engine_order() and rendered_images is not None:
        tried.append("tesseract")
        tesseract_result = extract_tesseract(file_path, images=rendered_images)
        warnings.extend(tesseract_result.warnings)
        tesseract_text_len = len(tesseract_result.combined_text)
        ocr_log(f"engine=tesseract raw_text_length={tesseract_text_len}")
        if tesseract_result.combined_text.strip():
            candidates.append(tesseract_result)

    # ── Step 4: PaddleOCR (Level 3) — jika Tesseract gagal atau confidence rendah ───
    should_try_paddle = False
    if "paddleocr" in engine_order() and parse_bool_env("OCR_ENABLE_PADDLEOCR", False):
        if tesseract_result is None:
            should_try_paddle = True  # Tesseract tidak dijalankan
        elif not tesseract_result.combined_text.strip():
            should_try_paddle = True  # Tesseract kosong
        elif is_low_confidence(tesseract_result):
            should_try_paddle = True  # Confidence rendah
            warnings.append(
                f"Tesseract confidence rendah ({tesseract_result.confidence:.1f}% < {tesseract_min_confidence}%); "
                "PaddleOCR dijalankan sebagai fallback."
            )

    paddle_called = False
    paddle_text_len = 0
    if should_try_paddle and rendered_images is not None:
        tried.append("paddleocr")
        paddleocr_result = extract_paddleocr(file_path, images=rendered_images)
        warnings.extend(paddleocr_result.warnings)
        paddle_called = True
        paddle_text_len = len(paddleocr_result.combined_text)
        ocr_log(f"engine=paddleocr raw_text_length={paddle_text_len}")
        if paddleocr_result.combined_text.strip():
            candidates.append(paddleocr_result)
    elif "paddleocr" in engine_order() and not parse_bool_env("OCR_ENABLE_PADDLEOCR", False):
        tried.append("paddleocr")
        warnings.append("PaddleOCR dilewati karena OCR_ENABLE_PADDLEOCR=false.")

    # ── Step 5: Cloud OCR ─────────────────────────────────────────────────────
    for engine in engine_order():
        if engine in {"cloud", "google_document_ai", "azure_document_intelligence"}:
            tried.append(engine)
            result = extract_cloud_ocr(file_path)
            warnings.extend(result.warnings)
            if result.combined_text.strip():
                candidates.append(result)

    # ── Pilih hasil terbaik ───────────────────────────────────────────────────
    native_len = len(native_result.combined_text) if native_result else 0
    tesseract_len = len(tesseract_result.combined_text) if tesseract_result else 0

    if not candidates:
        status = "needs_manual_review" if tried else "failed"
        output = build_public_result(EngineResult("failed", [], warnings), warnings, tried, status=status,
                                     paddleocr_called=paddle_called, paddleocr_text_length=paddle_text_len)
        output["native_text_length"] = native_len
        output["tesseract_called"] = "tesseract" in tried
        output["tesseract_text_length"] = tesseract_len
        output["tesseract_reason"] = "Tesseract dipanggil tetapi teks kosong." if "tesseract" in tried else "Tesseract tidak dipanggil."
        log_file_diagnostics(file_path, "done", f"best=failed raw_text_length=0 errors={'; '.join(warnings[-5:])}")
        return output

    best = max(candidates, key=lambda result: result_score(result, document_type))
    output = build_public_result(best, warnings, tried,
                                 paddleocr_called=paddle_called, paddleocr_text_length=paddle_text_len)
    output["native_text_length"] = native_len
    output["tesseract_called"] = "tesseract" in tried
    output["tesseract_text_length"] = tesseract_len
    output["tesseract_reason"] = (
        "Native text kosong/pendek atau force_image aktif; Tesseract dipanggil."
        if "tesseract" in tried
        else "Native text cukup; Tesseract tidak dipanggil."
    )
    log_file_diagnostics(file_path, "done", f"best={best.engine} raw_text_length={len(output.get('combined_text', ''))}")
    return output


def extract_pdf_pages(file_path, use_ocr=False):
    original_order = os.getenv("OCR_ENGINE_ORDER")
    if not use_ocr:
        os.environ["OCR_ENGINE_ORDER"] = "text,tesseract"
    elif not original_order:
        os.environ["OCR_ENGINE_ORDER"] = "text,tesseract,paddleocr"
    try:
        result = extract_document_text(file_path)
    finally:
        if original_order is None:
            os.environ.pop("OCR_ENGINE_ORDER", None)
        else:
            os.environ["OCR_ENGINE_ORDER"] = original_order
    return result
