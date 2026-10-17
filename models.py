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
    if unit == "count" and not qty.is_integer():
        raise ValueError("Count dishes need a whole-number quantity.")
    return qty


def quantity_text(qty: Any, unit: str) -> str:
    value = validate_quantity(qty, unit)
    rendered = format(value, ".10f").rstrip("0").rstrip(".")
    return f"{rendered} kg" if unit == "kg" else rendered


def normalize_items(items: Iterable[dict]) -> list[dict]:
    """Freeze dish details and calculate every line from its rate and quantity."""
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
        line_amount = amount(Decimal(str(qty)) * Decimal(str(price)), "Line amount")
        line = {"name": name, "qty": qty, "unit": unit,
                "price": price, "amount": line_amount}
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
