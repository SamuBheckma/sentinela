"""Ofertas JSON-LD vinculadas ao Product da URL monitorada."""
import json
import re
from decimal import Decimal
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from .money import parse_money


def _typed(obj, kind):
    types = obj.get("@type", [])
    types = [types] if isinstance(types, str) else types
    return isinstance(types, list) and kind in types


def _nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from _nodes(item)
    elif isinstance(value, dict):
        yield value
        for item in value.values():
            if isinstance(item, (list, dict)):
                yield from _nodes(item)


def _url(value, base):
    if not isinstance(value, str):
        return None
    absolute = urldefrag(urljoin(base, value))[0]
    parts = urlsplit(absolute)
    # Na KaBuM, @id usa /produto/ID, enquanto o link inclui o título.
    if parts.hostname in {"www.kabum.com.br", "kabum.com.br"} and parts.scheme == "https":
        match = re.fullmatch(r"/produto/([0-9]+)(?:/.*)?", parts.path)
        if match:
            return "https://www.kabum.com.br/produto/" + match.group(1)
    host = parts.netloc.lower()
    if parts.hostname in {"www.forhonor.com.br", "forhonor.com.br"}:
        host = "forhonor.com.br"
    return urlunsplit((parts.scheme.lower(), host, parts.path.rstrip("/") or "/", parts.query, ""))


def _offer_prices(value):
    offers = value if isinstance(value, list) else [value]
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        availability = str(offer.get("availability", "")).rsplit("/", 1)[-1]
        if availability in {"OutOfStock", "SoldOut", "Discontinued", "PreOrder", "PreSale"}:
            continue
        if _typed(offer, "Offer") and offer.get("priceCurrency") == "BRL":
            price = parse_money(offer.get("price"))
            if price is not None:
                yield price
        elif _typed(offer, "AggregateOffer"):
            # Não interpretar lowPrice como uma oferta concreta disponível.
            yield from _offer_prices(offer.get("offers", []))


def _select_products(html: str, product_url: str | None = None) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    products = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        products.extend(node for node in _nodes(data) if _typed(node, "Product") and "offers" in node)
    if product_url:
        target = _url(product_url, product_url)
        # Loja Integrada usa microdados em vez de JSON-LD nesta fonte.
        if urlsplit(product_url).hostname == "atalaia-sao-paulo.lojaintegrada.com.br":
            products.extend(_microdata_products(soup, product_url))
        matches = []
        for product in products:
            identifiers = [product.get("url"), product.get("@id")]
            # Algumas lojas identificam o Product somente pela URL da sua Offer.
            if not any(isinstance(value, str) for value in identifiers):
                offers = product.get("offers", [])
                offers = offers if isinstance(offers, list) else [offers]
                links = {_url(offer["url"], product_url) for offer in offers if isinstance(offer, dict) and isinstance(offer.get("url"), str)}
                if links == {target}:
                    identifiers.append(product_url)
            if any(identifier is not None and _url(identifier, product_url) == target for identifier in identifiers):
                matches.append(product)
        # Sem identificação explícita não atribuímos preços à URL monitorada.
        if not matches:
            return []
        products = matches
    elif len(products) != 1:
        return []
    return products


def extract_price(html: str, product_url: str | None = None) -> Decimal | None:
    products = _select_products(html, product_url)
    prices = [price for product in products for price in _offer_prices(product["offers"])]
    return min(prices) if prices else None


def is_unavailable(html: str, product_url: str) -> bool:
    products = _select_products(html, product_url)
    offers = [node for product in products for node in _nodes(product["offers"]) if _typed(node, "Offer")]
    return bool(offers) and all(str(offer.get("availability", "")).rsplit("/", 1)[-1] in {"OutOfStock", "SoldOut", "Discontinued", "PreOrder", "PreSale"} for offer in offers)


def _microdata_products(soup, product_url):
    canonical = soup.find("link", rel="canonical")
    if not canonical or _url(canonical.get("href"), product_url) != _url(product_url, product_url):
        return []
    scopes = [node for node in soup.select("[itemscope][itemtype]") if node.get("itemtype", "").rstrip("/").endswith("/Product")]
    if len(scopes) != 1:
        return []
    offers = []
    for scope in scopes[0].select('[itemprop="offers"][itemscope]'):
        if not scope.get("itemtype", "").rstrip("/").endswith("/Offer"):
            continue
        offer = {"@type": "Offer"}
        for key in ("price", "priceCurrency", "availability", "url"):
            node = scope.find(attrs={"itemprop": key})
            if node:
                offer[key] = node.get("content") or node.get("href") or node.get_text(strip=True)
        offers.append(offer)
    return [{"@type": "Product", "url": canonical.get("href"), "offers": offers}]
