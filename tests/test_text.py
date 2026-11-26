"""In-image caption overlay: shaping, multi-font, position, contrast, validation."""

from __future__ import annotations

import pathlib

import pytest
from PIL import Image

from gateway.images import text as T
from gateway.images.text import CaptionError, apply_caption, validate_texts

ARABIC = "صباح الخير"


def _canvas(tmp_path, name="img.png", size=(320, 320), color=(30, 60, 90)) -> pathlib.Path:
    p = tmp_path / name
    Image.new("RGB", size, color).save(p)
    return p


def _pixels(img: Image.Image):
    """All pixels without Pillow's deprecated getdata()."""
    getter = getattr(img, "get_flattened_data", None) or img.getdata
    return getter()


def test_validate_empty():
    assert validate_texts(None) == []
    assert validate_texts("") == []
    assert validate_texts([]) == []


def test_validate_defaults():
    items = validate_texts({"text": ARABIC})
    assert items == [{"text": ARABIC, "position": "center", "color": None, "font": "naskh"}]


def test_validate_all_fields():
    items = validate_texts(
        [{"text": ARABIC, "position": "bottom-right", "color": "#ffffff", "font": "kufi"}]
    )
    assert items[0]["position"] == "bottom-right"
    assert items[0]["color"] == (255, 255, 255)
    assert items[0]["font"] == "kufi"


@pytest.mark.parametrize("payload", [
    [{"text": "", "position": "center"}],            # empty text
    [{"text": "x", "position": "nowhere"}],           # bad position
    [{"text": "x", "color": "red"}],                  # bad color
    [{"text": "x", "color": "#12345"}],               # short hex
    [{"text": "x", "font": "serif"}],                 # bad font
    [{"text": "y" * 121}],                            # too long
    [{"text": "a"}, {"text": "b"}, {"text": "c"}, {"text": "d"}, {"text": "e"}],  # >4
])
def test_validate_rejects_bad(payload):
    with pytest.raises(CaptionError):
        validate_texts(payload)


def test_shaping_reorders_arabic():
    # RTL: the shaped form must differ from the logical input and be non-empty.
    shaped = T.shape(ARABIC)
    assert shaped
    assert shaped != ARABIC


def test_apply_draws_arabic_ink(tmp_path):
    p = _canvas(tmp_path)
    out = apply_caption(p, validate_texts({"text": ARABIC}))
    assert out == p
    img = Image.open(out).convert("RGB")
    # There must be pixels different from the flat background.
    bg = img.getpixel((0, 0))
    changed = any(
        abs(px[0] - bg[0]) + abs(px[1] - bg[1]) + abs(px[2] - bg[2]) > 60
        for px in _pixels(img)
    )
    assert changed, "no ink was drawn"


def _single(tmp_path, name, font):
    p = _canvas(tmp_path, name)
    apply_caption(p, validate_texts({"text": ARABIC, "position": "center", "font": font}))
    return p.read_bytes()


def test_each_font_is_distinct(tmp_path):
    # The same words in three typefaces must render different pixels: this is
    # what "multiple fonts" promises.
    naskh = _single(tmp_path, "naskh.png", "naskh")
    kufi = _single(tmp_path, "kufi.png", "kufi")
    sans = _single(tmp_path, "sans.png", "sans")
    assert naskh != kufi
    assert naskh != sans
    assert kufi != sans


def test_apply_multiple_items_and_fonts(tmp_path):
    p = _canvas(tmp_path)
    items = validate_texts([
        {"text": ARABIC, "position": "top-left", "font": "naskh"},
        {"text": ARABIC, "position": "bottom-right", "font": "kufi"},
        {"text": "Latin", "position": "center", "font": "sans"},
    ])
    apply_caption(p, items)
    img = Image.open(p).convert("RGB")
    assert img.size == (320, 320)


def test_apply_explicit_color_used(tmp_path):
    bright = _canvas(tmp_path, color=(10, 10, 10))
    apply_caption(bright, validate_texts({"text": "hello", "color": "#ff0000"}))
    img = Image.open(bright).convert("RGB")
    # Some pixel should be strongly red where the text landed.
    reds = [px for px in _pixels(img) if px[0] > 200 and px[1] < 80 and px[2] < 80]
    assert reds, "explicit red color was not applied"


def test_apply_no_items_copies_unmodified(tmp_path):
    src = _canvas(tmp_path, "src.png")
    dest = tmp_path / "dest.png"
    out = apply_caption(src, [], out_path=dest)
    assert out == dest
    assert dest.exists()
    assert list(_pixels(Image.open(src))) == list(_pixels(Image.open(dest)))


def test_apply_overwrites_in_place(tmp_path):
    p = _canvas(tmp_path)
    before = p.read_bytes()
    apply_caption(p, validate_texts({"text": "abc"}))
    assert p.read_bytes() != before


def test_loads_dumps_roundtrip():
    for payload in (
        {"text": ARABIC, "font": "kufi"},
        {"text": ARABIC, "font": "naskh", "color": "#ffe9c7"},
    ):
        items = validate_texts(payload)
        back = T.loads(T.dumps(items))
        assert back == items, payload


def test_loads_empty():
    assert T.loads(None) == []
    assert T.loads("") == []
