from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


MONTH_MAP = {
    "1": 1,
    "01": 1,
    "januari": 1,
    "jan": 1,
    "2": 2,
    "02": 2,
    "februari": 2,
    "feb": 2,
    "3": 3,
    "03": 3,
    "maret": 3,
    "mar": 3,
    "4": 4,
    "04": 4,
    "april": 4,
    "apr": 4,
    "5": 5,
    "05": 5,
    "mei": 5,
    "may": 5,
    "6": 6,
    "06": 6,
    "juni": 6,
    "jun": 6,
    "7": 7,
    "07": 7,
    "juli": 7,
    "jul": 7,
    "8": 8,
    "08": 8,
    "agustus": 8,
    "agu": 8,
    "aug": 8,
    "9": 9,
    "09": 9,
    "september": 9,
    "sep": 9,
    "10": 10,
    "oktober": 10,
    "okt": 10,
    "oct": 10,
    "11": 11,
    "november": 11,
    "nov": 11,
    "12": 12,
    "desember": 12,
    "des": 12,
    "dec": 12,
}


@dataclass
class ImportStats:
    source: str
    read: int = 0
    success: int = 0
    skipped: int = 0
    duplicates: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        self.failed += 1
        self.errors.append(message)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def normalize_header(value: Any) -> str:
    text = clean_text(value).lower()
    for old, new in {
        "\n": " ",
        "\r": " ",
        ".": "",
        "/": " ",
        "-": " ",
        "_": " ",
        "(": " ",
        ")": " ",
    }.items():
        text = text.replace(old, new)
    return " ".join(text.split())


def parse_decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = clean_text(value)
    if not text:
        return Decimal("0")
    text = text.replace("Rp", "").replace("rp", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return Decimal("0")


def parse_month(value: Any) -> int | None:
    text = clean_text(value).lower()
    if not text:
        return None
    return MONTH_MAP.get(text)


def parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = clean_text(value)
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def pick(row: dict[str, Any], *names: str, default: Any = "") -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return default


def dict_from_headers(headers: list[Any], values: tuple[Any, ...]) -> dict[str, Any]:
    result = {}
    for index, header in enumerate(headers):
        key = normalize_header(header)
        if key:
            result[key] = values[index] if index < len(values) else None
    return result
