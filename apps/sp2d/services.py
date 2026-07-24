import hashlib
from decimal import Decimal
from django.db import transaction, IntegrityError
from django.db.models import Sum

from apps.sp2d.models import SP2DRaw, SP2DImportBatch
from apps.dk.models import TransactionDetail, TransactionChangeLog


def resolve_sp2d_year(row, batch_tahun):
    if row.get("tahun"):
        try:
            return int(row["tahun"])
        except ValueError:
            pass
    if batch_tahun:
        return int(batch_tahun)
    for date_field in ["tgl_sp2d", "tanggal_selesai_sp2d", "tanggal_invoice"]:
        val = row.get(date_field)
        if val and hasattr(val, "year"):
            return val.year
        elif val and isinstance(val, str) and len(val) >= 4:
            try:
                return int(val[:4])
            except ValueError:
                pass
    return None

def normalize_sp2d_number(value):
    return str(value or "").strip().upper()

def normalize_money_for_identity(value):
    if value is None or value == "":
        return ""
    try:
        dec = Decimal(str(value))
        if dec == dec.to_integral_value():
            return str(int(dec))
        return str(dec.normalize())
    except:
        return str(value).strip()

def normalize_date_for_identity(value):
    if value is None or value == "":
        return ""
    return str(value).strip()

def generate_identity_key(satker, sp2d_no, invoice_no, spm_no, tgl_sp2d, tgl_invoice, nilai, tahun):
    """
    Deprecated: use build_identity_result
    """
    satker = normalize_sp2d_number(satker)
    tahun = str(tahun or "").strip()
    sp2d_no = normalize_sp2d_number(sp2d_no)
    invoice_no = normalize_sp2d_number(invoice_no)
    spm_no = normalize_sp2d_number(spm_no)
    tgl_sp2d = normalize_date_for_identity(tgl_sp2d)
    tgl_invoice = normalize_date_for_identity(tgl_invoice)
    nilai = normalize_money_for_identity(nilai)

    if sp2d_no:
        base = f"{satker}|{sp2d_no}|{tahun}"
    else:
        doc_no = invoice_no or spm_no
        tgl = tgl_sp2d or tgl_invoice
        base = f"{satker}|{doc_no}|{tgl}|{nilai}|{tahun}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()

def build_identity_result(satker, sp2d_no, invoice_no, spm_no, tgl_sp2d, tgl_invoice, nilai, tahun):
    satker = normalize_sp2d_number(satker)
    tahun = str(tahun or "").strip()
    sp2d_no = normalize_sp2d_number(sp2d_no)
    invoice_no = normalize_sp2d_number(invoice_no)
    spm_no = normalize_sp2d_number(spm_no)
    tgl_sp2d = normalize_date_for_identity(tgl_sp2d)
    tgl_invoice = normalize_date_for_identity(tgl_invoice)
    nilai = normalize_money_for_identity(nilai)

    if not satker or not tahun:
        return {"status": "GAGAL", "reason": "IDENTITAS_TIDAK_LENGKAP", "identity_key": None, "tahun": tahun}

    if sp2d_no:
        base = f"{satker}|{sp2d_no}|{tahun}"
    else:
        doc_no = invoice_no or spm_no
        tgl = tgl_sp2d or tgl_invoice
        if not doc_no or not tgl or not str(nilai):
            return {"status": "GAGAL", "reason": "IDENTITAS_TIDAK_LENGKAP", "identity_key": None, "tahun": tahun}
        base = f"{satker}|{doc_no}|{tgl}|{nilai}|{tahun}"
    
    return {
        "status": "OK",
        "reason": "",
        "identity_key": hashlib.sha256(base.encode("utf-8")).hexdigest(),
        "tahun": tahun
    }

def prepare_sp2d_rows(batch_tahun, raw_rows):
    prepared = []
    for row in raw_rows:
        r = row.copy()
        r["batch_tahun"] = resolve_sp2d_year(r, batch_tahun)
        ident = build_identity_result(
            satker=r.get("satker_code"),
            sp2d_no=r.get("no_sp2d"),
            invoice_no=r.get("nomor_invoice"),
            spm_no=r.get("nomor_spm_extracted"),
            tgl_sp2d=r.get("tgl_sp2d"),
            tgl_invoice=r.get("tanggal_invoice"),
            nilai=r.get("nilai_sp2d"),
            tahun=r["batch_tahun"]
        )
        r["identity_status"] = ident["status"]
        r["identity_reason"] = ident["reason"]
        r["identity_key"] = ident["identity_key"]
        prepared.append(r)
    return prepared


