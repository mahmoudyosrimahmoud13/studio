"""The Studio web page and its small API. Runs on this computer only (127.0.0.1) unless you choose otherwise.

The slow AI step runs once per photo; every change after that only re-renders (about half a second):
  POST /api/photos                    a photo + job + model -> kept with its AI mask; returns its id and preview
  POST /api/render                    id + the look + this photo's edits (+ touch-up PNG) -> the finished photo
                                      (preview=1: a quick small one for the editor, not saved)
  GET  /api/photos/<job>/<id>/preview.jpg | mask.png   for the editor (and the "before" picture)
  POST /api/logo  /  DELETE /api/logo/<job>          a shop logo stamped on every photo (optional)
  GET  /api/jobs/<job>/<file>  /  /api/jobs/<job>.zip  the results
Everything is kept in jobs/ for 24 hours, then deleted.
"""
import io
import json
import re
import shutil
import threading
import time
import zipfile
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from PIL import Image

from . import pipeline
from .pipeline import WARNINGS, Edits, Logo, Options

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "jobs"
PAGE = Path(__file__).resolve().parent / "static" / "index.html"
MAX_UPLOAD = 30 * 1024 * 1024
JOB_RE = re.compile(r"^[a-f0-9]{16,32}$")
ID_RE = re.compile(r"^[a-f0-9]{12}$")

app = FastAPI(title="Studio", docs_url=None, redoc_url=None, openapi_url=None)

# The last few opened photos stay in memory, so editing one doesn't reopen the file each time.
_cache: "OrderedDict[str, object]" = OrderedDict()
_cache_lock = threading.Lock()
CACHE_SIZE = 12


def _cached(key: str, make):
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    val = make()
    with _cache_lock:
        _cache[key] = val
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return val


def _safe_name(name: str) -> str:
    stem = Path(name or "photo").stem
    stem = re.sub(r"[^\w\-. ]+", "", stem, flags=re.UNICODE).strip(" .")[:80]
    return stem or "photo"


def _job_dir(job: str) -> Path:
    if not JOB_RE.match(job or ""):
        raise HTTPException(400, "job")
    return JOBS / job


def _photo_dir(job: str, pid: str) -> Path:
    if not ID_RE.match(pid or ""):
        raise HTTPException(400, "photo")
    d = _job_dir(job) / "photos" / pid
    if not d.is_dir():
        raise HTTPException(404, "photo")
    return d


def _cleanup() -> None:
    if not JOBS.exists():
        return
    old = time.time() - 24 * 3600
    for d in JOBS.iterdir():
        if d.is_dir() and d.stat().st_mtime < old:
            shutil.rmtree(d, ignore_errors=True)


def _image(job: str, pid: str) -> Image.Image:
    d = _photo_dir(job, pid)
    return _cached(f"{job}/{pid}/img", lambda: pipeline.load((d / "original").read_bytes()))


def _mask(job: str, pid: str, model: str) -> Image.Image:
    model = model if model in pipeline.MODELS else "best"
    d = _photo_dir(job, pid)
    f = d / f"mask-{model}.png"

    def make():
        if f.is_file():
            return Image.open(f).convert("L")
        m = pipeline.mask(_image(job, pid), model)
        m.save(f, compress_level=1)
        return m
    return _cached(f"{job}/{pid}/mask-{model}", make)


@app.get("/", response_class=HTMLResponse)
def page():
    return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@app.get("/api/status")
def status():
    return {"model_ready": pipeline.model_ready("best"), "models": {m: pipeline.model_ready(m) for m in pipeline.MODELS}}


