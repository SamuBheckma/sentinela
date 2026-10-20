"""Testes do worker sem rede, token ou conexão com Discord."""
from decimal import Decimal

from sentinela.config import Product
from sentinela.monitor import run
from sentinela.pricing import Observation


def test_monitor_can_publish_through_bot_without_webhook(tmp_path):
    item = Product("p1", "Produto", "tecnologia", "Loja", "https://example.org/produto")
    deliveries = []
    result = run(
        "unused", str(tmp_path / "history.sqlite3"), None,
        Decimal("0.20"), 5, 7, 15, "test",
        products=[item], publish_results=True,
        observations={item.id: Observation(Decimal("99.90"))},
        publisher=deliveries.append,
    )
    assert result.published == 1
    assert result.result_messages == 1
    assert deliveries[0]["allowed_mentions"] == {"parse": []}


def test_worker_imports_and_persistent_volume_seed(tmp_path, monkeypatch):
    from sentinela.bot import prepare_data
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "history.sqlite3").write_bytes(b"EXAMPLE")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "persistent" / "history.sqlite3"))
    monkeypatch.setenv("DISCOVERY_FILE", str(tmp_path / "persistent" / "discovered.json"))
    monkeypatch.setenv("RUN_REPORT_FILE", str(tmp_path / "persistent" / "last_run.json"))
    prepare_data()
    target = tmp_path / "persistent" / "history.sqlite3"
    assert target.read_bytes() == b"EXAMPLE"
    target.write_bytes(b"CHANGED")
    prepare_data()
    assert target.read_bytes() == b"CHANGED"
