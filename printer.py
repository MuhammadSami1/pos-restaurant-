"""Safe receipt layout and offline USB/Windows printing.

ESC/POS printers have firmware code pages, not arbitrary Unicode fonts. ASCII
receipts use python-escpos; Unicode uses Windows' text/shaping API and the
installed thermal-printer driver. Only the optional logo is an image in either
print path. Rendering never reads dish photos or recalculates a saved bill.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import textwrap
import unicodedata
from typing import Any


class PrinterError(Exception):
    """A recoverable printing error suitable for an owner-facing popup."""


_LAYOUT_DEFAULTS = {
    "show_logo": True, "show_name": True, "show_address": True,
    "show_phone": True, "show_bill_no": True, "show_datetime": True,
    "show_subtotal": True, "show_discount": True, "show_tax": False,
    "show_order_type": False, "show_table_waiter": False,
    "show_customer": False, "show_footer": True, "promo_lines": [],
    "promo_position": "above_total",
}


@dataclass(frozen=True)
class _Line:
    left: str = ""
    right: str = ""
    align: str = "left"
    bold: bool = False
    width: int = 1
    height: int = 1

    def text(self, columns: int) -> str:
        columns //= self.width
        if self.right:
            return self.left + " " * max(1, columns - len(self.left) - len(self.right)) + self.right
        return self.left


@dataclass
class _Receipt:
    lines: list[_Line]
    columns: int
    pixels: int
    logo: Any = None

    @property
    def height(self) -> int:
        return 12 + (self.logo.height + 12 if self.logo else 0) + sum(
            28 * line.height for line in self.lines
        ) + 56


def _clean(value: Any, limit: int = 500) -> str:
    """Remove printer control bytes, while preserving Urdu joiners/Unicode."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    return "".join(ch for ch in text[:limit] if ch == "\n" or unicodedata.category(ch) != "Cc").strip()


def _number(value: Any) -> float:
    try:
        result = round(float(value or 0), 2)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PrinterError("This bill has an invalid amount. Open the bill in Sales to check it.") from exc
    if not math.isfinite(result):
        raise PrinterError("This bill has an invalid amount. Open the bill in Sales to check it.")
    return result


def _money(value: Any) -> str:
    return f"{_number(value):,.2f}".removesuffix(".00")


def _qty(value: Any) -> str:
    try:
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise ValueError
        return f"{number:g}"
    except (ValueError, TypeError, OverflowError) as exc:
        raise PrinterError("This bill has an invalid quantity. Open the bill in Sales to check it.") from exc


