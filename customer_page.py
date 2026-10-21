"""Choose a customer for an uncommitted credit bill; this page never writes data."""
from __future__ import annotations

import customtkinter as ctk

from db import DuplicateCustomerError
from models import money
from ui_common import ACCENT, BG, DANGER, MUTED, PANEL, TEXT, SELECTION, SELECTION_HOVER, modal


class CustomerPage(ctk.CTkToplevel):
    def __init__(self, app, total, on_select, on_cancel=None):
        super().__init__(app)
        self.app = app
        self.on_select = on_select
        self.on_cancel = on_cancel
        self._submitting = False
        self._finished = False
        self._duplicate = None
        self.title("Save to Khata · Choose customer")
        modal(self, app, 720, 650)
        self.minsize(620, 570)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(20, 8))
        ctk.CTkLabel(header, text="Choose customer", font=ctk.CTkFont(size=25, weight="bold")).pack(side="left")
        ctk.CTkButton(header, text="Reprint last bill", height=36, width=130, fg_color=PANEL,
                      text_color=TEXT, command=app.reprint_last).pack(side="right", padx=(8, 0))
        ctk.CTkButton(header, text="Light / dark", height=36, width=110, fg_color=PANEL,
                      text_color=TEXT, command=app.toggle_theme).pack(side="right")
        ctk.CTkLabel(self, text=f"UDHAAR  ·  {money(total)}", text_color=("#965400", ACCENT),
                     font=ctk.CTkFont(size=20, weight="bold"), anchor="w").pack(fill="x", padx=24, pady=(0, 4))
        ctk.CTkLabel(self, text="Choosing a customer saves this sale and prints the slip.",
                     text_color=MUTED, anchor="w").pack(fill="x", padx=24, pady=(0, 8))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=24, pady=16)
        self.cancel_button = ctk.CTkButton(footer, text="Cancel · return to bill", height=42,
                                           fg_color=PANEL, text_color=TEXT, command=self.cancel)
        self.cancel_button.pack(side="right")
        ctk.CTkLabel(footer, text="Your order is kept until you save.", text_color=MUTED).pack(side="left")
        self.tabs = ctk.CTkTabview(self, fg_color=BG, segmented_button_selected_color=SELECTION,
                                 segmented_button_selected_hover_color=SELECTION_HOVER, text_color=TEXT)
        self.tabs.pack(fill="both", expand=True, padx=20, pady=(0, 0))
        self.tabs.add("New Customer")
        self.tabs.add("Existing Customer")
        self.name_var = ctk.StringVar()
        self.phone_var = ctk.StringVar()
        self.search_var = ctk.StringVar()
        self._build_new()
        self._build_existing()
        self.name_var.trace_add("write", self._name_changed)
        self.search_var.trace_add("write", lambda *_: self.refresh_existing())
        self.refresh_existing()
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda _event: self.cancel())
        self.after(150, self._focus)

    def _build_new(self):
        tab = self.tabs.tab("New Customer")
        ctk.CTkLabel(tab, text="Customer name", text_color=MUTED, anchor="w").pack(fill="x", padx=12, pady=(18, 3))
        self.name_entry = ctk.CTkEntry(tab, textvariable=self.name_var, height=42)
        self.name_entry.pack(fill="x", padx=12)
        ctk.CTkLabel(tab, text="Phone · optional", text_color=MUTED, anchor="w").pack(fill="x", padx=12, pady=(14, 3))
        self.phone_entry = ctk.CTkEntry(tab, textvariable=self.phone_var, height=42)
        self.phone_entry.pack(fill="x", padx=12)
        self.problem = ctk.CTkLabel(tab, text="", text_color=DANGER, anchor="w", wraplength=610)
        self.problem.pack(fill="x", padx=12, pady=(10, 0))
        self.duplicate_box = ctk.CTkFrame(tab, fg_color="transparent")
        self.duplicate_button = ctk.CTkButton(self.duplicate_box, text="", height=42,
                                               fg_color=ACCENT, text_color="#20190E", command=self.use_duplicate)
        self.duplicate_button.pack(fill="x")
        ctk.CTkButton(self.duplicate_box, text="Type different name", height=36, fg_color=PANEL,
                      text_color=TEXT, command=self.type_different_name).pack(fill="x", pady=(8, 0))
        self.save_button = ctk.CTkButton(tab, text="Save customer & bill", height=44, fg_color=ACCENT,
                                         text_color="#20190E", command=self.save_new)
        self.save_button.pack(fill="x", padx=12, pady=14)

    def _build_existing(self):
        tab = self.tabs.tab("Existing Customer")
        ctk.CTkLabel(tab, text="Search name or phone", text_color=MUTED, anchor="w").pack(fill="x", padx=8, pady=(12, 3))
        self.search_entry = ctk.CTkEntry(tab, textvariable=self.search_var, height=40,
                                       placeholder_text="Search by name or phone")
        self.search_entry.pack(fill="x", padx=8, pady=(0, 8))
        self.existing_problem = ctk.CTkLabel(tab, text="", text_color=DANGER, anchor="w", wraplength=600)
        self.existing_problem.pack(fill="x", padx=8)
        self.customer_list = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        self.customer_list.pack(fill="both", expand=True, padx=4, pady=(0, 4))

    def _focus(self):
        if self.winfo_exists():
            self.name_entry.focus_set()

    def _name_changed(self, *_):
        self._duplicate = None
        self.duplicate_box.pack_forget()
        self.save_button.configure(state="normal")
        self.problem.configure(text="")
        if not self.name_var.get().strip():
            return
        try:
            duplicate = self.app.db.find_customer_by_name(self.name_var.get())
        except Exception as exc:
            self.problem.configure(text=f"Cannot check customers: {exc}")
            self.save_button.configure(state="disabled")
            return
        if duplicate:
            self.show_duplicate(duplicate)

    def show_duplicate(self, customer):
        self._duplicate = customer
        self.problem.configure(text="This name already exists")
        self.duplicate_button.configure(text=f'Add bill to "{customer["name"]}" →')
        self.duplicate_box.pack(fill="x", padx=12, pady=(6, 0), before=self.save_button)
        self.save_button.configure(state="disabled")
        self.tabs.set("New Customer")

    def type_different_name(self):
        self.name_var.set("")
        self.name_entry.focus_set()

    def refresh_existing(self):
        for child in self.customer_list.winfo_children():
            child.destroy()
        try:
            customers = self.app.db.list_customers(self.search_var.get())
        except Exception as exc:
            self.existing_problem.configure(text=f"Cannot load customers: {exc}")
            return
        self.existing_problem.configure(text="")
        if not customers:
            ctk.CTkLabel(self.customer_list, text="No matching customers." if self.search_var.get().strip()
                         else "No customers yet. Use New Customer to create the first Khata.",
                         text_color=MUTED, wraplength=530, height=100).pack(fill="x")
        for customer in customers:
            phone = f"  ·  {customer['phone']}" if customer.get("phone") else ""
            button = ctk.CTkButton(self.customer_list,
                                   text=f"{customer['name']}{phone}\nKhata: {money(customer['balance'])}   ·   Add this bill →",
                                   anchor="w", height=66, fg_color=PANEL, text_color=TEXT,
                                   command=lambda customer_id=customer["id"]: self.select_existing(customer_id))
            button.pack(fill="x", pady=4)

    def save_new(self):
        name = self.name_var.get().strip()
        if not name:
            self.problem.configure(text="Enter a customer name.")
            self.name_entry.focus_set()
            return
        try:
            duplicate = self.app.db.find_customer_by_name(name)
        except Exception as exc:
            self.problem.configure(text=str(exc))
            return
        if duplicate:
            self.show_duplicate(duplicate)
            return
        self._submit(new_customer={"name": name, "phone": self.phone_var.get().strip()})

    def use_duplicate(self):
        if self._duplicate:
            self.select_existing(self._duplicate["id"])

    def select_existing(self, customer_id):
        self._submit(customer_id=customer_id)

    def _submit(self, **selection):
        if self._submitting or self._finished:
            return
        self._submitting = True
        self.cancel_button.configure(state="disabled")
        try:
            if self.on_select(**selection):
                self._finished = True
                self.grab_release()
                self.destroy()
                if self.app.winfo_exists():
                    self.app.focus_set()
        except DuplicateCustomerError as exc:
            self.show_duplicate(exc.customer)
        except Exception as exc:
            label = self.problem if self.tabs.get() == "New Customer" else self.existing_problem
            label.configure(text=f"Bill was not saved: {exc}")
        finally:
            self._submitting = False
            if self.winfo_exists():
                self.cancel_button.configure(state="normal")

    def cancel(self):
        if self._finished or self._submitting:
            return
        self._finished = True
        self.grab_release()
        self.destroy()
        if self.on_cancel:
            self.on_cancel()
        if self.app.winfo_exists():
            self.app.focus_set()
