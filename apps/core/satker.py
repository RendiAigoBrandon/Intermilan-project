"""
Satker utility functions.

Provides helper functions for working with BPS unit codes and official satker codes.

IMPORTANT: 4-digit unit codes (e.g., 1300) are NOT the same as 6-digit official satker codes (e.g., 019937).

Mapping:
    unit_code (4-digit, from filenames) -> satker_code (6-digit, official financial code)
    1300 -> 019937 (BPS Provinsi Sumatera Barat)
    1301 -> 636977 (BPS Kabupaten Kepulauan Mentawai)
    ...
"""
from __future__ import annotations

from dataclasses import dataclass


# Authoritative 4-digit unit_code -> 6-digit official satker_code mapping
# Source: Actual KK source dataset
UNIT_CODE_TO_SATKER_CODE = {
    "1300": "019937",
    "1301": "636977",
    "1302": "427981",
    "1303": "019979",
    "1304": "019983",
    "1305": "019990",
    "1306": "019958",
    "1307": "428041",
    "1308": "428063",
    "1309": "428057",
    "1310": "667193",
    "1311": "667172",
    "1312": "667189",
    "1371": "019941",
    "1372": "019962",
    "1373": "428001",
    "1374": "427990",
    "1375": "428026",
    "1376": "428032",
    "1377": "668512",
}


SATKER_NAME_FALLBACKS = {
    "1300": "BPS Provinsi Sumatera Barat",
    "1301": "BPS Kabupaten Kepulauan Mentawai",
    "1302": "BPS Kabupaten Pesisir Selatan",
    "1303": "BPS Kabupaten Solok",
    "1304": "BPS Kabupaten Sijunjung",
    "1305": "BPS Kabupaten Tanah Datar",
    "1306": "BPS Kabupaten Padang Pariaman",
    "1307": "BPS Kabupaten Agam",
    "1308": "BPS Kabupaten Lima Puluh Kota",
    "1309": "BPS Kabupaten Pasaman",
    "1310": "BPS Kabupaten Solok Selatan",
    "1311": "BPS Kabupaten Dharmasraya",
    "1312": "BPS Kabupaten Pasaman Barat",
    "1371": "BPS Kota Padang",
    "1372": "BPS Kota Solok",
    "1373": "BPS Kota Sawahlunto",
    "1374": "BPS Kota Padang Panjang",
    "1375": "BPS Kota Bukittinggi",
    "1376": "BPS Kota Payakumbuh",
    "1377": "BPS Kota Pariaman",
}


def normalize_satker_code(value):
    code = str(value or "").strip()
    if code.upper().startswith("KK_"):
        code = code[3:]
    if code.lower().startswith("bps"):
        code = code[3:]
    if code.endswith(".0"):
        code = code[:-2]
    return code.strip()


def fallback_satker_name(code):
    return SATKER_NAME_FALLBACKS.get(normalize_satker_code(code), "")


