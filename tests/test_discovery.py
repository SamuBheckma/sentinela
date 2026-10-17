import json
from decimal import Decimal
from unittest.mock import Mock

import pytest
import requests

from sentinela.config import Product
from sentinela.discovery import Search, SearchConfig, discover, extract_products, load_searches, update_catalog
from sentinela.extraction import extract_price


SEARCH_URL = 'https://www.kabum.com.br/busca/ssd'
PRODUCT_URL = 'https://www.kabum.com.br/produto/123/ssd-exemplo'


def html(products):
    return '<script type="application/ld+json">' + json.dumps(products) + '</script>'


def item(name='SSD Exemplo', url=PRODUCT_URL):
    return {'@type': 'Product', 'name': name, 'offers': {'@type': 'Offer', 'url': url, 'priceCurrency': 'BRL', 'price': '100'}}


def test_structured_discovery_and_stable_id():
    found = extract_products(html([item(), item(url=PRODUCT_URL + '?tracking=1')]), SEARCH_URL, 'kabum', Search('ssd', 'tecnologia', ('kabum',)))
    assert len(found) == 1
    assert found[0].id == 'kabum-123'
    assert found[0].url == PRODUCT_URL


def test_discovery_filters_recommendations_exclusions_and_external_links():
    data = [item('Mouse'), item('SSD Case'), item('SSD', 'https://evil.example/produto/1/ssd'), item('SSD', 'https://www.kabum.com.br/busca/ssd'), item('SSD Original')]
    search = Search('ssd', 'tecnologia', ('kabum',), exclude=('case',))
    assert [p.name for p in extract_products(html(data), SEARCH_URL, 'kabum', search)] == ['SSD Original']


def test_title_accents_and_limit():
    data = [item('Memória RAM 8GB', PRODUCT_URL), item('Memória RAM 16GB', PRODUCT_URL.replace('/123/', '/124/'))]
    assert len(extract_products(html(data), SEARCH_URL, 'kabum', Search('memoria ram', 'tecnologia', ('kabum',), limit=1))) == 1


def test_html_links_without_json_ld():
    markup = '<a href="/produto/123/ssd-exemplo" title="SSD Exemplo">Comprar</a>'
    assert extract_products(markup, SEARCH_URL, 'kabum', Search('ssd', 'tecnologia', ('kabum',)))[0].url == PRODUCT_URL


def test_malformed_json_ld_ignored():
    assert extract_products('<script type="application/ld+json">bad</script>', SEARCH_URL, 'kabum', Search('ssd', 'tecnologia', ('kabum',))) == []


def test_price_identity_from_offer_url():
    assert extract_price(html(item()), PRODUCT_URL) == Decimal('100')
    assert extract_price(html(item(url=PRODUCT_URL.replace('/123/', '/999/'))), PRODUCT_URL) is None


def test_conflicting_product_identity_not_overridden_by_offer():
    data = item()
    data['url'] = 'https://www.kabum.com.br/produto/999/other'
    assert extract_price(html(data), PRODUCT_URL) is None


def test_ambiguous_offer_identity_skipped():
    data = item()
    data['offers'] = [data['offers'], item(url=PRODUCT_URL.replace('/123/', '/999/'))['offers']]
    assert extract_price(html(data), PRODUCT_URL) is None


def test_search_partial_failure_keeps_results():
    response = Mock()
    response.text = html(item())
    session = Mock()
    session.get.side_effect = [response, requests.RequestException('secret')]
    result = discover(SearchConfig((Search('ssd', 'tecnologia', ('kabum', 'amazon')),)), session, 15, 'test')
    assert result.attempted == 2
    assert result.failed == 1
    assert len(result.products) == 1


def test_empty_search_is_visible_failure():
    response = Mock()
    response.text = '<html>captcha</html>'
    session = Mock()
    session.get.return_value = response
    result = discover(SearchConfig((Search('ssd', 'tecnologia', ('kabum',)),)), session, 15, 'test')
    assert result.failed == 1
    assert result.products == []