@app.post("/api/photos")
def add_photo(file: UploadFile = File(...), job: str = Form(...), model: str = Form("best")):
    d = _job_dir(job)
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "too_big")
    try:
        img = pipeline.load(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    import secrets
    pid = secrets.token_hex(6)
    pd = d / "photos" / pid
    pd.mkdir(parents=True, exist_ok=True)
    (pd / "original").write_bytes(data)
    (pd / "info.json").write_text(json.dumps({"name": _safe_name(file.filename or ""), "w": img.width, "h": img.height}))
    with _cache_lock:
        _cache[f"{job}/{pid}/img"] = img
    prev = img.convert("RGB")
    prev.thumbnail((1400, 1400), Image.Resampling.BILINEAR)
    prev.save(pd / "preview.jpg", quality=85)
    try:
        _mask(job, pid, model)  # the slow part, done now so every change after is quick
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    _cleanup()
    return {"id": pid, "name": _safe_name(file.filename or ""), "width": img.width, "height": img.height,
            "preview": f"/api/photos/{job}/{pid}/preview.jpg"}


@app.get("/api/photos/{job}/{pid}/preview.jpg")
def preview(job: str, pid: str):
    return FileResponse(_photo_dir(job, pid) / "preview.jpg", media_type="image/jpeg")


@app.get("/api/photos/{job}/{pid}/mask.png")
def mask_preview(job: str, pid: str, model: str = "best"):
    m = _mask(job, pid, model).copy()
    m.thumbnail((1400, 1400), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    m.save(buf, "PNG", compress_level=1)
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


def _logo(job: str, on: bool, position: str, scale: float, opacity: float) -> Logo | None:
    f = _job_dir(job) / "logo.png"
    if not on or not f.is_file():
        return None
    img = _cached(f"{job}/logo", lambda: Image.open(f).convert("RGBA"))
    return Logo(img, position if position in ("br", "bl", "tr", "tl", "center") else "br",
                max(0.04, min(0.5, scale)), max(0.1, min(1.0, opacity)))


@app.post("/api/logo")
def set_logo(job: str = Form(...), file: UploadFile = File(...)):
    d = _job_dir(job)
    data = file.file.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "too_big")
    try:
        lg = pipeline.load(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    d.mkdir(parents=True, exist_ok=True)
    lg.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    lg.save(d / "logo.png")
    with _cache_lock:
        _cache.pop(f"{job}/logo", None)
    return {"ok": True}


@app.delete("/api/logo/{job}")
def remove_logo(job: str):
    (_job_dir(job) / "logo.png").unlink(missing_ok=True)
    with _cache_lock:
        _cache.pop(f"{job}/logo", None)
    return {"ok": True}


@app.post("/api/render")
def render(job: str = Form(...), id: str = Form(...), name: str = Form(""), model: str = Form("best"),
           preview: bool = Form(False), background: str = Form("#FFFFFF"), shape: str = Form("square"),
           size: int = Form(1600), fill: float = Form(0.82), place: str = Form("center"), shadow: str = Form("soft"),
           enhance: bool = Form(True), remove_background: bool = Form(True), fmt: str = Form("webp"),
           quality: int = Form(90), edits: str = Form("{}"), touch: UploadFile | None = File(None),
           logo_on: bool = Form(False), logo_position: str = Form("br"), logo_scale: float = Form(0.18),
           logo_opacity: float = Form(0.85)):
    d = _photo_dir(job, id)
    try:
        ed = json.loads(edits or "{}")
        ed = ed if isinstance(ed, dict) else {}
    except ValueError:
        ed = {}
    keys = {"crop", "rotate", "flip", "straighten", "brightness", "contrast", "saturation", "warmth", "sharpness", "fill"}
    e = Edits(**{k: v for k, v in ed.items() if k in keys})
    if touch is not None:
        t = touch.file.read(8 * 1024 * 1024 + 1)
        e.touch = t if 0 < len(t) <= 8 * 1024 * 1024 else None
    opts = Options(background=background, shape=shape, size=size, fill=fill, place=place, shadow=shadow, enhance=enhance,
                   remove_background=remove_background, fmt=fmt, quality=quality)
    if preview:  # the editor's live view: small, quick, not saved
        opts.size, opts.fmt, opts.quality = min(int(size), 900), "webp" if fmt != "png" else "png", 80
    try:
        img = _image(job, id)
        m = _mask(job, id, model) if opts.remove_background else None
        r = pipeline.render(img, m, opts, e, _logo(job, logo_on, logo_position, logo_scale, logo_opacity))
    except ValueError as err:
        raise HTTPException(400, str(err))
    except RuntimeError as err:
        raise HTTPException(503, str(err))
    if preview:
        return Response(r.data, media_type=f"image/{'webp' if r.ext == 'webp' else 'png'}", headers={"Cache-Control": "no-store"})

    out = _job_dir(job) / "out"
    out.mkdir(parents=True, exist_ok=True)
    meta = d / "info.json"
    info = json.loads(meta.read_text())
    if info.get("out"):
        (out / info["out"]).unlink(missing_ok=True)  # only the newest version of each photo stays
    base = _safe_name(name or info.get("name") or "photo")
    fname, n = f"{base}.{r.ext}", 2
    while (out / fname).exists():
        fname, n = f"{base}-{n}.{r.ext}", n + 1
    (out / fname).write_bytes(r.data)
    info["out"] = fname
    meta.write_text(json.dumps(info))
    return {"name": fname, "url": f"/api/jobs/{job}/{fname}", "width": r.width, "height": r.height, "bytes": len(r.data),
            "warnings": [{"code": w, "text": WARNINGS[w]} for w in r.warnings]}


@app.get("/api/jobs/{job}.zip")
def job_zip(job: str):
    d = _job_dir(job) / "out"
    files = sorted(p for p in d.glob("*") if p.is_file()) if d.exists() else []
    if not files:
        raise HTTPException(404, "empty")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:  # photos are already compressed
        for p in files:
            z.write(p, p.name)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="studio-{time.strftime("%Y%m%d-%H%M")}.zip"'})


@app.get("/api/jobs/{job}/{name}")
def job_file(job: str, name: str):
    d = (_job_dir(job) / "out").resolve()
    p = (d / name).resolve()
    if p.parent != d or not p.is_file():
        raise HTTPException(404, "missing")
    media = {"webp": "image/webp", "jpg": "image/jpeg", "png": "image/png"}.get(p.suffix.lstrip("."), "application/octet-stream")
    return FileResponse(p, media_type=media, headers={"Cache-Control": "no-store"})
