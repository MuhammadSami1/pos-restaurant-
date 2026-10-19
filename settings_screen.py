"""Owner-editable dishes, restaurant details, and safe receipt options."""

from __future__ import annotations

import copy
import math
from decimal import Decimal
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image, ImageOps

from app_config import image_path, import_image, save_config
from models import amount, money, portion_quantities
from ui_common import ACCENT, SELECTION, SELECTION_HOVER, BG, DANGER, MUTED, PANEL, TEXT, confirm, error, thumbnail


def _header(window, app, title, subtitle=""):
    header = ctk.CTkFrame(window, fg_color="transparent")
    header.pack(fill="x", padx=24, pady=(20, 12))
    text = ctk.CTkFrame(header, fg_color="transparent")
    text.pack(side="left", fill="x", expand=True)
    ctk.CTkLabel(text, text=title, font=ctk.CTkFont(size=25, weight="bold"),
                 text_color=TEXT, anchor="w").pack(fill="x")
    if subtitle:
        ctk.CTkLabel(text, text=subtitle, text_color=MUTED, anchor="w").pack(fill="x")
    ctk.CTkButton(header, text="Light / dark", width=112, height=38,
                  fg_color=PANEL, text_color=TEXT, border_width=1,
                  command=app.toggle_theme).pack(side="right", padx=(12, 0))
    return header


def _field(parent, label, variable, row, column=0, **kwargs):
    box = ctk.CTkFrame(parent, fg_color="transparent")
    box.grid(row=row, column=column, sticky="ew", padx=10, pady=7)
    ctk.CTkLabel(box, text=label, text_color=MUTED, anchor="w").pack(fill="x")
    entry = ctk.CTkEntry(box, textvariable=variable, height=38, **kwargs)
    entry.pack(fill="x", pady=(3, 0))
    return entry


def _section(parent, title, subtitle=None):
    frame = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=12)
    frame.pack(fill="x", padx=6, pady=8)
    ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=17, weight="bold"),
                 anchor="w", text_color=TEXT).pack(fill="x", padx=18, pady=(14, 0))
    if subtitle:
        ctk.CTkLabel(frame, text=subtitle, anchor="w", justify="left",
                     text_color=MUTED, wraplength=850).pack(fill="x", padx=18, pady=(2, 8))
    return frame


def _grid_body(parent, columns=2):
    body = ctk.CTkFrame(parent, fg_color="transparent")
    body.pack(fill="x", padx=8, pady=(4, 12))
    for column in range(columns):
        body.grid_columnconfigure(column, weight=1)
    return body


