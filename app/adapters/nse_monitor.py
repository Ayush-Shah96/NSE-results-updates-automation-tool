from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin
import re

import requests


BASE = "https://www.nseindia.com"
INTEGRATED_FILING_API = f"{BASE}/api/integrated-filing-results"
LEGACY_RESULTS_API = f"{BASE}/api/corporates-financial-results"
INTEGRATED_FILING_PAGE = f"{BASE}/companies-listing/corporate-integrated-filing"
EQUITY_PAGE = f"{BASE}/get-quotes/equity?symbol=LT"


class NSEMonitor:
    """Fetch current NSE Integrated Filing - Financials records.

    NSE's current corporate UI exposes Integrated Filing - Financials, including
    company/symbol, quarter-end date, submission type, standalone/consolidated,
    detail and XBRL links. The monitor uses that current endpoint first and keeps
    the older financial-results endpoint only as a compatibility fallback.
    """

    def __init__(self, symbols: tuple[str, ...] = (), lookback_days: int = 45):
        self.symbols = {s.strip().upper() for s in symbols if s.strip()}
        self.lookback_days = max(1, lookback_days)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Host": "www.nseindia.com",
                "Referer": EQUITY_PAGE,
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/151.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Connection": "keep-alive",
            }
        )

    def _prime(self) -> None:
        # The current NSE live-data clients establish cookies from an equity page
        # before using the JSON API. This is more reliable than priming the old
        # corporate-results page.
        r = self.session.get(EQUITY_PAGE, timeout=30)
        r.raise_for_status()

    def _get_json(self, url: str, params: dict[str, Any]) -> Any:
        response = self.session.get(url, params=params, timeout=45)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _rows(payload: Any) -> list[dict]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("data", "results", "result", "records", "filings"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                for nested_key in ("data", "records", "results"):
                    nested = value.get(nested_key)
                    if isinstance(nested, list):
                        return [x for x in nested if isinstance(x, dict)]
        return []

    def _fetch_integrated(self, symbol: str | None = None, days: int | None = None, size: int = 100) -> list[dict]:
        lookback = max(1, days or self.lookback_days)
        today = date.today()
        from_date = today - timedelta(days=lookback)
        params = {
            "index": "equities",
            "period_ended": "all",
            "from_date": from_date.strftime("%d-%m-%Y"),
            "to_date": today.strftime("%d-%m-%Y"),
            "filing_type": "Integrated Filing- Financials",
            "page": 1,
            "size": min(max(size, 20), 100),
        }
        if symbol:
            params["symbol"] = symbol.upper()

        rows: list[dict] = []
        # Pull up to 5 pages for broad scans. Symbol-specific history generally
        # fits on the first page; this also protects the monitor during earnings season.
        for page in range(1, 6):
            params["page"] = page
            payload = self._get_json(INTEGRATED_FILING_API, params)
            batch = self._rows(payload)
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < params["size"]:
                break

        return rows

    def _fetch_legacy(self) -> list[dict]:
        today = date.today()
        params = {
            "index": "equities",
            "period": "Quarterly",
        }
        payload = self._get_json(LEGACY_RESULTS_API, params)
        rows = self._rows(payload)
        # The legacy endpoint can be stale, so only use it when it contains a
        # genuinely recent record inside our lookback window.
        cutoff = today - timedelta(days=self.lookback_days)
        recent = []
        for row in rows:
            dt = _parse_datetime(row.get("broadCastDate") or row.get("filingDate"))
            if dt and dt.date() >= cutoff:
                recent.append(row)
        return recent

    def fetch_new(self) -> list[dict]:
        self._prime()
        try:
            rows = self._fetch_integrated()
        except requests.RequestException as integrated_error:
            try:
                rows = self._fetch_legacy()
            except requests.RequestException as legacy_error:
                raise RuntimeError(
                    f"NSE Integrated Filing API failed: {integrated_error}; "
                    f"legacy endpoint also failed: {legacy_error}"
                ) from legacy_error

        records = self._normalize_rows(rows)
        if not records:
            # If the broad integrated feed returns nothing because of a temporary
            # cache/filter issue, retry each configured symbol directly.
            for symbol in sorted(self.symbols):
                try:
                    direct_rows = self._fetch_integrated(symbol=symbol, days=max(self.lookback_days, 90), size=50)
                    records.extend(self._normalize_rows(direct_rows))
                except requests.RequestException:
                    continue

        if self.symbols:
            records = [x for x in records if x["symbol"] in self.symbols]
        records = _dedupe_records(records)
        records.sort(key=lambda x: _date_key(x.get("published_at")), reverse=True)
        return records

    def fetch_symbol_history(self, symbol: str, days: int = 450) -> list[dict]:
        self._prime()
        rows = self._fetch_integrated(symbol=symbol.upper(), days=days, size=100)
        records = [x for x in self._normalize_rows(rows) if x["symbol"] == symbol.upper()]
        records = _dedupe_records(records)
        records.sort(key=lambda x: _date_key(x.get("published_at")), reverse=True)
        return records

    def _normalize_rows(self, rows: list[dict]) -> list[dict]:
        out = []
        for row in rows:
            symbol = _clean_symbol(
                row.get("symbol") or row.get("nseSymbol") or row.get("NSE Symbol")
            )
            if not symbol:
                continue
            period_end = _first(
                row,
                "quarterEndDate",
                "periodEndDate",
                "periodEnded",
                "toDate",
                "period_end",
            )
            quarter = _quarter_from_period(str(period_end or ""))
            if quarter == "Unknown":
                # Integrated filings are financial filings even when the API does
                # not provide a friendly quarter label; keep the date record.
                quarter = str(_first(row, "reportingQuarter", "quarter") or "Unknown")
            company = _first(row, "companyName", "company_name", "issuer", "name") or symbol
            consolidation = _normalise_consolidation(
                _first(row, "consolidated", "consolidation", "natureOfReport", "standaloneConsolidated")
            )
            broadcast = _first(
                row,
                "broadcastDateTime",
                "broadcastDate",
                "broadCastDate",
                "filingDate",
                "exchangeDisseminationDateTime",
            ) or ""
            revised = _first(row, "revisedDateTime", "revisedDate", "revisionDateTime") or ""
            details = _absolute_link(_first(row, "details", "detail", "detailUrl", "resultDetailedDataLink"))
            xbrl = _absolute_link(_first(row, "xbrl", "xbrlUrl", "xbrlFileLink"))
            source_url = details or xbrl or INTEGRATED_FILING_PAGE
            submission = _first(row, "typeOfSubmission", "submissionType", "format") or "Original"
            status = "revised" if str(submission).lower().startswith("revis") or revised else "new"
            external_id = "|".join(
                [
                    "NSE",
                    symbol,
                    str(period_end or ""),
                    consolidation,
                    str(broadcast),
                    str(revised),
                ]
            )
            out.append(
                {
                    "exchange": "NSE",
                    "external_id": external_id,
                    "company_name": str(company),
                    "symbol": symbol,
                    "bse_code": _first(row, "scripCode", "bseCode", "scrip_code"),
                    "period_end": str(period_end or ""),
                    "quarter": quarter,
                    "consolidation": consolidation,
                    "published_at": str(broadcast),
                    "source_url": source_url,
                    "attachment_url": details or xbrl,
                    "status": status,
                    "xbrl_url": xbrl,
                    "detail_url": details,
                    "raw": row,
                }
            )
        return out

    def fetch_comparison(self, symbol: str) -> dict:
        self._prime()
        # Kept for backward compatibility with older pipeline code. The current
        # history flow uses Integrated Filing - Financials instead.
        return self._get_json(
            f"{BASE}/api/results-comparision",
            {"symbol": symbol.upper()},
        )


def _first(row: dict, *keys: str):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _clean_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalise_consolidation(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if "consol" in raw:
        return "Consolidated"
    if "stand" in raw or "non-consol" in raw:
        return "Standalone"
    return "Unknown"


def _absolute_link(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text or text in {"-", "#"}:
        return None
    return urljoin(BASE, text)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    formats = (
        "%d-%b-%Y %H:%M:%S",
        "%d-%b-%Y %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%b-%Y",
        "%d-%m-%Y",
        "%Y-%m-%dT%H:%M:%S",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _date_key(value: str):
    return _parse_datetime(value) or datetime.min


def _quarter_from_period(period: str) -> str:
    match = re.search(r"(\d{1,2})[-/]([A-Za-z]{3})[-/](\d{4})", period or "")
    if not match:
        return "Unknown"
    month = datetime.strptime(match.group(2)[:3].title(), "%b").month
    year = int(match.group(3))
    q = {3: "Q4", 6: "Q1", 9: "Q2", 12: "Q3"}.get(month)
    if not q:
        return "Unknown"
    fiscal = year if month == 3 else year + 1
    return f"{q} FY{str(fiscal)[-2:]}"


def _dedupe_records(records: list[dict]) -> list[dict]:
    latest: dict[tuple, dict] = {}
    for record in records:
        key = (
            record.get("symbol"),
            record.get("period_end"),
            record.get("consolidation"),
        )
        current = latest.get(key)
        if current is None or _date_key(record.get("published_at")) > _date_key(current.get("published_at")):
            latest[key] = record
    return list(latest.values())
