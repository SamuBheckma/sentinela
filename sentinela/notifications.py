"""Publicação de ofertas no Discord."""
import requests
from decimal import Decimal
from .config import Product
from .money import brl


def send_discord(session: requests.Session, webhook: str, product: Product, price: Decimal, baseline: Decimal, discount: Decimal, timeout: int):
    suffix = f" | Frete estimado: {brl(product.shipping_brl)}" if product.shipping_brl is not None else " | Frete não verificado"
    description = (f"**{product.name}**\nPreço observado: **{brl(price)}**\n"
                   f"Mediana histórica: {brl(baseline)}\nQueda: **{discount:.0%}**{suffix}\n"
                   f"Loja: {product.store} | Categoria: {product.category}\n{product.url}\n"
                   "⚠️ Confira preço final, estoque, frete e vendedor antes de comprar.")
    r = session.post(webhook, json={"allowed_mentions": {"parse": []}, "embeds": [{"title": "🔎 Sentinela encontrou uma possível oferta", "description": description[:4096]}]}, timeout=timeout, allow_redirects=False)
    r.raise_for_status()
    if not 200 <= r.status_code < 300:
        raise requests.RequestException("Discord retornou uma resposta sem confirmação de sucesso.")


def send_search_results(session: requests.Session, webhook: str, results: list[tuple[Product, Decimal]], timeout: int, status: str) -> int:
    """Publica todos os resultados elegíveis sem apresentá-los como descontos."""
    import re

    pages = []
    current = []
    size = 0
    for product, price in results:
        name = re.sub(r"([*_`~\\])", r"\\\1", product.name[:140]).replace("\n", " ")
        text = f"**{name}** — {brl(price)}\n{product.store}\n<{product.url}>"
        if len(text) > 3500:
            raise ValueError("URL de produto longa demais para o resumo do Discord.")
        if current and size + len(text) + 2 > 3500:
            pages.append("\n\n".join(current))
            current, size = [], 0
        current.append(text)
        size += len(text) + 2
    if current:
        pages.append("\n\n".join(current))
    if not pages:
        pages = ["Nenhum produto com preço utilizável dentro dos filtros nesta execução."]
    for index, page in enumerate(pages, 1):
        payload = {
            "allowed_mentions": {"parse": []},
            "embeds": [{
                "title": f"🔎 Sentinela — resultados da busca ({index}/{len(pages)})",
                "description": "Preços observados; desconto ainda não verificado.\n\n" + page,
                "footer": {"text": (status + " | Confira variante, pagamento, estoque e frete.")[:2048]},
            }],
        }
        response = session.post(webhook, params={"wait": "true"}, json=payload, timeout=timeout, allow_redirects=False)
        response.raise_for_status()
        if not 200 <= response.status_code < 300:
            raise requests.RequestException("Discord não confirmou o resumo da busca.")
    return len(pages)
