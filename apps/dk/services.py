import re

from apps.documents.models import ChecklistStatus, DocumentDriveLink
from apps.drpp.models import DRPPMatch, DRPPUpload
from apps.dk.models import TransactionDetail


DRPP_KEYWORDS = ("GU", "GUP", "UP", "TUP", "PTUP", "KKP")


def requires_drpp(transaction):
    no_drpp = (transaction.no_drpp or "").strip()
    if no_drpp and no_drpp != "-":
        return True

    text = f"{transaction.jenis_spm or ''} {transaction.cara_pembayaran or ''}".upper()
    tokens = set(re.findall(r"[A-Z0-9]+", text))
    if tokens.intersection(DRPP_KEYWORDS):
        return True

    return has_drpp_record(transaction)


def has_drpp_record(transaction):
    return (
        DRPPUpload.objects.filter(transaction_detail=transaction).exists()
        or DRPPMatch.objects.filter(transaction_detail=transaction).exists()
    )


def calculate_document_completion(transaction):
    statuses = list(ChecklistStatus.objects.filter(transaction_detail=transaction))
    total = len(statuses)
    ada = sum(1 for item in statuses if item.status == ChecklistStatus.Status.ADA)
    percent = round((ada / total) * 100, 2) if total else 0
    return {"total": total, "ada": ada, "percent": percent}


def calculate_transaction_document_status(transaction):
    completion = calculate_document_completion(transaction)
    if completion["total"] and completion["ada"] >= completion["total"]:
        return TransactionDetail.StatusDetail.LENGKAP, completion
    return TransactionDetail.StatusDetail.PERLU_REVIEW, completion


def refresh_transaction_document_status(transaction, verified_document_type=""):
    status_detail, completion = calculate_transaction_document_status(transaction)
    if transaction.status_detail != status_detail:
        transaction.status_detail = status_detail
        transaction.save(update_fields=["status_detail", "updated_at"])

    if verified_document_type and status_detail == TransactionDetail.StatusDetail.LENGKAP:
        DocumentDriveLink.objects.filter(
            transaction_detail=transaction,
            jenis_dokumen__iexact=verified_document_type,
        ).exclude(status=DocumentDriveLink.Status.AKTIF).update(status=DocumentDriveLink.Status.AKTIF)

    return status_detail, completion
