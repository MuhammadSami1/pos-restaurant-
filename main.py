"""Restaurant Billing: launches directly into the offline billing screen."""
from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal
import queue
import threading
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from app_config import load_config, save_config
from db import Database
from models import (amount, calculate_totals, item_amount, money, normalize_items,
                    quantity_text, validate_quantity)
from ui_common import (ACCENT, ACCENT_TEXT, SELECTION, SELECTION_HOVER, DANGER_HOVER,
                       BG, PANEL, MUTED, TEXT, DANGER, GREEN, confirm, error, thumbnail,
                       logo_thumbnail, modal, configure_theme, bring_to_front)


class QuantityDialog(ctk.CTkToplevel):
    def __init__(self, app, item, callback, initial=1, editing=False):
        super().__init__(app)
        self.title("Change quantity" if editing else "Add quantity")
        modal(self, app, 460, 485)
        self.resizable(False, False)
        self.unit = item.get("unit", item.get("sell_type", "count"))
        self.item = copy.deepcopy(item)
        self.value = tk.StringVar(value=str(initial))
        self.callback = callback
        ctk.CTkLabel(self, text=item["name"], font=("Segoe UI", 22, "bold"), wraplength=420).pack(padx=20, pady=(22, 4))
        ctk.CTkLabel(self, text="Weight in kilograms" if self.unit == "kg" else "Number of items", text_color=MUTED).pack()
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(padx=24, pady=18, fill="x")
        ctk.CTkButton(row, text="−", width=65, height=48, command=lambda: self.step(-1)).pack(side="left")
        self.entry = ctk.CTkEntry(row, textvariable=self.value, width=210, height=48, justify="center", font=("Segoe UI", 25))
        self.entry.pack(side="left", padx=12)
        ctk.CTkButton(row, text="+", width=65, height=48, command=lambda: self.step(1)).pack(side="left")
        presets = ctk.CTkFrame(self, fg_color="transparent")
        presets.pack()
        values = [0.25, 0.5, 0.75, 1, 1.5, 2, 2.5] if self.unit == "kg" else [0.5, 1, 2, 3, 4, 5]
        for index, val in enumerate(values):
            ctk.CTkButton(presets, text=str(val), width=90, height=38, fg_color=PANEL, text_color=TEXT,
                          command=lambda n=val: self.value.set(str(n))).grid(row=index // 4, column=index % 4, padx=4, pady=4)
        prices = item.get("portion_prices") or {}
        price_text = "  ·  ".join(f"{qty}{' kg' if self.unit == 'kg' else ''}: {money(price)}"
                                  for qty, price in prices.items())
        ctk.CTkLabel(self, text=price_text, text_color=MUTED, wraplength=400).pack(padx=24, pady=(6, 0))
        self.line_total = ctk.CTkLabel(self, text="", font=("Segoe UI", 18, "bold"))
        self.line_total.pack(pady=(4, 0))
        self.problem = ctk.CTkLabel(self, text="", text_color=DANGER)
        self.problem.pack()
        ctk.CTkButton(self, text="Update quantity" if editing else "Add to bill", height=44, width=392,
                      fg_color=ACCENT, text_color="#20180E", command=self.submit).pack(padx=24)
        self.bind("<Return>", lambda e: self.submit())
        self.bind("<Escape>", lambda e: self.destroy())
        self.value.trace_add("write", lambda *_: self.update_amount())
        self.update_amount()
        self.after(150, self.entry.focus_set)

    def update_amount(self):
        try:
            self.line_total.configure(text=f"Amount: {money(item_amount(self.item, self.value.get()))}")
            self.problem.configure(text="")
        except ValueError:
            self.line_total.configure(text="Amount: —")

    def step(self, direction):
        try:
            current = validate_quantity(self.value.get(), self.unit)
            step = 0.25 if self.unit == "kg" else 0.5
            updated = max(Decimal(str(step)), Decimal(str(current)) + Decimal(str(direction * step)))
            item_amount(self.item, updated)
            self.value.set(format(updated.normalize(), "f"))
        except ValueError as exc:
            self.problem.configure(text=str(exc))

    def submit(self):
        try:
            qty = validate_quantity(self.value.get(), self.unit)
            item_amount(self.item, qty)
            self.callback(qty)
        except ValueError as exc:
            self.problem.configure(text=str(exc))
            return
        self.destroy()


class ReceiptPreview(ctk.CTkToplevel):
    def __init__(self, app, bill, config):
        super().__init__(app)
        from printer import render_receipt
        self.title("Receipt preview")
        self.geometry("690x720")
        self.configure(fg_color=BG)
        self.transient(app)
        self.bill, self.receipt_config = bill, config
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=12)
        ctk.CTkLabel(bar, text="Receipt preview", font=("Segoe UI", 20, "bold")).pack(side="left")
        ctk.CTkButton(bar, text="Light / Dark", width=100, command=app.toggle_theme).pack(side="right")
        scroll = ctk.CTkScrollableFrame(self, fg_color=("#DEDAD4", "#101215"))
        scroll.pack(fill="both", expand=True, padx=16)
        receipt = render_receipt(bill, config)
        width = min(576, receipt.width)
        size = (width, round(receipt.height * width / receipt.width))
        self.image = ctk.CTkImage(light_image=receipt, dark_image=receipt, size=size)
        ctk.CTkLabel(scroll, text="", image=self.image).pack(pady=16)
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=16, pady=12)
        ctk.CTkButton(actions, text="Print this receipt", height=40, fg_color=ACCENT, text_color="#20180E",
                      command=lambda: app.print_saved_bill(bill, config)).pack(side="left", expand=True, padx=4)
        ctk.CTkButton(actions, text="Reprint last bill", height=40, command=app.reprint_last).pack(side="left", expand=True, padx=4)


