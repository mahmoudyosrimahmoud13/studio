# Studio

**Clean, matching product photos in one click, on your own computer.**

Drop in ordinary phone photos of your products. Studio removes the background, centers each product, fixes the light
and colors, and gives every photo the same size, margin and shadow, so a whole catalog looks like one photo shoot.

It runs fully offline with a small AI model on your CPU. No GPU, no account, no paid service, and your photos never
leave your computer.

## What it does

- **Removes the background** with an AI model that runs on your computer.
- **Clean edges**: no halo of the old background around the product.
- **Light and colors, gently**:
  - Brightness and contrast are measured on the product.
  - The white balance is read only from parts of the photo that should be neutral (walls, whites, grays), never from
    the product itself. So a maroon dress or a green bottle keeps its real color.
- **Lines everything up**: crops to the product and places it centered (or standing on the floor) with the same margin
  on a square or tall 4:5 canvas.
- **Soft shadow** and the background you want: white, soft gray, any color, or see-through.
- **Warns you** when a photo is too small, too dark, blurry, or the product touches the edge (it may be cut off).
- **Batch**: drop 50 photos at once and download them all as one ZIP. iPhone photos (HEIC) work.
- **An editor for each photo**, with a live result:
  - **Crop** (free, 1:1 or 4:5), rotate, mirror, straighten.
  - **Touch up** brush: paint *Keep* over parts the AI dropped, or *Remove* over leftovers (a table edge, a hanger).
  - **Fix colors**: tap something that should be white or gray (a wall, a white shirt, a tile) and the colors are
    corrected from that spot. There's also a green/pink slider, and automatic colors can be turned off per photo.
  - Brightness, contrast, color, warmth and sharpness sliders, and its own product size.
- **Your logo** on every photo (corner or middle, size and strength).
- **File names by product code**: type `NM-102` and the photos become `NM-102-1`, `NM-102-2`… in the order shown.
- **Arabic and English**, light and dark.
- **A terminal mode** that cleans a whole folder with no page at all.

## Quick start (Linux or macOS)

You need Python 3.10 or newer and `curl`.

```bash
git clone git@github.com:mahmoudyosrimahmoud13/studio.git
cd studio
chmod +x run.sh
./run.sh
```

- `git clone ...` downloads the project into a new `studio` folder.
- `cd studio` goes into that folder.
- `chmod +x run.sh` lets the start script run as a program. You only need it once.
- `./run.sh` does the setup the first time, then starts the page:
  - creates a private Python folder (`.venv`) and installs the libraries;
  - downloads the two AI models (about 175 MB, once) and checks their fingerprints, so a broken download is never used.

Then open **http://localhost:8300**. Press **Ctrl+C** to stop.

### More commands

`./run.sh help` lists them all.

| Command | What it does |
| --- | --- |
| `./run.sh --background` | Keeps running after you close the terminal. Stop it with `./run.sh stop`. |
| `./run.sh --phone` | Also opens on phones on the same Wi-Fi (the address is printed). Anyone on that Wi-Fi can use it, so only use it on your own network. |
| `./run.sh clean <in> <out>` | Cleans a whole folder with no page. Example: `./run.sh clean ~/Pictures/new ~/Pictures/new-clean`. Options: `--background "#F4F2EF"` or `transparent`, `--shape portrait`, `--format jpg`, `--no-enhance`. |
| `./run.sh stop` | Stops it. |

The port is 8300. Set `STUDIO_PORT` to use another one.

## Using the page

1. **Pick the look** on the left: speed (Best edges or Fast), background, shape (square, or tall 4:5 for clothes),
   product size, centered or standing on the floor, shadow, light fix, and file type and size. Optional: **File
   names** and **Logo on photos**.
2. **Drop photos** in the box (or click to choose, or paste).
3. **Click a photo** to fix just that one. The photo with **Crop / Touch up / Fix colors** is on the left, the live
   result in the middle, and rotate and the sliders on the right. **Save** keeps your changes; **Reset** undoes them.
4. **Compare** shows before and after side by side. Yellow notes say what to fix the next time you shoot.
5. **Changed the look** on the left? The button becomes **Settings changed: apply to all**. Edits made on single
   photos are kept.
6. **Download all (ZIP)**. Results are kept for 24 hours, then deleted.

**Best or Fast?**
- **Fast** is about 6 times quicker and fine for plain products on a plain background.
- **Best** is for hair, lace, glass, thin parts like hangers, or a busy background.

**Which file type?**
- **WebP** is the smallest and right for websites.
- **JPG** is for WhatsApp and older apps.
- **PNG** is for see-through with the sharpest edges.

**Colors still look off?** Open the photo, go to **Fix colors** and tap a wall or something white. If the photo was
right to begin with, untick **Fix colors automatically**.

## Shooting tips that make the biggest difference

- Use daylight from a window, beside the product (not behind it). No flash.
- Use a plain background in a color different from the product: a white sheet for dark items, a dark cloth for
  white ones.
- Leave space around the product, hold the phone still (or use a small stand), and tap the product to focus.
- Keep a white or gray wall in the shot when you can: it helps Studio get the colors right.
- Use the same angles for every product: front, side or back, and a close-up.

## How it works

Studio splits the work into a slow step that runs once and a fast step that runs on every change:

```
photo ──load()──▶ image ──mask()──▶ mask (the slow AI step, about 1.5 s, done once and kept)
                     │                 │
                     └──────render()───┘──▶ finished photo (about 0.5 s, on every change)
```