def test_catalog_persistence_cache_and_dry_run(tmp_path):
    path = tmp_path / 'catalog.json'
    first = Product('kabum-123', 'SSD', 'tecnologia', 'KaBuM!', PRODUCT_URL)
    second = Product('kabum-124', 'SSD 2', 'tecnologia', 'KaBuM!', PRODUCT_URL.replace('/123/', '/124/'))
    assert update_catalog(str(path), [first], 2, dry_run=True) == [first]
    assert not path.exists()
    update_catalog(str(path), [first], 2)
    original = path.read_bytes()
    assert update_catalog(str(path), [second], 2, dry_run=True) == [second, first]
    assert path.read_bytes() == original
    assert update_catalog(str(path), [], 2) == [first]
    assert update_catalog(str(path), [second], 1) == [second]


@pytest.mark.parametrize('field,value', [('term', ''), ('stores', ['bad']), ('stores', 'kabum'), ('limit', True), ('limit', 100), ('category', 'bad'), ('exclude', 'bad'), ('enabled', 'true')])
def test_invalid_search_config(tmp_path, field, value):
    data = {'term': 'ssd', 'category': 'tecnologia', 'stores': ['kabum']}
    data[field] = value
    path = tmp_path / 'searches.json'
    path.write_text(json.dumps({'searches': [data]}))
    with pytest.raises(ValueError):
        load_searches(str(path))


def test_automatic_cli_search_then_monitor(tmp_path, monkeypatch):
    from sentinela.cli import main
    monkeypatch.setattr('sentinela.cli.load_dotenv', lambda: None)
    config = tmp_path / 'searches.json'
    config.write_text(json.dumps({'searches': [{'term': 'ssd', 'category': 'tecnologia', 'stores': ['kabum']}]}))
    manual = tmp_path / 'manual.json'
    manual.write_text('{"products": []}')
    catalog = tmp_path / 'catalog.json'
    database = tmp_path / 'history.sqlite3'
    for key, value in [('SEARCHES_FILE', config), ('PRODUCTS_FILE', manual), ('DISCOVERY_FILE', catalog), ('DATABASE_PATH', database)]:
        monkeypatch.setenv(key, str(value))
    monkeypatch.delenv('DISCORD_WEBHOOK_URL', raising=False)
    monkeypatch.setattr('sys.argv', ['sentinela'])
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=None)
    response = Mock()
    response.text = html(item())
    session.get.return_value = response
    monkeypatch.setattr('requests.Session', lambda: session)
    assert main() == 0
    assert session.get.call_count == 2
    assert catalog.exists()
    from sentinela.storage import connect
    db = connect(str(database))
    assert db.execute('SELECT product_id, price_cents FROM prices').fetchall() == [('kabum-123', 10000)]
    db.close()
    session.post.assert_not_called()


def test_kabum_product_id_matches_slug_but_not_another_product():
    data = item()
    data['@id'] = 'https://www.kabum.com.br/produto/123'
    assert extract_price(html(data), PRODUCT_URL) == 100
    assert extract_price(html(data), PRODUCT_URL.replace('/123/', '/124/')) is None


def test_cli_search_only_never_collects_or_publishes(tmp_path, monkeypatch, capsys):
    from sentinela.cli import main
    monkeypatch.setattr('sentinela.cli.load_dotenv', lambda: None)
    searches = tmp_path / 'searches.json'
    searches.write_text(json.dumps({'searches': [{'term': 'ssd', 'category': 'tecnologia', 'stores': ['kabum']}]}))
    manual = tmp_path / 'manual.json'
    manual.write_text('{"products": []}')
    monkeypatch.setenv('SEARCHES_FILE', str(searches))
    monkeypatch.setenv('PRODUCTS_FILE', str(manual))
    monkeypatch.setenv('DISCOVERY_FILE', str(tmp_path / 'catalog.json'))
    monkeypatch.delenv('DISCORD_WEBHOOK_URL', raising=False)
    monkeypatch.setattr('sys.argv', ['sentinela', '--search'])
    monkeypatch.setattr('sentinela.cli.run', lambda **kwargs: pytest.fail('Busca iniciou monitoramento'))
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=None)
    session.get.return_value.text = html(item())
    monkeypatch.setattr('requests.Session', lambda: session)
    assert main() == 0
    assert PRODUCT_URL in capsys.readouterr().out
    assert session.get.call_count == 1
    session.post.assert_not_called()


