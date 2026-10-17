"""Descobre produtos em buscas públicas sem usar descontos anunciados como histórico."""
import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from .config import Product, load_products, price_bounds, valid_https
from .extraction import _nodes, _typed

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Store:
    name: str
    template: str
    hosts: tuple[str, ...]
    product_pattern: str


STORES = {
    "forhonor": Store("For Honor", "https://www.forhonor.com.br/search?q={term}&type=product", ("www.forhonor.com.br", "forhonor.com.br"), r"^/products/([a-z0-9-]+)(?:/|$)"),
    "atalaia": Store("Coturno Atalaia", "https://atalaia-sao-paulo.lojaintegrada.com.br/buscar?q={term}", ("atalaia-sao-paulo.lojaintegrada.com.br",), r"^/produto/([a-z0-9-]+)\.html$"),
    "tuttomoto": Store("Tutto Moto", "https://www.tuttomoto.com.br/?s={term}&post_type=product", ("www.tuttomoto.com.br", "tuttomoto.com.br"), r"^/produto/([a-z0-9-]+)(?:/|$)"),
    "kabum": Store("KaBuM!", "https://www.kabum.com.br/busca/{term}", ("www.kabum.com.br", "kabum.com.br"), r"^/produto/([0-9]+)(?:/|$)"),
    "terabyte": Store("TerabyteShop", "https://www.terabyteshop.com.br/busca?str={term}", ("www.terabyteshop.com.br", "terabyteshop.com.br"), r"^/produto/([0-9]+)(?:/|$)"),
    "mercadolivre": Store("Mercado Livre", "https://lista.mercadolivre.com.br/{term}", ("www.mercadolivre.com.br", "produto.mercadolivre.com.br"), r"(?:/p/(MLB[0-9]+)|/(MLB-[0-9]+)-)"),
    "amazon": Store("Amazon Brasil", "https://www.amazon.com.br/s?k={term}", ("www.amazon.com.br", "amazon.com.br"), r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:/|$)"),
}


@dataclass(frozen=True)
class Search:
    term: str
    category: str
    stores: tuple[str, ...]
    limit: int = 5
    exclude: tuple[str, ...] = ()
    min_price_brl: Decimal | None = None
    max_price_brl: Decimal | None = None


@dataclass(frozen=True)
class SearchConfig:
    searches: tuple[Search, ...]
    max_products: int = 30


def load_searches(path: str) -> SearchConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("searches"), list):
        raise ValueError("A configuração deve conter uma lista searches.")
    maximum = raw.get("max_products", 30)
    if type(maximum) is not int or not 1 <= maximum <= 100:
        raise ValueError("max_products deve estar entre 1 e 100.")
    searches = []
    for item in raw["searches"]:
        if not isinstance(item, dict) or type(item.get("enabled", True)) is not bool:
            raise ValueError("Busca inválida ou enabled não booleano.")
        if not item.get("enabled", True):
            continue
        term = item.get("term")
        category = item.get("category")
        stores = item.get("stores")
        limit = item.get("limit", 5)
        exclude = item.get("exclude", [])
        if not isinstance(term, str) or not term.strip() or len(term) > 100:
            raise ValueError("Cada busca exige term com até 100 caracteres.")
        if category not in {"tecnologia", "militar"}:
            raise ValueError("Categoria de busca inválida.")
        if not isinstance(stores, list) or not stores or any(not isinstance(store, str) or store not in STORES for store in stores):
            raise ValueError("Loja de busca inválida. Consulte os adaptadores no README.")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("limit deve estar entre 1 e 20.")
        if not isinstance(exclude, list) or any(not isinstance(value, str) or not value.strip() for value in exclude):
            raise ValueError("exclude deve ser uma lista de termos não vazios.")
        min_price, max_price = price_bounds(item)
        searches.append(Search(term.strip(), category, tuple(dict.fromkeys(stores)), limit, tuple(exclude), min_price, max_price))
    return SearchConfig(tuple(searches), maximum)


def _normalize(text):
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _matches(name, search):
    normalized = _normalize(name)
    # A loja pode retornar anúncios e recomendações fora do termo.
    words = set(normalized.split())
    return all(word in words for word in _normalize(search.term).split()) and not any(_normalize(term) in normalized for term in search.exclude)