def get_satker_name_map(codes=None):
    from apps.accounts.models import Profile
    from apps.core.models import MonitoringSummary
    from apps.sp2d.models import SP2DRaw

    if codes is None:
        normalized_codes = set(SATKER_NAME_FALLBACKS)
        normalized_codes.update(
            normalize_satker_code(code)
            for code in SP2DRaw.objects.exclude(satker_code="").values_list("satker_code", flat=True)
        )
        normalized_codes.update(
            normalize_satker_code(code)
            for code in Profile.objects.exclude(satker_code="").values_list("satker_code", flat=True)
        )
        normalized_codes.update(
            normalize_satker_code(code)
            for code in MonitoringSummary.objects.exclude(satker_code="").values_list("satker_code", flat=True)
        )
    else:
        normalized_codes = {normalize_satker_code(code) for code in codes if normalize_satker_code(code)}

    names = {
        code: SATKER_NAME_FALLBACKS[code]
        for code in sorted(normalized_codes)
        if code in SATKER_NAME_FALLBACKS
    }

    for item in (
        SP2DRaw.objects.filter(satker_code__in=normalized_codes)
        .exclude(satker_name="")
        .values("satker_code", "satker_name")
        .distinct()
        .order_by("satker_code", "satker_name")
    ):
        code = normalize_satker_code(item["satker_code"])
        name = normalize_satker_name(item["satker_name"])
        if code and code not in SATKER_NAME_FALLBACKS and is_better_satker_name(code, name, names.get(code, "")):
            names[code] = name

    for item in (
        Profile.objects.filter(satker_code__in=normalized_codes)
        .exclude(satker_name="")
        .values("satker_code", "satker_name")
        .distinct()
    ):
        code = normalize_satker_code(item["satker_code"])
        name = normalize_satker_name(item["satker_name"])
        if code and code not in SATKER_NAME_FALLBACKS and is_better_satker_name(code, name, names.get(code, "")):
            names[code] = name

    for item in (
        MonitoringSummary.objects.filter(satker_code__in=normalized_codes)
        .exclude(satker_label="")
        .values("satker_code", "satker_label")
        .distinct()
    ):
        code = normalize_satker_code(item["satker_code"])
        name = normalize_satker_name(item["satker_label"])
        if code and code not in SATKER_NAME_FALLBACKS and is_better_satker_name(code, name, names.get(code, "")):
            names[code] = name

    return names


def normalize_satker_name(value):
    name = str(value or "").strip()
    lowered = name.lower()
    if not name or name == "-":
        return ""
    if lowered in {"admin", "viewer"}:
        return ""
    # Only strip "bps" prefix if the result looks like a numeric code (4-6 digits)
    # This prevents stripping full satker names like "BPS Provinsi Sumatera Barat"
    normalized_code = normalize_satker_code(name)
    if lowered.startswith("bps") and normalized_code and len(normalized_code) <= 6 and normalized_code.isdigit():
        return ""
    if lowered.startswith("operator_") and normalize_satker_code(name.replace("operator_", "")):
        return ""
    return name


def is_better_satker_name(code, candidate, current):
    candidate = (candidate or "").strip()
    current = (current or "").strip()
    if not candidate or candidate == "-":
        return False
    if not current or current == "-":
        return True
    candidate_upper = candidate.upper()
    current_upper = current.upper()
    if code != "1300" and "PROP. SUMATERA BARAT" in current_upper and "PROP. SUMATERA BARAT" not in candidate_upper:
        return True
    if candidate_upper.startswith("BPS ") and not current_upper.startswith("BPS "):
        return True
    return len(candidate) > len(current) and not current_upper.startswith("BPS ")


