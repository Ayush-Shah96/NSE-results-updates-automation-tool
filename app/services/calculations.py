from __future__ import annotations

from decimal import Decimal, InvalidOperation


def pct_change(current: Decimal | None, previous: Decimal | None) -> Decimal | None:
    if current is None or previous in (None, Decimal("0")):
        return None
    return (current - previous) / previous * Decimal("100")


def bps_change(current_margin: Decimal | None, previous_margin: Decimal | None) -> Decimal | None:
    if current_margin is None or previous_margin is None:
        return None
    # Margin inputs are percentages, e.g. 86 and 71. Difference of 15 percentage points = 1500 bps.
    return (current_margin - previous_margin) * Decimal("100")


def fmt_num(value: Decimal | None) -> str:
    if value is None:
        return "-"
    q = value.quantize(Decimal("0.1")) if value % 1 else value.quantize(Decimal("1"))
    return f"{q:,}".replace(",", ",")


def fmt_pct(value: Decimal | None, dash: str = "-") -> str:
    if value is None:
        return dash
    rounded = value.quantize(Decimal("1"))
    sign = "+" if rounded > 0 else ""
    return f"{sign}{rounded}%"


def fmt_bps(value: Decimal | None) -> str:
    if value is None:
        return "-"
    rounded = value.quantize(Decimal("1"))
    sign = "+" if rounded > 0 else ""
    return f"{sign}{rounded} bps"
