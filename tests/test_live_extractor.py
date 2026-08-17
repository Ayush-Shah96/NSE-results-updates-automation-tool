from decimal import Decimal

from app.services.live_extractor import parse_ixbrl_html


def test_ixbrl_extracts_current_period_and_normalizes_lakhs():
    html = """
    <html><body>
      <div>Name of company | Example Limited</div>
      <div>NSE Symbol | EXAMPLE</div>
      <div>Nature of report standalone or consolidated | Consolidated</div>
      <div>Amount in (Lakhs)</div>
      <table>
        <tr><td></td><td>Revenue from operations</td><td>10,000.00</td><td>25,000.00</td></tr>
        <tr><td></td><td>Total expenses</td><td>7,500.00</td><td>20,000.00</td></tr>
        <tr><td></td><td>Total profit before tax</td><td>2,500.00</td><td>5,000.00</td></tr>
        <tr><td></td><td>Net Profit Loss for the period from continuing operations</td><td>1,800.00</td><td>3,600.00</td></tr>
        <tr><td></td><td>Basic earnings (loss) per share</td><td>18.00</td><td>36.00</td></tr>
      </table>
    </body></html>
    """
    metrics, meta = parse_ixbrl_html(html, "30-Jun-2026")
    assert meta["symbol"] == "EXAMPLE"
    assert meta["consolidation"] == "Consolidated"
    assert metrics["Revenue"] == Decimal("100")
    assert metrics["Expenses"] == Decimal("75")
    assert metrics["PAT"] == Decimal("18")
    assert metrics["EPS (Basic)"] == Decimal("18")


def test_parser_does_not_invent_gross_profit():
    html = """
    <html><body>
      <div>Name of company | Example Limited</div>
      <div>NSE Symbol | EXAMPLE</div>
      <div>Amount in (Lakhs)</div>
      <table><tr><td></td><td>Revenue from operations</td><td>1,000.00</td><td>1,000.00</td></tr></table>
    </body></html>
    """
    metrics, _ = parse_ixbrl_html(html, "30-Jun-2026")
    assert "Revenue" in metrics
    assert "Gross Profit" not in metrics
