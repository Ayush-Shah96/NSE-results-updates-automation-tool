from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup


class BSEMonitor:
    """Best-effort adapter for BSE company financial-results pages."""

    def __init__(self, bse_codes: tuple[str, ...] = ()):
        self.bse_codes = bse_codes

    def fetch_new(self) -> list[dict]:
        out = []
        for code in self.bse_codes:
            url = f"https://www.bseindia.com/corporates/comp_results.aspx?Code={code}"
            try:
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "html.parser")
                for tr in soup.find_all("tr"):
                    cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                    if len(cells) < 6 or cells[0] == "Financial Year":
                        continue
                    joined = " | ".join(cells)
                    if "Quarter" not in joined:
                        continue
                    year, result_type, status, filing_time = cells[:4]
                    consolidation = "Consolidated" if "Consolidated" in result_type else "Standalone"
                    period_month = next((x for x in ["Mar", "Jun", "Sep", "Dec"] if x in result_type), "")
                    quarter = _quarter_from_month(period_month, year)
                    ext_id = f"BSE|{code}|{result_type}|{filing_time}"
                    out.append({
                        "exchange": "BSE",
                        "external_id": ext_id,
                        "company_name": soup.title.get_text(" ", strip=True) if soup.title else code,
                        "symbol": code,
                        "bse_code": code,
                        "period_end": f"{period_month}-{year}" if period_month else year,
                        "quarter": quarter,
                        "consolidation": consolidation,
                        "published_at": filing_time,
                        "source_url": url,
                        "attachment_url": None,
                        "status": "revised" if "Revised" in status else "new",
                    })
            except requests.RequestException:
                continue
        return out


def _quarter_from_month(month: str, fy: str) -> str:
    q = {"Mar": "Q4", "Jun": "Q1", "Sep": "Q2", "Dec": "Q3"}.get(month, "Q?")
    raw = str(fy).strip()
    year_match = re.search(r"(20\d{2})", raw)
    if not year_match:
        return f"{q} FY{raw[-2:]}"
    year = int(year_match.group(1))
    fy_end = year if month == "Mar" else year + 1
    return f"{q} FY{fy_end % 100:02d}"
