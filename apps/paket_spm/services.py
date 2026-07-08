from decimal import Decimal

from django.db.models import Q

from apps.dk.models import TransactionDetail
from apps.documents.models import DocumentDriveLink
from apps.paket_spm.models import PaketSPMUpload
from apps.sp2d.models import SP2DRaw


STATUS_LENGKAP = "Lengkap"
STATUS_BELUM_LENGKAP = "Belum Lengkap"
STATUS_REVIEW_OCR = "Perlu Review OCR"
STATUS_REVIEW_NOMOR = "Perlu Review Nomor"
STATUS_DUPLIKAT = "Duplikat"
STATUS_GAGAL = "Gagal Diproses"

REKON_DATA_AWAL_SP2D = "Data awal dari SP2D"
REKON_COCOK_SP2D = "Cocok dengan SP2D"
REKON_COCOK_DK = "Cocok dengan D_K"
REKON_BELUM_SP2D = "Belum ada SP2D pembanding"
REKON_BELUM_DK = "Belum ada D_K pembanding"
REKON_REVIEW = "Perlu Review Matching"
REKON_BELUM_DIPASTIKAN = "Belum dipastikan"


def normalize_key(value):
    return str(value or "").strip().upper()


def money_value(value):
    if value in (None, ""):
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(value))


def is_gup(jenis_spm):
    text = normalize_key(jenis_spm)
    return "GUP" in text or "GU" in text


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
    drpp_metas = [(item or {}).get("metadata", {}) for item in drpps]
    drpp_meta = drpp_metas[0] if drpp_metas else {}
    drpp_numbers = [normalize_key(meta.get("nomor_drpp")) for meta in drpp_metas if normalize_key(meta.get("nomor_drpp"))]
    kw_items = parsed.get("kw_items") or []
    kw_numbers = [normalize_key(item.get("no_bukti")) for item in kw_items if normalize_key(item.get("no_bukti"))]
    drpp_total = sum((money_value(meta.get("total")) for meta in drpp_metas), Decimal("0"))
    total = money_value(spm_meta.get("total_pembayaran") or drpp_total or sum((money_value(item.get("jumlah")) for item in kw_items), Decimal("0")))
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
        "satker_code": str(spm_meta.get("satker_code") or drpp_meta.get("satker_code") or "").strip(),
        "jenis_spm": str(spm_meta.get("jenis_spm") or drpp_meta.get("jenis_spm") or "").strip(),
        "tanggal_spm": spm_meta.get("tanggal_spm"),
        "total": total,
        "kw_count": len(kw_items),
        "kw_numbers": kw_numbers,
        "akun_count": len([row for row in kw_items if row.get("akun")]) or len(spm.get("akun_rows") or []),
        "has_spm": bool(spm and spm.get("status") in {"parsed_text", "parsed_ocr", "needs_manual_review"}),
        "has_drpp": any(bool(item and item.get("status") in {"parsed_text", "parsed_ocr", "needs_manual_review"}) for item in drpps),
        "drpp_count": len(drpp_numbers) or len(drpps),
        "spm_status": spm.get("status", ""),
        "drpp_status": drpp.get("status", ""),
        "best_engine": spm.get("best_engine") or drpp.get("best_engine") or "-",
        "ocr_status": spm.get("status") or drpp.get("status") or "needs_manual_review",
    }


def find_matching_transaction(meta):
    query = TransactionDetail.objects.all()
    conditions = Q()
    if meta["nomor_spm"]:
        conditions |= Q(nomor_spm__iexact=meta["nomor_spm"])
    if meta["nomor_drpp"]:
        for no_drpp in meta.get("nomor_drpp_list") or [meta["nomor_drpp"]]:
            conditions |= Q(no_drpp__iexact=no_drpp)
    if not conditions:
        return None
    query = query.filter(conditions)
    if meta["satker_code"]:
        satker_match = query.filter(satker_code=meta["satker_code"]).first()
        if satker_match:
            return satker_match
    if meta["total"]:
        total_match = query.filter(Q(nilai_bruto=meta["total"]) | Q(nilai_netto=meta["total"])).first()
        if total_match:
            return total_match
    return query.first()


def find_matching_sp2d(meta):
    if not meta["nomor_spm"] and not meta["total"]:
        return None
    query = SP2DRaw.objects.all()
    conditions = Q()
    if meta["nomor_spm"]:
        conditions |= Q(nomor_spm_extracted__iexact=meta["nomor_spm"]) | Q(nomor_invoice__icontains=meta["nomor_spm"]) | Q(deskripsi__icontains=meta["nomor_spm"])
    if meta["total"]:
        conditions |= Q(nilai_spm=meta["total"]) | Q(nilai_sp2d=meta["total"])
    query = query.filter(conditions)
    if meta["satker_code"]:
        satker_match = query.filter(satker_code=meta["satker_code"]).first()
        if satker_match:
            return satker_match
    return query.first()


