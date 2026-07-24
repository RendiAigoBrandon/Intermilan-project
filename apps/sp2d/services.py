import hashlib
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum

from apps.sp2d.models import SP2DRaw, SP2DImportBatch
from apps.dk.models import TransactionDetail, TransactionChangeLog


def generate_identity_key(satker, sp2d_no, invoice_no, spm_no, tgl_sp2d, tgl_invoice, nilai, tahun):
    """
    1. Utamakan Satker + No SP2D + Tahun
    2. Fallback Satker + Nomor Invoice / SPM + Tanggal Relevan + Nilai + Tahun
    """
    satker = str(satker or "").strip().upper()
    tahun = str(tahun or "").strip()
    sp2d_no = str(sp2d_no or "").strip().upper()
    invoice_no = str(invoice_no or "").strip().upper()
    spm_no = str(spm_no or "").strip().upper()
    tgl_sp2d = str(tgl_sp2d or "").strip()
    tgl_invoice = str(tgl_invoice or "").strip()
    nilai = str(nilai or "").strip()

    if sp2d_no:
        base = f"{satker}|{sp2d_no}|{tahun}"
    else:
        doc_no = invoice_no or spm_no
        tgl = tgl_sp2d or tgl_invoice
        base = f"{satker}|{doc_no}|{tgl}|{nilai}|{tahun}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


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


def classify_sp2d_rows(batch_tahun, mapped_rows):
    """
    Pseudo-classification without DB lock, used for preview.
    """
    results = []
    seen_identities = {}
    
    for idx, row in enumerate(mapped_rows):
        satker = row.get("satker_code")
        sp2d = row.get("no_sp2d")
        invoice = row.get("nomor_invoice")
        spm = row.get("nomor_spm_extracted")
        tgl_sp2d = row.get("tgl_sp2d")
        tgl_invoice = row.get("tanggal_invoice")
        nilai = row.get("nilai_sp2d")
        
        identity_key = generate_identity_key(satker, sp2d, invoice, spm, tgl_sp2d, tgl_invoice, nilai, batch_tahun)
        
        status = "BARU"
        
        if identity_key in seen_identities:
            status = "KONFLIK"
        else:
            seen_identities[identity_key] = True
            existing = list(SP2DRaw.objects.filter(identity_key=identity_key)[:2])
            
            if len(existing) == 1:
                if _is_identical(existing[0], row, batch_tahun):
                    status = "IDENTIK_DILEWATI"
                else:
                    status = "AKAN_DIPERBARUI"
            elif len(existing) > 1:
                status = "KONFLIK"
                
        r = row.copy()
        r["_identity_key"] = identity_key
        r["_status"] = status
        results.append(r)
        
    return results


@transaction.atomic
def commit_sp2d_rows(batch, mapped_rows, user):
    """
    Commit rows idempotently.
    Updates SP2DImportBatch metrics.
    Calls reconcile_sp2d_with_dk.
    """
    identities = []
    for row in mapped_rows:
        satker = row.get("satker_code")
        sp2d = row.get("no_sp2d")
        invoice = row.get("nomor_invoice")
        spm = row.get("nomor_spm_extracted")
        tgl_sp2d = row.get("tgl_sp2d")
        tgl_invoice = row.get("tanggal_invoice")
        nilai = row.get("nilai_sp2d")
        identity_key = generate_identity_key(satker, sp2d, invoice, spm, tgl_sp2d, tgl_invoice, nilai, batch.tahun)
        row["_identity_key"] = identity_key
        identities.append(identity_key)
        
    existing_records = {
        record.identity_key: record
        for record in SP2DRaw.objects.select_for_update().filter(identity_key__in=identities)
    }
    
    conflict_keys_db = set(
        SP2DRaw.objects.filter(identity_key__in=identities)
        .values_list("identity_key", flat=True)
        .annotate(count=Sum(1))
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
    
    for row in mapped_rows:
        key = row["_identity_key"]
        if key in conflict_keys_db or key in conflict_keys_batch:
            conflict_count += 1
            continue
            
        record = existing_records.get(key)
        if record:
            if _is_identical(record, row, batch.tahun):
                skipped_count += 1
                success_count += 1
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
                
                # Numeric comparison safely without wiping
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
                record.tahun = batch.tahun
                record.save()
                updated_count += 1
                success_count += 1
            else:
                skipped_count += 1
                success_count += 1
                
            reconcile_sp2d_with_dk(record, user)
        else:
            record = SP2DRaw(
                import_batch=batch,
                tahun=batch.tahun,
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
                created_by=user,
            )
            record.save()
            created_count += 1
            success_count += 1
            
            reconcile_sp2d_with_dk(record, user)
            
    batch.created_rows = created_count
    batch.updated_rows = updated_count
    batch.skipped_rows = skipped_count
    batch.conflict_rows = conflict_count
    batch.success_rows = success_count
    batch.status = SP2DImportBatch.Status.COMPLETED if conflict_count == 0 else SP2DImportBatch.Status.COMPLETED_WITH_REVIEW
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
