"""Conversão explícita de valores monetários, sem remover sinais ou letras."""
import re
from decimal import Decimal, InvalidOperation


def parse_money(value, *, allow_zero: bool = False) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("R$"):
            text = text[2:].strip()
        if re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{3})+,[0-9]{1,2}", text):
            text = text.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"[0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{1,2}", text):
            text = text.replace(",", "")
        elif re.fullmatch(r"[0-9]+(?:[.,][0-9]{1,2})?", text):
            text = text.replace(",", ".")
        else:
            return None
    elif isinstance(value, (int, float, Decimal)):
        text = str(value)
    else:
        return None
    try:
        result = Decimal(text)
        if not result.is_finite() or result < 0 or (result == 0 and not allow_zero):
            return None
        if result != result.quantize(Decimal("0.01")):
            return None
    except InvalidOperation:
        return None
    return result


def cents(price: Decimal) -> int:
    return int((price * 100).quantize(Decimal("1")))


def brl(value: Decimal) -> str:
    return "R$ " + f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
