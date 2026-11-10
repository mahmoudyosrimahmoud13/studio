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


# ---------------------------------------------------------------- the editor's tools

from studio.pipeline import Edits, Logo, load, mask, render  # noqa: E402

fast_ready = pytest.mark.skipif(not model_ready("fast"), reason="fast model not downloaded")


@needs_model
def test_mask_once_render_many_with_edits():
    img = load(photo())
    m = mask(img)
    plain = render(img, m)
    rotated = render(img, m, edits=Edits(rotate=90))
    assert plain.width == rotated.width == 1600  # same canvas
    assert plain.data != rotated.data
    # brightness and warmth change the colors, a crop to the left half keeps only part of the product
    warm = Image.open(io.BytesIO(render(img, m, Options(fmt="png"), Edits(warmth=1, brightness=0.4)).data)).convert("RGB")
    cool = Image.open(io.BytesIO(render(img, m, Options(fmt="png"), Edits(warmth=-1)).data)).convert("RGB")
    assert np.asarray(warm)[800, 800, 0] >= np.asarray(cool)[800, 800, 0]
    cropped = render(img, m, edits=Edits(crop=(0.0, 0.0, 0.5, 1.0)))
    assert "cut" in cropped.warnings  # the crop goes through the mug


@needs_model
def test_touch_up_remove_and_keep():
    img = load(photo())
    m = mask(img)
    # paint "remove" over the whole photo: nothing left but the background
    t = Image.new("RGBA", (40, 30), (255, 0, 0, 255))
    buf = io.BytesIO(); t.save(buf, "PNG")
    r = render(img, m, Options(fmt="png", shadow="none"), Edits(touch=buf.getvalue()))
    a = np.asarray(Image.open(io.BytesIO(r.data)).convert("RGB")).astype(int)
    assert (a > 245).all()
    # paint "keep" over a corner of the table: it stays, so the product box grows
    t = Image.new("RGBA", (40, 30), (0, 0, 0, 0))
    ImageDraw.Draw(t).rectangle([0, 0, 4, 4], fill=(0, 255, 0, 255))
    buf = io.BytesIO(); t.save(buf, "PNG")
    kept = render(img, m, Options(fmt="png", shadow="none"), Edits(touch=buf.getvalue()))
    plain = render(img, m, Options(fmt="png", shadow="none"))

    def red_center(res):
        a = np.asarray(Image.open(io.BytesIO(res.data)).convert("RGB")).astype(int)
        ys, xs = np.nonzero((a[..., 0] > 140) & (a[..., 1] < 90))
        return xs.mean(), ys.mean()
    (kx, ky), (px, py) = red_center(kept), red_center(plain)
    assert kx > px + 20 and ky > py + 20  # the kept table corner (top left) pushes the mug down and right


@needs_model
def test_logo_and_own_size():
    img = load(photo())
    m = mask(img)
    lg = Image.new("RGBA", (100, 40), (20, 20, 160, 255))
    r = render(img, m, Options(fmt="png", shadow="none"), logo=Logo(lg, "br", 0.2, 1.0))
    a = np.asarray(Image.open(io.BytesIO(r.data)).convert("RGB")).astype(int)
    assert a[1600 - 70, 1600 - 100, 2] > 120 and a[1600 - 70, 1600 - 100, 0] < 60  # blue logo bottom right
    small = render(img, m, Options(fmt="png", shadow="none"), Edits(fill=0.5))
    s = np.asarray(Image.open(io.BytesIO(small.data)).convert("RGB")).astype(int)
    assert (s[800, 380] > 245).all()  # with a smaller product, this spot is background


def test_edits_are_cleaned():
    e = Edits(crop=(-1, 0.5, 5, 0.3), rotate=450, straighten=99, brightness=7, fill="0.1").clean()
    assert e.crop == (0.0, 0.5, 1.0, 0.3) and e.rotate == 90 and e.straighten == 20 and e.brightness == 1 and e.fill == 0.5
    assert Edits(crop=(0.5, 0.5, 0.001, 0.4)).clean().crop is None


@fast_ready
def test_fast_model_is_faster_and_still_works():
    import time
    img = load(photo())
    mask(img, "fast"); mask(img, "best")  # warm up both
    t = time.time(); mf = mask(img, "fast"); fast = time.time() - t
    t = time.time(); mask(img, "best"); best = time.time() - t
    assert fast < best
    r = render(img, mf, Options(fmt="png"))
    a = np.asarray(Image.open(io.BytesIO(r.data)).convert("RGB")).astype(int)
    assert (a[5, 5] >= 250).all() and a[800, 800, 0] > 120
