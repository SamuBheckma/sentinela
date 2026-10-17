from decimal import Decimal
from unittest.mock import Mock

import pytest
import requests

from sentinela.config import Product
from sentinela.monitor import run
from sentinela.notifications import send_search_results
from sentinela.storage import connect


def product(**kwargs):
    return Product('p', 'SSD Exemplo', 'tecnologia', 'KaBuM!', 'https://www.kabum.com.br/produto/123/ssd', **kwargs)


def session_mock():
    session = Mock()
    session.post.return_value.status_code = 200
    return session


def test_results_publish_price_and_clickable_url_without_discount_claim():
    session = session_mock()
    count = send_search_results(session, 'unused', [(product(), Decimal('100'))], 15, '1 coletado')
    assert count == 1
    options = session.post.call_args.kwargs
    assert options['params'] == {'wait': 'true'}
    assert options['allow_redirects'] is False
    assert options['json']['allowed_mentions'] == {'parse': []}
    description = options['json']['embeds'][0]['description']
    assert 'R$ 100,00' in description
    assert product().url in description
    assert 'desconto ainda não verificado' in description


def test_all_results_are_paginated_within_discord_limits():
    session = session_mock()
    results = [(Product(str(i), f'Produto {i} ' + 'a' * 250, 'tecnologia', 'Loja', 'https://example.org/p/' + str(i) + 'b' * 500), Decimal('100')) for i in range(60)]
    count = send_search_results(session, 'unused', results, 15, 'c' * 3000)
    assert count > 1
    joined = ''
    for call in session.post.call_args_list:
        embed = call.kwargs['json']['embeds'][0]
        assert len(embed['description']) <= 4096
        assert len(embed['footer']['text']) <= 2048
        assert len(embed['title']) + len(embed['description']) + len(embed['footer']['text']) <= 6000
        joined += embed['description']
    assert all(item.url in joined for item, _ in results)


def test_empty_results_publish_status():
    session = session_mock()
    assert send_search_results(session, 'unused', [], 15, '0 preços') == 1
    assert 'Nenhum produto' in session.post.call_args.kwargs['json']['embeds'][0]['description']


@pytest.mark.parametrize('status', [302, 400, 401, 404, 429, 500])
def test_unsuccessful_discord_response_is_not_reported_as_sent(status):
    session = session_mock()
    session.post.return_value.status_code = status
    if status >= 400:
        session.post.return_value.raise_for_status.side_effect = requests.HTTPError('SECRET_URL')
    with pytest.raises(requests.RequestException):
        send_search_results(session, 'unused', [], 15, 'test')


def execute(tmp_path, monkeypatch, *, dry_run=False, item=None):
    monkeypatch.setattr('sentinela.monitor.fetch_price', lambda *args: Decimal('100'))
    return run('unused', str(tmp_path / 'history.sqlite3'), 'unused', Decimal('.2'), 5, 7, 15, 'test', products=[item or product()], publish_results=True, dry_run=dry_run)


def test_report_does_not_require_history(tmp_path, monkeypatch):
    reports = []
    def report(session, webhook, results, timeout, status):
        reports.extend(results)
        return 1
    monkeypatch.setattr('sentinela.monitor.send_search_results', report)
    monkeypatch.setattr('sentinela.monitor.send_discord', lambda *args: pytest.fail('Alerta sem histórico'))
    summary = execute(tmp_path, monkeypatch)
    assert summary.result_messages == 1
    assert summary.alerts_sent == 0
    assert reports == [(product(), Decimal('100'))]


def test_dry_run_never_publishes_results(tmp_path, monkeypatch):
    monkeypatch.setattr('sentinela.monitor.send_search_results', lambda *args: pytest.fail('Dry-run publicou'))
    assert execute(tmp_path, monkeypatch, dry_run=True).result_messages == 0
    assert not (tmp_path / 'history.sqlite3').exists()


def test_report_failure_preserves_history_and_does_not_log_secret(tmp_path, monkeypatch, caplog):
    def fail(*args):
        raise requests.RequestException('SECRET_URL')
    monkeypatch.setattr('sentinela.monitor.send_search_results', fail)
    summary = execute(tmp_path, monkeypatch)
    assert summary.result_failures == 1
    assert summary.exit_code == 1
    assert 'SECRET_URL' not in caplog.text
    db = connect(str(tmp_path / 'history.sqlite3'))
    assert db.execute('SELECT count(*) FROM prices').fetchone()[0] == 1
    db.close()


def test_report_respects_price_range(tmp_path, monkeypatch):
    def report(session, webhook, results, timeout, status):
        assert results == []
        return 1
    monkeypatch.setattr('sentinela.monitor.send_search_results', report)
    assert execute(tmp_path, monkeypatch, item=product(min_price_brl=Decimal('400'))).result_messages == 1
