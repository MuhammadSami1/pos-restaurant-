"""Exercise credit checkout and ledger screens with isolated data and fake printing."""
from pathlib import Path
import copy
import os
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ARTIFACTS = ROOT / ".test-data"
ARTIFACTS.mkdir(exist_ok=True)
temporary = tempfile.TemporaryDirectory(prefix="khata-ui-", dir=ARTIFACTS)
os.environ["RESTAURANT_BILLING_DATA_DIR"] = temporary.name

from PIL import ImageGrab
import main
import printer
from db import Database
from models import money
from sales_screen import BillDetail

app = main.BillingApp()
app.geometry("1320x690+0+0")
callback_errors = []
app.report_callback_exception = lambda typ, value, tb: callback_errors.append(str(value))


def pump(seconds=0.2):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        app.update()
        time.sleep(0.01)


def capture(window, filename):
    pump()
    ImageGrab.grab(window=window.winfo_id()).save(ARTIFACTS / filename)


def order_state():
    return (copy.deepcopy(app.cart), app.discount_value.get(), app.discount_mode.get(),
            app.order_type.get(), app.table_var.get(), app.waiter_var.get(),
            app.customer_var.get(), app.phone_var.get(), app.delivery_enabled.get(),
            app.delivery_value.get(), app.udhaar_enabled.get(), app._delivery_dirty)


def credit_page(dish, qty=1):
    app.add_item(dish, qty)
    app.udhaar_enabled.set(True)
    app.checkout()
    assert app._customer_page is not None
    return app._customer_page


