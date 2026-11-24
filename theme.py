"""
Light / dark appearance for the GUI.

Light mode keeps the native Windows look ("vista" ttk theme). Dark mode uses the "clam" ttk theme with the
palette below, because the native theme ignores colours. Plain tk widgets (banner, log, tooltips) read the
active palette from `current`.
"""
import ctypes
import sys
import tkinter as tk
from ctypes import wintypes as wt

THEME_CHOICES = ("system", "light", "dark")

LIGHT = {
    "bg": "#f0f0f0", "fg": "#1f2937", "muted": "#4b5563",
    "field": "#ffffff", "border": "#c8c8c8", "trough": "#e6e6e6",
    "btn": "#e1e1e1", "btn_hover": "#e5f1fb", "btn_press": "#cce4f7", "btn_disabled": "#f0f0f0",
    "accent": "#0f6cbd", "bar": "#16a34a",
    "ok": "#166534", "bad": "#b91c1c",
    "tip_bg": "#ffffe0", "tip_fg": "#111827",
    "log_bg": "#ffffff", "log_fg": "#111827",
}

DARK = {
    "bg": "#1e1f22", "fg": "#e5e7eb", "muted": "#9ca3af",
    "field": "#2b2d31", "border": "#43464d", "trough": "#2b2d31",
    "btn": "#383a40", "btn_hover": "#43464d", "btn_press": "#50535b", "btn_disabled": "#2b2d31",
    "accent": "#3b82f6", "bar": "#22c55e",
    "ok": "#4ade80", "bad": "#f87171",
    "tip_bg": "#2b2d31", "tip_fg": "#e5e7eb",
    "log_bg": "#16171a", "log_fg": "#d1d5db",
}

PALETTES = {"light": LIGHT, "dark": DARK}

# bot.state -> (banner background, banner text colour), per theme
STATE_COLORS = {
    "light": {
        "idle": ("#e5e7eb", "#111827"),
        "starting": ("#fde68a", "#78350f"),
        "waiting": ("#fde68a", "#78350f"),
        "learning": ("#0e7490", "#ffffff"),
        "watching": ("#15803d", "#ffffff"),
        "reeling": ("#15803d", "#ffffff"),
        "cooldown": ("#15803d", "#ffffff"),
        "testing": ("#2563eb", "#ffffff"),
        "paused": ("#f59e0b", "#111827"),
        "warn": ("#fecaca", "#7f1d1d"),
        "wait": ("#bfdbfe", "#1e3a8a"),
    },
    "dark": {
        "idle": ("#374151", "#f3f4f6"),
        "starting": ("#78350f", "#fef3c7"),
        "waiting": ("#78350f", "#fef3c7"),
        "learning": ("#0e7490", "#ffffff"),
        "watching": ("#15803d", "#ffffff"),
        "reeling": ("#15803d", "#ffffff"),
        "cooldown": ("#15803d", "#ffffff"),
        "testing": ("#1d4ed8", "#ffffff"),
        "paused": ("#b45309", "#ffffff"),
        "warn": ("#7f1d1d", "#fecaca"),
        "wait": ("#1e3a8a", "#dbeafe"),
    },
}

current = LIGHT   # the palette in use; plain tk widgets read this


# --------------------------------------------------------------------------- #
# Choosing a theme
# --------------------------------------------------------------------------- #
def system_prefers_dark() -> bool:
    """True if Windows is set to dark mode for apps (False if it can't be determined)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def resolve(choice: str) -> str:
    """'light' / 'dark' stay as they are; anything else ('system') follows Windows."""
    if choice in PALETTES:
        return choice
    return "dark" if system_prefers_dark() else "light"


def shade(color: str, amount: float) -> str:
    """Lighten (amount > 0) or darken (amount < 0) a #rrggbb colour by mixing with white or black."""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    target = 255 if amount > 0 else 0
    mix = lambda c: round(c + (target - c) * abs(amount))   # noqa: E731
    return "#{:02x}{:02x}{:02x}".format(mix(r), mix(g), mix(b))


