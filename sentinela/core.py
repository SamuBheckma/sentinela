"""Compatibilidade com as funções públicas anteriores; implementação por responsabilidade."""
from .config import Product, load_products
from .extraction import extract_price
from .money import brl, cents, parse_money
from .monitor import RunSummary, fetch_price, run
from .notifications import send_discord
from .storage import assess, connect, mark_alerted, record, was_alerted

__all__ = [
    "Product", "RunSummary", "load_products", "extract_price", "parse_money",
    "brl", "cents", "fetch_price", "run", "send_discord", "assess", "connect",
    "mark_alerted", "record", "was_alerted",
]
