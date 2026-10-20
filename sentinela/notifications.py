"""Publicação de ofertas no Discord."""
import requests
from decimal import Decimal
from typing import Callable
from .config import Product
from .money import brl



# O bot pode publicar pela conexão discord.py; os webhooks continuam disponíveis
# para o monitor antigo durante a migração.
PayloadSender = Callable[[dict], None]


def _deliver(session, webhook, payload, timeout, *, params=None, publisher: PayloadSender | None = None):
    if publisher is not None:
        publisher(payload)
        return
    options = {"json": payload, "timeout": timeout, "allow_redirects": False}
    if params is not None:
        options["params"] = params
    response = session.post(webhook, **options)
    response.raise_for_status()
    if not 200 <= response.status_code < 300:
        raise requests.RequestException("Discord não confirmou a publicação.")


def send_discord(session: requests.Session, webhook: str, product: Product, price: Decimal, baseline: Decimal, discount: Decimal, timeout: int, *, publisher: PayloadSender | None = None):
    suffix = f" | Frete estimado: {brl(product.shipping_brl)}" if product.shipping_brl is not None else " | Frete não verificado"
    description = (f"**{product.name}**\nPreço observado: **{brl(price)}**\n"
                   f"Mediana histórica: {brl(baseline)}\nQueda: **{discount:.0%}**{suffix}\n"
                   f"Loja: {product.store} | Categoria: {product.category}\n{product.url}\n"
                   "⚠️ Confira preço final, estoque, frete e vendedor antes de comprar.")
    payload = {"allowed_mentions": {"parse": []}, "embeds": [{"title": "🔎 Sentinela encontrou uma possível oferta", "description": description[:4096]}]}
    _deliver(session, webhook, payload, timeout, publisher=publisher)


def send_search_results(session: requests.Session, webhook: str, results: list[tuple[Product, Decimal]], timeout: int, status: str, *, publisher: PayloadSender | None = None) -> int:
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
        _deliver(session, webhook, payload, timeout, params={"wait": "true"}, publisher=publisher)
    return len(pages)


def send_candidates(session, webhook, candidates, timeout, status, on_delivered, *, publisher: PayloadSender | None = None):
    """Confirma cada lote antes de registrar sua publicação, permitindo retentar falhas."""
    import re

    pages, current, size = [], [], 0
    for candidate in candidates:
        product, observation = candidate.product, candidate.observation
        name = re.sub(r"([*_`~\\])", r"\\\1", product.name[:140]).replace('\n', ' ')
        if candidate.kind == 'historical':
            label = f'Queda de {candidate.discount:.1%} frente à mediana histórica de {brl(candidate.reference)}.'
        elif candidate.kind == 'advertised':
            label = f'Desconto anunciado: {candidate.discount:.1%}; preço anterior informado pela loja: {brl(candidate.reference)}. Ainda não confirmado pelo histórico.'
        elif candidate.kind == 'price_drop':
            label = f'Preço {candidate.discount:.1%} abaixo da última publicação ({brl(candidate.reference)}); histórico completo ainda insuficiente.'
        else:
            label = 'Novo resultado; desconto não verificado.'
        condition = '\n' + observation.condition if observation.condition else ''
        text = f'**{name}** — **{brl(observation.price)}**\n{product.store} | {label}{condition}\n<{product.url}>'
        if len(text) > 3500:
            raise ValueError('Resultado longo demais para o Discord.')
        if current and size + len(text) + 2 > 3500:
            pages.append(current)
            current, size = [], 0
        current.append((candidate, text))
        size += len(text) + 2
    if current:
        pages.append(current)
    for index, page in enumerate(pages, 1):
        payload = {'allowed_mentions': {'parse': []}, 'embeds': [{
            'title': f'🔎 Sentinela — novidades e descontos ({index}/{len(pages)})',
            'description': '\n\n'.join(text for _, text in page),
            'footer': {'text': (status + ' | Confira pagamento, variante, vendedor e frete.')[:2048]},
        }]}
        _deliver(session, webhook, payload, timeout, params={'wait': 'true'}, publisher=publisher)
        on_delivered([candidate for candidate, _ in page])
    return len(pages)
