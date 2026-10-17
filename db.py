"""Transactional SQLite storage. Printed bills keep immutable item snapshots."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import time

from app_config import DATA_DIR, image_path
from models import amount, cents, normalize_items


SEED_DISHES = [
    ("Chicken Karahi", 950, "kg", "Karahi"),
    ("Mutton Karahi", 1800, "kg", "Karahi"),
    ("Chicken Biryani", 250, "count", "Rice"),
    ("Beef Biryani", 300, "count", "Rice"),
    ("Seekh Kabab", 180, "count", "BBQ"),
    ("Naan", 40, "count", "Other"),
    ("Raita", 50, "count", "Other"),
    ("Cold Drink", 100, "count", "Drinks"),
    ("Chai", 80, "count", "Drinks"),
]


class Database:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else DATA_DIR / "restaurant.db"
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path), timeout=10)
        self.connection.row_factory = sqlite3.Row
        try:
            # A simultaneous first launch can briefly hold the journal-mode
            # schema lock; SQLite may return SQLITE_BUSY before its busy timeout.
            for attempt in range(40):
                try:
                    self.connection.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or attempt == 39:
                        raise
                    time.sleep(0.025)
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self._initialize()
        except Exception:
            self.connection.close()
            raise

    def _initialize(self):
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute("""CREATE TABLE IF NOT EXISTS dishes (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, price REAL NOT NULL,
                sell_type TEXT DEFAULT 'count', image_path TEXT DEFAULT '',
                category TEXT DEFAULT 'General', active INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0)""")
            self.connection.execute("""CREATE TABLE IF NOT EXISTS bills (
                id INTEGER PRIMARY KEY, bill_no INTEGER NOT NULL, date TEXT NOT NULL,
                items TEXT NOT NULL, subtotal REAL NOT NULL, discount REAL NOT NULL,
                tax REAL NOT NULL, total REAL NOT NULL, created_at TEXT NOT NULL,
                status TEXT DEFAULT 'active', order_info TEXT DEFAULT '{}',
                delivery_fee REAL NOT NULL DEFAULT 0)""")
            self.connection.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
            dish_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(dishes)")}
            if "deleted" not in dish_columns:
                self.connection.execute("ALTER TABLE dishes ADD COLUMN deleted INTEGER DEFAULT 0")
            bill_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(bills)")}
            if "order_info" not in bill_columns:
                self.connection.execute("ALTER TABLE bills ADD COLUMN order_info TEXT DEFAULT '{}'")
            if "status" not in bill_columns:
                self.connection.execute("ALTER TABLE bills ADD COLUMN status TEXT DEFAULT 'active'")
            if "delivery_fee" not in bill_columns:
                self.connection.execute("ALTER TABLE bills ADD COLUMN delivery_fee REAL NOT NULL DEFAULT 0")
            self.connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS bills_daily_number ON bills(date, bill_no)")
            self.connection.execute("CREATE INDEX IF NOT EXISTS bills_sales ON bills(date, status)")
            if self.connection.execute("SELECT COUNT(*) FROM dishes").fetchone()[0] == 0:
                self.connection.executemany(
                    "INSERT INTO dishes(name,price,sell_type,category,sort_order) VALUES (?,?,?,?,?)",
                    [(*dish, i) for i, dish in enumerate(SEED_DISHES)],
                )

    def list_dishes(self, include_inactive: bool = False) -> list[dict]:
        sql = "SELECT * FROM dishes WHERE deleted=0"
        if not include_inactive:
            sql += " AND active=1"
        return [dict(row) for row in self.connection.execute(sql + " ORDER BY sort_order,id")]

    def save_dish(self, dish: dict) -> int:
        name = str(dish.get("name", "")).strip()
        if not name:
            raise ValueError("Enter a dish name.")
        price = amount(dish.get("price"), "Price")
        sell_type = dish.get("sell_type", "count")
        if sell_type not in ("count", "kg"):
            raise ValueError("Choose Count or Weight (kg).")
        photo = str(dish.get("image_path", ""))
        if photo:
            image_path(photo)
        category = str(dish.get("category", "General")).strip() or "General"
        active = int(bool(dish.get("active", True)))
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            dish_id = dish.get("id")
            if dish_id is not None:
                existing = self.connection.execute("SELECT * FROM dishes WHERE id=? AND deleted=0", (dish_id,)).fetchone()
                if existing is None:
                    raise ValueError("This dish no longer exists.")
                sort_order = int(dish.get("sort_order", existing["sort_order"]))
                self.connection.execute("""UPDATE dishes SET name=?,price=?,sell_type=?,image_path=?,
                    category=?,active=?,sort_order=? WHERE id=?""",
                    (name, price, sell_type, photo, category, active, sort_order, dish_id))
                return int(dish_id)
            default_order = self.connection.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM dishes").fetchone()[0]
            cursor = self.connection.execute("""INSERT INTO dishes
                (name,price,sell_type,image_path,category,active,sort_order) VALUES (?,?,?,?,?,?,?)""",
                (name, price, sell_type, photo, category, active, int(dish.get("sort_order", default_order))))
            return cursor.lastrowid

    def delete_dish(self, dish_id: int):
        with self.connection:
            self.connection.execute("UPDATE dishes SET deleted=1,active=0 WHERE id=?", (dish_id,))

    def move_dish(self, dish_id: int, direction: int):
        if direction not in (-1, 1):
            raise ValueError("Direction must be -1 or 1.")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            rows = list(self.connection.execute("SELECT id FROM dishes WHERE deleted=0 ORDER BY sort_order,id"))
            ids = [row[0] for row in rows]
            if dish_id not in ids:
                raise ValueError("This dish no longer exists.")
            position = ids.index(dish_id)
            target = position + direction
            if 0 <= target < len(ids):
                ids[position], ids[target] = ids[target], ids[position]
                self.connection.executemany("UPDATE dishes SET sort_order=? WHERE id=?", enumerate(ids))

    @staticmethod
    def _day(day: str | None = None) -> str:
        return date.fromisoformat(day).isoformat() if day is not None else date.today().isoformat()

    @staticmethod
    def _bill(row) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        result["items"] = json.loads(result["items"])
        result["order_info"] = json.loads(result.get("order_info") or "{}")
        return result

    def save_bill(self, items, discount=0, tax=0, total=None, subtotal=None,
                  order_info=None, now=None, delivery_fee=0) -> dict:
        normalized = normalize_items(items)
        if not normalized:
            raise ValueError("Add at least one dish before printing.")
        computed_subtotal = amount(sum(Decimal(str(item["amount"])) for item in normalized), "Subtotal")
        discount = amount(discount, "Discount")
        tax = amount(tax, "Tax")
        delivery_fee = amount(delivery_fee, "Delivery charge")
        if cents(discount) > cents(Decimal(str(computed_subtotal)) + Decimal(str(tax))):
            raise ValueError("Discount cannot exceed food plus tax.")
        computed_total = amount(Decimal(str(computed_subtotal)) - Decimal(str(discount))
                                + Decimal(str(tax)) + Decimal(str(delivery_fee)), "Total")
        if subtotal is not None and cents(amount(subtotal)) != cents(computed_subtotal):
            raise ValueError("The subtotal changed. Review this bill before printing.")
        if total is not None and cents(amount(total)) != cents(computed_total):
            raise ValueError("The total changed. Review this bill before printing.")
        order = dict(order_info or {})
        for key, value in order.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("Order information must contain text fields.")
        current = now or datetime.now()
        if isinstance(current, str):
            current = datetime.fromisoformat(current)
        day = current.date().isoformat()
        created_at = current.isoformat(timespec="seconds")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            bill_no = self.connection.execute("SELECT COALESCE(MAX(bill_no),0)+1 FROM bills WHERE date=?", (day,)).fetchone()[0]
            cursor = self.connection.execute("""INSERT INTO bills
                (bill_no,date,items,subtotal,discount,tax,total,created_at,order_info,delivery_fee)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (bill_no, day, json.dumps(normalized, ensure_ascii=False, allow_nan=False),
                 computed_subtotal, discount, tax, computed_total, created_at,
                 json.dumps(order, ensure_ascii=False), delivery_fee))
            bill_id = cursor.lastrowid
        return self.get_bill(bill_id)

    def get_bill(self, bill_id: int) -> dict | None:
        return self._bill(self.connection.execute("SELECT * FROM bills WHERE id=?", (bill_id,)).fetchone())

    def last_bill(self) -> dict | None:
        return self._bill(self.connection.execute("SELECT * FROM bills WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone())

    def list_bills(self, day: str) -> list[dict]:
        return [self._bill(row) for row in self.connection.execute(
            "SELECT * FROM bills WHERE date=? AND status='active' ORDER BY created_at DESC,id DESC", (self._day(day),))]

    def daily_summary(self, day: str | None = None) -> dict:
        row = self.connection.execute("SELECT COALESCE(SUM(total),0),COUNT(*) FROM bills WHERE date=? AND status='active'", (self._day(day),)).fetchone()
        return {"total": round(row[0], 2), "count": row[1]}

    def top_dishes(self, day: str) -> list[dict]:
        groups = {}
        for bill in self.list_bills(day):
            for item in bill["items"]:
                key = (item["name"], item.get("unit", "count"))
                group = groups.setdefault(key, {"name": key[0], "unit": key[1], "qty": 0, "amount": 0})
                group["qty"] = round(group["qty"] + item["qty"], 6)
                group["amount"] = round(group["amount"] + item["amount"], 2)
        return sorted(groups.values(), key=lambda item: (-item["amount"], item["name"]))

    def void_bill(self, bill_id: int) -> bool:
        with self.connection:
            cursor = self.connection.execute("UPDATE bills SET status='void' WHERE id=? AND status='active'", (bill_id,))
            return cursor.rowcount == 1

    def set_setting(self, key: str, value):
        with self.connection:
            self.connection.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False, allow_nan=False)))

    def get_setting(self, key: str, default=None):
        row = self.connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            return row[0]

    def close(self):
        self.connection.close()
