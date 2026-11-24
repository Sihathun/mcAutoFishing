"""Synthetic Minecraft-like frames for the tests (no game, no screenshots needed)."""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import autodetect  # noqa: E402

TEXT_LEVEL = 220  # value of "text" pixels in the built-in reference


def scene(cw: int, ch: int, seed: int = 0) -> np.ndarray:
    """Blurry random 'game world' background (BGR)."""
    rng = np.random.default_rng(seed)
    noise = rng.integers(0, 256, (ch, cw, 3), dtype=np.uint8)
    return cv2.GaussianBlur(noise, (0, 0), 4)


def make_client(scale: int, cw: int = 1400, ch: int = 800, fade: float = 1.0, seed: int = 0,
                draw_text: bool = True, reference: "np.ndarray | None" = None):
    """
    A fake game-window client area with a subtitle line drawn the way Minecraft draws it: the 1x
    reference blown up by `scale`, on the block grid, inside a dark translucent box.

    Returns (client_bgr, (text_x, text_y)) with the text's top-left corner in client pixels.
    """
    ref = autodetect.load_reference() if reference is None else reference
    img = scene(cw, ch, seed)
    rh, rw = ref.shape
    x0, y0 = int(cw * autodetect.SEARCH_FRAC_X), int(ch * autodetect.SEARCH_FRAC_Y)
    # Subtitles hug the bottom-right corner and sit on the GUI-scale grid, like in the real game.
    tx = min(((x0 + 40) // scale + 1) * scale, ((cw - rw * scale - 20) // scale) * scale)
    ty = ((y0 + 60) // scale + 1) * scale
    assert tx >= x0 and tx + rw * scale < cw and ty + rh * scale < ch, "test canvas too small for this scale"
    if draw_text:
        pad = 4 * scale
        box = img[ty - pad:ty + rh * scale + pad, tx - pad:tx + rw * scale + pad]
        box[:] = (box * 0.5).astype(np.uint8)       # translucent black subtitle background
        mask = cv2.resize((ref >= TEXT_LEVEL - 10).astype(np.uint8), None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_NEAREST).astype(bool)
        region = img[ty:ty + rh * scale, tx:tx + rw * scale]
        region[mask] = int(TEXT_LEVEL * fade)       # fading text: the colour darkens as it ages
    return img, (tx, ty)
