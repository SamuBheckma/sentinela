"""Seleção independente: relevância, diversidade de lojas e intervalo de repetição."""
import os
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from .config import Product
from .pricing import Observation
from .storage import assess, last_publication, recently_published


@dataclass(frozen=True)
class PublicationPolicy:
    max_posts: int = 12
    repeat_days: int = 7
    min_advertised_discount: Decimal = Decimal("0.10")
    min_price_change: Decimal = Decimal("0.01")

    @classmethod
    def from_env(cls):
        try:
            policy = cls(int(os.getenv("MAX_POSTS_PER_RUN", "12")), int(os.getenv("REPEAT_AFTER_DAYS", "7")), Decimal(os.getenv("MIN_ADVERTISED_DISCOUNT", "0.10")), Decimal(os.getenv("MIN_PRICE_CHANGE", "0.01")))
        except (ValueError, InvalidOperation) as exc:
            raise ValueError("Política de publicação inválida.") from exc
        if not 1 <= policy.max_posts <= 50 or not 1 <= policy.repeat_days <= 365:
            raise ValueError("MAX_POSTS_PER_RUN deve ser 1–50 e REPEAT_AFTER_DAYS 1–365.")
        for value in (policy.min_advertised_discount, policy.min_price_change):
            if not value.is_finite() or not Decimal("0") < value < Decimal("1"):
                raise ValueError("Limiares de desconto devem ser frações entre 0 e 1.")
        return policy


@dataclass(frozen=True)
class Candidate:
    product: Product
    observation: Observation
    kind: str
    discount: Decimal = Decimal("0")
    reference: Decimal | None = None

    @property
    def priority(self):
        return {"historical": 3, "advertised": 2, "price_drop": 1, "new": 0}[self.kind]


def candidate_for(db, product: Product, observation: Observation, now: datetime, policy: PublicationPolicy, min_discount: Decimal, min_samples: int, min_days: int) -> Candidate | None:
    price = observation.price
    if recently_published(db, product.id, price, now, policy.repeat_days):
        return None
    historical = assess(db, product.id, price, now, min_discount, min_samples, min_days)
    if historical:
        return Candidate(product, observation, "historical", historical[1], historical[0])
    advertised = observation.advertised_discount
    if advertised is not None and advertised >= policy.min_advertised_discount:
        return Candidate(product, observation, "advertised", advertised, observation.original_price)
    previous = last_publication(db, product.id)
    if previous is None:
        return Candidate(product, observation, "new")
    reference = Decimal(previous[0]) / 100
    discount = (reference - price) / reference
    if discount >= policy.min_price_change:
        return Candidate(product, observation, "price_drop", discount, reference)
    return None


def select_candidates(candidates: list[Candidate], maximum: int) -> list[Candidate]:
    selected, ids, families = [], set(), set()
    # Descontos primeiro; em cada grupo, uma loja por vez.
    for priority in (3, 2, 1, 0):
        buckets = defaultdict(list)
        for candidate in candidates:
            if candidate.priority == priority:
                buckets[candidate.product.store].append(candidate)
        queues = []
        for bucket in buckets.values():
            bucket.sort(key=lambda c: (-c.discount, c.observation.price, c.product.id))
            queues.append(deque(bucket))
        queues.sort(key=lambda queue: (-queue[0].discount, queue[0].product.store))
        while any(queues) and len(selected) < maximum:
            for queue in queues:
                while queue:
                    candidate = queue.popleft()
                    product = candidate.product
                    url = urlsplit(product.url)
                    family = (product.store, url.path.rstrip('/'))
                    if product.id not in ids and family not in families:
                        ids.add(product.id)
                        families.add(family)
                        selected.append(candidate)
                        break
                if len(selected) >= maximum:
                    break
        if len(selected) >= maximum:
            break
    return selected
