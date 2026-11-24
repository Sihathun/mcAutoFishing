"""
AutoFishing GUI - a point-and-click front end for autofish.py.

    python gui.py

Hotkeys (work even while Minecraft is focused):
    F6 = start    F7 = pause / resume    F8 = stop    F9 = capture screenshot (during calibration)
"""
import base64
import ctypes
import io
import os
import queue
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# Must happen before the first window exists so Tk isn't blurry / mis-scaled on high-DPI screens.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass
# Our own app identity, so the taskbar shows the AutoFishing icon instead of grouping us under python.exe.
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Sihathun.AutoFishing")
except Exception:
    pass

import cv2
import keyboard
import numpy as np
from PIL import Image, ImageTk

import appicon
import autodetect
import autofish as af
import theme
import winutil

AUTO_ENTRY = "Auto-detect Minecraft (recommended)"

# (config key, label, min, max, step, number format, tooltip)
SETTINGS = [
    ("match_threshold", "Match threshold", 0.50, 0.99, 0.01, "%.2f",
     "How closely the screen must match the splash line (0-1).\n"
     "Lower it if bites are missed, raise it if you get false triggers."),
    ("reel_delay", "Reel-in delay (s)", 0.0, 5.0, 0.05, "%.2f",
     "Pause between spotting the splash and right-clicking to reel in."),
    ("recast_delay", "Recast delay (s)", 0.1, 10.0, 0.1, "%.1f",
     "Pause between reeling in and casting again."),
    ("cooldown", "Cooldown after catch (s)", 1.0, 15.0, 0.5, "%.1f",
     "Ignore the screen for this long after a catch so the lingering\n"
     "subtitle doesn't trigger a second time (subtitles last ~3 s)."),
    ("poll_interval", "Check interval (s)", 0.01, 1.0, 0.01, "%.2f",
     "How often the screen is checked. Smaller = faster reaction, more CPU."),
    ("start_delay", "Start delay (s)", 0, 30, 1, "%.0f",
     "Time to switch to the Minecraft window after pressing Start."),
]

ICON_SIZES = (16, 32, 48, 64, 128)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def load_icon_images(sizes=ICON_SIZES) -> list:
    """The embedded app icon (the fishing rod) at several sizes, as PIL images."""
    data = base64.b64decode(appicon.ICON_PNG_B64)
    base = Image.open(io.BytesIO(data)).convert("RGBA")
    return [base.resize((s, s), Image.Resampling.LANCZOS) for s in sizes]


def to_photo(img: np.ndarray, max_w: int, max_h: int, max_scale: float = 1.0) -> ImageTk.PhotoImage:
    """Fit a BGR or gray image inside max_w x max_h (enlarging at most max_scale times)."""
    if img.ndim == 2:
        rgb = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    else:
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    scale = min(max_scale, max_w / w, max_h / h)
    size = (max(1, int(w * scale)), max(1, int(h * scale)))
    resample = Image.Resampling.LANCZOS if scale < 1 else Image.Resampling.NEAREST
    return ImageTk.PhotoImage(Image.fromarray(rgb).resize(size, resample))


