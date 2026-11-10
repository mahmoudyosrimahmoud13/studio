# Studio

Turns ordinary product photos into clean, matching catalog photos, on your own computer:

- **Removes the background** with an AI model that runs on the laptop (nothing is uploaded anywhere).
- **Cleans the edges**, so there's no halo of the old background around the product.
- **Fixes the light gently**: white balance (no yellow or blue tint) and brightness, measured on the product only.
- **Lines everything up**: crops to the product and places it centered, or standing on the floor, with the same
  margin on a square or tall 4:5 canvas, so a whole catalog looks like one photo shoot.
- **Soft shadow** under the product, and the background you want: white, soft gray, any color, or see-through.
- **Warns you** when a photo is too small, dark, blurry, or the product touches the edge (may be cut off).
- **Batch**: drop 50 photos at once, then download them all as one ZIP. iPhone photos (HEIC) work.
- **Editor for each photo**: crop (free, 1:1 or 4:5), rotate, mirror, straighten, brightness, contrast, color,
  warmth, sharpness, its own product size, and a **touch-up brush**: paint *Keep* over parts the AI dropped, or
  *Remove* over leftovers (a table edge, a hanger). The result updates live as you change things.
- **Your logo** on every photo (corner or middle, size and strength), and **file names by product code**
  (type NM-102 and the photos become NM-102-1, NM-102-2… in the order shown).
- **Fast**: the slow AI step runs once per photo (about 1.5 s with Best, 0.3 s with Fast). Every change after that,
  in the editor or for the whole batch, takes about half a second.

It's separate from Rubies and Hydro Alex for now. The photo code (`studio/pipeline.py`) has nothing web-related in
it, so it can be moved into either site later.

## Start it

```bash
chmod +x run.sh
./run.sh
```

- `chmod +x run.sh` lets the script run as a program. Only needed once.
- `./run.sh` creates Studio's private Python folder, installs the photo libraries, downloads the background model
  (about 170 MB, one time only, and checked so a broken download is never used), then starts the page.
  Open **http://localhost:8300**. Press **Ctrl+C** to stop.

More (`./run.sh help` shows them all):

| Command | What it does |
| --- | --- |
| `./run.sh --background` | Keep running after you close the window. Stop with `./run.sh stop`. |
| `./run.sh --phone` | Also open it from a phone on the same Wi-Fi (the address is printed). Anyone on that Wi-Fi can use it, so only on your own network. |
| `./run.sh clean <in> <out>` | Clean a whole folder from the terminal, no page needed. Example: `./run.sh clean ~/Pictures/new ~/Pictures/new-clean`. Options: `--background "#F4F2EF"` or `transparent`, `--shape portrait`, `--format jpg`, `--no-enhance`. |
| `./run.sh stop` | Stop it. |

## Using the page

1. Pick the look on the left: speed (Best edges or Fast), background, shape (square, or tall 4:5 for clothes),
   product size, centered or standing on the floor (good for heaters, toilets, shoes), shadow, light fix, file
   type and size. Optional: **File names** (a product code) and **Logo on photos**.
2. Drop photos in the box (or click to choose, or paste).
3. Click a photo (or **Edit**) to fix just that one: **Crop** and **Touch up** on the left, the live result in the
   middle, rotate and sliders on the right. **Save** applies it; **Reset** undoes all edits on that photo.
4. **Compare** shows before and after side by side. Yellow notes say what to fix next time you shoot.
5. Changed the look on the left? The button turns into **Settings changed: apply to all**. Edits made on single
   photos are kept.
6. **Download all (ZIP)**. Results are kept 24 hours on the laptop, then deleted.

**Best or Fast?** Fast (about 6 times quicker) is fine for plain products on a plain background. Use Best for hair,
lace, glass, thin parts like hangers, or a busy background.

Which file type: **WebP** is the smallest and right for the websites. **JPG** for WhatsApp and older apps.
**PNG** when you need see-through with the sharpest edges.

## Shooting tips that make the biggest difference

- Daylight from a window, beside the product (not behind it). No flash.
- A plain background in a color different from the product (white sheet for dark items, dark cloth for white ones).
- Leave space around the product, hold the phone still (or use a small stand), tap the product to focus.
- Same angles for every product: front, side or back, and a close-up.

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

- The first line installs the test tool into Studio's private Python folder.
- The second runs the tests: a clean white result, see-through and tall shapes, the warnings, bad input, every
  editor tool (crop, rotate, colors, the brush, logo, own size) and that Fast really is faster.

## How it works (for later, when it moves into Rubies or Hydro)

- `studio/pipeline.py`: three steps. `load()` opens a photo, `mask()` is the AI step (run once, keep the result),
  `render(img, mask, Options(...), Edits(...), Logo(...))` makes the finished photo. `process()` does all three.
  Needs only
  `onnxruntime`, `numpy` and `pillow` (plus `pillow-heif` for iPhone photos) and the model file in `models/`.
  Models: ISNet "general use" (Best, 1024 x 1024) and U2Net-P (Fast, 320 x 320), both from the rembg project.
- `studio/web.py`: the page's small API. `studio/cli.py`: the folder cleaner.
- To merge: copy `pipeline.py` into the site's `backend/app/services/`, call `process()` after an upload (in the
  background, since each photo takes a few seconds), and add a per-shop switch "clean my photos". The website side
  only needs a "cleaning..." state on the photo.
