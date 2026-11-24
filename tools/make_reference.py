"""
Developer tool: build the built-in "Fishing Bobber splashes" reference image used by auto-setup.

Minecraft draws its GUI at an integer scale, so a subtitle captured at GUI scale N is the 1x text with
every pixel blown up into an N x N block. This script takes a manually calibrated template (see
`autofish.py calibrate`) plus the GUI scale it was captured at, shrinks it back to 1x, cleans it into a
two-level image (text vs background) and prints the base64 PNG to paste into
`REFERENCE_PNG_B64` in autodetect.py.

    python tools/make_reference.py --template template.png --scale 5

(The GUI scale can be read off the template: its edges fall on multiples of N pixels.)
"""
import argparse
import base64
import sys
from pathlib import Path

import cv2
import numpy as np

TEXT_LEVEL, BG_LEVEL = 220, 30


def block_offset(gray: np.ndarray, scale: int) -> tuple[int, int]:
    """Find the (x, y) crop offset that best aligns the image to the scale x scale pixel grid."""
    def err(img: np.ndarray, axis: int, off: int) -> float:
        a = img[off:] if axis == 0 else img[:, off:]
        n = (a.shape[axis] // scale) * scale
        a = a[:n] if axis == 0 else a[:, :n]
        # differences inside would-be blocks should be ~0 when the grid is aligned
        d = np.abs(np.diff(a.astype(int), axis=axis))
        interior = (np.arange(d.shape[axis]) % scale) != (scale - 1)
        return float(d.take(np.where(interior)[0], axis=axis).sum())
    ox = min(range(scale), key=lambda o: err(gray, 1, o))
    oy = min(range(scale), key=lambda o: err(gray, 0, o))
    return ox, oy


def build_reference(template_gray: np.ndarray, scale: int) -> np.ndarray:
    ox, oy = block_offset(template_gray, scale)
    g = template_gray[oy:, ox:]
    h, w = (g.shape[0] // scale) * scale, (g.shape[1] // scale) * scale
    small = g[:h, :w].reshape(h // scale, scale, w // scale, scale).mean(axis=(1, 3))
    threshold = (small.min() + small.max()) / 2
    clean = np.where(small > threshold, TEXT_LEVEL, BG_LEVEL).astype(np.uint8)
    # crop to the text's bounding box plus a 1-pixel margin
    ys, xs = np.where(clean == TEXT_LEVEL)
    y0, y1 = max(ys.min() - 1, 0), min(ys.max() + 2, clean.shape[0])
    x0, x1 = max(xs.min() - 1, 0), min(xs.max() + 2, clean.shape[1])
    return clean[y0:y1, x0:x1]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--template", required=True, type=Path, help="calibrated template.png (grayscale)")
    p.add_argument("--scale", required=True, type=int, help="GUI scale the template was captured at")
    p.add_argument("--preview", type=Path, help="optional path to write a 6x preview PNG")
    args = p.parse_args()

    data = np.fromfile(str(args.template), dtype=np.uint8)
    template = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if template is None:
        sys.exit(f"Could not read {args.template}")
    ref = build_reference(template, args.scale)
    print(f"reference size: {ref.shape[1]}x{ref.shape[0]} px at 1x", file=sys.stderr)
    if args.preview:
        cv2.imwrite(str(args.preview), cv2.resize(ref, None, fx=6, fy=6, interpolation=cv2.INTER_NEAREST))
    ok, buf = cv2.imencode(".png", ref)
    print(base64.b64encode(buf.tobytes()).decode("ascii"))


if __name__ == "__main__":
    main()
