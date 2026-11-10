"""Turns an ordinary product photo into a clean catalog photo.

The steps, in order:
1. Open the photo the right way up (phones store rotation separately) and check it (too small, dark, blurry, cut off).
2. Find the product: an on-device AI model (ISNet, 1024 x 1024) gives every pixel a "this is the product" score.
   Nothing leaves the computer.
3. Clean the edges: drop faint specks, and repaint half-transparent edge pixels with the product's own colors, so no
   halo of the old background is left.
4. Fix the light, gently: white balance and brightness measured on the product only, then a light sharpen.
5. Crop to the product, then place it centered (or standing on the floor) on a square or 4:5 canvas with the same
   margin every time, so a whole catalog lines up.
6. Optional soft shadow under it, then the background color (white, light gray, any color, or transparent).

This file has no web or Rubies code in it, so the same functions can be copied into Rubies or Hydro later.
"""
from __future__ import annotations

import io
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

MODEL_DIR = Path(os.getenv("STUDIO_MODEL_DIR", Path(__file__).resolve().parents[1] / "models"))
MODEL_FILE = MODEL_DIR / "isnet-general-use.onnx"
MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx"
MODEL_SHA256 = "60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a"

MAX_INPUT_PIXELS = 60_000_000   # refuse anything bigger (a "decompression bomb" could eat all memory)
WORK_SIDE = 3000                # bigger photos are shrunk to this first: plenty for a 2000 px result, and faster

Image.MAX_IMAGE_PIXELS = MAX_INPUT_PIXELS
try:  # iPhone photos (HEIC): optional add-on, installed by run.sh
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass


@dataclass
class Options:
    background: str = "#FFFFFF"   # "#RRGGBB" or "transparent"
    shape: str = "square"         # square | portrait (4:5, good for clothes) | original
    size: int = 1600              # longest side of the result, in pixels
    fill: float = 0.82            # how much of the canvas the product takes, 0.5 to 0.95
    place: str = "center"         # center | floor (standing items: a little more space above than below)
    shadow: str = "soft"          # none | soft
    enhance: bool = True          # white balance, brightness and a light sharpen
    remove_background: bool = True
    fmt: str = "webp"             # webp | jpg | png
    quality: int = 90

    def clean(self) -> "Options":
        bg = self.background.strip()
        if bg.lower() != "transparent":
            bg = bg if bg.startswith("#") else "#" + bg
            if len(bg) != 7 or any(c not in "0123456789abcdefABCDEF" for c in bg[1:]):
                bg = "#FFFFFF"
        fmt = self.fmt.lower().replace("jpeg", "jpg")
        if fmt not in ("webp", "jpg", "png"):
            fmt = "webp"
        if bg.lower() == "transparent" and fmt == "jpg":
            fmt = "png"  # JPG can't be see-through
        return Options(background=bg, shape=self.shape if self.shape in ("square", "portrait", "original") else "square",
                       size=max(400, min(4000, int(self.size))), fill=max(0.5, min(0.95, float(self.fill))),
                       place=self.place if self.place in ("center", "floor") else "center",
                       shadow=self.shadow if self.shadow in ("none", "soft") else "soft", enhance=bool(self.enhance),
                       remove_background=bool(self.remove_background), fmt=fmt, quality=max(50, min(100, int(self.quality))))


@dataclass
class Result:
    data: bytes
    ext: str
    width: int
    height: int
    warnings: list[str] = field(default_factory=list)  # short codes, see WARNINGS


WARNINGS = {
    "small": "The photo is small, so the result may look soft. Use at least 1000 pixels on the short side.",
    "dark": "The photo is dark. Shoot near a window or with more light.",
    "blurry": "The product looks blurry. Hold the phone still or use a stand, and tap to focus.",
    "cut": "The product touches the edge of the photo, so part of it may be cut off. Leave space around it.",
    "unclear": "The product wasn't found clearly. Use a plain background that is a different color from the product.",
}


# ---------------------------------------------------------------- the AI model

_session = None
_lock = threading.Lock()


def model_ready() -> bool:
    return MODEL_FILE.is_file() and MODEL_FILE.stat().st_size > 100_000_000


def _get_session():
    global _session
    with _lock:
        if _session is None:
            if not model_ready():
                raise RuntimeError(f"The background model is missing: {MODEL_FILE}. Run ./run.sh once to download it.")
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.log_severity_level = 3
            _session = ort.InferenceSession(str(MODEL_FILE), sess_options=so, providers=["CPUExecutionProvider"])
        return _session


