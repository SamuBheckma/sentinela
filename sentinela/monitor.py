"""Orquestra coleta, avaliação e publicação; não registra segredos em logs."""
import logging
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import requests
from .config import Product, load_products, validate_limits
from .extraction import is_unavailable
from .pricing import Observation, extract_observation
from .selection import PublicationPolicy, candidate_for, select_candidates
from .money import brl
from .notifications import send_discord, send_search_results, send_candidates
from .storage import assess, connect, mark_alerted, mark_published, record, was_alerted

LOG = logging.getLogger(__name__)


@dataclass
class RunSummary:
    products: int = 0
    collected: int = 0
    failed: int = 0
    unavailable: int = 0
    alerts_sent: int = 0
    alerts_failed: int = 0
    result_messages: int = 0
    result_failures: int = 0
    published: int = 0
    eligible: int = 0
    selected: int = 0

    @property
    def exit_code(self):
        return 1 if not self.products or self.failed or self.alerts_failed or self.result_failures else 0


class ProductUnavailable(Exception):
    """A página identificada declara todas as ofertas indisponíveis."""


def fetch_observation(session: requests.Session, product: Product, timeout: int, user_agent: str) -> Observation | None:
    try:
        response = session.get(product.url, timeout=timeout, headers={"User-Agent": user_agent, "Accept": "text/html"})
        response.raise_for_status()
        observation = extract_observation(response.text, product.url)
        if observation is None and is_unavailable(response.text, product.url):
            raise ProductUnavailable
        return observation
    except (requests.RequestException, ValueError) as exc:
        LOG.warning("Falha na coleta de %s (%s)", product.id, type(exc).__name__)
        return None


def fetch_price(session: requests.Session, product: Product, timeout: int, user_agent: str) -> Decimal | None:
    observation = fetch_observation(session, product, timeout, user_agent)
    return observation.price if observation else None


def run(products_file: str, db_path: str, webhook: str | None, min_discount: Decimal, min_samples: int, min_days: int, timeout: int, user_agent: str, dry_run: bool = False, products: list[Product] | None = None, publish_results: bool = False, policy: PublicationPolicy | None = None, observations: dict[str, Observation] | None = None, report_all: bool = False, source_status: dict[str, str] | None = None) -> RunSummary:
    validate_limits(min_discount, min_samples, min_days, timeout)
    products = load_products(products_file) if products is None else products
    summary = RunSummary(products=len(products))
    if not products:
        LOG.warning("Nenhum produto habilitado em %s. Configure URLs reais de produtos.", products_file)
        return summary
    now = datetime.now(timezone.utc)
    results, candidates = [], []
    policy = policy or PublicationPolicy.from_env()
    observations = observations or {}
    with closing(connect(db_path, dry_run=dry_run)) as db, requests.Session() as session:
        for product in products:
            try:
                observation = observations.get(product.id) or fetch_observation(session, product, timeout, user_agent)
            except ProductUnavailable:
                summary.unavailable += 1
                LOG.info("%s: ofertas declaradas indisponíveis; ignorado", product.id)
                continue
            if observation is None:
                summary.failed += 1
                LOG.warning("%s: preço BRL do produto não identificado; ignorado", product.id)
                continue
            price = observation.price
            summary.collected += 1
            within_budget = ((product.min_price_brl is None or price >= product.min_price_brl)
                             and (product.max_price_brl is None or price <= product.max_price_brl))
            if within_budget:
                results.append((product, price))
                if publish_results and not report_all:
                    candidate = candidate_for(db, product, observation, now, policy, min_discount, min_samples, min_days)
                    if candidate:
                        candidates.append(candidate)
            result = assess(db, product.id, price, now, min_discount, min_samples, min_days) if within_budget else None
            if not within_budget:
                LOG.info("%s: fora da faixa de preço configurada; sem alerta", product.id)
            LOG.info("%s: %s", product.id, brl(price))
            if not publish_results and result and not was_alerted(db, product.id, price):
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
        selected = select_candidates(candidates, policy.max_posts)
        summary.eligible, summary.selected = len(candidates), len(selected)
        if publish_results:
            status = f"{summary.collected} preços coletados; {summary.selected} selecionados; {summary.unavailable} indisponíveis; {summary.failed} falhas"
            source_labels = {'requires_login': 'requer login', 'invalid_session': 'sessão inválida', 'browser_not_installed': 'navegador não instalado', 'browser_access_failed': 'acesso pelo navegador falhou', 'http_error': 'acesso indisponível', 'unrecognized_page': 'página não reconhecida', 'no_matching_offers': 'sem ofertas correspondentes'}
            for source, state in (source_status or {}).items():
                if state != 'ok':
                    status += f" | {source}: {source_labels.get(state, state)}"
            if dry_run:
                for candidate in selected:
                    LOG.info("SIMULAÇÃO: %s | %s | %s", candidate.kind, candidate.product.id, brl(candidate.observation.price))
            elif not selected and not report_all:
                LOG.info("Nenhuma novidade ou mudança elegível; publicação dispensada")
            elif not webhook:
                LOG.warning("Seleção não enviada: DISCORD_WEBHOOK_URL não definido")
            else:
                def delivered(page):
                    for candidate in page:
                        mark_published(db, candidate.product.id, candidate.observation.price, candidate.kind, now)
                        summary.published += 1
                        if candidate.kind == 'historical':
                            mark_alerted(db, candidate.product.id, candidate.observation.price, now)
                            summary.alerts_sent += 1
                try:
                    if report_all:
                        summary.result_messages = send_search_results(session, webhook, results, timeout, status)
                    else:
                        summary.result_messages = send_candidates(session, webhook, selected, timeout, status, delivered)
                    LOG.info("Seleção publicada no Discord: %d mensagens; %d produtos registrados", summary.result_messages, summary.published)
                except (requests.RequestException, ValueError) as exc:
                    summary.result_failures += 1
                    LOG.error("Publicação falhou no Discord (%s); lotes confirmados foram preservados", type(exc).__name__)
    LOG.info("Resumo: %d produtos, %d coletados, %d indisponíveis, %d falhas de coleta, %d alertas enviados, %d falhas de envio", summary.products, summary.collected, summary.unavailable, summary.failed, summary.alerts_sent, summary.alerts_failed)
    return summary