def find_duplicate_package(meta, original_filename=""):
    query = PaketSPMUpload.objects.all()
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
    notes = []
    if not parsed.get("files") and not parsed.get("spm") and not parsed.get("drpp"):
        return STATUS_GAGAL, ["Dokumen tidak bisa diklasifikasi atau diproses."]
    if meta.get("nomor_spm_conflict"):
        return STATUS_REVIEW_NOMOR, ["Nomor SPM OCR dan filename berbeda. Pilih nomor yang benar sebelum commit."]
    if parsed_is_filename_only(parsed):
        return STATUS_REVIEW_OCR, ["Hanya metadata filename yang terbaca; perlu review OCR/manual."]
    if not meta["has_spm"] and not meta["has_drpp"]:
        return STATUS_REVIEW_OCR, ["OCR belum membaca SPM/DRPP secara memadai."]
    if is_gup(meta["jenis_spm"]) and (not meta["has_drpp"] or not meta["kw_count"]):
        return STATUS_BELUM_LENGKAP, ["SPM GUP membutuhkan DRPP dan KW/Bukti pengeluaran."]
    if meta["total"] <= 0:
        return STATUS_REVIEW_OCR, ["Nilai total belum terbaca."]
    if not meta["akun_count"]:
        return STATUS_REVIEW_OCR, ["Akun/COA atau item pengeluaran belum terbaca."]
    return STATUS_LENGKAP, notes


