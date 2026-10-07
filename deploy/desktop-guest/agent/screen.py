"""The desktop's eyes: a screenshot, and the overlays that turn one into coordinates.

A model that sees a screenshot has to come back with a PIXEL. Three aids make that reliable,
none of which hides a second model call behind the run's back — each is a picture the run's own
model reads on its own turn:

- MARKS (Set-of-Mark): every element the accessibility tree reported is boxed and numbered on
  the image, so the run answers with a NUMBER and the click lands on the element's centre. This
  is the default way to point at anything a toolkit exposes.
- GRID: labelled lines every `step` pixels, so a coordinate read off the image is read against a
  ruler rather than estimated.
- ZOOM: a crop around a predicted point, enlarged, with a fine grid labelled in SCREEN
  coordinates and a crosshair on the prediction — predict on the whole screen, then refine once
  on the close-up. Two or three looks instead of a search over quadrants.

The geometry is pure and tested on the host; Pillow is imported only where pixels are touched.
"""
from __future__ import annotations

import base64
import io
import os
import subprocess
import tempfile
from pathlib import Path

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
#: Mark colours, cycled: saturated and mutually distinct on both light and dark UI.
PALETTE = ((230, 25, 75), (0, 130, 200), (60, 180, 75), (245, 130, 48), (145, 30, 180),
           (0, 128, 128), (240, 50, 230), (128, 0, 0))
ZOOM_MAX_FACTOR = 6
ZOOM_MIN_RADIUS = 16


