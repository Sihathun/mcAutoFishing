"""
Minecraft auto-fisher driven by the "Fishing Bobber splashes" subtitle.

How it works
------------
Minecraft's Subtitles accessibility option prints every sound as text on screen.
The bot watches that part of the screen, and when the line
"Fishing Bobber splashes" appears it right-clicks to reel in and right-clicks
again to recast.

This module holds the core logic (screen capture, matching, the FishingBot loop,
calibration helpers) plus a small command-line interface. The graphical app lives
in gui.py and reuses everything here.

Command line usage
------------------
    python autofish.py calibrate    # one time, teaches the bot what the line looks like
    python autofish.py run          # then switch to Minecraft with the rod in your hand

Hotkeys (CLI):  F7 = pause/resume   F8 = quit
"""
import argparse
import ctypes
import json
import os
import sys
import threading
import time
from pathlib import Path

import cv2
import keyboard
import mss
import numpy as np
import pyautogui

import autodetect
import winutil

try:
    from mss.exception import ScreenShotError
except ImportError:  # very old mss
    ScreenShotError = Exception

__version__ = "1.1.0-beta.1"


def _data_dir() -> Path:
    """
    Where config.json / template.png / screenshots live.

    From source: next to this file. Packaged as an .exe: %APPDATA%\\AutoFishing, because a
    one-file exe unpacks itself into a temp folder that is deleted when the app closes.
    """
    if getattr(sys, "frozen", False):
        root = Path(os.environ.get("APPDATA") or Path.home())
        folder = root / "AutoFishing"
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    return Path(__file__).resolve().parent


DATA_DIR = _data_dir()
CONFIG_PATH = DATA_DIR / "config.json"
TEMPLATE_PATH = DATA_DIR / "template.png"
SCREENSHOT_PATH = DATA_DIR / "calibration_screenshot.png"

DEFAULTS = {
    "mode": "auto",           # "auto" = find the game window + GUI scale itself, "manual" = use the calibration below
    "target_window": None,    # {"exe", "cls", "title"} of a window the user picked from the list (None = auto-detect)
    "gui_scale": None,        # GUI scale learned in auto mode (reused while the window size stays the same)
    "gui_scale_client": None,  # [width, height] of the game window when that scale was learned
    "region": None,           # manual mode: [left, top, width, height] of the subtitle area on screen
    "match_threshold": 0.80,  # 0..1, how closely the line must match
    "reel_delay": 0.15,       # seconds between detection and reeling in
    "recast_delay": 1.0,      # seconds between reel-in and recast
    "cooldown": 4.0,          # seconds to ignore the screen after a catch (subtitle lingers ~3s)
    "poll_interval": 0.05,    # seconds between screen checks
    "start_delay": 5,         # seconds to switch to Minecraft after pressing Start
    "monitor": 0,             # mss monitor index (0 = all monitors combined)
    "require_focus": True,    # only click while the Minecraft window is focused
    "window_title": "Minecraft",  # text the focused window's title must contain
}

# mss renamed mss.mss -> mss.MSS in newer releases; support both.
MSS = getattr(mss, "MSS", None) or mss.mss

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = True  # slam the mouse into a screen corner to abort


