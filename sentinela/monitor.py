"""Orquestra coleta, avaliação e publicação; não registra segredos em logs."""
import logging
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import requests
from .config import Product, load_products, validate_limits
from .extraction import extract_price, is_unavailable
from .money import brl
from .notifications import send_discord
from .storage import assess, connect, mark_alerted, record, was_alerted

LOG = logging.getLogger(__name__)


@dataclass
class RunSummary:
    products: int = 0
    collected: int = 0
    failed: int = 0
    unavailable: int = 0
    alerts_sent: int = 0
    alerts_failed: int = 0

    @property
    def exit_code(self):
        return 1 if not self.products or self.failed or self.alerts_failed else 0


class ProductUnavailable(Exception):
    """A página identificada declara todas as ofertas indisponíveis."""


def fetch_price(session: requests.Session, product: Product, timeout: int, user_agent: str) -> Decimal | None:
    try:
        response = session.get(product.url, timeout=timeout, headers={"User-Agent": user_agent, "Accept": "text/html"})
        response.raise_for_status()
        price = extract_price(response.text, product.url)
        if price is None and is_unavailable(response.text, product.url):
            raise ProductUnavailable
        return price
    except (requests.RequestException, ValueError) as exc:
        LOG.warning("Falha na coleta de %s (%s)", product.id, type(exc).__name__)
        return None


def run(products_file: str, db_path: str, webhook: str | None, min_discount: Decimal, min_samples: int, min_days: int, timeout: int, user_agent: str, dry_run: bool = False, products: list[Product] | None = None) -> RunSummary:
    validate_limits(min_discount, min_samples, min_days, timeout)
    products = load_products(products_file) if products is None else products
    summary = RunSummary(products=len(products))
    if not products:
        LOG.warning("Nenhum produto habilitado em %s. Configure URLs reais de produtos.", products_file)
        return summary
    now = datetime.now(timezone.utc)
    with closing(connect(db_path, dry_run=dry_run)) as db, requests.Session() as session:
        for product in products:
            try:
                price = fetch_price(session, product, timeout, user_agent)
            except ProductUnavailable:
                summary.unavailable += 1
                LOG.info("%s: ofertas declaradas indisponíveis; ignorado", product.id)
                continue
            if price is None:
                summary.failed += 1
                LOG.warning("%s: preço BRL do produto não identificado; ignorado", product.id)
                continue
            summary.collected += 1
            within_budget = ((product.min_price_brl is None or price >= product.min_price_brl)
                             and (product.max_price_brl is None or price <= product.max_price_brl))
            result = assess(db, product.id, price, now, min_discount, min_samples, min_days) if within_budget else None
            if not within_budget:
                LOG.info("%s: fora da faixa de preço configurada; sem alerta", product.id)
            LOG.info("%s: %s", product.id, brl(price))
            if result and not was_alerted(db, product.id, price):
                baseline, discount = result
                if dry_run:
                    LOG.info("SIMULAÇÃO: alerta %s (-%.1f%%)", product.id, float(discount * 100))
                elif not webhook:
                    LOG.warning("Oferta detectada, mas DISCORD_WEBHOOK_URL não foi definido")
                else:
                    try:
                        send_discord(session, webhook, product, price, baseline, discount, timeout)
                    except requests.RequestException as exc:
                        summary.alerts_failed += 1
                        LOG.error("Discord falhou (%s); nova tentativa em coleta futura se a oferta continuar elegível", type(exc).__name__)
                    else:
                        mark_alerted(db, product.id, price, now)
                        summary.alerts_sent += 1
                        LOG.info("Alerta publicado: %s", product.id)
            if not dry_run:
                record(db, product.id, price, now)
    LOG.info("Resumo: %d produtos, %d coletados, %d indisponíveis, %d falhas de coleta, %d alertas enviados, %d falhas de envio", summary.products, summary.collected, summary.unavailable, summary.failed, summary.alerts_sent, summary.alerts_failed)
    return summary
