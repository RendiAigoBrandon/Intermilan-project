import os
import re
import zipfile
from decimal import Decimal

from django.db.models import Q
from django.utils.dateparse import parse_date as parse_iso_date

from apps.core.parsers import classify_document, extract_pdf_text, guess_number_from_filename, parse_spm_number_from_pages
from apps.dk.services import refresh_transaction_document_status
from apps.dk.models import TransactionDetail
from apps.documents.models import DocumentDriveLink
from apps.documents.services.checklist import mark_checklist_present
from apps.documents.services.google_drive import archive_file_link
from apps.paket_spm.models import PaketSPMUpload
from apps.sp2d.models import SP2DRaw


STATUS_LENGKAP = "Lengkap"
STATUS_LENGKAP_WARNING = "Lengkap dengan Peringatan Lampiran"
STATUS_BELUM_LENGKAP = "Belum Lengkap"
STATUS_REVIEW_OCR = "Perlu Review OCR"
STATUS_REVIEW_NOMOR = "Perlu Review Nomor"
STATUS_REVIEW_FIELD = "Perlu Review Field"
STATUS_REVIEW_JENIS = "Perlu Review Jenis SPM"
STATUS_DUPLIKAT = "Duplikat"
STATUS_GAGAL = "Gagal Diproses"

REKON_DATA_AWAL_SP2D = "Data awal dari SP2D"
REKON_COCOK_SP2D = "Cocok dengan SP2D"
REKON_COCOK_DK = "Cocok dengan D_K"
REKON_BELUM_SP2D = "Belum ada SP2D pembanding"
REKON_BELUM_DK = "Belum ada D_K pembanding"
REKON_BELUM_DIPASTIKAN = "Belum dipastikan"

MONTH_NAMES = {
    "JANUARI": 1,
    "FEBRUARI": 2,
    "MARET": 3,
    "APRIL": 4,
    "MEI": 5,
    "JUNI": 6,
    "JULI": 7,
    "AGUSTUS": 8,
    "SEPTEMBER": 9,
    "OKTOBER": 10,
    "NOVEMBER": 11,
    "DESEMBER": 12,
}


def normalize_key(value):
    return str(value or "").strip().upper()


def short_document_number(value):
    text = str(value or "").strip()
    return text.split("/", 1)[0].strip() if "/" in text else text


def clean_optional(value):
    text = str(value or "").strip()
    return "" if text == "-" else text


def _add_probe_candidate(candidates, number, source, confidence=0.0):
    number = normalize_key(number)
    if not number:
        return
    if not any(item["number"] == number and item["source"] == source for item in candidates):
        candidates.append({"number": number, "source": source, "confidence": confidence})


def _year_from_text(text):
    match = re.search(r"\b(20\d{2})\b", text or "")
    return int(match.group(1)) if match else None


def probe_package_identity(file_path, original_filename, input_satker="", input_tahun=None, kind=""):
    """Lightweight identity probe before expensive OCR/parser work."""
    candidates = []
    warnings = []
    document_types = set()
    filename_number = guess_number_from_filename(original_filename or file_path, "SPM")
    _add_probe_candidate(candidates, filename_number, "filename", 55)

    native_text = ""
    if kind == "zip" or str(original_filename).lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(file_path) as archive:
                for name in archive.namelist()[:1000]:
                    if name.endswith("/"):
                        continue
                    document_types.add(classify_document(name, ""))
                    _add_probe_candidate(candidates, guess_number_from_filename(name, "SPM"), f"zip:{os.path.basename(name)}", 50)
        except Exception as exc:
            warnings.append(f"ZIP probe gagal: {exc}")
    elif str(original_filename).lower().endswith(".pdf"):
        try:
            extracted = extract_pdf_text(file_path, ocr=False)
            page_details = extracted.get("page_details") or [
                {"text": text, "extracted_text": text, "page_number": index}
                for index, text in enumerate(extracted.get("pages", [])[:2], start=1)
            ]
            page_details = page_details[:2]
            native_text = "\n".join((page.get("text") or page.get("extracted_text") or "") for page in page_details)
            document_types.add(classify_document(original_filename, native_text))
            per_page = parse_spm_number_from_pages(page_details)
            _add_probe_candidate(candidates, per_page.get("no_spm"), "header_spm_native", 85)
            _add_probe_candidate(candidates, per_page.get("no_spp"), "header_spp_native", 80)
        except Exception as exc:
            warnings.append(f"Native probe gagal: {exc}")
            document_types.add(classify_document(original_filename, ""))
    else:
        document_types.add(classify_document(original_filename, ""))

    document_types.discard("UNKNOWN")
    tahun = int(input_tahun) if str(input_tahun or "").isdigit() else _year_from_text(native_text)
    satker = str(input_satker or "").strip()
    numbers = [item["number"] for item in candidates]
    distinct_numbers = sorted(set(numbers))
    exact_matches = []
    if satker and tahun and distinct_numbers:
        query = TransactionDetail.objects.filter(satker_code=satker)
        nomor_query = Q()
        for number in distinct_numbers:
            nomor_query |= Q(nomor_spm__iexact=number)
        exact_matches = list(
            query.filter(nomor_query)
            .filter(Q(tanggal_spm__year=tahun) | Q(tanggal_spm__isnull=True))
            .order_by("id")
        )

    matched_number = normalize_key(exact_matches[0].nomor_spm) if exact_matches else ""
    conflicting_numbers = len(distinct_numbers) > 1
    needs_review = conflicting_numbers and not matched_number
    if conflicting_numbers:
        warnings.append("Identity probe menemukan beberapa kandidat nomor SPM/SPP; perlu review sebelum kait otomatis.")
    if exact_matches and conflicting_numbers and matched_number not in distinct_numbers:
        needs_review = True
    return {
        "candidates": candidates,
        "document_types": sorted(document_types) or ["SPM"],
        "satker_code": satker,
        "tahun": tahun,
        "matched_number": matched_number,
        "exact_transaction_ids": [item.id for item in exact_matches],
        "needs_review": needs_review,
        "warnings": warnings,
        "method": "identity_probe_native",
    }


def parsed_from_identity_probe(probe, original_filename):
    number = probe.get("matched_number") or next((item["number"] for item in probe.get("candidates", []) if item.get("number")), "")
    metadata = {
        "nomor_spm": number,
        "nomor_spm_final": number,
        "satker_app_code": probe.get("satker_code") or "",
        "tahun": probe.get("tahun"),
        "nomor_spm_candidates": probe.get("candidates") or [],
        "nomor_spm_review_status": "Perlu Review" if probe.get("needs_review") else "OK",
        "nomor_spm_reason": "; ".join(probe.get("warnings") or []),
    }
    return {
        "ok": not probe.get("needs_review"),
        "identity_probe": probe,
        "files": [
            {
                "file_name": original_filename,
                "type": doc_type,
                "status": "identity_probe",
                "parse_status": "needs_manual_review" if probe.get("needs_review") else "matched_existing_dk",
                "method": probe.get("method", "identity_probe"),
                "warnings": probe.get("warnings") or [],
            }
            for doc_type in (probe.get("document_types") or ["SPM"])
        ],
        "spm": {
            "file_name": original_filename,
            "status": "needs_manual_review",
            "method": "identity_probe",
            "warnings": probe.get("warnings") or [],
            "metadata": metadata,
            "detail_items": [],
            "akun_rows": [],
        },
        "drpp": None,
        "drpps": [],
        "kw_by_drpp": {},
        "kw_items": [],
        "warnings": probe.get("warnings") or [],
    }


def parse_user_decimal(value):
    text = clean_optional(value)
    if not text:
        return Decimal("0")
    text = text.replace("Rp", "").replace("rp", "").replace(" ", "")
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


def parse_month_number(value):
    text = clean_optional(value).upper()
    if text.isdigit():
        month = int(text)
        return month if 1 <= month <= 12 else None
    return MONTH_NAMES.get(text)


def lampiran_warnings(parsed):
    warnings = []
    for warning in (parsed.get("warnings") or []) + ((parsed.get("spm") or {}).get("warnings") or []):
        if "lampiran" in str(warning).lower() and warning not in warnings:
            warnings.append(warning)
    return warnings