def product_mask(img: Image.Image) -> Image.Image:
    """A grayscale picture the size of `img`: white = product, black = background (ISNet, as used by rembg)."""
    sess = _get_session()
    small = img.convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)
    x = np.asarray(small, dtype=np.float32)
    x = x / max(float(x.max()), 1e-6)
    x = x - np.array([0.485, 0.456, 0.406], dtype=np.float32)
    x = x.transpose(2, 0, 1)[None]
    with _lock:  # one at a time: the model already uses every CPU core
        out = sess.run(None, {sess.get_inputs()[0].name: x})[0][:, 0, :, :]
    lo, hi = float(out.min()), float(out.max())
    pred = (out - lo) / max(hi - lo, 1e-6)
    m = Image.fromarray((pred[0] * 255).clip(0, 255).astype(np.uint8), "L")
    return m.resize(img.size, Image.Resampling.LANCZOS)


# ---------------------------------------------------------------- small helpers

def _hex(c: str) -> tuple[int, int, int]:
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def _blurriness(gray: np.ndarray, mask: np.ndarray) -> float:
    """Variance of the Laplacian on the product: low means blurry."""
    g = gray.astype(np.float32)
    lap = -4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    sel = mask[1:-1, 1:-1] > 0.5
    vals = lap[sel] if sel.sum() > 500 else lap.ravel()
    return float(vals.var())


def _clean_alpha(alpha: np.ndarray) -> np.ndarray:
    """0..1 alpha: faint specks off, nearly solid areas fully solid, a slightly firmer edge."""
    a = alpha.copy()
    a[a < 0.08] = 0.0
    a[a > 0.94] = 1.0
    mid = (a > 0) & (a < 1)
    a[mid] = np.clip((a[mid] - 0.08) / 0.86, 0, 1) ** 0.9
    return a


