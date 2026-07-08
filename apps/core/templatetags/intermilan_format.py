from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


@register.filter
def id_number(value):
    if value in (None, ""):
        return "-"
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return value
    return f"{number:,.0f}".replace(",", ".")


@register.filter
def month_id(value):
    names = {
        1: "Januari",
        2: "Februari",
        3: "Maret",
        4: "April",
        5: "Mei",
        6: "Juni",
        7: "Juli",
        8: "Agustus",
        9: "September",
        10: "Oktober",
        11: "November",
        12: "Desember",
    }
    try:
        return names.get(int(value), "-")
    except (TypeError, ValueError):
        return "-"


@register.filter
def dash(value):
    return value if value not in (None, "") else "-"