try:
    naan = next(d for d in app.db.list_dishes() if d["name"] == "Naan")
    karahi = next(d for d in app.db.list_dishes() if d["name"] == "Chicken Karahi")
    app.open_khata()
    ledger = app._khata
    assert ledger.total_label.cget("text") == "Total Khata: Rs. 0"
    assert ledger.selected_customer_id is None
    ledger.destroy()
    app._khata = None

    app.add_item(karahi, 0.75)
    app.add_item(naan, 0.5)
    app.config["tax_percent"] = 16
    app.discount_mode.set("%")
    app.discount_value.set("10")
    app._select_order_type("Delivery")
    app.delivery_value.set("150")
    app.customer_var.set("Delivery contact")
    app.phone_var.set("0300-delivery")
    app.udhaar_enabled.set(True)
    assert app.print_btn.cget("text") == "SAVE TO KHATA  ↵"
    initial = order_state()
    total = app.totals()["total"]
    row = app._cart_rows[id(app.cart[0])]["frame"]
    capture(app, "khata-billing-dark.png")
    app.checkout()
    page = app._customer_page
    app.checkout()
    assert app._customer_page is page
    page.name_var.set("Unsaved customer")
    page.phone_var.set("123")
    app.toggle_theme()
    capture(page, "khata-customer-light.png")
    assert page.winfo_viewable()
    page.cancel()
    assert order_state() == initial
    assert app._cart_rows[id(app.cart[0])]["frame"] is row
    assert app.db.list_customers() == [] and app.db.daily_summary()["count"] == 0
    app.toggle_theme()

    captured = []
    with patch.object(app, "print_saved_bill", side_effect=lambda bill, **kwargs: captured.append(bill)):
        app.checkout()
        page = app._customer_page
        page.name_var.set("Ahmed")
        page.phone_var.set("03001234567")
        capture(page, "khata-customer-new.png")
        page.save_new()
        assert not page.winfo_exists() and not app.cart
        first = captured[-1]
        ahmed = app.db.find_customer_by_name("ahmad")
        assert first["customer_id"] == ahmed["id"] and first["bill_no"] == 1
        assert first["total"] == total and ahmed["balance"] == total
        assert not app.udhaar_enabled.get() and app.print_btn.cget("text") == "PRINT BILL  ↵"
        assert "UDHAAR - Ahmed" in printer.receipt_text(first, app.config)
        assert app.db.daily_summary()["total"] == total
        assert app._attach_khata(customer_id=ahmed["id"]) is False

        app.config["tax_percent"] = 0
        app.add_item(naan)
        app.checkout()
        assert captured[-1]["bill_no"] == 2 and captured[-1]["customer_id"] is None
        page = credit_page(naan, 0.5)
        page.name_var.set("  aHMAD  ")
        assert page.problem.cget("text") == "This name already exists"
        assert page.save_button.cget("state") == "disabled"
        capture(page, "khata-duplicate.png")
        page.duplicate_button.invoke()
        assert captured[-1]["bill_no"] == 3 and captured[-1]["customer_id"] == ahmed["id"]
        assert len(app.db.list_customers()) == 1

        page = credit_page(naan)
        page.name_var.set("Zara")
        page.phone_var.set("03335550000")
        page.save_new()
        zara_bill = captured[-1]
        page = credit_page(naan)
        page.tabs.set("Existing Customer")
        page.search_var.set("0300123")
        assert len(page.customer_list.winfo_children()) == 1
        capture(page, "khata-existing.png")
        page.select_existing(ahmed["id"])
        last_ahmed = captured[-1]
        page.select_existing(ahmed["id"])
        assert app.db.daily_summary()["count"] == 5
        assert last_ahmed["bill_no"] == 5 and last_ahmed["customer_id"] == ahmed["id"]

    app.open_sales()
    sales = app._sales
    app.open_khata()
    ledger = app._khata
    assert ledger.selected_customer_id == ahmed["id"]
    assert ledger.total_label.cget("text") == f"Total Khata: {money(total + 100)}"
    assert len(ledger.bill_list.winfo_children()) == 3
    capture(ledger, "khata-ledger-dark.png")
    ledger.search_var.set("zara")
    assert ledger.selected_customer_id == zara_bill["customer_id"]
    assert ledger.total_label.cget("text") == f"Total Khata: {money(total + 100)}"
    app.toggle_theme()
    capture(ledger, "khata-ledger-light.png")
    ledger.search_var.set("")
    ledger.select_customer(ahmed["id"])
    detail = BillDetail(ledger, last_ahmed)
    capture(detail, "khata-bill-detail.png")
    with patch("khata_screen.confirm", return_value=False):
        ledger.void(last_ahmed, detail)
    assert app.db.get_bill(last_ahmed["id"])["status"] == "active"
    with patch("khata_screen.confirm", return_value=True):
        ledger.void(last_ahmed, detail)
    assert app.db.get_customer(ahmed["id"])["balance"] == total + 20
    assert ledger.total_label.cget("text") == f"Total Khata: {money(total + 60)}"
    assert sales.count_label.cget("text") == "4 bills"
    with patch("sales_screen.confirm", return_value=True):
        sales.void(zara_bill)
    assert ledger.total_label.cget("text") == f"Total Khata: {money(total + 20)}"
    with patch("main.confirm", return_value=True):
        app.delete_last()
    assert ledger.total_label.cget("text") == f"Total Khata: {money(total)}"
    assert app.db.daily_summary()["count"] == 2

    page = credit_page(naan)
    page.name_var.set("Failed save")
    before_failure = order_state()
    with patch.object(app.db, "save_bill", side_effect=RuntimeError("Simulated save failure")):
        page.save_new()
    assert page.winfo_exists() and "Simulated save failure" in page.problem.cget("text")
    assert order_state() == before_failure and len(app.db.list_customers()) == 2
    app.printing = True
    page.save_new()
    assert "Wait for the current receipt" in page.problem.cget("text")
    assert order_state() == before_failure
    app.printing = False
    page.cancel()
    app.clear_cart(ask=False)

    # A hardware failure leaves one credit sale, and retry never duplicates it.
    page = credit_page(naan)
    page.name_var.set("Printer failure customer")
    notices = []
    with patch("printer.print_bill", side_effect=printer.PrinterError("Simulated offline printer")), patch("main.error", side_effect=lambda *a: notices.append(a)):
        page.save_new()
        pump(0.6)
    assert notices and not app.printing and not app.cart
    failure_bill = app.db.last_bill()
    assert failure_bill["customer_id"] is not None
    assert app.db.khata_total() == total + 40
    with patch("printer.print_bill") as fake:
        app.reprint_last()
        pump(0.5)
        assert fake.call_count == 1
    assert app.db.khata_total() == total + 40
    assert app.db.daily_summary()["count"] == 3

    app.db.close()
    app.db = Database()
    app.refresh_sales()
    assert app.db.khata_total() == total + 40
    assert app.db.get_bill(first["id"])["order_info"]["khata_customer_name"] == "Ahmed"
    assert not callback_errors, callback_errors
    print("KHATA_UI_OK: untouched cancel, duplicate/existing/new flows, auto print, shared sales, all void paths, failure/retry, persistence, themes")
finally:
    app.db.close()
    app.destroy()
    temporary.cleanup()