def _keep_main_parts(alpha: np.ndarray) -> np.ndarray:
    """Drops loose bits the model picked up away from the product (a table edge, a speck of the floor): only pieces
    at least 3% the size of the biggest piece stay. Works on a small copy (longest side 256), so it is quick."""
    h, w = alpha.shape
    k = max(1, max(h, w) // 256)
    small = (alpha[::k, ::k] > 0.35)
    sh, sw = small.shape
    labels = np.zeros((sh, sw), dtype=np.int32)
    sizes = [0]
    cur = 0
    for y0 in range(sh):
        row = small[y0]
        for x0 in np.nonzero(row & (labels[y0] == 0))[0]:
            if labels[y0, x0]:
                continue
            cur += 1
            stack = [(y0, x0)]
            labels[y0, x0] = cur
            n = 0
            while stack:
                y, x = stack.pop()
                n += 1
                for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                    if 0 <= yy < sh and 0 <= xx < sw and small[yy, xx] and not labels[yy, xx]:
                        labels[yy, xx] = cur
                        stack.append((yy, xx))
            sizes.append(n)
    if cur <= 1:
        return alpha
    big = max(sizes)
    keep = np.array([s >= big * 0.03 for s in sizes])
    keep[0] = False
    kept = keep[labels]
    # grow the kept area a little so soft edges next to it stay, then back to full size
    km = Image.fromarray((kept * 255).astype(np.uint8), "L").filter(ImageFilter.MaxFilter(5))
    km = np.asarray(km.resize((w, h), Image.Resampling.NEAREST), dtype=np.float32) / 255.0
    return alpha * km


def _decontaminate(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Half-transparent edge pixels still carry the old background's color (a halo). Replace their color with the
    average color of the product nearby, weighted by how solid each neighbour is."""
    edge = (alpha > 0) & (alpha < 0.96)
    if not edge.any():
        return rgb
    a3 = alpha[..., None]
    prem = Image.fromarray(np.clip(rgb * a3, 0, 255).astype(np.uint8))
    am = Image.fromarray((alpha * 255).astype(np.uint8), "L")
    r = max(2, int(min(alpha.shape) / 300))
    pb = np.asarray(prem.filter(ImageFilter.BoxBlur(r)), dtype=np.float32)
    ab = np.asarray(am.filter(ImageFilter.BoxBlur(r)), dtype=np.float32)[..., None] / 255.0
    est = np.where(ab > 0.02, pb / np.maximum(ab, 0.02), rgb)
    out = rgb.copy()
    out[edge] = est[edge]
    return out


def _enhance(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Gentle fixes measured on the product only: white balance (gray world, half strength), brightness and contrast
    (stretch the 1st to 99th percentile toward the full range, but not more than 1.35x)."""
    sel = alpha > 0.6
    px = rgb[sel] if sel.sum() > 1000 else rgb.reshape(-1, 3)
    means = px.mean(axis=0)
    gray = means.mean()
    gains = 1 + 0.5 * (gray / np.maximum(means, 1) - 1)
    out = rgb * gains
    lum = (out[sel] if sel.sum() > 1000 else out.reshape(-1, 3)) @ np.array([0.299, 0.587, 0.114])
    lo, hi = np.percentile(lum, 1), np.percentile(lum, 99)
    span = max(hi - lo, 1.0)
    scale = min(240.0 / span, 1.35)
    if scale > 1.02 or lo > 12:
        out = (out - lo * 0.6) * scale + 6
    return np.clip(out, 0, 255)


# ---------------------------------------------------------------- the whole job

def process(data: bytes, opts: Options | None = None) -> Result:
    opts = (opts or Options()).clean()
    warns: list[str] = []
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img.load()
    except Image.DecompressionBombError:
        raise ValueError("too_big")
    except Exception:
        raise ValueError("not_image")
    if img.width * img.height > MAX_INPUT_PIXELS:
        raise ValueError("too_big")
    if min(img.size) < 1000:
        warns.append("small")
    had_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA")
    if max(img.size) > WORK_SIDE:
        img.thumbnail((WORK_SIDE, WORK_SIDE), Image.Resampling.LANCZOS)

    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    if opts.remove_background:
        alpha = np.asarray(product_mask(img), dtype=np.float32) / 255.0
        if had_alpha:  # already cut out: keep the stricter of the two
            alpha = np.minimum(alpha, np.asarray(img.getchannel("A"), dtype=np.float32) / 255.0)
        alpha = _keep_main_parts(_clean_alpha(alpha))
    else:
        alpha = np.asarray(img.getchannel("A"), dtype=np.float32) / 255.0

    solid = alpha > 0.5
    share = float(solid.mean())
    if opts.remove_background and (share < 0.01 or share > 0.97):
        warns.append("unclear")
    gray = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    if gray.mean() < 70:  # the whole photo: a black shirt on a light table is fine
        warns.append("dark")
    if _blurriness(gray, alpha) < 40:
        warns.append("blurry")
    if opts.remove_background and share < 0.97:
        if max(solid[0].mean(), solid[-1].mean(), solid[:, 0].mean(), solid[:, -1].mean()) > 0.03:  # any one side
            warns.append("cut")

    if opts.remove_background:
        rgb = _decontaminate(rgb, alpha)
    if opts.enhance:
        rgb = _enhance(rgb, alpha)

    product = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")
    product.putalpha(Image.fromarray((alpha * 255).astype(np.uint8), "L"))
    if opts.enhance:
        a = product.getchannel("A")
        product = product.convert("RGB").filter(ImageFilter.UnsharpMask(radius=1.6, percent=55, threshold=3))
        product.putalpha(a)

    box = product.getchannel("A").point(lambda v: 255 if v > 12 else 0).getbbox()
    if box:
        product = product.crop(box)

    # the canvas
    if opts.shape == "square":
        cw = ch = opts.size
    elif opts.shape == "portrait":
        ch, cw = opts.size, int(opts.size * 4 / 5)
    else:
        ratio = img.width / img.height
        cw, ch = (opts.size, round(opts.size / ratio)) if ratio >= 1 else (round(opts.size * ratio), opts.size)
    scale = min(cw * opts.fill / product.width, ch * opts.fill / product.height)
    nw, nh = max(1, round(product.width * scale)), max(1, round(product.height * scale))
    product = product.resize((nw, nh), Image.Resampling.LANCZOS)
    x = (cw - nw) // 2
    y = (ch - nh) // 2 if opts.place == "center" else round(ch - nh - (ch - nh) * 0.38)

    transparent = opts.background.lower() == "transparent"
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0) if transparent else (*_hex(opts.background), 255))
    if opts.shadow == "soft":
        canvas.alpha_composite(_shadow(product, cw, ch, x, y, transparent))
    canvas.alpha_composite(product, (x, y))

    out = io.BytesIO()
    if opts.fmt == "jpg":
        canvas.convert("RGB").save(out, "JPEG", quality=opts.quality, optimize=True, progressive=True)
    elif opts.fmt == "png":
        (canvas if transparent else canvas.convert("RGB")).save(out, "PNG", optimize=True)
    else:
        (canvas if transparent else canvas.convert("RGB")).save(out, "WEBP", quality=opts.quality, method=5)
    return Result(out.getvalue(), opts.fmt, cw, ch, list(dict.fromkeys(warns)))


def _shadow(product: Image.Image, cw: int, ch: int, x: int, y: int, transparent: bool) -> Image.Image:
    """A soft contact shadow: a flattened, blurred copy of the product's outline just under its base."""
    layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    a = product.getchannel("A")
    h = max(4, product.height // 9)
    flat = a.resize((product.width, h), Image.Resampling.BILINEAR)
    sh = Image.new("RGBA", flat.size, (0, 0, 0, 0))
    sh.putalpha(flat.point(lambda v: int(v * (0.30 if not transparent else 0.22))))
    layer.alpha_composite(sh, (x, min(ch - h, y + product.height - h // 2)))
    return layer.filter(ImageFilter.GaussianBlur(max(6, product.width // 28)))
