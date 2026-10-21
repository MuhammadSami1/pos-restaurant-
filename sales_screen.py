"""Offline daily sales, date selection, bill details, and reversible voids."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
import json

import customtkinter as ctk

from models import money, quantity_text
from ui_common import ACCENT, ACCENT_TEXT, BG, DANGER, MUTED, PANEL, TEXT, confirm, error


def _timestamp(value):
    try:
        return datetime.fromisoformat(value).strftime("%d %b %Y  ·  %I:%M:%S %p")
    except (TypeError, ValueError):
        return str(value or "")


def _khata_label(bill):
    order = bill.get("order_info") or {}
    if isinstance(order, str):
        try:
            order = json.loads(order)
        except (TypeError, ValueError):
            order = {}
    name = order.get("khata_customer_name") if isinstance(order, dict) else None
    return f"UDHAAR · {name or ('Customer #' + str(bill['customer_id']))}"


class DatePicker(ctk.CTkToplevel):
    """Dependency-free calendar supporting month and year navigation."""

    def __init__(self, parent, selected, on_select):
        super().__init__(parent)
        self.app = parent.app
        self.on_select = on_select
        self.selected = selected
        self.month = selected.replace(day=1)
        self.title("Choose a sales date")
        self.geometry("440x435")
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.transient(parent)
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", padx=16, pady=(16, 6))
        ctk.CTkButton(toolbar, text="‹", width=38, height=38,
                      command=lambda: self.shift(-1)).pack(side="left")
        self.title_label = ctk.CTkLabel(toolbar, text="", font=ctk.CTkFont(size=18, weight="bold"))
        self.title_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(toolbar, text="›", width=38, height=38,
                      command=lambda: self.shift(1)).pack(side="right")
        self.days = ctk.CTkFrame(self, fg_color="transparent")
        self.days.pack(fill="both", expand=True, padx=12, pady=8)
        for column in range(7):
            self.days.grid_columnconfigure(column, weight=1, uniform="day")
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(6, 16))
        ctk.CTkButton(bottom, text="Today", width=100, height=38,
                      command=lambda: self.select(date.today())).pack(side="left")
        ctk.CTkButton(bottom, text="Light / dark", width=110, height=38,
                      fg_color=PANEL, text_color=TEXT, command=self.app.toggle_theme).pack(side="right")
        self.render()
        self.after(100, self.grab_set)
        self.bind("<Escape>", lambda _event: self.destroy())

    def shift(self, amount):
        index = self.month.year * 12 + self.month.month - 1 + amount
        year, month = divmod(index, 12)
        if 1 <= year <= 9999:
            self.month = date(year, month + 1, 1)
            self.render()

    def render(self):
        for child in self.days.winfo_children():
            child.destroy()
        self.title_label.configure(text=self.month.strftime("%B %Y"))
        for column, label in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            ctk.CTkLabel(self.days, text=label, text_color=MUTED).grid(row=0, column=column, pady=(0, 6))
        weeks = calendar.monthcalendar(self.month.year, self.month.month)
        for row, week in enumerate(weeks, 1):
            for column, day in enumerate(week):
                if day:
                    value = self.month.replace(day=day)
                    chosen = value == self.selected
                    ctk.CTkButton(self.days, text=str(day), width=48, height=38,
                                  fg_color=ACCENT if chosen else PANEL,
                                  text_color="#20190E" if chosen else TEXT,
                                  command=lambda day=value: self.select(day)).grid(
                                      row=row, column=column, padx=3, pady=3, sticky="ew")

    def select(self, value):
        self.on_select(value)
        self.destroy()


class SalesScreen(ctk.CTkToplevel):
    """Read and correct saved sales without physically deleting receipts."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.selected_day = date.today()
        self.title("Sales · Restaurant billing")
        self.geometry("1040x690")
        self.minsize(850, 570)
        self.configure(fg_color=BG)
        self.transient(app)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(20, 12))
        title = ctk.CTkFrame(header, fg_color="transparent")
        title.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(title, text="Sales", font=ctk.CTkFont(size=25, weight="bold"),
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(title, text="Every bill, with room to fix a mistake.",
                     text_color=MUTED, anchor="w").pack(fill="x")
        ctk.CTkButton(header, text="Reprint last bill", width=145, height=38,
                      fg_color=PANEL, text_color=TEXT, command=self.app.reprint_last).pack(side="right", padx=(12, 0))
        ctk.CTkButton(header, text="Light / dark", width=110, height=38,
                      fg_color=PANEL, text_color=TEXT, command=self.app.toggle_theme).pack(side="right")

        filters = ctk.CTkFrame(self, fg_color="transparent")
        filters.pack(fill="x", padx=24, pady=(8, 14))
        ctk.CTkButton(filters, text="‹", width=38, height=40,
                      fg_color=PANEL, text_color=TEXT, command=lambda: self.shift_day(-1)).pack(side="left", padx=(0, 6))
        self.date_var = ctk.StringVar(value=self.selected_day.isoformat())
        self.date_entry = ctk.CTkEntry(filters, textvariable=self.date_var, width=145, height=40)
        self.date_entry.pack(side="left")
        self.date_entry.bind("<Return>", lambda _event: self.apply_date())
        ctk.CTkButton(filters, text="Calendar", width=90, height=40, fg_color=PANEL,
                      text_color=TEXT, command=self.open_calendar).pack(side="left", padx=6)
        ctk.CTkButton(filters, text="›", width=38, height=40, fg_color=PANEL,
                      text_color=TEXT, command=lambda: self.shift_day(1)).pack(side="left")
        ctk.CTkButton(filters, text="View day", width=100, height=40, fg_color=ACCENT,
                      text_color="#20190E", command=self.apply_date).pack(side="left", padx=(12, 6))
        ctk.CTkButton(filters, text="Today", width=85, height=40, fg_color=PANEL,
                      text_color=TEXT, command=lambda: self.set_day(date.today())).pack(side="left")
        ctk.CTkLabel(filters, text="YYYY-MM-DD", text_color=MUTED).pack(side="right")

        summary = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=12)
        summary.pack(fill="x", padx=24, pady=(0, 16))
        info = ctk.CTkFrame(summary, fg_color="transparent")
        info.pack(side="left", padx=20, pady=14)
        self.day_label = ctk.CTkLabel(info, text="", text_color=MUTED, anchor="w")
        self.day_label.pack(fill="x")
        self.total_label = ctk.CTkLabel(info, text="", font=ctk.CTkFont(size=30, weight="bold"), anchor="w")
        self.total_label.pack(fill="x", pady=(2, 0))
        self.count_label = ctk.CTkLabel(summary, text="", font=ctk.CTkFont(size=17), text_color=MUTED)
        self.count_label.pack(side="right", padx=24)

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=(0, 18))
        content.grid_columnconfigure(0, weight=3)
        content.grid_columnconfigure(1, weight=2)
        content.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(content, text="Bills", font=ctk.CTkFont(size=18, weight="bold"),
                     anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(content, text="All dishes sold", font=ctk.CTkFont(size=18, weight="bold"),
                     anchor="w").grid(row=0, column=1, sticky="ew", padx=(16, 0), pady=(0, 8))
        self.bill_list = ctk.CTkScrollableFrame(content, fg_color="transparent")
        self.bill_list.grid(row=1, column=0, sticky="nsew")
        self.top_list = ctk.CTkScrollableFrame(content, fg_color=PANEL, corner_radius=12)
        self.top_list.grid(row=1, column=1, sticky="nsew", padx=(16, 0))
        ctk.CTkLabel(self, text="Deleted bills are voided and excluded from all sales totals.",
                     text_color=MUTED).pack(side="bottom", pady=(0, 12))
        self.refresh()
        self.after(100, self.lift)

    def open_calendar(self):
        DatePicker(self, self.selected_day, self.set_day)

    def apply_date(self):
        try:
            selected = date.fromisoformat(self.date_var.get().strip())
        except ValueError:
            error(self, "Choose a valid date", "Use YYYY-MM-DD, for example 2026-10-14, or choose Calendar.")
            return
        self.set_day(selected)

    def shift_day(self, amount):
        try:
            self.set_day(self.selected_day + timedelta(days=amount))
        except OverflowError:
            pass

    def set_day(self, selected):
        self.selected_day = selected
        self.date_var.set(selected.isoformat())
        self.refresh()

    def refresh(self):
        if not self.winfo_exists():
            return
        day = self.selected_day.isoformat()
        try:
            summary = self.app.db.daily_summary(day)
            bills = self.app.db.list_bills(day)
            top = self.app.db.top_dishes(day)
        except Exception as exc:
            error(self, "Cannot load sales", str(exc))
            return
        self.day_label.configure(text=self.selected_day.strftime("%A, %d %B %Y"))
        self.total_label.configure(text=money(summary["total"]))
        count = summary["count"]
        self.count_label.configure(text=f"{count} {'bill' if count == 1 else 'bills'}")
        for panel in (self.bill_list, self.top_list):
            for child in panel.winfo_children():
                child.destroy()
        if not bills:
            ctk.CTkLabel(self.bill_list, text="No bills for this day.\nYour next sale starts on the billing screen.",
                         text_color=MUTED, justify="center", height=150).pack(fill="x")
        for bill in bills:
            row = ctk.CTkFrame(self.bill_list, fg_color=PANEL, corner_radius=10)
            row.pack(fill="x", pady=4)
            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, padx=14, pady=12)
            title = f"Bill #{bill['bill_no']}" + (" · UDHAAR" if bill.get("customer_id") is not None else "")
            label = ctk.CTkLabel(info, text=title, anchor="w",
                                 font=ctk.CTkFont(size=16, weight="bold"))
            label.pack(fill="x")
            stamp = ctk.CTkLabel(info, text=_timestamp(bill.get("created_at", bill.get("date"))),
                                 text_color=MUTED, anchor="w")
            stamp.pack(fill="x", pady=(2, 0))
            button = ctk.CTkButton(row, text=f"{money(bill['total'])}  ›", height=42, width=150,
                                   fg_color="transparent", text_color=TEXT, anchor="e",
                                   command=lambda bill_id=bill["id"]: self.open_bill(bill_id))
            button.pack(side="right", padx=10)
            for widget in (row, info, label, stamp):
                widget.bind("<Button-1>", lambda _event, bill_id=bill["id"]: self.open_bill(bill_id))
        if not top:
            ctk.CTkLabel(self.top_list, text="Dish totals appear after a sale.", text_color=MUTED,
                         height=120).pack(fill="x")
        for index, dish in enumerate(top, 1):
            frame = ctk.CTkFrame(self.top_list, fg_color="transparent")
            frame.pack(fill="x", padx=8, pady=10)
            ctk.CTkLabel(frame, text=f"{index:02}", width=28, text_color=ACCENT_TEXT,
                         font=ctk.CTkFont(size=17, weight="bold")).pack(side="left", padx=(0, 10))
            info = ctk.CTkFrame(frame, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(info, text=dish["name"], font=ctk.CTkFont(size=14, weight="bold"),
                         anchor="w", wraplength=210, justify="left").pack(fill="x")
            ctk.CTkLabel(info, text=f"{quantity_text(dish['qty'], dish.get('unit', 'count'))} sold  ·  {money(dish['amount'])}",
                         text_color=MUTED, anchor="w").pack(fill="x", pady=(2, 0))

    def open_bill(self, bill_id):
        try:
            bill = self.app.db.get_bill(bill_id)
            if not bill:
                raise ValueError("This bill could not be found.")
            BillDetail(self, bill)
        except Exception as exc:
            error(self, "Cannot open bill", str(exc))

    def void(self, bill, detail=None):
        if not confirm(detail or self, "Delete this bill?",
                       f"Delete bill #{bill['bill_no']} for {money(bill['total'])}?\n"
                       "It will be excluded from sales totals. The saved record will be kept."):
            return
        try:
            self.app.db.void_bill(bill["id"])
            if detail:
                detail.destroy()
            self.refresh()
            self.app.refresh_all()
            self.app.notify(f"Bill #{bill['bill_no']} deleted from sales")
        except Exception as exc:
            error(self, "Cannot delete bill", str(exc))


class BillDetail(ctk.CTkToplevel):
    def __init__(self, sales, bill):
        super().__init__(sales)
        self.app = sales.app
        self.bill = bill
        self.title(f"Bill #{bill['bill_no']}")
        self.geometry("690x640")
        self.minsize(620, 520)
        self.configure(fg_color=BG)
        self.transient(sales)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(20, 12))
        ctk.CTkLabel(header, text=f"Bill #{bill['bill_no']}",
                     font=ctk.CTkFont(size=25, weight="bold")).pack(side="left")
        ctk.CTkButton(header, text="Light / dark", height=36, width=110,
                      fg_color=PANEL, text_color=TEXT, command=self.app.toggle_theme).pack(side="right")
        ctk.CTkLabel(self, text=_timestamp(bill.get("created_at", bill.get("date"))),
                     text_color=MUTED, anchor="w").pack(fill="x", padx=24, pady=(0, 12))
        if bill.get("customer_id") is not None:
            ctk.CTkLabel(self, text=_khata_label(bill), text_color=ACCENT_TEXT,
                         font=ctk.CTkFont(size=17, weight="bold"), anchor="w",
                         justify="left", wraplength=570).pack(fill="x", padx=24, pady=(0, 12))
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(side="bottom", fill="x", padx=20, pady=16)
        ctk.CTkButton(actions, text="Reprint bill", width=125, height=42, fg_color=ACCENT,
                      text_color="#20190E", command=lambda: self.app.print_saved_bill(bill)).pack(side="left", padx=4)
        ctk.CTkButton(actions, text="Preview", width=90, height=42, fg_color=PANEL,
                      text_color=TEXT, command=lambda: self.app.show_receipt(bill)).pack(side="left", padx=4)
        ctk.CTkButton(actions, text="Reprint last", width=110, height=42, fg_color=PANEL,
                      text_color=TEXT, command=self.app.reprint_last).pack(side="left", padx=4)
        ctk.CTkButton(actions, text="Delete bill", width=105, height=42, fg_color=DANGER, text_color="#FFFFFF",
                      command=lambda: sales.void(bill, self)).pack(side="right", padx=4)

        body = ctk.CTkScrollableFrame(self, fg_color=PANEL, corner_radius=12)
        body.pack(fill="both", expand=True, padx=24, pady=(0, 4))
        body.grid_columnconfigure(0, weight=1)
        for column, label in enumerate(("Item", "Qty", "Rate", "Amount")):
            ctk.CTkLabel(body, text=label, text_color=MUTED, anchor="w" if column == 0 else "e").grid(
                row=0, column=column, sticky="ew", padx=8, pady=(10, 8))
        items = bill.get("items", [])
        if isinstance(items, str):
            items = json.loads(items)
        for row, item in enumerate(items, 1):
            values = (item["name"], quantity_text(item["qty"], item.get("unit", "count")),
                      "By portion" if item.get("portion_prices") else money(item["price"]), money(item["amount"]))
            for column, value in enumerate(values):
                ctk.CTkLabel(body, text=value, anchor="w" if column == 0 else "e",
                             wraplength=215 if column == 0 else 120, justify="left").grid(
                                 row=row, column=column, sticky="ew", padx=8, pady=8)
        row = len(items) + 1
        ctk.CTkFrame(body, height=1, fg_color=MUTED).grid(row=row, column=0, columnspan=4, sticky="ew", padx=8, pady=12)
        order = bill.get("order_info", {})
        if isinstance(order, str):
            try:
                order = json.loads(order)
            except (TypeError, ValueError):
                order = {}
        if not isinstance(order, dict):
            order = {}
        totals = [("Subtotal", "subtotal"), ("Tax", "tax"), ("Discount", "discount")]
        if bill.get("delivery_fee", 0) > 0 or order.get("order_type") == "Delivery":
            totals.append(("Delivery", "delivery_fee"))
        totals.append(("Total", "total"))
        for label, key in totals:
            row += 1
            total = key == "total"
            font = ctk.CTkFont(size=22 if total else 14, weight="bold" if total else "normal")
            ctk.CTkLabel(body, text=label, font=font, anchor="w").grid(row=row, column=0, columnspan=2, sticky="ew", padx=8, pady=5)
            value = money(bill.get(key, 0))
            if key == "discount" and bill.get(key, 0):
                value = "− " + value
            ctk.CTkLabel(body, text=value, font=font, anchor="e").grid(row=row, column=2, columnspan=2, sticky="ew", padx=8, pady=5)
        if order:
            labels = {"order_type": "Order", "table": "Table", "waiter": "Waiter",
                      "customer_name": "Customer", "customer_phone": "Phone"}
            text = "  ·  ".join(f"{label}: {order[key]}" for key, label in labels.items() if order.get(key))
            if text:
                ctk.CTkLabel(body, text=text, text_color=MUTED, wraplength=560,
                             justify="left").grid(row=row + 1, column=0, columnspan=4, sticky="w", padx=8, pady=16)
        self.after(100, self.lift)
        self.bind("<Escape>", lambda _event: self.destroy())
