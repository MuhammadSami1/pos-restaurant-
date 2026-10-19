"""Exercise portion editing, billing, and saved receipts using isolated data."""
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
temporary = tempfile.TemporaryDirectory(prefix="portions-", dir=ARTIFACTS)
os.environ["RESTAURANT_BILLING_DATA_DIR"] = temporary.name

import customtkinter as ctk
from PIL import ImageGrab
from db import Database
from main import BillingApp, QuantityDialog
from printer import receipt_text
from sales_screen import SalesScreen, BillDetail
from settings_screen import DishEditor

app = BillingApp()
app.geometry("1320x690+0+0")
errors = []
app.report_callback_exception = lambda typ, value, tb: errors.append(str(value))


def pump():
    until = time.monotonic() + 0.2
    while time.monotonic() < until:
        app.update()
        time.sleep(0.01)


def capture(window, filename):
    pump()
    ImageGrab.grab(window=window.winfo_id()).save(ARTIFACTS / filename)


def buttons(widget):
    for child in widget.winfo_children():
        if isinstance(child, ctk.CTkButton):
            yield child
        yield from buttons(child)


try:
    editor = DishEditor(app)
    editor.name.set("Portion Karahi")
    editor.sell_type.set("Weight (kg)")
    editor.price.set("1000")
    assert float(editor.portion_price_vars["0.25"].get()) == 250
    for key, value in {"0.25": 300, "0.5": 550, "0.75": 850}.items():
        editor.portion_price_vars[key].set(str(value))
    editor.sell_type.set("Count")
    editor.sell_type.set("Weight (kg)")
    pump()
    assert all(field.winfo_ismapped() for field in editor.portion_price_fields.values())
    capture(editor, "portion-editor-kg-dark.png")
    app.toggle_theme()
    capture(editor, "portion-editor-kg-light.png")
    assert editor.winfo_viewable()
    app.toggle_theme()
    editor.save()
    kg = next(d for d in app.db.list_dishes() if d["name"] == "Portion Karahi")
    assert kg["portion_prices"] == {"0.25": 300, "0.5": 550, "0.75": 850, "1": 1000}

    editor = DishEditor(app)
    editor.name.set("Portion Biryani")
    editor.portion_price_vars["0.5"].set("180")
    editor.price.set("300")
    assert float(editor.portion_price_vars["0.5"].get()) == 180
    assert float(editor.portion_price_vars["0.25"].get()) == 75
    pump()
    assert not editor.portion_price_fields["0.25"].winfo_ismapped()
    assert not editor.portion_price_fields["0.75"].winfo_ismapped()
    editor.portion_price_vars["0.5"].set("")
    with patch("settings_screen.error") as invalid:
        editor.save()
        assert invalid.call_count == 1 and editor.winfo_exists()
    editor.portion_price_vars["0.5"].set("180")
    capture(editor, "portion-editor-count.png")
    editor.save()
    count = next(d for d in app.db.list_dishes() if d["name"] == "Portion Biryani")
    assert count["portion_prices"] == {"0.5": 180, "1": 300}

    app.add_item(kg)
    line = app.cart[0]
    row = app._cart_rows[id(line)]["frame"]
    app.change_qty(line, -1)
    assert (line["qty"], line["amount"]) == (0.75, 850)
    app.change_qty(line, -1)
    assert (line["qty"], line["amount"]) == (0.5, 550)
    app.change_qty(line, -1)
    assert (line["qty"], line["amount"]) == (0.25, 300)
    app.change_qty(line, 1)
    assert (line["qty"], line["amount"]) == (0.5, 550)
    app.set_qty(line, 1.75)
    assert line["amount"] == 1850
    assert app._cart_rows[id(line)]["frame"] is row

    dialog = QuantityDialog(app, line, lambda qty: app.set_qty(line, qty), 1.75, editing=True)
    preset = next(b for b in buttons(dialog) if b.cget("text") == "0.75")
    preset.invoke()
    dialog.step(1)
    assert float(dialog.value.get()) == 1
    preset.invoke()
    capture(dialog, "portion-quantity-kg.png")
    dialog.submit()
    assert (line["qty"], line["amount"]) == (0.75, 850)

    app.add_item(count)
    count_line = app.cart[1]
    app.change_qty(count_line, 1)
    assert (count_line["qty"], count_line["amount"]) == (1.5, 480)
    app.change_qty(count_line, -1)
    assert (count_line["qty"], count_line["amount"]) == (1, 300)
    dialog = QuantityDialog(app, count_line, lambda qty: app.set_qty(count_line, qty), 1, editing=True)
    next(b for b in buttons(dialog) if b.cget("text") == "0.5").invoke()
    capture(dialog, "portion-quantity-count.png")
    dialog.submit()
    assert count_line["amount"] == 180
    assert app.totals()["total"] == 1030
    capture(app, "portion-current-bill.png")

    captured = []
    with patch.object(app, "print_saved_bill", side_effect=lambda bill, **kwargs: captured.append(bill)):
        app.checkout()
    bill = captured[0]
    assert bill["total"] == 1030 and not app.cart
    original_receipt = receipt_text(bill, app.config)
    assert "0.75 kg Portion Karahi" in original_receipt
    assert "0.5x Portion Biryani" in original_receipt
    assert "Rs. 1,030" in original_receipt

    # Changing only a fraction price must not merge new items into an old line.
    app.add_item(kg, 0.5)
    kg["portion_prices"]["0.5"] = 600
    app.db.save_dish(kg)
    changed = next(d for d in app.db.list_dishes() if d["id"] == kg["id"])
    app.add_item(changed, 0.5)
    assert [i["amount"] for i in app.cart] == [550, 600]
    app.clear_cart(ask=False)
    app.add_item(count, 0.5)
    app.change_qty(app.cart[0], -1)
    assert not app.cart
    app.add_item(kg, 0.25)
    app.change_qty(app.cart[0], -1)
    assert not app.cart

    app.db.close()
    app.db = Database()
    restored = app.db.get_bill(bill["id"])
    assert restored["items"][0]["portion_prices"]["0.5"] == 550
    assert receipt_text(restored, app.config) == original_receipt
    editor = DishEditor(app, next(d for d in app.db.list_dishes() if d["id"] == kg["id"]))
    assert float(editor.portion_price_vars["0.5"].get()) == 600
    editor.destroy()
    sales = SalesScreen(app)
    detail = BillDetail(sales, restored)
    pump()
    detail.destroy()
    sales.destroy()
    assert not errors, errors
    print("PORTION_UI_OK: price fields, presets, steps, totals, persistence, snapshots, receipts, and sales")
finally:
    app.db.close()
    app.destroy()
    temporary.cleanup()
