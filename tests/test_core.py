import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sentinela.core import assess, connect, extract_price, parse_money, record, was_alerted, mark_alerted


def test_parse_money():
    assert parse_money("R$ 1.299,90") == Decimal("1299.90")
    assert parse_money("99.90") == Decimal("99.90")
    assert parse_money("indisponível") is None


def test_json_ld_brl():
    html = '<script type="application/ld+json">{"@type":"Product","offers":{"@type":"Offer","priceCurrency":"BRL","price":"199.90"}}</script>'
    assert extract_price(html) == Decimal("199.90")


def test_price_history_and_dedup(tmp_path):
    db = connect(str(tmp_path / "test.sqlite"))
    now = datetime.now(timezone.utc)
    for i in range(8):
        record(db, "p", Decimal("100"), now - timedelta(days=8-i))
    result = assess(db, "p", Decimal("75"), now, Decimal("0.20"), 5, 7)
    assert result is not None
    assert result[1] == Decimal("0.25")
    assert not was_alerted(db, "p", Decimal("75"))
    mark_alerted(db, "p", Decimal("75"), now)
    assert was_alerted(db, "p", Decimal("75"))
    db.close()
