from __future__ import annotations

from datetime import datetime
import requests

from app.adapters.bse_monitor import BSEMonitor
from app.adapters.nse_monitor import NSEMonitor
from app.config import settings
from app.db.database import Database
from app.services.live_extractor import build_live_record, parse_ixbrl_html, parse_xbrl
from app.services.report_builder import build_from_record, persist_report
from app.services.summary import build_detection_message
from app.services.whatsapp import WhatsAppClient


class LivePipeline:
    def __init__(self, db: Database):
        self.db = db
        self.wa = WhatsAppClient()
        self.nse = NSEMonitor(settings.watch_nse_symbols, settings.live_lookback_days)
        self.bse = BSEMonitor(settings.watch_bse_codes)

    def scan_nse(self) -> list[dict]:
        return self.nse.fetch_new()

    def scan_alerts(self) -> dict:
        """Run one alert cycle using the active user-created alerts."""
        alerts = self.db.list_alerts(active_only=True)
        events: list[dict] = []
        errors: list[str] = []
        if not alerts:
            return {"status": "idle", "active_alerts": 0, "events": [], "errors": []}

        nse_symbols = sorted({a["symbol"].strip().upper() for a in alerts if a["exchange"] in ("NSE", "BOTH") and a.get("symbol")})
        bse_codes = sorted({a["bse_code"].strip() for a in alerts if a["exchange"] in ("BSE", "BOTH") and a.get("bse_code")})

        for symbol in nse_symbols:
            try:
                filings = self.nse.fetch_symbol_history(symbol, max(settings.live_lookback_days, 90))
                events.extend(self._handle_new_nse_filings(filings, alerts))
            except Exception as exc:
                errors.append(f"NSE {symbol}: {exc}")

        if bse_codes:
            try:
                self.bse.bse_codes = tuple(bse_codes)
                bse_filings = self.bse.fetch_new()
                events.extend(self._handle_new_bse_filings(bse_filings, alerts))
            except Exception as exc:
                errors.append(f"BSE: {exc}")

        return {"status": "ok", "active_alerts": len(alerts), "events": events, "errors": errors}

    def bootstrap_alert(self, alert_id: int) -> dict:
        """Send a welcome message and the latest available result immediately after alert creation."""
        alert = self.db.get_alert(alert_id)
        if not alert:
            return {"status": "not_found", "alert_id": alert_id}

        plan = _delivery_plan([alert])
        welcome_results: list[dict] = []
        for recipient, opts in (plan.get("plan") or {}).items():
            if not opts.get("send_text"):
                continue
            welcome = (
                f"✅ Quarterly Results Alert activated\n\n"
                f"Company: {alert.get('symbol') or alert.get('bse_code') or alert.get('company_keyword') or 'Tracked company'}\n"
                f"Exchange: {alert.get('exchange', 'NSE')}\n\n"
                "I will monitor new quarterly filings and send you the result summary "
                "and report image on WhatsApp as soon as a new filing is detected."
            )
            try:
                data = self.wa.send_text(recipient, welcome)
                mid = ((data.get("messages") or [{}])[0]).get("id")
                self.db.log_monitor_event(alert.get("exchange", "NSE"), "alert_created", f"Welcome sent to {recipient} for alert #{alert_id}")
                welcome_results.append({"recipient": recipient, "status": "sent", "message_id": mid})
            except Exception as exc:
                self.db.log_monitor_event(alert.get("exchange", "NSE"), "welcome_failed", f"Welcome to {recipient} failed: {exc}")
                welcome_results.append({"recipient": recipient, "status": "failed", "error": str(exc)})

        try:
            exchange = alert.get("exchange", "NSE")
            if exchange == "NSE" and alert.get("symbol"):
                symbol = alert["symbol"].strip().upper()
                filings = self.nse.fetch_symbol_history(symbol, settings.live_history_lookback_days)
                if not filings:
                    return {"status": "welcome_sent_no_result", "alert_id": alert_id, "welcome": welcome_results, "message": f"No NSE quarterly filing was found yet for {symbol}."}
                current = self._choose_current(filings)
                processed = self.process_nse_filing(
                    current,
                    all_filings=filings,
                    delivery_options=plan,
                    force_delivery=True,
                )
                return {"status": "bootstrapped", "alert_id": alert_id, "welcome": welcome_results, "result": processed}

            if exchange == "BSE" and alert.get("bse_code"):
                self.bse.bse_codes = (str(alert["bse_code"]).strip(),)
                filings = self.bse.fetch_new()
                matches = _matching_alerts(filings, [alert])
                if matches:
                    latest = sorted(filings, key=lambda x: _date_key(x.get("published_at")), reverse=True)[0]
                    filing_id = self.db.insert_filing(latest)
                    if filing_id is None:
                        existing = next((row for row in self.db.list_filings(500) if row.get("external_id") == latest.get("external_id") and row.get("exchange") == "BSE"), None)
                        filing_id = int(existing["id"]) if existing else None
                    message = build_detection_message(latest) + "\n\nThe BSE filing was detected. Open the official source link for the attached result."
                    delivery = self._send_alert_text(plan, message)
                    return {"status": "bootstrapped_bse", "alert_id": alert_id, "welcome": welcome_results, "filing_id": filing_id, "delivery": delivery}

            return {"status": "welcome_sent_no_result", "alert_id": alert_id, "welcome": welcome_results, "message": "Alert created, but a current result could not be fetched yet."}
        except Exception as exc:
            self.db.log_monitor_event(alert.get("exchange", "NSE"), "bootstrap_failed", f"Alert #{alert_id}: {exc}")
            return {"status": "welcome_sent_result_failed", "alert_id": alert_id, "welcome": welcome_results, "error": str(exc)}

    def _handle_new_nse_filings(self, filings: list[dict], alerts: list[dict]) -> list[dict]:
        results = []
        for filing in sorted(filings, key=lambda x: _date_key(x.get("published_at")), reverse=True):
            matches = _matching_alerts(filing, alerts)
            if not matches or not _new_since_alert(filing, matches):
                continue
            if self.db.is_filing_seen(filing["external_id"], "NSE"):
                continue
            try:
                result = self.process_nse_filing(filing, delivery_options=_delivery_plan(matches))
                results.append(result)
                self.db.log_monitor_event("NSE", "result_processed", f"{filing['symbol']} {filing['quarter']} processed", filing["external_id"])
            except Exception as exc:
                message = build_detection_message(filing) + f"\n\nProcessing error: {exc}"
                delivery = self._send_alert_text(_delivery_plan(matches), message)
                self.db.log_monitor_event("NSE", "processing_error", str(exc), filing["external_id"])
                results.append({"status": "processing_error", "filing": filing, "delivery": delivery, "error": str(exc)})
        return results

    def _handle_new_bse_filings(self, filings: list[dict], alerts: list[dict]) -> list[dict]:
        results = []
        for filing in sorted(filings, key=lambda x: _date_key(x.get("published_at")), reverse=True):
            matches = _matching_alerts(filing, alerts)
            if not matches or not _new_since_alert(filing, matches):
                continue
            if self.db.is_filing_seen(filing["external_id"], "BSE"):
                continue
            # BSE's company-result page is a filing monitor in this build. When a
            # structured attachment is not exposed, we still alert immediately with
            # the official source link rather than inventing financial figures.
            filing_id = self.db.insert_filing(filing)
            if filing_id is None:
                continue
            message = build_detection_message(filing) + "\n\nThe BSE filing has been detected. Open the exchange source for the attached result." 
            delivery = self._send_alert_text(_delivery_plan(matches), message)
            self.db.log_monitor_event("BSE", "result_detected", f"{filing['company_name']} {filing['quarter']} detected", filing["external_id"])
            results.append({"status": "detected", "source": "BSE", "filing_id": filing_id, "filing": filing, "delivery": delivery})
        return results

    def process_latest_nse(self, symbol: str | None = None) -> dict:
        symbol = (symbol or (settings.watch_nse_symbols[0] if settings.watch_nse_symbols else "")).strip().upper()
        filings = self.nse.fetch_symbol_history(symbol, settings.live_history_lookback_days) if symbol else self.nse.fetch_new()
        if not filings:
            return {"status": "not_found", "message": f"No recent NSE Integrated Filing - Financials record found for {symbol}."}
        current = self._choose_current(filings)
        return self.process_nse_filing(current, all_filings=filings)

    def process_nse_filing(self, filing: dict, all_filings: list[dict] | None = None, delivery_options: dict | None = None, force_delivery: bool = False) -> dict:
        symbol = filing.get("symbol", "").strip().upper()
        filings = all_filings or self.nse.fetch_symbol_history(symbol, settings.live_history_lookback_days)
        current_metrics, meta = self._extract_filing(filing)
        history = self._build_history(filing, filings)
        record = build_live_record(filing, current_metrics, history)
        record["company_name"] = meta.get("company_name") or filing["company_name"]
        if meta.get("consolidation") and meta["consolidation"] != "Unknown":
            record["consolidation"] = meta["consolidation"]
        return self._process_record(record, delivery_options=delivery_options, force_delivery=force_delivery)

    @staticmethod
    def _choose_current(filings: list[dict]) -> dict:
        preferred = [f for f in filings if (f.get("consolidation") or "").lower() == settings.live_preferred_consolidation.lower()]
        pool = preferred or filings
        return sorted(pool, key=lambda x: (_date_key(x.get("published_at")), x.get("status") == "revised"), reverse=True)[0]

    def _extract_filing(self, filing: dict) -> tuple[dict, dict]:
        url = filing.get("detail_url") or filing.get("xbrl_url")
        if not url:
            raise RuntimeError("NSE filing does not expose a Details or XBRL link.")
        response = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.nseindia.com/companies-listing/corporate-integrated-filing",
        }, timeout=60)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if "xml" in content_type or url.lower().endswith(".xml"):
            metrics, meta = parse_xbrl(response.content, filing.get("period_end"))
        else:
            metrics, meta = parse_ixbrl_html(response.text, filing.get("period_end"))
            if not metrics:
                try:
                    metrics, meta = parse_xbrl(response.content, filing.get("period_end"))
                except Exception:
                    pass
        if not metrics:
            raise RuntimeError(f"Unable to extract financial metrics from NSE filing: {url}")
        return metrics, meta

    def _build_history(self, current_filing: dict, candidate_filings: list[dict]) -> dict[str, dict]:
        history = {"previous_quarter": {}, "previous_year": {}}
        for filing in candidate_filings:
            if filing.get("period_end") == current_filing.get("period_end"):
                continue
            if filing.get("consolidation") != current_filing.get("consolidation"):
                continue
            role = None
            if _is_previous_quarter(filing.get("period_end", ""), current_filing.get("period_end", "")):
                role = "previous_quarter"
            elif _is_previous_year(filing.get("period_end", ""), current_filing.get("period_end", "")):
                role = "previous_year"
            if not role:
                continue
            try:
                values, _ = self._extract_filing(filing)
                for key, value in values.items():
                    history[role].setdefault(key, value)
            except Exception:
                continue
        return history

    def _process_record(self, record: dict, delivery_options: dict | None = None, force_delivery: bool = False) -> dict:
        filing = {
            "exchange": record.get("source_exchange", "NSE"),
            "external_id": f"live|{record['source_exchange']}|{record['company_name']}|{record['period_end']}|{record.get('consolidation','')}",
            "company_name": record["company_name"],
            "symbol": record.get("metadata", {}).get("symbol", ""),
            "bse_code": None,
            "period_end": record["period_end"],
            "quarter": record["quarter_label"],
            "consolidation": record.get("consolidation", "Consolidated"),
            "published_at": record.get("metadata", {}).get("published_at", ""),
            "source_url": record.get("source_url", ""),
            "attachment_url": record.get("metadata", {}).get("source_filing"),
            "status": "revised" if record.get("metadata", {}).get("status") == "revised" else "new",
        }
        filing_id = self.db.insert_filing(filing)
        if filing_id is None:
            if not force_delivery:
                return {"status": "duplicate", "company": record["company_name"], "source": "NSE", "quarter": record["quarter_label"]}
            # The filing already exists, but this can still be the first result for a brand-new alert.
            # Reuse the existing row and refresh its calculated report/delivery for this alert bootstrap.
            existing = next((row for row in self.db.list_filings(500)
                             if row.get("exchange") == filing["exchange"]
                             and row.get("external_id") == filing["external_id"]), None)
            if not existing:
                return {"status": "duplicate", "company": record["company_name"], "source": "NSE", "quarter": record["quarter_label"]}
            filing_id = int(existing["id"])

        self.db.add_metrics(filing_id, [
            {"metric_name": m["name"], "value": m.get("current"), "unit": record.get("unit", "Rs in Cr"), "confidence": 0.95}
            for m in record["metrics"]
        ])
        report = build_from_record(record)
        image_path, text_message = persist_report(report)
        self.db.save_report(filing_id, record, text_message, image_path)
        delivery = self._deliver(filing_id, image_path, record, delivery_options=delivery_options)
        return {
            "status": "processed",
            "source": "NSE",
            "filing_id": filing_id,
            "company": record["company_name"],
            "symbol": record.get("metadata", {}).get("symbol", ""),
            "quarter": record["quarter_label"],
            "image_path": image_path,
            "delivery": delivery,
            "source_url": record["source_url"],
        }

    def _deliver(self, filing_id: int, image_path: str, record: dict, delivery_options: dict | None = None) -> list[dict]:
        if not settings.whatsapp_enabled:
            return []
        mode = settings.whatsapp_send_mode.lower()
        send_text = delivery_options.get("send_text") if delivery_options else (mode in {"text", "image", "both"})
        send_image = delivery_options.get("send_image") if delivery_options else (mode in {"image", "both"})
        if mode == "template" and not delivery_options:
            send_text = False
            send_image = False
        from app.services.summary import build_summary_message
        summary = build_summary_message(record)
        results: list[dict] = []
        plan = delivery_options.get("plan") if delivery_options else None
        recipients = list(plan.keys()) if plan else list(settings.whatsapp_recipients)
        for recipient in recipients:
            recipient_opts = plan.get(recipient, {}) if plan else {}
            recipient_send_text = recipient_opts.get("send_text", send_text)
            recipient_send_image = recipient_opts.get("send_image", send_image)
            if mode == "template" and not delivery_options:
                try:
                    data = self.wa.send_template(recipient, settings.whatsapp_template_name, settings.whatsapp_template_language, [record["company_name"], record["quarter_label"]])
                    message_id = ((data.get("messages") or [{}])[0]).get("id")
                    self.db.log_delivery(filing_id, recipient, "template", "sent", message_id)
                    results.append({"recipient": recipient, "mode": "template", "status": "sent", "message_id": message_id})
                except Exception as exc:
                    self.db.log_delivery(filing_id, recipient, "template", "failed", error=str(exc))
                    results.append({"recipient": recipient, "mode": "template", "status": "failed", "error": str(exc)})
                continue
            if recipient_send_text:
                try:
                    data = self.wa.send_text(recipient, summary)
                    message_id = ((data.get("messages") or [{}])[0]).get("id")
                    self.db.log_delivery(filing_id, recipient, "text", "sent", message_id)
                    results.append({"recipient": recipient, "mode": "text", "status": "sent", "message_id": message_id})
                except Exception as exc:
                    self.db.log_delivery(filing_id, recipient, "text", "failed", error=str(exc))
                    results.append({"recipient": recipient, "mode": "text", "status": "failed", "error": str(exc)})
            if recipient_send_image:
                try:
                    data = self.wa.send_image(recipient, image_path, caption=f"{record['company_name']} – {record['quarter_label']}")
                    message_id = ((data.get("messages") or [{}])[0]).get("id")
                    self.db.log_delivery(filing_id, recipient, "image", "sent", message_id)
                    results.append({"recipient": recipient, "mode": "image", "status": "sent", "message_id": message_id})
                except Exception as exc:
                    self.db.log_delivery(filing_id, recipient, "image", "failed", error=str(exc))
                    results.append({"recipient": recipient, "mode": "image", "status": "failed", "error": str(exc)})
        return results

    def _send_alert_text(self, delivery_options: dict, message: str) -> list[dict]:
        if not settings.whatsapp_enabled:
            return []
        results = []
        plan = delivery_options.get("plan") or {}
        for recipient, opts in plan.items():
            if not opts.get("send_text", False):
                continue
            try:
                data = self.wa.send_text(recipient, message)
                mid = ((data.get("messages") or [{}])[0]).get("id")
                self.db.log_monitor_event("NSE", "alert_sent", f"Text alert sent to {recipient}")
                results.append({"recipient": recipient, "mode": "text", "status": "sent", "message_id": mid})
            except Exception as exc:
                results.append({"recipient": recipient, "mode": "text", "status": "failed", "error": str(exc)})
        return results


