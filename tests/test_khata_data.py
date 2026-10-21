"""Khata accounting, duplicate guarding, and atomic bill/customer storage."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from db import Database, DuplicateCustomerError


class KhataDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "restaurant.db"
        self.db = Database(self.path)
        self.now = datetime(2026, 10, 20, 12, 30, 1)
        self.items = [{"name": "Biryani", "unit": "count", "qty": 1, "price": 100}]

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def save(self, **kwargs):
        return self.db.save_bill(self.items, now=self.now, **kwargs)

    def test_paid_and_khata_share_sales_numbering_and_void_behavior(self):
        paid = self.save()
        credit = self.save(new_customer={"name": "Ahmed", "phone": "03001234567"},
                           discount=10, tax=16, delivery_fee=50)
        customer_id = credit["customer_id"]
        third = self.save(customer_id=customer_id)
        self.assertEqual([paid["bill_no"], credit["bill_no"], third["bill_no"]], [1, 2, 3])
        self.assertIsNone(paid["customer_id"])
        self.assertEqual(self.db.daily_summary("2026-10-20"), {"total": 356, "count": 3})
        self.assertEqual(len(self.db.list_bills("2026-10-20")), 3)
        self.assertEqual(self.db.top_dishes("2026-10-20")[0]["qty"], 3)
        self.assertEqual(self.db.get_customer(customer_id)["balance"], 256)
        self.assertEqual(self.db.khata_total(), 256)
        self.assertEqual([b["id"] for b in self.db.customer_bills(customer_id)], [third["id"], credit["id"]])
        self.assertTrue(self.db.void_bill(credit["id"]))
        self.assertFalse(self.db.void_bill(credit["id"]))
        self.assertEqual(self.db.khata_total(), 100)
        self.assertEqual(self.db.get_customer(customer_id)["balance"], 100)
        self.assertEqual(self.db.daily_summary("2026-10-20"), {"total": 200, "count": 2})
        self.assertEqual(self.db.get_bill(credit["id"])["status"], "void")
        self.assertEqual(self.save()["bill_no"], 4)
        self.db.void_bill(third["id"])
        self.assertEqual(self.db.get_customer(customer_id)["balance"], 0)
        self.assertEqual(self.db.customer_bills(customer_id), [])
        self.assertEqual(self.db.khata_total(), 0)

    def test_balances_span_days_search_and_default_alphabetical_order(self):
        zara = self.save(new_customer={"name": "Zara", "phone": "03211111111"})
        ahmed = self.save(new_customer={"name": "Ahmed", "phone": "03002222222"})
        old = self.db.save_bill(self.items, customer_id=ahmed["customer_id"],
                                now=datetime(2026, 10, 1, 19, 0))
        latest = self.db.save_bill(self.items, customer_id=ahmed["customer_id"],
                                   now=datetime(2026, 10, 21, 9, 0))
        self.assertEqual(latest["bill_no"], 1)
        self.assertEqual([c["name"] for c in self.db.list_customers()], ["Ahmed", "Zara"])
        self.assertEqual([c["balance"] for c in self.db.list_customers()], [300, 100])
        self.assertEqual(self.db.khata_total(), 400)
        self.assertEqual(self.db.list_customers(" AHMAD ")[0]["id"], ahmed["customer_id"])
        self.assertEqual(self.db.list_customers("0321")[0]["id"], zara["customer_id"])
        self.assertEqual(self.db.list_customers("no match"), [])
        self.assertEqual([b["id"] for b in self.db.customer_bills(ahmed["customer_id"])],
                         [latest["id"], ahmed["id"], old["id"]])

    def test_duplicate_case_spacing_alias_and_unicode_are_blocked(self):
        original = self.save(new_customer={"name": "  Ahmed   Khan  ", "phone": " 0300 "})
        customer_id = original["customer_id"]
        self.assertEqual(self.db.get_customer(customer_id)["name"], "Ahmed Khan")
        self.assertEqual(self.db.get_customer(customer_id)["phone"], "0300")
        for name in ("AHMED KHAN", " ahmad  khan ", "Ａｈｍｅｄ Khan"):
            with self.subTest(name=name), self.assertRaises(DuplicateCustomerError) as raised:
                self.save(new_customer={"name": name, "phone": "different phone"})
            self.assertEqual(raised.exception.customer["id"], customer_id)
            self.assertEqual(str(raised.exception), "This name already exists")
        self.assertEqual(len(self.db.list_customers()), 1)
        second = self.save(customer_id=customer_id)
        self.assertEqual(second["bill_no"], 2)
        urdu = self.save(new_customer={"name": "احمد", "phone": ""})
        self.assertEqual(urdu["order_info"]["khata_customer_name"], "احمد")
        with self.assertRaises(DuplicateCustomerError):
            self.save(new_customer={"name": "  احمد  "})
        # Other similar spellings are not silently treated as one person.
        self.assertIsNone(self.db.find_customer_by_name("Ahmed Ali"))

    def test_customer_and_bill_rollback_together_on_failed_insert(self):
        self.db.connection.execute("""CREATE TRIGGER reject_bill BEFORE INSERT ON bills
            BEGIN SELECT RAISE(ABORT, 'simulated failure'); END""")
        self.db.connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.save(new_customer={"name": "Ahmed"})
        self.assertEqual(self.db.list_customers(), [])
        self.assertEqual(self.db.list_bills("2026-10-20"), [])
        self.db.connection.execute("DROP TRIGGER reject_bill")
        self.db.connection.commit()
        saved = self.save(new_customer={"name": "Ahmed"})
        self.assertEqual(saved["bill_no"], 1)
        self.assertEqual(self.db.get_customer(saved["customer_id"])["balance"], 100)

    def test_invalid_customer_does_not_save_bill_or_consume_number(self):
        for arguments in ({"customer_id": 98765}, {"customer_id": True}, {"customer_id": 1.5},
                          {"new_customer": {"name": " "}}, {"new_customer": {"name": None}},
                          {"new_customer": {"name": "Ahmed", "phone": False}},
                          {"customer_id": 1, "new_customer": {"name": "Ahmed"}}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.save(**arguments)
        self.assertEqual(self.db.list_customers(), [])
        self.assertEqual(self.save()["bill_no"], 1)
        self.assertEqual(self.db.khata_total(), 0)
        self.assertIsNone(self.db.get_customer(98765))

    def test_customer_snapshot_and_balances_persist_after_restart(self):
        saved = self.save(new_customer={"name": "Ahmed", "phone": "0300123"},
                          order_info={"order_type": "Delivery", "customer_name": "Delivery contact"})
        customer_id = saved["customer_id"]
        with self.db.connection:
            self.db.connection.execute("UPDATE customers SET name=?,phone=? WHERE id=?",
                                       ("Corrected name", "0300999", customer_id))
        self.db.close()
        self.db = Database(self.path)
        restored = self.db.get_bill(saved["id"])
        self.assertEqual(restored["order_info"]["khata_customer_name"], "Ahmed")
        self.assertEqual(restored["order_info"]["khata_customer_phone"], "0300123")
        self.assertEqual(restored["order_info"]["customer_name"], "Delivery contact")
        self.assertEqual(self.db.get_customer(customer_id)["balance"], 100)
        self.assertEqual(self.db.khata_total(), 100)
        self.assertFalse({"balance", "total"} & {r[1] for r in self.db.connection.execute("PRAGMA table_info(customers)")})

    def test_simultaneous_duplicate_creation_only_saves_one_customer_and_bill(self):
        barrier = threading.Barrier(2)

        def create(name):
            instance = Database(self.path)
            try:
                barrier.wait(timeout=5)
                try:
                    saved = instance.save_bill(self.items, now=self.now, new_customer={"name": name})
                    return "saved", saved["customer_id"]
                except DuplicateCustomerError as exc:
                    return "duplicate", exc.customer["id"]
            finally:
                instance.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, ["Ahmed", " ahmad "]))
        self.assertEqual(sorted(result[0] for result in results), ["duplicate", "saved"])
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(len(self.db.list_customers()), 1)
        self.assertEqual(len(self.db.list_bills("2026-10-20")), 1)
        self.assertEqual(self.db.khata_total(), 100)

    def test_legacy_database_migrates_old_bills_to_paid_without_changing_history(self):
        legacy = Path(self.temp.name) / "legacy.db"
        with closing(sqlite3.connect(legacy)) as connection:
            with connection:
                connection.execute("""CREATE TABLE bills(id INTEGER PRIMARY KEY, bill_no INTEGER,
                    date TEXT, items TEXT, subtotal REAL, discount REAL, tax REAL, total REAL,
                    created_at TEXT, status TEXT DEFAULT 'active')""")
                connection.execute("""INSERT INTO bills
                    VALUES (1,7,'2026-10-20',?,100,0,0,100,'2026-10-20T11:00:00','active')""",
                    (json.dumps(self.items),))
        instance = Database(legacy)
        try:
            self.assertIsNone(instance.get_bill(1)["customer_id"])
            self.assertEqual(instance.get_bill(1)["total"], 100)
            self.assertEqual(instance.list_customers(), [])
            self.assertEqual(instance.khata_total(), 0)
            saved = instance.save_bill(self.items, now=self.now, new_customer={"name": "Ahmed"})
            self.assertEqual(saved["bill_no"], 8)
            self.assertEqual(instance.daily_summary("2026-10-20"), {"total": 200, "count": 2})
            self.assertEqual(instance.khata_total(), 100)
            self.assertEqual(instance.connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            instance.close()


if __name__ == "__main__":
    unittest.main()
