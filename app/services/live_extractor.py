from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup

from app.services.calculations import pct_change

ALIASES: dict[str, tuple[str, ...]] = {
    "Revenue": ("revenue from operations", "revenue from operation", "revenue"),
    "Expenses": ("total expenses", "total expenditure", "expenditure"),
    "Employee benefits expense": ("employee benefits expense", "employee benefit expense"),
    "Other expenses": ("other expenses",),
    "Depreciation": (
        "depreciation, depletion and amortisation expense",
        "depreciation, depletion and amortization expense",
        "depreciation and amortisation expense",
        "depreciation and amortization expense",
        "depreciation",
    ),
    "Finance Cost": ("finance costs", "finance cost"),
    "Profit Before Exceptional Items": (
        "profit before exceptional items and tax",
        "total profit before exceptional items and tax",
        "profit after interest but before exceptional items",
    ),
    "Exceptional Items": ("exceptional items",),
    "Other Income": ("other income",),
    "Profit Before Tax": ("profit before tax", "total profit before tax"),
    "Total tax expense": ("total tax expense", "total tax expenses", "income tax expense", "tax expense"),
    "PAT": (
        "profit or loss attributable to owners of parent",
        "profit/loss attributable to owners of parent",
        "profit attributable to owners of parent",
        "net profit (loss) for the period",
        "profit for the period",
        "profit (loss) for the period",
        "total profit (loss) for the period",
        "net profit",
    ),
    "EPS (Basic)": ("basic earnings (loss) per share", "basic earnings per share", "basic eps"),
    "EPS (Diluted)": ("diluted earnings (loss) per share", "diluted earnings per share", "diluted eps"),
}


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9%]", "", value.lower())


def _to_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    raw = _clean_text(value).replace(",", "").replace("₹", "")
    if not raw or raw in {"-", "—", "–", "na", "n/a"}:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()")
    raw = re.sub(r"[^0-9.+-]", "", raw)
    if raw in {"", ".", "+", "-"}:
        return None
    try:
        number = Decimal(raw)
        return -number if negative else number
    except InvalidOperation:
        return None


def _unit_factor(text: str) -> tuple[str, Decimal]:
    t = text.lower()
    if "crore" in t or re.search(r"\bcr\b", t):
        return "Rs in Cr", Decimal("1")
    if "lakh" in t or "lakhs" in t:
        return "Rs in Cr", Decimal("0.01")
    if "million" in t:
        return "Rs in Cr", Decimal("0.1")
    if "billion" in t:
        return "Rs in Cr", Decimal("100")
    if "rupee" in t or "actual" in t:
        return "Rs in Cr", Decimal("0.0000001")
    return "Rs in Cr", Decimal("0.01")


def _find_meta(text: str, label: str) -> str:
    pattern = re.compile(rf"{re.escape(label)}\s*[:|]\s*([^\n|]+)", re.I)
    match = pattern.search(text)
    return _clean_text(match.group(1)) if match else ""


def _looks_like_period_header(value: str) -> bool:
    v = value.lower()
    return any(x in v for x in ("quarter ended", "year to date", "previous year", "current quarter", "period"))


def _row_value(cells: list[str]) -> Decimal | None:
    # NSE iXBRL financial tables present the current-period value before the
    # year-to-date value. Select the first numeric cell after the label.
    for cell in cells:
        if _looks_like_period_header(cell):
            continue
        value = _to_decimal(cell)
        if value is not None:
            return value
    return None


def _match_metric(label: str) -> str | None:
    compact = _compact(label)
    for metric, aliases in ALIASES.items():
        for alias in aliases:
            if _compact(alias) in compact:
                return metric
    return None


