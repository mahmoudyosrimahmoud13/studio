"""Run with: .venv/bin/python -m pytest -q  (needs the model: ./run.sh downloads it)."""
import io

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from studio.pipeline import Options, model_ready, process

needs_model = pytest.mark.skipif(not model_ready(), reason="background model not downloaded")


def photo(w=1600, h=1200, dark=False, blur=0, cut=False) -> bytes:
    """A red mug-like product on a noisy, warm-tinted table."""
    rng = np.random.default_rng(1)
    bg = (rng.normal(120 if not dark else 35, 18, (h, w, 3)) + [30, 10, -20]).clip(0, 255).astype(np.uint8)
    im = Image.fromarray(bg)
    d = ImageDraw.Draw(im)
    x0 = w - 300 if cut else w // 2 - 260
    d.rounded_rectangle([x0, h // 2 - 300, x0 + 520, h // 2 + 300], 60, fill=(190, 30, 40) if not dark else (90, 15, 20))
    d.ellipse([x0 + 470, h // 2 - 120, x0 + 680, h // 2 + 120], outline=(190, 30, 40), width=40)
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90)
    return out.getvalue()


@needs_model
def test_clean_white_square_and_no_warnings():
    r = process(photo(), Options(fmt="png"))
    im = Image.open(io.BytesIO(r.data)).convert("RGB")
    assert (r.width, r.height) == (1600, 1600) and r.warnings == []
    a = np.asarray(im).astype(int)
    assert (a[5, 5] >= 250).all() and (a[-5, -5] >= 250).all()  # corners are white
    assert a[800, 800, 0] > 120 and a[800, 800, 1] < 90  # the product (red) is in the middle


@needs_model
def test_transparent_portrait_and_jpg_is_never_transparent():
    r = process(photo(), Options(background="transparent", shape="portrait", fmt="webp"))
    im = Image.open(io.BytesIO(r.data))
    assert im.mode == "RGBA" and (r.width, r.height) == (1280, 1600) and im.getpixel((3, 3))[3] == 0
    assert process(photo(), Options(background="transparent", fmt="jpg")).ext == "png"


@needs_model
def test_warnings_dark_blurry_small_cut():
    assert "dark" in process(photo(dark=True)).warnings
    assert "blurry" in process(photo(blur=6)).warnings
    assert "small" in process(photo(800, 600)).warnings
    assert "cut" in process(photo(cut=True)).warnings


def test_bad_input_and_settings_are_cleaned():
    with pytest.raises(ValueError, match="not_image"):
        process(b"hello")
    o = Options(background="red", shape="weird", size=99999, fill=5, fmt="gif").clean()
    assert (o.background, o.shape, o.size, o.fill, o.fmt) == ("#FFFFFF", "square", 4000, 0.95, "webp")


def test_without_background_removal_needs_no_model():
    r = process(photo(), Options(remove_background=False, shadow="none", fmt="jpg"))
    assert r.ext == "jpg" and r.width == 1600
