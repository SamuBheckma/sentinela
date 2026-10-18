from decimal import Decimal
from unittest.mock import Mock

import pytest

from sentinela.discovery import Search, SearchConfig, discover, _identity
from sentinela.mercadolivre import parse_offers, fetch_offers, MarketplaceUnavailable


def money(value, tag='span', class_name=''):
    whole, cents = value.split('.')
    return f'<{tag} class="andes-money-amount {class_name}"><span class="andes-money-amount__currency-symbol">R$</span><span class="andes-money-amount__fraction">{whole}</span><span class="andes-money-amount__cents">{cents}</span></{tag}>'


def card(title='SSD 500GB', url='https://www.mercadolivre.com.br/ssd/p/MLB123456?wid=MLB999999', current='80.00', original='100.00'):
    return f'<div class="poly-card"><a class="poly-component__title" href="{url}">{title}</a><div class="poly-price__current">{money(current)}</div>{money(original, "s", "andes-money-amount--previous")}<span class="poly-price__discount-polylabel">90% OFF</span><span class="poly-price__unit-description">No Pix</span></div>'


def search():
    return Search('ssd 500gb', 'tecnologia', ('kabum',))


def test_meli_collects_paired_prices_and_recomputes_discount():
    products, observations = parse_offers(card(), [search()])
    assert len(products) == 1
    observation = observations[products[0].id]
    assert observation.price == 80
    assert observation.original_price == 100
    assert observation.advertised_discount == Decimal('.2')
    assert observation.condition == 'No Pix'
    assert products[0].id == 'mercadolivre-mlb999999'
    assert products[0].url.endswith('wid=MLB999999')


def test_meli_ignores_navigation_external_domains_and_unrelated_products():
    markup = card(title='Mouse') + card(url='https://evil.example/p/MLB123456') + card(url='https://www.mercadolivre.com.br/ofertas')
    assert parse_offers(markup, [search()]) == ([], {})


def test_meli_currency_and_original_price_validation():
    products, observations = parse_offers(card(original='50.00'), [search()])
    assert observations[products[0].id].original_price is None
    assert parse_offers(card().replace('R$', 'US$'), [search()]) == ([], {})


def test_two_cards_cannot_mix_prices():
    products, observations = parse_offers(card(current='80.00') + card(url='https://produto.mercadolivre.com.br/MLB-888888-ssd-_JM', current='300.00', original='500.00'), [search()])
    assert [observations[p.id].price for p in products] == [80, 300]


def test_account_verification_is_a_diagnosed_unavailable_source(tmp_path, monkeypatch):
    monkeypatch.setenv('ML_STORAGE_STATE_FILE', str(tmp_path / 'missing.json'))
    session = Mock()
    session.get.return_value.url = 'https://www.mercadolivre.com.br/gz/account-verification'
    session.get.return_value.text = '<html>Verify account</html>'
    with pytest.raises(MarketplaceUnavailable) as caught:
        fetch_offers(session, [search()], 3, (), 15, 'test')
    assert caught.value.status == 'requires_login'
    assert session.get.call_count == 1


def test_pagination_is_bounded_and_stops_repeated_content(tmp_path, monkeypatch):
    monkeypatch.setenv('ML_STORAGE_STATE_FILE', str(tmp_path / 'missing.json'))
    session = Mock()
    session.get.return_value.url = 'https://www.mercadolivre.com.br/ofertas'
    session.get.return_value.text = card()
    products, _ = fetch_offers(session, [search()], 3, (), 15, 'test')
    assert len(products) == 1
    assert session.get.call_count == 2


def test_optional_ml_failure_does_not_destroy_working_store_results(tmp_path, monkeypatch):
    monkeypatch.setenv('ML_STORAGE_STATE_FILE', str(tmp_path / 'missing.json'))
    response = Mock()
    response.text = '<a href="/produto/123/ssd">SSD 500GB</a>'
    blocked = Mock()
    blocked.url = 'https://www.mercadolivre.com.br/gz/account-verification'
    session = Mock()
    session.get.side_effect = [response, blocked]
    result = discover(SearchConfig((search(),), 30, 3), session, 15, 'test')
    assert len(result.products) == 1
    assert result.failed == 0
    assert result.source_status == {'mercadolivre': 'requires_login'}


def test_catalog_pages_with_different_sellers_keep_distinct_identity():
    first = _identity('https://www.mercadolivre.com.br/ssd/p/MLB123456?wid=MLB111111', 'mercadolivre')
    second = _identity('https://www.mercadolivre.com.br/ssd/p/MLB123456?wid=MLB222222', 'mercadolivre')
    assert first[0] != second[0]


def test_partial_pagination_failure_keeps_already_collected_offers(tmp_path, monkeypatch):
    monkeypatch.setenv('ML_STORAGE_STATE_FILE', str(tmp_path / 'missing.json'))
    valid, blocked = Mock(), Mock()
    valid.url = 'https://www.mercadolivre.com.br/ofertas'
    valid.text = card()
    blocked.url = 'https://www.mercadolivre.com.br/gz/account-verification'
    session = Mock()
    session.get.side_effect = [valid, blocked]
    with pytest.raises(MarketplaceUnavailable) as caught:
        fetch_offers(session, [search()], 3, (), 15, 'test')
    assert len(caught.value.products) == 1
    assert caught.value.observations[caught.value.products[0].id].price == 80


def test_invalid_session_is_diagnosed_without_reading_private_browser_profiles(tmp_path, monkeypatch):
    from sentinela.mercadolivre import _browser_html
    path = tmp_path / 'state.json'
    path.write_text('{bad')
    monkeypatch.setenv('ML_STORAGE_STATE_FILE', str(path))
    with pytest.raises(MarketplaceUnavailable) as caught:
        _browser_html('https://www.mercadolivre.com.br/ofertas', 15)
    assert caught.value.status == 'invalid_session'


def test_catalog_and_direct_link_of_same_seller_offer_have_same_id():
    catalog = _identity('https://www.mercadolivre.com.br/ssd/p/MLB123456?wid=MLB999999', 'mercadolivre')
    direct = _identity('https://produto.mercadolivre.com.br/MLB-999999-ssd-_JM', 'mercadolivre')
    assert catalog[0] == direct[0]
    assert '/MLB-999999-' in direct[1]


def test_firefox_is_the_default_for_collection(monkeypatch):
    from sentinela.mercadolivre import browser_name, _launch_browser
    monkeypatch.delenv('ML_BROWSER', raising=False)
    playwright = Mock()
    assert browser_name() == 'firefox'
    _launch_browser(playwright, headless=True)
    assert playwright.firefox.launch.call_count == 1
    playwright.chromium.launch.assert_not_called()
    playwright.firefox.launch.assert_called_once_with(headless=True)


def test_chromium_is_an_explicit_option(monkeypatch):
    from sentinela.mercadolivre import _launch_browser
    monkeypatch.setenv('ML_BROWSER', 'chromium')
    playwright = Mock()
    _launch_browser(playwright, headless=True)
    playwright.chromium.launch.assert_called_once_with(headless=True)
    playwright.firefox.launch.assert_not_called()


def test_invalid_browser_is_rejected(monkeypatch):
    from sentinela.mercadolivre import browser_name
    monkeypatch.setenv('ML_BROWSER', 'unknown')
    with pytest.raises(ValueError):
        browser_name()
