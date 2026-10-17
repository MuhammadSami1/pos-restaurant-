"""Small shared UI helpers; all application data stays outside packaged assets."""
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image, ImageOps

from app_config import image_path

ACCENT = "#E89B35"
ACCENT_TEXT = ("#965400", "#E89B35")
SELECTION = ("#E7AA51", "#E89B35")
SELECTION_HOVER = ("#D69637", "#C78029")
HOVER = ("#E3C18E", "#14375E")
DANGER_HOVER = ("#F3C9C5", "#5A292C")
BG = ("#F5F2ED", "#15181C")
PANEL = ("#FFFFFF", "#20242A")
MUTED = ("#646B75", "#A0A7B1")
TEXT = ("#20242A", "#F1F2F4")
DANGER = "#C65252"
GREEN = ("#237650", "#7BCD9F")


def configure_theme():
    """Give every control deliberate light colors while preserving dark colors."""
    ctk.set_default_color_theme("dark-blue")
    theme = ctk.ThemeManager.theme
    theme["CTkButton"].update(fg_color=("#F3E1C6", "#1F538D"), hover_color=HOVER,
                              text_color=TEXT, text_color_disabled=("#837B70", "gray60"))
    theme["CTkLabel"]["text_color"] = TEXT
    theme["CTkEntry"].update(fg_color=("#FFFDFC", "#343638"), border_color=("#B9A88C", "#565B5E"), text_color=TEXT)
    theme["CTkOptionMenu"].update(fg_color=("#F4E9D8", "#1F538D"), button_color=("#E9CCA0", "#14375E"),
                                  button_hover_color=("#D8AD6A", "#1E2C40"), text_color=TEXT)
    theme["DropdownMenu"].update(fg_color=("#FFFDFC", "gray20"), hover_color=("#EFCD99", "gray28"), text_color=TEXT)
    theme["CTkSegmentedButton"].update(fg_color=("#E9E2D8", "gray29"), selected_color=SELECTION,
                                       selected_hover_color=SELECTION_HOVER, unselected_color=("#E9E2D8", "gray29"),
                                       unselected_hover_color=("#DDC8A9", "gray41"), text_color=TEXT)
    theme["CTkTextbox"].update(fg_color=("#FFFDFC", "gray20"), text_color=TEXT)
    theme["CTkSwitch"].update(progress_color=ACCENT, fg_color=("#BAAD9A", "#4A4D50"), text_color=TEXT)
    theme["CTkCheckBox"].update(fg_color=ACCENT, hover_color=HOVER, text_color=TEXT)


def bring_to_front(window):
    """Restore existing windows, including CTk's temporary withdrawn state."""
    def restore():
        if window.winfo_exists():
            window.deiconify()
            window.lift()
            window.focus_force()
    restore()
    # CTk changes the Windows titlebar asynchronously; restore after it settles.
    window.after(120, restore)


def confirm(parent, title, message):
    return messagebox.askyesno(title, message, parent=parent)


def error(parent, title, message):
    messagebox.showerror(title, str(message), parent=parent)


def thumbnail(relative, size=(120, 120)):
    if not relative:
        return None
    try:
        with Image.open(image_path(relative)) as source:
            img = ImageOps.fit(ImageOps.exif_transpose(source).convert("RGB"), size, method=Image.Resampling.LANCZOS)
        return ctk.CTkImage(light_image=img, dark_image=img, size=size)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def logo_thumbnail(relative, size=(48, 48)):
    """Preserve the entire logo, including transparency and wide aspect ratios."""
    if not relative:
        return None
    try:
        with Image.open(image_path(relative)) as source:
            content = ImageOps.contain(ImageOps.exif_transpose(source).convert("RGBA"), size, Image.Resampling.LANCZOS)
        image = Image.new("RGBA", size)
        image.alpha_composite(content, ((size[0] - content.width) // 2, (size[1] - content.height) // 2))
        return ctk.CTkImage(light_image=image, dark_image=image, size=size)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def modal(window, parent, width, height):
    window.transient(parent)
    window.geometry(f"{width}x{height}")
    window.configure(fg_color=BG)
    window.after(100, lambda: window.grab_set() if window.winfo_exists() else None)
