from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class Filing:
    exchange: str
    external_id: str
    company_name: str
    symbol: str
    bse_code: str | None
    period_end: str
    quarter: str
    consolidation: str
    published_at: str
    source_url: str
    attachment_url: str | None = None
    status: str = "new"


@dataclass
class Metric:
    name: str
    current: Decimal | None
    previous_quarter: Decimal | None
    previous_year: Decimal | None
    unit: str = "Rs in Cr"
    qoq_pct: Decimal | None = None
    yoy_pct: Decimal | None = None
    qoq_bps: Decimal | None = None
    yoy_bps: Decimal | None = None
    current_display: str | None = None
    previous_q_display: str | None = None
    previous_y_display: str | None = None
    qoq_display: str | None = None
    yoy_display: str | None = None


@dataclass
class ResultReport:
    company_name: str
    quarter_label: str
    period_end: str
    consolidation: str
    unit: str
    metrics: list[Metric] = field(default_factory=list)
    source_exchange: str = "NSE"
    source_url: str = ""
    extracted_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
