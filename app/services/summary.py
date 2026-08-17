from __future__ import annotations

from decimal import Decimal
from typing import Any


def _fmt(v: Any) -> str:
    if v is None or v == "":
        return "N/A"
    try:
        d = Decimal(str(v))
        if d == d.to_integral():
            return f"{int(d):,}"
        return f"{d:,.2f}"
    except Exception:
        return str(v)


def _fmt_pct(v: Any) -> str:
    if v is None or v == "":
        return "N/A"
    try:
        d = Decimal(str(v))
        return f"{d:+.1f}%"
    except Exception:
        return str(v)


def _get_metric(record: dict, name: str) -> dict | None:
    for metric in record.get("metrics", []):
        if metric.get("name") == name:
            return metric
    return None


def build_summary_message(record: dict) -> str:
    company = record.get("company_name", "Company")
    quarter = record.get("quarter_label", "Latest Quarter")
    exchange = record.get("source_exchange", "NSE")
    consolidation = record.get("consolidation", "Consolidated")
    source = record.get("source_url", "")

    revenue = _get_metric(record, "Revenue") or {}
    ebitda = _get_metric(record, "EBITDA") or {}
    pat = _get_metric(record, "PAT") or {}
    eps = _get_metric(record, "EPS (Basic)") or {}

    lines = [
        f"📊 {company}",
        f"{exchange} • {quarter} • {consolidation}",
        "",
        f"Revenue: ₹{_fmt(revenue.get('current'))} Cr | QoQ {_fmt_pct(revenue.get('qoq_pct'))} | YoY {_fmt_pct(revenue.get('yoy_pct'))}",
        f"EBITDA: ₹{_fmt(ebitda.get('current'))} Cr | QoQ {_fmt_pct(ebitda.get('qoq_pct'))} | YoY {_fmt_pct(ebitda.get('yoy_pct'))}",
        f"PAT: ₹{_fmt(pat.get('current'))} Cr | QoQ {_fmt_pct(pat.get('qoq_pct'))} | YoY {_fmt_pct(pat.get('yoy_pct'))}",
        f"EPS (Basic): {_fmt(eps.get('current'))}",
        "",
        "New quarterly result detected and processed automatically.",
    ]
    if source:
        lines.append(f"Source: {source}")
    return "\n".join(lines)


def build_detection_message(filing: dict) -> str:
    exchange = filing.get("exchange", "Exchange")
    company = filing.get("company_name", filing.get("symbol", "Company"))
    quarter = filing.get("quarter", "Quarterly Result")
    published = filing.get("published_at", "")
    source = filing.get("source_url", "")
    lines = [
        f"🔔 New {exchange} quarterly result detected",
        f"{company}",
        f"{quarter}",
    ]
    if published:
        lines.append(f"Published: {published}")
    if source:
        lines.append(f"Open filing: {source}")
    return "\n".join(lines)
