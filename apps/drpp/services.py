import hashlib
from datetime import datetime
from decimal import Decimal

from django.db import transaction

from apps.core.parsers import parse_paket_spm_zip, normalized_bukti_key
from apps.dk.models import MasterAkun, TransactionDetail, TransactionChangeLog
from .models import DRPPImportBatch, DRPPUpload, DRPPItem, DRPPMatch


def get_drpp_hard_identity(satker_code, tahun, nomor_drpp):
    satker_code = (satker_code or "").strip()
    tahun = str(tahun or "").strip()
    nomor_drpp = (nomor_drpp or "").strip()
    raw = f"{satker_code}|{tahun}|{nomor_drpp}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_drpp_item_hard_identity(satker_code, tahun, nomor_drpp, no_kuitansi):
    satker_code = (satker_code or "").strip()
    tahun = str(tahun or "").strip()
    nomor_drpp = (nomor_drpp or "").strip()
    no_kuitansi = (no_kuitansi or "").strip()
    raw = f"{satker_code}|{tahun}|{nomor_drpp}|{no_kuitansi}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_kw_mandiri_hard_identity(satker_code, tahun, no_kuitansi):
    satker_code = (satker_code or "").strip()
    tahun = str(tahun or "").strip()
    no_kuitansi = (no_kuitansi or "").strip()
    raw = f"{satker_code}|{tahun}||{no_kuitansi}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def prepare_drpp_rows(zip_path, ocr=False, satker_code="", tahun=None):
    parsed = parse_paket_spm_zip(zip_path, ocr=ocr, drpp_kuitansi_mode=True)
    if not parsed["ok"]:
        return {"ok": False, "warnings": parsed["warnings"], "rows": []}

    rows = []
    
    # Process DRPPs and their items
    for drpp in parsed["drpps"]:
        nomor_drpp = drpp.get("metadata", {}).get("nomor_drpp", "")
        # The kw_items related to this drpp
        items = parsed["kw_by_drpp"].get(nomor_drpp, [])
        for item in items:
            no_kuitansi = item.get("no_bukti", "")
            if not no_kuitansi:
                continue
            
            akun = item.get("akun", "")
            bruto = item.get("bruto") or item.get("jumlah") or Decimal("0")
            netto = item.get("netto") or item.get("jumlah") or Decimal("0")
            
            row = {
                "source_type": DRPPItem.SourceType.DRPP_ITEM,
                "satker_code": satker_code,
                "tahun": tahun,
                "nomor_drpp": nomor_drpp,
                "no_kuitansi": no_kuitansi,
                "akun": akun,
                "bruto": bruto,
                "netto": netto,
                "tanggal_bukti": item.get("tanggal_bukti"),
                "penerima": item.get("penerima", ""),
                "keperluan": item.get("keperluan", ""),
                "source_file": item.get("source_file", ""),
            }
            
            if satker_code and tahun and nomor_drpp and no_kuitansi:
                row["identity_key"] = get_drpp_item_hard_identity(satker_code, tahun, nomor_drpp, no_kuitansi)
            else:
                row["identity_key"] = None
                
            raw_source = f"{item.get('source_file_detail') or item.get('source_file')}|{no_kuitansi}"
            row["source_row_key"] = hashlib.sha256(raw_source.encode("utf-8")).hexdigest()
            rows.append(row)
            
    # Process KW mandiri (those in TANPA_DRPP)
    for item in parsed["kw_by_drpp"].get("TANPA_DRPP", []):
        no_kuitansi = item.get("no_bukti", "")
        if not no_kuitansi:
            continue
            
        akun = item.get("akun", "")
        bruto = item.get("bruto") or item.get("jumlah") or Decimal("0")
        netto = item.get("netto") or item.get("jumlah") or Decimal("0")
        
        row = {
            "source_type": DRPPItem.SourceType.KUITANSI_MANDIRI,
            "satker_code": satker_code,
            "tahun": tahun,
            "nomor_drpp": "",
            "no_kuitansi": no_kuitansi,
            "akun": akun,
            "bruto": bruto,
            "netto": netto,
            "tanggal_bukti": item.get("tanggal_bukti"),
            "penerima": item.get("penerima", ""),
            "keperluan": item.get("keperluan", ""),
            "source_file": item.get("source_file", ""),
        }
        
        if satker_code and tahun and no_kuitansi:
            row["identity_key"] = get_kw_mandiri_hard_identity(satker_code, tahun, no_kuitansi)
        else:
            row["identity_key"] = None
            
        raw_source = f"{item.get('source_file_detail') or item.get('source_file')}|{no_kuitansi}"
        row["source_row_key"] = hashlib.sha256(raw_source.encode("utf-8")).hexdigest()
        rows.append(row)
        
    return {"ok": True, "rows": rows, "warnings": parsed["warnings"]}