class DishEditor(ctk.CTkToplevel):
    """Small modal editor shared by the billing grid and Settings."""

    def __init__(self, app, dish=None, on_saved=None):
        super().__init__(app)
        self.app = app
        self.dish = dict(dish or {})
        self.on_saved = on_saved
        self.selected_photo = None
        self.photo_path = self.dish.get("image_path", "")
        self.title("Edit dish" if dish else "Add a dish")
        self.geometry("620x690")
        self.minsize(570, 580)
        self.configure(fg_color=BG)
        self.transient(app)
        _header(self, app, "Edit dish" if dish else "Add a dish", "Your menu, your prices.")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=24, pady=16)
        ctk.CTkButton(footer, text="Save dish", height=44, fg_color=ACCENT,
                      text_color="#20190E", command=self.save).pack(side="right")
        ctk.CTkButton(footer, text="Cancel", height=44, width=100, fg_color=PANEL,
                      text_color=TEXT, command=self.destroy).pack(side="right", padx=10)

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14)
        body.grid_columnconfigure((0, 1), weight=1)
        self.name = ctk.StringVar(value=self.dish.get("name", ""))
        self.price = ctk.StringVar(value=str(self.dish.get("price", "")))
        saved_prices = self.dish.get("portion_prices") or {}
        self.portion_price_vars = {
            key: ctk.StringVar(value=str(saved_prices.get(key, "")))
            for key in ("0.25", "0.5", "0.75")
        }
        self.portion_price_vars["1"] = self.price
        self._custom_portion_keys = set(saved_prices) - {"1"}
        self._updating_portions = False
        self.category = ctk.StringVar(value=self.dish.get("category", "General"))
        self.sell_type = ctk.StringVar(value="Weight (kg)" if self.dish.get("sell_type") == "kg" else "Count")
        self.active = ctk.BooleanVar(value=bool(self.dish.get("active", 1)))
        self.name_entry = _field(body, "Dish name · English or Urdu", self.name, 0)
        _field(body, "Category", self.category, 0, 1)

        sell_box = ctk.CTkFrame(body, fg_color="transparent")
        sell_box.grid(row=1, column=0, sticky="ew", padx=10, pady=7)
        ctk.CTkLabel(sell_box, text="Sold by", text_color=MUTED, anchor="w").pack(fill="x")
        ctk.CTkOptionMenu(sell_box, variable=self.sell_type, values=["Count", "Weight (kg)"],
                          height=38).pack(fill="x", pady=(3, 0))
        prices = ctk.CTkFrame(body, fg_color="transparent")
        prices.grid(row=2, column=0, columnspan=2, sticky="ew")
        prices.grid_columnconfigure((0, 1), weight=1)
        self.portion_price_fields = {}
        self.portion_price_labels = {}
        for key, variable in self.portion_price_vars.items():
            box = ctk.CTkFrame(prices, fg_color="transparent")
            label = ctk.CTkLabel(box, text="", text_color=MUTED, anchor="w")
            label.pack(fill="x")
            ctk.CTkEntry(box, textvariable=variable, height=38).pack(fill="x", pady=(3, 0))
            self.portion_price_fields[key] = box
            self.portion_price_labels[key] = label
            if key != "1":
                variable.trace_add("write", lambda *_args, k=key: self._portion_edited(k))
        self.price_label = self.portion_price_labels["1"]
        self.price.trace_add("write", self._fill_portion_defaults)
        self.sell_type.trace_add("write", self._selling_changed)
        self._fill_portion_defaults()
        self._selling_changed()

        photo_box = ctk.CTkFrame(body, fg_color=PANEL, corner_radius=12)
        photo_box.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=16)
        self.photo_preview = ctk.CTkLabel(photo_box, text="No photo", width=150, height=150,
                                         text_color=MUTED)
        self.photo_preview.pack(side="left", padx=16, pady=16)
        controls = ctk.CTkFrame(photo_box, fg_color="transparent")
        controls.pack(side="left", fill="x", expand=True, padx=(0, 16))
        ctk.CTkLabel(controls, text="Dish photo", font=ctk.CTkFont(size=17, weight="bold"),
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(controls, text="JPG or PNG · optional", text_color=MUTED,
                     anchor="w").pack(fill="x", pady=(2, 12))
        ctk.CTkButton(controls, text="Choose photo", height=38,
                      command=self.choose_photo).pack(fill="x")
        ctk.CTkButton(controls, text="Remove photo", height=34, fg_color="transparent",
                      text_color=MUTED, command=self.remove_photo).pack(fill="x", pady=(6, 0))
        ctk.CTkSwitch(body, text="Show on the billing screen", variable=self.active,
                      progress_color=ACCENT).grid(row=4, column=0, columnspan=2,
                                                 sticky="w", padx=12, pady=(0, 12))
        ctk.CTkLabel(body, text="Set each portion's selling price. Larger quantities use the full price plus the remaining portion.",
                     text_color=MUTED, wraplength=490, justify="left").grid(
                         row=5, column=0, columnspan=2, sticky="w", padx=12, pady=4)
        self._update_photo()
        self.after(100, self._focus)
        self.bind("<Escape>", lambda _event: self.destroy())

    def _focus(self):
        if self.winfo_exists():
            self.grab_set()
            self.name_entry.focus_set()

    def _selling_changed(self, *_):
        unit = "kg" if self.sell_type.get() == "Weight (kg)" else "count"
        for box in self.portion_price_fields.values():
            box.grid_remove()
        for index, quantity in enumerate(portion_quantities(unit)):
            key = f"{quantity:g}"
            suffix = " kg" if unit == "kg" else ""
            self.portion_price_labels[key].configure(text=f"Price for {key}{suffix} (Rs.)")
            self.portion_price_fields[key].grid(row=index // 2, column=index % 2,
                                               sticky="ew", padx=10, pady=7)

    def _portion_edited(self, key):
        if not self._updating_portions:
            self._custom_portion_keys.add(key)

    def _fill_portion_defaults(self, *_):
        """Suggest proportional prices until the owner enters a custom amount."""
        try:
            price = amount(self.price.get().strip(), "Price for 1")
        except ValueError:
            return
        self._updating_portions = True
        try:
            for key, variable in self.portion_price_vars.items():
                if key != "1" and key not in self._custom_portion_keys:
                    default = amount(Decimal(str(price)) * Decimal(key))
                    variable.set(f"{default:.2f}".removesuffix(".00"))
        finally:
            self._updating_portions = False

    def _update_photo(self):
        image = None
        if self.selected_photo:
            try:
                with Image.open(self.selected_photo) as source:
                    preview = ImageOps.fit(ImageOps.exif_transpose(source).convert("RGB"), (150, 150))
                image = ctk.CTkImage(preview, preview, size=(150, 150))
            except (OSError, ValueError, Image.DecompressionBombError):
                pass
        elif self.photo_path:
            image = thumbnail(self.photo_path, size=(150, 150))
        self.photo_preview.configure(image=image, text="" if image else "No photo")
        if image is None:
            # CTkLabel leaves the old Tk image installed when configured with None.
            self.photo_preview._label.configure(image="")
        self.photo_preview.image = image

    def choose_photo(self):
        chosen = filedialog.askopenfilename(parent=self, title="Choose dish photo",
                                            filetypes=[("Photos", "*.jpg *.jpeg *.png")])
        if not chosen:
            return
        try:
            with Image.open(chosen) as source:
                source.verify()
            self.selected_photo = chosen
            self._update_photo()
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            error(self, "Cannot open photo", f"Choose a valid JPG or PNG photo.\n\n{exc}")

    def remove_photo(self):
        self.selected_photo = None
        self.photo_path = ""
        self._update_photo()

    def save(self):
        try:
            name = self.name.get().strip()
            if not name:
                raise ValueError("Enter a dish name.")
            unit = "kg" if self.sell_type.get() == "Weight (kg)" else "count"
            portions = {}
            for quantity in portion_quantities(unit):
                key = f"{quantity:g}"
                suffix = " kg" if unit == "kg" else ""
                portions[key] = amount(self.portion_price_vars[key].get().strip(),
                                       f"Price for {key}{suffix}")
            path = import_image(self.selected_photo) if self.selected_photo else self.photo_path
            values = dict(self.dish)
            values.update(name=name, price=portions["1"], portion_prices=portions,
                          sell_type=unit,
                          category=self.category.get().strip() or "General",
                          image_path=path, active=int(self.active.get()))
            self.app.db.save_dish(values)
        except (ValueError, OSError) as exc:
            error(self, "Cannot save dish", str(exc))
            return
        except Exception as exc:
            error(self, "Cannot save dish", f"Your changes were not saved.\n\n{exc}")
            return
        self.app.refresh_all()
        if self.on_saved:
            self.on_saved()
        self.app.notify(f"{name} saved")
        self.destroy()


class SettingsScreen(ctk.CTkToplevel):
    """One settings window with shared values across General and Bill Layout."""

    LAYOUT_OPTIONS = [
        ("show_logo", "Restaurant logo"), ("show_name", "Restaurant name"),
        ("show_address", "Address"), ("show_phone", "Phone number"),
        ("show_bill_no", "Bill number"), ("show_datetime", "Date and time"),
        ("show_subtotal", "Subtotal"), ("show_discount", "Discount"),
        ("show_tax", "Tax"), ("show_order_type", "Order type"),
        ("show_table_waiter", "Table / waiter"), ("show_customer", "Customer / phone"),
        ("show_footer", "Footer message"),
    ]

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Settings · Restaurant billing")
        self.geometry("1000x700")
        self.minsize(800, 580)
        self.configure(fg_color=BG)
        self.transient(app)
        config = app.config
        self.vars = {key: ctk.StringVar(value=str(config.get(key, ""))) for key in (
            "restaurant_name", "address", "phone", "tax_percent", "paper_width",
            "usb_vid", "usb_pid", "printer_driver_name")}
        self.vars["delivery_fee"] = ctk.StringVar(value=str(config.get("delivery_fee", 0)))
        self.printer_mode = ctk.StringVar(value="USB" if config.get("printer_mode") == "usb" else "Windows driver")
        self.logo_path = config.get("logo_path", "")
        self.footer_value = config.get("footer", "")
        self.footer_widgets = {}
        layout = config.get("bill_layout", {})
        self.layout_vars = {key: ctk.BooleanVar(value=bool(layout.get(key, True)))
                            for key, _ in self.LAYOUT_OPTIONS}
        promos = layout.get("promo_lines", [])
        self.promo_vars = [ctk.StringVar(value=promos[i] if i < len(promos) else "") for i in range(2)]
        self.promo_position = ctk.StringVar(value="Above total" if layout.get("promo_position") != "below_total" else "Below total")
        _header(self, app, "Settings", "Make the menu and the printed bill your own.")

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(side="bottom", fill="x", padx=24, pady=14)
        self.status = ctk.CTkLabel(bar, text="Changes to dishes are saved immediately.", text_color=MUTED)
        self.status.pack(side="left")
        ctk.CTkButton(bar, text="Save settings", height=42, fg_color=ACCENT,
                      text_color="#20190E", command=self.save).pack(side="right")
        ctk.CTkButton(bar, text="Close", width=80, height=42, fg_color=PANEL,
                      text_color=TEXT, command=self.close).pack(side="right", padx=10)
        self.tabs = ctk.CTkTabview(self, fg_color=BG, command=self._tab_changed,
                                 segmented_button_selected_color=SELECTION,
                                 segmented_button_selected_hover_color=SELECTION_HOVER,
                                 text_color=TEXT)
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(0, 0))
        for name in ("Dishes", "General", "Bill Layout"):
            self.tabs.add(name)
        self.last_tab = "Dishes"
        self._build_dishes()
        self._build_general()
        self._build_layout()
        self.refresh_dishes()
        self._saved_values = self._form_values()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self.lift)

    def _build_dishes(self):
        page = self.tabs.tab("Dishes")
        toolbar = ctk.CTkFrame(page, fg_color="transparent")
        toolbar.pack(fill="x", padx=8, pady=(8, 12))
        ctk.CTkLabel(toolbar, text="Your menu", font=ctk.CTkFont(size=19, weight="bold")).pack(side="left")
        self.dish_count = ctk.CTkLabel(toolbar, text="", text_color=MUTED)
        self.dish_count.pack(side="left", padx=14)
        ctk.CTkButton(toolbar, text="+  Add dish", height=40, fg_color=ACCENT,
                      text_color="#20190E", command=lambda: DishEditor(
                          self.app, on_saved=self.refresh_dishes)).pack(side="right")
        self.dish_rows = ctk.CTkScrollableFrame(page, fg_color="transparent")
        self.dish_rows.pack(fill="both", expand=True)

    def refresh_dishes(self):
        if not self.winfo_exists():
            return
        for child in self.dish_rows.winfo_children():
            child.destroy()
        dishes = self.app.db.list_dishes(include_inactive=True)
        self.dish_count.configure(text=f"{len(dishes)} dishes")
        for dish in dishes:
            row = ctk.CTkFrame(self.dish_rows, fg_color=PANEL, corner_radius=10)
            row.pack(fill="x", padx=4, pady=4)
            img = thumbnail(dish.get("image_path", ""), size=(52, 52))
            photo = ctk.CTkLabel(row, text="" if img else "—", image=img, width=64,
                                height=64, text_color=MUTED)
            photo.pack(side="left", padx=(8, 4), pady=5)
            photo.image = img
            label = ctk.CTkFrame(row, fg_color="transparent")
            label.pack(side="left", fill="x", expand=True, padx=8)
            name = ctk.CTkLabel(label, text=dish["name"], font=ctk.CTkFont(size=15, weight="bold"),
                                anchor="w")
            name.pack(fill="x")
            price = money(dish["price"]) + (" / kg" if dish.get("sell_type") == "kg" else " / item")
            detail = ctk.CTkLabel(label, text=f"{price}   ·   {dish.get('category', 'General')}",
                                  text_color=MUTED, anchor="w")
            detail.pack(fill="x")
            for widget in (photo, name, detail):
                widget.bind("<Button-1>", lambda _event, d=dish: DishEditor(self.app, d, self.refresh_dishes))
            active = ctk.BooleanVar(value=bool(dish.get("active", 1)))
            switch = ctk.CTkSwitch(row, text="Show", width=72, variable=active,
                                   progress_color=ACCENT,
                                   command=lambda d=dish, v=active: self._toggle_dish(d, v.get()))
            switch.pack(side="left", padx=6)
            for text, direction in (("↑", -1), ("↓", 1)):
                ctk.CTkButton(row, text=text, width=36, height=36, fg_color=BG, text_color=TEXT,
                              command=lambda d=dish, step=direction: self._move_dish(d["id"], step)).pack(
                                  side="left", padx=2)
            ctk.CTkButton(row, text="Edit", width=58, height=36, fg_color=BG, text_color=TEXT,
                          command=lambda d=dish: DishEditor(self.app, d, self.refresh_dishes)).pack(side="left", padx=(8, 3))
            ctk.CTkButton(row, text="×", width=36, height=36, fg_color="transparent", text_color=DANGER,
                          command=lambda d=dish: self._delete_dish(d)).pack(side="left", padx=(3, 8))
        if not dishes:
            ctk.CTkLabel(self.dish_rows, text="Add your first dish to start billing.",
                         text_color=MUTED, height=120).pack(fill="x")

    def _toggle_dish(self, dish, active):
        try:
            self.app.db.save_dish(dict(dish, active=int(active)))
            self.app.refresh_all()
        except Exception as exc:
            error(self, "Cannot update dish", str(exc))
        self.refresh_dishes()

    def _move_dish(self, dish_id, direction):
        try:
            self.app.db.move_dish(dish_id, direction)
            self.app.refresh_all()
            self.refresh_dishes()
        except Exception as exc:
            error(self, "Cannot move dish", str(exc))

    def _delete_dish(self, dish):
        if confirm(self, "Delete dish?", f"Remove {dish['name']} from your menu?\nPrevious bills will be kept."):
            try:
                self.app.db.delete_dish(dish["id"])
                self.app.refresh_all()
                self.refresh_dishes()
            except Exception as exc:
                error(self, "Cannot delete dish", str(exc))

    def _text_fields(self, parent, tab):
        body = _grid_body(parent)
        _field(body, "Restaurant name", self.vars["restaurant_name"], 0, 0)
        _field(body, "Phone", self.vars["phone"], 0, 1)
        address = ctk.CTkFrame(body, fg_color="transparent")
        address.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=7)
        ctk.CTkLabel(address, text="Address", text_color=MUTED, anchor="w").pack(fill="x")
        ctk.CTkEntry(address, textvariable=self.vars["address"], height=38).pack(fill="x", pady=(3, 0))
        footer = ctk.CTkFrame(body, fg_color="transparent")
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=7)
        ctk.CTkLabel(footer, text="Footer message · multiple lines welcome", text_color=MUTED,
                     anchor="w").pack(fill="x")
        widget = ctk.CTkTextbox(footer, height=76)
        widget.pack(fill="x", pady=(3, 0))
        widget.insert("1.0", self.footer_value)
        widget.bind("<KeyRelease>", lambda _event, source=tab: self._sync_footer(source))
        widget.bind("<FocusOut>", lambda _event, source=tab: self._sync_footer(source))
        self.footer_widgets[tab] = widget

    def _sync_footer(self, source):
        if source not in self.footer_widgets:
            return
        self.footer_value = self.footer_widgets[source].get("1.0", "end-1c")
        for tab, widget in self.footer_widgets.items():
            if tab != source and widget.get("1.0", "end-1c") != self.footer_value:
                widget.delete("1.0", "end")
                widget.insert("1.0", self.footer_value)

    def _tab_changed(self):
        self._sync_footer(self.last_tab)
        self.last_tab = self.tabs.get()

    def _build_general(self):
        page = ctk.CTkScrollableFrame(self.tabs.tab("General"), fg_color="transparent")
        page.pack(fill="both", expand=True)
        identity = _section(page, "Restaurant details", "These details also appear in Bill Layout.")
        self._text_fields(identity, "General")
        logo = _section(page, "Restaurant logo", "The logo prints in black and white. Dish photos never print.")
        logo_body = ctk.CTkFrame(logo, fg_color="transparent")
        logo_body.pack(fill="x", padx=18, pady=12)
        self.logo_preview = ctk.CTkLabel(logo_body, text="No logo", width=110, height=85, text_color=MUTED)
        self.logo_preview.pack(side="left", padx=(0, 18))
        ctk.CTkButton(logo_body, text="Choose logo", height=38, command=self.choose_logo).pack(side="left", padx=4)
        ctk.CTkButton(logo_body, text="Remove logo", height=38, fg_color=BG, text_color=TEXT,
                      command=self.remove_logo).pack(side="left", padx=4)
        self._update_logo()
        delivery = _section(page, "Delivery charge", "Default charge when delivery is enabled on a bill. "
                            "You can change it for each order. Added after tax and discount; delivery is not discounted.")
        delivery_body = ctk.CTkFrame(delivery, fg_color="transparent")
        delivery_body.pack(fill="x", padx=18, pady=(4, 16))
        ctk.CTkLabel(delivery_body, text="Delivery rate (Rs.)", text_color=MUTED,
                     anchor="w").pack(fill="x", pady=(0, 6))
        delivery_controls = ctk.CTkFrame(delivery_body, fg_color="transparent")
        delivery_controls.pack(anchor="w")
        ctk.CTkButton(delivery_controls, text="− 10", width=72, height=40,
                      fg_color=BG, text_color=TEXT,
                      command=lambda: self._change_delivery_fee(-10)).pack(side="left")
        ctk.CTkEntry(delivery_controls, textvariable=self.vars["delivery_fee"],
                     width=145, height=40, justify="center").pack(side="left", padx=8)
        ctk.CTkButton(delivery_controls, text="+ 10", width=72, height=40,
                      fg_color=BG, text_color=TEXT,
                      command=lambda: self._change_delivery_fee(10)).pack(side="left")
        printer = _section(page, "Tax and printer", "Select a direct USB connection or the printer's installed Windows driver.\n"
                           "Urdu receipts require a Windows printer driver. Enter its exact Windows printer name; "
                           "select roll / continuous paper in printer preferences.")
        body = _grid_body(printer)
        _field(body, "Tax (%)", self.vars["tax_percent"], 0, 0)
        width = ctk.CTkFrame(body, fg_color="transparent")
        width.grid(row=0, column=1, sticky="ew", padx=10, pady=7)
        ctk.CTkLabel(width, text="Paper width (mm)", text_color=MUTED, anchor="w").pack(fill="x")
        ctk.CTkOptionMenu(width, variable=self.vars["paper_width"], values=["80", "58"], height=38).pack(fill="x", pady=(3, 0))
        mode = ctk.CTkFrame(body, fg_color="transparent")
        mode.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=7)
        ctk.CTkLabel(mode, text="Printer connection", text_color=MUTED, anchor="w").pack(fill="x")
        ctk.CTkSegmentedButton(mode, values=["USB", "Windows driver"], variable=self.printer_mode,
                              selected_color=SELECTION, selected_hover_color=SELECTION_HOVER, height=38).pack(fill="x", pady=(3, 0))
        _field(body, "USB vendor ID (for example 0x0483)", self.vars["usb_vid"], 2, 0)
        _field(body, "USB product ID (for example 0x5740)", self.vars["usb_pid"], 2, 1)
        driver = ctk.CTkFrame(body, fg_color="transparent")
        driver.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=7)
        ctk.CTkLabel(driver, text="Windows printer driver name", text_color=MUTED, anchor="w").pack(fill="x")
        ctk.CTkEntry(driver, textvariable=self.vars["printer_driver_name"], height=38).pack(fill="x", pady=(3, 0))
        self._print_controls(page)

    def _delivery_fee(self):
        try:
            fee = float(self.vars["delivery_fee"].get().strip())
        except ValueError:
            raise ValueError("Delivery rate must be a number from Rs. 0 to Rs. 999,999,999.99.") from None
        if not math.isfinite(fee) or not 0 <= fee <= 999_999_999.99:
            raise ValueError("Delivery rate must be a number from Rs. 0 to Rs. 999,999,999.99.")
        return round(fee, 2)

    def _change_delivery_fee(self, step):
        try:
            fee = max(0, min(999_999_999.99, round(self._delivery_fee() + step, 2)))
            self.vars["delivery_fee"].set(f"{fee:.2f}".removesuffix(".00"))
        except ValueError as exc:
            error(self, "Invalid delivery rate", str(exc))

    def _build_layout(self):
        page = ctk.CTkScrollableFrame(self.tabs.tab("Bill Layout"), fg_color="transparent")
        page.pack(fill="both", expand=True)
        options = _section(page, "Choose what appears", "Items, quantities, delivery charges, and the final total always print. "
                           "Their formatting is automatic. The delivery line appears when delivery is enabled.")
        body = _grid_body(options, 3)
        for index, (key, label) in enumerate(self.LAYOUT_OPTIONS):
            ctk.CTkSwitch(body, text=label, variable=self.layout_vars[key], progress_color=ACCENT).grid(
                row=index // 3, column=index % 3, sticky="w", padx=10, pady=10)
        text = _section(page, "Header and footer", "Shared with General settings; edit here or there.")
        self._text_fields(text, "Bill Layout")
        promos = _section(page, "A message for your customers", "Optional offers, greetings, or delivery information.")
        promo_body = _grid_body(promos)
        _field(promo_body, "Promo line 1", self.promo_vars[0], 0, 0)
        _field(promo_body, "Promo line 2", self.promo_vars[1], 0, 1)
        ctk.CTkOptionMenu(promo_body, variable=self.promo_position,
                          values=["Above total", "Below total"], height=38).grid(
                              row=1, column=0, sticky="ew", padx=10, pady=8)
        self._print_controls(page)

    def _print_controls(self, parent):
        controls = ctk.CTkFrame(parent, fg_color="transparent")
        controls.pack(fill="x", padx=10, pady=16)
        ctk.CTkButton(controls, text="Preview", height=42, command=self.preview).pack(side="left", padx=4)
        ctk.CTkButton(controls, text="Test print", height=42, fg_color=ACCENT, text_color="#20190E",
                      command=self.test_print).pack(side="left", padx=4)
        ctk.CTkButton(controls, text="Reprint last bill", height=42, fg_color=PANEL, text_color=TEXT,
                      command=self.app.reprint_last).pack(side="right", padx=4)

    def choose_logo(self):
        chosen = filedialog.askopenfilename(parent=self, title="Choose restaurant logo",
                                            filetypes=[("Images", "*.jpg *.jpeg *.png")])
        if not chosen:
            return
        try:
            self.logo_path = import_image(chosen)
            self._update_logo()
        except (OSError, ValueError) as exc:
            error(self, "Cannot use logo", f"Choose a valid JPG or PNG image.\n\n{exc}")

    def remove_logo(self):
        self.logo_path = ""
        self._update_logo()

    def _update_logo(self):
        preview = None
        if self.logo_path:
            try:
                with Image.open(image_path(self.logo_path)) as source:
                    image = ImageOps.exif_transpose(source).convert("RGBA")
                    background = Image.new("RGBA", image.size, "white")
                    background.alpha_composite(image)
                    image = background.convert("L")
                    image.thumbnail((145, 85))
                    image = image.convert("1").convert("RGB")
                preview = ctk.CTkImage(image, image, size=image.size)
            except (OSError, ValueError, Image.DecompressionBombError):
                pass
        self.logo_preview.configure(image=preview, text="" if preview else "No logo")
        if preview is None:
            self.logo_preview._label.configure(image="")
        self.logo_preview.image = preview

    def _collect(self):
        self._sync_footer(self.tabs.get())
        config = copy.deepcopy(self.app.config)
        name = self.vars["restaurant_name"].get().strip()
        if not name:
            raise ValueError("Enter your restaurant name.")
        try:
            tax = float(self.vars["tax_percent"].get().strip())
        except ValueError:
            raise ValueError("Tax must be a number from 0 to 100.") from None
        if not math.isfinite(tax) or not 0 <= tax <= 100:
            raise ValueError("Tax must be a number from 0 to 100.")
        mode = "usb" if self.printer_mode.get() == "USB" else "win32"
        if mode == "usb":
            for key, label in (("usb_vid", "USB vendor ID"), ("usb_pid", "USB product ID")):
                value = self.vars[key].get().strip()
                try:
                    parsed = int(value, 16 if value.lower().startswith("0x") else 10)
                    if not 0 < parsed <= 65535:
                        raise ValueError
                except ValueError:
                    raise ValueError(f"{label} must be 1–65535, or hexadecimal such as 0x0483.") from None
        elif not self.vars["printer_driver_name"].get().strip():
            raise ValueError("Enter the printer name shown in Windows Printers & scanners.")
        config.update({key: value.get().strip() for key, value in self.vars.items()})
        config.update(restaurant_name=name, tax_percent=round(tax, 2), delivery_fee=self._delivery_fee(),
                      paper_width=int(self.vars["paper_width"].get()), printer_mode=mode,
                      logo_path=self.logo_path, footer=self.footer_value.strip())
        config["bill_layout"] = {key: value.get() for key, value in self.layout_vars.items()}
        config["bill_layout"].update(promo_lines=[value.get().strip() for value in self.promo_vars if value.get().strip()],
                                     promo_position="above_total" if self.promo_position.get() == "Above total" else "below_total")
        return config

    def save(self):
        try:
            config = self._collect()
            saved = save_config(config)
            self.app.config = saved if isinstance(saved, dict) else config
            self._saved_values = self._form_values()
            self.app.refresh_all()
            self.status.configure(text="Settings saved.", text_color=("#287D55", "#85D5AA"))
            self.app.notify("Settings saved")
        except Exception as exc:
            error(self, "Cannot save settings", str(exc))

    def _form_values(self):
        """Track user edits without validating incomplete fields or the live theme."""
        self._sync_footer(self.tabs.get())
        return (
            tuple((key, value.get()) for key, value in self.vars.items()),
            tuple((key, value.get()) for key, value in self.layout_vars.items()),
            self.printer_mode.get(), self.logo_path, self.footer_value,
            tuple(value.get() for value in self.promo_vars), self.promo_position.get(),
        )

    def close(self):
        if self._form_values() != self._saved_values:
            if not confirm(self, "Discard settings changes?",
                           "Restaurant and bill layout changes have not been saved.\n"
                           "Close without saving them? Menu changes are already saved."):
                return
        self.destroy()

    def preview(self):
        try:
            from printer import test_bill
            self.app.show_receipt(test_bill(), config=self._collect())
        except Exception as exc:
            error(self, "Cannot preview bill", str(exc))

    def test_print(self):
        try:
            from printer import test_bill
            self.app.print_saved_bill(test_bill(), config=self._collect())
        except Exception as exc:
            error(self, "Cannot test printer", str(exc))
