from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, File

from app.db.database import Database
from app.config import settings
from app.services.live_pipeline import LivePipeline

router = APIRouter(prefix="/api")
db = Database()
live_pipeline = LivePipeline(db)


@router.get("/health")
def health():
    return {"status": "ok", "database": "sqlite"}


@router.get("/filings")
def filings(limit: int = 50):
    return db.list_filings(limit)


@router.get("/deliveries")
def deliveries(filing_id: int | None = None, limit: int = 50):
    return db.list_deliveries(filing_id, limit)


@router.get("/reports/{filing_id}")
def report(filing_id: int):
    data = db.get_report(filing_id)
    if not data:
        raise HTTPException(status_code=404, detail="Report not found")
    return data


@router.get("/live/scan")
def live_scan(symbol: str | None = None):
    try:
        if symbol and symbol.strip():
            rows = live_pipeline.nse.fetch_symbol_history(symbol.strip().upper(), max(settings.live_lookback_days, 90))
        else:
            rows = live_pipeline.scan_nse()
        return {"status": "ok", "exchange": "NSE", "count": len(rows), "filings": rows[:50]}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Live NSE scan failed: {exc}")


@router.get("/config")
def app_config():
    return {
        "monitor_mode": settings.monitor_mode,
        "watch_nse_symbols": list(settings.watch_nse_symbols),
        "live_lookback_days": settings.live_lookback_days,
        "auto_scan_live": settings.auto_scan_live,
        "preferred_consolidation": settings.live_preferred_consolidation,
        "source": "NSE Integrated Filing - Financials",
        "whatsapp_enabled": settings.whatsapp_enabled,
        "whatsapp_send_mode": settings.whatsapp_send_mode,
    }


@router.post("/live/process")
def process_live(symbol: str | None = None):
    try:
        return live_pipeline.process_latest_nse(symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Live NSE processing failed: {exc}")


@router.get("/recipients/template")
def recipient_template():
    from fastapi.responses import FileResponse
    return FileResponse("static/whatsapp_recipients_template.csv", media_type="text/csv", filename="whatsapp_recipients_template.csv")


@router.get("/recipients")
def recipients():
    return db.list_recipients(active_only=False)


@router.post("/recipients")
def create_recipient(payload: dict):
    phone = payload.get("phone_number") or payload.get("phone") or payload.get("number")
    if not phone:
        raise HTTPException(status_code=400, detail="Enter a WhatsApp number in international format.")
    try:
        recipient_id = db.upsert_recipient(phone, payload.get("name") or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return db.get_recipient(recipient_id)


@router.post("/recipients/import")
async def import_recipients(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a CSV file.")
    import csv
    import io
    raw = await file.read()
    if len(raw) > 2_000_000:
        raise HTTPException(status_code=400, detail="CSV is too large. Keep it under 2 MB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV needs a header row such as name,phone_number.")
    headers = {h.strip().lower() for h in reader.fieldnames if h}
    phone_key = next((h for h in ("phone_number", "phone", "mobile", "number", "whatsapp") if h in headers), None)
    name_key = next((h for h in ("name", "recipient_name", "contact") if h in headers), None)
    if not phone_key:
        raise HTTPException(status_code=400, detail="CSV must contain a phone_number, phone, mobile, number, or whatsapp column.")
    added = 0
    updated = 0
    errors = []
    for row_no, row in enumerate(reader, start=2):
        try:
            phone = row.get(phone_key)
            name = row.get(name_key, "") if name_key else ""
            before = {r["phone_number"] for r in db.list_recipients(active_only=False)}
            db.upsert_recipient(phone or "", name or "")
            after = {r["phone_number"] for r in db.list_recipients(active_only=False)}
            if phone and "".join(ch for ch in phone if ch.isdigit()) in after - before:
                added += 1
            else:
                updated += 1
        except Exception as exc:
            errors.append({"row": row_no, "error": str(exc)})
    return {"status": "ok", "added_or_updated": added + updated, "new": added, "updated": updated, "errors": errors[:20], "count": len(db.list_recipients(active_only=False))}


@router.delete("/recipients/{recipient_id}")
def delete_recipient(recipient_id: int):
    if not db.delete_recipient(recipient_id):
        raise HTTPException(status_code=404, detail="Recipient not found.")
    return {"status": "deleted", "id": recipient_id}


@router.get("/alerts")
def alerts(active_only: bool = False):
    return db.list_alerts(active_only=active_only)


@router.post("/alerts")
def create_alert(payload: dict, background_tasks: BackgroundTasks):
    exchange = payload.get("exchange")
    symbol = (payload.get("symbol") or "").strip().upper()
    bse_code = (payload.get("bse_code") or "").strip()
    if exchange not in {"NSE", "BSE"}:
        raise HTTPException(status_code=400, detail="Choose NSE or BSE.")
    if exchange == "NSE" and not symbol:
        raise HTTPException(status_code=400, detail="Enter an NSE symbol such as TCS or RELIANCE.")
    if exchange == "BSE" and not bse_code:
        raise HTTPException(status_code=400, detail="Enter a BSE scrip code.")
    payload["symbol"] = symbol or None
    payload["bse_code"] = bse_code or None
    if not (payload.get("name") or "").strip():
        payload["name"] = f"{exchange} quarterly results"
    recipient_ids = [int(x) for x in (payload.get("recipient_ids") or [])]
    if not recipient_ids:
        raise HTTPException(status_code=400, detail="Select at least one WhatsApp recipient for this alert.")
    known_ids = {r["id"] for r in db.list_recipients(active_only=True)}
    unknown = [rid for rid in recipient_ids if rid not in known_ids]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown recipient IDs: {unknown}")
    payload["recipient_ids"] = recipient_ids
    alert_id = db.create_alert(payload)
    background_tasks.add_task(live_pipeline.bootstrap_alert, alert_id)
    return db.get_alert(alert_id)


@router.patch("/alerts/{alert_id}")
def update_alert(alert_id: int, payload: dict):
    if "recipient_ids" in payload:
        recipient_ids = [int(x) for x in (payload.get("recipient_ids") or [])]
        known_ids = {r["id"] for r in db.list_recipients(active_only=True)}
        unknown = [rid for rid in recipient_ids if rid not in known_ids]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown recipient IDs: {unknown}")
        if not recipient_ids:
            raise HTTPException(status_code=400, detail="Select at least one WhatsApp recipient for this alert.")
    if not db.update_alert(alert_id, payload):
        raise HTTPException(status_code=404, detail="Alert not found or no changes supplied.")
    return db.get_alert(alert_id)


@router.delete("/alerts/{alert_id}")
def delete_alert(alert_id: int):
    if not db.delete_alert(alert_id):
        raise HTTPException(status_code=404, detail="Alert not found.")
    return {"status": "deleted", "id": alert_id}


@router.get("/monitor/status")
def monitor_status():
    active = db.list_alerts(active_only=True)
    events = db.list_monitor_events(20)
    return {
        "enabled": settings.monitor_mode == "live" and settings.alert_poll_enabled,
        "interval_seconds": settings.monitor_interval_seconds,
        "active_alerts": len(active),
        "recent_events": events,
    }


@router.post("/monitor/scan")
def monitor_scan():
    try:
        return live_pipeline.scan_alerts()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Alert scan failed: {exc}")