def _is_identical(existing_obj, row_data, tahun):
    fields_to_check = [
        "satker_code", "satker_name", "no_sp2d", "tanggal_selesai_sp2d",
        "tgl_sp2d", "mata_uang", "nilai_spm", "potongan", "nilai_sp2d",
        "nomor_invoice", "tanggal_invoice", "jenis_spm", "jenis_sp2d",
        "deskripsi", "nomor_spm_extracted"
    ]
    if getattr(existing_obj, "tahun") != tahun:
        return False
    for field in fields_to_check:
        new_val = row_data.get(field)
        if new_val is None:
            continue
        old_val = getattr(existing_obj, field)
        
        # Format comparison safely
        if isinstance(new_val, Decimal) or isinstance(old_val, Decimal):
            if Decimal(str(new_val or 0)) != Decimal(str(old_val or 0)):
                return False
        elif str(new_val) != str(old_val):
            return False
            
    return True

def find_legacy_candidates(prepared_row):
    """
    Find legacy candidates (identity_key IS NULL).
    Primary match: normalized satker + tahun + normalized no_sp2d
    Fallback match: normalized satker + tahun + (invoice or spm) + date + normalized nilai
    """
    satker = normalize_sp2d_number(prepared_row.get("satker_code"))
    tahun = prepared_row.get("batch_tahun")
    
    if not satker or not tahun:
        return []
        
    candidates = list(SP2DRaw.objects.filter(
        identity_key__isnull=True,
        satker_code__iexact=satker,
        tahun=tahun
    ))
    
    # Primary match
    no_sp2d = normalize_sp2d_number(prepared_row.get("no_sp2d"))
    if no_sp2d:
        matches = []
        for c in candidates:
            if normalize_sp2d_number(c.no_sp2d) == no_sp2d:
                matches.append(c)
                if len(matches) >= 2:
                    break
        return matches
        
    # Fallback match
    invoice = normalize_sp2d_number(prepared_row.get("nomor_invoice"))
    spm = normalize_sp2d_number(prepared_row.get("nomor_spm_extracted"))
    doc_no = invoice or spm
    tgl = normalize_date_for_identity(prepared_row.get("tgl_sp2d")) or normalize_date_for_identity(prepared_row.get("tanggal_invoice"))
    nilai = normalize_money_for_identity(prepared_row.get("nilai_sp2d"))
    
    if not doc_no or not tgl or not nilai:
        return []
        
    # Fallback match filtering is done on the same candidates

    matches = []
    for c in candidates:
        c_invoice = normalize_sp2d_number(c.nomor_invoice)
        c_spm = normalize_sp2d_number(c.nomor_spm_extracted)
        c_doc = c_invoice or c_spm
        c_tgl = normalize_date_for_identity(c.tgl_sp2d) or normalize_date_for_identity(c.tanggal_invoice)
        c_nilai = normalize_money_for_identity(c.nilai_sp2d)
        
        if c_doc == doc_no and c_tgl == tgl and c_nilai == nilai:
            matches.append(c)
            if len(matches) >= 2:
                break
                
    return matches


def classify_sp2d_rows(batch_tahun, mapped_rows):
    """
    Pseudo-classification without DB lock, used for preview.
    """
    prepared_rows = prepare_sp2d_rows(batch_tahun, mapped_rows)
    results = []
    seen_identities = {}
    
    for row in prepared_rows:
        identity_key = row["identity_key"]
        
        if row["identity_status"] == "GAGAL":
            row["preview_status"] = "GAGAL"
            row["preview_reason"] = row["identity_reason"]
            results.append(row)
            continue
        
        status = "BARU"
        
        if identity_key in seen_identities:
            status = "KONFLIK"
        else:
            seen_identities[identity_key] = True
            existing = list(SP2DRaw.objects.filter(identity_key=identity_key)[:2])
            
            # Legacy fallback check
            if not existing:
                legacy = find_legacy_candidates(row)
                if len(legacy) == 1:
                    existing = legacy
                elif len(legacy) > 1:
                    status = "KONFLIK"
            
            if len(existing) == 1:
                if _is_identical(existing[0], row, row["batch_tahun"]):
                    status = "IDENTIK_DILEWATI"
                else:
                    status = "AKAN_DIPERBARUI"
            elif len(existing) > 1:
                status = "KONFLIK"
                
        row["preview_status"] = status
        results.append(row)
        
    return results