def money_value(value):
    if value in (None, ""):
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(value))


def item_bruto_value(item):
    return money_value(
        item.get("bruto")
        or item.get("nilai_bruto")
        or item.get("jumlah")
        or item.get("nilai")
    )


def item_deduction_value(item):
    return sum(
        (
            money_value(item.get(field))
            for field in ("pph21", "pph22", "pph23", "ppn", "potongan", "pajak")
        ),
        Decimal("0"),
    )


def item_netto_value(item, bruto=None):
    explicit = money_value(item.get("netto") or item.get("nilai_netto"))
    if explicit > 0:
        return explicit
    bruto = item_bruto_value(item) if bruto is None else bruto
    deduction = item_deduction_value(item)
    return bruto - deduction if deduction > 0 and bruto >= deduction else bruto


def date_value(value):
    if not value:
        return None
    if hasattr(value, "year"):
        return value
    return parse_iso_date(str(value))


def is_gup(jenis_spm):
    text = normalize_key(jenis_spm)
    return bool(text) and ("GUP" in text or ("GU" in text and "TUP" not in text))


def is_tup(jenis_spm):
    text = normalize_key(jenis_spm)
    return bool(text) and "TUP" in text


def is_ls(jenis_spm):
    text = normalize_key(jenis_spm)
    return bool(text) and text.startswith("LS")


def requires_drpp_kw(jenis_spm):
    """GU/GUP/TUP wajib DRPP+KW. LS tidak wajib. Jenis kosong = belum diketahui."""
    if not jenis_spm or not jenis_spm.strip():
        return None  # belum diketahui
    return is_gup(jenis_spm) or is_tup(jenis_spm)


def parsed_is_filename_only(parsed):
    files = parsed.get("files", [])
    if files and all((item.get("method") or "") == "filename" for item in files if item.get("type") != "SKIPPED"):
        return True
    spm = parsed.get("spm") or {}
    drpps = parsed.get("drpps") or ([parsed.get("drpp")] if parsed.get("drpp") else [])
    engines = set(spm.get("engines_tried") or [])
    for drpp in drpps:
        engines.update((drpp or {}).get("engines_tried") or [])
    return engines == {"filename"}


def package_metadata(parsed):
    spm = parsed.get("spm") or {}
    drpp = parsed.get("drpp") or {}
    drpps = parsed.get("drpps") or ([drpp] if drpp else [])
    spm_meta = spm.get("metadata", {})
    context = parsed.get("paket_context") or {}
    drpp_metas = [(item or {}).get("metadata", {}) for item in drpps]
    drpp_meta = drpp_metas[0] if drpp_metas else {}
    drpp_numbers = [normalize_key(meta.get("nomor_drpp")) for meta in drpp_metas if normalize_key(meta.get("nomor_drpp"))]
    kw_items = parsed.get("kw_items") or []
    kw_match_items = [
        {
            "no_bukti": short_document_number(item.get("no_bukti")),
            "akun": normalize_key(item.get("akun")),
            "jumlah": money_value(item.get("jumlah") or item.get("bruto")),
            "pembebanan": clean_optional(item.get("pembebanan")),
            "no_drpp": normalize_key(item.get("no_drpp")),
        }
        for item in kw_items
        if normalize_key(item.get("akun")) and money_value(item.get("jumlah") or item.get("bruto")) > 0
    ]
    detail_items = spm.get("detail_items") or []
    kw_numbers = [normalize_key(item.get("no_bukti")) for item in kw_items if normalize_key(item.get("no_bukti"))]
    drpp_total = sum((money_value(meta.get("total")) for meta in drpp_metas), Decimal("0"))
    total = money_value(spm_meta.get("total_pembayaran") or drpp_total or sum((money_value(item.get("jumlah")) for item in kw_items), Decimal("0")))
    tanggal_spm = date_value(spm_meta.get("tanggal_spm") or drpp_meta.get("tanggal_spm"))
    tanggal_sp2d = date_value(spm_meta.get("tanggal_sp2d")) or next(
        (date_value(item.get("tanggal_sp2d")) for item in detail_items if date_value(item.get("tanggal_sp2d"))),
        None,
    )
    tahun = getattr(tanggal_spm, "year", None) or context.get("tahun") or drpp_meta.get("tahun") or spm_meta.get("tahun")
    return {
        "nomor_spm": normalize_key(spm_meta.get("nomor_spm") or drpp_meta.get("nomor_spm")),
        "nomor_spm_ocr": normalize_key(spm_meta.get("nomor_spm_ocr")),
        "nomor_spm_filename": normalize_key(spm_meta.get("nomor_spm_filename")),
        "nomor_spm_final": normalize_key(spm_meta.get("nomor_spm_final") or spm_meta.get("nomor_spm") or drpp_meta.get("nomor_spm")),
        "nomor_spm_final_source": spm_meta.get("nomor_spm_final_source", ""),
        "nomor_spm_conflict": bool(spm_meta.get("nomor_spm_conflict")),
        "nomor_spm_review_status": spm_meta.get("nomor_spm_review_status", ""),
        "nomor_spm_reason": spm_meta.get("nomor_spm_reason", ""),
        "nomor_spm_matching": "",
        "nomor_spp": normalize_key(spm_meta.get("nomor_spp")),
        "nomor_sp2d": normalize_key(spm_meta.get("nomor_sp2d")),
        "nomor_invoice": normalize_key(spm_meta.get("nomor_invoice")),
        "nomor_drpp": ", ".join(drpp_numbers)[:100] or normalize_key(spm_meta.get("nomor_drpp")),
        "nomor_drpp_list": drpp_numbers,
        "satker_code": str(spm_meta.get("satker_app_code") or spm_meta.get("satker_code") or drpp_meta.get("satker_app_code") or context.get("satker_code") or drpp_meta.get("satker_code") or "").strip(),
        "satker_djpb_code": str(spm_meta.get("satker_djpb_code") or "").strip(),
        "satker_app_code": str(spm_meta.get("satker_app_code") or "").strip(),
        "satker_app_name": str(spm_meta.get("satker_app_name") or "").strip(),
        "jenis_spm": str(spm_meta.get("jenis_spm") or drpp_meta.get("jenis_spm") or "").strip(),
        "cara_pembayaran": str(spm_meta.get("cara_pembayaran") or "").strip(),
        "tanggal_spm": tanggal_spm,
        "tahun": int(tahun) if str(tahun or "").isdigit() else None,
        "tanggal_sp2d": tanggal_sp2d,
        "total": total,
        "kw_count": len(kw_items),
        "kw_numbers": kw_numbers,
        "kw_match_items": kw_match_items,
        "akun_count": len([row for row in kw_items if row.get("akun")]) or len(detail_items) or len(spm.get("akun_rows") or []),
        "nomor_spm_candidates": spm_meta.get("nomor_spm_candidates") or [],
        "has_spm": bool(spm and spm.get("status") in {"parsed_text", "parsed_ocr", "needs_manual_review"}),
        "has_drpp": any(bool(item and item.get("status") in {"parsed_text", "parsed_ocr", "needs_manual_review"}) for item in drpps),
        "drpp_count": len(drpp_numbers) or len(drpps),
        "spm_status": spm.get("status", ""),
        "drpp_status": drpp.get("status", ""),
        "best_engine": spm.get("best_engine") or drpp.get("best_engine") or "-",
        "ocr_status": spm.get("status") or drpp.get("status") or "needs_manual_review",
    }