# --------------------------------------------------------------------------- #
# Windows title bar
# --------------------------------------------------------------------------- #
def set_titlebar_dark(window: tk.Misc, dark: bool) -> None:
    """Make the window's title bar dark/light (Windows 10 20H1+ / 11). Silently does nothing elsewhere."""
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.WinDLL("user32")
        dwm = ctypes.WinDLL("dwmapi")
        user32.GetParent.argtypes = [wt.HWND]
        user32.GetParent.restype = wt.HWND
        user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, wt.UINT]
        dwm.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
        window.update_idletasks()
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (older Windows 10 builds used 19)
            if dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
        # Ask Windows to redraw the frame now, otherwise the old title bar can linger until focus changes.
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Applying a theme
# --------------------------------------------------------------------------- #
def _style_clam(style, p: dict) -> None:
    """Paint the 'clam' ttk theme with a palette."""
    style.theme_use("clam")
    style.configure(".", background=p["bg"], foreground=p["fg"], fieldbackground=p["field"],
                    bordercolor=p["border"], lightcolor=p["border"], darkcolor=p["border"],
                    troughcolor=p["trough"], focuscolor=p["bg"], insertcolor=p["fg"],
                    selectbackground=p["accent"], selectforeground="#ffffff")
    style.map(".", foreground=[("disabled", p["muted"])])
    style.configure("TFrame", background=p["bg"])
    style.configure("TLabel", background=p["bg"], foreground=p["fg"])
    style.configure("TLabelframe", background=p["bg"], bordercolor=p["border"])
    style.configure("TLabelframe.Label", background=p["bg"], foreground=p["fg"])

    style.configure("TButton", background=p["btn"], foreground=p["fg"], bordercolor=p["border"],
                    focuscolor=p["btn"], padding=(8, 3))
    style.map("TButton",
              background=[("disabled", p["btn_disabled"]), ("pressed", p["btn_press"]), ("active", p["btn_hover"])],
              bordercolor=[("focus", p["accent"])])

    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background=p["bg"], foreground=p["fg"], indicatorbackground=p["field"],
                        indicatorforeground="#ffffff", focuscolor=p["bg"])
        style.map(name, background=[("active", p["bg"])],
                  indicatorbackground=[("selected", p["accent"]), ("pressed", p["btn_press"]),
                                       ("disabled", p["btn_disabled"])])

    for name in ("TEntry", "TSpinbox", "TCombobox"):
        style.configure(name, fieldbackground=p["field"], foreground=p["fg"], insertcolor=p["fg"],
                        bordercolor=p["border"], arrowcolor=p["fg"], background=p["btn"])
    style.map("TEntry", fieldbackground=[("disabled", p["bg"])])
    style.map("TSpinbox", fieldbackground=[("disabled", p["bg"])], background=[("active", p["btn_hover"])])
    style.map("TCombobox", fieldbackground=[("readonly", p["field"]), ("disabled", p["bg"])],
              foreground=[("disabled", p["muted"])],
              selectbackground=[("readonly", p["field"])], selectforeground=[("readonly", p["fg"])],
              background=[("active", p["btn_hover"]), ("disabled", p["btn_disabled"])])

    style.configure("Horizontal.TProgressbar", background=p["bar"], troughcolor=p["trough"],
                    bordercolor=p["border"], lightcolor=p["bar"], darkcolor=p["bar"])
    style.configure("Vertical.TScrollbar", background=p["btn"], troughcolor=p["trough"],
                    bordercolor=p["border"], arrowcolor=p["fg"], lightcolor=p["btn"], darkcolor=p["btn"])
    style.map("Vertical.TScrollbar", background=[("active", p["btn_hover"])])


def apply(root: tk.Tk, style, mode: str) -> dict:
    """Switch the whole app to 'light' or 'dark'. Returns the palette (also stored in `current`)."""
    global current
    p = PALETTES[mode]
    if mode == "dark" or "vista" not in style.theme_names():
        _style_clam(style, p)
    else:
        style.theme_use("vista")
    style.configure("Big.TButton", padding=(10, 6))
    root.configure(bg=p["bg"])
    # Drop-down lists of comboboxes are plain tk listboxes, themed through the option database.
    root.option_add("*TCombobox*Listbox.background", p["field"])
    root.option_add("*TCombobox*Listbox.foreground", p["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", p["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    set_titlebar_dark(root, mode == "dark")
    current = p
    return p


def style_popdown(combo: tk.Misc, p: dict) -> None:
    """Recolour an existing combobox's drop-down list (the option database only applies to new ones)."""
    try:
        popdown = combo.tk.call("ttk::combobox::PopdownWindow", str(combo))
        combo.tk.call(f"{popdown}.f.l", "configure", "-background", p["field"], "-foreground", p["fg"],
                      "-selectbackground", p["accent"], "-selectforeground", "#ffffff")
    except tk.TclError:
        pass
