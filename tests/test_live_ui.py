from pathlib import Path


def test_dashboard_is_live_only():
    html = Path("static/index.html").read_text(encoding="utf-8").lower()
    assert "reference demo" not in html
    assert "process demo" not in html
    assert "demo sample" not in html
    assert "nse integrated filing - financials" in html