def classify_drpp_rows(rows, user_corrections=None):
    user_corrections = user_corrections or {}
    
    # Pre-fetch existing TransactionDetail for exact matches
    # Keys for matching: satker_code + tahun + no_kuitansi
    satker_tahun_kws = {(r["satker_code"], str(r["tahun"]), normalized_bukti_key(r["no_kuitansi"])) for r in rows if r["satker_code"] and r["tahun"] and r["no_kuitansi"]}
    
    existing_dks = TransactionDetail.objects.all()
    dk_lookup = {}  # key: (satker_code, norm_bukti)
    
    # Pre-fetch active akun codes from MasterAkun for validation
    # Only validate if MasterAkun table actually has records (allows empty-table test setup)
    master_akun_exists = MasterAkun.objects.exists()
    active_akun_set = set(MasterAkun.objects.filter(is_active=True).values_list("kode", flat=True))
    
    for dk in existing_dks:
        if not dk.satker_code or not dk.no_kuitansi:
            continue
        # Match by satker + normalized kuitansi number (TransactionDetail has no explicit tahun field)
        key = (dk.satker_code, normalized_bukti_key(dk.no_kuitansi))
        dk_lookup[key] = dk

    classified_rows = []
    
    for row in rows:
        # Apply corrections
        corr = user_corrections.get(row["source_row_key"], {})
        if "akun" in corr:
            row["akun"] = corr["akun"]
            
        if not row["akun"]:
            row["status"] = "REVIEW"
            row["message"] = "Akun kosong"
            classified_rows.append(row)
            continue
        
        # Validate akun must exist and be active in MasterAkun
        if master_akun_exists and row["akun"] not in active_akun_set:
            row["status"] = "REVIEW"
            row["message"] = f"Akun {row['akun']} tidak aktif di MasterAkun"
            classified_rows.append(row)
            continue
            
        key = (row["satker_code"], normalized_bukti_key(row["no_kuitansi"]))
        dk = dk_lookup.get(key)
        
        if not dk:
            row["status"] = "BARU"
            row["message"] = "Data baru"
        else:
            if dk.status_detail in [TransactionDetail.StatusDetail.FINAL, TransactionDetail.StatusDetail.DIARSIPKAN]:
                row["status"] = "KONFLIK_TERKUNCI"
                row["message"] = "Data sudah final/diarsipkan"
            else:
                # Compare fields for update vs skip
                diffs = []
                if row["akun"] and row["akun"] != dk.akun:
                    diffs.append("akun")
                if row["bruto"] and row["bruto"] != dk.nilai_bruto:
                    diffs.append("nilai_bruto")
                if row["netto"] and row["netto"] != dk.nilai_netto:
                    diffs.append("nilai_netto")
                
                if not diffs:
                    row["status"] = "SKIP"
                    row["message"] = "Data sama persis"
                else:
                    row["status"] = "UPDATE"
                    row["message"] = f"Update: {', '.join(diffs)}"
        
        classified_rows.append(row)
        
    return classified_rows


@transaction.atomic
def commit_drpp_rows(zip_path, ocr, satker_code, tahun, user, filename, original_filename, user_corrections=None):
    prep = prepare_drpp_rows(zip_path, ocr=ocr, satker_code=satker_code, tahun=tahun)
    if not prep["ok"]:
        return {"ok": False, "error": prep["warnings"]}
    
    # Cross-satker guard: operator can only upload for their own satker
    from apps.accounts.access import get_user_satker_code, is_admin
    user_satker = get_user_satker_code(user)
    if user_satker and not is_admin(user) and user_satker != satker_code:
        return {"ok": False, "error": [f"Akses ditolak: Anda tidak bisa mengupload untuk satker {satker_code}"]}
        
    rows = classify_drpp_rows(prep["rows"], user_corrections)
    
    batch = DRPPImportBatch.objects.create(
        uploaded_by=user,
        filename=filename,
        original_filename=original_filename,
    )
    
    drpp_uploads_cache = {}
    
    for row in rows:
        # Dedupe review
        if row["status"] == "REVIEW":
            batch.review_rows += 1
            continue
            
        if row["status"] in ["KONFLIK_TERKUNCI", "KONFLIK_DIARSIPKAN", "KONFLIK"]:
            batch.conflict_rows += 1
            continue
            
        if row["status"] == "GAGAL":
            batch.failed_rows += 1
            continue

        # Handle DRPPUpload
        drpp_upload = None
        if row["source_type"] == DRPPItem.SourceType.DRPP_ITEM:
            drpp_key = get_drpp_hard_identity(satker_code, tahun, row["nomor_drpp"])
            if drpp_key not in drpp_uploads_cache:
                drpp_upload, created = DRPPUpload.objects.get_or_create(
                    identity_key=drpp_key,
                    defaults={
                        "import_batch": batch,
                        "nomor_drpp": row["nomor_drpp"],
                        "satker_code": satker_code,
                        "tahun": tahun,
                        "uploaded_by": user,
                    }
                )
                drpp_uploads_cache[drpp_key] = drpp_upload
            drpp_upload = drpp_uploads_cache[drpp_key]

        # Handle DRPPItem – only upsert if identity_key is known
        if not row.get("identity_key"):
            # No identity = cannot upsert idempotently; skip silently
            batch.failed_rows += 1
            continue
        
        drpp_item, created_item = DRPPItem.objects.update_or_create(
            identity_key=row["identity_key"],
            defaults={
                "drpp_upload": drpp_upload,
                "import_batch": batch,
                "source_type": row["source_type"],
                "source_row_key": row["source_row_key"],
                "satker_code": satker_code,
                "tahun": tahun,
                "no_bukti": row["no_kuitansi"],
                "no_bukti_norm": normalized_bukti_key(row["no_kuitansi"]),
                "tanggal_bukti": row["tanggal_bukti"],
                "penerima": row["penerima"],
                "keperluan": row["keperluan"],
                "akun": row["akun"],
                "jumlah": row["bruto"] or row["netto"],
                "nilai_bruto": row["bruto"],
                "nilai_netto": row["netto"],
            }
        )
        
        # Reconcile with D_K (handles created/updated/skipped counting)
        reconcile_drpp_item_to_dk(drpp_item, row, batch, user, item_is_new=created_item)
        
    batch.save()
    return {"ok": True, "batch": batch}


