from decimal import Decimal
from app.services.calculations import pct_change, bps_change

def test_pct_change():
    assert pct_change(Decimal('125'), Decimal('100')) == Decimal('25')
    assert pct_change(Decimal('10'), Decimal('0')) is None

def test_bps_change():
    assert bps_change(Decimal('72'), Decimal('63')) == Decimal('900')