def _wrap(text: str, columns: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        lines.extend(textwrap.wrap(paragraph, max(1, columns), break_long_words=True,
                                   break_on_hyphens=False) or [""])
    return lines


def _resolved_config(config: dict | None) -> dict:
    if config is None:
        from app_config import load_config
        return load_config()
    return dict(config)


def _load_logo(config: dict, pixels: int):
    name = config.get("logo_path", "")
    if not name:
        return None
    try:
        from PIL import Image, ImageOps
        from app_config import image_path
        path = image_path(name)
        if not path:
            return None
        with Image.open(path) as source:
            source = ImageOps.exif_transpose(source).convert("RGBA")
            background = Image.new("RGBA", source.size, "white")
            background.alpha_composite(source)
            logo = background.convert("L")
            logo.thumbnail((pixels, 180), Image.Resampling.LANCZOS)
            return logo.point(lambda value: 255 if value > 160 else 0, mode="1")
    except (OSError, ValueError, TypeError, ImportError):
        # An owner's missing/corrupt logo must never stop the bill printing.
        return None


def _receipt(bill: dict, config: dict) -> _Receipt:
    columns = 32 if str(config.get("paper_width", 80)) == "58" else 48
    pixels = columns * 12
    layout = dict(_LAYOUT_DEFAULTS)
    if isinstance(config.get("bill_layout"), dict):
        layout.update(config["bill_layout"])
    lines: list[_Line] = []

    def add(text: Any, *, align="left", bold=False, width=1, height=1):
        for line in _wrap(_clean(text), columns // width):
            lines.append(_Line(line, align=align, bold=bold, width=width, height=height))

    def pair(left: str, right: str, *, bold=False, width=1):
        capacity = columns // width
        if len(left) + len(right) + 1 <= capacity:
            lines.append(_Line(left, right, bold=bold, width=width))
        else:
            add(left, bold=bold, width=width)
            add(right, align="right", bold=bold, width=width)

    logo = _load_logo(config, pixels) if layout.get("show_logo") else None
    if bill.get("is_test"):
        add("TEST PRINT - NOT A SALE", align="center", bold=True)
    if bill.get("status") == "void":
        add("VOID BILL - NOT A SALE", align="center", bold=True)
    if layout.get("show_name"):
        add(config.get("restaurant_name", "Restaurant"), align="center", bold=True, width=2, height=2)
    for flag, key in (("show_address", "address"), ("show_phone", "phone")):
        if layout.get(flag) and config.get(key):
            add(config[key], align="center")
    if layout.get("show_bill_no"):
        add(f"Bill #{_clean(bill.get('bill_no', '-'), 30)}", align="center")
    if layout.get("show_datetime"):
        timestamp = _clean(bill.get("created_at") or bill.get("date"), 80)
        try:
            timestamp = datetime.fromisoformat(timestamp).strftime("%d-%b-%Y  %I:%M %p")
        except ValueError:
            pass
        if timestamp:
            add(timestamp, align="center")

    order = bill.get("order_info") or {}
    if isinstance(order, str):
        try:
            order = json.loads(order)
        except (ValueError, TypeError):
            order = {}
    if not isinstance(order, dict):
        order = {}
    order = {**bill, **order}
    if layout.get("show_order_type") and order.get("order_type"):
        add(f"Order: {order['order_type']}")
    if layout.get("show_table_waiter"):
        table = order.get("table") or order.get("table_no")
        if table:
            add(f"Table: {table}")
        if order.get("waiter"):
            add(f"Waiter: {order['waiter']}")
    if layout.get("show_customer"):
        if order.get("customer_name"):
            add(f"Customer: {order['customer_name']}")
        if order.get("customer_phone"):
            add(f"Phone: {order['customer_phone']}")

    add("-" * columns)
    pair("Item / Qty", "Amount", bold=True)
    add("-" * columns)
    items = bill.get("items") or []
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except (ValueError, TypeError) as exc:
            raise PrinterError("The saved bill's items cannot be read. Check it in Sales.") from exc
    if not isinstance(items, list):
        raise PrinterError("The saved bill's items cannot be read. Check it in Sales.")
    for item in items:
        if not isinstance(item, dict):
            raise PrinterError("The saved bill contains an invalid item. Check it in Sales.")
        unit = item.get("unit") or item.get("sell_type") or "count"
        prefix = f"{_qty(item.get('qty', 1))} kg " if unit == "kg" else f"{_qty(item.get('qty', 1))}x "
        amount = _money(item.get("amount", 0))
        name = _clean(item.get("name", "Item"), 300).replace("\n", " ")
        item_width = max(8, columns - len(amount) - 1)
        chunks = _wrap(prefix + name, item_width)
        if len(amount) + item_width + 1 <= columns:
            lines.append(_Line(chunks[0], amount))
            lines.extend(_Line(chunk) for chunk in chunks[1:])
        else:
            add(prefix + name)
            add(amount, align="right")
    add("-" * columns)
    if layout.get("show_subtotal"):
        pair("Subtotal:", f"Rs. {_money(bill.get('subtotal'))}")
    if layout.get("show_tax") and _number(bill.get("tax")):
        pair("Tax:", f"Rs. {_money(bill.get('tax'))}")
    if layout.get("show_discount") and _number(bill.get("discount")):
        pair("Discount:", f"-Rs. {_money(bill.get('discount'))}")
    # Use the saved charge: changing today's default must not change a reprint.
    if _number(bill.get("delivery_fee")) > 0 or order.get("order_type") == "Delivery":
        pair("Delivery:", f"Rs. {_money(bill.get('delivery_fee'))}")

    promo = layout.get("promo_lines") or []
    if isinstance(promo, str):
        promo = promo.splitlines()
    if not isinstance(promo, (list, tuple)):
        promo = []

    def add_promo():
        for promo_line in promo[:2]:
            if _clean(promo_line):
                add(promo_line, align="center")

    if layout.get("promo_position") != "below_total":
        add_promo()
    pair("TOTAL:", f"Rs. {_money(bill.get('total'))}", bold=True, width=2)
    if layout.get("promo_position") == "below_total":
        add_promo()
    add("-" * columns)
    if layout.get("show_footer") and config.get("footer"):
        add(config["footer"], align="center")
    return _Receipt(lines, columns, pixels, logo)


def receipt_text(bill: dict, config: dict | None = None) -> str:
    """Return the same wrapped, immutable receipt content used by printing."""
    receipt = _receipt(bill, _resolved_config(config))
    return "\n".join(line.text(receipt.columns) for line in receipt.lines) + "\n"


def _needs_unicode(receipt: _Receipt) -> bool:
    return any(not line.text(receipt.columns).isascii() for line in receipt.lines)


def _draw_gdi(dc, receipt: _Receipt) -> None:
    """Shared Windows preview/print drawing; text stays native Unicode text."""
    import ctypes
    from ctypes import wintypes
    import win32con
    import win32ui
    from PIL import ImageWin

    # Some win32ui builds route DrawText/GetTextExtent through ANSI MFC APIs.
    # Call the explicit wide-character Windows functions to preserve Urdu.
    draw_text = ctypes.windll.user32.DrawTextW
    draw_text.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                          ctypes.POINTER(wintypes.RECT), wintypes.UINT]
    draw_text.restype = ctypes.c_int
    measure_text = ctypes.windll.gdi32.GetTextExtentPoint32W
    measure_text.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                             ctypes.POINTER(wintypes.SIZE)]
    measure_text.restype = wintypes.BOOL
    handle = dc.GetHandleOutput()

    def text_width(text):
        size = wintypes.SIZE()
        if not measure_text(handle, text, len(text.encode("utf-16-le")) // 2, ctypes.byref(size)):
            raise OSError("Windows could not measure receipt text.")
        return size.cx

    def draw(text, bounds, flags):
        rect = wintypes.RECT(*bounds)
        if text and not draw_text(handle, text, -1, ctypes.byref(rect), flags):
            raise OSError("Windows could not render receipt text.")

    dc.SetBkMode(win32con.TRANSPARENT)
    dc.SetTextColor(0)
    y = 12
    if receipt.logo:
        x = (receipt.pixels - receipt.logo.width) // 2
        ImageWin.Dib(receipt.logo.convert("RGB")).draw(
            dc.GetHandleOutput(), (x, y, x + receipt.logo.width, y + receipt.logo.height))
        y += receipt.logo.height + 12
    for line in receipt.lines:
        text = line.left
        font_name = "Arial" if not text.isascii() else "Courier New"
        font = win32ui.CreateFont({"name": font_name, "height": -22 * line.height,
                                  "width": 12 * line.width if text.isascii() else 0,
                                  "weight": 700 if line.bold else 400,
                                  "quality": win32con.NONANTIALIASED_QUALITY})
        previous = dc.SelectObject(font)
        try:
            right_width = text_width(line.right) if line.right else 0
            available = receipt.pixels - right_width - (12 if line.right else 0)
            measured = text_width(text) if text else 0
            if measured > available and measured:
                dc.SelectObject(previous)
                font = win32ui.CreateFont({"name": font_name,
                                          "height": -max(10, int(22 * line.height * available / measured)),
                                          "weight": 700 if line.bold else 400,
                                          "quality": win32con.NONANTIALIASED_QUALITY})
                dc.SelectObject(font)
            flags = win32con.DT_SINGLELINE | win32con.DT_TOP | win32con.DT_NOPREFIX
            if line.align == "center":
                flags |= win32con.DT_CENTER
            elif line.align == "right":
                flags |= win32con.DT_RIGHT
            draw(text, (0, y, available, y + 28 * line.height), flags)
            if line.right:
                draw(line.right, (0, y, receipt.pixels, y + 28 * line.height),
                     win32con.DT_SINGLELINE | win32con.DT_RIGHT | win32con.DT_NOPREFIX)
        finally:
            dc.SelectObject(previous)
        y += 28 * line.height


def _preview_gdi(receipt: _Receipt):
    import win32con
    import win32gui
    import win32ui
    from PIL import Image

    screen_handle = win32gui.GetDC(0)
    screen = win32ui.CreateDCFromHandle(screen_handle)
    memory = screen.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(screen, receipt.pixels, receipt.height)
    previous = memory.SelectObject(bitmap)
    try:
        memory.FillSolidRect((0, 0, receipt.pixels, receipt.height), 0xFFFFFF)
        _draw_gdi(memory, receipt)
        info = bitmap.GetInfo()
        data = bitmap.GetBitmapBits(True)
        return Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), data,
                                "raw", "BGRX", 0, 1).copy()
    finally:
        memory.SelectObject(previous)
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC()
        win32gui.ReleaseDC(0, screen_handle)


