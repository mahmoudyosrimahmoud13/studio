"""The Studio web page and its small API. Runs on this computer only (127.0.0.1) unless you choose otherwise.

  GET  /                         the page
  GET  /api/status               is the background model ready?
  POST /api/process              one photo + the settings -> the cleaned photo is saved in the job's folder
  GET  /api/jobs/<job>/<file>    one result
  GET  /api/jobs/<job>.zip       every result of a job, in one ZIP
Results are kept in jobs/ for 24 hours, then deleted.
"""
import io
import re
import shutil
import time
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from . import pipeline
from .pipeline import WARNINGS, Options

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "jobs"
PAGE = Path(__file__).resolve().parent / "static" / "index.html"
MAX_UPLOAD = 30 * 1024 * 1024
JOB_RE = re.compile(r"^[a-f0-9]{16,32}$")

app = FastAPI(title="Studio", docs_url=None, redoc_url=None, openapi_url=None)


def _safe_name(name: str) -> str:
    stem = Path(name or "photo").stem
    stem = re.sub(r"[^\w\-. ]+", "", stem, flags=re.UNICODE).strip(" .")[:80]
    return stem or "photo"


def _job_dir(job: str) -> Path:
    if not JOB_RE.match(job or ""):
        raise HTTPException(400, "job")
    return JOBS / job


def _cleanup() -> None:
    if not JOBS.exists():
        return
    old = time.time() - 24 * 3600
    for d in JOBS.iterdir():
        if d.is_dir() and d.stat().st_mtime < old:
            shutil.rmtree(d, ignore_errors=True)


@app.get("/", response_class=HTMLResponse)
def page():
    return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@app.get("/api/status")
def status():
    return {"model_ready": pipeline.model_ready()}


@app.post("/api/process")
def process(file: UploadFile = File(...), job: str = Form(...), background: str = Form("#FFFFFF"),
            shape: str = Form("square"), size: int = Form(1600), fill: float = Form(0.82), place: str = Form("center"),
            shadow: str = Form("soft"), enhance: bool = Form(True), remove_background: bool = Form(True),
            fmt: str = Form("webp"), quality: int = Form(90)):
    d = _job_dir(job)
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "too_big")
    opts = Options(background=background, shape=shape, size=size, fill=fill, place=place, shadow=shadow,
                   enhance=enhance, remove_background=remove_background, fmt=fmt, quality=quality)
    try:
        r = pipeline.process(data, opts)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    d.mkdir(parents=True, exist_ok=True)
    base = _safe_name(file.filename or "")
    name, n = f"{base}.{r.ext}", 2
    while (d / name).exists():
        name, n = f"{base}-{n}.{r.ext}", n + 1
    (d / name).write_bytes(r.data)
    _cleanup()
    return {"name": name, "url": f"/api/jobs/{job}/{name}", "width": r.width, "height": r.height, "bytes": len(r.data),
            "warnings": [{"code": w, "text": WARNINGS[w]} for w in r.warnings]}


@app.get("/api/jobs/{job}.zip")
def job_zip(job: str):
    d = _job_dir(job)
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
    d = _job_dir(job)
    p = (d / name).resolve()
    if p.parent != d.resolve() or not p.is_file():
        raise HTTPException(404, "missing")
    media = {"webp": "image/webp", "jpg": "image/jpeg", "png": "image/png"}.get(p.suffix.lstrip("."), "application/octet-stream")
    return FileResponse(p, media_type=media, headers={"Cache-Control": "no-store"})
