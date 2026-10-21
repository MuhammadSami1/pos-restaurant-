"""Customer credit ledger with balances computed from active saved bills."""
from __future__ import annotations

import customtkinter as ctk

from models import money
from sales_screen import BillDetail, _timestamp
from ui_common import ACCENT_TEXT, BG, MUTED, PANEL, TEXT, SELECTION, SELECTION_HOVER, confirm, error


class KhataScreen(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.selected_customer_id = None
        self.title("Khata · Restaurant billing")
        self.geometry("1040x690")
        self.minsize(850, 570)
        self.configure(fg_color=BG)
        self.transient(app)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(20, 12))
        ctk.CTkLabel(header, text="Khata · Udhaar", font=ctk.CTkFont(size=25, weight="bold")).pack(side="left")
        ctk.CTkButton(header, text="Reprint last bill", width=145, height=38, fg_color=PANEL,
                      text_color=TEXT, command=app.reprint_last).pack(side="right", padx=(12, 0))
        ctk.CTkButton(header, text="Light / dark", width=110, height=38, fg_color=PANEL,
                      text_color=TEXT, command=app.toggle_theme).pack(side="right")
        self.total_label = ctk.CTkLabel(self, text="Total Khata: Rs. 0", anchor="w",
                                       font=ctk.CTkFont(size=29, weight="bold"), text_color=ACCENT_TEXT)
        self.total_label.pack(fill="x", padx=24, pady=(0, 18))
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=(0, 12))
        content.grid_columnconfigure(0, weight=2, minsize=285)
        content.grid_columnconfigure(1, weight=3, minsize=425)
        content.grid_rowconfigure(0, weight=1)
        left = ctk.CTkFrame(content, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        ctk.CTkLabel(left, text="Customers", anchor="w", font=ctk.CTkFont(size=18, weight="bold")).pack(fill="x", pady=(0, 8))
        self.search_var = ctk.StringVar()
        ctk.CTkLabel(left, text="Search name or phone", text_color=MUTED, anchor="w").pack(fill="x", pady=(0, 3))
        self.search_entry = ctk.CTkEntry(left, textvariable=self.search_var, height=40,
                                       placeholder_text="Search name or phone")
        self.search_entry.pack(fill="x", pady=(0, 10))
        self.customer_list = ctk.CTkScrollableFrame(left, fg_color=PANEL, corner_radius=12)
        self.customer_list.pack(fill="both", expand=True)
        right = ctk.CTkFrame(content, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        self.customer_label = ctk.CTkLabel(right, text="Select a customer", anchor="w",
                                          font=ctk.CTkFont(size=20, weight="bold"), wraplength=520)
        self.customer_label.pack(fill="x")
        self.balance_label = ctk.CTkLabel(right, text="", anchor="w", text_color=ACCENT_TEXT,
                                         font=ctk.CTkFont(size=17, weight="bold"))
        self.balance_label.pack(fill="x", pady=(4, 8))
        self.bill_list = ctk.CTkScrollableFrame(right, fg_color="transparent")
        self.bill_list.pack(fill="both", expand=True)
        ctk.CTkLabel(self, text="Udhaar bills also count in Sales. Deleted bills are excluded from all balances.",
                     text_color=MUTED).pack(pady=(0, 12))
        self.search_var.trace_add("write", lambda *_: self.refresh())
        self.refresh()
        self.after(100, self.lift)
        self.bind("<Escape>", lambda _event: self.destroy())

    def refresh(self):
        if not self.winfo_exists():
            return
        try:
            total = self.app.db.khata_total()
            customers = self.app.db.list_customers(self.search_var.get())
            ids = {customer["id"] for customer in customers}
            if self.selected_customer_id not in ids:
                self.selected_customer_id = customers[0]["id"] if customers else None
            selected = next((c for c in customers if c["id"] == self.selected_customer_id), None)
            bills = self.app.db.customer_bills(self.selected_customer_id) if selected else []
        except Exception as exc:
            error(self, "Cannot load Khata", str(exc))
            return
        self.total_label.configure(text=f"Total Khata: {money(total)}")
        for panel in (self.customer_list, self.bill_list):
            for child in panel.winfo_children():
                child.destroy()
        if not customers:
            message = "No matching customers." if self.search_var.get().strip() else "No Khata customers yet.\nTurn on Udhaar while making a bill."
            ctk.CTkLabel(self.customer_list, text=message, text_color=MUTED, wraplength=270, height=120).pack(fill="x")
        for customer in customers:
            chosen = customer["id"] == self.selected_customer_id
            phone = f"\n{customer['phone']}" if customer.get("phone") else ""
            ctk.CTkButton(self.customer_list,
                          text=f"{customer['name']} — {money(customer['balance'])}{phone}",
                          height=62, anchor="w", fg_color=SELECTION if chosen else "transparent",
                          hover_color=SELECTION_HOVER, text_color="#20190E" if chosen else TEXT,
                          command=lambda customer_id=customer["id"]: self.select_customer(customer_id)).pack(fill="x", padx=2, pady=4)
        if selected:
            self.customer_label.configure(text=selected["name"])
            self.balance_label.configure(text=f"Balance: {money(selected['balance'])}")
        else:
            self.customer_label.configure(text="Select a customer")
            self.balance_label.configure(text="")
        if not bills:
            ctk.CTkLabel(self.bill_list, text="No active Khata bills." if selected else "Choose a customer to view their bills.",
                         text_color=MUTED, height=150).pack(fill="x")
        for bill in bills:
            row = ctk.CTkFrame(self.bill_list, fg_color=PANEL, corner_radius=10)
            row.pack(fill="x", pady=4)
            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, padx=14, pady=12)
            title = ctk.CTkLabel(info, text=f"Bill #{bill['bill_no']} · UDHAAR", anchor="w",
                                font=ctk.CTkFont(size=16, weight="bold"))
            title.pack(fill="x")
            stamp = ctk.CTkLabel(info, text=_timestamp(bill.get("created_at", bill.get("date"))), text_color=MUTED, anchor="w")
            stamp.pack(fill="x", pady=(2, 0))
            ctk.CTkButton(row, text=f"{money(bill['total'])}  ›", width=150, height=42,
                          fg_color="transparent", text_color=TEXT, anchor="e",
                          command=lambda bill_id=bill["id"]: self.open_bill(bill_id)).pack(side="right", padx=10)
            for widget in (row, info, title, stamp):
                widget.bind("<Button-1>", lambda _event, bill_id=bill["id"]: self.open_bill(bill_id))

    def select_customer(self, customer_id):
        self.selected_customer_id = customer_id
        self.refresh()

    def open_bill(self, bill_id):
        try:
            bill = self.app.db.get_bill(bill_id)
            if not bill or bill.get("status") != "active" or bill.get("customer_id") != self.selected_customer_id:
                self.refresh()
                raise ValueError("This Khata bill is no longer available.")
            BillDetail(self, bill)
        except Exception as exc:
            error(self, "Cannot open bill", str(exc))

    def void(self, bill, detail=None):
        if not confirm(detail or self, "Delete this Khata bill?",
                       f"Delete bill #{bill['bill_no']} for {money(bill['total'])}?\n"
                       "It will be removed from Khata balances and Sales totals. The saved record will be kept."):
            return
        try:
            self.app.db.void_bill(bill["id"])
            if detail:
                detail.destroy()
            self.refresh()
            self.app.refresh_all()
            self.app.notify(f"Bill #{bill['bill_no']} deleted from Khata and Sales")
        except Exception as exc:
            error(self, "Cannot delete bill", str(exc))
