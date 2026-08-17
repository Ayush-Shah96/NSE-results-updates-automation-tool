from pathlib import Path
import tempfile

from app.db.database import Database
from app.services.summary import build_summary_message


def test_alert_crud_and_defaults():
    with tempfile.TemporaryDirectory() as td:
        db = Database(str(Path(td) / "alerts.db"))
        alert_id = db.create_alert({
            "name": "TCS quarterly results",
            "exchange": "NSE",
            "symbol": "TCS",
            "active": True,
            "send_text": True,
            "send_image": True,
        })
        alert = db.get_alert(alert_id)
        assert alert["symbol"] == "TCS"
        assert alert["active"] == 1
        assert alert["send_text"] == 1
        assert db.update_alert(alert_id, {"active": False})
        assert db.get_alert(alert_id)["active"] == 0
        assert db.delete_alert(alert_id)


def test_summary_contains_headline_metrics():
    message = build_summary_message({
        "company_name": "TCS Limited",
        "quarter_label": "Q1 FY27",
        "source_exchange": "NSE",
        "consolidation": "Consolidated",
        "source_url": "https://example.test/filing",
        "metrics": [
            {"name": "Revenue", "current": "100", "qoq_pct": "5.5", "yoy_pct": "12.2"},
            {"name": "EBITDA", "current": "25", "qoq_pct": "4", "yoy_pct": "10"},
            {"name": "PAT", "current": "18", "qoq_pct": "2", "yoy_pct": "8"},
            {"name": "EPS (Basic)", "current": "4.2"},
        ],
    })
    assert "TCS Limited" in message
    assert "Revenue: ₹100 Cr" in message
    assert "PAT: ₹18 Cr" in message
    assert "Source:" in message