def commit_sp2d_rows(batch, mapped_rows, user, filename=""):
    """
    Commit rows idempotently.
    Updates SP2DImportBatch metrics.
    Calls reconcile_sp2d_with_dk.
    """
    prepared_rows = prepare_sp2d_rows(batch.tahun, mapped_rows)
    identities = [r["identity_key"] for r in prepared_rows if r["identity_key"]]

    existing_records = {
        record.identity_key: record
        for record in SP2DRaw.objects.select_for_update().filter(identity_key__in=identities)
    }

    # Detect duplicates in DB (same identity_key, more than one record)
    from django.db.models import Count
    conflict_keys_db = set(
        SP2DRaw.objects.filter(identity_key__in=identities)
        .values("identity_key")
        .annotate(count=Count("id"))
        .filter(count__gt=1)
        .values_list("identity_key", flat=True)
    )

    seen_in_batch = set()
    conflict_keys_batch = set()
    for key in identities:
        if key in seen_in_batch:
            conflict_keys_batch.add(key)
        seen_in_batch.add(key)

    created_count = 0
    updated_count = 0
    skipped_count = 0
    conflict_count = 0
    success_count = 0
    failed_count = batch.failed_rows  # carry over from parser invalid

    for row in prepared_rows:
        key = row["identity_key"]
        if row["identity_status"] == "GAGAL":
            failed_count += 1
            continue

        if key in conflict_keys_db or key in conflict_keys_batch:
            conflict_count += 1
            continue

        record = existing_records.get(key)

        # Legacy matching: identity_key was NULL before backfill migration
        if not record:
            legacy = find_legacy_candidates(row)
            if len(legacy) == 1:
                record = legacy[0]
            elif len(legacy) > 1:
                conflict_count += 1
                continue

        if record:
            if _is_identical(record, row, row["batch_tahun"]):
                skipped_count += 1
                success_count += 1
                
                # Persist identity_key + batch history on IDENTIK skip
                fields_to_update = []
                if record.identity_key != key:
                    record.identity_key = key
                    fields_to_update.append("identity_key")
                if record.tahun != row["batch_tahun"]:
                    record.tahun = row["batch_tahun"]
                    fields_to_update.append("tahun")
                if record.last_import_batch_id != batch.id:
                    record.last_import_batch = batch
                    fields_to_update.append("last_import_batch")
                if filename and record.original_file != filename:
                    record.original_file = filename
                    fields_to_update.append("original_file")
                    
                if fields_to_update:
                    fields_to_update.append("updated_at")
                    record.save(update_fields=fields_to_update)
                    
                reconcile_sp2d_with_dk(record, user)
                continue

            changed = False
            for field in [
                "satker_code", "satker_name", "no_sp2d", "tanggal_selesai_sp2d",
                "tgl_sp2d", "mata_uang", "nilai_spm", "potongan", "nilai_sp2d",
                "nomor_invoice", "tanggal_invoice", "jenis_spm", "jenis_sp2d",
                "deskripsi", "nomor_spm_extracted"
            ]:
                new_val = row.get(field)
                if new_val in [None, ""] and getattr(record, field) not in [None, ""]:
                    continue

                if isinstance(new_val, Decimal) or isinstance(getattr(record, field), Decimal):
                    nv = Decimal(str(new_val or 0))
                    ov = Decimal(str(getattr(record, field) or 0))
                    if nv == 0 and ov != 0:
                        continue
                    if nv != ov:
                        setattr(record, field, new_val)
                        changed = True
                elif getattr(record, field) != new_val:
                    setattr(record, field, new_val)
                    changed = True

            if changed:
                record.tahun = row["batch_tahun"]
                record.last_import_batch = batch
                if filename:
                    record.original_file = filename
                record.save()
                updated_count += 1
                success_count += 1
            else:
                skipped_count += 1
                success_count += 1

            reconcile_sp2d_with_dk(record, user)
        else:
            try:
                with transaction.atomic():
                    record = SP2DRaw(
                        import_batch=batch,
                        last_import_batch=batch,
                        tahun=row["batch_tahun"],
                        identity_key=key,
                        satker_code=row.get("satker_code", ""),
                        satker_name=row.get("satker_name", ""),
                        no_sp2d=row.get("no_sp2d", ""),
                        tanggal_selesai_sp2d=row.get("tanggal_selesai_sp2d"),
                        tgl_sp2d=row.get("tgl_sp2d"),
                        mata_uang=row.get("mata_uang", ""),
                        nilai_spm=row.get("nilai_spm") or 0,
                        potongan=row.get("potongan") or 0,
                        nilai_sp2d=row.get("nilai_sp2d") or 0,
                        nomor_invoice=row.get("nomor_invoice", ""),
                        tanggal_invoice=row.get("tanggal_invoice"),
                        jenis_spm=row.get("jenis_spm", ""),
                        jenis_sp2d=row.get("jenis_sp2d", ""),
                        deskripsi=row.get("deskripsi", ""),
                        nomor_spm_extracted=row.get("nomor_spm_extracted", ""),
                        bulan_sp2d=batch.bulan,
                        status=SP2DRaw.Status.PERLU_DETAIL,
                        original_file=filename,
                        created_by=user,
                    )
                    record.save()
                    created_count += 1
                    success_count += 1
                    reconcile_sp2d_with_dk(record, user)
            except IntegrityError:
                # Race condition: another thread created the record first
                try:
                    record = SP2DRaw.objects.get(identity_key=key)
                    if _is_identical(record, row, row["batch_tahun"]):
                        skipped_count += 1
                        success_count += 1
                    else:
                        conflict_count += 1
                except SP2DRaw.DoesNotExist:
                    failed_count += 1
            except Exception:
                failed_count += 1

    batch.created_rows = created_count
    batch.updated_rows = updated_count
    batch.skipped_rows = skipped_count
    batch.conflict_rows = conflict_count
    batch.success_rows = success_count
    batch.failed_rows = failed_count

    if conflict_count == 0 and failed_count == 0:
        batch.status = SP2DImportBatch.Status.COMPLETED
    elif success_count > 0:
        batch.status = SP2DImportBatch.Status.COMPLETED_WITH_REVIEW
    else:
        batch.status = SP2DImportBatch.Status.FAILED
    batch.save()



