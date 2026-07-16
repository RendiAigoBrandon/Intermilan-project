from __future__ import annotations


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
    if lowered.startswith("bps") and normalize_satker_code(name):
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
