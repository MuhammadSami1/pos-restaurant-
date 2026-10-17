"""Writable runtime paths, safe image imports, and atomic owner settings."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from models import amount, number


PROJECT_DIR = Path(__file__).resolve().parent
_override = os.environ.get("RESTAURANT_BILLING_DATA_DIR")
if _override:
    DATA_DIR = Path(_override).expanduser().resolve()
elif os.name == "nt" and os.environ.get("LOCALAPPDATA"):
    DATA_DIR = Path(os.environ["LOCALAPPDATA"]) / "restaurant-billing"
else:
    DATA_DIR = PROJECT_DIR
IMAGE_DIR = DATA_DIR / "images"
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULT_CONFIG = {
    "restaurant_name": "Al-Madina Restaurant",
    "logo_path": "",
    "address": "Main Bazaar Road",
    "phone": "03xx-xxxxxxx",
    "footer": "Thank you! Phir tashreef layen",
    "bill_layout": {
        "show_logo": True, "show_name": True, "show_address": True,
        "show_phone": True, "show_bill_no": True, "show_datetime": True,
        "show_subtotal": True, "show_discount": True, "show_tax": False,
        "show_order_type": False, "show_table_waiter": False,
        "show_customer": False, "show_footer": True,
        "promo_lines": [], "promo_position": "above_total",
    },
    "theme": "dark", "tax_percent": 0, "delivery_fee": 0, "paper_width": 80,
    "printer_mode": "usb", "usb_vid": "0x0483", "usb_pid": "0x5740",
    "printer_driver_name": "POS80 Printer",
}


def image_path(relative: str) -> Path:
    """Resolve only managed image paths; do not allow paths outside images/."""
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError("Choose an image first.")
    relative_path = Path(relative.replace("\\", "/"))
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError("Images must be stored in the app's images folder.")
    candidate = (DATA_DIR / relative_path).resolve()
    try:
        candidate.relative_to(IMAGE_DIR.resolve())
    except ValueError as exc:
        raise ValueError("Images must be stored in the app's images folder.") from exc
    return candidate


def import_image(source: str | Path) -> str:
    from PIL import Image, UnidentifiedImageError

    source_path = Path(source)
    if source_path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
        raise ValueError("Choose a JPG or PNG image.")
    try:
        with Image.open(source_path) as photo:
            if photo.format not in ("JPEG", "PNG"):
                raise ValueError("Choose a JPG or PNG image.")
            photo.verify()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("This image is damaged or too large. Choose another JPG or PNG.") from exc
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    relative = f"images/{uuid4().hex}{source_path.suffix.lower()}"
    target = image_path(relative)
    staging = target.with_suffix(target.suffix + ".tmp")
    try:
        shutil.copyfile(source_path, staging)
        os.replace(staging, target)
    finally:
        staging.unlink(missing_ok=True)
    return relative


def _validated(config: dict) -> dict:
    if not isinstance(config, dict):
        raise ValueError("Settings must be an object.")
    result = copy.deepcopy(DEFAULT_CONFIG)
    result.update({key: value for key, value in config.items() if key != "bill_layout"})
    layout = config.get("bill_layout", {})
    if not isinstance(layout, dict):
        raise ValueError("Bill layout settings must be an object.")
    result["bill_layout"].update(layout)
    for key in ("restaurant_name", "address", "phone", "footer", "logo_path",
                "printer_driver_name", "usb_vid", "usb_pid"):
        if not isinstance(result[key], str):
            raise ValueError(f"{key.replace('_', ' ').capitalize()} must be text.")
        result[key] = result[key].strip()
    if not result["restaurant_name"]:
        raise ValueError("Enter a restaurant name.")
    if result["logo_path"]:
        image_path(result["logo_path"])
    if result["theme"] not in ("dark", "light"):
        raise ValueError("Theme must be dark or light.")
    tax = number(result["tax_percent"], "Tax percentage")
    if not 0 <= tax <= 100:
        raise ValueError("Tax percentage must be between 0 and 100.")
    result["tax_percent"] = round(tax, 2)
    result["delivery_fee"] = amount(result["delivery_fee"], "Delivery charge")
    if result["paper_width"] not in (58, 80, "58", "80"):
        raise ValueError("Paper width must be 58 or 80 mm.")
    result["paper_width"] = int(result["paper_width"])
    if result["printer_mode"] not in ("usb", "win32"):
        raise ValueError("Select USB or Windows printer mode.")
    for key in ("usb_vid", "usb_pid"):
        try:
            value = int(result[key], 16 if result[key].lower().startswith("0x") else 10)
        except ValueError as exc:
            raise ValueError("USB IDs must be decimal or hexadecimal, for example 0x0483.") from exc
        if not 0 <= value <= 65535:
            raise ValueError("USB IDs must be between 0 and 65535.")
    for key, default in DEFAULT_CONFIG["bill_layout"].items():
        if isinstance(default, bool) and not isinstance(result["bill_layout"][key], bool):
            raise ValueError("Bill layout switches must be on or off.")
    promos = result["bill_layout"]["promo_lines"]
    if not isinstance(promos, list) or len(promos) > 2 or any(not isinstance(p, str) for p in promos):
        raise ValueError("Enter at most two promo lines.")
    result["bill_layout"]["promo_lines"] = [line.strip() for line in promos if line.strip()]
    if result["bill_layout"]["promo_position"] not in ("above_total", "below_total"):
        raise ValueError("Choose above or below the total for promo lines.")
    return result


def save_config(config: dict) -> dict:
    result = _validated(config)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".tmp",
                                         prefix="config-", dir=DATA_DIR, delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, CONFIG_PATH)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return result


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return save_config(DEFAULT_CONFIG)
    try:
        with CONFIG_PATH.open(encoding="utf-8") as handle:
            return _validated(json.load(handle))
    except (ValueError, TypeError, UnicodeError):
        # Preserve a damaged manual edit for recovery and restore printable defaults.
        backup = CONFIG_PATH.with_name(f"config.damaged-{uuid4().hex[:8]}.json")
        shutil.copyfile(CONFIG_PATH, backup)
        return save_config(DEFAULT_CONFIG)
