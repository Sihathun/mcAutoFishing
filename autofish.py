"""
Minecraft auto-fisher driven by the "Fishing Bobber splashes" subtitle.

How it works
------------
Minecraft's Subtitles accessibility option prints every sound as text on screen.
The script watches that part of the screen, and when the line
"Fishing Bobber splashes" appears it right-clicks to reel in and right-clicks
again to recast.

Usage
-----
1. In Minecraft:  Options > Accessibility Settings > Show Subtitles = ON
   (Run the game in windowed / borderless-fullscreen mode, not exclusive fullscreen.)
2. python autofish.py calibrate    # one time, teaches the script what the line looks like
3. python autofish.py run          # then switch to Minecraft with the rod in your hand

Hotkeys while running:  F7 = pause/resume   F8 = quit
"""
import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import keyboard
import mss
import numpy as np
import pyautogui

CONFIG_PATH = Path(__file__).with_name("config.json")
TEMPLATE_PATH = Path(__file__).with_name("template.png")

DEFAULTS = {
    "region": None,          # [left, top, width, height] of the subtitle area on screen
    "match_threshold": 0.80,  # 0..1, how closely the line must match the template
    "reel_delay": 0.15,      # seconds between detection and reeling in
    "recast_delay": 1.0,     # seconds between reel-in and recast
    "cooldown": 4.0,         # seconds to ignore the screen after a catch (subtitle lingers ~3s)
    "poll_interval": 0.05,   # seconds between screen checks
    "monitor": 0,            # mss monitor index (0 = all monitors combined)
}

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = True  # slam the mouse into a screen corner to abort


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


def grab(sct: mss.mss, monitor_idx: int, region=None) -> np.ndarray:
    mon = sct.monitors[monitor_idx]
    if region:
        left, top, w, h = region
        box = {"left": mon["left"] + left, "top": mon["top"] + top, "width": w, "height": h}
    else:
        box = mon
    return np.ascontiguousarray(np.array(sct.grab(box))[:, :, :3])  # BGRA -> BGR


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    return cfg


# --------------------------------------------------------------------------- #
# Calibration
# --------------------------------------------------------------------------- #
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
    cfg = load_config()
    # monitor 0 in mss = ALL monitors stitched together, so it works wherever Minecraft is.
    cfg["monitor"] = 0
    with mss.mss() as sct:
        if image_path:
            frame = cv2.imread(image_path)
            if frame is None:
                sys.exit(f"Could not read image: {image_path}")
        else:
            print("Go to Minecraft, cast your rod and wait for a bite.")
            print("The moment you see 'Fishing Bobber splashes' in the subtitles, press F9.")
            print("(Tip: start calibration with the rod already cast; subtitles fade after ~3 s.)")
            keyboard.wait("f9")
            frame = grab(sct, 0)
            shot = Path(__file__).with_name("calibration_screenshot.png")
            cv2.imwrite(str(shot), frame)
            print(f"Captured all monitors ({frame.shape[1]}x{frame.shape[0]}), saved to {shot.name}")

    print("\nStep 1/2: drag a box around the WHOLE subtitle area (all lines), press ENTER.")
    x, y, w, h = select_roi("Step 1: subtitle area (whole desktop shown)", frame)
    if w == 0 or h == 0:
        sys.exit("No area selected.")
    region = [x, y, w, h]
    area = frame[y:y + h, x:x + w]

    print("Step 2/2: drag a tight box around ONLY the text 'Fishing Bobber splashes', press ENTER.")
    tx, ty, tw, th = select_roi("Step 2: the splash line", area)
    if tw == 0 or th == 0:
        sys.exit("No text selected.")

    template = to_gray(area[ty:ty + th, tx:tx + tw])
    if template.std() < 5:
        sys.exit("The selected box has no visible text in it. Try again with a tighter box on the text.")
    cv2.imwrite(str(TEMPLATE_PATH), template)

    cfg["region"] = region
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    score = match_score(area, template)
    print(f"\nSaved {CONFIG_PATH.name} and {TEMPLATE_PATH.name}. Self-match score: {score:.2f} (should be ~1.0)")
    print("Now run:  python autofish.py run")


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #
def right_click() -> None:
    pyautogui.mouseDown(button="right")
    time.sleep(0.05)
    pyautogui.mouseUp(button="right")


def run(debug: bool) -> None:
    cfg = load_config()
    if not cfg["region"] or not TEMPLATE_PATH.exists():
        sys.exit("Not calibrated yet. Run:  python autofish.py calibrate")
    template = cv2.imread(str(TEMPLATE_PATH), cv2.IMREAD_GRAYSCALE)

    paused = False
    catches = 0

    def toggle():
        nonlocal paused
        paused = not paused
        print("PAUSED" if paused else "RESUMED")

    keyboard.add_hotkey("f7", toggle)
    print("Auto-fishing started. F7 = pause/resume, F8 = quit.")
    print("Switch to Minecraft now. Starting in 5 seconds...")
    time.sleep(5)

    with mss.mss() as sct:
        while not keyboard.is_pressed("f8"):
            if paused:
                time.sleep(0.1)
                continue

            region = grab(sct, cfg["monitor"], cfg["region"])
            score = match_score(region, template)
            if debug:
                print(f"score={score:.2f}", end="\r")

            if score >= cfg["match_threshold"]:
                catches += 1
                print(f"[{time.strftime('%H:%M:%S')}] Splash detected (score {score:.2f}) - catch #{catches}")
                time.sleep(cfg["reel_delay"])
                right_click()                      # reel in
                time.sleep(cfg["recast_delay"])
                right_click()                      # recast
                time.sleep(cfg["cooldown"])        # let the old subtitle fade
                continue

            time.sleep(cfg["poll_interval"])
    print("\nStopped.")


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
