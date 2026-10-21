"""Receipt safety/layout and transport tests; no physical printer required."""

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

import printer
from app_config import DEFAULT_CONFIG


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.bill = printer.test_bill()
        self.bill.pop("is_test")
        self.bill["bill_no"] = 12
        self.bill["created_at"] = "2026-10-13T21:41:00"

    def test_weight_count_currency_and_saved_totals(self):
        # Deliberately retain a historic total different from today's menu prices.
        self.bill["items"][0]["price"] = 9999
        text = printer.receipt_text(self.bill, self.config)
        self.assertIn("1.5 kg Chicken Karahi", text)
        self.assertIn("3x Naan", text)
        self.assertIn("Rs. 1,500", text)
        self.assertIn("13-Oct-2026  09:41 PM", text)
        self.assertNotIn("9,999", text)

    def test_fractional_portions_print_saved_amounts_on_both_paper_widths(self):
        self.bill["items"] = [
            {"name": "Chicken", "qty": 0.25, "unit": "kg", "price": 1000,
             "portion_prices": {"0.25": 300, "0.5": 550, "0.75": 800, "1": 1000}, "amount": 300},
            {"name": "Mutton", "qty": 0.75, "unit": "kg", "price": 1800,
             "portion_prices": {"0.25": 500, "0.5": 950, "0.75": 1400, "1": 1800}, "amount": 1400},
            {"name": "Biryani", "qty": 0.5, "unit": "count", "price": 250,
             "portion_prices": {"0.5": 150, "1": 250}, "amount": 150},
        ]
        self.bill.update(subtotal=1850, discount=0, tax=0, total=1850)
        # A reprint must use its stored amounts, even if pricing metadata differs.
        self.bill["items"][0]["portion_prices"]["0.25"] = 9999
        original = copy.deepcopy(self.bill)
        for width, columns in ((58, 32), (80, 48)):
            with self.subTest(paper_width=width):
                self.config["paper_width"] = width
                receipt = printer._receipt(self.bill, self.config)
                for prefix, amount in (("0.25 kg Chicken", "300"), ("0.75 kg Mutton", "1,400"),
                                       ("0.5x Biryani", "150")):
                    line = next(line for line in receipt.lines if line.left == prefix)
                    self.assertEqual(line.right, amount)
                text = printer.receipt_text(self.bill, self.config)
                self.assertIn("Rs. 1,850", text)
                self.assertNotIn("9,999", text)
                self.assertTrue(all(len(line.text(columns)) * line.width <= columns
                                    for line in receipt.lines))
        self.assertEqual(self.bill, original)

    def test_each_line_fits_both_paper_widths_including_large_numbers(self):
        self.bill["items"][0]["name"] = "Very long Chicken Karahi special family takeaway with extra spices " * 3
        self.bill["total"] = 123456789.75
        for width, columns in ((58, 32), (80, 48)):
            self.config["paper_width"] = width
            receipt = printer._receipt(self.bill, self.config)
            for line in receipt.lines:
                self.assertLessEqual(len(line.text(columns)) * line.width, columns)
            self.assertIn("123,456,789.75", printer.receipt_text(self.bill, self.config))

    def test_toggles_hide_optional_lines_but_never_items_and_total(self):
        self.config["bill_layout"] = {key: False for key, value in self.config["bill_layout"].items()
                                      if isinstance(value, bool)}
        text = printer.receipt_text(self.bill, self.config)
        self.assertNotIn("Al-Madina", text)
        self.assertNotIn("Bill #", text)
        self.assertNotIn("13-Oct", text)
        self.assertNotIn("Subtotal", text)
        self.assertNotIn("Discount", text)
        self.assertNotIn("Thank you", text)
        self.assertIn("Chicken Karahi", text)
        self.assertIn("TOTAL:", text)

    def test_khata_identity_prints_under_bill_number_even_with_optional_sections_off(self):
        self.bill["customer_id"] = 7
        self.bill["order_info"].update(khata_customer_name="Ahmed", khata_customer_phone="03001234567",
                                       customer_name="Delivery recipient")
        self.config["bill_layout"]["show_customer"] = False
        text = printer.receipt_text(self.bill, self.config)
        self.assertIn("Bill #12\nUDHAAR - Ahmed\n", text)
        self.assertNotIn("Delivery recipient", text)
        for key, value in self.config["bill_layout"].items():
            if isinstance(value, bool):
                self.config["bill_layout"][key] = False
        text = printer.receipt_text(self.bill, self.config)
        self.assertIn("UDHAAR - Ahmed", text)
        self.assertNotIn("Bill #", text)
        self.assertIn("Rs. 1,500", text)

    def test_khata_long_unicode_name_wraps_and_reprints_saved_identity_and_amounts(self):
        name = "احمد محمد " + "Long Customer Name " * 5
        self.bill["customer_id"] = 7
        self.bill["order_info"]["khata_customer_name"] = name
        self.bill["order_info"] = json.dumps(self.bill["order_info"], ensure_ascii=False)
        self.bill["customer_name"] = "Changed current customer name"
        self.bill["items"][0]["price"] = 9999
        original = copy.deepcopy(self.bill)
        for width, columns in ((58, 32), (80, 48)):
            with self.subTest(paper_width=width):
                self.config["paper_width"] = width
                receipt = printer._receipt(self.bill, self.config)
                text = printer.receipt_text(self.bill, self.config)
                self.assertIn("UDHAAR - احمد محمد", text)
                self.assertNotIn("Changed current", text)
                self.assertNotIn("9,999", text)
                self.assertIn("Rs. 1,500", text)
                self.assertTrue(all(len(line.text(columns)) * line.width <= columns
                                    for line in receipt.lines))
                self.assertEqual(printer.render_receipt(self.bill, self.config).width, columns * 12)
        self.assertEqual(self.bill, original)

    def test_paid_delivery_bill_is_never_marked_as_khata(self):
        self.bill["order_info"].update(customer_name="Ahmed", order_type="Delivery",
                                       khata_customer_name="Old unused metadata")
        self.config["bill_layout"]["show_customer"] = True
        for customer_id in ("missing", None):
            if customer_id is None:
                self.bill["customer_id"] = None
            text = printer.receipt_text(self.bill, self.config)
            self.assertIn("Customer: Ahmed", text)
            self.assertNotIn("UDHAAR", text)
            self.assertNotIn("Old unused metadata", text)

    def test_order_promos_tax_footer_and_json_items(self):
        self.config["bill_layout"].update({"show_order_type": True, "show_table_waiter": True,
            "show_customer": True, "show_tax": True, "promo_lines": ["Eid Mubarak", "Delivery available"],
            "promo_position": "below_total"})
        self.config["footer"] = "First footer line\nSecond footer line"
        self.bill["items"] = json.dumps(self.bill["items"])
        self.bill["order_info"].update(customer_name="Customer Example", customer_phone="0300-1234567")
        self.bill["order_info"] = json.dumps(self.bill["order_info"])
        self.bill["tax"] = 150
        text = printer.receipt_text(self.bill, self.config)
        for field in ("Order: Dine-in", "Table: 3", "Waiter: Ali", "Customer Example", "0300-1234567", "Tax:", "Second footer line"):
            self.assertIn(field, text)
        self.assertGreater(text.index("Eid Mubarak"), text.index("TOTAL:"))
        self.config["bill_layout"]["promo_position"] = "above_total"
        text = printer.receipt_text(self.bill, self.config)
        self.assertLess(text.index("Eid Mubarak"), text.index("TOTAL:"))

    def test_control_sequences_cannot_be_injected_through_owner_text(self):
        self.bill["items"][0]["name"] = "Chicken\x1b\x1d\x00\x07\x9bKarahi"
        self.config["footer"] = "Thanks\x1b@\x1dV"
        text = printer.receipt_text(self.bill, self.config)
        for control in ("\x1b", "\x1d", "\x00", "\x07", "\x9b"):
            self.assertNotIn(control, text)
        self.assertIn("ChickenKarahi", text)

    def test_delivery_charge_prints_from_saved_bill_with_optional_sections_off(self):
        self.bill.update(delivery_fee=125.50, total=1625.50)
        self.config["delivery_fee"] = 999
        for key, value in self.config["bill_layout"].items():
            if isinstance(value, bool):
                self.config["bill_layout"][key] = False
        for width in (58, 80):
            self.config["paper_width"] = width
            text = printer.receipt_text(self.bill, self.config)
            delivery = next(line for line in text.splitlines() if "Delivery:" in line)
            self.assertIn("Rs. 125.50", delivery)
            self.assertIn("Rs. 1,625.50", text)
            self.assertNotIn("999", text)
            self.assertLess(text.index("Delivery:"), text.index("TOTAL:"))

    def test_delivery_is_hidden_for_old_bills_and_orders_without_a_charge(self):
        self.config["delivery_fee"] = 250
        self.bill.pop("delivery_fee", None)
        self.assertNotIn("Delivery:", printer.receipt_text(self.bill, self.config))
        self.bill["delivery_fee"] = 0
        self.assertNotIn("Delivery:", printer.receipt_text(self.bill, self.config))

    def test_free_delivery_prints_when_order_type_line_is_disabled(self):
        self.bill["delivery_fee"] = 0
        self.bill["order_info"]["order_type"] = "Delivery"
        self.config["bill_layout"]["show_order_type"] = False
        text = printer.receipt_text(self.bill, self.config)
        self.assertNotIn("Order: Delivery", text)
        delivery = next(line for line in text.splitlines() if "Delivery:" in line)
        self.assertIn("Rs. 0", delivery)

    def test_missing_and_corrupt_logo_do_not_prevent_printing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "logo.png"
            self.config["logo_path"] = "images/logo.png"
            with patch("app_config.image_path", return_value=path):
                self.assertIsNone(printer._receipt(self.bill, self.config).logo)
                path.write_bytes(b"not an image")
                self.assertIsNone(printer._receipt(self.bill, self.config).logo)

    def test_logo_is_bw_and_dish_photos_are_never_opened(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "logo.png"
            Image.new("RGB", (2000, 1000), "red").save(path)
            self.config["logo_path"] = "images/logo.png"
            self.bill["items"][0]["image_path"] = "images/never-open-dish-photo.png"
            with patch("app_config.image_path", return_value=path) as resolver:
                receipt = printer._receipt(self.bill, self.config)
            resolver.assert_called_once_with("images/logo.png")
            self.assertEqual(receipt.logo.mode, "1")
            self.assertLessEqual(receipt.logo.width, 576)
            self.assertLessEqual(receipt.logo.height, 180)

    def test_preview_both_widths_and_urdu(self):
        self.bill["items"][0]["name"] = "چکن کڑاہی Chicken Karahi"
        for width, pixels in ((58, 384), (80, 576)):
            self.config["paper_width"] = width
            image = printer.render_receipt(self.bill, self.config)
            self.assertEqual(image.width, pixels)
            self.assertGreater(image.height, 200)
            self.assertIn("چکن", printer.receipt_text(self.bill, self.config))

    @unittest.skipUnless(os.name == "nt", "Windows Unicode rendering")
    def test_urdu_reaches_explicit_wide_character_renderer(self):
        # MFC's ANSI DrawText wrapper produced mojibake despite correct DB text.
        import ctypes
        original = ctypes.windll.user32.DrawTextW
        self.bill["items"][0]["name"] = "چکن کڑاہی"
        with patch.object(ctypes.windll.user32, "DrawTextW", wraps=original) as draw:
            printer.render_receipt(self.bill, self.config)
        rendered = [call.args[1] for call in draw.call_args_list]
        self.assertTrue(any("چکن کڑاہی" in text for text in rendered))

    def test_invalid_money_is_reported(self):
        for invalid in (float("nan"), float("inf"), "invalid"):
            self.bill["total"] = invalid
            with self.assertRaises(printer.PrinterError):
                printer.receipt_text(self.bill, self.config)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.bill = printer.test_bill()

    def test_usb_output_uses_exact_receipt_text_and_never_rasterizes_text(self):
        device = MagicMock()
        with patch("printer._open_usb", return_value=device):
            printer.print_bill(self.bill, self.config)
        output = "".join(call.args[0] for call in device.text.call_args_list)
        self.assertEqual(output, printer.receipt_text(self.bill, self.config) + "\n\n\n")
        device.image.assert_not_called()
        device.cut.assert_called_once()
        device.close.assert_called_once()
        self.assertTrue(all(call.kwargs.get("custom_size") for call in device.set.call_args_list))

    def test_usb_open_failure_falls_back_to_win32_raw(self):
        device = MagicMock()
        with patch("printer._open_usb", side_effect=OSError("USB missing")), \
             patch("printer._open_win32_raw", return_value=device) as fallback:
            printer.print_bill(self.bill, self.config)
        fallback.assert_called_once()
        device.cut.assert_called_once()

    def test_mid_print_failure_never_retries_and_reports_uncertain_output(self):
        device = MagicMock()
        device.text.side_effect = OSError("cable disconnected")
        with patch("printer._open_usb", return_value=device), \
             patch("printer._open_win32_raw") as fallback:
            with self.assertRaisesRegex(printer.PrinterError, "Check the paper"):
                printer.print_bill(self.bill, self.config)
        fallback.assert_not_called()
        device.close.assert_called_once()

    def test_absent_printer_has_actionable_config_instructions(self):
        with patch("printer._open_usb", side_effect=OSError("not found")), \
             patch("printer._open_win32_raw", side_effect=OSError("driver absent")):
            with self.assertRaises(printer.PrinterError) as context:
                printer.print_bill(self.bill, self.config)
        message = str(context.exception)
        self.assertIn("Settings > General", message)
        self.assertIn("VID/PID", message)
        self.assertIn("POS80 Printer", message)

    def test_urdu_uses_windows_native_text_without_sending_escpos(self):
        self.bill["items"][0]["name"] = "چکن کڑاہی"
        with patch("printer._print_windows") as windows, patch("printer._print_escpos") as usb:
            printer.print_bill(self.bill, self.config)
        windows.assert_called_once()
        usb.assert_not_called()

    def test_windows_mode_uses_native_text_for_english_too(self):
        self.config["printer_mode"] = "win32"
        with patch("printer._print_windows") as windows, patch("printer._print_escpos") as usb:
            printer.print_bill(self.bill, self.config)
        windows.assert_called_once()
        usb.assert_not_called()


if __name__ == "__main__":
    unittest.main()
