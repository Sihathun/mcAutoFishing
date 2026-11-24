"""
Automatic subtitle detection: finds the "Fishing Bobber splashes" line without any calibration.

How it works
------------
Minecraft draws its whole GUI at an integer scale N (the "GUI scale"). A subtitle captured at scale N is
therefore the 1x text with every pixel blown up into an exact N x N block, and the block grid starts at the
game window's top-left corner. So instead of asking the user to draw boxes, we:

1. look only at the bottom-right of the game window, where subtitles live,
2. for each candidate N, crop that area to the N-pixel grid and shrink it by N (exact block averaging),
3. match the built-in 1x reference of the splash line against it.

The N whose match scores ~0.99 is the real GUI scale (wrong scales score below ~0.5), which also gives the
subtitle position for free. Brightness-invariant matching (TM_CCOEFF_NORMED) copes with the fading text.

Only the default Minecraft font and the English text are built in. Anything else (other languages, font
resource packs) should use the manual calibration instead.
"""
import base64

import cv2
import numpy as np

# 1x image of "Fishing Bobber splashes" (text = 220, background = 30), made by tools/make_reference.py
REFERENCE_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAHwAAAAKCAAAAABTDmW+AAAA5ElEQVQ4EcXB0U0bUQBFwdlGtphT+K2NZ4dIloUg+WLmuv2e656j5Wk5ljdD/liO5QfLq+XNdS8vli8tlqflWH6wvFreXPeIZWhN1hBDjiFG1mRNjKzJ09CaLEYMrYkRQ9e9HGs5Jmst1mI5FmuxJmuyFmuyHMsxWcvDWo7JWqzluO7lWEYWay3WYjkWa7EWa7HmaPlrZLEWQ0YWa46MXPdyLMdarLVYhhyLtViLtVjLsbxYQ5bFcqzFWj6t616ONWSx1mI5lmOIkSFDDC2fhiwPy9CQxTI05Lp9Y8j/W/7Fdfs9HwkiozdzLp/CAAAAAElFTkSuQmCC"
)

LEARN_THRESHOLD = 0.85   # score needed to accept a GUI scale while learning (real matches score ~0.99)
LEARN_FRAMES = 2         # consecutive frames that must agree before the scale is locked in
MAX_GUI_SCALE = 12
SEARCH_FRAC_X = 0.45     # subtitles live in the bottom-right: search from 45% across ...
SEARCH_FRAC_Y = 0.40     # ... and 40% down, to the window's edges


def load_reference() -> np.ndarray:
    """The built-in 1x reference as a grayscale array."""
    data = np.frombuffer(base64.b64decode(REFERENCE_PNG_B64), dtype=np.uint8)
    ref = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if ref is None:
        raise RuntimeError("Built-in reference image is corrupt")
    return ref


class SubtitleDetector:
    """Scores how well the splash line appears in a captured area, at a given GUI scale."""

    def __init__(self, reference: np.ndarray | None = None):
        self.ref = load_reference() if reference is None else reference

    def reference_at(self, scale: int) -> np.ndarray:
        """The reference blown up to what it looks like on screen at this GUI scale."""
        return cv2.resize(self.ref, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)

    def search_rect(self, client_w: int, client_h: int) -> tuple[int, int, int, int]:
        """(x, y, w, h), relative to the game window's client area, of the area to capture."""
        x0, y0 = int(client_w * SEARCH_FRAC_X), int(client_h * SEARCH_FRAC_Y)
        return x0, y0, client_w - x0, client_h - y0

    def candidate_scales(self, client_w: int, client_h: int) -> list[int]:
        """GUI scales at which the reference could possibly fit in the search area."""
        _, _, w, h = self.search_rect(client_w, client_h)
        rh, rw = self.ref.shape
        return [s for s in range(1, MAX_GUI_SCALE + 1) if rw * s <= w and rh * s <= h]

    def _score_gray(self, gray: np.ndarray, origin: tuple[int, int], scale: int):
        # The block grid starts at the client's (0, 0); `origin` is where this crop begins, so skip
        # to the first grid line before shrinking, otherwise blocks would be averaged across edges.
        ox, oy = (-origin[0]) % scale, (-origin[1]) % scale
        w = (gray.shape[1] - ox) // scale
        h = (gray.shape[0] - oy) // scale
        rh, rw = self.ref.shape
        if w < rw or h < rh:
            return 0.0, None
        block = gray[oy:oy + h * scale, ox:ox + w * scale]
        small = block if scale == 1 else cv2.resize(block, (w, h), interpolation=cv2.INTER_AREA)
        result = cv2.matchTemplate(small, self.ref, cv2.TM_CCOEFF_NORMED)
        result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
        _, best, _, loc = cv2.minMaxLoc(result)
        return float(best), (ox + loc[0] * scale, oy + loc[1] * scale)

    def score(self, region_bgr: np.ndarray, origin: tuple[int, int], scale: int):
        """
        Match score (about -1..1) at one known GUI scale, and where the text's top-left corner is
        (in region pixels). `origin` is the region's top-left corner relative to the window's client area.
        """
        return self._score_gray(cv2.cvtColor(region_bgr, cv2.COLOR_BGR2GRAY), origin, scale)

    def best_scale(self, region_bgr: np.ndarray, origin: tuple[int, int], scales):
        """Try every candidate scale. Returns (scale, score, location) of the best one."""
        gray = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2GRAY)
        best = (0, 0.0, None)
        for s in scales:
            score, loc = self._score_gray(gray, origin, s)
            if score > best[1]:
                best = (s, score, loc)
        return best
