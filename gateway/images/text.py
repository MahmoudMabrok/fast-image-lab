"""Composites requested text onto a finished PNG.

The distilled model cannot draw script glyphs — in-image English is fragile
and Arabic is hopeless — so requested wording is drawn by the gateway after
ComfyUI decodes the latent. That makes any script renderable and
pixel-reliable: Arabic is shaped and reordered here (arabic-reshaper +
python-bidi) instead of being left to the model.

Every text item may pick one of the bundled typefaces; several items can be
requested at once and items sharing a position stack.

The fonts are vendored next to this module: the production VM has no system
fonts at all, so nothing may be resolved from the host fontconfig.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont

_FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"

#: typeface key -> vendored OFL font. Every family renders Arabic through
#: presentation forms, so no system shaping library is required on the host.
#: "sans" (Noto Sans) also covers Latin, so it is the only face usable for
#: English/Greek/Cyrillic captions in addition to Arabic.
FONTS: dict[str, Path] = {
    "naskh": _FONTS_DIR / "NotoNaskhArabic-Regular.ttf",
    "kufi": _FONTS_DIR / "NotoKufiArabic-Regular.ttf",
    "sans": _FONTS_DIR / "NotoSans-Regular.ttf",
}
DEFAULT_FONT = "naskh"

#: Positions where a text block may be anchored. Corners + edges + center.
POSITIONS = (
    "top-left",
    "top-center",
    "top-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
    "center",
)

MAX_TEXTS = 4
MAX_CHARS = 120

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class CaptionError(ValueError):
    """The requested text is not drawable."""


def shape(text: str) -> str:
    """Connect and reorder the script so a glyph font can draw it.

    Applied to Latin and CJK the round-trip is identity.
    """
    return get_display(arabic_reshaper.reshape(text))


def _parse_color(value: Any) -> tuple[int, int, int] | None:
    """A #rrggbb hex color, or None for the automatic contrast pick.

    A 3-element list/tuple of ints is accepted too: that is how ``loads``
    sees a canonicalized color after ``dumps`` round-trips it through JSON.
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        if len(value) != 3 or not all(isinstance(v, int) and 0 <= v <= 255 for v in value):
            raise CaptionError(f"color {value!r} must be three 0-255 RGB integers")
        return (value[0], value[1], value[2])
    value = str(value).strip()
    if not value:
        return None
    if not _HEX_COLOR.match(value):
        raise CaptionError(f"color {value!r} is not a #rrggbb hex value")
    return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))  # type: ignore[return-value]


def _normalize_font(value: Any) -> str:
    key = str(value or DEFAULT_FONT).strip().lower()
    if key not in FONTS:
        raise CaptionError(f"font must be one of: {', '.join(FONTS)}")
    return key


def validate_texts(raw: Any) -> list[dict[str, Any]]:
    """Validate/normalize a raw `texts` payload (API or MCP) into the
    canonical per-item shape this module draws:
    ``{"text": str, "position": str, "color": tuple|None, "font": str}``."""
    if raw is None:
        return []
    if isinstance(raw, str) and not raw.strip():
        return []
    if isinstance(raw, (str, dict)):
        # Tolerate single-item shorthands: "abc" and {"text": "abc"}.
        raw = [{"text": raw}] if isinstance(raw, str) else [raw]
    if not isinstance(raw, list):
        raise CaptionError("texts must be a list of {text, position, color, font}")
    if not raw:
        return []
    if len(raw) > MAX_TEXTS:
        raise CaptionError(f"at most {MAX_TEXTS} text items, got {len(raw)}")

    items: list[dict[str, Any]] = []
    for entry in raw:
        if isinstance(entry, str):
            entry = {"text": entry}
        if not isinstance(entry, dict):
            raise CaptionError("each text item must be an object")
        text = str(entry.get("text") or "").strip()
        if not text:
            raise CaptionError("each text item needs its text")
        if len(text) > MAX_CHARS:
            raise CaptionError(f"each text is at most {MAX_CHARS} characters")
        position = str(entry.get("position") or "center").strip()
        if position not in POSITIONS:
            raise CaptionError(f"position must be one of: {', '.join(POSITIONS)}")
        items.append(
            {
                "text": text,
                "position": position,
                "color": _parse_color(entry.get("color")),
                "font": _normalize_font(entry.get("font")),
            }
        )
    return items


def _contrast(c: tuple[int, int, int]) -> tuple[int, int, int]:
    """White or near-black by relative luminance, so text stays readable on
    whatever the model painted."""
    r, g, b = (v / 255 for v in c)

    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    return (244, 244, 244) if luminance < 0.36 else (14, 16, 24)


def _background_average(img: Image.Image) -> tuple[int, int, int]:
    """Coarse average of the canvas; the auto color picks against this."""
    step = 8
    r = g = b = 0.0
    n = 1
    for y in range(0, img.height, step):
        for x in range(0, img.width, step):
            pr, pg, pb = img.getpixel((min(x, img.width - 1), min(y, img.height - 1)))
            r += pr
            g += pg
            b += pb
            n += 1
    return (int(r / n), int(g / n), int(b / n))


