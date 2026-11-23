"""
AutoFishing GUI - a point-and-click front end for autofish.py.

    python gui.py

Hotkeys (work even while Minecraft is focused):
    F6 = start    F7 = pause / resume    F8 = stop    F9 = capture screenshot (during calibration)
"""
import ctypes
import queue
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

# Must happen before the first window exists so Tk isn't blurry / mis-scaled on high-DPI screens.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

import cv2
import keyboard
import numpy as np
from PIL import Image, ImageTk

import autofish as af

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

# bot.state -> (banner background, banner text colour)
STATE_COLORS = {
    "idle": ("#e5e7eb", "#111827"),
    "starting": ("#fde68a", "#78350f"),
    "watching": ("#16a34a", "#ffffff"),
    "reeling": ("#16a34a", "#ffffff"),
    "cooldown": ("#16a34a", "#ffffff"),
    "testing": ("#2563eb", "#ffffff"),
    "paused": ("#f59e0b", "#111827"),
    "warn": ("#fecaca", "#7f1d1d"),
    "wait": ("#bfdbfe", "#1e3a8a"),
}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
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
        tk.Label(self.tip, text=self.text, justify="left", background="#ffffe0", relief="solid",
                 borderwidth=1, padx=6, pady=4).pack()

    def _hide(self, _event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class RoiSelector(tk.Toplevel):
    """Modal window: show an image, let the user drag a rectangle, return it in image pixels."""

    def __init__(self, parent: tk.Misc, title: str, instructions: str, image_bgr: np.ndarray):
        super().__init__(parent)
        self.title(title)
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

        self.canvas = tk.Canvas(self, width=disp_w, height=disp_h, highlightthickness=1, cursor="crosshair")
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


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AutoFishing")
        self.minsize(900, 640)

        style = ttk.Style(self)
        style.theme_use("vista" if "vista" in style.theme_names() else "clam")
        style.configure("Big.TButton", padding=(10, 6))

        self.cfg = af.load_config()
        self.template = af.load_template()
        self.bot = None
        self.log_q = queue.Queue()
        self.event_q = queue.Queue()
        self._waiting_capture = False
        self._preview_photo = None
        self._template_photo = None
        self._last_frame_id = None

        self._build_ui()
        self._refresh_calibration_info()
        self._register_hotkeys()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._tick)

    # -- UI construction ------------------------------------------------------
    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        # Status banner
        self.banner = tk.Label(self, text="", font=("Segoe UI", 13, "bold"), anchor="w", padx=14, pady=10)
        self.banner.pack(fill="x")

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
        setup = ttk.LabelFrame(left, text="1. Setup")
        setup.pack(fill="x", **{"pady": (0, 8)})
        ttk.Label(
            setup, justify="left", wraplength=290,
            text=("Turn on subtitles in Minecraft:\nOptions > Accessibility Settings > Show Subtitles.\n\n"
                  "Then click Calibrate, switch to Minecraft, cast your rod, and press F9 the moment "
                  "'Fishing Bobber splashes' shows up."),
        ).pack(anchor="w", **pad)
        self.cal_info = ttk.Label(setup, text="", justify="left", wraplength=290)
        self.cal_info.pack(anchor="w", padx=10)
        btns = ttk.Frame(setup)
        btns.pack(fill="x", **pad)
        self.cal_btn = ttk.Button(btns, text="Calibrate (F9)", command=self._calibrate_live, style="Big.TButton")
        self.cal_btn.pack(side="left")
        self.cal_img_btn = ttk.Button(btns, text="From screenshot...", command=self._calibrate_from_file)
        self.cal_img_btn.pack(side="left", padx=(8, 0))

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
        chk = ttk.Checkbutton(sett, text="Only click while the focused window's title contains:", variable=self.focus_var)
        chk.grid(row=r, column=0, columnspan=2, sticky="w", padx=10, pady=(6, 0))
        Tooltip(chk, "Safety net: if you alt-tab away, the bot stops clicking\ninstead of right-clicking in some other program.")
        self.title_var = tk.StringVar(value=self.cfg["window_title"])
        title_entry = ttk.Entry(sett, textvariable=self.title_var)
        title_entry.grid(row=r + 1, column=0, columnspan=2, sticky="ew", padx=10, pady=(2, 4))
        Tooltip(title_entry, "The focused window's title must contain this text.")
        ttk.Button(sett, text="Reset to defaults", command=self._reset_settings).grid(
            row=r + 2, column=0, columnspan=2, sticky="w", padx=10, pady=(2, 8))

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
        self.log_box = scrolledtext.ScrolledText(logf, height=10, state="disabled", wrap="word",
                                                 font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True, padx=6, pady=6)

        self._log("Ready. Hotkeys: F6 start, F7 pause/resume, F8 stop, F9 capture (calibration).")

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

    # -- calibration ----------------------------------------------------------
    @property
    def calibrated(self) -> bool:
        return bool(self.cfg.get("region")) and self.template is not None

    def _refresh_calibration_info(self):
        if self.calibrated:
            _x, _y, w, h = self.cfg["region"]
            self.cal_info.config(text=f"Calibrated: watching a {w} x {h} px area.")
            self._template_photo = to_photo(self.template, 420, 60, max_scale=3.0)
            self.template_label.config(image=self._template_photo, text="")
        else:
            self.cal_info.config(text="Not calibrated yet.")
            self.template_label.config(image="", text="(not calibrated)")

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

        af.save_calibration(self.cfg, [x, y, w, h], template)
        self.template = template
        self._refresh_calibration_info()
        score = af.match_score(area, template)
        self._log(f"Calibrated. Self-match score {score:.2f} (should be about 1.00).")
        messagebox.showinfo("Calibrated", "Calibration saved.\n\nTip: press 'Test detection' and "
                                          "reel in a fish to confirm it spots the splash.")

    # -- bot control ----------------------------------------------------------
    def _begin(self, dry_run: bool):
        if self.bot and self.bot.running:
            return
        if not self.calibrated:
            messagebox.showwarning("Not calibrated", "Calibrate first: click 'Calibrate' and follow the steps.")
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
        self.bot = af.FishingBot(cfg, self.template, log=self._log)
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
        self.after(100, self._tick)

    def _refresh(self):
        bot = self.bot
        running = bool(bot and bot.running)

        # Banner
        if self._waiting_capture:
            text, key = "Waiting for F9 - go to Minecraft and press it when the splash subtitle shows", "wait"
        elif running:
            text, key = bot.status, bot.state
        elif not self.calibrated:
            text, key = "Not calibrated - click Calibrate to get started", "warn"
        else:
            text, key = "Ready - press Start (F6)", "idle"
        bg, fg = STATE_COLORS[key]
        self.banner.config(text=text, bg=bg, fg=fg)

        # Buttons
        idle = not running
        self.start_btn.config(state="normal" if idle else "disabled")
        self.test_btn.config(state="normal" if idle else "disabled")
        self.cal_img_btn.config(state="normal" if idle else "disabled")
        self.cal_btn.config(state="normal" if idle else "disabled",
                            text="Cancel capture" if self._waiting_capture else "Calibrate (F9)")
        self.stop_btn.config(state="normal" if running else "disabled")
        can_pause = running and not bot.dry_run
        self.pause_btn.config(state="normal" if can_pause else "disabled",
                              text="Resume (F7)" if (running and bot.paused) else "Pause (F7)")

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
            thr = bot.cfg["match_threshold"]
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


def main():
    App().mainloop()


if __name__ == "__main__":
    sys.exit(main())