def reconcile_sp2d_with_dk(sp2d_record, user):
    """
    Auto-link SP2DRaw to existing TransactionDetails based on Satker, Nomor SPM exact, and Tahun exact.
    Checks:
    - Sum(nilai_bruto) == SP2DRaw.nilai_spm
    - Sum(nilai_netto) == SP2DRaw.nilai_sp2d
    - Sum(nilai_bruto - nilai_netto) == SP2DRaw.potongan
    Tolerance is max Rp 1 for each.
    """
    if not sp2d_record.satker_code or not sp2d_record.nomor_spm_extracted or not sp2d_record.tahun:
        sp2d_record.status = SP2DRaw.Status.PERLU_DETAIL
        sp2d_record.save(update_fields=['status'])
        return

    dk_items = list(TransactionDetail.objects.filter(
        satker_code=sp2d_record.satker_code,
        nomor_spm=sp2d_record.nomor_spm_extracted,
    ).exclude(status_detail=TransactionDetail.StatusDetail.DIARSIPKAN))
    
    # Filter by tahun exact
    dk_items = [
        item for item in dk_items 
        if item.tanggal_spm and item.tanggal_spm.year == sp2d_record.tahun
    ]
    
    total_bruto = sum(item.nilai_bruto for item in dk_items)
    total_netto = sum(item.nilai_netto for item in dk_items)
    total_potongan = sum((item.nilai_bruto - item.nilai_netto) for item in dk_items)
    
    diff_bruto = abs(total_bruto - sp2d_record.nilai_spm)
    diff_netto = abs(total_netto - sp2d_record.nilai_sp2d)
    diff_potongan = abs(total_potongan - sp2d_record.potongan)
    
    has_dk = len(dk_items) > 0
    
    conflict = False
    for item in dk_items:
        if item.sp2d_raw_id and item.sp2d_raw_id != sp2d_record.id:
            conflict = True
            
    if conflict:
        sp2d_record.status = SP2DRaw.Status.TIDAK_COCOK
        sp2d_record.cek_akun = "Konflik: D_K terhubung ke SP2D lain"
        sp2d_record.save(update_fields=['status', 'cek_akun'])
        return
        
    if has_dk:
        if diff_bruto <= 1 and diff_netto <= 1 and diff_potongan <= 1:
            for item in dk_items:
                if item.sp2d_raw_id != sp2d_record.id:
                    TransactionChangeLog.objects.create(
                        transaction=item,
                        field_name="sp2d_raw",
                        old_value=str(item.sp2d_raw_id) if item.sp2d_raw_id else "",
                        new_value=str(sp2d_record.id),
                        change_source=TransactionChangeLog.ChangeSource.SYSTEM,
                        changed_by=user
                    )
                    item.sp2d_raw = sp2d_record
                    item.save(update_fields=['sp2d_raw'])
            sp2d_record.status = SP2DRaw.Status.COCOK
            sp2d_record.cek_akun = ""
            sp2d_record.save(update_fields=['status', 'cek_akun'])
        else:
            sp2d_record.status = SP2DRaw.Status.TIDAK_COCOK
            sp2d_record.cek_akun = f"Total D_K tidak sama dengan SP2D (Bruto diff: {diff_bruto}, Netto diff: {diff_netto}, Potongan diff: {diff_potongan})"
            sp2d_record.save(update_fields=['status', 'cek_akun'])
    else:
        sp2d_record.status = SP2DRaw.Status.PERLU_DETAIL
        sp2d_record.save(update_fields=['status'])
