import hashlib
import mimetypes
import os
import re
import shutil
from datetime import date, datetime
from decimal import Decimal

from django.core.files import File
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.parsers import normalized_bukti_key, parse_date, parse_paket_spm_zip
from apps.core.satker import get_official_satker_code
from apps.dk.models import MasterAkun, TransactionChangeLog, TransactionDetail
from apps.documents.models import DocumentUpload

from .models import DRPPImportBatch, DRPPItem, DRPPMatch, DRPPUpload


def _sha256_file(file_path):
    digest = hashlib.sha256()
    with open(file_path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_source_row_key(file_hash, source_member_name, source_row_id):
    raw = f"{file_hash}{source_member_name}{source_row_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_drpp_hard_identity(satker_code, tahun, nomor_drpp):
    raw = f"{(satker_code or '').strip()}|{str(tahun or '').strip()}|{(nomor_drpp or '').strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_drpp_item_hard_identity(satker_code, tahun, nomor_drpp, no_kuitansi):
    raw = (
        f"{(satker_code or '').strip()}|{str(tahun or '').strip()}|"
        f"{(nomor_drpp or '').strip()}|{(no_kuitansi or '').strip()}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_kw_mandiri_hard_identity(satker_code, tahun, no_kuitansi):
    raw = f"{(satker_code or '').strip()}|{str(tahun or '').strip()}||{(no_kuitansi or '').strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _decimal_or_none(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _date_or_none(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    return parse_date(value)


def _inject_spm_fields_from_package(rows, package):
    """
    Inject authoritative SPM fields from a TransactionPackage into DRPP source rows.

    Only fills in fields that are blank in the row.  Does not overwrite existing
    values.  Does not modify row["message"] or row["status"] — the classifier
    will recompute those correctly after re-classification.
    """
    if not rows or not package:
        return
    for row in rows:
        if not row.get("nomor_spm") and package.nomor_spm:
            row["nomor_spm"] = package.nomor_spm
        if not row.get("tanggal_spm") and package.tanggal_spm:
            if hasattr(package.tanggal_spm, "isoformat"):
                row["tanggal_spm"] = package.tanggal_spm.isoformat()
            else:
                row["tanggal_spm"] = str(package.tanggal_spm)
        if not row.get("jenis_spm") and package.jenis_spm:
            row["jenis_spm"] = package.jenis_spm
        if not row.get("cara_pembayaran") and getattr(package, "cara_pembayaran", None):
            row["cara_pembayaran"] = package.cara_pembayaran
        if not row.get("bulan_sp2d") and getattr(package, "bulan_sp2d", None):
            row["bulan_sp2d"] = package.bulan_sp2d


def _row_from_item(*, item, source_type, satker_code, tahun, nomor_drpp, file_hash, source_row_id, metadata):
    no_kuitansi = (item.get("no_bukti") or "").strip()
    source_member_name = (
        item.get("source_member_name")
        or item.get("source_file")
        or item.get("source_member_name_detail")
        or item.get("source_file_detail")
        or "unknown.pdf"
    )
    source_row_id = str(item.get("source_row_id") or source_row_id)
    identity_key = None
    if satker_code and tahun and no_kuitansi:
        if source_type == DRPPItem.SourceType.DRPP_ITEM and nomor_drpp:
            identity_key = get_drpp_item_hard_identity(satker_code, tahun, nomor_drpp, no_kuitansi)
        elif source_type == DRPPItem.SourceType.KUITANSI_MANDIRI:
            identity_key = get_kw_mandiri_hard_identity(satker_code, tahun, no_kuitansi)

    tanggal_drpp = _date_or_none(metadata.get("tanggal_drpp"))
    # `jumlah` is intentionally retained as the raw source amount. It is never
    # promoted to both bruto and netto without two explicit parser fields.
    return {
        "source_type": source_type,
        "satker_code": satker_code,
        "tahun": tahun,
        "nomor_drpp": nomor_drpp,
        "no_kuitansi": no_kuitansi,
        "akun": (item.get("akun") or "").strip(),
        "jumlah": _decimal_or_none(item.get("jumlah")) or Decimal("0"),
        "bruto": _decimal_or_none(item.get("bruto")) if "bruto" in item else None,
        "netto": _decimal_or_none(item.get("netto")) if "netto" in item else None,
        "potongan": _decimal_or_none(item.get("potongan")),
        "tanggal_bukti": _date_or_none(item.get("tanggal_bukti")),
        "penerima": item.get("penerima", ""),
        "keperluan": item.get("keperluan", ""),
        "npwp": item.get("npwp", ""),
        "no_urut": item.get("no_urut"),
        "source_file": item.get("source_file", source_member_name),
        "source_file_hash": file_hash,
        "source_member_name": source_member_name,
        "source_row_id": source_row_id,
        "source_row_key": get_source_row_key(file_hash, source_member_name, source_row_id),
        "identity_key": identity_key,
        "parser_needs_review": bool(item.get("needs_review")),
        "parser_review_fields": list(item.get("review_fields") or []),
        "tanggal_drpp": tanggal_drpp,
        "jenis_spp": metadata.get("jenis_spp", ""),
        "bulan": metadata.get("bulan") or (tanggal_drpp.month if tanggal_drpp else None),
        "nomor_spm": metadata.get("nomor_spm", ""),
        "drpp_total": _decimal_or_none(metadata.get("total")) or Decimal("0"),
        "drpp_raw_text": metadata.get("raw_text", ""),
    }


def prepare_drpp_rows(zip_path, ocr=False, satker_code="", tahun=None):
    file_hash = _sha256_file(zip_path)
    parsed = None
    try:
        parsed = parse_paket_spm_zip(zip_path, ocr=ocr, drpp_kuitansi_mode=True)
        if not parsed["ok"]:
            return {"ok": False, "warnings": parsed["warnings"], "rows": [], "file_hash": file_hash}
        rows = []
        for drpp_index, drpp in enumerate(parsed.get("drpps", []), start=1):
            metadata = dict(drpp.get("metadata", {}))
            metadata["raw_text"] = drpp.get("text_sample", "")
            nomor_drpp = (metadata.get("nomor_drpp") or "").strip()
            items = parsed.get("kw_by_drpp", {}).get(nomor_drpp, [])
            if not items:
                items = [{"source_file": drpp.get("file_name", ""), "needs_review": True}]
            for item_index, item in enumerate(items, start=1):
                source_type = (
                    DRPPItem.SourceType.DRPP_ITEM if nomor_drpp else DRPPItem.SourceType.UNRESOLVED
                )
                rows.append(
                    _row_from_item(
                        item=item,
                        source_type=source_type,
                        satker_code=satker_code,
                        tahun=tahun,
                        nomor_drpp=nomor_drpp,
                        file_hash=file_hash,
                        source_row_id=f"drpp:{drpp_index}:row:{item_index}",
                        metadata=metadata,
                    )
                )

        for item_index, item in enumerate(parsed.get("kw_by_drpp", {}).get("TANPA_DRPP", []), start=1):
            source_type = (
                DRPPItem.SourceType.KUITANSI_MANDIRI
                if item.get("no_bukti")
                else DRPPItem.SourceType.UNRESOLVED
            )
            rows.append(
                _row_from_item(
                    item=item,
                    source_type=source_type,
                    satker_code=satker_code,
                    tahun=tahun,
                    nomor_drpp="",
                    file_hash=file_hash,
                    source_row_id=f"standalone:row:{item_index}",
                    metadata={},
                )
            )

        return {"ok": True, "rows": rows, "warnings": parsed["warnings"], "file_hash": file_hash}
    finally:
        temp_dir = parsed.get("temp_dir", "") if parsed else ""
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _document_year(dk):
    if dk.tanggal_spm:
        return dk.tanggal_spm.year
    for value in (dk.no_kuitansi, dk.no_drpp, dk.nomor_spm):
        match = re.search(r"(?:^|\D)(20\d{2})(?:\D|$)", value or "")
        if match:
            return int(match.group(1))
    return None


def _single_or_ambiguous(queryset, tahun):
    candidates = [dk for dk in queryset if _document_year(dk) == int(tahun)]
    if len(candidates) == 1:
        return candidates[0], False
    return None, len(candidates) > 1


def _find_exact_dk(row):
    if not (row.get("satker_code") and row.get("tahun") and row.get("no_kuitansi")):
        return None, False
    base = TransactionDetail.objects.filter(
        satker_code=row["satker_code"],
        no_kuitansi=row["no_kuitansi"],
    )
    if row.get("nomor_drpp"):
        dk, ambiguous = _single_or_ambiguous(base.filter(no_drpp=row["nomor_drpp"]), row["tahun"])
        if dk or ambiguous:
            return dk, ambiguous
    return _single_or_ambiguous(base, row["tahun"])


def _find_linked_identity_dk(row):
    identity_key = row.get("identity_key")
    if not identity_key:
        return None, False
    ids = list(
        DRPPMatch.objects.filter(
            drpp_item__identity_key=identity_key,
            transaction_detail__isnull=False,
        )
        .values_list("transaction_detail_id", flat=True)
        .distinct()[:2]
    )
    if len(ids) == 1:
        return TransactionDetail.objects.get(pk=ids[0]), False
    return None, len(ids) > 1


def _manual_differences(dk, row):
    differences = []
    comparisons = (
        ("akun", row.get("akun"), dk.akun),
        ("nilai_bruto", row.get("bruto"), dk.nilai_bruto),
        ("nilai_netto", row.get("netto"), dk.nilai_netto),
        ("deskripsi", (row.get("keperluan") or "").strip(), (dk.deskripsi or "").strip()),
    )
    for field, incoming, current in comparisons:
        if incoming not in (None, "") and current not in (None, "") and incoming != current:
            differences.append(field)
    if row.get("nomor_drpp") and dk.no_drpp and row["nomor_drpp"] != dk.no_drpp:
        differences.append("no_drpp")
    return differences


def _recompute_identity(row):
    if not (row.get("satker_code") and row.get("tahun") and row.get("no_kuitansi")):
        row["identity_key"] = None
    elif row.get("source_type") == DRPPItem.SourceType.DRPP_ITEM and row.get("nomor_drpp"):
        row["identity_key"] = get_drpp_item_hard_identity(
            row["satker_code"], row["tahun"], row["nomor_drpp"], row["no_kuitansi"]
        )
    elif row.get("source_type") == DRPPItem.SourceType.KUITANSI_MANDIRI:
        row["identity_key"] = get_kw_mandiri_hard_identity(
            row["satker_code"], row["tahun"], row["no_kuitansi"]
        )
    else:
        row["identity_key"] = None


def classify_drpp_rows(rows, user_corrections=None):
    user_corrections = user_corrections or {}
    active_akun_set = set(MasterAkun.objects.filter(is_active=True).values_list("kode", flat=True))
    classified_rows = []
    for row in rows:
        correction = user_corrections.get(row["source_row_key"], {})
        for field in ("akun", "no_kuitansi", "nomor_drpp"):
            if field in correction:
                row[field] = (correction[field] or "").strip()
        for field in ("bruto", "netto"):
            if field in correction:
                row[field] = _decimal_or_none(correction[field])
        _recompute_identity(row)

        if not row.get("identity_key"):
            row.update(status="REVIEW_IDENTITAS", message="Identitas DRPP/kuitansi belum lengkap")
        elif row.get("bruto") is None or row.get("netto") is None:
            row.update(status="REVIEW_NOMINAL", message="Bruto dan netto harus terbaca atau dikoreksi eksplisit")
        elif not row.get("akun"):
            row.update(status="REVIEW_AKUN", message="Akun kosong atau tidak aktif di MasterAkun")
        elif row["akun"] not in active_akun_set:
            row.update(status="REVIEW_AKUN", message=f"Akun {row['akun']} tidak aktif di MasterAkun")
        elif row.get("parser_needs_review"):
            reasons = ", ".join(row.get("parser_review_fields") or ["hasil parser perlu dicek"])
            row.update(status="REVIEW", message=reasons)
        else:
            dk, ambiguous = _find_linked_identity_dk(row)
            if not dk and not ambiguous:
                dk, ambiguous = _find_exact_dk(row)
            if ambiguous:
                row.update(status="KONFLIK_AMBIGU", message="Lebih dari satu D_K cocok secara exact")
            elif not dk:
                row.update(status="BARU", message="Data baru")
            elif dk.status_detail == TransactionDetail.StatusDetail.FINAL:
                row.update(status="KONFLIK_TERKUNCI", message="Data sudah final/diarsipkan", matched_dk_id=dk.pk)
            elif dk.status_detail == TransactionDetail.StatusDetail.DIARSIPKAN:
                row.update(status="KONFLIK_DIARSIPKAN", message="Data sudah diarsipkan", matched_dk_id=dk.pk)
            else:
                differences = _manual_differences(dk, row)
                if differences:
                    row.update(
                        status="KONFLIK_DATA_MANUAL",
                        message=f"Data manual berbeda: {', '.join(differences)}",
                        differences=differences,
                        matched_dk_id=dk.pk,
                    )
                elif not dk.no_drpp and row.get("nomor_drpp"):
                    row.update(status="UPDATE", message="Lengkapi nomor DRPP", matched_dk_id=dk.pk)
                else:
                    row.update(status="SKIP", message="Data sama persis", matched_dk_id=dk.pk)
        classified_rows.append(row)
    return classified_rows


def _create_document_upload(file_path, original_filename, file_hash, user):
    document = DocumentUpload(
        document_type="DRPP_BATCH",
        original_filename=original_filename,
        stored_filename=os.path.basename(original_filename),
        file_hash=file_hash,
        file_size=os.path.getsize(file_path),
        mime_type=mimetypes.guess_type(original_filename)[0] or "application/octet-stream",
        uploaded_by=user,
        notes="Sumber lokal batch DRPP; arsip eksternal diproses setelah commit database.",
    )
    with open(file_path, "rb") as source:
        document.file.save(os.path.basename(original_filename), File(source), save=False)
    document.stored_filename = os.path.basename(document.file.name)
    document.save()
    return document


def _get_or_create_drpp_upload(batch, row, user, document_upload):
    if row["source_type"] != DRPPItem.SourceType.DRPP_ITEM or not row.get("nomor_drpp"):
        return None
    identity_key = get_drpp_hard_identity(batch.satker_code, batch.tahun, row["nomor_drpp"])
    defaults = {
        "import_batch": batch,
        "first_import_batch": batch,
        "last_import_batch": batch,
        "document_upload": document_upload,
        "nomor_drpp": row["nomor_drpp"],
        "nomor_drpp_norm": normalized_bukti_key(row["nomor_drpp"]),
        "tanggal_drpp": row.get("tanggal_drpp"),
        "jenis_spp": row.get("jenis_spp", ""),
        "bulan": row.get("bulan"),
        "tahun": batch.tahun,
        "satker_code": batch.satker_code,
        "nomor_spm": row.get("nomor_spm", ""),
        "total_jumlah": row.get("drpp_total") or Decimal("0"),
        "raw_text": row.get("drpp_raw_text", ""),
        "uploaded_by": user,
    }
    try:
        with transaction.atomic():
            upload, created = DRPPUpload.objects.get_or_create(identity_key=identity_key, defaults=defaults)
    except IntegrityError:
        upload = DRPPUpload.objects.get(identity_key=identity_key)
        created = False
    if not created:
        for field, value in defaults.items():
            if field not in {"first_import_batch", "uploaded_by"} and value not in (None, ""):
                setattr(upload, field, value)
        if not upload.first_import_batch_id:
            upload.first_import_batch = batch
        upload.last_import_batch = batch
        upload.save()
    return upload


def _persist_source_item(batch, row, drpp_upload):
    verification_by_status = {
        "REVIEW_IDENTITAS": DRPPItem.StatusVerifikasi.PERLU_REVIEW,
        "REVIEW_NOMINAL": DRPPItem.StatusVerifikasi.PERLU_REVIEW,
        "REVIEW_AKUN": DRPPItem.StatusVerifikasi.PERLU_REVIEW,
        "REVIEW": DRPPItem.StatusVerifikasi.PERLU_REVIEW,
        "KONFLIK_DATA_MANUAL": DRPPItem.StatusVerifikasi.TIDAK_SESUAI,
        "KONFLIK_AMBIGU": DRPPItem.StatusVerifikasi.TIDAK_SESUAI,
        "KONFLIK_TERKUNCI": DRPPItem.StatusVerifikasi.TIDAK_SESUAI,
        "KONFLIK_DIARSIPKAN": DRPPItem.StatusVerifikasi.TIDAK_SESUAI,
    }
    defaults = {
        "drpp_upload": drpp_upload,
        "import_batch": batch,
        "source_type": row["source_type"],
        "identity_key": row.get("identity_key"),
        "source_file_hash": row["source_file_hash"],
        "source_member_name": row["source_member_name"],
        "source_row_id": row["source_row_id"],
        "satker_code": batch.satker_code,
        "tahun": batch.tahun,
        "no_urut": row.get("no_urut"),
        "no_bukti": row.get("no_kuitansi", ""),
        "no_bukti_norm": normalized_bukti_key(row.get("no_kuitansi", "")),
        "tanggal_bukti": row.get("tanggal_bukti"),
        "penerima": row.get("penerima", ""),
        "keperluan": row.get("keperluan", ""),
        "npwp": row.get("npwp", ""),
        "akun": row.get("akun", ""),
        "jumlah": row.get("jumlah") or Decimal("0"),
        "nilai_bruto": row.get("bruto"),
        "nilai_netto": row.get("netto"),
        "potongan": row.get("potongan"),
        "status_verifikasi": verification_by_status.get(row["status"], DRPPItem.StatusVerifikasi.BELUM_DICEK),
        "catatan": row.get("message", ""),
    }
    try:
        with transaction.atomic():
            item, created = DRPPItem.objects.get_or_create(
                source_row_key=row["source_row_key"], defaults=defaults
            )
    except IntegrityError:
        try:
            item = DRPPItem.objects.get(source_row_key=row["source_row_key"])
        except DRPPItem.DoesNotExist:
            if not row.get("identity_key"):
                raise
            item = DRPPItem.objects.get(identity_key=row["identity_key"])
        created = False
    if not created:
        item.source_row_key = row["source_row_key"]
        for field, value in defaults.items():
            setattr(item, field, value)
        item.save()
    return item, created


def _get_or_create_match(item):
    try:
        with transaction.atomic():
            return DRPPMatch.objects.get_or_create(
                drpp_item=item,
                defaults={"drpp_upload": item.drpp_upload, "status_match": DRPPMatch.StatusMatch.PERLU_DICEK},
            )[0]
    except IntegrityError:
        return DRPPMatch.objects.get(drpp_item=item)


def _set_match_conflict(item, row, dk=None):
    match = _get_or_create_match(item)
    match.transaction_detail = dk
    match.status_match = DRPPMatch.StatusMatch.KONFLIK
    match.catatan = row.get("message", "")
    match.save()


def reconcile_drpp_item_to_dk(drpp_item, row, user):
    match = _get_or_create_match(drpp_item)
    dk = match.transaction_detail
    ambiguous = False
    if not dk:
        dk, ambiguous = _find_linked_identity_dk(row)
    if not dk and not ambiguous:
        dk, ambiguous = _find_exact_dk(row)
    if ambiguous:
        row["message"] = "Lebih dari satu D_K cocok secara exact"
        _set_match_conflict(drpp_item, row)
        return "conflict"

    if dk:
        if dk.status_detail == TransactionDetail.StatusDetail.FINAL:
            row["status"] = "KONFLIK_TERKUNCI"
            row["message"] = "D_K final tidak boleh diubah"
            _set_match_conflict(drpp_item, row, dk)
            return "conflict"
        if dk.status_detail == TransactionDetail.StatusDetail.DIARSIPKAN:
            row["status"] = "KONFLIK_DIARSIPKAN"
            row["message"] = "D_K diarsipkan tidak boleh diubah"
            _set_match_conflict(drpp_item, row, dk)
            return "conflict"
        differences = _manual_differences(dk, row)
        if differences:
            row["message"] = f"KONFLIK_DATA_MANUAL: {', '.join(differences)}"
            drpp_item.status_verifikasi = DRPPItem.StatusVerifikasi.TIDAK_SESUAI
            drpp_item.catatan = row["message"]
            drpp_item.save(update_fields=["status_verifikasi", "catatan", "updated_at"])
            _set_match_conflict(drpp_item, row, dk)
            return "conflict"
        if not dk.no_drpp and row.get("nomor_drpp"):
            dk.no_drpp = row["nomor_drpp"]
            dk.drpp_status = TransactionDetail.DRPPStatus.ADA
            dk.save(update_fields=["no_drpp", "drpp_status", "updated_at"])
            TransactionChangeLog.objects.create(
                transaction=dk,
                field_name="no_drpp",
                old_value="",
                new_value=row["nomor_drpp"],
                change_source=TransactionChangeLog.ChangeSource.IMPORT,
                changed_by=user,
            )
            outcome = "updated"
        else:
            outcome = "skipped"
    else:
        # Normalize satker_code to 6-digit official code
        raw_satker_code = row.get("satker_code", "")
        satker_code = get_official_satker_code(raw_satker_code) or raw_satker_code

        dk = TransactionDetail.objects.create(
            satker_code=satker_code,
            no_kuitansi=row["no_kuitansi"],
            no_drpp=row.get("nomor_drpp", ""),
            akun=row["akun"],
            nilai_bruto=row["bruto"],
            nilai_netto=row["netto"],
            status_detail=TransactionDetail.StatusDetail.MENUNGGU_SPM,
            drpp_status=TransactionDetail.DRPPStatus.ADA,
            created_by=user,
            deskripsi=row.get("keperluan", ""),
        )
        TransactionChangeLog.objects.create(
            transaction=dk,
            field_name="*ALL*",
            new_value="Created from DRPP",
            change_source=TransactionChangeLog.ChangeSource.IMPORT,
            changed_by=user,
        )
        outcome = "created"

    match.transaction_detail = dk
    match.status_match = DRPPMatch.StatusMatch.COCOK_OTOMATIS
    match.catatan = "Exact satker + kuitansi + tahun dokumen"
    match.save()
    drpp_item.status_verifikasi = DRPPItem.StatusVerifikasi.SESUAI
    drpp_item.catatan = ""
    drpp_item.save(update_fields=["status_verifikasi", "catatan", "updated_at"])
    return outcome


@transaction.atomic
def commit_drpp_rows(zip_path, ocr, satker_code, tahun, user, filename, original_filename, user_corrections=None, inherited_spm_package=None):
    from apps.accounts.access import get_user_satker_code, is_admin

    satker_code = (satker_code or "").strip()
    user_satker = get_user_satker_code(user)
    if not satker_code:
        return {"ok": False, "error": ["Satker wajib dipilih."]}
    if user_satker and not is_admin(user) and user_satker != satker_code:
        return {"ok": False, "error": [f"Akses ditolak: Anda tidak bisa mengupload untuk satker {satker_code}"]}

    try:
        prep = prepare_drpp_rows(zip_path, ocr=ocr, satker_code=satker_code, tahun=tahun)
    except Exception as exc:
        prep = {"ok": False, "warnings": [str(exc)], "rows": [], "file_hash": _sha256_file(zip_path)}

    batch = DRPPImportBatch.objects.create(
        uploaded_by=user,
        filename=filename,
        original_filename=original_filename,
        satker_code=satker_code,
        tahun=int(tahun),
        file_hash=prep["file_hash"],
        total_rows=len(prep["rows"]),
        status=DRPPImportBatch.Status.PROCESSING,
        notes="; ".join(prep["warnings"]),
    )
    document_upload = _create_document_upload(zip_path, original_filename, prep["file_hash"], user)
    document_upload.notes = f"Batch DRPP #{batch.pk}; arsip eksternal diproses setelah commit database."
    document_upload.save(update_fields=["notes"])
    batch.document_upload = document_upload
    batch.save(update_fields=["document_upload"])
    if not prep["ok"]:
        batch.status = DRPPImportBatch.Status.FAILED
        batch.notes = "; ".join(prep["warnings"])
        batch.save(update_fields=["status", "notes"])
        return {"ok": False, "error": prep["warnings"], "batch": batch, "document_upload": document_upload}

    # Inject SPM fields from inherited parent package (if user manually selected a parent SPM).
    # This must happen BEFORE classify so the classifier sees the SPM metadata.
    if inherited_spm_package:
        _inject_spm_fields_from_package(prep["rows"], inherited_spm_package)

    for source_row in prep["rows"]:
        source_row["satker_code"] = batch.satker_code
        source_row["tahun"] = batch.tahun
    rows = classify_drpp_rows(prep["rows"], user_corrections)
    parent_cache = {}
    review_statuses = {"REVIEW", "REVIEW_IDENTITAS", "REVIEW_NOMINAL", "REVIEW_AKUN"}
    conflict_statuses = {
        "KONFLIK_AMBIGU",
        "KONFLIK_TERKUNCI",
        "KONFLIK_DIARSIPKAN",
        "KONFLIK_DATA_MANUAL",
    }
    for row in rows:
        row["satker_code"] = batch.satker_code
        row["tahun"] = batch.tahun
        parent_key = row.get("nomor_drpp", "")
        if parent_key not in parent_cache:
            parent_cache[parent_key] = _get_or_create_drpp_upload(batch, row, user, document_upload)
        item, _ = _persist_source_item(batch, row, parent_cache[parent_key])

        if row["status"] in review_statuses:
            batch.review_rows += 1
            _get_or_create_match(item)
        elif row["status"] in conflict_statuses:
            batch.conflict_rows += 1
            dk = TransactionDetail.objects.filter(pk=row.get("matched_dk_id")).get() if row.get("matched_dk_id") else None
            _set_match_conflict(item, row, dk)
        elif row["status"] == "GAGAL":
            batch.failed_rows += 1
        else:
            outcome = reconcile_drpp_item_to_dk(item, row, user)
            setattr(batch, f"{outcome}_rows", getattr(batch, f"{outcome}_rows") + 1)

    counted = sum(
        getattr(batch, field)
        for field in ("created_rows", "updated_rows", "skipped_rows", "conflict_rows", "review_rows", "failed_rows")
    )
    if counted != batch.total_rows:
        raise ValueError(f"Invariant statistik batch gagal: {counted} != {batch.total_rows}")
    for upload in parent_cache.values():
        if not upload:
            continue
        item_statuses = set(upload.items.values_list("status_verifikasi", flat=True))
        if upload.matches.filter(status_match=DRPPMatch.StatusMatch.KONFLIK).exists():
            upload.match_status = DRPPUpload.MatchStatus.KONFLIK
        elif item_statuses <= {DRPPItem.StatusVerifikasi.SESUAI}:
            upload.match_status = DRPPUpload.MatchStatus.COCOK
        else:
            upload.match_status = DRPPUpload.MatchStatus.PERLU_DICEK
        upload.status_updated_at = timezone.now()
        upload.save(update_fields=["match_status", "status_updated_at"])
    batch.status = (
        DRPPImportBatch.Status.COMPLETED_WITH_REVIEW
        if batch.review_rows or batch.conflict_rows or batch.failed_rows
        else DRPPImportBatch.Status.COMPLETED
    )
    batch.save()
    return {"ok": True, "batch": batch, "document_upload": document_upload}
