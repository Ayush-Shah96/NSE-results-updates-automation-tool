from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _csv(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    database_path: str = os.getenv("DATABASE_PATH", "data/results_live.db")
    monitor_interval_seconds: int = int(os.getenv("MONITOR_INTERVAL_SECONDS", "60"))
    monitor_mode: str = os.getenv("MONITOR_MODE", "live")
    live_lookback_days: int = int(os.getenv("LIVE_LOOKBACK_DAYS", "45"))
    live_history_lookback_days: int = int(os.getenv("LIVE_HISTORY_LOOKBACK_DAYS", "450"))
    live_preferred_consolidation: str = os.getenv("LIVE_PREFERRED_CONSOLIDATION", "Consolidated")
    auto_scan_live: bool = os.getenv("AUTO_SCAN_LIVE", "false").lower() == "true"
    alert_poll_enabled: bool = os.getenv("ALERT_POLL_ENABLED", "true").lower() == "true"
    watch_nse_symbols: tuple[str, ...] = tuple(_csv(os.getenv("WATCH_NSE_SYMBOLS", "")))
    watch_bse_codes: tuple[str, ...] = tuple(_csv(os.getenv("WATCH_BSE_CODES", "")))

    whatsapp_enabled: bool = os.getenv("WHATSAPP_ENABLED", "false").lower() == "true"
    whatsapp_send_mode: str = os.getenv("WHATSAPP_SEND_MODE", "image")
    whatsapp_graph_version: str = os.getenv("WHATSAPP_GRAPH_VERSION", "v25.0")
    whatsapp_phone_number_id: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    whatsapp_access_token: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    whatsapp_recipients: tuple[str, ...] = tuple(_csv(os.getenv("WHATSAPP_RECIPIENTS", "")))
    whatsapp_template_name: str = os.getenv("WHATSAPP_TEMPLATE_NAME", "")
    whatsapp_template_language: str = os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "en_US")
    whatsapp_verify_token: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")

    report_title_suffix: str = os.getenv("REPORT_TITLE_SUFFIX", "(CONSOLIDATED)")


settings = Settings()