def _preview_pillow(receipt: _Receipt):
    """Portable development preview; Windows uses the actual GDI text renderer."""
    from PIL import Image, ImageDraw, ImageFont

    canvas = Image.new("RGB", (receipt.pixels, receipt.height), "white")
    draw = ImageDraw.Draw(canvas)
    y = 12
    if receipt.logo:
        canvas.paste(receipt.logo, ((receipt.pixels - receipt.logo.width) // 2, y))
        y += receipt.logo.height + 12
    for line in receipt.lines:
        font_paths = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf",
                      Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
        font = None
        for path in font_paths:
            try:
                font = ImageFont.truetype(str(path), 22 * line.height)
                break
            except OSError:
                pass
        if font is None:
            font = ImageFont.load_default(size=22 * line.height)
        text = line.left
        available = receipt.pixels - (draw.textlength(line.right, font=font) + 12 if line.right else 0)
        measured = max(1, int(draw.textlength(text, font=font)))
        strip = Image.new("RGB", (max(measured + 4, int(available)), 28 * line.height), "white")
        ImageDraw.Draw(strip).text((0, 0), text, fill="black", font=font, stroke_width=int(line.bold))
        if measured > available:
            strip = strip.resize((max(1, int(available)), strip.height))
            measured = int(available)
        if line.align == "center":
            x = (receipt.pixels - measured) // 2
        elif line.align == "right":
            x = receipt.pixels - measured
        else:
            x = 0
        canvas.paste(strip.crop((0, 0, min(strip.width, measured + 4), strip.height)), (x, y))
        if line.right:
            draw.text((receipt.pixels - draw.textlength(line.right, font=font), y),
                      line.right, fill="black", font=font)
        y += 28 * line.height
    return canvas


def render_receipt(bill: dict, config: dict | None = None):
    """Return a white 576/384px slip; print and preview share layout/line breaks."""
    receipt = _receipt(bill, _resolved_config(config))
    if os.name == "nt":
        try:
            return _preview_gdi(receipt)
        except ImportError:
            pass
    return _preview_pillow(receipt)


def _device_error(config: dict, detail: str, *, unicode=False, uncertain=False) -> PrinterError:
    name = _clean(config.get("printer_driver_name", "POS80 Printer"), 120)
    message = "Printing did not complete. "
    if uncertain:
        message += "The printer may have received part or all of this bill. Check the paper before using Reprint last.\n\n"
    message += (
        "Open Settings > General. Turn on and connect the printer, then select the correct paper width.\n"
        "For a Windows driver: install the thermal printer's Windows driver, copy its exact name from "
        f"Windows Settings > Printers & scanners into Printer driver name (currently '{name}'), "
        "and choose Windows driver.\n"
    )
    if unicode:
        message += "Urdu/Unicode receipts require that Windows driver for correct joined text.\n"
    else:
        message += (
            "For direct USB: choose USB and enter the printer's VID/PID from Device Manager > "
            "Properties > Details > Hardware Ids (for example 0x0483 / 0x5740). Direct USB needs "
            "a compatible WinUSB/libusb driver; the installed Windows printer driver is the simpler fallback.\n"
        )
    message += f"\nTechnical detail: {_clean(detail, 500)}"
    return PrinterError(message)


def _usb_id(value: Any) -> int:
    if isinstance(value, int):
        result = value
    else:
        text = str(value).strip()
        result = int(text, 16 if text.lower().startswith("0x") or any(ch in text.lower() for ch in "abcdef") else 10)
    if not 1 <= result <= 65535:
        raise ValueError("USB IDs must be between 0x0001 and 0xFFFF")
    return result


def _open_usb(config: dict):
    from escpos.printer import Usb
    usb_args = {}
    try:
        import libusb_package
        import usb.backend.libusb1
        backend = usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
        if backend is not None:
            usb_args["backend"] = backend
    except ImportError:
        pass
    printer = Usb(_usb_id(config.get("usb_vid", "0x0483")),
                  _usb_id(config.get("usb_pid", "0x5740")), usb_args=usb_args, timeout=5000)
    try:
        printer.open()
        return printer
    except Exception:
        try:
            printer.close()
        except Exception:
            pass
        raise


def _open_win32_raw(config: dict):
    from escpos import printer as drivers
    # Current python-escpos calls this Win32Raw; older releases used Win32.
    driver = getattr(drivers, "Win32Raw", None) or getattr(drivers, "Win32")
    name = str(config.get("printer_driver_name", "")).strip()
    if not name:
        raise ValueError("Enter the exact Windows printer driver name")
    printer = driver(name)
    try:
        printer.open()
        return printer
    except Exception:
        try:
            printer.close()
        except Exception:
            pass
        raise


def _print_escpos(receipt: _Receipt, config: dict) -> None:
    try:
        device = _open_usb(config)
    except Exception as usb_error:
        # Opening is the only safe automatic fallback point: no bill bytes sent.
        try:
            device = _open_win32_raw(config)
        except Exception as windows_error:
            raise _device_error(config, f"USB: {usb_error}; Windows: {windows_error}") from windows_error
    try:
        device.set(align="center", font="a", bold=False, width=1, height=1, custom_size=True)
        if receipt.logo:
            from PIL import Image
            logo_strip = Image.new("1", (receipt.pixels, receipt.logo.height), 1)
            logo_strip.paste(receipt.logo, ((receipt.pixels - receipt.logo.width) // 2, 0))
            device.image(logo_strip, center=False)
            device.text("\n")
        for line in receipt.lines:
            device.set(align=line.align, font="a", bold=line.bold,
                       width=line.width, height=line.height, custom_size=True)
            device.line_spacing(28 * line.height)
            device.text(line.text(receipt.columns) + "\n")
        device.set(align="left", bold=False, width=1, height=1, custom_size=True)
        device.line_spacing()
        device.text("\n\n\n")
        device.cut()
        device.close()
    except Exception as exc:
        try:
            device.close()
        except Exception:
            pass
        raise _device_error(config, str(exc), uncertain=True) from exc


def _print_windows(receipt: _Receipt, config: dict) -> None:
    dc = None
    started = False
    try:
        import win32con
        import win32gui
        import win32print
        import win32ui

        name = str(config.get("printer_driver_name", "")).strip()
        if not name:
            raise ValueError("Enter the exact Windows printer driver name")
        handle = win32print.OpenPrinter(name)
        try:
            mode = win32print.GetPrinter(handle, 2)["pDevMode"]
            if mode is not None:
                mode.PaperSize = 0
                mode.PaperWidth = 580 if receipt.columns == 32 else 800
                mode.PaperLength = min(32767, max(100, math.ceil((receipt.height + 64) * 254 / 203)))
                mode.Orientation = win32con.DMORIENT_PORTRAIT
                mode.Fields |= (win32con.DM_PAPERSIZE | win32con.DM_PAPERWIDTH |
                                win32con.DM_PAPERLENGTH | win32con.DM_ORIENTATION)
            hdc = win32gui.CreateDC("WINSPOOL", name, mode)
            dc = win32ui.CreateDCFromHandle(hdc)
        finally:
            win32print.ClosePrinter(handle)
        # Use 203-dpi logical units in both preview and physical driver output.
        # Restrict the width to the driver's printable area to prevent clipping.
        scale_x = min(dc.GetDeviceCaps(win32con.LOGPIXELSX) / 203,
                      dc.GetDeviceCaps(win32con.HORZRES) / receipt.pixels)
        scale_y = dc.GetDeviceCaps(win32con.LOGPIXELSY) / 203
        dc.SetMapMode(win32con.MM_ANISOTROPIC)
        dc.SetWindowExt((receipt.pixels, receipt.height))
        dc.SetViewportExt((max(1, round(receipt.pixels * scale_x)),
                           max(1, round(receipt.height * scale_y))))
        printable_height = int(dc.GetDeviceCaps(win32con.VERTRES) / scale_y)
        if printable_height < receipt.height:
            raise ValueError("The driver ignored the receipt paper length. In Windows Printing preferences, "
                             "select roll/continuous receipt paper long enough for the bill")
        dc.StartDoc("Restaurant bill")
        started = True
        dc.StartPage()
        _draw_gdi(dc, receipt)
        dc.EndPage()
        dc.EndDoc()
        started = False
    except Exception as exc:
        if dc is not None and started:
            try:
                dc.AbortDoc()
            except Exception:
                pass
        raise _device_error(config, str(exc), unicode=_needs_unicode(receipt), uncertain=started) from exc
    finally:
        if dc is not None:
            dc.DeleteDC()


def print_bill(bill: dict, config: dict | None = None) -> None:
    """Print one existing bill. Saving/voiding the bill belongs to the UI/DB."""
    config = _resolved_config(config)
    receipt = _receipt(bill, config)
    mode = str(config.get("printer_mode", "usb")).lower()
    if mode in {"windows", "win32", "driver", "windows driver"} or _needs_unicode(receipt):
        _print_windows(receipt, config)
    else:
        _print_escpos(receipt, config)


def test_bill() -> dict:
    """An unsaved sample; test slips never affect daily bill numbers or sales."""
    now = datetime.now().astimezone()
    return {
        "bill_no": "TEST", "date": now.date().isoformat(), "created_at": now.isoformat(),
        "is_test": True, "status": "active",
        "items": [{"name": "Chicken Karahi", "qty": 1.5, "unit": "kg", "price": 950, "amount": 1425},
                  {"name": "Naan", "qty": 3, "unit": "count", "price": 40, "amount": 120}],
        "subtotal": 1545, "discount": 45, "tax": 0, "total": 1500,
        "order_info": {"order_type": "Dine-in", "table": "3", "waiter": "Ali", "customer_name": "", "customer_phone": ""},
    }


def test_print(config: dict | None = None) -> None:
    print_bill(test_bill(), config)
