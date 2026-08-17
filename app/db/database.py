from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

from app.config import settings


class Database:
    def __init__(self, path: str | None = None):
        self.path = Path(path or settings.database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS companies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    nse_symbol TEXT,
                    bse_code TEXT,
                    isin TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(nse_symbol, bse_code)
                );

                CREATE TABLE IF NOT EXISTS filings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    bse_code TEXT,
                    period_end TEXT NOT NULL,
                    quarter TEXT NOT NULL,
                    consolidation TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    attachment_url TEXT,
                    status TEXT NOT NULL DEFAULT 'new',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(exchange, external_id, consolidation)
                );

                CREATE TABLE IF NOT EXISTS metric_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filing_id INTEGER NOT NULL,
                    metric_name TEXT NOT NULL,
                    value REAL,
                    unit TEXT NOT NULL,
                    raw_label TEXT,
                    source_page INTEGER,
                    confidence REAL,
                    FOREIGN KEY(filing_id) REFERENCES filings(id),
                    UNIQUE(filing_id, metric_name)
                );

                CREATE TABLE IF NOT EXISTS calculated_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filing_id INTEGER NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    text_message TEXT NOT NULL,
                    image_path TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(filing_id) REFERENCES filings(id)
                );

                CREATE TABLE IF NOT EXISTS delivery_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filing_id INTEGER NOT NULL,
                    recipient TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider_message_id TEXT,
                    error TEXT,
                    sent_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(filing_id) REFERENCES filings(id)
                );

                CREATE TABLE IF NOT EXISTS recipients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL DEFAULT '',
                    phone_number TEXT NOT NULL UNIQUE,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    symbol TEXT,
                    bse_code TEXT,
                    company_keyword TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    send_text INTEGER NOT NULL DEFAULT 1,
                    send_image INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS alert_recipients (
                    alert_id INTEGER NOT NULL,
                    recipient_id INTEGER NOT NULL,
                    PRIMARY KEY(alert_id, recipient_id),
                    FOREIGN KEY(alert_id) REFERENCES alerts(id) ON DELETE CASCADE,
                    FOREIGN KEY(recipient_id) REFERENCES recipients(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS monitor_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    filing_external_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def upsert_company(self, name: str, nse_symbol: str | None = None, bse_code: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO companies(name, nse_symbol, bse_code)
                VALUES (?, ?, ?)
                ON CONFLICT(nse_symbol, bse_code) DO UPDATE SET name=excluded.name""",
                (name, nse_symbol, bse_code),
            )

    def insert_filing(self, filing: dict) -> int | None:
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO filings(
                    exchange, external_id, company_name, symbol, bse_code,
                    period_end, quarter, consolidation, published_at,
                    source_url, attachment_url, status
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(filing.get(k) for k in [
                    "exchange", "external_id", "company_name", "symbol", "bse_code",
                    "period_end", "quarter", "consolidation", "published_at",
                    "source_url", "attachment_url", "status"
                ]),
            )
            if cur.rowcount == 0:
                return None
            return int(cur.lastrowid)

    def add_metrics(self, filing_id: int, metrics: Iterable[dict]) -> None:
        with self.connect() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO metric_snapshots(
                    filing_id, metric_name, value, unit, raw_label, source_page, confidence
                ) VALUES(?,?,?,?,?,?,?)""",
                [(
                    filing_id,
                    m.get("metric_name"),
                    m.get("value"),
                    m.get("unit", "Rs in Cr"),
                    m.get("raw_label"),
                    m.get("source_page"),
                    m.get("confidence"),
                ) for m in metrics],
            )

    def save_report(self, filing_id: int, payload: dict, text_message: str, image_path: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO calculated_reports(filing_id, payload_json, text_message, image_path)
                   VALUES(?,?,?,?)""",
                (filing_id, json.dumps(payload), text_message, image_path),
            )

    def log_delivery(self, filing_id: int, recipient: str, mode: str, status: str,
                     provider_message_id: str | None = None, error: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO delivery_logs(filing_id, recipient, mode, status,
                   provider_message_id, error, sent_at)
                   VALUES(?,?,?,?,?,?,CASE WHEN ?='sent' THEN CURRENT_TIMESTAMP ELSE NULL END)""",
                (filing_id, recipient, mode, status, provider_message_id, error, status),
            )

    def list_filings(self, limit: int = 50):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM filings ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()]

    def get_report(self, filing_id: int):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM calculated_reports WHERE filing_id=?", (filing_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_deliveries(self, filing_id: int | None = None, limit: int = 50):
        with self.connect() as conn:
            if filing_id is not None:
                rows = conn.execute(
                    "SELECT * FROM delivery_logs WHERE filing_id=? ORDER BY id DESC LIMIT ?",
                    (filing_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM delivery_logs ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]


    def is_filing_seen(self, external_id: str, exchange: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM filings WHERE external_id=? AND exchange=? LIMIT 1",
                (external_id, exchange),
            ).fetchone()
            return row is not None

    def normalize_phone(self, value: str) -> str:
        raw = str(value or "").strip()
        if raw.startswith("+"):
            raw = raw[1:]
        if raw.startswith("00"):
            raw = raw[2:]
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not 8 <= len(digits) <= 15:
            raise ValueError("Phone number must contain 8 to 15 digits in international format.")
        return digits

    def upsert_recipient(self, phone_number: str, name: str = "") -> int:
        phone = self.normalize_phone(phone_number)
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO recipients(name, phone_number) VALUES(?, ?)
                   ON CONFLICT(phone_number) DO UPDATE SET name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE recipients.name END, active=1""",
                (str(name or "").strip(), phone),
            )
            row = conn.execute("SELECT id FROM recipients WHERE phone_number=?", (phone,)).fetchone()
            return int(row[0])

    def list_recipients(self, active_only: bool = False):
        with self.connect() as conn:
            sql = "SELECT * FROM recipients" + (" WHERE active=1" if active_only else "") + " ORDER BY id DESC"
            return [dict(r) for r in conn.execute(sql).fetchall()]

    def get_recipient(self, recipient_id: int):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM recipients WHERE id=?", (recipient_id,)).fetchone()
            return dict(row) if row else None

    def delete_recipient(self, recipient_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM recipients WHERE id=?", (recipient_id,))
            return cur.rowcount > 0

    def _set_alert_recipients(self, conn, alert_id: int, recipient_ids: list[int]) -> None:
        conn.execute("DELETE FROM alert_recipients WHERE alert_id=?", (alert_id,))
        cleaned = sorted({int(rid) for rid in recipient_ids if int(rid) > 0})
        if cleaned:
            conn.executemany(
                "INSERT OR IGNORE INTO alert_recipients(alert_id, recipient_id) VALUES(?, ?)",
                [(alert_id, rid) for rid in cleaned],
            )

    def list_alert_recipients(self, alert_id: int):
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT r.* FROM recipients r
                   JOIN alert_recipients ar ON ar.recipient_id=r.id
                   WHERE ar.alert_id=? ORDER BY r.name, r.phone_number""",
                (alert_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def create_alert(self, payload: dict) -> int:
        recipient_ids = [int(x) for x in (payload.get("recipient_ids") or [])]
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO alerts(name, exchange, symbol, bse_code, company_keyword, active, send_text, send_image)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    payload.get("name"), payload.get("exchange"), payload.get("symbol"),
                    payload.get("bse_code"), payload.get("company_keyword"),
                    1 if payload.get("active", True) else 0,
                    1 if payload.get("send_text", True) else 0,
                    1 if payload.get("send_image", True) else 0,
                ),
            )
            alert_id = int(cur.lastrowid)
            self._set_alert_recipients(conn, alert_id, recipient_ids)
            return alert_id

    def _decorate_alert(self, row: dict) -> dict:
        row["recipients"] = self.list_alert_recipients(row["id"])
        row["recipient_ids"] = [r["id"] for r in row["recipients"]]
        return row

    def list_alerts(self, active_only: bool = False):
        with self.connect() as conn:
            sql = "SELECT * FROM alerts" + (" WHERE active=1" if active_only else "") + " ORDER BY id DESC"
            rows = [dict(r) for r in conn.execute(sql).fetchall()]
        return [self._decorate_alert(r) for r in rows]

    def get_alert(self, alert_id: int):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
            data = dict(row) if row else None
        return self._decorate_alert(data) if data else None

    def update_alert(self, alert_id: int, payload: dict) -> bool:
        fields = []
        values = []
        for key in ("name", "exchange", "symbol", "bse_code", "company_keyword"):
            if key in payload:
                fields.append(f"{key}=?")
                values.append(payload[key])
        for key in ("active", "send_text", "send_image"):
            if key in payload:
                fields.append(f"{key}=?")
                values.append(1 if payload[key] else 0)
        recipient_ids = payload.get("recipient_ids")
        if not fields and recipient_ids is None:
            return False
        with self.connect() as conn:
            if fields:
                values.append(alert_id)
                cur = conn.execute(f"UPDATE alerts SET {', '.join(fields)} WHERE id=?", values)
                changed = cur.rowcount > 0
            else:
                changed = conn.execute("SELECT 1 FROM alerts WHERE id=?", (alert_id,)).fetchone() is not None
            if recipient_ids is not None:
                self._set_alert_recipients(conn, alert_id, [int(x) for x in recipient_ids])
            return changed

    def delete_alert(self, alert_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
            return cur.rowcount > 0

    def log_monitor_event(self, exchange: str, event_type: str, message: str, filing_external_id: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO monitor_events(exchange,event_type,message,filing_external_id) VALUES(?,?,?,?)",
                (exchange, event_type, message, filing_external_id),
            )

    def list_monitor_events(self, limit: int = 50):
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM monitor_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def latest_history(self, company_name: str, quarter: str, consolidation: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT f.*, r.payload_json
                   FROM filings f JOIN calculated_reports r ON r.filing_id=f.id
                   WHERE f.company_name=? AND f.consolidation=? AND f.quarter LIKE ?
                   ORDER BY f.period_end DESC LIMIT 8""",
                (company_name, consolidation, f"%FY%"),
            ).fetchall()
            return [dict(r) for r in rows]
