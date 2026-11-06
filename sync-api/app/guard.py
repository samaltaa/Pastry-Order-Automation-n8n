"""Checks every price and stock change before it reaches Shopify.

A typo in a spreadsheet ($4.50 typed as $450, 15 typed as 1500) is the most likely way this system
could hurt a shop, so changes that look like one are held: the store keeps its old value, the change
is listed on the demo page and sent to the owner, to approve or decline. These rules catch the
extreme cases even when Claude is unavailable; Claude reviews whatever gets past them (app/llm.py).
Ordinary edits, and stock going down (sales), pass straight through.
"""

from decimal import Decimal, InvalidOperation

PRICE_MIN = Decimal("0.25")
PRICE_MAX = Decimal("500")
PRICE_JUMP = Decimal("0.5")  # at most 50% up or down at once
STOCK_MAX = 9999
STOCK_JUMP = 500  # at most this many units added at once


def check(product: dict, item: dict | None, released: dict[str, str]) -> list[dict]:
    """Holds for this product as {"field", "old", "new", "reason"}. item is what was last synced; a
    value the owner released passes once."""
    holds = []
    price = _decimal(product.get("price"))
    old_price = _decimal(item.get("last_price")) if item else None
    if price is not None and released.get("price") != str(price):
        if not PRICE_MIN <= price <= PRICE_MAX:
            holds.append(_hold("price", old_price, price, f"outside ${PRICE_MIN}-${PRICE_MAX}"))
        elif old_price and abs(price - old_price) / old_price > PRICE_JUMP:
            holds.append(_hold("price", old_price, price, f"a {abs(price - old_price) / old_price:.0%} jump"))

    quantity = product.get("quantity")
    old_quantity = item.get("last_quantity") if item else None
    if quantity is not None and released.get("quantity") != str(quantity):
        if not 0 <= quantity <= STOCK_MAX:
            holds.append(_hold("quantity", old_quantity, quantity, f"outside 0-{STOCK_MAX}"))
        elif old_quantity is not None and quantity - old_quantity > STOCK_JUMP:
            holds.append(
                _hold("quantity", old_quantity, quantity, f"+{quantity - old_quantity} units at once")
            )
    return holds


def changes(product: dict, item: dict | None) -> list[dict]:
    """The price and stock this product would change, as {"field", "old", "new"}."""
    found = []
    price = _decimal(product.get("price"))
    old_price = _decimal(item.get("last_price")) if item else None
    if price is not None and (item is None or (old_price is not None and price != old_price)):
        found.append(
            {"field": "price", "old": None if old_price is None else str(old_price), "new": str(price)}
        )
    quantity = product.get("quantity")
    old_quantity = item.get("last_quantity") if item else None
    if quantity is not None and quantity != old_quantity:
        found.append(
            {
                "field": "quantity",
                "old": None if old_quantity is None else str(old_quantity),
                "new": str(quantity),
            }
        )
    return found


def _hold(field: str, old, new, reason: str) -> dict:
    return {"field": field, "old": None if old is None else str(old), "new": str(new), "reason": reason}


def _decimal(value) -> Decimal | None:
    try:
        return None if value in (None, "") else Decimal(str(value))
    except InvalidOperation:
        return None
