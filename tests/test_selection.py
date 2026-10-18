from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest
import requests

from sentinela.config import Product
from sentinela.pricing import Observation, extract_observation
from sentinela.selection import Candidate, PublicationPolicy, candidate_for, select_candidates
from sentinela.storage import connect, mark_published, record, recently_published
from sentinela.monitor import run
from sentinela.notifications import send_candidates


def product(number='p', store='Loja', **kwargs):
    return Product(number, 'SSD ' + number, 'tecnologia', store, 'https://example.org/p/' + number, **kwargs)


@pytest.fixture
def history(tmp_path):
    db = connect(str(tmp_path / 'history.sqlite3'))
    yield db
    db.close()


def choose(db, observation, now, item=None):
    return candidate_for(db, item or product(), observation, now, PublicationPolicy(), Decimal('.2'), 5, 7)


def test_historical_discount_has_priority_over_advertised(history):
    now = datetime.now(timezone.utc)
    for i in range(8):
        record(history, 'p', Decimal('100'), now - timedelta(days=i + 1))
    candidate = choose(history, Observation(Decimal('70'), Decimal('90')), now)
    assert candidate.kind == 'historical'
    assert candidate.reference == 100
    assert candidate.discount == Decimal('.3')


def test_advertised_discount_does_not_require_history(history):
    candidate = choose(history, Observation(Decimal('80'), Decimal('100')), datetime.now(timezone.utc))
    assert candidate.kind == 'advertised'
    assert candidate.discount == Decimal('.2')


def test_first_seen_is_new_but_same_price_is_not_republished(history):
    now = datetime.now(timezone.utc)
    observation = Observation(Decimal('100'))
    assert choose(history, observation, now).kind == 'new'
    mark_published(history, 'p', observation.price, 'new', now)
    assert choose(history, observation, now + timedelta(hours=3)) is None
    # Preço inalterado, sem promoção, não volta só porque passaram sete dias.
    assert choose(history, observation, now + timedelta(days=8)) is None


def test_lower_price_compares_last_delivered_publication(history):
    now = datetime.now(timezone.utc)
    mark_published(history, 'p', Decimal('100'), 'new', now - timedelta(days=1))
    candidate = choose(history, Observation(Decimal('90')), now)
    assert candidate.kind == 'price_drop'
    assert candidate.reference == 100
    record(history, 'p', Decimal('90'), now)
    # Falhou envio: a próxima coleta retenta mesmo se o banco já tiver o novo preço.
    assert choose(history, Observation(Decimal('90')), now + timedelta(hours=3)).kind == 'price_drop'
    mark_published(history, 'p', Decimal('90'), 'price_drop', now)
    assert choose(history, Observation(Decimal('90')), now + timedelta(hours=3)) is None


def test_announced_offer_can_recur_after_interval(history):
    now = datetime.now(timezone.utc)
    observation = Observation(Decimal('80'), Decimal('100'))
    mark_published(history, 'p', observation.price, 'advertised', now - timedelta(days=6))
    assert choose(history, observation, now) is None
    assert choose(history, observation, now + timedelta(days=2)).kind == 'advertised'


def test_rank_prioritizes_discounts_and_balances_stores():
    candidates = [Candidate(product(str(i), 'A'), Observation(Decimal('100')), 'advertised', Decimal('.4')) for i in range(8)]
    candidates += [Candidate(product('other', 'B'), Observation(Decimal('80')), 'advertised', Decimal('.2'))]
    candidates += [Candidate(product('new', 'C'), Observation(Decimal('10')), 'new')]
    chosen = select_candidates(candidates, 3)
    assert [c.product.store for c in chosen] == ['A', 'B', 'A']
    assert all(c.kind == 'advertised' for c in chosen)


def test_known_variants_do_not_fill_one_selection():
    first = Product('p1', 'Luva P', 'militar', 'Loja', 'https://example.org/luva?variant=1')
    second = Product('p2', 'Luva M', 'militar', 'Loja', 'https://example.org/luva?variant=2')
    chosen = select_candidates([Candidate(first, Observation(Decimal('100')), 'new'), Candidate(second, Observation(Decimal('100')), 'new')], 12)
    assert len(chosen) == 1


@pytest.mark.parametrize('variable,value', [('MAX_POSTS_PER_RUN','0'), ('REPEAT_AFTER_DAYS','0'), ('MIN_ADVERTISED_DISCOUNT','NaN'), ('MIN_PRICE_CHANGE','1')])
def test_invalid_policy(monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError):
        PublicationPolicy.from_env()


def test_structured_strikethrough_price_is_announced_not_historical():
    html = '''<script type="application/ld+json">{"@type":"Product","url":"https://example.org/p","offers":{"@type":"Offer","price":"80","priceCurrency":"BRL","priceSpecification":{"@type":"UnitPriceSpecification","priceType":"https://schema.org/StrikethroughPrice","price":"100","priceCurrency":"BRL"}}}</script>'''
    observation = extract_observation(html, 'https://example.org/p')
    assert observation.price == 80
    assert observation.advertised_discount == Decimal('.2')
    assert extract_observation(html, 'https://example.org/other') is None


