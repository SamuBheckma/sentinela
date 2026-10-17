"""Modelos e validação da configuração antes de qualquer acesso à rede."""
import json
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

from .money import parse_money


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    category: str
    store: str
    url: str
    shipping_brl: Decimal | None = None
    min_price_brl: Decimal | None = None
    max_price_brl: Decimal | None = None


def valid_https(url) -> bool:
    if not isinstance(url, str) or any(c.isspace() for c in url):
        return False
    try:
        parsed = urlparse(url)
        return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password and parsed.port in (None, 443)
    except ValueError:
        return False


def load_products(path: str) -> list[Product]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("products"), list):
        raise ValueError("A configuração deve conter uma lista 'products'.")
    products, ids = [], set()
    for index, obj in enumerate(raw["products"], 1):
        if not isinstance(obj, dict) or not isinstance(obj.get("enabled", False), bool):
            raise ValueError(f"Produto {index}: objeto inválido ou enabled não booleano.")
        if not obj.get("enabled", False):
            continue
        for field in ("id", "name", "category", "store", "url"):
            if not isinstance(obj.get(field), str) or not obj[field].strip():
                raise ValueError(f"Produto {index}: campo obrigatório inválido: {field}.")
        if obj["id"] != obj["id"].strip():
            raise ValueError(f"Produto {index}: ID com espaços nas extremidades.")
        if obj["id"] in ids:
            raise ValueError(f"ID duplicado: {obj['id']}")
        ids.add(obj["id"])
        if obj["category"] not in {"tecnologia", "militar"}:
            raise ValueError(f"Produto {index}: categoria deve ser tecnologia ou militar.")
        if not valid_https(obj["url"]):
            raise ValueError(f"Produto {index}: URL HTTPS inválida.")
        shipping = obj.get("shipping_brl")
        parsed_shipping = None if shipping is None else parse_money(shipping, allow_zero=True)
        if shipping is not None and parsed_shipping is None:
            raise ValueError(f"Produto {index}: shipping_brl deve ser um valor não negativo ou null.")
        minimum, maximum = price_bounds(obj)
        products.append(Product(*(obj[k] for k in ("id", "name", "category", "store", "url")), parsed_shipping, minimum, maximum))
    return products


def price_bounds(obj):
    values = []
    for key in ("min_price_brl", "max_price_brl"):
        raw = obj.get(key)
        value = None if raw is None else parse_money(raw, allow_zero=True)
        if raw is not None and value is None:
            raise ValueError(f"{key} deve ser um valor monetário não negativo.")
        values.append(value)
    minimum, maximum = values
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("min_price_brl não pode superar max_price_brl.")
    return minimum, maximum


def validate_limits(min_discount: Decimal, min_samples: int, min_days: int, timeout: int):
    if not min_discount.is_finite() or not Decimal("0") < min_discount < Decimal("1"):
        raise ValueError("MIN_DISCOUNT deve estar entre 0 e 1, por exemplo 0.20.")
    if min_samples < 1 or min_days < 1 or timeout < 1:
        raise ValueError("MIN_SAMPLES, MIN_HISTORY_DAYS e REQUEST_TIMEOUT devem ser positivos.")


@dataclass(frozen=True)
class Settings:
    products_file: str = "config/products.json"
    db_path: str = "data/history.sqlite3"
    webhook: str | None = None
    min_discount: Decimal = Decimal("0.20")
    min_samples: int = 5
    min_days: int = 7
    timeout: int = 15
    user_agent: str = "SentinelaOfertas/0.1"

    @classmethod
    def from_env(cls):
        try:
            settings = cls(
                products_file=os.getenv("PRODUCTS_FILE", cls.products_file),
                db_path=os.getenv("DATABASE_PATH", cls.db_path),
                webhook=os.getenv("DISCORD_WEBHOOK_URL") or None,
                min_discount=Decimal(os.getenv("MIN_DISCOUNT", "0.20")),
                min_samples=int(os.getenv("MIN_SAMPLES", "5")),
                min_days=int(os.getenv("MIN_HISTORY_DAYS", "7")),
                timeout=int(os.getenv("REQUEST_TIMEOUT", "15")),
                user_agent=os.getenv("USER_AGENT", cls.user_agent),
            )
        except (ValueError, InvalidOperation) as exc:
            raise ValueError("Configuração numérica inválida no ambiente/.env.") from exc
        validate_limits(settings.min_discount, settings.min_samples, settings.min_days, settings.timeout)
        if not settings.products_file or not settings.db_path or not settings.user_agent.strip():
            raise ValueError("PRODUCTS_FILE, DATABASE_PATH e USER_AGENT não podem estar vazios.")
        if settings.webhook:
            parsed = urlparse(settings.webhook)
            if not valid_https(settings.webhook) or parsed.hostname not in {"discord.com", "discordapp.com"} or not parsed.path.startswith("/api/webhooks/") or len(parsed.path.strip("/").split("/")) != 4 or parsed.query or parsed.fragment:
                raise ValueError("DISCORD_WEBHOOK_URL deve ser uma URL HTTPS de webhook do Discord.")
        return settings
