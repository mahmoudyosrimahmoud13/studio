"""Turns an ordinary product photo into a clean catalog photo.

Three steps, so changing the look never waits for the slow part:
1. load()   open the photo the right way up (phones store rotation separately), shrink very big ones.
2. mask()   find the product: an on-device AI model gives every pixel a "this is the product" score. This is the slow
            part (about 3 s with "best", 0.3 s with "fast"), so the web page keeps the result and reuses it.
            Nothing leaves the computer.
3. render() everything else, in well under a second: your touch-ups (keep / remove brush), crop, rotate, flip,
            straighten; clean edges with no halo; gentle light fix plus your sliders; crop to the product and place it
            the same way on every canvas; soft shadow, background, optional logo; then the file.

process() does all three for one photo (used by the terminal cleaner and the tests). This file has no web or Rubies
code in it, so it can be copied into Rubies or Hydro later.
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
# Both come from the rembg project's releases. "best" = ISNet general use, "fast" = U2Net-P (4.5 MB, about 6x faster,
# rougher edges). run.sh downloads them and checks the fingerprints, so a broken or swapped file is never used.
MODELS = {
    "best": {"file": "isnet-general-use.onnx", "side": 1024, "std": (1.0, 1.0, 1.0), "min_bytes": 100_000_000,
             "url": "https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx",
             "sha256": "60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a"},
    "fast": {"file": "u2netp.onnx", "side": 320, "std": (0.229, 0.224, 0.225), "min_bytes": 4_000_000,
             "url": "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx",
             "sha256": "309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8"},
}
MEAN = (0.485, 0.456, 0.406)

MAX_INPUT_PIXELS = 60_000_000   # refuse anything bigger (a "decompression bomb" could eat all memory)
WORK_SIDE = 3000                # bigger photos are shrunk to this when opened

Image.MAX_IMAGE_PIXELS = MAX_INPUT_PIXELS
try:  # iPhone photos (HEIC): optional add-on, installed by run.sh
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass


@dataclass
class Options:
    """The look, the same for every photo in a batch."""
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
        bg = (self.background or "").strip()
        if bg.lower() != "transparent":
            bg = bg if bg.startswith("#") else "#" + bg
            if len(bg) != 7 or any(c not in "0123456789abcdefABCDEF" for c in bg[1:]):
                bg = "#FFFFFF"
        fmt = (self.fmt or "").lower().replace("jpeg", "jpg")
        if fmt not in ("webp", "jpg", "png"):
            fmt = "webp"
        if bg.lower() == "transparent" and fmt == "jpg":
            fmt = "png"  # JPG can't be see-through
        return Options(background=bg, shape=self.shape if self.shape in ("square", "portrait", "original") else "square",
                       size=max(400, min(4000, int(self.size))), fill=max(0.5, min(0.95, float(self.fill))),
                       place=self.place if self.place in ("center", "floor") else "center",
                       shadow=self.shadow if self.shadow in ("none", "soft") else "soft", enhance=bool(self.enhance),
                       remove_background=bool(self.remove_background), fmt=fmt, quality=max(50, min(100, int(self.quality))))


def _f(v, lo=-1.0, hi=1.0) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return 0.0


@dataclass
class Edits:
    """Changes for one photo, made in the editor. Crop and touch-ups are drawn on the photo as it was opened."""
    crop: tuple[float, float, float, float] | None = None  # x, y, width, height as parts of the photo (0 to 1)
    rotate: int = 0              # 0, 90, 180 or 270, clockwise
    flip: bool = False           # mirror left to right
    straighten: float = 0.0      # degrees, -20 to 20
    brightness: float = 0.0      # -1 to 1 (0 = as is)
    contrast: float = 0.0
    saturation: float = 0.0
    warmth: float = 0.0          # minus = cooler (blue), plus = warmer (yellow)
    sharpness: float = 0.0
    fill: float | None = None    # this photo's own product size, instead of the batch one
    touch: bytes | None = None   # PNG: green = always keep, red = always remove (any size, stretched to the photo)

    def clean(self) -> "Edits":
        crop = None
        if self.crop:
            try:
                x, y, w, h = (max(0.0, min(1.0, float(v))) for v in self.crop)
                w, h = min(w, 1 - x), min(h, 1 - y)
                crop = (x, y, w, h) if w > 0.02 and h > 0.02 else None
            except (TypeError, ValueError):
                crop = None
        return Edits(crop=crop, rotate=int(self.rotate or 0) % 360 // 90 * 90, flip=bool(self.flip),
                     straighten=_f(self.straighten, -20, 20), brightness=_f(self.brightness), contrast=_f(self.contrast),
                     saturation=_f(self.saturation), warmth=_f(self.warmth), sharpness=_f(self.sharpness),
                     fill=None if self.fill in (None, "") else _f(self.fill, 0.5, 0.95), touch=self.touch or None)


@dataclass
class Logo:
    """A shop logo stamped on every photo (optional)."""
    image: Image.Image
    position: str = "br"         # br | bl | tr | tl | center
    scale: float = 0.18          # logo width as a part of the canvas width
    opacity: float = 0.85


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


# ---------------------------------------------------------------- 1. opening a photo

def load(data: bytes) -> Image.Image:
    """The photo as RGBA, the right way up, at most WORK_SIDE on its longest side. ValueError too_big / not_image."""
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
    img = img.convert("RGBA")
    if max(img.size) > WORK_SIDE:
        img.thumbnail((WORK_SIDE, WORK_SIDE), Image.Resampling.LANCZOS)
    return img


# ---------------------------------------------------------------- 2. the AI model

_sessions: dict = {}
_lock = threading.Lock()      # loading
_run_lock = threading.Lock()  # one photo at a time: each run already uses every CPU core


def model_path(model: str = "best") -> Path:
    return MODEL_DIR / MODELS[model]["file"]


def model_ready(model: str = "best") -> bool:
    p = model_path(model)
    return p.is_file() and p.stat().st_size > MODELS[model]["min_bytes"]


def _get_session(model: str):
    with _lock:
        if model not in _sessions:
            if not model_ready(model):
                raise RuntimeError(f"The {model} background model is missing: {model_path(model)}. Run ./run.sh once to download it.")
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.log_severity_level = 3
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            so.intra_op_num_threads = os.cpu_count() or 1
            _sessions[model] = ort.InferenceSession(str(model_path(model)), sess_options=so, providers=["CPUExecutionProvider"])
        return _sessions[model]


def mask(img: Image.Image, model: str = "best") -> Image.Image:
    """A grayscale picture the size of `img`: white = product, black = background."""
    model = model if model in MODELS else "best"
    m = MODELS[model]
    sess = _get_session(model)
    small = img.convert("RGB").resize((m["side"], m["side"]), Image.Resampling.BILINEAR)
    x = np.asarray(small, dtype=np.float32)
    x = x / max(float(x.max()), 1e-6)
    x = (x - np.array(MEAN, dtype=np.float32)) / np.array(m["std"], dtype=np.float32)
    x = x.transpose(2, 0, 1)[None].astype(np.float32)
    with _run_lock:
        out = sess.run(None, {sess.get_inputs()[0].name: x})[0][:, 0, :, :]
    lo, hi = float(out.min()), float(out.max())
    pred = (out - lo) / max(hi - lo, 1e-6)
    mm = Image.fromarray((pred[0] * 255).clip(0, 255).astype(np.uint8), "L")
    out_mask = mm.resize(img.size, Image.Resampling.BILINEAR)
    if img.getextrema()[3][0] < 255:  # already cut out: keep the stricter of the two
        out_mask = Image.fromarray(np.minimum(np.asarray(out_mask), np.asarray(img.getchannel("A"))), "L")
    return out_mask


product_mask = mask  # the older name


# ---------------------------------------------------------------- small helpers

def _hex(c: str) -> tuple[int, int, int]:
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def _blurriness(gray: np.ndarray, mask: np.ndarray) -> float:
    """How sharp the product's outline is: variance of the Laplacian on a band around the edge (a sharp photo has
    crisp edges even when the product itself is plain). Falls back to the whole photo when no outline is found."""
    g = gray.astype(np.float32)
    lap = -4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    m = Image.fromarray(((mask > 0.5) * 255).astype(np.uint8), "L")
    band = (np.asarray(m.filter(ImageFilter.MaxFilter(7)), dtype=np.int16) - np.asarray(m.filter(ImageFilter.MinFilter(7)), dtype=np.int16)) > 0
    sel = band[1:-1, 1:-1]
    vals = lap[sel] if sel.sum() > 300 else lap.ravel()
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


# ---------------------------------------------------------------- 3. the look

def _geometry(img: Image.Image, aux: Image.Image, e: Edits) -> tuple[Image.Image, Image.Image]:
    """Crop, then rotate, flip and straighten, the same way on the photo and on its mask and touch-ups."""
    if e.crop:
        x, y, w, h = e.crop
        box = (round(x * img.width), round(y * img.height), round((x + w) * img.width), round((y + h) * img.height))
        img, aux = img.crop(box), aux.crop(box)
    turn = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}.get(e.rotate)
    if turn:
        img, aux = img.transpose(turn), aux.transpose(turn)
    if e.flip:
        img, aux = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT), aux.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if abs(e.straighten) > 0.05:
        img = img.rotate(-e.straighten, Image.Resampling.BICUBIC, expand=True, fillcolor=(0, 0, 0, 0))
        aux = aux.rotate(-e.straighten, Image.Resampling.BILINEAR, expand=True, fillcolor=(0, 0, 0))
    return img, aux


def _adjust(rgb: np.ndarray, e: Edits) -> np.ndarray:
    """The editor's sliders. Each one is gentle at its ends, so nothing looks fake."""
    out = rgb
    if e.warmth:
        out = out * np.array([1 + 0.12 * e.warmth, 1 + 0.02 * e.warmth, 1 - 0.12 * e.warmth], dtype=np.float32)
    if e.brightness:
        out = out * (1 + 0.45 * e.brightness)
    if e.contrast:
        out = (out - 128) * (1 + 0.6 * e.contrast) + 128
    if e.saturation:
        lum = (out @ np.array([0.299, 0.587, 0.114], dtype=np.float32))[..., None]
        out = lum + (out - lum) * (1 + e.saturation)
    return np.clip(out, 0, 255)


