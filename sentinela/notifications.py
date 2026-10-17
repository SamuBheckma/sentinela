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