def zoom_box(x: int, y: int, radius: int, screen: tuple[int, int]) -> tuple[int, int, int, int]:
    """The square crop (x0, y0, x1, y1) of side 2*radius around (x, y), shifted to stay on
    screen rather than shrunk — a crop at the edge keeps its size, so every zoom of one radius
    has one scale.
    """
    w, h = screen
    r = max(ZOOM_MIN_RADIUS, min(int(radius), w // 2, h // 2))
    x0 = min(max(int(x) - r, 0), w - 2 * r)
    y0 = min(max(int(y) - r, 0), h - 2 * r)
    return x0, y0, x0 + 2 * r, y0 + 2 * r


def zoom_step(radius: int) -> int:
    """Grid spacing on a zoom, in SCREEN pixels: about eight lines across the crop, rounded to
    a number a reader adds in their head.
    """
    raw = max(1, (2 * radius) // 8)
    for nice in (2, 5, 10, 20, 25, 50, 100):
        if raw <= nice:
            return nice
    return 100


def grid_lines(start: int, end: int, step: int) -> list[int]:
    """Screen coordinates of the grid lines in [start, end): the multiples of `step`."""
    first = -(-start // step) * step
    return list(range(first, end, step))


def to_screen(px: float, offset: int, factor: float) -> int:
    """A pixel on an enlarged crop, back to the screen coordinate it shows."""
    return round(offset + px / factor)


def _overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def label_box(rect: tuple[int, int, int, int], text_w: int, text_h: int,
              screen: tuple[int, int],
              taken: list[tuple[int, int, int, int]] | None = None) -> tuple[int, int]:
    """Where a mark's number tag goes: above the box's top-left corner, else inside it, else at
    its other corners — the first spot no earlier tag covers, and never off the image. A dense
    page otherwise stacks tags on one another until no number can be read.
    """
    x0, y0, x1, y1 = rect
    candidates = [(x0, y0 - text_h - 2), (x0 + 1, y0 + 1), (x1 - text_w, y0 - text_h - 2),
                  (x1 - text_w - 1, y0 + 1), (x0, y1 + 1), (x0 - text_w - 2, y0)]
    clamped = []
    for cx, cy in candidates:
        tx = min(max(cx, 0), max(screen[0] - text_w - 1, 0))
        ty = min(max(cy, 0), max(screen[1] - text_h - 1, 0))
        clamped.append((tx, ty))
        if not any(_overlaps((tx, ty, tx + text_w, ty + text_h), t) for t in taken or []):
            return tx, ty
    return clamped[0]


# ---- pixels --------------------------------------------------------------------------------

def capture(display: str):
    """The whole screen as a Pillow image (XCB grab; `scrot` when Pillow was built without
    XCB).
    """
    from PIL import Image, ImageGrab
    try:
        return ImageGrab.grab(xdisplay=display).convert("RGB")
    except OSError:
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            subprocess.run(["scrot", "-o", path], check=True, timeout=10,
                           env={**os.environ, "DISPLAY": display})
            with Image.open(path) as img:
                return img.convert("RGB")
        finally:
            Path(path).unlink()


def _font(size: int):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def draw_marks(img, elements: list[dict]) -> None:
    """Box and number every element (dicts with n, x, y, w, h) on the image in place."""
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    font = _font(13)
    taken: list[tuple[int, int, int, int]] = []
    for el in elements:
        colour = PALETTE[el["n"] % len(PALETTE)]
        rect = (el["x"], el["y"], el["x"] + max(el["w"], 1), el["y"] + max(el["h"], 1))
        draw.rectangle(rect, outline=colour, width=2)
        text = str(el["n"])
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        tw, th = right - left + 4, bottom - top + 4
        tx, ty = label_box(rect, tw, th, img.size, taken)
        taken.append((tx, ty, tx + tw, ty + th))
        draw.rectangle((tx, ty, tx + tw, ty + th), fill=colour)
        draw.text((tx + 2 - left, ty + 2 - top), text, fill=(255, 255, 255), font=font)


def draw_grid(img, step: int, *, offset: tuple[int, int] = (0, 0), factor: float = 1.0) -> None:
    """Labelled lines every `step` SCREEN pixels. On a zoom, `offset` is the crop's top-left
    and `factor` its enlargement, so the labels still read in screen coordinates.
    """
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img, "RGBA")
    font = _font(11)
    w, h = img.size
    ox, oy = offset
    for sx in grid_lines(ox, ox + int(w / factor) + 1, step):
        px = round((sx - ox) * factor)
        draw.line((px, 0, px, h), fill=(255, 0, 160, 110), width=1)
        draw.text((px + 2, 1), str(sx), fill=(255, 0, 160, 255), font=font)
    for sy in grid_lines(oy, oy + int(h / factor) + 1, step):
        py = round((sy - oy) * factor)
        draw.line((0, py, w, py), fill=(255, 0, 160, 110), width=1)
        if py >= 14:                    # the top row already carries the x labels
            draw.text((1, py + 1), str(sy), fill=(255, 0, 160, 255), font=font)


def draw_crosshair(img, px: int, py: int) -> None:
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    for colour, width in (((255, 255, 255), 3), ((0, 0, 0), 1)):
        draw.line((px - 14, py, px - 4, py), fill=colour, width=width)
        draw.line((px + 4, py, px + 14, py), fill=colour, width=width)
        draw.line((px, py - 14, px, py - 4), fill=colour, width=width)
        draw.line((px, py + 4, px, py + 14), fill=colour, width=width)


def zoom(img, x: int, y: int, radius: int, factor: int):
    """(enlarged crop, its box) around (x, y), gridded in screen coordinates, crosshair on."""
    from PIL import Image
    factor = max(1, min(int(factor), ZOOM_MAX_FACTOR))
    box = zoom_box(x, y, radius, img.size)
    crop = img.crop(box)
    big = crop.resize((crop.width * factor, crop.height * factor), Image.Resampling.NEAREST)
    draw_grid(big, zoom_step((box[2] - box[0]) // 2), offset=box[:2], factor=factor)
    draw_crosshair(big, (int(x) - box[0]) * factor, (int(y) - box[1]) * factor)
    return big, box


def png_b64(img, scale: float = 1.0) -> str:
    from PIL import Image
    if scale and scale != 1.0:
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                         Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return base64.b64encode(buf.getvalue()).decode("ascii")