def analyze_matching_transactions(meta):
    nomor_candidates = []
    for value in [meta.get("nomor_spm"), meta.get("nomor_spm_filename"), meta.get("nomor_spp"), meta.get("nomor_invoice", "").split("/", 1)[0]]:
        value = normalize_key(value)
        if value and value not in nomor_candidates:
            nomor_candidates.append(value)
    for item in meta.get("nomor_spm_candidates") or []:
        value = normalize_key(item.get("number") if isinstance(item, dict) else item)
        if value and value not in nomor_candidates:
            nomor_candidates.append(value)
    satker_clean = normalize_key(meta.get("satker_code"))
    tanggal_spm = meta.get("tanggal_spm")
    tahun_spm = getattr(tanggal_spm, "year", None) or meta.get("tahun")

    if not satker_clean:
        return None, []

    query = TransactionDetail.objects.filter(satker_code=satker_clean)
    if nomor_candidates:
        nomor_query = Q()
        for nomor in nomor_candidates:
            nomor_query |= Q(nomor_spm__iexact=nomor)
        query = query.filter(nomor_query)
        if tahun_spm:
            query = query.filter(Q(tanggal_spm__year=tahun_spm) | Q(tanggal_spm__isnull=True))

        existing = list(query)
    else:
        existing = _match_existing_dk_from_kw_items(satker_clean, tahun_spm, meta.get("kw_match_items") or [])
    if not existing:
        return None, []

    first = existing[0]
    best_match = {
        "id": first.id,
        "nomor_spm": first.nomor_spm,
        "satker_code": first.satker_code,
        "akun": first.akun,
        "tanggal_spm": first.tanggal_spm.isoformat() if first.tanggal_spm else "",
        "nilai_bruto": str(first.nilai_bruto) if first.nilai_bruto else "0",
        "nilai_netto": str(first.nilai_netto) if first.nilai_netto else "0",
    }

    # We return the first one as best_match, and the list of existing as candidates
    candidates = [{"transaction": best_match, "score": 100, "reasons": ["Exact match"]}]
    return best_match, candidates


def _match_existing_dk_from_kw_items(satker_clean, tahun_spm, kw_items):
    if not kw_items:
        return []
    query = TransactionDetail.objects.filter(satker_code=satker_clean)
    if tahun_spm:
        query = query.filter(Q(tanggal_spm__year=tahun_spm) | Q(tanggal_spm__isnull=True))
    rows = list(query)
    matched = []
    used_ids = set()
    for item in kw_items:
        item_kw = normalize_key(item.get("no_bukti"))
        item_akun = normalize_key(item.get("akun"))
        item_amount = money_value(item.get("jumlah"))
        item_pembebanan = normalize_key(item.get("pembebanan"))
        candidates = []
        for row in rows:
            if row.id in used_ids:
                continue
            if item_akun and normalize_key(row.akun) != item_akun:
                continue
            if item_amount and abs((row.nilai_bruto or Decimal("0")) - item_amount) > Decimal("1"):
                continue
            if item_kw and normalize_key(short_document_number(row.no_kuitansi)) != item_kw:
                continue
            if item_pembebanan and normalize_key(row.pembebanan) and normalize_key(row.pembebanan) != item_pembebanan:
                continue
            candidates.append(row)
        if len(candidates) != 1:
            return []
        used_ids.add(candidates[0].id)
        matched.append(candidates[0])
    nomor_set = {normalize_key(row.nomor_spm) for row in matched if normalize_key(row.nomor_spm)}
    return matched if len(nomor_set) == 1 else []


def find_matching_sp2d(meta):
    if not meta["nomor_spm"] and not meta["total"]:
        return None
    query = SP2DRaw.objects.all()
    conditions = Q()
    if meta["nomor_spm"]:
        conditions |= Q(nomor_spm_extracted__iexact=meta["nomor_spm"]) | Q(deskripsi__icontains=meta["nomor_spm"])
    if meta["total"]:
        conditions |= Q(nilai_spm=meta["total"]) | Q(nilai_sp2d=meta["total"])
    query = query.filter(conditions)
    if meta["satker_code"]:
        satker_match = query.filter(satker_code=meta["satker_code"]).first()
        if satker_match:
            return satker_match
    return query.first()


def find_duplicate_package(meta, original_filename="", current_paket_id=None):
    query = PaketSPMUpload.objects.all()
    if current_paket_id:
        query = query.exclude(id=current_paket_id)

    conditions = Q()
    if meta["nomor_spm"]:
        conditions |= Q(nomor_spm__iexact=meta["nomor_spm"])
    if original_filename:
        conditions |= Q(original_filename__iexact=original_filename)
    if not conditions:
        return None
    duplicate = query.filter(conditions).first()
    if duplicate:
        return duplicate
    doc_conditions = Q()
    if meta["nomor_spm"]:
        doc_conditions |= Q(nomor_spm__iexact=meta["nomor_spm"])
    if meta["nomor_drpp"]:
        for no_drpp in meta.get("nomor_drpp_list") or [meta["nomor_drpp"]]:
            doc_conditions |= Q(no_drpp__iexact=no_drpp)
    for item in meta.get("kw_numbers", []):
        doc_conditions |= Q(no_kuitansi__icontains=item)
    if original_filename:
        doc_conditions |= Q(nama_file__iexact=original_filename) | Q(catatan__icontains=original_filename)
    upload_link_markers = (
        Q(catatan__icontains="source=Paket SPM")
        | Q(catatan__icontains="source=checklist_dk")
        | Q(catatan__icontains="source=checklist_dk_extracted")
        | Q(catatan__icontains="upload_test=true")
        | Q(catatan__icontains="parser_status=")
    )
    if doc_conditions and DocumentDriveLink.objects.filter(doc_conditions).filter(upload_link_markers).exists():
        return "document_link"
    return None


def evaluate_document_status(parsed):
    meta = package_metadata(parsed)
    notes = lampiran_warnings(parsed)
    if not parsed.get("files") and not parsed.get("spm") and not parsed.get("drpp"):
        return STATUS_GAGAL, ["Dokumen tidak bisa diklasifikasi atau diproses."]
    if meta.get("nomor_spm_conflict"):
        return STATUS_REVIEW_NOMOR, ["Nomor SPM OCR dan filename berbeda. Pilih nomor yang benar sebelum commit."]
    if parsed_is_filename_only(parsed):
        return STATUS_REVIEW_OCR, ["Hanya metadata filename yang terbaca; perlu review OCR/manual."]
    if not meta["has_spm"] and not meta["has_drpp"]:
        return STATUS_REVIEW_OCR, ["OCR belum membaca SPM/DRPP secara memadai."]

    jenis_spm = meta["jenis_spm"]
    drpp_kw_required = requires_drpp_kw(jenis_spm)

    # Jika jenis SPM belum terbaca dan tidak bisa dipastikan
    if drpp_kw_required is None and meta["has_spm"] and not meta["has_drpp"]:
        return STATUS_REVIEW_JENIS, [
            "Jenis SPM belum terbaca. Tidak bisa memastikan apakah DRPP/KW diperlukan. "
            "Periksa jenis SPM (GU/GUP/TUP atau LS) dari dokumen asli."
        ]

    # GU/GUP/TUP: wajib DRPP dan KW
    if drpp_kw_required and (not meta["has_drpp"] or not meta["kw_count"]):
        keterangan = f"SPM {jenis_spm} membutuhkan DRPP dan KW/Bukti pengeluaran."
        if is_gup(jenis_spm):
            keterangan = f"SPM GUP ({jenis_spm}) membutuhkan DRPP dan KW/Bukti pengeluaran."
        elif is_tup(jenis_spm):
            keterangan = f"SPM TUP ({jenis_spm}) membutuhkan DRPP dan KW/Bukti pengeluaran."
        return STATUS_BELUM_LENGKAP, [keterangan]

    if meta["total"] <= 0:
        return STATUS_REVIEW_OCR, ["Nilai total belum terbaca."]
    if not meta["akun_count"]:
        return STATUS_REVIEW_OCR, ["Akun/COA atau item pengeluaran belum terbaca."]

    # LS: tidak wajib DRPP/KW, tapi cek field penting
    if is_ls(jenis_spm) or drpp_kw_required is False:
        # Cek apakah field penting terbaca
        spm_meta = (parsed.get("spm") or {}).get("metadata", {})
        missing_fields = []
        if not spm_meta.get("nomor_sp2d") and not meta.get("nomor_sp2d"):
            missing_fields.append("No SP2D")
        if missing_fields:
            return STATUS_REVIEW_FIELD, [
                f"SPM {jenis_spm}: field berikut belum terbaca dari dokumen: {', '.join(missing_fields)}. "
                "Periksa lampiran Detail Pengeluaran dan Potongan."
            ]
        return "Lengkap SPM Utama", ["SPM berhasil dibaca dan field penting lengkap. Dokumen pendukung LS mungkin perlu review manual."]

    # GU/GUP/TUP dengan DRPP dan KW: cek field tambahan
    if drpp_kw_required and meta["has_drpp"] and meta["kw_count"]:
        return (STATUS_LENGKAP_WARNING if notes else STATUS_LENGKAP), notes

    # Hanya SPM saja yang diupload (bukan LS, dan bukan sudah Lengkap)
    if meta["has_spm"] and not meta["has_drpp"] and not meta["kw_count"]:
        return STATUS_BELUM_LENGKAP, ["SPM berhasil dibaca. DRPP dan KW/Bukti pengeluaran belum diupload."]

    return STATUS_LENGKAP, notes