def _encode(canvas: Image.Image, fmt: str, quality: int, transparent: bool) -> bytes:
    out = io.BytesIO()
    if fmt == "jpg":
        canvas.convert("RGB").save(out, "JPEG", quality=quality, optimize=True, progressive=True)
    elif fmt == "png":
        (canvas if transparent else canvas.convert("RGB")).save(out, "PNG", compress_level=6)
    else:
        (canvas if transparent else canvas.convert("RGB")).save(out, "WEBP", quality=quality, method=4)
    return out.getvalue()


def _stamp(canvas: Image.Image, logo: Logo) -> None:
    lg = logo.image.convert("RGBA")
    w = max(16, round(canvas.width * max(0.04, min(0.5, logo.scale))))
    lg = lg.resize((w, max(1, round(lg.height * w / lg.width))), Image.Resampling.LANCZOS)
    if logo.opacity < 1:
        lg.putalpha(lg.getchannel("A").point(lambda v: int(v * max(0.1, logo.opacity))))
    pad = round(canvas.width * 0.035)
    x = {"bl": pad, "tl": pad, "center": (canvas.width - lg.width) // 2}.get(logo.position, canvas.width - lg.width - pad)
    y = {"tl": pad, "tr": pad, "center": (canvas.height - lg.height) // 2}.get(logo.position, canvas.height - lg.height - pad)
    canvas.alpha_composite(lg, (x, y))


def render(img: Image.Image, product_mask_img: Image.Image | None, opts: Options | None = None, edits: Edits | None = None,
           logo: Logo | None = None) -> Result:
    """Everything after the AI step. `product_mask_img` is mask(img) (None when not removing the background)."""
    opts = (opts or Options()).clean()
    e = (edits or Edits()).clean()
    fill = e.fill if e.fill is not None else opts.fill
    warns: list[str] = []

    # the mask and the touch-ups travel together through crop and rotate: R = mask, G = keep, B = remove
    base = product_mask_img if (opts.remove_background and product_mask_img is not None) else img.getchannel("A")
    keep = Image.new("L", img.size, 0)
    remove = Image.new("L", img.size, 0)
    if e.touch:
        try:
            t = Image.open(io.BytesIO(e.touch)).convert("RGBA").resize(img.size, Image.Resampling.BILINEAR)
            r, g, _b, a = t.split()
            keep = Image.fromarray(np.minimum(np.asarray(g), np.asarray(a)), "L")
            remove = Image.fromarray(np.minimum(np.asarray(r), np.asarray(a)), "L")
        except Exception:
            pass
    aux = Image.merge("RGB", (base, keep, remove))
    img, aux = _geometry(img, aux, e)
    if min(img.size) < 1000:
        warns.append("small")

    # checks, on a small copy (quick)
    chk = img.convert("RGB")
    chk.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
    cgray = np.asarray(chk.convert("L"), dtype=np.float32)
    caux = np.asarray(aux.resize(chk.size, Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
    calpha = np.clip(np.maximum(caux[..., 0], caux[..., 1]) * (1 - caux[..., 2]), 0, 1)
    solid = calpha > 0.5
    share = float(solid.mean())
    if opts.remove_background and (share < 0.01 or share > 0.97):
        warns.append("unclear")
    if cgray.mean() < 70:  # the whole photo: a black shirt on a light table is fine
        warns.append("dark")
    if _blurriness(cgray, calpha) < 40:
        warns.append("blurry")
    if opts.remove_background and share < 0.97:
        if max(solid[0].mean(), solid[-1].mean(), solid[:, 0].mean(), solid[:, -1].mean()) > 0.03:  # any one side
            warns.append("cut")

    # work only on the product, at the size it will be shown: much faster than the whole big photo
    a8 = np.asarray(aux, dtype=np.uint8)
    rough = np.maximum(a8[..., 0], a8[..., 1]) > 70
    rough &= a8[..., 2] < 128
    ys, xs = np.nonzero(rough)
    if opts.remove_background and len(xs) > 50:
        pad = round(0.03 * max(img.size))
        box = (max(0, xs.min() - pad), max(0, ys.min() - pad), min(img.width, xs.max() + pad + 1), min(img.height, ys.max() + pad + 1))
        img, aux = img.crop(box), aux.crop(box)
    target = max(400, round(opts.size * fill * 1.15))
    if max(img.size) > target:
        f = target / max(img.size)
        ns = (max(1, round(img.width * f)), max(1, round(img.height * f)))
        img, aux = img.resize(ns, Image.Resampling.LANCZOS), aux.resize(ns, Image.Resampling.BILINEAR)

    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    ax = np.asarray(aux, dtype=np.float32) / 255.0
    if opts.remove_background:
        alpha = _keep_main_parts(_clean_alpha(ax[..., 0]))
    else:
        alpha = ax[..., 0]
    alpha = np.clip(np.maximum(alpha, ax[..., 1]) * (1 - ax[..., 2]), 0, 1)  # the brush always wins

    if opts.remove_background:
        rgb = _decontaminate(rgb, alpha)
    if opts.enhance:
        rgb = _enhance(rgb, alpha)
    rgb = _adjust(rgb, e)

    product = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")
    sharp = (55 if opts.enhance else 0) + max(0.0, e.sharpness) * 120
    if sharp > 0:
        product = product.filter(ImageFilter.UnsharpMask(radius=1.6, percent=round(sharp), threshold=3))
    elif e.sharpness < 0:
        product = product.filter(ImageFilter.GaussianBlur(-e.sharpness * 1.5))
    product.putalpha(Image.fromarray((alpha * 255).astype(np.uint8), "L"))

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
    scale = min(cw * fill / product.width, ch * fill / product.height)
    nw, nh = max(1, round(product.width * scale)), max(1, round(product.height * scale))
    product = product.resize((nw, nh), Image.Resampling.LANCZOS)
    x = (cw - nw) // 2
    y = (ch - nh) // 2 if opts.place == "center" else round(ch - nh - (ch - nh) * 0.38)

    transparent = opts.background.lower() == "transparent"
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0) if transparent else (*_hex(opts.background), 255))
    if opts.shadow == "soft":
        canvas.alpha_composite(_shadow(product, cw, ch, x, y, transparent))
    canvas.alpha_composite(product, (x, y))
    if logo is not None:
        _stamp(canvas, logo)
    return Result(_encode(canvas, opts.fmt, opts.quality, transparent), opts.fmt, cw, ch, list(dict.fromkeys(warns)))


def process(data: bytes, opts: Options | None = None, edits: Edits | None = None, model: str = "best",
            logo: Logo | None = None) -> Result:
    """All three steps for one photo."""
    opts = (opts or Options()).clean()
    img = load(data)
    m = mask(img, model) if opts.remove_background else None
    return render(img, m, opts, edits, logo)


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