def test_generic_unit_price_does_not_become_previous_price():
    html = '''<script type="application/ld+json">{"@type":"Product","url":"https://example.org/p","offers":{"@type":"Offer","price":"80","priceCurrency":"BRL","priceSpecification":{"@type":"UnitPriceSpecification","price":"100","priceCurrency":"BRL"}}}</script>'''
    assert extract_observation(html, 'https://example.org/p').original_price is None


def test_selection_records_only_confirmed_pages(tmp_path, monkeypatch):
    db_path = tmp_path / 'test.sqlite3'
    item = product()
    def send(session, webhook, candidates, timeout, status, on_delivered):
        on_delivered(candidates[:1])
        raise requests.RequestException('SECRET')
    monkeypatch.setattr('sentinela.monitor.fetch_observation', lambda *args: Observation(Decimal('100')))
    monkeypatch.setattr('sentinela.monitor.send_candidates', send)
    summary = run('unused', str(db_path), 'unused', Decimal('.2'), 5, 7, 15, 'test', products=[item, product('second')], publish_results=True)
    assert summary.published == 1
    assert summary.result_failures == 1
    db = connect(str(db_path))
    assert db.execute('SELECT count(*) FROM publications').fetchone()[0] == 1
    assert db.execute('SELECT count(*) FROM prices').fetchone()[0] == 2
    db.close()


def test_normal_cycle_does_not_repeat_entire_catalog(tmp_path, monkeypatch):
    calls = []
    def send(session, webhook, candidates, timeout, status, on_delivered):
        calls.extend(candidates)
        on_delivered(candidates)
        return 1
    monkeypatch.setattr('sentinela.monitor.fetch_observation', lambda *args: Observation(Decimal('100')))
    monkeypatch.setattr('sentinela.monitor.send_candidates', send)
    args = ('unused', str(tmp_path / 'db.sqlite3'), 'unused', Decimal('.2'), 5, 7, 15, 'test')
    assert run(*args, products=[product()], publish_results=True).published == 1
    assert run(*args, products=[product()], publish_results=True).published == 0
    assert len(calls) == 1


def test_selection_dry_run_does_not_publish_or_write(tmp_path, monkeypatch):
    monkeypatch.setattr('sentinela.monitor.fetch_observation', lambda *args: Observation(Decimal('80'), Decimal('100')))
    monkeypatch.setattr('sentinela.monitor.send_candidates', lambda *args: pytest.fail('Enviou em simulação'))
    path = tmp_path / 'db.sqlite3'
    summary = run('unused', str(path), 'unused', Decimal('.2'), 5, 7, 15, 'test', products=[product()], publish_results=True, dry_run=True)
    assert summary.selected == 1
    assert summary.published == 0
    assert not path.exists()


def test_candidate_message_labels_and_confirmation():
    item = product()
    candidates = [Candidate(item, Observation(Decimal('80'), Decimal('100'), condition='No Pix'), 'advertised', Decimal('.2'), Decimal('100'))]
    session = Mock()
    session.post.return_value.status_code = 200
    delivered = []
    assert send_candidates(session, 'unused', candidates, 15, 'status', delivered.extend) == 1
    assert delivered == candidates
    description = session.post.call_args.kwargs['json']['embeds'][0]['description']
    assert 'Desconto anunciado' in description
    assert 'Ainda não confirmado pelo histórico' in description
    assert 'No Pix' in description
    assert item.url in description
    session.post.return_value.status_code = 302
    delivered.clear()
    with pytest.raises(requests.RequestException):
        send_candidates(session, 'unused', candidates, 15, 'status', delivered.extend)
    assert delivered == []


def test_specification_from_unavailable_offer_is_not_used_as_original():
    html = '''<script type="application/ld+json">{"@type":"Product","url":"https://example.org/p","offers":[{"@type":"Offer","price":"80","priceCurrency":"BRL"},{"@type":"Offer","price":"80","priceCurrency":"BRL","availability":"https://schema.org/OutOfStock","priceSpecification":{"priceType":"https://schema.org/StrikethroughPrice","price":"500","priceCurrency":"BRL"}}]}</script>'''
    assert extract_observation(html, 'https://example.org/p').original_price is None


def test_tuttomoto_only_uses_main_price_with_matching_canonical():
    structured = '''<script type="application/ld+json">{"@type":"Product","url":"https://www.tuttomoto.com.br/produto/jaqueta","offers":{"@type":"Offer","price":"80","priceCurrency":"BRL"}}</script>'''
    markup = structured + '''<link rel="canonical" href="https://www.tuttomoto.com.br/produto/jaqueta"><div class="summary"><p class="price"><del><span class="woocommerce-Price-amount">R$ 100,00</span></del></p></div><div class="related"><del><span class="woocommerce-Price-amount">R$ 999,00</span></del></div>'''
    assert extract_observation(markup, 'https://www.tuttomoto.com.br/produto/jaqueta').original_price == 100
    mismatch = markup.replace('rel="canonical" href="https://www.tuttomoto.com.br/produto/jaqueta"', 'rel="canonical" href="https://www.tuttomoto.com.br/produto/other"')
    assert extract_observation(mismatch, 'https://www.tuttomoto.com.br/produto/jaqueta').original_price is None