class Tooltip:
    """Minimal hover tooltip."""

    def __init__(self, widget: tk.Widget, text: str):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event=None):
        if self.tip:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        p = theme.current
        self.tip.configure(bg=p["border"])
        tk.Label(self.tip, text=self.text, justify="left", background=p["tip_bg"], foreground=p["tip_fg"],
                 relief="flat", borderwidth=0, padx=6, pady=4).pack(padx=1, pady=1)

    def _hide(self, _event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class RoiSelector(tk.Toplevel):
    """Modal window: show an image, let the user drag a rectangle, return it in image pixels."""

    def __init__(self, parent: tk.Misc, title: str, instructions: str, image_bgr: np.ndarray):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=theme.current["bg"])
        self.result = None
        self._rect = None
        self._origin = (0, 0)
        self._box = None

        h, w = image_bgr.shape[:2]
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.scale = min(4.0, sw * 0.9 / w, sh * 0.7 / h)
        disp_w, disp_h = max(1, int(w * self.scale)), max(1, int(h * self.scale))
        self._img_size = (w, h)
        self._disp_size = (disp_w, disp_h)
        self._photo = to_photo(image_bgr, disp_w, disp_h, max_scale=4.0)

        ttk.Label(self, text=instructions, wraplength=max(420, disp_w), justify="left").pack(
            anchor="w", padx=10, pady=(10, 6))

        self.canvas = tk.Canvas(self, width=disp_w, height=disp_h, highlightthickness=1, cursor="crosshair",
                                highlightbackground=theme.current["border"], bg=theme.current["bg"])
        self.canvas.pack(padx=10)
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=10)
        self.ok_btn = ttk.Button(bar, text="Confirm", command=self._confirm, state="disabled")
        self.ok_btn.pack(side="right")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right", padx=(0, 8))
        self.hint = ttk.Label(bar, text="Drag with the left mouse button to draw a box.")
        self.hint.pack(side="left")

        theme.set_titlebar_dark(self, theme.current is theme.DARK)
        self.transient(parent)
        self.attributes("-topmost", True)
        self.grab_set()
        self.focus_force()

    def _clamp(self, x, y):
        return (min(max(x, 0), self._disp_size[0]), min(max(y, 0), self._disp_size[1]))

    def _press(self, e):
        self._origin = self._clamp(e.x, e.y)
        if self._rect:
            self.canvas.delete(self._rect)
        x, y = self._origin
        self._rect = self.canvas.create_rectangle(x, y, x, y, outline="#ff3b30", width=2)

    def _drag(self, e):
        x, y = self._clamp(e.x, e.y)
        self.canvas.coords(self._rect, self._origin[0], self._origin[1], x, y)

    def _release(self, e):
        x1, y1 = self._origin
        x2, y2 = self._clamp(e.x, e.y)
        left, right = sorted((x1, x2))
        top, bottom = sorted((y1, y2))
        if right - left < 4 or bottom - top < 4:
            self._box = None
            self.ok_btn.config(state="disabled")
            return
        w, h = self._img_size
        box = (int(left / self.scale), int(top / self.scale),
               int((right - left) / self.scale), int((bottom - top) / self.scale))
        x, y, bw, bh = box
        self._box = (min(x, w - 1), min(y, h - 1), max(1, min(bw, w - x)), max(1, min(bh, h - y)))
        self.ok_btn.config(state="normal")
        self.hint.config(text=f"Selected {self._box[2]} x {self._box[3]} px - press Confirm (or drag again).")

    def _confirm(self):
        self.result = self._box
        self.destroy()

    @classmethod
    def ask(cls, parent, title, instructions, image_bgr):
        dlg = cls(parent, title, instructions, image_bgr)
        parent.wait_window(dlg)
        return dlg.result


