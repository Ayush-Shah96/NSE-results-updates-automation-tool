from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from datetime import datetime, timezone

from app.schemas import Metric, ResultReport
from app.services.calculations import pct_change, bps_change
from app.services.formatter import format_text, render_png, payload_from_report


def _d(v):
    return None if v in (None, "") else Decimal(str(v))


def build_from_record(record: dict, output_dir: str = "generated") -> ResultReport:
    metrics = []
    for item in record["metrics"]:
        current = _d(item.get("current"))
        previous_q = _d(item.get("previous_quarter"))
        previous_y = _d(item.get("previous_year"))
        qoq_pct = _d(item.get("qoq_pct")) if item.get("qoq_pct") is not None else pct_change(current, previous_q)
        yoy_pct = _d(item.get("yoy_pct")) if item.get("yoy_pct") is not None else pct_change(current, previous_y)
        qoq_bps = _d(item.get("qoq_bps")) if item.get("qoq_bps") is not None else None
        yoy_bps = _d(item.get("yoy_bps")) if item.get("yoy_bps") is not None else None
        metrics.append(Metric(
            name=item["name"],
            current=current,
            previous_quarter=previous_q,
            previous_year=previous_y,
            unit=record.get("unit", "Rs in Cr"),
            qoq_pct=qoq_pct,
            yoy_pct=yoy_pct,
            qoq_bps=qoq_bps,
            yoy_bps=yoy_bps,
            current_display=item.get("current_display"),
            previous_q_display=item.get("previous_q_display"),
            previous_y_display=item.get("previous_y_display"),
            qoq_display=item.get("qoq_display"),
            yoy_display=item.get("yoy_display"),
        ))

    report = ResultReport(
        company_name=record["company_name"],
        quarter_label=record["quarter_label"],
        period_end=record["period_end"],
        consolidation=record.get("consolidation", "Consolidated"),
        unit=record.get("unit", "Rs in Cr"),
        metrics=metrics,
        source_exchange=record.get("source_exchange", "NSE"),
        source_url=record.get("source_url", ""),
        extracted_at=datetime.now(timezone.utc).isoformat(),
        metadata=record.get("metadata", {}),
    )
    return report


def persist_report(report: ResultReport, output_dir: str = "generated") -> tuple[str, str]:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    slug = report.company_name.lower().replace(" ", "_").replace("/", "_")[:80]
    png_path = str(Path(output_dir) / f"{slug}_{report.quarter_label.replace(' ', '_')}.png")
    render_png(report, png_path)
    txt = format_text(report)
    payload = payload_from_report(report)
    json_path = str(Path(output_dir) / f"{slug}_{report.quarter_label.replace(' ', '_')}.json")
    Path(json_path).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return png_path, txt