def is_followup_drpp_kw(parsed):
    meta = package_metadata(parsed)
    return bool(meta["has_drpp"] and not meta["has_spm"])


def has_standalone_kw_without_drpp(parsed):
    files = parsed.get("files") or []
    has_kw_file = any((item.get("type") or "").upper() == "KW" for item in files)
    has_drpp_file = any((item.get("type") or "").upper() == "DRPP" for item in files)
    return has_kw_file and not has_drpp_file and not package_metadata(parsed).get("has_drpp")


def spm_table_parser_needs_review(parsed):
    spm = parsed.get("spm") or {}
    summary = (spm.get("metadata") or {}).get("detail_parse_summary") or {}
    source = str(summary.get("source") or "")
    if not spm or (parsed.get("kw_items") or []):
        return False
    return source in {"PERLU_REVIEW_PARSER_TABEL", "DETAIL_SPP_SPM_SP2D_REVIEW", "fallback_total"}


def exact_transactions_for_package(parsed, paket=None):
    meta = package_metadata(parsed)
    satker = normalize_key(meta.get("satker_code") or getattr(paket, "satker_code", ""))
    nomor_spm = normalize_key(meta.get("nomor_spm") or getattr(paket, "nomor_spm", ""))
    tahun = meta.get("tahun") or getattr(getattr(paket, "tanggal_spm", None), "year", None) or getattr(paket, "tahun", None)
    if not satker or not nomor_spm:
        return []
    query = TransactionDetail.objects.filter(satker_code=satker, nomor_spm__iexact=nomor_spm)
    if tahun:
        query = query.filter(Q(tanggal_spm__year=tahun) | Q(tanggal_spm__isnull=True))
    return list(query.order_by("id"))


