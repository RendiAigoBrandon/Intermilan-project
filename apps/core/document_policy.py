"""Kebijakan kebutuhan dokumen berdasarkan keluarga Jenis SPM.

Modul ini hanya mendeskripsikan kebijakan. Aktivasi parser alternatif tetap
menjadi keputusan caller agar keluarga yang belum didukung tidak otomatis
menghasilkan transaksi.
"""

from enum import Enum
import re


class SPMFamily(str, Enum):
    GUP_REGULAR = "GUP_REGULAR"
    GUP_PNBP = "GUP_PNBP"
    GUP_KKP = "GUP_KKP"
    UP = "UP"
    TUP = "TUP"
    GTUP_NIHIL = "GTUP_NIHIL"
    GAJI = "GAJI"
    PENGHASILAN_PPNPN = "PENGHASILAN_PPNPN"
    TUNJANGAN_KINERJA = "TUNJANGAN_KINERJA"
    THR = "THR"
    GAJI_13 = "GAJI_13"
    NON_GAJI = "NON_GAJI"
    NON_GAJI_KONTRAKTUAL = "NON_GAJI_KONTRAKTUAL"
    UNKNOWN = "UNKNOWN"


class DocumentRequirement(str, Enum):
    DRPP_REQUIRED = "DRPP_REQUIRED"
    KKP_PAYMENT_LIST_REQUIRED = "KKP_PAYMENT_LIST_REQUIRED"
    NOMINATIVE_REQUIRED = "NOMINATIVE_REQUIRED"
    SOURCE_DOCUMENT_REQUIRED = "SOURCE_DOCUMENT_REQUIRED"
    HEADER_ONLY = "HEADER_ONLY"
    CONTEXT_DEPENDENT = "CONTEXT_DEPENDENT"
    UNSUPPORTED_REVIEW = "UNSUPPORTED_REVIEW"


POLICY_BY_FAMILY = {
    SPMFamily.GUP_REGULAR: DocumentRequirement.DRPP_REQUIRED,
    SPMFamily.GUP_PNBP: DocumentRequirement.DRPP_REQUIRED,
    SPMFamily.GUP_KKP: DocumentRequirement.KKP_PAYMENT_LIST_REQUIRED,
    SPMFamily.UP: DocumentRequirement.HEADER_ONLY,
    SPMFamily.TUP: DocumentRequirement.HEADER_ONLY,
    SPMFamily.GTUP_NIHIL: DocumentRequirement.CONTEXT_DEPENDENT,
    SPMFamily.GAJI: DocumentRequirement.NOMINATIVE_REQUIRED,
    SPMFamily.PENGHASILAN_PPNPN: DocumentRequirement.NOMINATIVE_REQUIRED,
    SPMFamily.TUNJANGAN_KINERJA: DocumentRequirement.NOMINATIVE_REQUIRED,
    SPMFamily.THR: DocumentRequirement.NOMINATIVE_REQUIRED,
    SPMFamily.GAJI_13: DocumentRequirement.NOMINATIVE_REQUIRED,
    SPMFamily.NON_GAJI: DocumentRequirement.CONTEXT_DEPENDENT,
    SPMFamily.NON_GAJI_KONTRAKTUAL: DocumentRequirement.SOURCE_DOCUMENT_REQUIRED,
    SPMFamily.UNKNOWN: DocumentRequirement.UNSUPPORTED_REVIEW,
}


def _normalized_label(value):
    text = str(value or "").upper().replace("_", " ")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return " ".join(text.split())


def normalize_spm_family(value):
    """Kembalikan keluarga stabil tanpa mengubah label asli Jenis SPM."""
    text = _normalized_label(value)
    if not text:
        return SPMFamily.UNKNOWN
    text = re.sub(r"^SPM\s+", "", text)

    # Urutan spesifik harus mendahului label yang merupakan substring-nya.
    if re.search(r"\b(?:GUP|GU)\s+KKP(?:\s+\d+)?$", text):
        return SPMFamily.GUP_KKP
    if re.search(r"\bGUP\s+2\s+PNBP(?:\s+\d+)?$", text) or (
        text.startswith("GUP") and "PNBP" in text
    ):
        return SPMFamily.GUP_PNBP
    if re.fullmatch(r"GUP(?:\s+\d+)?", text):
        return SPMFamily.GUP_REGULAR
    if re.fullmatch(r"GTUP\s+NIHIL(?:\s+\d+)?", text):
        return SPMFamily.GTUP_NIHIL
    if re.fullmatch(r"TUP(?:\s+\d+)?", text):
        return SPMFamily.TUP
    if re.fullmatch(r"UP(?:\s+\d+)?", text):
        return SPMFamily.UP
    if text.startswith("NON GAJI KONTRAKTUAL"):
        return SPMFamily.NON_GAJI_KONTRAKTUAL
    if text.startswith("NON GAJI"):
        return SPMFamily.NON_GAJI
    if "PENGHASILAN PPNPN" in text or re.search(r"\bPPNPN\b", text):
        return SPMFamily.PENGHASILAN_PPNPN
    if "TUNJANGAN KINERJA" in text or re.search(r"\bTUKIN\b", text):
        return SPMFamily.TUNJANGAN_KINERJA
    if re.search(r"\bGAJI\s+(?:KE\s*)?13\b", text):
        return SPMFamily.GAJI_13
    if re.search(r"\bTHR\b", text):
        return SPMFamily.THR
    if re.search(r"\bGAJI\b", text):
        return SPMFamily.GAJI
    return SPMFamily.UNKNOWN


def document_requirement_policy(value):
    family = value if isinstance(value, SPMFamily) else normalize_spm_family(value)
    return POLICY_BY_FAMILY.get(family, DocumentRequirement.UNSUPPORTED_REVIEW)


def is_drpp_required(value):
    return document_requirement_policy(value) == DocumentRequirement.DRPP_REQUIRED


def allows_empty_drpp(value):
    family = value if isinstance(value, SPMFamily) else normalize_spm_family(value)
    return family != SPMFamily.UNKNOWN and not is_drpp_required(family)
