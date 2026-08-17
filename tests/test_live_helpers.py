from app.services.formatter import quarter_headers


def test_q1_headers():
    assert quarter_headers("Q1 FY27") == ["Particulars", "Q1 FY26", "Q4 FY26", "Q1 FY27", "QoQ %", "YoY %"]


def test_q2_headers():
    assert quarter_headers("Q2 FY27") == ["Particulars", "Q2 FY26", "Q1 FY27", "Q2 FY27", "QoQ %", "YoY %"]