def build_package_decision(parsed, original_filename="", forced_sp2d=None, current_paket_id=None):
    meta = package_metadata(parsed)
    document_status, notes = evaluate_document_status(parsed)
    duplicate = find_duplicate_package(meta, original_filename, current_paket_id=current_paket_id)
    matched_transaction, candidates = analyze_matching_transactions(meta)
    matched_sp2d = forced_sp2d or find_matching_sp2d(meta)
    followup_only = is_followup_drpp_kw(parsed)

    if has_standalone_kw_without_drpp(parsed):
        return {
            "document_status": STATUS_REVIEW_OCR,
            "reconciliation_status": REKON_BELUM_DIPASTIKAN,
            "commit_action": "review_only",
            "commit_label": "KW wajib bersama DRPP",
            "can_commit": False,
            "decision_text": "KW/Bukti tunggal tidak boleh membuat transaksi. Upload DRPP terkait bersama KW.",
            "notes": notes + ["KW/Bukti wajib diunggah bersama DRPP."],
            "meta": meta,
            "matched_transaction": None,
            "matched_sp2d": matched_sp2d,
            "duplicate": None,
            "candidates": candidates,
        }

    if matched_transaction and matched_transaction.get("nomor_spm"):
        meta["nomor_spm_matching"] = normalize_key(matched_transaction.get("nomor_spm"))
        _apply_matched_nomor_spm_to_parsed(parsed, meta["nomor_spm_matching"])
    elif candidates and candidates[0]["transaction"].get("nomor_spm"):
        meta["nomor_spm_matching"] = normalize_key(candidates[0]["transaction"].get("nomor_spm"))
    elif matched_sp2d and matched_sp2d.nomor_spm_extracted:
        meta["nomor_spm_matching"] = normalize_key(matched_sp2d.nomor_spm_extracted)

    if matched_transaction and meta.get("nomor_spm_matching") and meta.get("nomor_spm") != meta["nomor_spm_matching"]:
        warning = f"Nomor badan SPM {meta.get('nomor_spm_ocr') or meta.get('nomor_spm')} berbeda dari nomor D_K/SPP {meta['nomor_spm_matching']}; memakai nomor D_K untuk matching."
        meta["nomor_spm"] = meta["nomor_spm_matching"]
        meta["nomor_spm_final"] = meta["nomor_spm_matching"]
        meta["nomor_spm_conflict"] = False
        meta["nomor_spm_review_status"] = "OK"
        meta["nomor_spm_reason"] = warning
        if parsed.get("spm") and "metadata" in parsed["spm"]:
            parsed["spm"]["metadata"]["nomor_spm"] = meta["nomor_spm_matching"]
            parsed["spm"]["metadata"]["nomor_spm_final"] = meta["nomor_spm_matching"]
            parsed["spm"]["metadata"]["nomor_spm_conflict"] = False
            parsed["spm"]["metadata"]["nomor_spm_review_status"] = "OK"
            parsed["spm"]["metadata"]["nomor_spm_reason"] = warning
            parsed["spm"].setdefault("warnings", [])
            if warning not in parsed["spm"]["warnings"]:
                parsed["spm"]["warnings"].insert(0, warning)
        _apply_matched_nomor_spm_to_parsed(parsed, meta["nomor_spm_matching"])
        notes = [warning] + [note for note in notes if note != warning]
        document_status, fresh_notes = evaluate_document_status(parsed)
        notes = [warning] + [note for note in fresh_notes if note != warning]

    # Resolusi konflik SPM otomatis jika cocok dengan D_K/SP2D
    if meta.get("nomor_spm_conflict") and meta.get("nomor_spm_ocr") and meta.get("nomor_spm_matching"):
        if meta["nomor_spm_ocr"] == meta["nomor_spm_matching"]:
            meta["nomor_spm_conflict"] = False
            meta["nomor_spm_review_status"] = "OK"
            meta["nomor_spm_final"] = meta["nomor_spm_ocr"]
            meta["nomor_spm_reason"] = "Konflik OCR vs Filename otomatis diselesaikan karena cocok dengan D_K/SP2D/Kandidat."
            if parsed.get("spm") and "metadata" in parsed["spm"]:
                parsed["spm"]["metadata"]["nomor_spm_conflict"] = False
                parsed["spm"]["metadata"]["nomor_spm_review_status"] = "OK"
                parsed["spm"]["metadata"]["nomor_spm_reason"] = meta["nomor_spm_reason"]
                if "warnings" in parsed["spm"]:
                    parsed["spm"]["warnings"] = [w for w in parsed["spm"]["warnings"] if "berbeda" not in w.lower()]
            # Update ulang document status
            document_status, notes = evaluate_document_status(parsed)

    if duplicate and matched_transaction and not followup_only:
        return {
            "document_status": STATUS_DUPLIKAT,
            "reconciliation_status": REKON_BELUM_DIPASTIKAN,
            "commit_action": "link_existing",
            "commit_label": "Kaitkan Dokumen ke Data Existing",
            "can_commit": True,
            "decision_text": "Dokumen sudah pernah diupload dan cocok dengan D_K existing.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": matched_transaction,
            "matched_sp2d": matched_sp2d,
            "duplicate": duplicate,
        }

    if document_status == STATUS_GAGAL:
        return {
            "document_status": document_status,
            "reconciliation_status": REKON_BELUM_DIPASTIKAN,
            "commit_action": "failed",
            "commit_label": "Gagal Diproses",
            "can_commit": False,
            "decision_text": "Dokumen tidak bisa diproses sebagai paket SPM.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": matched_transaction,
            "matched_sp2d": matched_sp2d,
            "duplicate": None,
        }

    if followup_only and matched_transaction:
        return {
            "document_status": document_status,
            "reconciliation_status": REKON_COCOK_DK,
            "commit_action": "update_existing",
            "commit_label": "PERBARUI D_K EXISTING",
            "can_commit": True,
            "decision_text": "DRPP/KW akan memperbarui D_K existing berdasarkan exact match satker, tahun, dan nomor SPM.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": matched_transaction,
            "matched_sp2d": matched_sp2d,
            "duplicate": None,
            "candidates": candidates,
        }

    if followup_only and not matched_transaction:
        return {
            "document_status": document_status,
            "reconciliation_status": REKON_BELUM_DK,
            "commit_action": "review_only",
            "commit_label": "SPM utama belum ada di D_K",
            "can_commit": False,
            "decision_text": "Paket DRPP/KW tanpa SPM utama tidak dibuat otomatis. Upload atau pilih SPM utama terlebih dahulu.",
            "notes": notes + ["SPM utama belum ada di D_K."],
            "meta": meta,
            "matched_transaction": None,
            "matched_sp2d": matched_sp2d,
            "duplicate": None,
            "candidates": candidates,
        }

    if meta.get("satker_djpb_code") and not meta.get("satker_app_code"):
        return {
            "document_status": document_status,
            "reconciliation_status": "Belum dipastikan",
            "commit_action": "review_manual",
            "commit_label": "Perlu Validasi",
            "can_commit": True,
            "decision_text": f"Satker dokumen terbaca {meta.get('satker_djpb_code')}/{meta.get('satker_name_ocr')}, tetapi mapping ke satker aplikasi belum pasti.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": matched_transaction,
            "matched_sp2d": matched_sp2d,
            "duplicate": duplicate,
            "candidates": candidates,
        }

    if not matched_transaction and candidates:
        return {
            "document_status": document_status,
            "reconciliation_status": "Belum dipastikan",
            "commit_action": "review_manual",
            "commit_label": "Perlu Validasi",
            "can_commit": True,
            "decision_text": "Ditemukan beberapa kandidat D_K namun tidak ada yang memenuhi syarat cocok kuat (poin >= 70 & satker sama). Silakan kaitkan manual atau buat D_K baru.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": None,
            "matched_sp2d": matched_sp2d,
            "duplicate": None,
            "candidates": candidates,
        }

    if document_status == STATUS_REVIEW_NOMOR:
        return {
            "document_status": document_status,
            "reconciliation_status": REKON_BELUM_DIPASTIKAN,
            "commit_action": "review_only",
            "commit_label": "Simpan Draft Review",
            "can_commit": True,
            "decision_text": "Nomor SPM OCR dan filename berbeda. Simpan sebagai draft review manual sebelum menentukan nomor final.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": matched_transaction,
            "matched_sp2d": matched_sp2d,
            "duplicate": None,
            "candidates": candidates,
        }

    if document_status == STATUS_REVIEW_OCR and matched_transaction:
        return {
            "document_status": document_status,
            "reconciliation_status": REKON_COCOK_DK,
            "commit_action": "link_existing",
            "commit_label": "Simpan Draft Review",
            "can_commit": True,
            "decision_text": "File akan disimpan dan dikaitkan ke D_K existing dengan status Perlu Review OCR.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": matched_transaction,
            "matched_sp2d": matched_sp2d,
            "duplicate": None,
            "candidates": candidates,
        }

    if spm_table_parser_needs_review(parsed) and not matched_transaction:
        return {
            "document_status": STATUS_REVIEW_OCR,
            "reconciliation_status": REKON_BELUM_DIPASTIKAN,
            "commit_action": "review_only",
            "commit_label": "Perlu Review Parser Tabel",
            "can_commit": False,
            "decision_text": "Parser tabel v2 belum menghasilkan rincian valid. D_K baru tidak boleh dibuat dari fallback teks datar.",
            "notes": notes + ["Parser tabel v2 belum valid; tidak memakai fallback legacy untuk auto-commit."],
            "meta": meta,
            "matched_transaction": None,
            "matched_sp2d": matched_sp2d,
            "duplicate": None,
            "candidates": candidates,
        }

    if document_status == STATUS_REVIEW_OCR and matched_sp2d:
        return {
            "document_status": document_status,
            "reconciliation_status": REKON_COCOK_SP2D,
            "commit_action": "link_sp2d",
            "commit_label": "Simpan Draft Review",
            "can_commit": True,
            "decision_text": "File akan disimpan dan dikaitkan ke SP2D existing dengan status Perlu Review OCR.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": None,
            "matched_sp2d": matched_sp2d,
            "duplicate": None,
            "candidates": candidates,
        }

    if document_status == STATUS_REVIEW_OCR:
        return {
            "document_status": document_status,
            "reconciliation_status": REKON_BELUM_DIPASTIKAN,
            "commit_action": "create_from_package",
            "commit_label": "Simpan Draft Review",
            "can_commit": True,
            "decision_text": "File akan disimpan sebagai data Paket SPM mandiri dan perlu review OCR.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": None,
            "matched_sp2d": None,
            "duplicate": None,
            "candidates": candidates,
        }

    if matched_transaction:
        return {
            "document_status": document_status,
            "reconciliation_status": REKON_COCOK_DK,
            "commit_action": "link_existing",
            "commit_label": "Kaitkan Dokumen ke Data Existing",
            "can_commit": True,
            "decision_text": "Akan dikaitkan ke data existing.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": matched_transaction,
            "matched_sp2d": matched_sp2d,
            "duplicate": None,
            "candidates": candidates,
        }

    if matched_sp2d:
        return {
            "document_status": document_status,
            "reconciliation_status": REKON_COCOK_SP2D,
            "commit_action": "create_from_package",
            "commit_label": "Simpan ke D_K Baru",
            "can_commit": True,
            "decision_text": "SP2D pembanding ditemukan; D_K baru akan dibuat dan dikaitkan ke SP2D tersebut.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": None,
            "matched_sp2d": matched_sp2d,
            "duplicate": None,
            "candidates": candidates,
        }

    if document_status == STATUS_BELUM_LENGKAP and matched_transaction:
        return {
            "document_status": document_status,
            "reconciliation_status": REKON_COCOK_DK,
            "commit_action": "link_existing",
            "commit_label": "Kaitkan Dokumen ke Data Existing",
            "can_commit": True,
            "decision_text": "SPM akan dikaitkan ke data D_K existing. Checklist SPM jadi Ada; DRPP dan KW tetap Belum Ada sampai diupload.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": matched_transaction,
            "matched_sp2d": matched_sp2d,
            "duplicate": None,
            "candidates": candidates,
        }

    if document_status == STATUS_BELUM_LENGKAP:
        return {
            "document_status": document_status,
            "reconciliation_status": REKON_BELUM_SP2D,
            "commit_action": "create_from_package",
            "commit_label": "Simpan Draft Review",
            "can_commit": True,
            "decision_text": "Dokumen belum lengkap, tetapi file dan metadata akan tetap disimpan untuk dilengkapi.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": None,
            "matched_sp2d": None,
            "duplicate": None,
            "candidates": candidates,
        }

    return {
        "document_status": document_status,
        "reconciliation_status": f"{REKON_BELUM_SP2D} / {REKON_BELUM_DK}",
        "commit_action": "create_from_package",
        "commit_label": "Simpan ke D_K Baru",
        "can_commit": True,
        "decision_text": "Akan dibuat sebagai data baru di INTERMILAN dari sumber Paket SPM.",
        "notes": notes,
        "meta": meta,
        "matched_transaction": None,
        "matched_sp2d": None,
        "duplicate": None,
        "candidates": candidates,
    }