def simplify_satker_name(value):
    text = str(value or "").upper()
    replacements = {
        "BADAN PUSAT STATISTIK": "BPS",
        "PROP,": "PROVINSI ",
        "PROP.": "PROVINSI",
        "PROP ": "PROVINSI ",
        "KAB.": "KABUPATEN",
        "KOTA ADMINISTRASI": "KOTA",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    keep = []
    for char in text:
        keep.append(char if char.isalnum() else " ")
    return " ".join("".join(keep).split())


def infer_satker_from_name(value):
    candidate = simplify_satker_name(value)
    if not candidate:
        return "", normalize_satker_name(value)

    best_code = ""
    best_name = ""
    for code, official_name in SATKER_NAME_FALLBACKS.items():
        official = simplify_satker_name(official_name)
        if official and (official in candidate or candidate in official):
            return code, official_name
        official_tokens = set(official.split())
        candidate_tokens = set(candidate.split())
        if len(official_tokens & candidate_tokens) >= max(2, min(len(official_tokens), 4)):
            best_code = code
            best_name = official_name

    return best_code, best_name or normalize_satker_name(value)


def get_official_satker_code(unit_code):
    """
    Convert a 4-digit BPS unit_code to the official 6-digit satker_code.

    Args:
        unit_code: 4-digit unit code (e.g., "1300", 1300, "KK_1300.xlsx")

    Returns:
        6-digit official satker_code (e.g., "019937") or None if not found.

    Examples:
        >>> get_official_satker_code("1300")
        '019937'
        >>> get_official_satker_code(1300)
        '019937'
        >>> get_official_satker_code("KK_1300.xlsx")
        '019937'
        >>> get_official_satker_code("9999")
        None
    """
    code = str(unit_code or "").strip()

    # Handle "KK_XXXX.xlsx" format
    if code.upper().startswith("KK_"):
        code = code[3:]
        # Remove file extension if present
        for ext in (".XLSX", ".XLS"):
            if code.upper().endswith(ext):
                code = code[:-len(ext)]
                break

    # Handle "bpsXXXX" format
    if code.lower().startswith("bps"):
        code = code[3:]

    # Handle .0 suffix (from Excel number conversion)
    # Only remove .0 if it's at the very end (not part of .xlsx)
    if code.endswith(".0"):
        code = code[:-2]

    return UNIT_CODE_TO_SATKER_CODE.get(code.strip())


def get_unit_code_from_satker(satker_code):
    """
    Convert a 6-digit official satker_code to the 4-digit unit_code.

    Args:
        satker_code: 6-digit official satker code (e.g., "019937")

    Returns:
        4-digit unit_code (e.g., "1300") or None if not found.

    Examples:
        >>> get_unit_code_from_satker("019937")
        '1300'
        >>> get_unit_code_from_satker("428041")
        '1307'
    """
    for unit_code, official_code in UNIT_CODE_TO_SATKER_CODE.items():
        if official_code == str(satker_code):
            return unit_code
    return None


def is_known_unit_code(unit_code):
    """Check if a unit_code is in the authoritative mapping."""
    code = str(unit_code or "").strip()
    if code.upper().startswith("KK_"):
        code = code[3:]
        # Remove file extension if present
        for ext in (".XLSX", ".XLS"):
            if code.upper().endswith(ext):
                code = code[:-len(ext)]
                break
    if code.endswith(".0"):
        code = code[:-2]
    return code in UNIT_CODE_TO_SATKER_CODE


def is_known_satker_code(satker_code):
    """Check if a satker_code is in the reverse mapping."""
    return get_unit_code_from_satker(satker_code) is not None


@dataclass
class SatkerResolutionResult:
    """Result of satker resolution from SP2D/document data."""

    # Final resolved values
    unit_code: str = ""  # 4-digit unit code (e.g., "1300")
    satker_code: str = ""  # Official 6-digit satker code (e.g., "019937")
    satker_name: str = ""  # Full satker name

    # Resolution status
    resolved: bool = False
    status: str = "UNKNOWN"  # OK, ERROR_CONFLICT, ERROR_MISSING, ERROR_AMBIGUOUS
    error_message: str = ""

    # Metadata about resolution
    had_explicit_code: bool = False
    had_name: bool = False
    had_unit_code: bool = False


def resolve_sp2d_satker(
    satker_code_input: str = None,
    satker_name_input: str = None,
    unit_code_input: str = None,
) -> SatkerResolutionResult:
    """
    Resolve satker information from SP2D/document data.

    This implements a deterministic resolution policy for SP2D uploads:

    1. If explicit 6-digit official satker code exists:
       - Validate against known mapping
       - BLOCK if inconsistent with other evidence

    2. If explicit 6-digit satker code is absent but known unit_code exists:
       - Resolve unit_code → official satker_code

    3. If both are absent but recognized satker name exists:
       - Resolve: name → known unit → official satker_code

    4. If multiple sources exist with conflicts:
       - BLOCK with validation error

    5. If unknown satker:
       - Return error status

    Args:
        satker_code_input: 6-digit official satker code from document (may be blank)
        satker_name_input: Full satker name from document (may be blank)
        unit_code_input: 4-digit unit code from document (may be blank)

    Returns:
        SatkerResolutionResult with resolved values or error status

    Examples:
        # Case 1: explicit official code
        >>> resolve_sp2d_satker(satker_code_input="019937", satker_name_input="BPS Provinsi Sumatera Barat")
        SatkerResolutionResult(resolved=True, status='OK', satker_code='019937', unit_code='1300', ...)

        # Case 2: code missing, name known
        >>> resolve_sp2d_satker(satker_name_input="BPS Provinsi Sumatera Barat")
        SatkerResolutionResult(resolved=True, status='OK', satker_code='019937', unit_code='1300', ...)

        # Case 3: conflicting evidence
        >>> resolve_sp2d_satker(satker_code_input="428041", satker_name_input="BPS Provinsi Sumatera Barat")
        SatkerResolutionResult(resolved=False, status='ERROR_CONFLICT', ...)
    """
    # Normalize inputs
    satker_code = normalize_satker_code(satker_code_input or "")
    satker_name = normalize_satker_name(satker_name_input or "")
    unit_code = normalize_satker_code(unit_code_input or "")

    result = SatkerResolutionResult(
        had_explicit_code=bool(satker_code),
        had_name=bool(satker_name),
        had_unit_code=bool(unit_code),
    )

    # Step 1: Try to infer unit_code from name if not provided
    inferred_unit_code = ""
    inferred_satker_name = ""
    if satker_name:
        inferred_unit_code, inferred_satker_name = infer_satker_from_name(satker_name)
        result.satker_name = inferred_satker_name or satker_name

    # Step 2: Resolve official satker_code
    # Priority: explicit satker_code > inferred from name > unit_code input > inferred from unit_code

    if satker_code:
        # Case A: Explicit 6-digit satker code provided
        # Validate against known mapping if we have other evidence
        if inferred_unit_code:
            # We have name evidence - check consistency
            expected_satker = get_official_satker_code(inferred_unit_code)
            if expected_satker and expected_satker != satker_code:
                # CONFLICT: explicit code doesn't match name evidence
                return SatkerResolutionResult(
                    resolved=False,
                    status="ERROR_CONFLICT",
                    error_message=(
                        f"Konflik evidence: nama '{satker_name}' mengharapkan "
                        f"satker_code '{expected_satker}' tapi dokumen menunjukkan '{satker_code}'. "
                        f"Periksa kembali data sebelum melanjutkan."
                    ),
                    had_explicit_code=True,
                    had_name=bool(satker_name),
                    had_unit_code=bool(unit_code),
                    satker_code=satker_code,
                    satker_name=satker_name,
                )

        # Use the explicit satker_code
        result.satker_code = satker_code
        result.unit_code = get_unit_code_from_satker(satker_code) or ""
        result.resolved = True
        result.status = "OK"

    elif inferred_unit_code:
        # Case B: Name provided, resolved to known unit
        result.unit_code = inferred_unit_code
        result.satker_code = get_official_satker_code(inferred_unit_code) or ""
        result.resolved = bool(result.satker_code)
        result.status = "OK" if result.satker_code else "ERROR_MISSING"
        if not result.satker_code:
            result.error_message = f"Unit code '{inferred_unit_code}' tidak ditemukan di master satker"

    elif unit_code:
        # Case C: Unit code provided directly
        result.unit_code = unit_code
        result.satker_code = get_official_satker_code(unit_code) or ""
        result.resolved = bool(result.satker_code)
        result.status = "OK" if result.satker_code else "ERROR_MISSING"
        if not result.satker_code:
            result.error_message = f"Unit code '{unit_code}' tidak ditemukan di master satker"

    else:
        # Case D: No usable evidence
        result.resolved = False
        result.status = "ERROR_MISSING"
        result.error_message = (
            "Tidak ada informasi satker yang valid. "
            "Sediakan kode satker 6-digit, nama satker yang dikenal, atau kode unit 4-digit."
        )

    return result


def resolve_sp2d_satker_safe(
    satker_code_input: str = None,
    satker_name_input: str = None,
    unit_code_input: str = None,
) -> tuple[str, str, str]:
    """
    Safe wrapper for resolve_sp2d_satker that returns tuple.

    Returns:
        tuple: (unit_code, satker_code, error_message)
        - If resolved: (unit_code, satker_code, "")
        - If error: ("", "", error_message)
    """
    result = resolve_sp2d_satker(
        satker_code_input=satker_code_input,
        satker_name_input=satker_name_input,
        unit_code_input=unit_code_input,
    )

    if result.resolved:
        return result.unit_code, result.satker_code, ""
    else:
        return "", "", result.error_message