def test_cli_automatic_dry_run_does_not_write(tmp_path, monkeypatch):
    from sentinela.cli import main
    monkeypatch.setattr('sentinela.cli.load_dotenv', lambda: None)
    searches = tmp_path / 'searches.json'
    searches.write_text(json.dumps({'searches': [{'term': 'ssd', 'category': 'tecnologia', 'stores': ['kabum']}]}))
    manual = tmp_path / 'manual.json'
    manual.write_text('{"products": []}')
    monkeypatch.setenv('SEARCHES_FILE', str(searches))
    monkeypatch.setenv('PRODUCTS_FILE', str(manual))
    monkeypatch.setenv('DISCOVERY_FILE', str(tmp_path / 'data' / 'catalog.json'))
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'data' / 'history.sqlite3'))
    monkeypatch.delenv('DISCORD_WEBHOOK_URL', raising=False)
    monkeypatch.setattr('sys.argv', ['sentinela', '--dry-run'])
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=None)
    session.get.return_value.text = html(item())
    monkeypatch.setattr('requests.Session', lambda: session)
    assert main() == 0
    assert not (tmp_path / 'data').exists()
    session.post.assert_not_called()


def test_search_budget_and_catalog_limit_are_independent(tmp_path):
    path = tmp_path / 'searches.json'
    path.write_text(json.dumps({'max_products': 60, 'searches': [{'term': 'samsung watch', 'category': 'tecnologia', 'stores': ['kabum'], 'min_price_brl': 400, 'max_price_brl': 1600}]}))
    config = load_searches(str(path))
    assert config.max_products == 60
    assert config.searches[0].min_price_brl == 400
    assert config.searches[0].max_price_brl == 1600


def test_budget_survives_catalog_roundtrip(tmp_path):
    search = Search('ssd', 'tecnologia', ('kabum',), min_price_brl=Decimal('400'), max_price_brl=Decimal('1600'))
    products = extract_products(html(item()), SEARCH_URL, 'kabum', search)
    path = tmp_path / 'catalog.json'
    update_catalog(str(path), products, 10)
    loaded = update_catalog(str(path), [], 10)
    assert loaded[0].min_price_brl == 400
    assert loaded[0].max_price_brl == 1600


@pytest.mark.parametrize('price,expected_alerts', [('300', 0), ('500', 1), ('1700', 0)])
def test_budget_filters_alerts_but_keeps_history(tmp_path, monkeypatch, price, expected_alerts):
    from datetime import datetime, timedelta, timezone
    from sentinela.monitor import run
    from sentinela.storage import connect, record
    db_path = tmp_path / 'history.sqlite3'
    db = connect(str(db_path))
    now = datetime.now(timezone.utc)
    for i in range(8):
        record(db, 'watch', Decimal('2500'), now - timedelta(days=i + 1))
    db.close()
    watch = Product('watch', 'Watch', 'tecnologia', 'Loja', PRODUCT_URL, min_price_brl=Decimal('400'), max_price_brl=Decimal('1600'))
    monkeypatch.setattr('sentinela.monitor.fetch_price', lambda *args: Decimal(price))
    sends = []
    monkeypatch.setattr('sentinela.monitor.send_discord', lambda *args: sends.append(args))
    summary = run('unused', str(db_path), 'unused', Decimal('.2'), 5, 7, 15, 'test', products=[watch])
    assert summary.alerts_sent == expected_alerts
    db = connect(str(db_path))
    assert db.execute('SELECT count(*) FROM prices').fetchone()[0] == 9
    db.close()


def test_forhonor_variants_keep_separate_identities():
    search = Search('luva tatica', 'militar', ('forhonor',))
    markup = '<a href="/products/luva-tatica?variant=123&amp;tracking=1">Luva Tática P</a><a href="/products/luva-tatica?variant=124">Luva Tática M</a>'
    products = extract_products(markup, 'https://www.forhonor.com.br/search?q=luva', 'forhonor', search)
    assert len(products) == 2
    assert products[0].id != products[1].id
    assert products[0].url.endswith('?variant=123')
    data = {'@type': 'ProductGroup', 'hasVariant': [
        {'@type': 'Product', '@id': '/products/luva-tatica?variant=123#variant', 'offers': {'@type': 'Offer', 'priceCurrency': 'BRL', 'price': '250'}},
        {'@type': 'Product', '@id': '/products/luva-tatica?variant=124#variant', 'offers': {'@type': 'Offer', 'priceCurrency': 'BRL', 'price': '100'}},
    ]}
    assert extract_price(html(data), products[0].url) == 250