def transaction_identity(row):
    return (
        normalize_key(clean_optional(row.akun)),
        normalize_key(clean_optional(row.no_kuitansi)),
        normalize_key(clean_optional(row.no_drpp)),
        normalize_key(clean_optional(row.pembebanan)),
    )


def transaction_from_values(values, defaults, user=None, sp2d_raw=None, document_status=STATUS_LENGKAP):
    return TransactionDetail(
        satker_code=defaults["satker_code"],
        sp2d_raw=sp2d_raw,
        akun=clean_optional(values.get("akun"))[:32],
        kategori="",
        bulan_sp2d=parse_month_number(values.get("bulan_sp2d")),
        cara_pembayaran=clean_optional(values.get("cara_pembayaran"))[:100],
        nomor_spm=clean_optional(values.get("nomor_spm") or defaults["nomor_spm"])[:100],
        tanggal_spm=date_value(values.get("tanggal_spm")) or defaults["tanggal_spm"],
        jenis_spm=clean_optional(values.get("jenis_spm") or defaults["jenis_spm"])[:100],
        no_kuitansi=short_document_number(clean_optional(values.get("no_kuitansi")))[:100],
        no_drpp=clean_optional(values.get("no_drpp"))[:100],
        deskripsi=clean_optional(values.get("deskripsi"))[:1000],
        nilai_bruto=parse_user_decimal(values.get("nilai_bruto")),
        nilai_netto=parse_user_decimal(values.get("nilai_netto")),
        pembebanan=clean_optional(values.get("pembebanan"))[:255],
        fp=clean_optional(values.get("fp"))[:100],
        pph21=parse_user_decimal(values.get("pph21")),
        status_detail=(
            TransactionDetail.StatusDetail.LENGKAP
            if document_status in {STATUS_LENGKAP, STATUS_LENGKAP_WARNING, "Lengkap SPM Utama"}
            else TransactionDetail.StatusDetail.PERLU_REVIEW
        ),
        drpp_status=TransactionDetail.DRPPStatus.ADA if clean_optional(values.get("no_drpp")) else TransactionDetail.DRPPStatus.BELUM_ADA,
        created_by=user,
    )


def _apply_matched_nomor_spm_to_parsed(parsed, nomor_spm):
    nomor_spm = normalize_key(nomor_spm)
    if not nomor_spm:
        return
    for drpp in parsed.get("drpps") or []:
        if not drpp:
            continue
        drpp.setdefault("metadata", {})["nomor_spm"] = nomor_spm
        for item in drpp.get("items") or []:
            item["nomor_spm"] = nomor_spm
    if parsed.get("drpp"):
        parsed["drpp"].setdefault("metadata", {})["nomor_spm"] = nomor_spm
    for item in parsed.get("kw_items") or []:
        item["nomor_spm"] = nomor_spm


def build_transaction_rows_from_package(parsed, paket, user=None, sp2d_raw=None, document_status=STATUS_LENGKAP, save=True, skip_existing=True):
    meta = package_metadata(parsed)
    spm_meta = (parsed.get("spm") or {}).get("metadata", {})
    if has_standalone_kw_without_drpp(parsed):
        raise ValueError("KW/Bukti tunggal wajib diunggah bersama DRPP dan tidak boleh membuat D_K.")
    if spm_table_parser_needs_review(parsed):
        raise ValueError("Parser tabel v2 belum valid; fallback legacy tidak dipakai untuk membuat D_K.")

    satker_code = meta["satker_code"] or paket.satker_code
    nomor_spm = meta["nomor_spm"] or paket.nomor_spm
    tanggal_spm = meta.get("tanggal_spm") or paket.tanggal_spm

    # Validasi Tanggal SPM untuk Exact Match
    if not tanggal_spm:
        raise ValueError("Tanggal SPM belum valid, commit dibatalkan.")
    tahun_spm = tanggal_spm.year
    if paket.tahun and paket.tahun != tahun_spm:
        raise ValueError(f"Tahun pada paket ({paket.tahun}) tidak sama dengan tahun Tanggal SPM ({tahun_spm}).")

    defaults = {
        "satker_code": satker_code,
        "nomor_spm": nomor_spm,
        "tanggal_spm": tanggal_spm,
        "jenis_spm": meta["jenis_spm"] or paket.jenis_spm_label or paket.jenis_spm_asli,
    }

    # Pre-fetch existing rows for this exact package
    existing_rows_query = TransactionDetail.objects.filter(
        satker_code=satker_code,
        nomor_spm__iexact=nomor_spm,
        tanggal_spm__year=tahun_spm
    )
    existing_keys = set()
    # Identitas penggabungan baris (tuple)
    if skip_existing:
        for row in existing_rows_query:
            existing_keys.add(transaction_identity(row))

    if parsed.get("preview_rows"):
        rows = []
        for item in parsed.get("preview_rows") or []:
            row = transaction_from_values(item, defaults, user=user, sp2d_raw=sp2d_raw, document_status=document_status)
            key = transaction_identity(row)
            if key in existing_keys:
                continue
            rows.append(row)
            existing_keys.add(key)
        if save and rows:
            return TransactionDetail.objects.bulk_create(rows)
        return rows

    items = parsed.get("kw_items") or []

    if not items and parsed.get("spm"):
        detail_rows = parsed["spm"].get("detail_items") or []
        akun_rows = detail_rows if (is_gup(meta["jenis_spm"]) or is_tup(meta["jenis_spm"])) else parsed["spm"].get("akun_rows", [])
        if not akun_rows:
            # Fallback jika tidak ada rincian akun di SPM
            akun_rows = [{"akun": akun, "jumlah": Decimal("0"), "uraian": spm_meta.get("uraian") or ""} for akun in spm_meta.get("akun_pengeluaran", [])]

        items = []
        for row in akun_rows:
            bruto_val = (
                money_value(row.get("nilai"))
                or money_value(row.get("bruto"))
                or money_value(row.get("jumlah"))
                or money_value(spm_meta.get("jumlah_pengeluaran"))
            )
            netto_val = money_value(row.get("netto")) or money_value(row.get("jumlah")) or bruto_val
            items.append({
                "akun": row.get("akun", ""),
                "bruto": bruto_val,
                "jumlah": netto_val,
                "no_bukti": row.get("no_bukti") or meta.get("nomor_spm", ""),
                "keperluan": row.get("keperluan") or row.get("uraian") or spm_meta.get("uraian") or "",
                "pembebanan": row.get("pembebanan") or (spm_meta.get("pembebanan_list") or [""])[0],
                "fp": row.get("fp") or spm_meta.get("fp") or "",
                "source_row_id": row.get("source_row_id") or row.get("no_bukti") or "",
            })

    if not items:
        items = [{"akun": "", "bruto": money_value(spm_meta.get("jumlah_pengeluaran")), "jumlah": money_value(spm_meta.get("total_pembayaran")), "no_bukti": "", "keperluan": spm_meta.get("uraian") or "Hasil Paket SPM; perlu review rincian.", "pembebanan": ""}]

    # Group items by exact identity
    grouped_items = {}
    for item in items:
        akun = str(item.get("akun", ""))[:32]
        # Jika item berasal dari KW (punya no_bukti), pakai itu. Jika tidak, pakai nomor SPM.
        no_kuitansi = short_document_number(item.get("no_bukti", "") or meta.get("nomor_spm", ""))[:100]
        no_drpp = clean_optional(item.get("no_drpp") or meta.get("nomor_drpp"))[:100]
        pembebanan = str(item.get("pembebanan", ""))

        identity_key = (normalize_key(akun), normalize_key(no_kuitansi), normalize_key(no_drpp), normalize_key(pembebanan))
        row_identity = normalize_key(item.get("source_row_id", ""))
        key = (*identity_key, row_identity)
        if identity_key in existing_keys:
            continue

        bruto_value = item_bruto_value(item)
        netto_value = item_netto_value(item, bruto_value)
        deduction_value = item_deduction_value(item)

        if key not in grouped_items:
            grouped_items[key] = {
                "akun": akun,
                "no_kuitansi": no_kuitansi,
                "no_drpp": no_drpp,
                "pembebanan": pembebanan,
                "bruto": Decimal("0"),
                "netto": Decimal("0"),
                "deduction": Decimal("0"),
                "keperluan": item.get("keperluan", ""),
                "fp": str(item.get("fp", "")),
                "pph21": Decimal("0"),
            }

        grouped_items[key]["bruto"] += bruto_value
        grouped_items[key]["netto"] += netto_value
        grouped_items[key]["deduction"] += deduction_value
        grouped_items[key]["pph21"] += money_value(item.get("pph21"))
        if not grouped_items[key]["keperluan"] and item.get("keperluan"):
            grouped_items[key]["keperluan"] = item.get("keperluan")
        if not grouped_items[key]["fp"] and item.get("fp"):
            grouped_items[key]["fp"] = item.get("fp")

    # Alokasi potongan
    total_potongan = money_value(spm_meta.get("jumlah_potongan"))
    explicit_deductions = sum(
        (item["deduction"] or max(item["bruto"] - item["netto"], Decimal("0")) for item in grouped_items.values()),
        Decimal("0"),
    )

    if len(grouped_items) == 1:
        # Jika hanya 1 baris, Netto = Netto Header
        for key in grouped_items:
            header_netto = money_value(spm_meta.get("total_pembayaran"))
            if header_netto > 0:
                grouped_items[key]["netto"] = header_netto
    elif len(grouped_items) > 1 and total_potongan > 0 and explicit_deductions <= 0:
        paket.alokasi_potongan_ambigu = True  # dibaca view sebagai warning rekonsiliasi, bukan exception

    pph21_value = money_value(spm_meta.get("jumlah_potongan")) if "411121" in (spm_meta.get("akun_potongan") or []) else Decimal("0")

    rows = []
    for key, g_item in grouped_items.items():
        identity_key = key[:4]
        if identity_key in existing_keys:
            continue

        rows.append(
            TransactionDetail(
                satker_code=satker_code,
                sp2d_raw=sp2d_raw,
                akun=g_item["akun"],
                kategori="",
                bulan_sp2d=getattr(meta.get("tanggal_sp2d"), "month", None) or paket.bulan,
                cara_pembayaran="UP/TUP" if (is_gup(meta["jenis_spm"]) or is_tup(meta["jenis_spm"])) else (meta.get("cara_pembayaran") or meta["jenis_spm"]),
                nomor_spm=nomor_spm,
                tanggal_spm=tanggal_spm,
                jenis_spm=meta["jenis_spm"] or paket.jenis_spm_label or paket.jenis_spm_asli,
                no_kuitansi=g_item["no_kuitansi"],
                no_drpp=g_item["no_drpp"],
                deskripsi=g_item["keperluan"][:1000],
                nilai_bruto=g_item["bruto"],
                nilai_netto=g_item["netto"],
                pembebanan=g_item["pembebanan"],
                fp=g_item["fp"],
                pph21=g_item["pph21"] or pph21_value,
                status_detail=(
                    TransactionDetail.StatusDetail.LENGKAP
                    if document_status in {STATUS_LENGKAP, STATUS_LENGKAP_WARNING, "Lengkap SPM Utama"}
                    else TransactionDetail.StatusDetail.PERLU_REVIEW
                ),
                drpp_status=TransactionDetail.DRPPStatus.ADA if meta.get("nomor_drpp") else TransactionDetail.DRPPStatus.BELUM_ADA,
                created_by=user,
            )
        )
        existing_keys.add(identity_key)

    if save and rows:
        return TransactionDetail.objects.bulk_create(rows)
    return rows


