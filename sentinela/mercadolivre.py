"""Adaptador próprio da vitrine pública de ofertas; sessão opcional do usuário."""
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from .config import Product
from .money import parse_money
from .pricing import Observation

LOG = logging.getLogger(__name__)
URL = 'https://www.mercadolivre.com.br/ofertas'


class MarketplaceUnavailable(Exception):
    def __init__(self, status):
        self.status = status
        self.products = []
        self.observations = {}
        super().__init__(status)


def _amount(node):
    if node is None:
        return None
    symbol = node.select_one('.andes-money-amount__currency-symbol')
    if symbol is None or symbol.get_text(strip=True) != 'R$':
        return None
    fraction = node.select_one('.andes-money-amount__fraction')
    if fraction is None:
        return None
    whole = fraction.get_text(strip=True)
    if not re.fullmatch(r'(?:[0-9]{1,3}(?:\.[0-9]{3})+|[0-9]+)', whole):
        return None
    cents = node.select_one('.andes-money-amount__cents')
    decimal = cents.get_text(strip=True) if cents else '00'
    if not re.fullmatch(r'[0-9]{2}', decimal):
        return None
    return parse_money(whole.replace('.', '') + '.' + decimal)


def parse_offers(html, searches):
    from .discovery import _identity, _matches
    soup = BeautifulSoup(html, 'html.parser')
    products, observations = {}, {}
    for card in soup.select('div.poly-card, li.ui-search-layout__item'):
        title = card.select_one('a.poly-component__title, a.ui-search-item__group__element')
        if title is None or not title.get('href'):
            continue
        name = title.get_text(' ', strip=True)
        search = next((s for s in searches if _matches(name, s)), None)
        if search is None:
            continue
        identity = _identity(urljoin(URL, title['href']), 'mercadolivre')
        if identity is None:
            continue
        current = _amount(card.select_one('.poly-price__current .andes-money-amount, .poly-price__current'))
        if current is None:
            continue
        original = _amount(card.select_one('s.andes-money-amount--previous'))
        if original is not None and original <= current:
            original = None
        condition = card.select_one('.poly-price__unit-description')
        product_id, product_url = identity
        products.setdefault(product_id, Product(product_id, name[:300], search.category, 'Mercado Livre', product_url, min_price_brl=search.min_price_brl, max_price_brl=search.max_price_brl))
        observations.setdefault(product_id, Observation(current, original, 'listing_page', condition.get_text(' ', strip=True)[:120] if condition else None))
    return list(products.values()), observations


def browser_name():
    name = os.getenv('ML_BROWSER', 'firefox').strip().lower()
    if name not in {'firefox', 'chromium'}:
        raise ValueError('ML_BROWSER deve ser firefox ou chromium.')
    return name


def _launch_browser(playwright, *, headless):
    return getattr(playwright, browser_name()).launch(headless=headless)


def session_path():
    return Path(os.getenv('ML_STORAGE_STATE_FILE', '.secrets/ml_state.json'))


def _browser_html(url, timeout):
    path = session_path()
    if not path.is_file():
        raise MarketplaceUnavailable('requires_login')
    try:
        state = json.loads(path.read_text())
        if not isinstance(state, dict) or not isinstance(state.get('cookies'), list) or not isinstance(state.get('origins', []), list):
            raise ValueError('invalid_state')
    except (ValueError, OSError):
        raise MarketplaceUnavailable('invalid_session') from None
    try:
        from playwright.sync_api import sync_playwright, Error
    except ImportError:
        raise MarketplaceUnavailable('browser_not_installed') from None
    try:
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright, headless=True)
            try:
                context = browser.new_context(storage_state=state, locale='pt-BR')
                page = context.new_page()
                page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
                if _requires_login(page.url):
                    raise MarketplaceUnavailable('requires_login')
                page.locator('div.poly-card').first.wait_for(state='attached', timeout=min(timeout * 1000, 10000))
                return page.content()
            finally:
                browser.close()
    except Error:
        raise MarketplaceUnavailable('browser_access_failed') from None


def _requires_login(url):
    parts = urlsplit(url)
    return parts.hostname not in {'www.mercadolivre.com.br', 'mercadolivre.com.br'} or any(part in parts.path for part in ('account-verification', '/login', '/registration', '/captcha'))


def fetch_offers(session, searches, pages, categories, timeout, user_agent):
    products, observations = {}, {}
    fingerprints = set()
    try:
        for category in categories or ('',):
            for page in range(1, pages + 1):
                params = {'page': page}
                if category:
                    params['category'] = category
                url = URL + '?' + urlencode(params)
                if session_path().is_file():
                    html = _browser_html(url, timeout)
                else:
                    try:
                        response = session.get(url, timeout=timeout, headers={'User-Agent': user_agent, 'Accept': 'text/html'})
                        response.raise_for_status()
                    except requests.RequestException:
                        raise MarketplaceUnavailable('http_error') from None
                    if _requires_login(response.url):
                        raise MarketplaceUnavailable('requires_login')
                    html = response.text
                soup = BeautifulSoup(html, 'html.parser')
                if not soup.select('div.poly-card, li.ui-search-layout__item'):
                    if any(message in soup.get_text(' ', strip=True).lower() for message in ('nenhum resultado', 'nenhuma oferta')):
                        break
                    raise MarketplaceUnavailable('unrecognized_page')
                found, prices = parse_offers(html, searches)
                # Pare se a loja ignorar a paginação; não gere observações duplicadas.
                fingerprint = tuple(sorted(a.get('href', '') for a in soup.select('a.poly-component__title')))
                if fingerprint and fingerprint in fingerprints:
                    break
                if fingerprint:
                    fingerprints.add(fingerprint)
                for product in found:
                    products.setdefault(product.id, product)
                    observations.setdefault(product.id, prices[product.id])
    except MarketplaceUnavailable as exc:
        exc.products = list(products.values())
        exc.observations = observations
        raise
    return list(products.values()), observations


def login():
    from .native_login import login as native_login
    return native_login(destination=session_path())