def build_package_decision(parsed, original_filename="", forced_sp2d=None):
    meta = package_metadata(parsed)
    document_status, notes = evaluate_document_status(parsed)
    duplicate = find_duplicate_package(meta, original_filename)
    matched_transaction = find_matching_transaction(meta)
    matched_sp2d = forced_sp2d or find_matching_sp2d(meta)
    if matched_transaction and matched_transaction.nomor_spm:
        meta["nomor_spm_matching"] = normalize_key(matched_transaction.nomor_spm)
    elif matched_sp2d and matched_sp2d.nomor_spm_extracted:
        meta["nomor_spm_matching"] = normalize_key(matched_sp2d.nomor_spm_extracted)

    # Resolusi konflik SPM otomatis jika cocok dengan D_K/SP2D
    if meta.get("nomor_spm_conflict") and meta.get("nomor_spm_ocr") and meta.get("nomor_spm_matching"):
        if meta["nomor_spm_ocr"] == meta["nomor_spm_matching"]:
            meta["nomor_spm_conflict"] = False
            meta["nomor_spm_review_status"] = "OK"
            meta["nomor_spm_final"] = meta["nomor_spm_ocr"]
            meta["nomor_spm_reason"] = "Konflik OCR vs Filename otomatis diselesaikan karena cocok dengan D_K/SP2D."
            if "spm" in parsed and "metadata" in parsed["spm"]:
                parsed["spm"]["metadata"]["nomor_spm_conflict"] = False
                parsed["spm"]["metadata"]["nomor_spm_review_status"] = "OK"
                parsed["spm"]["metadata"]["nomor_spm_reason"] = meta["nomor_spm_reason"]
                if "warnings" in parsed["spm"]:
                    parsed["spm"]["warnings"] = [w for w in parsed["spm"]["warnings"] if "berbeda" not in w.lower()]
            # Update ulang document status
            document_status, notes = evaluate_document_status(parsed)

    if duplicate:
        return {
            "document_status": STATUS_DUPLIKAT,
            "reconciliation_status": REKON_REVIEW,
            "commit_action": "duplicate",
            "commit_label": "Dokumen Sudah Ada",
            "can_commit": False,
            "decision_text": "Dokumen dengan nomor ini sudah pernah diupload.",
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

    if document_status == STATUS_REVIEW_NOMOR:
        return {
            "document_status": document_status,
            "reconciliation_status": REKON_REVIEW,
            "commit_action": "review_only",
            "commit_label": "Simpan Draft Review Manual",
            "can_commit": True,
            "decision_text": "Nomor SPM OCR dan filename berbeda. Simpan sebagai draft review manual sebelum menentukan nomor final.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": matched_transaction,
            "matched_sp2d": matched_sp2d,
            "duplicate": None,
        }

    if document_status == STATUS_REVIEW_OCR and matched_transaction:
        return {
            "document_status": document_status,
            "reconciliation_status": REKON_COCOK_DK,
            "commit_action": "link_existing",
            "commit_label": "Simpan Dokumen Review OCR",
            "can_commit": True,
            "decision_text": "File akan disimpan dan dikaitkan ke D_K existing dengan status Perlu Review OCR.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": matched_transaction,
            "matched_sp2d": matched_sp2d,
            "duplicate": None,
        }

    if document_status == STATUS_REVIEW_OCR and matched_sp2d:
        return {
            "document_status": document_status,
            "reconciliation_status": REKON_COCOK_SP2D,
            "commit_action": "link_sp2d",
            "commit_label": "Simpan Dokumen Review OCR",
            "can_commit": True,
            "decision_text": "File akan disimpan dan dikaitkan ke SP2D existing dengan status Perlu Review OCR.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": None,
            "matched_sp2d": matched_sp2d,
            "duplicate": None,
        }

    if document_status == STATUS_REVIEW_OCR:
        return {
            "document_status": document_status,
            "reconciliation_status": REKON_REVIEW,
            "commit_action": "create_from_package",
            "commit_label": "Simpan Draft Review OCR",
            "can_commit": True,
            "decision_text": "File akan disimpan sebagai data Paket SPM mandiri dan perlu review OCR.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": None,
            "matched_sp2d": None,
            "duplicate": None,
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
        }

    if matched_sp2d:
        return {
            "document_status": document_status,
            "reconciliation_status": REKON_COCOK_SP2D,
            "commit_action": "link_sp2d",
            "commit_label": "Kaitkan Dokumen ke Data Existing",
            "can_commit": True,
            "decision_text": "Akan dikaitkan ke data SP2D existing.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": None,
            "matched_sp2d": matched_sp2d,
            "duplicate": None,
        }

    if document_status == STATUS_BELUM_LENGKAP:
        return {
            "document_status": document_status,
            "reconciliation_status": REKON_BELUM_SP2D,
            "commit_action": "create_from_package",
            "commit_label": "Simpan Dokumen Belum Lengkap",
            "can_commit": True,
            "decision_text": "Dokumen belum lengkap, tetapi file dan metadata akan tetap disimpan untuk dilengkapi.",
            "notes": notes,
            "meta": meta,
            "matched_transaction": None,
            "matched_sp2d": None,
            "duplicate": None,
        }

    return {
        "document_status": document_status,
        "reconciliation_status": f"{REKON_BELUM_SP2D} / {REKON_BELUM_DK}",
        "commit_action": "create_from_package",
        "commit_label": "Simpan Paket SPM",
        "can_commit": True,
        "decision_text": "Akan dibuat sebagai data baru di INTERMILAN dari sumber Paket SPM.",
        "notes": notes,
        "meta": meta,
        "matched_transaction": None,
        "matched_sp2d": None,
        "duplicate": None,
    }


def build_transaction_rows_from_package(parsed, paket, user=None, sp2d_raw=None, document_status=STATUS_LENGKAP):
    meta = package_metadata(parsed)
    items = parsed.get("kw_items") or []
    if not items and parsed.get("spm"):
        items = [
            {"akun": row.get("akun", ""), "jumlah": Decimal("0"), "no_bukti": "", "keperluan": row.get("uraian", "")}
            for row in parsed["spm"].get("akun_rows", [])
        ]
    if not items:
        items = [{"akun": "", "jumlah": meta["total"], "no_bukti": "", "keperluan": "Hasil Paket SPM; perlu review rincian."}]
    rows = []
    for item in items:
        amount = money_value(item.get("jumlah"))
        rows.append(
            TransactionDetail(
                satker_code=paket.satker_code or meta["satker_code"],
                sp2d_raw=sp2d_raw,
                akun=str(item.get("akun", ""))[:32],
                kategori="",
                bulan_sp2d=paket.bulan,
                cara_pembayaran=meta["jenis_spm"],
                nomor_spm=paket.nomor_spm,
                tanggal_spm=paket.tanggal_spm,
                jenis_spm=paket.jenis_spm_label or paket.jenis_spm_asli,
                no_kuitansi=str(item.get("no_bukti", ""))[:100],
                no_drpp=str(item.get("no_drpp") or meta["nomor_drpp"])[:100],
                deskripsi=str(item.get("keperluan", "") or "Data dibuat dari Paket SPM OCR.")[:1000],
                nilai_bruto=amount,
                nilai_netto=amount,
                pembebanan="Paket SPM OCR",
                status_detail=(
                    TransactionDetail.StatusDetail.LENGKAP
                    if document_status == STATUS_LENGKAP
                    else TransactionDetail.StatusDetail.PERLU_REVIEW
                ),
                drpp_status=TransactionDetail.DRPPStatus.ADA if meta["nomor_drpp"] else TransactionDetail.DRPPStatus.BELUM_ADA,
                created_by=user,
            )
        )
    return TransactionDetail.objects.bulk_create(rows)
