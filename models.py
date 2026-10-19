"""Small, shared validation and money helpers for billing and receipts."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any, Iterable


# Generous bounds catch accidental exponents before they reach SQLite or a slip.
MAX_AMOUNT = 999_999_999.99
MAX_QUANTITY = 1_000_000


def number(value: Any, label: str = "Value") -> float:
    """Read a finite numeric input without accepting booleans or blank text."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} must be a number.")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number.")
    return result


def amount(value: Any, label: str = "Amount") -> float:
    result = number(value, label)
    if result < 0:
        raise ValueError(f"{label} cannot be negative.")
    if result > MAX_AMOUNT:
        raise ValueError(f"{label} must not exceed Rs. 999,999,999.99.")
    exact = value if isinstance(value, Decimal) else Decimal(str(result))
    return float(round(exact, 2))


def cents(value: Any) -> int:
    """Use whole cents, rather than float equality, to compare money."""
    return int(round(Decimal(str(number(value))), 2) * 100)


def money(value: Any) -> str:
    rounded = round(Decimal(str(number(value, "Money"))), 2)
    if rounded == 0:
        rounded = 0.0
    rendered = f"{rounded:,.2f}"
    if rendered.endswith(".00"):
        rendered = rendered[:-3]
    return f"Rs. {rendered}"


def validate_quantity(value: Any, unit: str) -> float:
    if unit not in ("count", "kg"):
        raise ValueError("Selling type must be Count or Weight (kg).")
    qty = number(value, "Quantity")
    if qty <= 0:
        raise ValueError("Quantity must be greater than zero.")
    if qty > MAX_QUANTITY:
        raise ValueError("Quantity must not exceed 1,000,000.")
    if unit == "count" and Decimal(str(qty)) % Decimal("0.5"):
        raise ValueError("Count dishes need a quantity in steps of 0.5.")
    return qty


def portion_quantities(unit: str) -> tuple[float, ...]:
    if unit == "kg":
        return (0.25, 0.5, 0.75, 1)
    if unit == "count":
        return (0.5, 1)
    raise ValueError("Selling type must be Count or Weight (kg).")


def normalize_portion_prices(prices: Any, unit: str, price: Any) -> dict[str, float]:
    """Validate custom prices; an empty map keeps existing linear pricing."""
    keys = tuple(format(qty, "g") for qty in portion_quantities(unit))
    full_price = amount(price, "Price")
    if prices is None or prices == {}:
        return {}
    if not isinstance(prices, dict) or set(prices) != set(keys):
        labels = ", ".join(f"{key} kg" if unit == "kg" else key for key in keys)
        raise ValueError(f"Enter a price for every portion: {labels}.")
    result = {key: amount(prices[key], f"Price for {key}{' kg' if unit == 'kg' else ''}")
              for key in keys}
    # Keep the established price field authoritative for callers editing a dish.
    result["1"] = full_price
    return result


def item_amount(item: dict, qty: Any = None) -> float:
    """Price complete portions plus a remainder using this item's saved rates."""
    unit = item.get("unit", item.get("sell_type", "count"))
    quantity = validate_quantity(item.get("qty") if qty is None else qty, unit)
    price = amount(item.get("price"), "Price")
    prices = normalize_portion_prices(item.get("portion_prices"), unit, price)
    exact_quantity = Decimal(str(quantity))
    if not prices:
        return amount(exact_quantity * Decimal(str(price)), "Line amount")
    step = Decimal("0.25") if unit == "kg" else Decimal("0.5")
    if exact_quantity % step:
        raise ValueError("Dishes with portion prices need a quantity in steps of 0.25 kg.")
    whole = int(exact_quantity)
    remainder = exact_quantity - whole
    total = Decimal(whole) * Decimal(str(prices["1"]))
    if remainder:
        total += Decimal(str(prices[format(remainder.normalize(), "f")]))
    return amount(total, "Line amount")


def quantity_text(qty: Any, unit: str) -> str:
    value = validate_quantity(qty, unit)
    rendered = format(value, ".10f").rstrip("0").rstrip(".")
    return f"{rendered} kg" if unit == "kg" else rendered


def normalize_items(items: Iterable[dict]) -> list[dict]:
    """Freeze dish details and calculate every line from its saved portion rates."""
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each bill item must contain dish details.")
        name = str(item.get("name", "")).strip()
        if not name:
            raise ValueError("Every bill item needs a name.")
        unit = item.get("unit", item.get("sell_type", "count"))
        qty = validate_quantity(item.get("qty"), unit)
        price = amount(item.get("price"), "Price")
        prices = normalize_portion_prices(item.get("portion_prices"), unit, price)
        line_amount = item_amount(item, qty)
        line = {"name": name, "qty": qty, "unit": unit,
                "price": price, "amount": line_amount}
        if prices:
            line["portion_prices"] = prices
        if item.get("dish_id") is not None:
            line["dish_id"] = int(item["dish_id"])
        result.append(line)
    return result


def calculate_totals(items: Iterable[dict], discount_value: Any = 0,
                     discount_mode: str = "Rs.", tax_percent: Any = 0,
                     delivery_fee: Any = 0) -> dict:
    """Tax applies to food, then discount to food plus tax; delivery is added last."""
    normalized = normalize_items(items)
    subtotal = amount(sum(Decimal(str(item["amount"])) for item in normalized), "Subtotal")
    discount_input = number(discount_value, "Discount")
    if discount_input < 0:
        raise ValueError("Discount cannot be negative.")
    tax_rate = number(tax_percent, "Tax percentage")
    if not 0 <= tax_rate <= 100:
        raise ValueError("Tax percentage must be between 0 and 100.")
    tax = amount(Decimal(str(subtotal)) * Decimal(str(tax_rate)) / 100, "Tax")
    discount_base = Decimal(str(subtotal)) + Decimal(str(tax))
    if discount_mode == "%":
        if discount_input > 100:
            raise ValueError("Discount percentage cannot exceed 100.")
        discount = amount(discount_base * Decimal(str(discount_input)) / 100, "Discount")
    elif discount_mode in ("Rs.", "Rs", "amount"):
        discount = amount(discount_input, "Discount")
    else:
        raise ValueError("Choose Rs. or % for the discount.")
    if cents(discount) > cents(discount_base):
        raise ValueError("Discount cannot exceed food plus tax.")
    delivery = amount(delivery_fee, "Delivery charge")
    total = amount(discount_base - Decimal(str(discount)) + Decimal(str(delivery)), "Total")
    return {"subtotal": subtotal, "discount": discount, "tax": tax,
            "delivery_fee": delivery, "total": total}
