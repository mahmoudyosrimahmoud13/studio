"""Clean a whole folder of photos from the terminal, without the web page.

  python -m studio.cli <photos folder> <results folder> [--background "#FFFFFF"|transparent] [--shape square|portrait|original]
                       [--size 1600] [--fill 0.82] [--place center|floor] [--shadow soft|none] [--no-enhance] [--format webp|jpg|png]
"""
import argparse
import sys
import time
from pathlib import Path

from .pipeline import WARNINGS, Options, process

PHOTOS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp", ".tif", ".tiff"}


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m studio.cli", description="Clean product photos in bulk.")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--background", default="#FFFFFF")
    ap.add_argument("--shape", default="square", choices=["square", "portrait", "original"])
    ap.add_argument("--size", type=int, default=1600)
    ap.add_argument("--fill", type=float, default=0.82)
    ap.add_argument("--place", default="center", choices=["center", "floor"])
    ap.add_argument("--shadow", default="soft", choices=["soft", "none"])
    ap.add_argument("--no-enhance", action="store_true")
    ap.add_argument("--format", default="webp", choices=["webp", "jpg", "png"])
    a = ap.parse_args()
    src, dst = Path(a.src), Path(a.dst)
    files = sorted(p for p in src.rglob("*") if p.suffix.lower() in PHOTOS) if src.is_dir() else []
    if not files:
        sys.exit(f"No photos found in {src}")
    dst.mkdir(parents=True, exist_ok=True)
    opts = Options(background=a.background, shape=a.shape, size=a.size, fill=a.fill, place=a.place, shadow=a.shadow,
                   enhance=not a.no_enhance, fmt=a.format)
    t0, bad = time.time(), 0
    for i, f in enumerate(files, 1):
        try:
            r = process(f.read_bytes(), opts)
        except ValueError as e:
            bad += 1
            print(f"[{i}/{len(files)}] {f.name}: skipped ({e})")
            continue
        out = dst / f.relative_to(src).with_suffix("." + r.ext)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(r.data)
        note = " ".join(WARNINGS[w] for w in r.warnings)
        print(f"[{i}/{len(files)}] {f.name} -> {out.name}" + (f"  ({note})" if note else ""))
    print(f"Done: {len(files) - bad} photo(s) in {time.time() - t0:.0f}s, saved in {dst}")


if __name__ == "__main__":
    main()
