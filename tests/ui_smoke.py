"""Exercise real Tk screens in an isolated workspace with a fake printer.

Run explicitly: uv run python tests/ui_smoke.py
This opens the application briefly and never talks to a real printer.
"""
from pathlib import Path
import os
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ARTIFACTS = ROOT / ".test-data"
ARTIFACTS.mkdir(exist_ok=True)
temporary = tempfile.TemporaryDirectory(prefix="ui-", dir=ARTIFACTS)
os.environ["RESTAURANT_BILLING_DATA_DIR"] = temporary.name
started = time.perf_counter()

import main
import printer
from app_config import image_path, import_image, load_config
from PIL import Image, ImageGrab
from settings_screen import DishEditor, SettingsScreen
from sales_screen import SalesScreen, DatePicker, BillDetail

app = main.BillingApp()
app.geometry("1320x690+0+0")
callback_errors = []
app.report_callback_exception = lambda typ, value, tb: callback_errors.append(str(value))
app.update()
print(f"STARTUP_SECONDS={time.perf_counter() - started:.3f}", flush=True)


def pump(seconds=0.15):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        app.update()
        time.sleep(0.01)


def screenshot(window, filename):
    window.update_idletasks()
    pump(0.25)
    # Capture only our test window, even if another application covers it.
    ImageGrab.grab(window=window.winfo_id()).save(ARTIFACTS / filename)


