import re

from apps.drpp.models import DRPPMatch, DRPPUpload


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