class BillingApp(ctk.CTk):
    def __init__(self):
        self.config = load_config()
        ctk.set_appearance_mode(self.config.get("theme", "dark"))
        configure_theme()
        super().__init__()
        self.title("Restaurant Billing")
        self.geometry("1320x690")
        self.minsize(1120, 680)
        self.configure(fg_color=BG)
        self.db = Database()
        self.cart = []
        self._cart_rows = {}
        self._empty_labels = []
        self._header_logo = None
        self.delivery_enabled = tk.BooleanVar(value=False)
        self.delivery_value = tk.StringVar(value=str(self.config.get("delivery_fee", 0)))
        self._delivery_dirty = False
        self.printing = False
        self._print_events = queue.Queue()
        self._last_day = date.today().isoformat()
        self._settings = self._sales = None
        self._search_after = self._grid_after = None
        self._columns = 3
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._build_body()
        self.status = ctk.CTkLabel(self, text="Ready  ·  All data stays on this PC", text_color=MUTED, anchor="w", height=24, font=("Segoe UI", 11))
        self.status.grid(row=2, column=0, sticky="ew", padx=22)
        self.refresh_all()
        self.bind("<Return>", self._enter)
        self.bind("<Escape>", self._escape)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(120, self._poll_print)
        self.after(15000, self._day_tick)

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0, height=70)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.grid(row=0, column=0, padx=22, pady=12, sticky="w")
        self.logo_label = ctk.CTkLabel(brand, text="", width=48, height=48)
        self.brand_text = ctk.CTkFrame(brand, fg_color="transparent")
        self.brand_text.pack(side="left")
        self.restaurant = ctk.CTkLabel(self.brand_text, text="", font=("Segoe UI", 21, "bold"), anchor="w")
        self.restaurant.pack(anchor="w")
        ctk.CTkLabel(self.brand_text, text="RESTAURANT BILLING", font=("Segoe UI", 10, "bold"), text_color=MUTED).pack(anchor="w")
        self.today_label = ctk.CTkLabel(header, text="", text_color=GREEN, font=("Segoe UI", 15, "bold"))
        self.today_label.grid(row=0, column=1, padx=12)
        self.theme_btn = ctk.CTkButton(header, text="☀  Light", width=90, height=38, fg_color=BG, text_color=TEXT, command=self.toggle_theme)
        self.theme_btn.grid(row=0, column=2, padx=5)
        ctk.CTkButton(header, text="Sales", width=90, height=38, fg_color=BG, text_color=TEXT, command=self.open_sales).grid(row=0, column=3, padx=5)
        ctk.CTkButton(header, text="⚙  Settings", width=110, height=38, fg_color=BG, text_color=TEXT, command=self.open_settings).grid(row=0, column=4, padx=(5, 20))

    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(16, 0))
        body.grid_columnconfigure(0, weight=1, minsize=500, uniform="panes")
        body.grid_columnconfigure(1, weight=1, minsize=520, uniform="panes")
        body.grid_rowconfigure(0, weight=1)
        menu = ctk.CTkFrame(body, fg_color="transparent")
        menu.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        menu.grid_columnconfigure(0, weight=1)
        menu.grid_rowconfigure(2, weight=1)
        title = ctk.CTkFrame(menu, fg_color="transparent")
        title.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkLabel(title, text="Choose dishes", font=("Segoe UI", 23, "bold")).pack(side="left")
        ctk.CTkButton(title, text="+ Add dish", width=100, height=34, fg_color=PANEL, text_color=TEXT, command=self.add_dish).pack(side="right")
        filters = ctk.CTkFrame(menu, fg_color="transparent")
        filters.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.search = tk.StringVar()
        self.search.trace_add("write", self._search_changed)
        ctk.CTkLabel(filters, text="Search", text_color=MUTED).pack(side="left", padx=(0, 8))
        ctk.CTkEntry(filters, textvariable=self.search, placeholder_text="Search dishes…", height=38).pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.category = tk.StringVar(value="All")
        self.category_menu = ctk.CTkOptionMenu(filters, values=["All"], variable=self.category, width=140, height=38, fg_color=PANEL, text_color=TEXT, command=lambda _: self.render_dishes())
        self.category_menu.pack(side="right")
        self.dish_grid = ctk.CTkScrollableFrame(menu, fg_color="transparent", corner_radius=0)
        self.dish_grid.grid(row=2, column=0, sticky="nsew")
        menu.bind("<Configure>", self._resize_menu)
        ctk.CTkLabel(menu, text="Tap to add  ·  Hold for quantity  ·  ✎ to edit dish", text_color=MUTED, font=("Segoe UI", 11)).grid(row=3, column=0, sticky="w", pady=(6, 0))
        cart = ctk.CTkFrame(body, fg_color=PANEL, corner_radius=14)
        cart.grid(row=0, column=1, sticky="nsew")
        cart.grid_columnconfigure(0, weight=1)
        cart.grid_rowconfigure(4, weight=1)
        cart_title = ctk.CTkFrame(cart, fg_color="transparent")
        cart_title.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 6))
        ctk.CTkLabel(cart_title, text="Current bill", font=("Segoe UI", 23, "bold")).pack(side="left")
        self.item_count = ctk.CTkLabel(cart_title, text="0 items", text_color=MUTED)
        self.item_count.pack(side="right")
        self.order_type = tk.StringVar(value="Dine-in")
        ctk.CTkSegmentedButton(cart, values=["Dine-in", "Takeaway", "Delivery"], variable=self.order_type, height=35,
                               selected_color=SELECTION, selected_hover_color=SELECTION_HOVER, text_color=TEXT,
                               command=self._select_order_type).grid(row=1, column=0, padx=18, pady=(0, 8), sticky="ew")
        self.order_fields = ctk.CTkFrame(cart, fg_color="transparent", height=34)
        self.order_fields.grid(row=2, column=0, padx=18, sticky="ew")
        self.table_var, self.waiter_var = tk.StringVar(), tk.StringVar()
        self.customer_var, self.phone_var = tk.StringVar(), tk.StringVar()
        self._order_fields()
        columns = ctk.CTkFrame(cart, fg_color=BG, corner_radius=0)
        columns.grid(row=3, column=0, sticky="ew", padx=18, pady=(10, 0))
        columns.grid_columnconfigure(0, weight=1)
        for col, text, width in [(0, "ITEM / QUANTITY", 185), (1, "RATE", 95), (2, "AMOUNT", 105)]:
            ctk.CTkLabel(columns, text=text, width=width, anchor="w" if col == 0 else "e", text_color=MUTED, font=("Segoe UI", 10, "bold")).grid(row=0, column=col, padx=8)
        self.bill_rows = ctk.CTkScrollableFrame(cart, fg_color="transparent")
        self.bill_rows.grid(row=4, column=0, sticky="nsew", padx=12)
        self.bill_rows.grid_columnconfigure(0, weight=1)
        summary = ctk.CTkFrame(cart, fg_color="transparent")
        summary.grid(row=5, column=0, sticky="ew", padx=18, pady=(2, 12))
        summary.grid_columnconfigure(1, weight=1)
        amounts = ctk.CTkFrame(summary, fg_color="transparent")
        amounts.grid(row=0, column=0, columnspan=2, sticky="ew")
        amounts.grid_columnconfigure(1, weight=1)
        self.subtotal_label = ctk.CTkLabel(amounts, text="Subtotal", text_color=MUTED)
        self.subtotal_label.grid(row=0, column=0, sticky="w")
        self.subtotal_value = ctk.CTkLabel(amounts, text="", text_color=TEXT)
        self.subtotal_value.grid(row=0, column=1, sticky="w", padx=(8, 16))
        discount_row = ctk.CTkFrame(summary, fg_color="transparent")
        discount_row.grid(row=1, column=0, sticky="w")
        ctk.CTkLabel(discount_row, text="Discount (food + tax)", text_color=MUTED).pack(side="left", padx=(0, 8))
        self.discount_value = tk.StringVar(value="0")
        self.discount_mode = tk.StringVar(value="Rs.")
        ctk.CTkEntry(discount_row, textvariable=self.discount_value, width=74, height=30).pack(side="left", padx=4)
        ctk.CTkOptionMenu(discount_row, values=["Rs.", "%"], variable=self.discount_mode, width=68, height=30,
                          fg_color=BG, text_color=TEXT).pack(side="left")
        self.discount_total = ctk.CTkLabel(summary, text="", text_color=TEXT)
        self.discount_total.grid(row=1, column=1, sticky="e")
        self.tax_label = ctk.CTkLabel(amounts, text="Tax", text_color=MUTED)
        self.tax_label.grid(row=0, column=2, sticky="e", padx=(0, 8))
        self.tax_value = ctk.CTkLabel(amounts, text="", text_color=TEXT)
        self.tax_value.grid(row=0, column=3, sticky="e")
        delivery_row = ctk.CTkFrame(summary, fg_color="transparent")
        delivery_row.grid(row=2, column=0, sticky="w", pady=(3, 3))
        self.delivery_switch = ctk.CTkSwitch(delivery_row, text="Delivery", variable=self.delivery_enabled,
                                             width=100, command=self._toggle_delivery)
        self.delivery_switch.pack(side="left", padx=(0, 6))
        self.delivery_minus = ctk.CTkButton(delivery_row, text="−10", width=42, height=30, fg_color=BG,
                                           text_color=TEXT, command=lambda: self.change_delivery_fee(-10))
        self.delivery_minus.pack(side="left")
        self.delivery_entry = ctk.CTkEntry(delivery_row, textvariable=self.delivery_value, width=76,
                                           height=30, justify="center")
        self.delivery_entry.pack(side="left", padx=4)
        self.delivery_plus = ctk.CTkButton(delivery_row, text="+10", width=42, height=30, fg_color=BG,
                                          text_color=TEXT, command=lambda: self.change_delivery_fee(10))
        self.delivery_plus.pack(side="left")
        self.delivery_total = ctk.CTkLabel(summary, text="Off", text_color=MUTED)
        self.delivery_total.grid(row=2, column=1, sticky="e")
        self.validation = ctk.CTkLabel(summary, text="", text_color=DANGER, height=18, font=("Segoe UI", 11))
        self.validation.grid(row=3, column=0, columnspan=2, sticky="w")
        ctk.CTkLabel(summary, text="TOTAL", font=("Segoe UI", 17, "bold")).grid(row=4, column=0, sticky="w")
        self.total_label = ctk.CTkLabel(summary, text="Rs. 0", text_color=ACCENT_TEXT, font=("Segoe UI", 32, "bold"))
        self.total_label.grid(row=4, column=1, sticky="e")
        actions = ctk.CTkFrame(summary, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 8))
        self.clear_btn = ctk.CTkButton(actions, text="Clear", width=85, height=48, fg_color=BG, text_color=TEXT, command=self.clear_cart)
        self.clear_btn.pack(side="left", padx=(0, 8))
        self.print_btn = ctk.CTkButton(actions, text="PRINT BILL  ↵", height=48, fg_color=ACCENT, hover_color="#CE852A", text_color="#23180B", font=("Segoe UI", 17, "bold"), command=self.checkout)
        self.print_btn.pack(side="left", fill="x", expand=True)
        extras = ctk.CTkFrame(summary, fg_color="transparent")
        extras.grid(row=6, column=0, columnspan=2, sticky="ew")
        ctk.CTkButton(extras, text="Reprint last", height=31, fg_color=BG, text_color=TEXT, command=self.reprint_last).pack(side="left", fill="x", expand=True, padx=(0, 5))
        ctk.CTkButton(extras, text="Delete last bill", height=31, fg_color=BG, text_color=DANGER, command=self.delete_last).pack(side="left", fill="x", expand=True, padx=(5, 0))
        self.discount_value.trace_add("write", lambda *_: self.update_totals())
        self.discount_mode.trace_add("write", lambda *_: self.update_totals())
        self.delivery_value.trace_add("write", self._delivery_edited)
        self._update_delivery_controls()

    def _select_order_type(self, value):
        self.order_type.set(value)
        self.delivery_enabled.set(value == "Delivery")
        self._order_fields()
        self._update_delivery_controls()
        self.update_totals()

    def _toggle_delivery(self):
        if self.delivery_enabled.get():
            self.order_type.set("Delivery")
        elif self.order_type.get() == "Delivery":
            self.order_type.set("Dine-in")
        self._order_fields()
        self._update_delivery_controls()
        self.update_totals()

    def _update_delivery_controls(self):
        state = "normal" if self.delivery_enabled.get() else "disabled"
        for widget in (self.delivery_entry, self.delivery_minus, self.delivery_plus):
            widget.configure(state=state)

    def _delivery_edited(self, *_):
        self._delivery_dirty = True
        self.update_totals()

    def change_delivery_fee(self, step):
        if not self.delivery_enabled.get():
            return
        try:
            fee = amount(self.delivery_value.get() or "0", "Delivery charge")
            value = amount(max(0, Decimal(str(fee)) + Decimal(str(step))), "Delivery charge")
            self.delivery_value.set(f"{value:.2f}".removesuffix(".00"))
        except ValueError as exc:
            error(self, "Invalid delivery rate", exc)

    def _order_fields(self):
        for widget in self.order_fields.winfo_children():
            widget.destroy()
        kind = self.order_type.get()
        if kind == "Takeaway":
            ctk.CTkLabel(self.order_fields, text="Packed to go", height=34, text_color=MUTED).pack(side="left")
            return
        fields = [("Table", self.table_var), ("Waiter", self.waiter_var)] if kind == "Dine-in" else [("Customer name", self.customer_var), ("Phone", self.phone_var)]
        for label, variable in fields:
            block = ctk.CTkFrame(self.order_fields, fg_color="transparent")
            block.pack(side="left", fill="x", expand=True, padx=(0, 6))
            ctk.CTkLabel(block, text=label, text_color=MUTED, font=("Segoe UI", 11)).pack(side="left", padx=(0, 5))
            ctk.CTkEntry(block, textvariable=variable, width=105, height=32).pack(side="left", fill="x", expand=True)

    def _search_changed(self, *_):
        if self._search_after:
            self.after_cancel(self._search_after)
        self._search_after = self.after(120, self.render_dishes)

    def _resize_menu(self, event):
        cols = 3 if event.width >= 600 else 2
        if cols != self._columns:
            self._columns = cols
            if self._grid_after:
                self.after_cancel(self._grid_after)
            self._grid_after = self.after(120, self.render_dishes)

    def refresh_all(self):
        if not self._delivery_dirty:
            self.delivery_value.set(str(self.config.get("delivery_fee", 0)))
            self._delivery_dirty = False
        self.restaurant.configure(text=self.config["restaurant_name"])
        self._header_logo = logo_thumbnail(self.config.get("logo_path", ""))
        if self._header_logo:
            self.logo_label.configure(image=self._header_logo)
            self.logo_label.pack(side="left", before=self.brand_text, padx=(0, 12))
        else:
            self.logo_label.pack_forget()
            self.logo_label.configure(image=None)
            self.logo_label._label.configure(image="")
        self.theme_btn.configure(text="☀  Light" if self.config.get("theme") == "dark" else "☾  Dark")
        dishes = self.db.list_dishes()
        values = ["All"] + sorted({d.get("category", "General") or "General" for d in dishes})
        self.category_menu.configure(values=values)
        if self.category.get() not in values:
            self.category.set("All")
        self.render_dishes()
        self.render_cart()
        self.refresh_sales()
        if self._settings and self._settings.winfo_exists():
            self._settings.refresh_dishes()

    def refresh_sales(self):
        summary = self.db.daily_summary(date.today().isoformat())
        self.today_label.configure(text=f"Today: {money(summary['total'])}  ·  {summary['count']} bills")
        if self._sales and self._sales.winfo_exists():
            self._sales.refresh()

    def render_dishes(self):
        self._search_after = self._grid_after = None
        for child in self.dish_grid.winfo_children():
            child.destroy()
        for col in range(3):
            self.dish_grid.grid_columnconfigure(col, weight=1 if col < self._columns else 0, uniform="dishes" if col < self._columns else "")
        query = self.search.get().casefold().strip()
        dishes = [d for d in self.db.list_dishes() if query in d["name"].casefold() and (self.category.get() == "All" or d["category"] == self.category.get())]
        for idx, dish in enumerate(dishes):
            card = ctk.CTkFrame(self.dish_grid, fg_color=PANEL, corner_radius=12)
            card.grid(row=idx // self._columns, column=idx % self._columns, sticky="nsew", padx=4, pady=5)
            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=6, pady=(5, 0))
            ctk.CTkLabel(top, text=dish.get("category", "General")[:14].upper(), font=("Segoe UI", 9, "bold"), text_color=MUTED).pack(side="left")
            ctk.CTkButton(top, text="✕", width=26, height=26, fg_color="transparent", text_color=DANGER, hover_color=DANGER_HOVER,
                          command=lambda d=dish: self.delete_dish(d)).pack(side="right")
            ctk.CTkButton(top, text="✎", width=26, height=26, fg_color="transparent", text_color=MUTED,
                          command=lambda d=dish: self.edit_dish(d)).pack(side="right")
            photo = thumbnail(dish.get("image_path"))
            rate = money(dish["price"]) + (" / kg" if dish["sell_type"] == "kg" else "")
            name = self._wrap_name(dish["name"], 19)
            text = f"{name}\n{rate}"
            button = ctk.CTkButton(card, text=text, image=photo, compound="top", height=165 if photo else 130,
                                   fg_color="transparent", hover_color=("#F2E6D2", "#35312B"), text_color=TEXT,
                                   font=("Segoe UI", 14, "bold"), corner_radius=10)
            button._dish_image = photo
            button._hold_job, button._held = None, False
            button.configure(command=lambda d=dish, b=button: self._tap(d, b))
            button.bind("<ButtonPress-1>", lambda e, d=dish, b=button: self._hold_start(d, b), add="+")
            button.bind("<ButtonRelease-1>", lambda e, b=button: self._hold_cancel(b), add="+")
            button.bind("<Leave>", lambda e, b=button: self._hold_cancel(b), add="+")
            button.pack(fill="both", expand=True, padx=4, pady=(0, 6))
        if not dishes:
            ctk.CTkLabel(self.dish_grid, text="No dishes found.\nAdd a dish or change the filter.", text_color=MUTED, height=200).grid(row=0, column=0, columnspan=self._columns)

    @staticmethod
    def _wrap_name(name, width):
        import textwrap
        return "\n".join(textwrap.wrap(name, width=width) or [name])

    def _hold_start(self, dish, button):
        button._held = False
        self._hold_cancel(button)
        button._hold_job = self.after(550, lambda: self._hold_fire(dish, button))

    def _hold_cancel(self, button):
        if button._hold_job:
            self.after_cancel(button._hold_job)
            button._hold_job = None

    def _hold_fire(self, dish, button):
        if not button.winfo_exists():
            return
        button._held = True
        button._hold_job = None
        QuantityDialog(self, dish, lambda qty: self.add_item(dish, qty))

    def _tap(self, dish, button):
        if button._held:
            button._held = False
            return
        try:
            self.add_item(dish)
        except ValueError as exc:
            error(self, "Cannot add quantity", exc)

    def add_item(self, dish, qty=1):
        line = normalize_items([{**dish, "dish_id": dish["id"], "qty": qty}])[0]
        # Price/type edits do not retroactively change an order already on screen.
        existing = next((i for i in self.cart if all(i.get(key) == line.get(key)
                         for key in ("dish_id", "price", "unit", "name", "portion_prices"))), None)
        if existing:
            new_qty = validate_quantity(Decimal(str(existing["qty"])) + Decimal(str(line["qty"])), existing["unit"])
            new_amount = item_amount(existing, new_qty)
            existing.update(qty=new_qty, amount=new_amount)
        else:
            self.cart.append(line)
        self.render_cart()

    def render_cart(self):
        # Keep existing widgets and scroll position: a quantity tap must not
        # destroy/repaint the whole bill or replace the button being pressed.
        live_keys = {id(item) for item in self.cart}
        for key in list(self._cart_rows):
            if key not in live_keys:
                self._cart_rows.pop(key)["frame"].destroy()
        count_text = f"{len(self.cart)} {'item' if len(self.cart) == 1 else 'items'}"
        if self.item_count.cget("text") != count_text:
            self.item_count.configure(text=count_text)
        if not self.cart:
            if not self._empty_labels:
                heading = ctk.CTkLabel(self.bill_rows, text="Your next order starts here", font=("Segoe UI", 19, "bold"), text_color=MUTED)
                heading.grid(row=0, column=0, pady=(42, 4))
                hint = ctk.CTkLabel(self.bill_rows, text="Tap a dish on the left to add it to the bill.", text_color=MUTED)
                hint.grid(row=1, column=0, pady=(0, 30))
                self._empty_labels = [heading, hint]
        elif self._empty_labels:
            for widget in self._empty_labels:
                widget.destroy()
            self._empty_labels.clear()
        for idx, item in enumerate(self.cart):
            key = id(item)
            if key in self._cart_rows:
                widgets = self._cart_rows[key]
                if widgets["index"] != idx:
                    widgets["frame"].grid_configure(row=idx)
                    widgets["index"] = idx
                for label_key, value in [("quantity", quantity_text(item["qty"], item["unit"])), ("amount", money(item["amount"]))]:
                    if widgets[label_key].cget("text") != value:
                        widgets[label_key].configure(text=value)
                continue
            row = ctk.CTkFrame(self.bill_rows, fg_color=BG, corner_radius=8)
            row.grid(row=idx, column=0, sticky="ew", pady=4)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=item["name"], height=22, font=("Segoe UI", 14, "bold"), anchor="w", wraplength=245).grid(row=0, column=0, sticky="w", padx=10, pady=(5, 0))
            rate = "By portion" if item.get("portion_prices") else money(item["price"])
            ctk.CTkLabel(row, text=rate, height=22, width=88, anchor="e", text_color=MUTED, font=("Segoe UI", 12)).grid(row=0, column=1, padx=4)
            amount_label = ctk.CTkLabel(row, text=money(item["amount"]), height=22, width=102, anchor="e", font=("Segoe UI", 14, "bold"))
            amount_label.grid(row=0, column=2, padx=(4, 10))
            controls = ctk.CTkFrame(row, fg_color="transparent")
            controls.grid(row=1, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 5))
            quantity_label = ctk.CTkLabel(controls, text=quantity_text(item["qty"], item["unit"]), width=65, anchor="w", text_color=ACCENT_TEXT)
            quantity_label.pack(side="left")
            for label, command in [("−", lambda i=item: self.change_qty(i, -1)), ("+", lambda i=item: self.change_qty(i, 1)), ("✎", lambda i=item: self.edit_qty(i)), ("✕", lambda i=item: self.remove_item(i))]:
                ctk.CTkButton(controls, text=label, width=35, height=28, fg_color=PANEL, text_color=DANGER if label == "✕" else TEXT, command=command).pack(side="left", padx=3)
            self._cart_rows[key] = {"frame": row, "quantity": quantity_label, "amount": amount_label, "index": idx}
        self.update_totals()

    def change_qty(self, item, delta):
        step = 0.25 if item["unit"] == "kg" else 0.5
        qty = float(Decimal(str(item["qty"])) + Decimal(str(delta * step)))
        if qty <= 0:
            self.remove_item(item)
            return
        try:
            self.set_qty(item, qty)
        except ValueError as exc:
            error(self, "Cannot change quantity", exc)

    def set_qty(self, item, qty):
        new_qty = validate_quantity(qty, item["unit"])
        new_amount = item_amount(item, new_qty)
        item.update(qty=new_qty, amount=new_amount)
        self.render_cart()

    def edit_qty(self, item):
        QuantityDialog(self, item, lambda qty: self.set_qty(item, qty), item["qty"], editing=True)

    def remove_item(self, item):
        self.cart.remove(item)
        self.render_cart()

    def totals(self):
        fee = (self.delivery_value.get() or "0") if self.delivery_enabled.get() else 0
        return calculate_totals(self.cart, self.discount_value.get() or "0", self.discount_mode.get(), self.config.get("tax_percent", 0), fee)

    def update_totals(self):
        try:
            totals = self.totals()
        except ValueError as exc:
            self.validation.configure(text=str(exc))
            self.validation.grid()
            self.print_btn.configure(state="disabled")
            self.total_label.configure(text="—")
            return
        self.validation.configure(text="")
        self.validation.grid_remove()
        self.subtotal_value.configure(text=money(totals["subtotal"]))
        self.discount_total.configure(text="− " + money(totals["discount"]))
        self.tax_label.configure(text=f"Tax ({self.config.get('tax_percent', 0):g}%)")
        self.tax_value.configure(text=money(totals["tax"]))
        self.delivery_total.configure(text=money(totals["delivery_fee"]) if self.delivery_enabled.get() else "Off")
        self.total_label.configure(text=money(totals["total"]))
        self.print_btn.configure(state="normal" if self.cart and not self.printing else "disabled")

    def clear_cart(self, ask=True):
        if ask and self.cart and not confirm(self, "Clear current bill", "Remove all items from this unprinted bill?"):
            return
        self.cart.clear()
        self.delivery_enabled.set(False)
        self.delivery_value.set(str(self.config.get("delivery_fee", 0)))
        self._delivery_dirty = False
        self._update_delivery_controls()
        self.discount_value.set("0")
        self.discount_mode.set("Rs.")
        self.order_type.set("Dine-in")
        for var in [self.table_var, self.waiter_var, self.customer_var, self.phone_var]:
            var.set("")
        self._order_fields()
        self.render_cart()

    def checkout(self):
        if self.printing or not self.cart:
            return
        try:
            totals = self.totals()
            kind = self.order_type.get()
            order = {"order_type": kind, "table": self.table_var.get().strip() if kind == "Dine-in" else "",
                     "waiter": self.waiter_var.get().strip() if kind == "Dine-in" else "",
                     "customer_name": self.customer_var.get().strip() if kind == "Delivery" else "",
                     "customer_phone": self.phone_var.get().strip() if kind == "Delivery" else ""}
            bill = self.db.save_bill(copy.deepcopy(self.cart), order_info=order, **totals)
        except Exception as exc:
            error(self, "Bill was not saved", exc)
            return
        self.clear_cart(ask=False)
        self.refresh_sales()
        self.print_saved_bill(bill, saved_new=True)

    def print_saved_bill(self, bill, config=None, saved_new=False):
        if self.printing:
            self.notify("A receipt is already printing. Please wait.")
            return
        if bill.get("id") and not bill.get("is_test"):
            current = self.db.get_bill(bill["id"])
            if not current or current.get("status") != "active":
                error(self, "Bill deleted", "This bill has been deleted. Choose an active bill in Sales to reprint.")
                return
            bill = current
        self.printing = True
        self.update_totals()
        snapshot = copy.deepcopy(config if config is not None else self.config)
        self.notify(f"Printing bill #{bill.get('bill_no', 'TEST')}…")
        def work():
            try:
                from printer import print_bill
                print_bill(bill, snapshot)
                self._print_events.put((bill, saved_new, None))
            except Exception as exc:
                self._print_events.put((bill, saved_new, str(exc)))
        threading.Thread(target=work, daemon=True, name="receipt-printer").start()

    def _poll_print(self):
        try:
            bill, saved_new, problem = self._print_events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.printing = False
            self.update_totals()
            if problem:
                saved = f"Bill #{bill['bill_no']} is saved and included in sales.\nUse REPRINT LAST or Sales → Reprint to retry without creating another sale.\nUse Delete last bill if the sale should be cancelled.\n\n" if saved_new else "Reprinting does not add another sale.\n\n"
                self.notify("Printer needs attention. Saved bills are safe.")
                error(self, "Could not print receipt", saved + problem)
            else:
                self.notify(f"Bill #{bill.get('bill_no', 'TEST')} sent to printer. Ready for the next order.")
        self.after(120, self._poll_print)

    def show_receipt(self, bill, config=None):
        try:
            ReceiptPreview(self, bill, copy.deepcopy(config if config is not None else self.config))
        except Exception as exc:
            error(self, "Could not preview receipt", exc)

    def reprint_last(self):
        bill = self.db.last_bill()
        if not bill:
            messagebox.showinfo("Reprint last bill", "There are no active bills to reprint.", parent=self)
            return
        self.print_saved_bill(bill)

    def delete_last(self):
        if self.printing:
            self.notify("Wait for printing to finish before deleting a bill.")
            return
        bill = self.db.last_bill()
        if not bill:
            messagebox.showinfo("Delete last bill", "There are no active bills to delete.", parent=self)
            return
        if confirm(self, "Delete last bill", f"Void bill #{bill['bill_no']} from {bill['created_at']} for {money(bill['total'])}?\n\nIt will be removed from sales totals. Its record will be kept."):
            try:
                self.db.void_bill(bill["id"])
                self.refresh_sales()
                if self._sales and self._sales.winfo_exists():
                    self._sales.refresh()
                self.notify(f"Bill #{bill['bill_no']} deleted from sales totals.")
            except Exception as exc:
                error(self, "Could not delete bill", exc)

    def add_dish(self):
        from settings_screen import DishEditor
        DishEditor(self, on_saved=self.refresh_all)

    def edit_dish(self, dish):
        from settings_screen import DishEditor
        DishEditor(self, dish, on_saved=self.refresh_all)

    def delete_dish(self, dish):
        if confirm(self, "Delete dish", f"Delete {dish['name']} from the menu?\nExisting bills and current order items will be kept."):
            try:
                self.db.delete_dish(dish["id"])
                self.refresh_all()
            except Exception as exc:
                error(self, "Could not delete dish", exc)

    def open_settings(self):
        if self._raise_active_dialog():
            return
        if self._settings and self._settings.winfo_exists():
            bring_to_front(self._settings)
            return
        from settings_screen import SettingsScreen
        self._settings = SettingsScreen(self)
        bring_to_front(self._settings)

    def open_sales(self):
        if self._raise_active_dialog():
            return
        if self._sales and self._sales.winfo_exists():
            bring_to_front(self._sales)
            return
        from sales_screen import SalesScreen
        self._sales = SalesScreen(self)
        bring_to_front(self._sales)

    def _raise_active_dialog(self):
        grabbed = self.grab_current()
        if grabbed is not None and grabbed.winfo_exists() and grabbed.winfo_toplevel() != self:
            bring_to_front(grabbed.winfo_toplevel())
            self.notify("Finish or cancel the open dialog to continue.")
            return True
        return False

    def toggle_theme(self):
        updated = copy.deepcopy(self.config)
        updated["theme"] = "light" if self.config.get("theme", "dark") == "dark" else "dark"
        try:
            self.config = save_config(updated)
        except Exception as exc:
            error(self, "Theme could not be saved", exc)
            return
        # Changing the Windows titlebar briefly withdraws the root, which can
        # leave transient dialogs hidden when their own theme update runs.
        visible_dialogs = []

        def collect_dialogs(parent):
            for child in parent.winfo_children():
                if isinstance(child, tk.Toplevel) and child.winfo_viewable():
                    visible_dialogs.append(child)
                collect_dialogs(child)

        collect_dialogs(self)
        focused = self.focus_get()
        ctk.set_appearance_mode(self.config["theme"])

        def restore_dialogs():
            for dialog in visible_dialogs:
                if dialog.winfo_exists():
                    dialog.deiconify()
            if focused is not None and focused.winfo_exists() and focused.winfo_viewable():
                focused.focus_set()

        restore_dialogs()
        self.after(120, restore_dialogs)
        self.theme_btn.configure(text="☀  Light" if self.config["theme"] == "dark" else "☾  Dark")

    def notify(self, text):
        self.status.configure(text=text)

    def _day_tick(self):
        today = date.today().isoformat()
        if today != self._last_day:
            self._last_day = today
            self.refresh_sales()
        self.after(15000, self._day_tick)

    def _enter(self, event):
        if event.widget.winfo_toplevel() == self:
            self.checkout()

    def _escape(self, event):
        if event.widget.winfo_toplevel() == self:
            self.clear_cart()

    def close(self):
        if self.printing:
            messagebox.showinfo("Printing in progress", "Please wait for the printer to finish before closing.", parent=self)
            return
        if self.cart and not confirm(self, "Close billing", "The current bill has not been printed. Discard it and close?"):
            return
        self.db.close()
        self.destroy()


def main():
    app = BillingApp()
    app.mainloop()


if __name__ == "__main__":
    main()