def _identity(url, store_key):
    store = STORES[store_key]
    if not valid_https(url):
        return None
    parts = urlsplit(url)
    if parts.hostname not in store.hosts:
        return None
    match = re.search(store.product_pattern, parts.path)
    if not match:
        return None
    key = next(group for group in match.groups() if group)
    # URLs e IDs ficam estáveis mesmo que os parâmetros de publicidade mudem.
    if store_key == "amazon":
        path = "/dp/" + key
    elif store_key == "mercadolivre" and key.startswith("MLB") and "-" not in key:
        path = "/p/" + key
    else:
        path = parts.path.rstrip("/")
    query = ""
    if store_key == "forhonor":
        variant = parse_qs(parts.query).get("variant", [])
        if variant:
            if len(variant) != 1 or not variant[0].isdigit():
                return None
            key += "-" + variant[0]
            query = urlencode({"variant": variant[0]})
    canonical = urlunsplit(("https", parts.hostname, path, query, ""))
    return f"{store_key}-{key.lower()}", canonical


def extract_products(html: str, search_url: str, store_key: str, search: Search) -> list[Product]:
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _nodes(data):
            if not _typed(node, "Product") or not isinstance(node.get("name"), str):
                continue
            links = [node.get("url")]
            offers = node.get("offers", [])
            offers = offers if isinstance(offers, list) else [offers]
            links.extend(offer.get("url") for offer in offers if isinstance(offer, dict))
            candidates.extend((node["name"], link) for link in links if isinstance(link, str))
    for anchor in soup.find_all("a", href=True):
        name = anchor.get("title") or anchor.get("aria-label") or anchor.get_text(" ", strip=True)
        if not name:
            image = anchor.find("img", alt=True)
            name = image.get("alt", "") if image else ""
        candidates.append((name, anchor["href"]))
    products = {}
    for name, link in candidates:
        if not _matches(name, search):
            continue
        identity = _identity(urljoin(search_url, link), store_key)
        if identity:
            product_id, url = identity
            products.setdefault(product_id, Product(product_id, name[:300], search.category, STORES[store_key].name, url, min_price_brl=search.min_price_brl, max_price_brl=search.max_price_brl))
        if len(products) >= search.limit:
            break
    return list(products.values())


@dataclass
class DiscoverySummary:
    products: list[Product] = field(default_factory=list)
    attempted: int = 0
    failed: int = 0
    empty: int = 0


def discover(config: SearchConfig, session: requests.Session, timeout: int, user_agent: str) -> DiscoverySummary:
    summary = DiscoverySummary()
    products = {}
    for search in config.searches:
        for store_key in search.stores:
            summary.attempted += 1
            term = quote(search.term.replace(" ", "-") if store_key == "mercadolivre" else search.term, safe="")
            url = STORES[store_key].template.format(term=term)
            try:
                response = session.get(url, timeout=timeout, headers={"User-Agent": user_agent, "Accept": "text/html"})
                response.raise_for_status()
                found = extract_products(response.text, url, store_key, search)
                if not found:
                    markup = BeautifulSoup(response.text, "html.parser")
                    visible = any(_identity(urljoin(url, anchor["href"]), store_key) for anchor in markup.find_all("a", href=True))
                    text = _normalize(markup.get_text(" ", strip=True))
                    empty_message = any(message in text for message in ("nenhum resultado", "nenhum produto", "no results"))
                    if visible or empty_message:
                        summary.empty += 1
                        LOG.info("Busca '%s' em %s: nenhum título corresponde aos filtros", search.term, store_key)
                    else:
                        summary.failed += 1
                        LOG.warning("Busca '%s' em %s: página sem catálogo reconhecível ou bloqueada", search.term, store_key)
                else:
                    LOG.info("Busca '%s' em %s: %d produtos", search.term, store_key, len(found))
                for product in found:
                    if len(products) < config.max_products:
                        products.setdefault(product.id, product)
            except requests.RequestException as exc:
                summary.failed += 1
                LOG.warning("Busca '%s' em %s falhou (%s)", search.term, store_key, type(exc).__name__)
    summary.products = list(products.values())
    return summary


def update_catalog(path: str, found: list[Product], maximum: int, *, dry_run: bool = False, config: SearchConfig | None = None) -> list[Product]:
    target = Path(path)
    previous = load_products(path) if target.exists() else []
    if config is not None:
        active = []
        for product in previous:
            for search in config.searches:
                names = {STORES[key].name for key in search.stores}
                if product.store in names and _matches(product.name, search):
                    active.append(replace(product, category=search.category, min_price_brl=search.min_price_brl, max_price_brl=search.max_price_brl))
                    break
        previous = active
    # Resultados atuais têm prioridade; histórico de preços permanece no SQLite.
    products = {product.id: product for product in found}
    for product in previous:
        if len(products) >= maximum:
            break
        products.setdefault(product.id, product)
    selected = list(products.values())[:maximum]
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for product in selected:
            row = asdict(product)
            for key in ("shipping_brl", "min_price_brl", "max_price_brl"):
                value = getattr(product, key)
                row[key] = None if value is None else str(value)
            row["enabled"] = True
            rows.append(row)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps({"products": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
    return selected