def copy_existing_links(source_transaction, target_transaction, user=None):
    for link in DocumentDriveLink.objects.filter(transaction_detail=source_transaction):
        if DocumentDriveLink.objects.filter(
            transaction_detail=target_transaction,
            jenis_dokumen=link.jenis_dokumen,
            nama_file=link.nama_file,
            google_drive_url=link.google_drive_url,
        ).exists():
            continue
        DocumentDriveLink.objects.create(
            transaction_detail=target_transaction,
            satker_code=target_transaction.satker_code,
            nomor_spm=target_transaction.nomor_spm,
            no_kuitansi=target_transaction.no_kuitansi,
            no_drpp=target_transaction.no_drpp,
            jenis_dokumen=link.jenis_dokumen,
            nama_file=link.nama_file,
            google_drive_url=link.google_drive_url,
            status=link.status,
            catatan=(link.catatan + f"; linked_from_document_id={link.id}")[:2000],
            created_by=user,
        )


def update_transaction_from_candidate(target, candidate):
    fields = [
        "akun", "bulan_sp2d", "cara_pembayaran", "nomor_spm", "tanggal_spm", "jenis_spm",
        "no_kuitansi", "no_drpp", "deskripsi", "nilai_bruto", "nilai_netto", "pembebanan",
        "fp", "pph21", "status_detail", "drpp_status",
    ]
    changed = []
    for field in fields:
        value = getattr(candidate, field)
        if field in {"cara_pembayaran", "nomor_spm", "jenis_spm", "no_kuitansi", "no_drpp", "deskripsi", "pembebanan", "fp", "akun"} and clean_optional(value) == "":
            continue
        if field in {"tanggal_spm", "bulan_sp2d"} and value is None:
            continue
        if field in {"nilai_bruto", "nilai_netto"} and value == Decimal("0") and getattr(target, field):
            continue
        if getattr(target, field) != value:
            setattr(target, field, value)
            changed.append(field)
    if changed:
        target.save(update_fields=changed + ["updated_at"])
    return target


def link_followup_document(paket, transactions, user=None, parsed=None, document_status=""):
    transaction_list = [item for item in transactions if item]
    if not transaction_list:
        return {"status": "skipped", "links": [], "archive_status": ""}
    package_marker = f"paket_spm_id={paket.id}"
    if DocumentDriveLink.objects.filter(catatan__icontains=package_marker, jenis_dokumen="DRPP/KW").exists():
        return {"status": "exists", "links": [], "archive_status": ""}
    source_path = _package_source_path(paket)
    if not source_path:
        return {"status": "missing_source", "links": [], "archive_status": ""}
    meta = package_metadata(parsed or paket.parsed_data or {})
    first = transaction_list[0]
    drive_result, first_link = archive_file_link(
        source_path,
        user=user,
        jenis_dokumen="DRPP/KW",
        nama_file=paket.original_filename,
        satker_code=first.satker_code,
        nomor_spm=first.nomor_spm,
        no_drpp=str(meta.get("nomor_drpp") or first.no_drpp or "")[:100],
        no_kuitansi=first.no_kuitansi,
        catatan_extra=(
            "source=Paket SPM followup; "
            f"paket_spm_id={paket.id}; "
            f"document_status={document_status or '-'}"
        ),
        transaction_detail=first,
    )
    if drive_result["status"] not in {"uploaded", "local_archived"}:
        raise ValueError(drive_result["error_message"] or "File DRPP/KW gagal disimpan ke arsip permanen.")
    links = [first_link]
    for tx in transaction_list[1:]:
        links.append(DocumentDriveLink.objects.create(
            transaction_detail=tx,
            satker_code=tx.satker_code,
            nomor_spm=tx.nomor_spm,
            no_kuitansi=tx.no_kuitansi,
            no_drpp=tx.no_drpp,
            jenis_dokumen="DRPP/KW",
            nama_file=paket.original_filename,
            google_drive_url=first_link.google_drive_url,
            status=first_link.status,
            catatan=(first_link.catatan + f"; linked_from_document_id={first_link.id}")[:2000],
            created_by=user,
        ))
    return {"status": "created", "links": links, "archive_status": drive_result["status"]}