def parse_ixbrl_html(html: str, period_end: str | None = None) -> tuple[dict, dict]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    company = _find_meta(text, "Name of company") or _find_meta(text, "Name of the company") or "Unknown Company"
    symbol = _find_meta(text, "NSE Symbol")
    consolidation = _find_meta(text, "Nature of report standalone or consolidated") or "Consolidated"
    amount_match = re.search(r"Amount in \(([^)]+)\)", text, flags=re.I)
    unit, factor = _unit_factor(amount_match.group(1) if amount_match else "Lakhs")

    extracted: dict[str, Decimal] = {}
    raw_labels: dict[str, str] = {}

    for tr in soup.find_all("tr"):
        cells = [_clean_text(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        label = _clean_text(" ".join(cells[:-1]))
        metric = _match_metric(label)
        if not metric or metric in extracted:
            continue
        value = _row_value(cells[1:])
        if value is None:
            continue
        extracted[metric] = value if metric.startswith("EPS") else value * factor
        raw_labels[metric] = " | ".join(cells)

    meta = {
        "company_name": company,
        "symbol": symbol,
        "consolidation": consolidation,
        "unit": unit,
        "raw_labels": raw_labels,
    }
    return extracted, meta


def parse_xbrl(xml_bytes: bytes, period_end: str | None = None) -> tuple[dict, dict]:
    root = ET.fromstring(xml_bytes)
    facts: dict[str, Decimal] = {}
    raw_labels: dict[str, str] = {}
    for elem in root.iter():
        text = (elem.text or "").strip()
        if not text:
            continue
        tag = elem.tag.rsplit("}", 1)[-1]
        compact = _compact(tag)
        value = _to_decimal(text)
        if value is None:
            continue
        for metric, aliases in ALIASES.items():
            if metric in facts:
                continue
            if not any(_compact(alias) in compact for alias in aliases):
                continue
            if metric.startswith("EPS"):
                facts[metric] = value
            else:
                # XBRL facts often use ISO currency units and explicit scale.
                scale = elem.attrib.get("scale") or elem.attrib.get("{http://www.xbrl.org/2003/instance}scale")
                if scale:
                    try:
                        value *= Decimal(10) ** int(scale)
                    except Exception:
                        pass
                facts[metric] = value * Decimal("0.01")
            raw_labels[metric] = tag

    return facts, {"company_name": "", "symbol": "", "consolidation": "Consolidated", "unit": "Rs in Cr", "raw_labels": raw_labels}


def _derive(metrics: dict[str, Decimal]) -> dict[str, Decimal]:
    revenue = metrics.get("Revenue")
    pbei = metrics.get("Profit Before Exceptional Items")
    if pbei is None and metrics.get("Profit Before Tax") is not None:
        pbei = metrics["Profit Before Tax"] + (metrics.get("Exceptional Items") or Decimal("0")) 
        pbei -= metrics.get("Finance Cost") or Decimal("0")
        pbei -= metrics.get("Depreciation") or Decimal("0")
        metrics["Profit Before Exceptional Items"] = pbei
    if "EBITDA" not in metrics and pbei is not None:
        metrics["EBITDA"] = pbei + (metrics.get("Finance Cost") or Decimal("0")) + (metrics.get("Depreciation") or Decimal("0"))
    if revenue:
        if metrics.get("EBITDA") is not None:
            metrics["EBITDA Margin %"] = metrics["EBITDA"] / revenue * Decimal("100")
        if metrics.get("PAT") is not None:
            metrics["PAT Margin %"] = metrics["PAT"] / revenue * Decimal("100")
    return metrics


def build_live_record(filing: dict, current_metrics: dict, historical: dict[str, dict]) -> dict:
    current_metrics = _derive(current_metrics)
    prev_q = _derive(dict(historical.get("previous_quarter", {})))
    prev_y = _derive(dict(historical.get("previous_year", {})))
    if current_metrics.get("Revenue"):
        current_metrics["Gross Profit Margin %"] = Decimal("0") if current_metrics.get("Gross Profit") is None else current_metrics["Gross Profit"] / current_metrics["Revenue"] * Decimal("100")
    names = [
        "Revenue", "Expenses", "Gross Profit", "Gross Profit Margin %",
        "Employee benefits expense", "Other expenses", "EBITDA", "EBITDA Margin %",
        "Depreciation", "Finance Cost", "Profit Before Exceptional Items", "Exceptional Items",
        "Other Income", "Profit Before Tax", "Total tax expense", "PAT", "PAT Margin %",
        "EPS (Basic)", "EPS (Diluted)",
    ]
    metrics = []
    for name in names:
        cur = current_metrics.get(name)
        q = prev_q.get(name)
        y = prev_y.get(name)
        metrics.append({
            "name": name,
            "current": str(cur) if cur is not None else None,
            "previous_quarter": str(q) if q is not None else None,
            "previous_year": str(y) if y is not None else None,
            "qoq_pct": str(pct_change(cur, q)) if pct_change(cur, q) is not None else None,
            "yoy_pct": str(pct_change(cur, y)) if pct_change(cur, y) is not None else None,
        })
    return {
        "company_name": filing["company_name"],
        "quarter_label": filing["quarter"],
        "period_end": filing["period_end"],
        "consolidation": filing.get("consolidation", "Consolidated"),
        "unit": current_metrics.pop("_unit", "Rs in Cr"),
        "metrics": metrics,
        "source_exchange": "NSE",
        "source_url": filing.get("detail_url") or filing.get("xbrl_url") or filing.get("source_url", ""),
        "metadata": {
            "source_note": "Live NSE Integrated Filing - Financials; values extracted from the exchange filing/XBRL.",
            "source_filing": filing.get("detail_url") or filing.get("xbrl_url") or filing.get("source_url"),
            "symbol": filing.get("symbol"),
            "published_at": filing.get("published_at"),
            "status": filing.get("status", "new"),
            "raw": filing.get("raw", {}),
        },
    }