# --------------------------------------------------------------------------- #
# Files / config
# --------------------------------------------------------------------------- #
def read_image(path: Path, flags: int = cv2.IMREAD_COLOR):
    """cv2.imread that also works with non-ASCII Windows paths. Returns None if unreadable."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


def write_image(path: Path, img: np.ndarray) -> None:
    ok, buf = cv2.imencode(Path(path).suffix or ".png", img)
    if not ok:
        raise OSError(f"Could not encode image for {path}")
    buf.tofile(str(path))


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except (OSError, ValueError):
            pass  # unreadable config -> fall back to defaults
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def load_template():
    """The saved gray image of the splash line, or None if not calibrated yet."""
    if not TEMPLATE_PATH.exists():
        return None
    return read_image(TEMPLATE_PATH, cv2.IMREAD_GRAYSCALE)


# --------------------------------------------------------------------------- #
# Image helpers
# --------------------------------------------------------------------------- #
def to_gray(bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)


def match_score(region_bgr: np.ndarray, template_gray: np.ndarray) -> float:
    """
    Best template-match score (about -1..1) of the subtitle line inside the region.

    TM_CCOEFF_NORMED ignores overall brightness/contrast, so it still matches when the
    subtitle text has faded to gray as it ages.
    """
    gray = to_gray(region_bgr)
    th, tw = template_gray.shape
    if gray.shape[0] < th or gray.shape[1] < tw:
        return 0.0
    result = cv2.matchTemplate(gray, template_gray, cv2.TM_CCOEFF_NORMED)
    result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
    return float(result.max())


def grab(sct, monitor_idx: int, region=None) -> np.ndarray:
    mon = sct.monitors[monitor_idx]
    if region:
        left, top, w, h = region
        box = {"left": mon["left"] + left, "top": mon["top"] + top, "width": w, "height": h}
    else:
        box = mon
    return np.ascontiguousarray(np.array(sct.grab(box))[:, :, :3])  # BGRA -> BGR


def grab_abs(sct, left: int, top: int, width: int, height: int) -> np.ndarray:
    """Grab a rectangle given in absolute screen coordinates (can be negative on multi-monitor setups)."""
    box = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
    return np.ascontiguousarray(np.array(sct.grab(box))[:, :, :3])  # BGRA -> BGR


def desktop_screenshot() -> np.ndarray:
    """Screenshot of ALL monitors stitched together (mss monitor 0)."""
    with MSS() as sct:
        return grab(sct, 0)


def foreground_window_title() -> str:
    """Title of the window that currently has focus ('' if unknown / not Windows)."""
    if sys.platform != "win32":
        return ""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


# --------------------------------------------------------------------------- #
# Calibration helpers (shared by the CLI and the GUI)
# --------------------------------------------------------------------------- #
def extract_template(area_bgr: np.ndarray, box) -> np.ndarray:
    """Cut the splash line out of the subtitle area. Raises ValueError if it looks empty."""
    x, y, w, h = box
    if w <= 0 or h <= 0:
        raise ValueError("No text selected.")
    template = to_gray(area_bgr[y:y + h, x:x + w])
    if template.size == 0 or template.std() < 5:
        raise ValueError("The selected box has no visible text in it. Select a tighter box on the text.")
    return template


def save_calibration(cfg: dict, region, template: np.ndarray) -> None:
    cfg["region"] = [int(v) for v in region]
    cfg["monitor"] = 0  # region coordinates are relative to the all-monitors capture
    cfg["mode"] = "manual"  # a manual calibration means: use it instead of auto-detection
    write_image(TEMPLATE_PATH, template)
    save_config(cfg)


def select_roi(title: str, img: np.ndarray, max_w: int = 1600, max_h: int = 900) -> tuple[int, int, int, int]:
    """cv2.selectROI that shrinks big images to fit the screen but returns full-size coordinates."""
    scale = min(3.0, max_w / img.shape[1], max_h / img.shape[0])  # shrink big images, enlarge small ones
    if scale != 1:
        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_NEAREST
        shown = cv2.resize(img, None, fx=scale, fy=scale, interpolation=interp)
    else:
        shown = img
    x, y, w, h = cv2.selectROI(title, shown, showCrosshair=False)
    cv2.destroyAllWindows()
    return int(x / scale), int(y / scale), int(w / scale), int(h / scale)


def calibrate(image_path: str | None) -> None:
    """Command-line calibration (the GUI has its own friendlier version)."""
    cfg = load_config()
    if image_path:
        frame = read_image(Path(image_path))
        if frame is None:
            sys.exit(f"Could not read image: {image_path}")
    else:
        print("Go to Minecraft, cast your rod and wait for a bite.")
        print("The moment you see 'Fishing Bobber splashes' in the subtitles, press F9.")
        print("(Tip: start calibration with the rod already cast; subtitles fade after ~3 s.)")
        keyboard.wait("f9")
        frame = desktop_screenshot()
        write_image(SCREENSHOT_PATH, frame)
        print(f"Captured all monitors ({frame.shape[1]}x{frame.shape[0]}), saved to {SCREENSHOT_PATH.name}")

    print("\nStep 1/2: drag a box around the WHOLE subtitle area (all lines), press ENTER.")
    x, y, w, h = select_roi("Step 1: subtitle area (whole desktop shown)", frame)
    if w == 0 or h == 0:
        sys.exit("No area selected.")
    area = frame[y:y + h, x:x + w]

    print("Step 2/2: drag a tight box around ONLY the text 'Fishing Bobber splashes', press ENTER.")
    box = select_roi("Step 2: the splash line", area)
    try:
        template = extract_template(area, box)
    except ValueError as e:
        sys.exit(str(e))

    save_calibration(cfg, [x, y, w, h], template)
    score = match_score(area, template)
    print(f"\nSaved {CONFIG_PATH.name} and {TEMPLATE_PATH.name}. Self-match score: {score:.2f} (should be ~1.0)")
    print("Now run:  python autofish.py run   (or launch the GUI:  python gui.py)")


# --------------------------------------------------------------------------- #
# The bot
# --------------------------------------------------------------------------- #
class FishingBot:
    """
    Watches the subtitles on a background thread and reels in / recasts on a splash.

    Two modes (cfg["mode"]):
      * "auto"   - finds the game window itself, watches the bottom-right of it, and learns the GUI
                   scale from the first splash (see autodetect.py). No calibration needed.
      * "manual" - watches the screen region and template from a manual calibration.

    Safe to drive from a GUI: start(), stop(), toggle_pause() return immediately, and the
    live values (state, status, catches, last_score, threshold, last_frame, window, scale) can be
    read at any time.

    state: idle | starting | waiting | learning | watching | testing | reeling | cooldown | paused
    """

    def __init__(self, cfg: dict, template: "np.ndarray | None" = None, log=print, window_resolver=None):
        """
        template: manual mode only (the calibrated splash image).
        window_resolver: auto mode only; callable returning the game's WindowInfo (or None). Defaults to
        automatic Minecraft detection / the window remembered in cfg["target_window"].
        """
        self.cfg = dict(cfg)
        self.template = template
        self._log = log
        self.auto = self.cfg.get("mode", "auto") != "manual"
        if not self.auto and template is None:
            raise ValueError("Manual mode needs a calibrated template")
        self._resolver = window_resolver or (lambda: winutil.resolve_target(self.cfg.get("target_window")))
        self.detector = autodetect.SubtitleDetector() if self.auto else None
        self.window = None            # WindowInfo of the game window (auto mode)
        self.scale = None             # GUI scale in use (auto mode), None while still learning
        self.scale_client = None      # game window size the scale was learned at
        if self.auto and self.cfg.get("gui_scale") and self.cfg.get("gui_scale_client"):
            self.scale = int(self.cfg["gui_scale"])
            self.scale_client = tuple(int(v) for v in self.cfg["gui_scale_client"])
        self._cand_scale = None
        self._cand_hits = 0
        self._last_window_lookup = 0.0
        self._last_rescan = 0.0
        self.catches = 0
        self.last_score = 0.0
        self.threshold = self.cfg["match_threshold"]
        self.last_frame = None
        self.state = "idle"
        self.status = "Idle"
        self.dry_run = False
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread = None
        self._last_focus_warning = 0.0

    # -- control ------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def start(self, dry_run: bool = False) -> None:
        """dry_run=True only watches and reports detections; it never clicks."""
        if self.running:
            return
        self.dry_run = dry_run
        self.catches = 0
        self._cand_scale, self._cand_hits = None, 0
        self.window = None
        self._stop.clear()
        self._paused.clear()
        self._thread = threading.Thread(target=self._run, name="FishingBot", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def toggle_pause(self) -> None:
        if self._paused.is_set():
            self._paused.clear()
            self.log("Resumed")
        else:
            self._paused.set()
            self.log("Paused")

    # -- internals ----------------------------------------------------------
    def log(self, msg: str) -> None:
        self._log(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def _set(self, state: str, status: str) -> None:
        self.state, self.status = state, status

    def _sleep(self, seconds: float) -> bool:
        """Interruptible sleep. Returns False if stop() was requested meanwhile."""
        return not self._stop.wait(max(0.0, seconds))

    def _minecraft_focused(self) -> bool:
        if not self.cfg.get("require_focus", True):
            return True
        if self.auto:
            # Compare window handles, not titles: modpacks rename the game window.
            return self.window is not None and winutil.foreground_hwnd() == self.window.hwnd
        needle = (self.cfg.get("window_title") or "").strip().lower()
        return not needle or needle in foreground_window_title().lower()

    def _warn_unfocused(self) -> None:
        if time.monotonic() - self._last_focus_warning < 5:
            return
        self._last_focus_warning = time.monotonic()
        if self.auto:
            self.log(
                f"Splash seen but not clicking: the focused window is '{foreground_window_title()}', not the "
                "game window. Click into the game, or untick the focus option."
            )
        else:
            self.log(
                f"Splash seen but not clicking: focused window is '{foreground_window_title()}' "
                f"(its title must contain '{self.cfg.get('window_title')}'). Click into Minecraft, "
                "or untick the focus option."
            )

    # -- observation: grab the screen and score it ---------------------------
    def _observe(self, sct):
        """One look at the screen. Returns (frame, score, hit), or None if there is nothing to look at."""
        return self._observe_auto(sct) if self.auto else self._observe_manual(sct)

    def _observe_manual(self, sct):
        cfg = self.cfg
        self._set("testing" if self.dry_run else "watching",
                  "Testing - watching only, no clicks" if self.dry_run else "Watching for a splash")
        frame = grab(sct, cfg["monitor"], cfg["region"])
        score = match_score(frame, self.template)
        self.threshold = cfg["match_threshold"]
        return frame, score, score >= self.threshold

    def _current_window(self):
        """The game window, re-resolved if it closed. Lookups are rate-limited while none is found."""
        win = self.window
        if win is not None and winutil.is_window(win.hwnd):
            return win
        now = time.monotonic()
        if win is None and now - self._last_window_lookup < 1.0:
            return None
        self._last_window_lookup = now
        new = self._resolver()
        if new is not None and (win is None or new.hwnd != win.hwnd):
            self.log(f"Game window: {new.title} ({new.exe}, {new.width}x{new.height})")
        elif new is None and win is not None:
            self.log("Game window closed - waiting for it to come back")
        self.window = new
        return new

    def _observe_auto(self, sct):
        cfg = self.cfg
        win = self._current_window()
        if win is None:
            self._set("waiting", "Waiting for the Minecraft window - start the game or pick it from the list")
            return None
        if winutil.is_minimized(win.hwnd):
            self._set("waiting", "The game window is minimized - restore it so it can be watched")
            return None
        rect = winutil.client_rect(win.hwnd)
        if rect is None:
            self._set("waiting", "Can't read the game window's size")
            return None
        left, top, cw, ch = rect

        if self.scale and self.scale_client != (cw, ch):
            if self.scale_client:   # not just the very first look after loading the saved scale
                self.log(f"Game window is now {cw}x{ch} - learning the GUI scale again")
            self.scale = None
            self.scale_client = None
        x0, y0, rw, rh = self.detector.search_rect(cw, ch)
        try:
            frame = grab_abs(sct, left + x0, top + y0, rw, rh)
        except ScreenShotError:
            self._set("waiting", "Can't capture the game window - is it fully on screen?")
            return None

        if self.scale:   # known GUI scale: one cheap check
            score, _loc = self.detector.score(frame, (x0, y0), self.scale)
            self.threshold = cfg["match_threshold"]
            if score < self.threshold and time.monotonic() - self._last_rescan > 2.0:
                # No match: that's normal between bites, but it could also mean the in-game GUI scale was
                # changed without resizing the window. A rare full re-scan catches that.
                self._last_rescan = time.monotonic()
                other, other_score, _ = self.detector.best_scale(
                    frame, (x0, y0), self.detector.candidate_scales(cw, ch))
                if other != self.scale and other_score >= autodetect.LEARN_THRESHOLD:
                    self.log(f"GUI scale looks like {other}x now (was {self.scale}x) - learning it again")
                    self.scale, self.scale_client = None, None
                    self._cand_scale, self._cand_hits = None, 0
            if self.scale:
                self._set("testing" if self.dry_run else "watching",
                          ("Testing - watching only, no clicks" if self.dry_run else "Watching for a splash")
                          + f" (GUI scale {self.scale}x)")
                return frame, score, score >= self.threshold

        # Still learning: try every GUI scale; the real one scores ~0.99, wrong ones stay below ~0.5.
        scale, score, _loc = self.detector.best_scale(frame, (x0, y0), self.detector.candidate_scales(cw, ch))
        self.threshold = autodetect.LEARN_THRESHOLD
        self._set("learning", "Learning your GUI size - cast your rod and wait for the first bite")
        if score >= autodetect.LEARN_THRESHOLD:
            self._cand_hits = self._cand_hits + 1 if scale == self._cand_scale else 1
            self._cand_scale = scale
        else:
            self._cand_scale, self._cand_hits = None, 0
        if self._cand_hits >= autodetect.LEARN_FRAMES:
            self.scale, self.scale_client = scale, (cw, ch)
            self.cfg["gui_scale"], self.cfg["gui_scale_client"] = scale, [cw, ch]
            self.threshold = cfg["match_threshold"]
            self.log(f"GUI scale detected: {scale}x (score {score:.2f}) - remembered for next time")
            return frame, score, True
        return frame, score, False

    def _click(self) -> bool:
        if not self._minecraft_focused():
            self._warn_unfocused()
            return False
        pyautogui.mouseDown(button="right")
        time.sleep(0.05)
        pyautogui.mouseUp(button="right")
        return True

    def _run(self) -> None:
        cfg = self.cfg
        try:
            with MSS() as sct:
                # Give the user time to switch to the game.
                if not self.dry_run and cfg["start_delay"] > 0:
                    self.log(f"Starting in {cfg['start_delay']:.0f}s - switch to Minecraft and hold the rod")
                    end = time.monotonic() + cfg["start_delay"]
                    while time.monotonic() < end:
                        self._set("starting", f"Starting in {end - time.monotonic() + 0.5:.0f}s - switch to Minecraft")
                        if not self._sleep(0.2):
                            return
                self.log("Test mode: watching only, no clicks" if self.dry_run else "Fishing started")

                while not self._stop.is_set():
                    if self._paused.is_set():
                        self._set("paused", "Paused")
                        self._sleep(0.1)
                        continue
                    obs = self._observe(sct)
                    if obs is None:        # nothing to look at (e.g. game window not found)
                        if not self._sleep(0.5):
                            break
                        continue
                    frame, score, hit = obs
                    self.last_frame, self.last_score = frame, score

                    if hit:
                        if self.dry_run:
                            self.catches += 1
                            self.log(f"Splash detected (score {score:.2f}) - test mode, not clicking")
                            self._set("cooldown", "Cooldown")
                            if not self._sleep(cfg["cooldown"]):
                                break
                            continue

                        if not self._minecraft_focused():
                            self._warn_unfocused()
                            self._sleep(1.0)
                            continue

                        self._set("reeling", "Splash! Reeling in")
                        if not self._sleep(cfg["reel_delay"]):
                            break
                        if not self._click():          # reel in
                            continue
                        self.catches += 1
                        self.log(f"Splash detected (score {score:.2f}) - catch #{self.catches}")
                        if not self._sleep(cfg["recast_delay"]):
                            break
                        self._click()                  # recast
                        self._set("cooldown", f"Cooldown ({cfg['cooldown']:.1f}s)")
                        if not self._sleep(cfg["cooldown"]):   # let the old subtitle fade
                            break
                        continue

                    if not self._sleep(cfg["poll_interval"]):
                        break
        except pyautogui.FailSafeException:
            self.log("Stopped: mouse was moved into a screen corner (failsafe)")
        except Exception as e:  # keep the thread's failure visible instead of dying silently
            self.log(f"Error: {e!r}")
        finally:
            self._set("idle", "Stopped")
            self.log("Stopped")


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #
def run(debug: bool) -> None:
    cfg = load_config()
    template = None
    if cfg["mode"] == "manual":
        template = load_template()
        if not cfg["region"] or template is None:
            sys.exit("Manual mode but not calibrated. Run:  python autofish.py calibrate")
    print(f"Mode: {cfg['mode']}" + ("  (no calibration needed: the game window and GUI size are detected)"
                                    if cfg["mode"] == "auto" else ""))

    bot = FishingBot(cfg, template, log=print)
    keyboard.add_hotkey("f7", bot.toggle_pause)
    print("Auto-fishing: F7 = pause/resume, F8 = quit.")
    bot.start()
    try:
        while bot.running and not keyboard.is_pressed("f8"):
            if debug:
                print(f"score={bot.last_score:.2f}   ", end="\r")
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    bot.stop()
    bot.join(2)
    if bot.scale and bot.cfg.get("gui_scale"):   # remember the learned GUI scale for next time
        cfg["gui_scale"], cfg["gui_scale_client"] = bot.cfg["gui_scale"], bot.cfg["gui_scale_client"]
        save_config(cfg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Minecraft subtitle-based auto fisher")
    sub = parser.add_subparsers(dest="cmd", required=True)
    cal = sub.add_parser("calibrate", help="teach the script where/what the splash subtitle is")
    cal.add_argument("--image", help="use an existing screenshot instead of capturing live")
    r = sub.add_parser("run", help="start auto-fishing")
    r.add_argument("--debug", action="store_true", help="print the live match score")
    args = parser.parse_args()

    if args.cmd == "calibrate":
        calibrate(args.image)
    else:
        run(args.debug)


if __name__ == "__main__":
    main()
