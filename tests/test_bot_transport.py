"""Testes independentes de conexão ao Discord para o novo transporte."""
from decimal import Decimal
from unittest.mock import Mock

from sentinela.config import Product
from sentinela.notifications import send_discord, send_search_results, send_candidates
from sentinela.pricing import Observation
from sentinela.selection import Candidate


def _product():
    return Product("id", "Produto exemplo", "tecnologia", "Loja", "https://example.org/p")


def test_bot_publisher_sends_embed_without_webhook():
    payloads = []
    send_discord(Mock(), None, _product(), Decimal("80"), Decimal("100"), Decimal(".2"), 15,
                 publisher=payloads.append)
    assert len(payloads) == 1
    assert payloads[0]["allowed_mentions"] == {"parse": []}
    assert payloads[0]["embeds"][0]["description"].find("R$ 80,00") >= 0


def test_bot_report_uses_publisher():
    payloads = []
    pages = send_search_results(Mock(), None, [(_product(), Decimal("80"))], 15, "status",
                                publisher=payloads.append)
    assert pages == len(payloads) == 1


def test_delivery_callback_only_runs_after_bot_confirmation():
    candidate = Candidate(_product(), Observation(Decimal("80")), "new")
    confirmed = []
    def fail(_payload):
        raise RuntimeError("Falhou")
    import pytest
    with pytest.raises(RuntimeError):
        send_candidates(Mock(), None, [candidate], 15, "status", confirmed.extend, publisher=fail)
    assert not confirmed

    sent = []
    assert send_candidates(Mock(), None, [candidate], 15, "status", confirmed.extend,
                           publisher=sent.append) == 1
    assert len(confirmed) == 1