def _matching_alerts(filing: dict, alerts: list[dict]) -> list[dict]:
    matches = []
    for alert in alerts:
        exchange = alert.get("exchange", "NSE")
        if exchange not in {filing.get("exchange"), "BOTH"}:
            continue
        symbol = (alert.get("symbol") or "").strip().upper()
        code = (alert.get("bse_code") or "").strip()
        keyword = (alert.get("company_keyword") or "").strip().lower()
        if symbol and symbol != (filing.get("symbol") or "").strip().upper():
            continue
        if code and code != (filing.get("bse_code") or "").strip():
            continue
        if keyword and keyword not in (filing.get("company_name") or "").lower():
            continue
        matches.append(alert)
    return matches


def _new_since_alert(filing: dict, alerts: list[dict]) -> bool:
    published = _date_key(filing.get("published_at"))
    valid = []
    for alert in alerts:
        created = _date_key(alert.get("created_at"))
        if created == datetime.min or published == datetime.min or published >= created:
            valid.append(alert)
    return bool(valid)


def _delivery_plan(alerts: list[dict]) -> dict:
    plan: dict = {}
    for alert in alerts:
        recipients = alert.get("recipients") or []
        if not recipients:
            for fallback in settings.whatsapp_recipients:
                plan.setdefault(fallback, {"send_text": False, "send_image": False})
                plan[fallback]["send_text"] = plan[fallback]["send_text"] or bool(alert.get("send_text"))
                plan[fallback]["send_image"] = plan[fallback]["send_image"] or bool(alert.get("send_image"))
            continue
        for recipient in recipients:
            phone = recipient.get("phone_number")
            if not phone:
                continue
            plan.setdefault(phone, {"send_text": False, "send_image": False})
            plan[phone]["send_text"] = plan[phone]["send_text"] or bool(alert.get("send_text"))
            plan[phone]["send_image"] = plan[phone]["send_image"] or bool(alert.get("send_image"))
    return {"plan": plan}


def _period_tuple(date_str: str):
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%d-%b-%y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.year, dt.month
        except ValueError:
            continue
    return None


def _is_previous_quarter(candidate: str, current: str) -> bool:
    a = _period_tuple(candidate)
    b = _period_tuple(current)
    if not a or not b:
        return False
    ay, am = a
    by, bm = b
    prev = (by - 1, 12) if bm == 3 else (by, bm - 3)
    return (ay, am) == prev


def _is_previous_year(candidate: str, current: str) -> bool:
    a = _period_tuple(candidate)
    b = _period_tuple(current)
    return bool(a and b and a[1] == b[1] and a[0] == b[0] - 1)


def _date_key(value: str | None):
    if not value:
        return datetime.min
    raw = str(value).strip()
    for fmt in (
        "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return datetime.strptime(raw.replace("Z", ""), fmt)
        except ValueError:
            continue
    return datetime.min