def _font_for(size: int, font_key: str) -> ImageFont.FreeTypeFont:
    path = FONTS.get(font_key, FONTS[DEFAULT_FONT])
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:  # noqa: BLE001 - fall back to the default face
        return ImageFont.truetype(str(FONTS[DEFAULT_FONT]), size)


def _wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    font_key: str,
) -> list[str]:
    """Greedy wrap on the LOGICAL text (RTL must break lines the reader
    intends), measuring the shaped width. A long unbroken run breaks by length.
    """
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        trial = (current + " " + word).strip()
        width = draw.textlength(shape(trial), font=font)
        if width <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            while draw.textlength(shape(word), font=font) > max_width:
                cut = max(1, len(word) - 2)
                lines.append(word[:cut])
                word = word[cut:]
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _place(
    img: Image.Image,
    *,
    item: dict[str, Any],
    bg_avg: tuple[int, int, int],
    offset: int,
    scale: float,
) -> None:
    """Draw one text block, auto-sized, anchored at the item's position,
    shifted by `offset` for items sharing that position. Mutates `img`."""
    if img.width < 32 or img.height < 32:
        return
    text = item["text"]
    font_key = item["font"]
    color = item.get("color")
    fill, outline = (
        (color, _contrast(color)) if color is not None else (_contrast(bg_avg), _contrast(_contrast(bg_avg)))
    )

    draw = ImageDraw.Draw(img, "RGBA")
    base = max(12.0, 0.055 * min(img.width, img.height)) * max(0.1, min(3.0, scale))
    max_width = int(img.width * 0.86)

    for size in range(int(base), 9, -2):
        font = _font_for(size, font_key)
        lines = _wrap(draw, text, font, max_width, font_key)
        metrics: list[tuple[str, int, int]] = []
        for line in lines:
            l0, l1, rw, rb = draw.textbbox((0, 0), shape(line), font=font, stroke_width=1)
            metrics.append((line, max(1, rb - l1 + int(size * 0.18)), rw - l0 + 12))
        if not metrics:
            continue
        total_h = sum(h for _, h, _ in metrics)
        if max(w for _, _, w in metrics) > max_width:
            continue
        if total_h > img.height * 0.55 and size > int(base) // 2:
            continue

        margin_x = int(img.width * 0.05)
        margin_y = int(img.height * 0.04)
        position = item["position"]

        if position.startswith("top"):
            top = margin_y + offset
        elif position.startswith("bottom"):
            top = img.height - margin_y - total_h - offset
        else:  # center
            top = (img.height - total_h) // 2 + offset

        cursor = top
        for line, lh, w in metrics:
            if position.endswith("left"):
                x = margin_x
            elif position.endswith("right"):
                x = img.width - margin_x - w
            else:
                x = (img.width - w) // 2
            draw.text(
                (x, cursor),
                shape(line),
                font=font,
                fill=fill,
                stroke_width=max(1, size // 20),
                stroke_fill=outline + (200,),
            )
            cursor += lh
        return

    # Fallback for extreme aspect ratios where no size fits: minimal line up top.
    font = _font_for(10, font_key)
    draw.text(
        (10, 10),
        shape(text),
        font=font,
        fill=fill,
        stroke_width=1,
        stroke_fill=outline + (200,),
    )


def apply_caption(
    path: Path,
    items: list[dict[str, Any]] | None,
    *,
    out_path: Path | None = None,
) -> Path:
    """Draw every requested item onto the PNG at `path` (in place unless
    `out_path` is given) and return the path of the written file.

    `items` is the canonical shape from :func:`validate_texts`; empty means
    "no text" and leaves the file untouched.
    """
    path = Path(path)
    target = Path(out_path) if out_path else path
    items = items or []
    if not items:
        if target != path and path.exists():
            import shutil

            shutil.copyfile(path, target)
        return target

    with Image.open(path) as base:
        img = base.convert("RGB")

    bg_avg = _background_average(img)
    # More items -> each takes a smaller share of the canvas.
    scale = {1: 1.0, 2: 0.85, 3: 0.72, 4: 0.62}[len(items)]
    used: dict[str, int] = {p: 0 for p in POSITIONS}
    gap = int(0.04 * min(img.width, img.height))

    measure = ImageDraw.Draw(img)
    for item in items:
        position = item["position"]
        base = max(12.0, 0.055 * min(img.width, img.height)) * scale
        font = _font_for(int(base), item["font"])
        lines = _wrap(measure, item["text"], font, int(img.width * 0.86), item["font"])
        height = 0
        for line in lines:
            l0, l1, rw, rb = measure.textbbox((0, 0), shape(line), font=font, stroke_width=1)
            height += max(1, rb - l1 + max(1, int(base * 0.18)))
        _place(img, item=item, bg_avg=bg_avg, offset=used[position], scale=scale)
        used[position] += height + gap

    img.save(target, format="PNG")
    return target


def dumps(items: list[dict[str, Any]]) -> str:
    """Canonical JSON for persistence (tuples become lists)."""
    return json.dumps(items, separators=(",", ":"), ensure_ascii=False)


def loads(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return validate_texts(data)
