import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import requests

from sentinela.config import Settings, load_products
from sentinela.extraction import extract_price
from sentinela.money import parse_money
from sentinela.monitor import run
from sentinela.storage import connect, record, was_alerted


@pytest.mark.parametrize("value", ["-10.00", "1e3", True, False, "NaN", "Infinity", float("nan"), -1, "1.234", "R$ lixo 10", "", None])
def test_invalid_money(value):
    assert parse_money(value) is None


@pytest.mark.parametrize("value", ["R$ 1.299,90", "1,299.90", 1299.9, Decimal("1299.90")])
def test_valid_money(value):
    assert parse_money(value) == Decimal("1299.90")


def test_free_shipping():
    assert parse_money(0) is None
    assert parse_money(0, allow_zero=True) == 0


def offer(price="100", **fields):
    return {"@type": "Offer", "priceCurrency": "BRL", "price": price, **fields}


def product(url="https://loja.example/p", price="100"):
    return {"@type": "Product", "url": url, "offers": offer(price)}


def html(data):
    return '<script type="application/ld+json">' + json.dumps(data) + '</script>'


def test_recommendations_do_not_change_price():
    assert extract_price(html([product(), product("https://loja.example/other", "1")]), "https://loja.example/p") == 100


def test_unidentified_product_is_skipped():
    data = product()
    del data["url"]
    assert extract_price(html(data), "https://loja.example/p") is None


def test_ambiguous_without_target_is_skipped():
    assert extract_price(html([product(), product("https://loja.example/other")])) is None


def test_product_graph_and_fragment_identifier():
    data = product()
    del data["url"]
    data["@id"] = "/p#product"
    assert extract_price(html({"@graph": [data]}), "https://loja.example/p") == 100


@pytest.mark.parametrize("fields", [{"priceCurrency": "USD"}, {"availability": "https://schema.org/OutOfStock"}, {"availability": "https://schema.org/Discontinued"}])
def test_unusable_offer(fields):
    data = product()
    data["offers"].update(fields)
    assert extract_price(html(data), data["url"]) is None


def test_aggregate_low_price_is_not_an_offer():
    data = product()
    data["offers"] = {"@type": "AggregateOffer", "priceCurrency": "BRL", "lowPrice": "1"}
    assert extract_price(html(data), data["url"]) is None


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "products.json"
    path.write_text(json.dumps({"products": [{"id": "p", "name": "Produto", "store": "Loja", "url": "https://loja.example/p", "category": "tecnologia", "enabled": True, "shipping_brl": 0}]}))
    return path


def test_product_config(config_file):
    assert load_products(str(config_file))[0].shipping_brl == 0


@pytest.mark.parametrize("field,value", [("category", "outra"), ("url", "http://loja.example/p"), ("shipping_brl", -1), ("enabled", "true"), ("name", "")])
def test_invalid_product_config(config_file, field, value):
    data = json.loads(config_file.read_text())
    data["products"][0][field] = value
    config_file.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_products(str(config_file))