def reconcile_drpp_item_to_dk(drpp_item, row, batch, user, item_is_new=True):
    # DRPPItem -> DRPPMatch -> TransactionDetail
    match_obj, created_match = DRPPMatch.objects.get_or_create(
        drpp_item=drpp_item,
        defaults={
            "drpp_upload": drpp_item.drpp_upload,
            "status_match": DRPPMatch.StatusMatch.PERLU_DICEK
        }
    )
    
    dk = match_obj.transaction_detail
    
    if not dk:
        # Exact match logic
        # Satker + Tahun + No Kuitansi exact
        qs = TransactionDetail.objects.filter(
            satker_code=row["satker_code"],
            no_kuitansi=row["no_kuitansi"],
            created_at__year=int(row["tahun"]) if row["tahun"] else datetime.now().year
        )
        
        dk = qs.first()
        
        if not dk:
            # Create NEW
            if row["akun"] and row["satker_code"] and row["tahun"] and row["no_kuitansi"] and (row["bruto"] or row["netto"]):
                dk = TransactionDetail.objects.create(
                    satker_code=row["satker_code"],
                    no_kuitansi=row["no_kuitansi"],
                    no_drpp=row["nomor_drpp"] or "",
                    akun=row["akun"],
                    nilai_bruto=row["bruto"],
                    nilai_netto=row["netto"],
                    status_detail=TransactionDetail.StatusDetail.MENUNGGU_SPM,
                    drpp_status=TransactionDetail.DRPPStatus.ADA,
                    created_by=user,
                    deskripsi=row["keperluan"]
                )
                TransactionChangeLog.objects.create(
                    transaction=dk,
                    field_name="*ALL*",
                    new_value="Created from DRPP",
                    change_source=TransactionChangeLog.ChangeSource.IMPORT,
                    changed_by=user
                )
                batch.created_rows += 1
            else:
                # Cannot create, maybe missing mandatory fields
                # We skip creating D_K, but match remains PERLU_DICEK
                pass
        else:
            # UPDATE existing
            updates = []
            if not dk.no_drpp and row["nomor_drpp"]:
                dk.no_drpp = row["nomor_drpp"]
                updates.append("no_drpp")
            if not dk.akun and row["akun"]:
                dk.akun = row["akun"]
                updates.append("akun")
                
            if updates:
                dk.drpp_status = TransactionDetail.DRPPStatus.ADA
                dk.save()
                TransactionChangeLog.objects.create(
                    transaction=dk,
                    field_name=",".join(updates),
                    new_value="Updated from DRPP",
                    change_source=TransactionChangeLog.ChangeSource.IMPORT,
                    changed_by=user
                )
                batch.updated_rows += 1
            else:
                batch.skipped_rows += 1
                
        if dk:
            match_obj.transaction_detail = dk
            match_obj.status_match = DRPPMatch.StatusMatch.COCOK_OTOMATIS
            match_obj.save()
    else:
        # Already linked, check for updates
        updates = []
        if not dk.no_drpp and row["nomor_drpp"]:
            dk.no_drpp = row["nomor_drpp"]
            updates.append("no_drpp")
            
        if updates:
            dk.save()
            TransactionChangeLog.objects.create(
                transaction=dk,
                field_name=",".join(updates),
                new_value="Updated from DRPP (Already linked)",
                change_source=TransactionChangeLog.ChangeSource.IMPORT,
                changed_by=user
            )
            batch.updated_rows += 1
        else:
            batch.skipped_rows += 1