try:
    dishes = app.db.list_dishes()
    assert len(dishes) == 9
    karahi = next(d for d in dishes if d["name"] == "Chicken Karahi")
    naan = next(d for d in dishes if d["name"] == "Naan")
    app.add_item(karahi, 0.0004)
    app.add_item(karahi)
    assert app.cart[0]["qty"] == 1.0004
    app.clear_cart(ask=False)
    app.add_item(karahi)
    first_item = app.cart[0]
    first_row = app._cart_rows[id(first_item)]["frame"]
    app.add_item(karahi)
    assert app.cart[0]["qty"] == 2
    assert app._cart_rows[id(first_item)]["frame"] is first_row and first_row.winfo_exists()
    app.set_qty(app.cart[0], 1.5)
    app.add_item(naan, 3)
    assert app._cart_rows[id(first_item)]["frame"] is first_row
    app.discount_value.set("10")
    app.discount_mode.set("%")
    assert app.totals()["total"] == 1390.5
    app.discount_value.set("nan")
    assert app.print_btn.cget("state") == "disabled"
    app.update_idletasks()
    assert app.validation.winfo_ismapped()
    app.discount_value.set("10")
    screenshot(app, "billing-dark.png")
    app.toggle_theme()
    assert load_config()["theme"] == "light"
    screenshot(app, "billing-light.png")
    app.toggle_theme()

    editor = DishEditor(app)
    editor.name.set("Test dish چکن")
    editor.price.set("321.50")
    editor.sell_type.set("Weight (kg)")
    source = ARTIFACTS / "sample-photo.png"
    Image.new("RGB", (160, 160), "#dca752").save(source)
    editor.selected_photo = str(source)
    editor.save()
    saved_dish = next(d for d in app.db.list_dishes() if d["name"] == "Test dish چکن")
    assert image_path(saved_dish["image_path"]).exists()
    assert saved_dish["sell_type"] == "kg"
    editor = DishEditor(app, saved_dish)
    editor.remove_photo()
    editor.save()
    assert next(d for d in app.db.list_dishes() if d["id"] == saved_dish["id"])["image_path"] == ""

    app.open_settings()
    settings = app._settings
    pump()
    settings.withdraw()
    app.open_settings()
    pump(0.25)
    assert app._settings is settings and settings.winfo_viewable()
    settings.tabs.set("General")
    settings._tab_changed()
    settings.vars["restaurant_name"].set("Spicy & Taste")
    settings.vars["tax_percent"].set("5")
    settings.vars["delivery_fee"].set("125")
    settings._change_delivery_fee(10)
    assert settings._delivery_fee() == 135
    settings._change_delivery_fee(-10)
    settings.layout_vars["show_tax"].set(True)
    settings.logo_path = import_image(source)
    settings.save()
    assert app.config["restaurant_name"] == "Spicy & Taste"
    assert app.config["tax_percent"] == 5
    assert app.config["delivery_fee"] == 125
    assert float(app.delivery_value.get()) == 125
    assert not app.delivery_enabled.get()
    assert load_config()["logo_path"] == settings.logo_path
    app.update_idletasks()
    assert app.logo_label.winfo_ismapped() and app._header_logo is not None
    screenshot(settings, "settings-general.png")
    settings.tabs.set("Bill Layout")
    settings._tab_changed()
    screenshot(settings, "settings-layout.png")
    settings.destroy()
    app._settings = None
    screenshot(app, "billing-header-logo.png")

    # Delivery can be enabled, adjusted, typed, disabled, and saved independently.
    app._select_order_type("Delivery")
    assert app.delivery_enabled.get() and app.totals()["total"] == 1585.03
    app.change_delivery_fee(10)
    assert app.totals()["delivery_fee"] == 135
    app.change_delivery_fee(-10)
    app.delivery_value.set("150")
    assert app.totals()["total"] == 1610.03
    app.delivery_enabled.set(False)
    app._toggle_delivery()
    assert app.order_type.get() == "Dine-in" and app.totals()["total"] == 1460.03
    app.delivery_enabled.set(True)
    app._toggle_delivery()
    app.delivery_value.set("nan")
    assert app.print_btn.cget("state") == "disabled"
    app.delivery_value.set("125")
    app.toggle_theme()
    app.clear_btn._on_enter()
    screenshot(app, "billing-light-delivery.png")
    app.clear_btn._on_leave()
    app.toggle_theme()

    # A successful checkout creates exactly one saved immutable bill.
    captured = []
    app._select_order_type("Delivery")
    app.customer_var.set("Ali")
    app.phone_var.set("03001234567")
    with patch.object(app, "print_saved_bill", side_effect=lambda bill, **kwargs: captured.append(bill)):
        app.checkout()
    assert len(captured) == 1 and not app.cart
    bill = captured[0]
    assert bill["order_info"]["customer_name"] == "Ali"
    assert bill["total"] == 1585.03 and bill["delivery_fee"] == 125
    assert bill["tax"] == 77.25 and bill["discount"] == 162.22
    assert not app.delivery_enabled.get()
    assert "Delivery:" in printer.receipt_text(bill, app.config)
    printer.render_receipt(bill, app.config).save(ARTIFACTS / "receipt-delivery.png")
    assert app.db.daily_summary()["count"] == 1

    # Hardware failure preserves the sale; reprint never inserts another sale.
    messages = []
    with patch("printer.print_bill", side_effect=printer.PrinterError("Test printer unavailable")), patch("main.error", side_effect=lambda *args: messages.append(args)):
        app.reprint_last()
        pump(0.5)
    assert not app.printing and messages
    assert app.db.daily_summary()["count"] == 1
    with patch("printer.print_bill") as fake:
        app.reprint_last()
        pump(0.5)
        assert fake.call_count == 1
    assert app.db.daily_summary()["count"] == 1

    app.open_sales()
    screenshot(app._sales, "sales.png")
    detail = BillDetail(app._sales, bill)
    pump()
    detail.destroy()
    calendar = DatePicker(app._sales, app._sales.selected_day, app._sales.set_day)
    calendar.shift(-1)
    pump()
    calendar.destroy()
    app._sales.destroy()
    app._sales = None

    app.show_receipt(bill)
    pump()
    for widget in app.winfo_children():
        if isinstance(widget, main.ReceiptPreview):
            widget.destroy()
    with patch("main.confirm", return_value=True):
        app.delete_last()
    assert app.db.daily_summary()["count"] == 0
    assert app.db.get_bill(bill["id"])["status"] == "void"
    with patch("main.error") as popup, patch("printer.print_bill") as fake:
        app.print_saved_bill(bill)
        assert popup.call_count == 1
        fake.assert_not_called()
    assert not callback_errors, callback_errors
    print("UI_SMOKE_OK: billing, quantities, discount, themes, photo lifecycle, settings, checkout, print failure/retry, sales/calendar, preview and void", flush=True)
finally:
    app.db.close()
    app.destroy()
    temporary.cleanup()
