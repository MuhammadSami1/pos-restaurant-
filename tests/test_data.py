"""Regression tests for bill accounting, daily numbering, and owner data."""

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import app_config
from db import Database
from models import amount, calculate_totals, money, quantity_text, validate_quantity


class BillDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "restaurant.db"
        self.db = Database(self.path)
        self.now = datetime(2026, 10, 14, 21, 41, 0)
        self.items = [
            {"name": "Chicken Karahi", "qty": 1.5, "unit": "kg", "price": 950},
            {"name": "نان", "qty": 3, "unit": "count", "price": 40},
        ]

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_seed_once_and_preserve_deleted_dishes_across_restart(self):
        self.assertEqual(len(self.db.list_dishes()), 9)
        for dish in self.db.list_dishes():
            self.db.delete_dish(dish["id"])
        self.db.close()
        self.db = Database(self.path)
        self.assertEqual(self.db.list_dishes(include_inactive=True), [])

    def test_edit_inactive_and_reorder(self):
        original = self.db.list_dishes()[0]
        original.update(name="چکن کڑاہی", price=1200.555, active=False)
        self.db.save_dish(original)
        self.assertEqual(len(self.db.list_dishes()), 8)
        updated = self.db.list_dishes(include_inactive=True)[0]
        self.assertEqual(updated["price"], 1200.56)
        self.assertEqual(updated["name"], "چکن کڑاہی")
        self.db.move_dish(original["id"], 1)
        self.assertEqual(self.db.list_dishes(include_inactive=True)[1]["id"], original["id"])

    def test_numbering_resets_daily_and_void_does_not_reuse_number(self):
        first = self.db.save_bill(self.items, now=self.now)
        second = self.db.save_bill(self.items, now=self.now)
        self.assertTrue(self.db.void_bill(second["id"]))
        third = self.db.save_bill(self.items, now=self.now)
        tomorrow = self.db.save_bill(self.items, now=datetime(2026, 10, 15, 0, 0))
        self.assertEqual([first["bill_no"], second["bill_no"], third["bill_no"], tomorrow["bill_no"]], [1, 2, 3, 1])
        self.assertEqual(self.db.get_bill(second["id"])["status"], "void")
        self.assertFalse(self.db.void_bill(second["id"]))
        self.assertEqual(self.db.daily_summary("2026-10-14"), {"total": 3090.0, "count": 2})
        self.assertEqual(len(self.db.list_bills("2026-10-14")), 2)
        self.assertEqual(self.db.top_dishes("2026-10-14")[0]["qty"], 3.0)
        self.assertEqual(self.db.last_bill()["id"], tomorrow["id"])

    def test_saved_items_are_snapshots_and_amount_is_recalculated(self):
        self.items[0]["amount"] = 1
        saved = self.db.save_bill(self.items, now=self.now)
        self.items[0]["name"] = "Changed after printing"
        dish = self.db.list_dishes()[0]
        dish.update(price=9999, name="New dish name")
        self.db.save_dish(dish)
        read = self.db.get_bill(saved["id"])
        self.assertEqual(read["items"][0]["name"], "Chicken Karahi")
        self.assertEqual(read["items"][0]["amount"], 1425.0)
        self.assertEqual(read["total"], 1545.0)

    def test_delivery_charge_is_a_saved_snapshot_and_is_included_in_sales(self):
        totals = calculate_totals(self.items, 10, "%", 16, delivery_fee=150)
        saved = self.db.save_bill(self.items, **totals, now=self.now,
                                  order_info={"order_type": "Delivery"})
        totals["delivery_fee"] = 999
        self.db.close()
        self.db = Database(self.path)
        restored = self.db.last_bill()
        self.assertEqual(restored["id"], saved["id"])
        self.assertEqual(restored["delivery_fee"], 150)
        self.assertEqual(restored["discount"], 179.22)
        self.assertEqual(restored["tax"], 247.2)
        self.assertEqual(restored["total"], 1762.98)
        self.assertEqual(self.db.daily_summary("2026-10-14"), {"total": 1762.98, "count": 1})
        self.assertEqual(sum(dish["amount"] for dish in self.db.top_dishes("2026-10-14")), 1545)
        self.assertTrue(self.db.void_bill(saved["id"]))
        self.assertEqual(self.db.daily_summary("2026-10-14"), {"total": 0, "count": 0})
        self.assertEqual(self.db.get_bill(saved["id"])["delivery_fee"], 150)

    def test_delivery_total_validation_and_no_charge_default(self):
        with self.assertRaises(ValueError):
            self.db.save_bill(self.items, delivery_fee=150, total=1545, now=self.now)
        with self.assertRaises(ValueError):
            self.db.save_bill(self.items, delivery_fee=-1, now=self.now)
        no_delivery = self.db.save_bill(self.items, now=self.now)
        self.assertEqual(no_delivery["bill_no"], 1)
        self.assertEqual(no_delivery["delivery_fee"], 0)
        self.assertEqual(no_delivery["total"], 1545)

    def test_invalid_bill_does_not_save_or_consume_number(self):
        with self.assertRaises(ValueError):
            self.db.save_bill(self.items, total=1, now=self.now)
        with self.assertRaises(ValueError):
            self.db.save_bill(self.items, discount=2000, now=self.now)
        with self.assertRaises(ValueError):
            self.db.save_bill([], now=self.now)
        self.assertEqual(self.db.daily_summary("2026-10-14")["count"], 0)
        self.assertEqual(self.db.save_bill(self.items, now=self.now)["bill_no"], 1)

    def test_transaction_rolls_back_a_failed_write(self):
        self.db.connection.execute("CREATE TRIGGER reject_bill BEFORE INSERT ON bills BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
        self.db.connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.save_bill(self.items, now=self.now)
        self.db.connection.execute("DROP TRIGGER reject_bill")
        self.db.connection.commit()
        self.assertEqual(self.db.save_bill(self.items, now=self.now)["bill_no"], 1)
        self.assertEqual(self.db.connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_second_connection_reserves_unique_daily_numbers(self):
        other = Database(self.path)
        try:
            first = self.db.save_bill(self.items, now=self.now)
            second = other.save_bill(self.items, now=self.now)
            self.assertEqual((first["bill_no"], second["bill_no"]), (1, 2))
        finally:
            other.close()

    def test_concurrent_first_launch_seeds_once_and_reserves_unique_numbers(self):
        new_path = Path(self.temp.name) / "concurrent.db"
        barrier = threading.Barrier(4)

        def launch_and_save(_):
            barrier.wait()
            instance = Database(new_path)
            try:
                return instance.save_bill(self.items, now=self.now)["bill_no"]
            finally:
                instance.close()

        with ThreadPoolExecutor(max_workers=4) as pool:
            numbers = list(pool.map(launch_and_save, range(4)))
        self.assertEqual(sorted(numbers), [1, 2, 3, 4])
        instance = Database(new_path)
        try:
            self.assertEqual(len(instance.list_dishes()), 9)
            self.assertEqual(instance.daily_summary("2026-10-14")["count"], 4)
        finally:
            instance.close()

    def test_supplied_original_schema_migrates_without_losing_history(self):
        legacy_path = Path(self.temp.name) / "legacy.db"
        with sqlite3.connect(legacy_path) as connection:
            connection.execute("""CREATE TABLE dishes(id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                price REAL NOT NULL, sell_type TEXT DEFAULT 'count', image_path TEXT DEFAULT '',
                category TEXT DEFAULT 'General', active INTEGER DEFAULT 1, sort_order INTEGER DEFAULT 0)""")
            connection.execute("""CREATE TABLE bills(id INTEGER PRIMARY KEY, bill_no INTEGER NOT NULL,
                date TEXT NOT NULL, items TEXT NOT NULL, subtotal REAL, discount REAL, tax REAL,
                total REAL, created_at TEXT, status TEXT DEFAULT 'active')""")
            connection.execute("INSERT INTO dishes(name,price) VALUES ('Original dish',12)")
            connection.execute("""INSERT INTO bills(bill_no,date,items,subtotal,discount,tax,total,created_at)
                VALUES (7,'2026-10-14','[]',100,0,0,100,'2026-10-14T10:00:00')""")
            connection.execute("""INSERT INTO bills(bill_no,date,items,subtotal,discount,tax,total,created_at,status)
                VALUES (6,'2026-10-14','[]',200,0,0,200,'2026-10-14T09:00:00','void')""")
        connection.close()
        instance = Database(legacy_path)
        try:
            self.assertEqual(instance.list_dishes()[0]["name"], "Original dish")
            self.assertEqual(instance.get_bill(1)["order_info"], {})
            self.assertEqual(instance.get_bill(1)["delivery_fee"], 0)
            self.assertEqual(instance.get_bill(1)["total"], 100)
            self.assertEqual(instance.get_bill(2)["delivery_fee"], 0)
            self.assertEqual(instance.get_bill(2)["status"], "void")
            self.assertEqual(instance.daily_summary("2026-10-14"), {"total": 100, "count": 1})
            self.assertEqual(instance.save_bill(self.items, now=self.now)["bill_no"], 8)
        finally:
            instance.close()

    def test_order_info_and_settings_unicode_round_trip(self):
        info = {"order_type": "Delivery", "customer_name": "احمد", "phone": "0300-1234567"}
        bill = self.db.save_bill(self.items, order_info=info, now=self.now)
        self.assertEqual(bill["order_info"], info)
        self.db.set_setting("theme", "light")
        self.assertEqual(self.db.get_setting("theme"), "light")


class MoneyTests(unittest.TestCase):
    def test_weight_count_discount_and_tax(self):
        items = [{"name": "Karahi", "qty": 1.5, "price": 950, "unit": "kg"}]
        self.assertEqual(calculate_totals(items, 10, "%", 16),
                         {"subtotal": 1425.0, "discount": 165.3, "tax": 228.0,
                          "delivery_fee": 0.0, "total": 1487.7})
        self.assertEqual(money(1500), "Rs. 1,500")
        self.assertEqual(money(1500.5), "Rs. 1,500.50")
        self.assertEqual(quantity_text(1.5, "kg"), "1.5 kg")
        self.assertEqual(quantity_text(3, "count"), "3")

    def test_delivery_added_after_food_discount_and_tax(self):
        items = [{"name": "Food", "qty": 1, "price": 1000, "unit": "count"}]
        self.assertEqual(calculate_totals(items, 10, "%", 16, 150),
                         {"subtotal": 1000.0, "discount": 116.0, "tax": 160.0,
                          "delivery_fee": 150.0, "total": 1194.0})
        self.assertEqual(calculate_totals(items, 100, "Rs.", 16, 150)["total"], 1210.0)
        self.assertEqual(calculate_totals(items, 100, "%", 16, 150)["total"], 150.0)
        self.assertEqual(calculate_totals(items, 10, "%", 16, 0)["total"], 1044.0)
        with self.assertRaises(ValueError):
            calculate_totals(items, 1100, "Rs.", 0, 150)
        for value in (-0.001, float("nan"), float("inf"), True, "1e308"):
            with self.subTest(delivery=value), self.assertRaises(ValueError):
                calculate_totals(items, delivery_fee=value)

    def test_owner_discount_example_applies_to_food_plus_tax(self):
        items = [{"name": "Food", "qty": 1, "price": 3470, "unit": "count"}]
        totals = calculate_totals(items, 10, "%", 16)
        self.assertEqual(totals, {"subtotal": 3470, "tax": 555.2, "discount": 402.52,
                                  "delivery_fee": 0, "total": 3622.68})
        self.assertEqual(calculate_totals(items, 10, "%", 16, 150)["total"], 3772.68)

    def test_decimal_rounding_does_not_lose_paise_from_float_products(self):
        items = [{"name": "Food", "qty": 1, "price": 21.9, "unit": "count"}]
        totals = calculate_totals(items, 5, "%")
        self.assertEqual(totals["discount"], 1.1)
        self.assertEqual(totals["total"], 20.8)
        self.assertEqual(calculate_totals(items, tax_percent=5)["tax"], 1.1)
        weighted = [{"name": "Food", "qty": 0.35, "price": 3.7, "unit": "kg"}]
        self.assertEqual(calculate_totals(weighted)["subtotal"], 1.3)
        self.assertEqual(amount(2.675), 2.68)

    def test_rejects_nonfinite_negative_and_fractional_count(self):
        for value in (float("nan"), float("inf"), -1, 0, 1.5, True, "1e308"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_quantity(value, "count")
        for value in (-0.001, float("nan"), float("inf")):
            with self.subTest(discount=value), self.assertRaises(ValueError):
                calculate_totals([], value)
        with self.assertRaises(ValueError):
            calculate_totals([], 100.001, "%")
        with self.assertRaises(ValueError):
            amount("1e308")
        with self.assertRaises(ValueError):
            calculate_totals([{"name": "Bulk", "qty": 1_000_000, "unit": "kg", "price": 1_000_000}])


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(app_config, "DATA_DIR", self.root),
                        patch.object(app_config, "IMAGE_DIR", self.root / "images"),
                        patch.object(app_config, "CONFIG_PATH", self.root / "config.json")]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_first_run_and_nested_defaults(self):
        config = app_config.load_config()
        self.assertEqual(config["theme"], "dark")
        self.assertEqual(config["delivery_fee"], 0)
        saved = app_config.save_config({"theme": "light", "bill_layout": {"show_tax": True}})
        self.assertTrue(saved["bill_layout"]["show_name"])
        self.assertTrue(app_config.load_config()["bill_layout"]["show_tax"])
        self.assertEqual(app_config.DEFAULT_CONFIG["theme"], "dark")

    def test_delivery_default_rate_persists_and_rejects_invalid_changes(self):
        app_config.save_config({"delivery_fee": "125.50"})
        self.assertEqual(app_config.load_config()["delivery_fee"], 125.5)
        for value in (-0.001, float("nan"), float("inf"), True, ""):
            with self.subTest(delivery=value), self.assertRaises(ValueError):
                app_config.save_config({"delivery_fee": value})
            self.assertEqual(app_config.load_config()["delivery_fee"], 125.5)

    def test_bad_save_keeps_previous_settings(self):
        app_config.save_config({"theme": "light"})
        with self.assertRaises(ValueError):
            app_config.save_config({"tax_percent": "NaN"})
        self.assertEqual(app_config.load_config()["theme"], "light")

    def test_corrupt_manual_edit_is_preserved_and_recovers(self):
        (self.root / "config.json").write_text("{ broken", encoding="utf-8")
        self.assertEqual(app_config.load_config()["paper_width"], 80)
        backups = list(self.root.glob("config.damaged-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "{ broken")

    def test_atomic_replace_failure_does_not_truncate_config(self):
        app_config.save_config({"theme": "light"})
        with patch.object(app_config.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                app_config.save_config({"theme": "dark"})
        self.assertEqual(app_config.load_config()["theme"], "light")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_image_path_and_verified_copy(self):
        from PIL import Image
        photo = self.root / "dish.png"
        Image.new("RGB", (16, 16), "red").save(photo)
        relative = app_config.import_image(photo)
        self.assertTrue(relative.startswith("images/"))
        self.assertTrue(app_config.image_path(relative).exists())
        photo.unlink()
        self.assertTrue(app_config.image_path(relative).exists())
        for path in ("../secret.png", "images/../../secret.png", str(self.root / "outside.png")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                app_config.image_path(path)
        broken = self.root / "broken.png"
        broken.write_text("not an image", encoding="utf-8")
        with self.assertRaises(ValueError):
            app_config.import_image(broken)


if __name__ == "__main__":
    unittest.main()