def test_tuttomoto_discovery_excludes_categories():
    markup = '<a href="/product-category/jaqueta">Jaqueta</a><a href="/produto/jaqueta-azul/">Jaqueta Azul</a>'
    products = extract_products(markup, 'https://www.tuttomoto.com.br/?s=jaqueta', 'tuttomoto', Search('jaqueta', 'militar', ('tuttomoto',)))
    assert len(products) == 1
    assert products[0].id == 'tuttomoto-jaqueta-azul'


@pytest.mark.parametrize('availability,expected', [('InStock', Decimal('100')), ('OutOfStock', None)])
def test_atalaia_scoped_microdata_and_stock(availability, expected):
    url = 'https://atalaia-sao-paulo.lojaintegrada.com.br/produto/luva.html'
    markup = f'''<link rel="canonical" href="{url}">
    <div itemscope itemtype="http://schema.org/Product">
      <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
        <meta itemprop="price" content="100.00">
        <meta itemprop="priceCurrency" content="BRL">
        <meta itemprop="availability" content="http://schema.org/{availability}">
      </div>
    </div><meta itemprop="price" content="1">'''
    assert extract_price(markup, url) == expected
    assert extract_price(markup, url.replace('luva.html', 'other.html')) is None


def test_catalog_reconciles_interests_and_budget_without_new_results(tmp_path):
    path = tmp_path / 'catalog.json'
    ram = Product('ram', 'Memória RAM', 'tecnologia', 'KaBuM!', PRODUCT_URL)
    ssd = Product('ssd', 'SSD 500GB', 'tecnologia', 'KaBuM!', PRODUCT_URL.replace('/123/', '/124/'))
    update_catalog(str(path), [ram, ssd], 10)
    config = SearchConfig((Search('ssd', 'tecnologia', ('kabum',), max_price_brl=Decimal('500')),))
    selected = update_catalog(str(path), [], 10, config=config)
    assert [p.id for p in selected] == ['ssd']
    assert selected[0].max_price_brl == 500
    update_catalog(str(path), [], 10, config=SearchConfig(()))
    assert json.loads(path.read_text())['products'] == []


def test_accessible_catalog_without_matches_is_not_a_network_failure():
    session = Mock()
    session.get.return_value.text = '<a href="/produto/123/mouse">Mouse</a>'
    result = discover(SearchConfig((Search('ssd', 'tecnologia', ('kabum',)),)), session, 15, 'test')
    assert result.products == []
    assert result.empty == 1
    assert result.failed == 0


@pytest.mark.parametrize('stock,expected_price,expected_unavailable', [('InStock', Decimal('100'), False), ('OutOfStock', None, True)])
def test_atalaia_nested_recommendation_cannot_supply_price_or_stock(stock, expected_price, expected_unavailable):
    from sentinela.extraction import is_unavailable
    url = 'https://atalaia-sao-paulo.lojaintegrada.com.br/produto/bota.html'
    markup = f'''<link rel="canonical" href="{url}">
    <div itemscope itemtype="http://schema.org/Product">
      <h1 itemprop="name">Bota</h1>
      <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
        <meta itemprop="price" content="100.00">
        <meta itemprop="priceCurrency" content="BRL">
        <meta itemprop="availability" content="http://schema.org/{stock}">
      </div>
      <div itemscope itemtype="http://schema.org/Product">
        <a itemprop="url" href="/produto/lanterna.html">Lanterna</a>
        <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
          <meta itemprop="price" content="1.00">
          <meta itemprop="priceCurrency" content="BRL">
          <meta itemprop="availability" content="http://schema.org/InStock">
        </div>
      </div>
    </div>'''
    assert extract_price(markup, url) == expected_price
    assert is_unavailable(markup, url) == expected_unavailable