1. **`load()`** opens the photo the right way up (phones store the rotation separately), reads iPhone HEIC files,
   and shrinks very big photos.
2. **`mask()`** runs the AI model, which gives every pixel a "this is the product" score from 0 to 1. This is the
   slow part, so the page keeps the result and never runs it twice for the same photo.
3. **`render()`** does everything else, fast, in this order:
   1. **Your touch-ups** (the Keep / Remove brush) are merged into the mask.
   2. **Crop, rotate, mirror, straighten** are applied to the photo and the mask together.
   3. **The edges are cleaned**: the mask is tidied, small stray bits away from the product are dropped, and edge
      pixels are "decontaminated" (the old background's color is pulled out of them), so there's no halo.
   4. **Colors and light**:
      - The **white balance** is read from the whole photo, but only from pixels that look neutral (low color, not
        too dark, not blown out). It's half strength and at most 8% per color. If there's too little neutral area to
        judge, nothing is changed. If you picked a white spot, that spot decides instead.
      - **Brightness and contrast** are stretched gently on the product, the same for all three colors so no color
        shifts.
      - **Your sliders** come last: warmth, green/pink, brightness, contrast, color, sharpness.
   5. **Placement**: the product is cut to its own edges, then scaled and placed on the canvas with the same margin
      every time, centered or standing on the floor.
   6. **Shadow, background, logo**, then the file (WebP, JPG or PNG).
4. **Warnings** are measured on a small copy: too small, too dark (judged on the whole photo, so a black shirt on a
   light table is fine), blurry (measured along the product's edges), or touching a side.

### The models

| Speed | Model | Input | Size |
| --- | --- | --- | --- |
| Best | IS-Net, "general use" weights | 1024 × 1024 | 170 MB |
| Fast | U²-Net-P | 320 × 320 | 4.5 MB |

Both run with ONNX Runtime on the CPU. `run.sh` downloads them from the rembg project's releases and checks their
SHA-256 fingerprints.

### The code

| File | What it is |
| --- | --- |
| `studio/pipeline.py` | All the photo work: `load()`, `mask()`, `render()`, and `process()` to do all three at once. No web code, so you can use it in your own program. |
| `studio/web.py` | The page's small API (FastAPI). It keeps each photo's mask so changes only re-render. |
| `studio/static/index.html` | The page: one file, no build step. |
| `studio/cli.py` | The folder cleaner behind `./run.sh clean`. |
| `run.sh` | Setup, model download, start and stop. |
| `tests/` | Tests for the pipeline. |

### Use it from Python

```python
from studio.pipeline import load, mask, render, Options, Edits

img = load(open("dress.jpg", "rb").read())
m = mask(img, "best")                                   # slow, once
r = render(img, m, Options(background="#FFFFFF", shape="portrait", fmt="webp"),
           Edits(crop=(0.1, 0.05, 0.8, 0.9), warmth=0.2))  # fast, as often as you like
open("dress-clean.webp", "wb").write(r.data)
print(r.warnings)                                       # for example ['small', 'blurry']
```

### The page's API

| Call | What it does |
| --- | --- |
| `POST /api/photos` | A photo, a job id and a model. It is kept with its AI mask; returns its id and a preview. |
| `POST /api/render` | The id, the look and this photo's edits (plus an optional touch-up PNG). Returns the finished photo; with `preview=1`, a quick small one that isn't saved. |
| `GET /api/photos/<job>/<id>/preview.jpg`, `mask.png` | For the editor. |
| `POST /api/logo`, `DELETE /api/logo/<job>` | A logo stamped on every photo. |
| `GET /api/jobs/<job>/<file>`, `/api/jobs/<job>.zip` | The results. |
| `GET /api/status` | Whether the models are downloaded. |

### Put the page inside your own site

The page reads two optional settings from the window before it starts:

```html
<script>
  window.STUDIO_API = "/my-site/studio/api";  // where the API above lives (default "/api")
  window.STUDIO_EMBED = { site: "My shop", back: "/dashboard", backLabel: "Back", lang: "en", theme: "dark" };
</script>
```

`products: true` in `STUDIO_EMBED` also shows an **Add to a product** button. It calls `GET {STUDIO_API}/products`
and `POST {STUDIO_API}/to-product`, which your site provides.

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

- The first line installs the test tool into Studio's private Python folder.
- The second runs the tests:
  - a clean white result, see-through and tall shapes;
  - the warnings and bad input;
  - every editor tool: crop, rotate, colors, the brush, logo, own size;
  - that a strongly colored product keeps its color, and the white-spot and green/pink controls;
  - that Fast really is faster.

The tests that need the models skip themselves until `./run.sh` has downloaded them.

## Privacy

Everything runs on your computer. Photos are kept in `jobs/` for 24 hours so you can keep editing, then deleted. The
page listens only on your own computer (`127.0.0.1`) unless you start it with `--phone`.

## Thanks

- [rembg](https://github.com/danielgatis/rembg) for the ready-to-use ONNX models.
- [U²-Net](https://github.com/xuebinqin/U-2-Net) and [DIS / IS-Net](https://github.com/xuebinqin/DIS) by Xuebin Qin
  and others, the models behind Fast and Best.
- [ONNX Runtime](https://onnxruntime.ai), [Pillow](https://python-pillow.org), [NumPy](https://numpy.org) and
  [FastAPI](https://fastapi.tiangolo.com).

## License

MIT. See [LICENSE](LICENSE). The AI models have their own licenses; see the projects above.