@pytest.mark.parametrize("key,value", [("MIN_DISCOUNT", "NaN"), ("MIN_DISCOUNT", "1"), ("MIN_SAMPLES", "0"), ("MIN_HISTORY_DAYS", "-1"), ("REQUEST_TIMEOUT", "abc"), ("DISCORD_WEBHOOK_URL", "https://example.org/secret")])
def test_invalid_settings(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        Settings.from_env()


def execute(config_file, db_path, **kwargs):
    return run(str(config_file), str(db_path), kwargs.pop("webhook", None), Decimal(".2"), 5, 7, 15, "test", **kwargs)


def seed(db_path):
    db = connect(str(db_path))
    now = datetime.now(timezone.utc)
    for i in range(8):
        record(db, "p", Decimal("100"), now - timedelta(days=i + 1))
    db.close()


def test_dry_run_does_not_create_directory(config_file, tmp_path, monkeypatch):
    monkeypatch.setattr("sentinela.monitor.fetch_price", lambda *args: Decimal("75"))
    parent = tmp_path / "new-directory"
    execute(config_file, parent / "history.sqlite3", dry_run=True)
    assert not parent.exists()


def test_dry_run_reads_existing_history_without_writing(config_file, tmp_path, monkeypatch, caplog):
    db_path = tmp_path / "history.sqlite3"
    seed(db_path)
    original = db_path.read_bytes()
    monkeypatch.setattr("sentinela.monitor.fetch_price", lambda *args: Decimal("75"))
    monkeypatch.setattr("sentinela.monitor.send_discord", lambda *args: pytest.fail("Dry-run enviou mensagem"))
    with caplog.at_level("INFO"):
        execute(config_file, db_path, webhook="unused", dry_run=True)
    assert "SIMULAÇÃO" in caplog.text
    assert db_path.read_bytes() == original


def test_send_failure_keeps_history_without_dedup_or_secret(config_file, tmp_path, monkeypatch, caplog):
    db_path = tmp_path / "history.sqlite3"
    seed(db_path)
    monkeypatch.setattr("sentinela.monitor.fetch_price", lambda *args: Decimal("75"))
    def fail(*args):
        raise requests.RequestException("SECRET_WEBHOOK")
    monkeypatch.setattr("sentinela.monitor.send_discord", fail)
    summary = execute(config_file, db_path, webhook="unused")
    assert summary.alerts_failed == 1
    assert summary.exit_code == 1
    db = connect(str(db_path))
    assert not was_alerted(db, "p", Decimal("75"))
    assert db.execute("SELECT count(*) FROM prices").fetchone()[0] == 9
    db.close()
    assert "SECRET_WEBHOOK" not in caplog.text


def test_successful_alert_is_not_repeated(config_file, tmp_path, monkeypatch):
    db_path = tmp_path / "history.sqlite3"
    seed(db_path)
    calls = []
    monkeypatch.setattr("sentinela.monitor.fetch_price", lambda *args: Decimal("75"))
    monkeypatch.setattr("sentinela.monitor.send_discord", lambda *args: calls.append(args))
    assert execute(config_file, db_path, webhook="unused").alerts_sent == 1
    assert execute(config_file, db_path, webhook="unused").alerts_sent == 0
    assert len(calls) == 1


def test_collection_failure_is_visible(config_file, tmp_path, monkeypatch):
    monkeypatch.setattr("sentinela.monitor.fetch_price", lambda *args: None)
    summary = execute(config_file, tmp_path / "history.sqlite3")
    assert summary.failed == 1
    assert summary.exit_code == 1


def test_history_requires_samples_and_age(tmp_path):
    from sentinela.storage import assess
    db = connect(str(tmp_path / 'history.sqlite3'))
    now = datetime.now(timezone.utc)
    for i in range(5):
        record(db, 'p', Decimal('100'), now - timedelta(hours=i + 1))
    assert assess(db, 'p', Decimal('80'), now, Decimal('.2'), 5, 7) is None
    assert assess(db, 'p', Decimal('80'), now, Decimal('.2'), 6, 0) is None
    record(db, 'p', Decimal('100'), now - timedelta(days=7))
    assert assess(db, 'p', Decimal('80'), now, Decimal('.2'), 5, 7) is not None
    assert assess(db, 'p', Decimal('80.01'), now, Decimal('.2'), 5, 7) is None
    db.close()


def test_cli_validation_does_not_access_network_or_database(config_file, tmp_path, monkeypatch):
    from sentinela.cli import main
    monkeypatch.setattr('sentinela.cli.load_dotenv', lambda: None)
    monkeypatch.setenv('PRODUCTS_FILE', str(config_file))
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'new.sqlite3'))
    monkeypatch.delenv('DISCORD_WEBHOOK_URL', raising=False)
    monkeypatch.setattr('sys.argv', ['sentinela', '--check-config'])
    monkeypatch.setattr('requests.Session', lambda: pytest.fail('Validação acessou rede'))
    assert main() == 0
    assert not (tmp_path / 'new.sqlite3').exists()


def test_cli_invalid_settings(config_file, monkeypatch):
    from sentinela.cli import main
    monkeypatch.setattr('sentinela.cli.load_dotenv', lambda: None)
    monkeypatch.setenv('PRODUCTS_FILE', str(config_file))
    monkeypatch.setenv('MIN_DISCOUNT', 'invalid')
    monkeypatch.setattr('sys.argv', ['sentinela', '--check-config'])
    assert main() == 2


def test_discord_payload_and_redirect_handling():
    from unittest.mock import Mock
    from sentinela.config import Product
    from sentinela.notifications import send_discord
    session = Mock()
    session.post.return_value.status_code = 204
    item = Product('p', 'Produto', 'tecnologia', 'Loja', 'https://loja.example/p', Decimal('0'))
    send_discord(session, 'unused', item, Decimal('75'), Decimal('100'), Decimal('.25'), 15)
    options = session.post.call_args.kwargs
    assert options['allow_redirects'] is False
    assert options['json']['allowed_mentions'] == {'parse': []}
    assert 'Frete estimado: R$ 0,00' in options['json']['embeds'][0]['description']
    session.post.return_value.status_code = 302
    with pytest.raises(requests.RequestException):
        send_discord(session, 'unused', item, Decimal('75'), Decimal('100'), Decimal('.25'), 15)
