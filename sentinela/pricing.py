"""Preços observados e referências anunciadas, separados do histórico próprio."""
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .extraction import _offer_prices, _select_products, _url, extract_price
from .money import parse_money


@dataclass(frozen=True)
class Observation:
    price: Decimal
    original_price: Decimal | None = None
    source: str = "product_page"
    condition: str | None = None

    def __post_init__(self):
        if parse_money(self.price) is None:
            raise ValueError("Preço observado inválido.")
        if self.original_price is not None and (parse_money(self.original_price) is None or self.original_price <= self.price):
            raise ValueError("Preço anterior anunciado deve superar o atual.")

    @property
    def advertised_discount(self) -> Decimal | None:
        if self.original_price is None:
            return None
        return (self.original_price - self.price) / self.original_price


def extract_observation(html: str, url: str) -> Observation | None:
    current = extract_price(html, url)
    if current is None:
        return None
    original = None
    # Somente preço riscado explicitamente identificado; nunca lowPrice/highPrice.
    for product in _select_products(html, url):
        offers = product["offers"]
        offers = offers if isinstance(offers, list) else [offers]
        for offer in offers:
            if not isinstance(offer, dict) or current not in list(_offer_prices(offer)):
                continue
            specs = offer.get("priceSpecification", [])
            specs = specs if isinstance(specs, list) else [specs]
            for spec in specs:
                if not isinstance(spec, dict) or str(spec.get("priceType", "")).rsplit("/", 1)[-1] != "StrikethroughPrice":
                    continue
                if spec.get("priceCurrency", offer.get("priceCurrency")) != "BRL":
                    continue
                value = parse_money(spec.get("price"))
                if value is not None and value > current:
                    original = value if original is None else min(original, value)
    # WooCommerce: preço riscado apenas no resumo principal, com URL canônica.
    if urlsplit(url).hostname in {"www.tuttomoto.com.br", "tuttomoto.com.br"}:
        soup = BeautifulSoup(html, "html.parser")
        canonical = soup.find("link", rel="canonical")
        if canonical and _url(canonical.get("href"), url) == _url(url, url):
            values = [parse_money(node.get_text(" ", strip=True)) for node in soup.select('div.summary p.price del .woocommerce-Price-amount')]
            values = [value for value in values if value is not None and value > current]
            if len(set(values)) == 1:
                original = values[0]
    return Observation(current, original)
