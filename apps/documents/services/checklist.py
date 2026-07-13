from apps.documents.models import ChecklistStatus


def mark_checklist_present(transaction, document_type, user=None):
    normalized = (document_type or "").upper()
    candidates = list(ChecklistStatus.objects.filter(transaction_detail=transaction))
    matched = False
    for status in candidates:
        name = status.nama_dokumen.upper()
        if normalized in name or name in normalized or (
            normalized == "KW" and ("KUITANSI" in name or "BUKTI" in name)
        ):
            status.status = ChecklistStatus.Status.ADA
            status.updated_by = user
            status.save(update_fields=["status", "updated_by", "updated_at"])
            matched = True
    if not matched:
        ChecklistStatus.objects.get_or_create(
            transaction_detail=transaction,
            nama_dokumen=document_type,
            defaults={"wajib": True, "status": ChecklistStatus.Status.ADA, "updated_by": user},
        )