def merge_followup_into_existing_dk(parsed, paket, user=None, document_status=STATUS_LENGKAP):
    existing = exact_transactions_for_package(parsed, paket)
    if not existing:
        raise ValueError("SPM utama belum ada di D_K.")
    candidates = build_transaction_rows_from_package(
        parsed,
        paket,
        user=user,
        document_status=document_status,
        save=False,
        skip_existing=False,
    )
    if not candidates:
        raise ValueError("Tidak ada rincian DRPP/KW yang bisa memperbarui D_K.")

    candidate_total = sum((row.nilai_bruto for row in candidates), Decimal("0"))
    existing_total = sum((row.nilai_bruto for row in existing), Decimal("0"))
    if existing_total > 0 and abs(candidate_total - existing_total) > Decimal("1"):
        raise ValueError(f"Total rincian Rp{candidate_total:,.0f} tidak sama dengan D_K existing Rp{existing_total:,.0f}.")

    existing_by_key = {transaction_identity(row): row for row in existing}
    placeholder = existing[0] if len(existing) == 1 else None
    placeholder_used = False
    updated = []
    for candidate in candidates:
        key = transaction_identity(candidate)
        target = existing_by_key.get(key)
        if not target and placeholder and not placeholder_used:
            target = placeholder
            placeholder_used = True
        if target:
            update_transaction_from_candidate(target, candidate)
        else:
            candidate.pk = None
            candidate.id = None
            candidate.save()
            target = candidate
            copy_existing_links(existing[0], target, user=user)
        updated.append(target)
        existing_by_key[transaction_identity(target)] = target
        mark_checklist_present(target, "DRPP", user)
        mark_checklist_present(target, "KW", user)
        refresh_transaction_document_status(target, verified_document_type="DRPP")

    link_followup_document(paket, updated, user=user, parsed=parsed, document_status=document_status)
    return updated


def _package_source_path(paket):
    try:
        path = paket.zip_file.path if paket.zip_file else ""
    except (NotImplementedError, ValueError):
        return ""
    return path if path and os.path.exists(path) else ""


def _existing_spm_link(transaction, paket):
    package_marker = f"paket_spm_id={paket.id}"
    query = DocumentDriveLink.objects.filter(
        transaction_detail=transaction,
        jenis_dokumen__iexact="SPM",
    )
    return query.filter(
        Q(catatan__icontains=package_marker)
        | Q(
            nama_file=paket.original_filename,
            satker_code__iexact=paket.satker_code,
            nomor_spm__iexact=paket.nomor_spm,
        )
    ).first()


def link_paket_spm_source_document(paket, transactions, user=None, parsed=None, document_status=""):
    transaction_list = [item for item in transactions if item]
    if not transaction_list:
        return {"status": "skipped", "links": [], "archive_status": ""}

    missing_transactions = [tx for tx in transaction_list if not _existing_spm_link(tx, paket)]
    if not missing_transactions:
        for tx in transaction_list:
            mark_checklist_present(tx, "SPM", user)
            refresh_transaction_document_status(tx, verified_document_type="SPM")
        return {"status": "exists", "links": [], "archive_status": ""}

    source_path = _package_source_path(paket)
    if not source_path:
        raise ValueError("File sumber Paket SPM tidak tersedia di storage. Upload ulang dokumen untuk mengaitkan PDF ke D_K.")

    first_transaction = missing_transactions[0]
    meta = package_metadata(parsed or paket.parsed_data or {})
    drive_result, first_link = archive_file_link(
        source_path,
        user=user,
        jenis_dokumen="SPM",
        nama_file=paket.original_filename,
        satker_code=paket.satker_code,
        nomor_spm=paket.nomor_spm,
        no_drpp=str(meta.get("nomor_drpp") or "")[:100],
        no_kuitansi=first_transaction.no_kuitansi,
        catatan_extra=(
            "source=Paket SPM; "
            f"paket_spm_id={paket.id}; "
            f"document_status={document_status or '-'}"
        ),
        transaction_detail=first_transaction,
    )
    if drive_result["status"] not in {"uploaded", "local_archived"}:
        raise ValueError(drive_result["error_message"] or "File sumber Paket SPM gagal disimpan ke arsip permanen.")

    links = [first_link]
    mark_checklist_present(first_transaction, "SPM", user)
    refresh_transaction_document_status(first_transaction, verified_document_type="SPM")
    for transaction in missing_transactions[1:]:
        link = DocumentDriveLink.objects.create(
            transaction_detail=transaction,
            satker_code=paket.satker_code or transaction.satker_code,
            nomor_spm=paket.nomor_spm or transaction.nomor_spm,
            no_kuitansi=transaction.no_kuitansi,
            no_drpp=first_link.no_drpp,
            jenis_dokumen="SPM",
            nama_file=paket.original_filename,
            google_drive_url=first_link.google_drive_url,
            status=first_link.status,
            catatan=(first_link.catatan + f"; linked_from_document_id={first_link.id}")[:2000],
            created_by=user,
        )
        links.append(link)
        mark_checklist_present(transaction, "SPM", user)
        refresh_transaction_document_status(transaction, verified_document_type="SPM")

    return {"status": "created", "links": links, "archive_status": drive_result["status"]}


def link_existing_package_documents(paket, transactions, user=None, parsed=None, document_status=""):
    transaction_list = [item for item in transactions if item]
    if not transaction_list:
        return {"status": "skipped", "links": [], "archive_status": ""}

    doc_types = {
        (item.get("type") or "").upper()
        for item in (parsed or {}).get("files", [])
        if item.get("type")
    } or {"SPM"}
    doc_types.discard("UNKNOWN")
    links = []
    archive_status = ""
    source_path = _package_source_path(paket)
    if not source_path:
        raise ValueError("File sumber Paket SPM tidak tersedia di storage. Upload ulang dokumen untuk mengaitkan PDF ke D_K.")

    for doc_type in sorted(doc_types):
        checklist_type = "Kuitansi/Bukti Pembayaran" if doc_type == "KW" else doc_type
        if doc_type == "SPM":
            result = link_paket_spm_source_document(
                paket,
                transaction_list,
                user=user,
                parsed=parsed,
                document_status=document_status,
            )
            links.extend(result.get("links") or [])
            archive_status = result.get("archive_status") or archive_status
            continue

        missing = [
            tx for tx in transaction_list
            if not DocumentDriveLink.objects.filter(
                transaction_detail=tx,
                jenis_dokumen__iexact=checklist_type,
                nama_file=paket.original_filename,
            ).exists()
        ]
        if not missing:
            for tx in transaction_list:
                mark_checklist_present(tx, checklist_type, user)
                refresh_transaction_document_status(tx, verified_document_type=checklist_type)
            continue

        first = missing[0]
        meta = package_metadata(parsed or paket.parsed_data or {})
        drive_result, first_link = archive_file_link(
            source_path,
            user=user,
            jenis_dokumen=checklist_type,
            nama_file=paket.original_filename,
            satker_code=first.satker_code,
            nomor_spm=first.nomor_spm,
            no_drpp=str(meta.get("nomor_drpp") or first.no_drpp or "")[:100],
            no_kuitansi=first.no_kuitansi,
            catatan_extra=(
                f"source=Paket SPM existing D_K; paket_spm_id={paket.id}; "
                f"document_status={document_status or '-'}"
            ),
            transaction_detail=first,
        )
        if drive_result["status"] not in {"uploaded", "local_archived"}:
            raise ValueError(drive_result["error_message"] or "File sumber Paket SPM gagal disimpan ke arsip permanen.")
        links.append(first_link)
        archive_status = drive_result["status"]
        mark_checklist_present(first, checklist_type, user)
        refresh_transaction_document_status(first, verified_document_type=checklist_type)
        for tx in missing[1:]:
            link = DocumentDriveLink.objects.create(
                transaction_detail=tx,
                satker_code=tx.satker_code,
                nomor_spm=tx.nomor_spm,
                no_kuitansi=tx.no_kuitansi,
                no_drpp=tx.no_drpp,
                jenis_dokumen=checklist_type,
                nama_file=paket.original_filename,
                google_drive_url=first_link.google_drive_url,
                status=first_link.status,
                catatan=(first_link.catatan + f"; linked_from_document_id={first_link.id}")[:2000],
                created_by=user,
            )
            links.append(link)
            mark_checklist_present(tx, checklist_type, user)
            refresh_transaction_document_status(tx, verified_document_type=checklist_type)

    return {"status": "created" if links else "exists", "links": links, "archive_status": archive_status}