class SettingsDialog(tk.Toplevel):
    """Small window opened from the gear button in the top-right corner: appearance (theme)."""

    OPTIONS = (("system", "Match Windows (default)"), ("light", "Light"), ("dark", "Dark"))

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        self.title("Settings")
        self.resizable(False, False)
        self.transient(app)
        self.bind("<Escape>", lambda _e: self.destroy())

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        box = ttk.LabelFrame(body, text="Appearance")
        box.pack(fill="x")
        for i, (value, label) in enumerate(self.OPTIONS):
            ttk.Radiobutton(box, text=label, value=value, variable=app.theme_choice,
                            command=app._on_theme_change).pack(
                anchor="w", padx=12, pady=(8 if i == 0 else 2, 8 if i == len(self.OPTIONS) - 1 else 2))
        self.version_label = ttk.Label(body, text=f"AutoFishing v{af.__version__}")
        self.version_label.pack(anchor="w", pady=(12, 0))
        ttk.Button(body, text="Close", command=self.destroy).pack(anchor="e", pady=(8, 0))

        self.restyle()
        self.update_idletasks()
        x = app.winfo_rootx() + app.winfo_width() - self.winfo_reqwidth() - 16
        y = app.winfo_rooty() + 8
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.focus_set()

    def restyle(self):
        self.configure(bg=theme.current["bg"])
        self.version_label.config(foreground=theme.current["muted"])
        theme.set_titlebar_dark(self, self.app.mode == "dark")


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"AutoFishing v{af.__version__}")
        self.minsize(940, 720)

        # Window / taskbar icon (the same fishing rod as the .exe). Keep references so Tk doesn't drop them.
        self._icons = [ImageTk.PhotoImage(img) for img in load_icon_images()]
        try:
            self.iconphoto(True, *self._icons)   # True: every later window (dialogs) gets it too
        except tk.TclError:
            pass

        self.style = ttk.Style(self)
        self.cfg = af.load_config()
        choice = self.cfg.get("theme", "system")
        self.theme_choice = tk.StringVar(value=choice if choice in theme.THEME_CHOICES else "system")
        self.mode = theme.resolve(self.theme_choice.get())     # "light" or "dark"
        self.pal = theme.apply(self, self.style, self.mode)
        self._settings_dlg = None
        self._last_theme_check = 0.0
        self.template = af.load_template()
        self.detector = autodetect.SubtitleDetector()
        self.bot = None
        self.log_q = queue.Queue()
        self.event_q = queue.Queue()
        self._waiting_capture = False
        self._preview_photo = None
        self._template_photo = None
        self._last_frame_id = None
        self._windows: list = []          # WindowInfo list behind the dropdown (index i -> entry i + 1)
        self._pinned_hwnd = None          # the exact window the user picked (None = auto-detect)
        self._last_window_check = 0.0
        self._target_win = None           # the game window as of the last (throttled) check

        self._build_ui()
        self._refresh_windows()
        self._on_mode_change()
        self._restyle()
        self._register_hotkeys()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._tick)

    # -- UI construction ------------------------------------------------------
    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        # Status banner, with the settings gear in the top-right corner
        self.header = tk.Frame(self)
        self.header.pack(fill="x")
        self.banner = tk.Label(self.header, text="", font=("Segoe UI", 13, "bold"), anchor="w", padx=14, pady=10)
        self.banner.pack(side="left", fill="x", expand=True)
        self.gear = tk.Button(self.header, text="⚙", font=("Segoe UI Symbol", 16), bd=0, relief="flat",
                              padx=14, cursor="hand2", takefocus=0, command=self._open_settings)
        self.gear.pack(side="right", fill="y")
        Tooltip(self.gear, "Settings (appearance)")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="ns", padx=(10, 5), pady=6)
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=6)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)

        # --- 1. Setup
        setup = ttk.LabelFrame(left, text="1. Game window")
        setup.pack(fill="x", **{"pady": (0, 8)})
        ttk.Label(
            setup, justify="left", wraplength=300,
            text=("Turn on subtitles in Minecraft (Options > Accessibility Settings > Show Subtitles), "
                  "then press Start and cast your rod. The game window and your GUI size are found "
                  "automatically."),
        ).pack(anchor="w", **pad)

        pick = ttk.Frame(setup)
        pick.pack(fill="x", padx=10)
        pick.columnconfigure(0, weight=1)
        self.win_combo = ttk.Combobox(pick, state="readonly", width=42, postcommand=self._refresh_windows)
        self.win_combo.grid(row=0, column=0, sticky="ew")
        self.win_combo.bind("<<ComboboxSelected>>", self._on_window_selected)
        self.win_refresh_btn = ttk.Button(pick, text="↻", width=3, command=self._refresh_windows)
        self.win_refresh_btn.grid(row=0, column=1, padx=(6, 0))
        Tooltip(self.win_combo, "Every open window. If auto-detection picks the wrong one,\nchoose the game's window here.")
        Tooltip(self.win_refresh_btn, "Refresh the list of open windows")

        self.win_status = ttk.Label(setup, text="", justify="left", wraplength=300)
        self.win_status.pack(anchor="w", padx=10, pady=(6, 0))
        self.scale_status = ttk.Label(setup, text="", justify="left", wraplength=300)
        self.scale_status.pack(anchor="w", padx=10, pady=(2, 4))

        self.mode_var = tk.StringVar(value=self.cfg["mode"])
        modes = ttk.Frame(setup)
        modes.pack(fill="x", padx=10, pady=(2, 6))
        ttk.Radiobutton(modes, text="Automatic", value="auto", variable=self.mode_var,
                        command=self._on_mode_change).pack(side="left")
        manual_radio = ttk.Radiobutton(modes, text="Manual calibration", value="manual", variable=self.mode_var,
                                       command=self._on_mode_change)
        manual_radio.pack(side="left", padx=(12, 0))
        Tooltip(manual_radio, "Advanced: draw the subtitle area yourself. Use this if automatic detection\n"
                              "doesn't work (other language, or a resource pack that changes the font).")

        # Manual calibration (advanced): only shown when "Manual calibration" is selected.
        self.manual_frame = ttk.LabelFrame(setup, text="Manual calibration (advanced)")
        ttk.Label(
            self.manual_frame, justify="left", wraplength=290,
            text=("Click Calibrate, switch to Minecraft, cast your rod, and press F9 the moment "
                  "'Fishing Bobber splashes' shows up."),
        ).pack(anchor="w", **pad)
        self.cal_info = ttk.Label(self.manual_frame, text="", justify="left", wraplength=290)
        self.cal_info.pack(anchor="w", padx=10)
        btns = ttk.Frame(self.manual_frame)
        btns.pack(fill="x", **pad)
        self.cal_btn = ttk.Button(btns, text="Calibrate (F9)", command=self._calibrate_live, style="Big.TButton")
        self.cal_btn.pack(side="left")
        self.cal_img_btn = ttk.Button(btns, text="From screenshot...", command=self._calibrate_from_file)
        self.cal_img_btn.pack(side="left", padx=(8, 0))
        ttk.Label(self.manual_frame, text="Game window title must contain:").pack(anchor="w", padx=10)
        self.title_var = tk.StringVar(value=self.cfg["window_title"])
        title_entry = ttk.Entry(self.manual_frame, textvariable=self.title_var)
        title_entry.pack(fill="x", padx=10, pady=(2, 8))
        Tooltip(title_entry, "Manual mode only: clicks are allowed only while the focused window's\ntitle contains this text.")

        # --- 2. Fishing controls
        ctl = ttk.LabelFrame(left, text="2. Fish")
        ctl.pack(fill="x", pady=(0, 8))
        row1 = ttk.Frame(ctl)
        row1.pack(fill="x", **pad)
        self.start_btn = ttk.Button(row1, text="Start (F6)", command=self._start, style="Big.TButton")
        self.start_btn.pack(side="left")
        self.pause_btn = ttk.Button(row1, text="Pause (F7)", command=self._toggle_pause, state="disabled")
        self.pause_btn.pack(side="left", padx=8)
        self.stop_btn = ttk.Button(row1, text="Stop (F8)", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left")
        row2 = ttk.Frame(ctl)
        row2.pack(fill="x", padx=10, pady=(0, 8))
        self.test_btn = ttk.Button(row2, text="Test detection (no clicking)", command=self._test)
        self.test_btn.pack(side="left")
        Tooltip(self.test_btn, "Watches the subtitle area and reports splashes\nwithout ever clicking. Use it to check your setup.")

        # --- Settings
        sett = ttk.LabelFrame(left, text="Settings")
        sett.pack(fill="x")
        self.vars = {}
        for i, (key, label, lo, hi, step, fmt, tip) in enumerate(SETTINGS):
            lbl = ttk.Label(sett, text=label)
            lbl.grid(row=i, column=0, sticky="w", padx=(10, 6), pady=3)
            var = tk.StringVar(value=fmt % float(self.cfg[key]))
            spin = ttk.Spinbox(sett, from_=lo, to=hi, increment=step, textvariable=var, width=8, format=fmt)
            spin.grid(row=i, column=1, sticky="e", padx=(0, 10), pady=3)
            self.vars[key] = var
            Tooltip(lbl, tip)
            Tooltip(spin, tip)
        r = len(SETTINGS)
        self.focus_var = tk.BooleanVar(value=bool(self.cfg["require_focus"]))
        chk = ttk.Checkbutton(sett, text="Only click while the game window is focused", variable=self.focus_var)
        chk.grid(row=r, column=0, columnspan=2, sticky="w", padx=10, pady=(6, 0))
        Tooltip(chk, "Safety net: if you alt-tab away, the bot stops clicking\ninstead of right-clicking in some other program.")
        ttk.Button(sett, text="Reset to defaults", command=self._reset_settings).grid(
            row=r + 1, column=0, columnspan=2, sticky="w", padx=10, pady=(6, 8))

        # --- Live view
        live = ttk.LabelFrame(right, text="Live view")
        live.grid(row=0, column=0, sticky="ew")
        live.columnconfigure(1, weight=1)
        ttk.Label(live, text="Looking for:").grid(row=0, column=0, sticky="w", padx=10, pady=(8, 2))
        self.template_label = ttk.Label(live, text="(not calibrated)")
        self.template_label.grid(row=0, column=1, sticky="w", padx=4, pady=(8, 2))
        ttk.Label(live, text="Watching:").grid(row=1, column=0, sticky="nw", padx=10, pady=2)
        self.preview_label = ttk.Label(live, text="Appears once the bot is running or testing.",
                                       anchor="center", relief="sunken", width=60)
        self.preview_label.grid(row=1, column=1, sticky="ew", padx=4, pady=2, ipady=40)
        ttk.Label(live, text="Match:").grid(row=2, column=0, sticky="w", padx=10, pady=(2, 8))
        mrow = ttk.Frame(live)
        mrow.grid(row=2, column=1, sticky="ew", padx=4, pady=(2, 8))
        mrow.columnconfigure(0, weight=1)
        self.score_bar = ttk.Progressbar(mrow, maximum=100)
        self.score_bar.grid(row=0, column=0, sticky="ew")
        self.score_label = ttk.Label(mrow, text="--", width=22)
        self.score_label.grid(row=0, column=1, padx=(8, 0))

        stats = ttk.Frame(right)
        stats.grid(row=1, column=0, sticky="ew", pady=6)
        self.catch_label = ttk.Label(stats, text="Catches: 0", font=("Segoe UI", 11, "bold"))
        self.catch_label.pack(side="left")

        # --- Log
        logf = ttk.LabelFrame(right, text="Log")
        logf.grid(row=2, column=0, sticky="nsew")
        logbody = ttk.Frame(logf)
        logbody.pack(fill="both", expand=True, padx=6, pady=6)
        # A plain Text + ttk scrollbar (instead of ScrolledText) so the scrollbar follows the theme too.
        self.log_box = tk.Text(logbody, height=10, state="disabled", wrap="word", font=("Consolas", 9),
                               relief="flat", borderwidth=0, highlightthickness=1)
        log_scroll = ttk.Scrollbar(logbody, orient="vertical", command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.log_box.pack(side="left", fill="both", expand=True)

        self._log("Ready. Hotkeys: F6 start, F7 pause/resume, F8 stop, F9 capture (calibration).")

    # -- appearance (light / dark) ----------------------------------------------
    def _apply_theme(self):
        self.mode = theme.resolve(self.theme_choice.get())
        self.pal = theme.apply(self, self.style, self.mode)
        self._restyle()

    def _restyle(self):
        """Repaint everything that isn't a themed ttk widget, after the palette changed."""
        p = self.pal
        self.log_box.config(bg=p["log_bg"], fg=p["log_fg"], insertbackground=p["log_fg"],
                            selectbackground=p["accent"], selectforeground="#ffffff",
                            highlightbackground=p["border"], highlightcolor=p["accent"])
        theme.style_popdown(self.win_combo, p)
        if self._settings_dlg is not None and self._settings_dlg.winfo_exists():
            self._settings_dlg.restyle()
        self._update_window_status(force=True)
        self._refresh()   # banner and gear colours

    def _on_theme_change(self):
        choice = self.theme_choice.get()
        self.cfg["theme"] = choice if choice in theme.THEME_CHOICES else "system"
        try:
            af.save_config(self.cfg)
        except OSError:
            pass
        self._apply_theme()

    def _open_settings(self):
        if self._settings_dlg is not None and self._settings_dlg.winfo_exists():
            self._settings_dlg.lift()
            self._settings_dlg.focus_set()
            return
        self._settings_dlg = SettingsDialog(self)

    # -- logging / hotkeys ----------------------------------------------------
    def _log(self, msg: str):
        self.log_q.put(msg)

    def _append_log(self, msg: str):
        self.log_box.config(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def _register_hotkeys(self):
        try:
            for key, evt in (("f6", "start"), ("f7", "pause"), ("f8", "stop"), ("f9", "capture")):
                keyboard.add_hotkey(key, lambda e=evt: self.event_q.put(e))
        except Exception as e:
            self._log(f"Global hotkeys unavailable ({e}). Use the buttons instead "
                      f"(or run as administrator).")

    # -- settings -------------------------------------------------------------
    def _collect_settings(self, silent: bool = False) -> bool:
        """Copy the form into self.cfg (and validate). Returns False on bad input."""
        new = {}
        for key, label, lo, hi, _step, _fmt, _tip in SETTINGS:
            try:
                value = float(self.vars[key].get())
            except ValueError:
                if not silent:
                    messagebox.showerror("Invalid setting", f"'{label}' must be a number.")
                return False
            new[key] = min(max(value, lo), hi)
        self.cfg.update(new)
        self.cfg["mode"] = self.mode_var.get()
        self.cfg["require_focus"] = bool(self.focus_var.get())
        self.cfg["window_title"] = self.title_var.get().strip()
        for key, _l, _lo, _hi, _s, fmt, _t in SETTINGS:  # show the clamped values back to the user
            self.vars[key].set(fmt % self.cfg[key])
        return True

    def _reset_settings(self):
        for key, _l, _lo, _hi, _s, fmt, _t in SETTINGS:
            self.vars[key].set(fmt % af.DEFAULTS[key])
        self.focus_var.set(af.DEFAULTS["require_focus"])
        self.title_var.set(af.DEFAULTS["window_title"])

    # -- game window selection --------------------------------------------------
    def _refresh_windows(self):
        """Fill the dropdown with every open window (Auto first) and keep the current choice selected."""
        try:
            self._windows = winutil.list_windows()
        except Exception as e:   # never let a Win32 hiccup break the dropdown
            self._windows = []
            self._log(f"Couldn't list windows: {e}")
        self.win_combo["values"] = [AUTO_ENTRY] + [w.label() for w in self._windows]

        index = 0
        if self._pinned_hwnd:
            index = next((i for i, w in enumerate(self._windows, 1) if w.hwnd == self._pinned_hwnd), 0)
            if not index:
                self._pinned_hwnd = None       # that window is gone
        elif self.cfg.get("target_window"):     # remembered from last time
            match = winutil.match_hints(self._windows, self.cfg["target_window"])
            if match:
                self._pinned_hwnd = match.hwnd
                index = self._windows.index(match) + 1
        self.win_combo.current(index)
        self._update_window_status(force=True)

    def _on_window_selected(self, _event=None):
        index = self.win_combo.current()
        if index <= 0:
            self._pinned_hwnd = None
            self.cfg["target_window"] = None
            self._log("Game window: auto-detect")
        else:
            win = self._windows[index - 1]
            self._pinned_hwnd = win.hwnd
            self.cfg["target_window"] = win.hints()
            self._log(f"Game window chosen: {win.title} ({win.exe})")
        try:
            af.save_config(self.cfg)
        except OSError:
            pass
        self.win_combo.selection_clear()
        self._update_window_status(force=True)

    def _resolve_window(self):
        """What the bot calls to find the game window (runs on the bot's thread)."""
        return winutil.resolve_target(self.cfg.get("target_window"), self._pinned_hwnd)

    def _update_window_status(self, force: bool = False):
        """Show which window is in use, and the GUI scale state. Throttled: it enumerates windows."""
        now = time.monotonic()
        if not force and now - self._last_window_check < 1.5:
            return
        self._last_window_check = now
        bot = self.bot
        running = bool(bot and bot.running)

        if self.mode_var.get() == "manual":
            self._target_win = None
            self.win_status.config(text="Manual mode: watching the area you calibrated.", foreground=self.pal["fg"])
            self.scale_status.config(text="")
            return

        win = bot.window if running and bot.window else self._resolve_window()
        self._target_win = win
        if win is None:
            self.win_status.config(
                text="✗ No Minecraft window found. Start the game, or pick its window from the list.",
                foreground=self.pal["bad"])
        else:
            how = "chosen by you" if self._pinned_hwnd else "auto-detected"
            self.win_status.config(
                text=f"✓ {win.title}\n   {win.width}x{win.height}, {win.exe} ({how})",
                foreground=self.pal["ok"])

        if running and bot.scale:
            self.scale_status.config(text=f"GUI scale: {bot.scale}x")
        elif running:
            self.scale_status.config(text="GUI scale: learning... (needs one bite)")
        elif self.cfg.get("gui_scale"):
            self.scale_status.config(text=f"GUI scale: {self.cfg['gui_scale']}x (remembered)")
        else:
            self.scale_status.config(text="GUI scale: learned automatically on the first bite")

    # -- mode / calibration ---------------------------------------------------
    @property
    def manual_ready(self) -> bool:
        return bool(self.cfg.get("region")) and self.template is not None

    def _on_mode_change(self):
        manual = self.mode_var.get() == "manual"
        if manual:
            self.manual_frame.pack(fill="x", padx=10, pady=(0, 8))
        else:
            self.manual_frame.pack_forget()
        self.win_combo.config(state="disabled" if manual else "readonly")
        self.win_refresh_btn.config(state="disabled" if manual else "normal")
        self._refresh_looking_for()
        self._update_window_status(force=True)

    def _refresh_looking_for(self):
        """The 'Looking for' picture: the built-in reference (auto) or your calibrated template (manual)."""
        if self.mode_var.get() == "manual":
            if self.manual_ready:
                _x, _y, w, h = self.cfg["region"]
                self.cal_info.config(text=f"Calibrated: watching a {w} x {h} px area.")
                self._template_photo = to_photo(self.template, 420, 60, max_scale=3.0)
                self.template_label.config(image=self._template_photo, text="")
            else:
                self.cal_info.config(text="Not calibrated yet.")
                self.template_label.config(image="", text="(not calibrated)")
        else:
            self.cal_info.config(text="Calibrated: watching a "
                                      f"{self.cfg['region'][2]} x {self.cfg['region'][3]} px area."
                                 if self.manual_ready else "Not calibrated yet.")
            self._template_photo = to_photo(self.detector.ref, 420, 60, max_scale=4.0)
            self.template_label.config(image=self._template_photo, text="")

    def _calibrate_live(self):
        if self._waiting_capture:  # button doubles as "cancel"
            self._waiting_capture = False
            self._log("Calibration capture cancelled.")
            return
        self._waiting_capture = True
        self._log("Calibration: switch to Minecraft, cast, and press F9 when the splash subtitle shows.")

    def _do_capture(self):
        self._waiting_capture = False
        try:
            frame = af.desktop_screenshot()
            af.write_image(af.SCREENSHOT_PATH, frame)
        except Exception as e:
            messagebox.showerror("Capture failed", str(e))
            return
        self._log(f"Captured all monitors ({frame.shape[1]}x{frame.shape[0]}).")
        self.deiconify()
        self.lift()
        self._run_calibration(frame)

    def _calibrate_from_file(self):
        path = filedialog.askopenfilename(
            title="Pick a screenshot of your whole desktop showing the splash subtitle",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")])
        if not path:
            return
        frame = af.read_image(Path(path))
        if frame is None:
            messagebox.showerror("Could not open image", path)
            return
        self._run_calibration(frame)

    def _run_calibration(self, frame: np.ndarray):
        self._collect_settings(silent=True)
        box = RoiSelector.ask(
            self, "Step 1 of 2 - subtitle area",
            "Step 1 of 2: drag a box around the WHOLE subtitle area (every subtitle line, with a "
            "little margin), then press Confirm.", frame)
        if not box:
            self._log("Calibration cancelled.")
            return
        x, y, w, h = box
        area = frame[y:y + h, x:x + w]

        text_box = RoiSelector.ask(
            self, "Step 2 of 2 - the splash line",
            "Step 2 of 2: drag a TIGHT box around only the text 'Fishing Bobber splashes', "
            "then press Confirm.", area)
        if not text_box:
            self._log("Calibration cancelled.")
            return
        try:
            template = af.extract_template(area, text_box)
        except ValueError as e:
            messagebox.showerror("Calibration failed", str(e))
            return

        af.save_calibration(self.cfg, [x, y, w, h], template)   # also switches the config to manual mode
        self.template = template
        self.mode_var.set("manual")
        self._on_mode_change()
        score = af.match_score(area, template)
        self._log(f"Calibrated. Self-match score {score:.2f} (should be about 1.00).")
        messagebox.showinfo("Calibrated", "Calibration saved.\n\nTip: press 'Test detection' and "
                                          "reel in a fish to confirm it spots the splash.")

    # -- bot control ----------------------------------------------------------
    def _begin(self, dry_run: bool):
        if self.bot and self.bot.running:
            return
        manual = self.mode_var.get() == "manual"
        if manual and not self.manual_ready:
            messagebox.showwarning("Not calibrated", "Manual mode needs a calibration: click 'Calibrate' and "
                                                     "follow the steps, or switch back to Automatic.")
            return
        if self._waiting_capture:
            self._waiting_capture = False
        if not self._collect_settings():
            return
        af.save_config(self.cfg)
        cfg = dict(self.cfg)
        if dry_run:
            cfg["start_delay"] = 0
        self._last_frame_id = None
        self.bot = af.FishingBot(cfg, self.template if manual else None, log=self._log,
                                 window_resolver=self._resolve_window)
        self.bot.start(dry_run=dry_run)

    def _start(self):
        self._begin(dry_run=False)

    def _test(self):
        self._begin(dry_run=True)

    def _toggle_pause(self):
        if self.bot and self.bot.running:
            self.bot.toggle_pause()

    def _stop(self):
        if self.bot and self.bot.running:
            self.bot.stop()

    # -- periodic refresh -----------------------------------------------------
    def _tick(self):
        while True:
            try:
                self._append_log(self.log_q.get_nowait())
            except queue.Empty:
                break
        while True:
            try:
                evt = self.event_q.get_nowait()
            except queue.Empty:
                break
            if evt == "start":
                self._start()
            elif evt == "pause":
                self._toggle_pause()
            elif evt == "stop":
                self._stop()
            elif evt == "capture" and self._waiting_capture:
                self._do_capture()
        self._refresh()
        # "Match Windows": follow the system if the user flips Windows between light and dark.
        if self.theme_choice.get() == "system" and time.monotonic() - self._last_theme_check > 2.0:
            self._last_theme_check = time.monotonic()
            if theme.resolve("system") != self.mode:
                self._apply_theme()
        self.after(100, self._tick)

    def _refresh(self):
        bot = self.bot
        running = bool(bot and bot.running)

        # Banner
        if self._waiting_capture:
            text, key = "Waiting for F9 - go to Minecraft and press it when the splash subtitle shows", "wait"
        elif running:
            text, key = bot.status, bot.state
        elif self.mode_var.get() == "manual" and not self.manual_ready:
            text, key = "Manual mode: not calibrated - click Calibrate, or switch to Automatic", "warn"
        elif self.mode_var.get() == "auto" and self._target_win is None:
            text, key = "No Minecraft window found - start the game, or pick it from the list", "warn"
        else:
            text, key = "Ready - cast your rod, then press Start (F6)", "idle"
        bg, fg = theme.STATE_COLORS[self.mode][key]
        self.header.config(bg=bg)
        self.banner.config(text=text, bg=bg, fg=fg)
        self.gear.config(bg=bg, fg=fg, activeforeground=fg,
                         activebackground=theme.shade(bg, 0.2 if self.mode == "dark" else -0.12))

        # Buttons
        idle = not running
        self.start_btn.config(state="normal" if idle else "disabled")
        self.test_btn.config(state="normal" if idle else "disabled")
        self.cal_img_btn.config(state="normal" if idle else "disabled")
        self.win_combo.config(state="readonly" if idle and self.mode_var.get() == "auto" else "disabled")
        self.win_refresh_btn.config(state="normal" if idle and self.mode_var.get() == "auto" else "disabled")
        self.cal_btn.config(state="normal" if idle else "disabled",
                            text="Cancel capture" if self._waiting_capture else "Calibrate (F9)")
        self.stop_btn.config(state="normal" if running else "disabled")
        can_pause = running and not bot.dry_run
        self.pause_btn.config(state="normal" if can_pause else "disabled",
                              text="Resume (F7)" if (running and bot.paused) else "Pause (F7)")

        self._update_window_status()

        # Remember a GUI scale the bot just learned, so the next start doesn't need a bite to find it.
        if bot is not None and bot.auto and bot.cfg.get("gui_scale") \
                and bot.cfg["gui_scale"] != self.cfg.get("gui_scale"):
            self.cfg["gui_scale"], self.cfg["gui_scale_client"] = bot.cfg["gui_scale"], bot.cfg["gui_scale_client"]
            try:
                af.save_config(self.cfg)
            except OSError:
                pass
            self._update_window_status(force=True)

        # Live view
        if bot is not None:
            self.catch_label.config(
                text=f"{'Detections (test)' if bot.dry_run else 'Catches'}: {bot.catches}")
            frame = bot.last_frame
            if frame is not None and id(frame) != self._last_frame_id:
                self._last_frame_id = id(frame)
                self._preview_photo = to_photo(frame, 520, 220, max_scale=1.0)
                self.preview_label.config(image=self._preview_photo, text="")
            score = max(0.0, bot.last_score)
            thr = bot.threshold
            self.score_bar.config(value=score * 100)
            hit = "  MATCH" if score >= thr else ""
            self.score_label.config(text=f"{score:.2f} / need {thr:.2f}{hit}")

    # -- shutdown -------------------------------------------------------------
    def _on_close(self):
        if self.bot:
            self.bot.stop()
        self._collect_settings(silent=True)
        try:
            af.save_config(self.cfg)
        except OSError:
            pass
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        self.destroy()


def selftest() -> None:
    """
    `AutoFishing.exe --selftest`: exercise the parts that can break when packaged (screen capture,
    template matching, image -> Tk rendering, keyboard hooks) without opening a window or clicking.
    Raises on failure; main() turns that into exit code 1. Used by the release workflow.
    """
    shot = af.desktop_screenshot()
    assert shot.ndim == 3 and shot.shape[0] > 0 and shot.shape[1] > 0, "screen capture returned nothing"

    rng = np.random.default_rng(0)
    scene = rng.integers(0, 255, (80, 200, 3), dtype=np.uint8)
    template = af.to_gray(scene[20:50, 40:120])
    score = af.match_score(scene, template)
    assert score > 0.99, f"template match self-score too low: {score}"

    root = tk.Tk()
    root.withdraw()
    to_photo(scene, 100, 100)  # needs PIL.ImageTk + the bundled Tk
    root.destroy()

    # Auto-setup: the built-in reference must be found, at the right GUI scale, in a synthetic subtitle.
    detector = autodetect.SubtitleDetector()
    canvas = np.full((120, 700, 3), 40, dtype=np.uint8)
    line = cv2.cvtColor(detector.reference_at(4), cv2.COLOR_GRAY2BGR)
    canvas[20:20 + line.shape[0], 40:40 + line.shape[1]] = line
    found_scale, found_score, _ = detector.best_scale(canvas, (0, 0), range(1, 7))
    assert found_scale == 4 and found_score > 0.9, f"auto-detect failed: scale {found_scale}, score {found_score}"
    assert isinstance(winutil.list_windows(), list), "window listing failed"

    # Appearance + icon: the embedded icon must decode and convert for Tk, and both palettes must be complete.
    icon_root = tk.Tk()
    icon_root.withdraw()
    icons = [ImageTk.PhotoImage(img) for img in load_icon_images()]
    assert len(icons) == len(ICON_SIZES), "app icon failed to load"
    icon_root.destroy()
    assert set(theme.LIGHT) == set(theme.DARK), "light and dark palettes differ"

    keyboard.add_hotkey("f6", lambda: None)
    keyboard.unhook_all()


def main():
    # A windowed (no-console) exe has no stdout/stderr; make stray prints harmless.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w"))

    try:
        if "--selftest" in sys.argv:
            selftest()
            return 0
        App().mainloop()
    except Exception:
        # With no console, a crash would otherwise vanish silently.
        import traceback
        details = traceback.format_exc()
        crash_log = af.DATA_DIR / "crash.log"
        try:
            crash_log.write_text(details, encoding="utf-8")
        except OSError:
            pass
        if "--selftest" not in sys.argv:  # never block CI on a dialog
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("AutoFishing crashed", f"{details[-1200:]}\n\nDetails saved to:\n{crash_log}")
            root.destroy()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
