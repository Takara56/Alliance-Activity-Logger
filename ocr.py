"""Reading names (and might) from screenshots with free, offline text recognition.

Readers are tried best first:
  1. RapidOCR 3 with the PP-OCRv5 models (server size if available). This is the most accurate free reader and has
     recognition models for Latin letters (accents, Turkish) and Cyrillic as well as Chinese and English.
  2. RapidOCR 1.x (the older package) with its built-in models.
  3. Tesseract, if it is installed.
"""
from __future__ import annotations

import io
from typing import List

import numpy as np
from PIL import Image, ImageFilter, ImageOps

import logic as L

LANGUAGES = {
    "default": "Chinese and English (default)",
    "latin": "Latin letters (accents, Turkish and other European names)",
    "cyrillic": "Cyrillic (Russian and similar)",
    "korean": "Korean (plus English)",
}


# Where the names are on the two screens we know about: (left %, right %, top %, bottom %).
# The top cut hides the header, the bottom cut hides the pinned "your own row" so it is not read on every screenshot.
PRESETS = {
    "none": ("Full screenshot (no crop)", None),
    "alliance_power": ("Alliance Ranking (Power list: names and power)", (34.8, 95.5, 15.5, 90.5)),
    "mobilization": ("Alliance Mobilization (Personal Ranking)", (47, 72, 39.5, 73.3)),
    "custom": ("Custom (use the sliders)", None),
}
TESS_LANG = {"default": "eng", "latin": "eng+tur+deu+fra+spa", "cyrillic": "rus+eng", "korean": "kor+eng"}


def _v3_engine(lang: str):
    """Build a RapidOCR 3 engine. Returns (engine, label) or raises if this version/lang route is unavailable."""
    from rapidocr import ModelType, OCRVersion, RapidOCR

    try:
        from rapidocr import LangRec
    except Exception:  # older 3.x releases
        LangRec = None
    last_err = None
    types = [t for t in (getattr(ModelType, "SERVER", None), getattr(ModelType, "MEDIUM", None),
                         getattr(ModelType, "MOBILE", None), getattr(ModelType, "TINY", None)) if t is not None]
    for mt in types or [None]:
        params = {"Det.ocr_version": OCRVersion.PPOCRV5, "Rec.ocr_version": OCRVersion.PPOCRV5}
        if mt is not None:
            params["Det.model_type"] = mt
            params["Rec.model_type"] = mt
        if lang != "default":
            val = getattr(LangRec, lang.upper(), None) if LangRec is not None else None
            params["Rec.lang_type"] = val if val is not None else lang
        try:
            eng = RapidOCR(params=params)
            eng(np.full((64, 256, 3), 255, dtype=np.uint8))  # warm-up: downloads models, proves they load
            size = getattr(mt, "value", mt) or "default"
            return eng, f"RapidOCR 3, PP-OCRv5 {size} model" + ("" if lang == "default" else f", {lang} letters")
        except Exception as e:  # unsupported combination: try the next model size
            last_err = e
    raise RuntimeError(f"RapidOCR 3 unavailable for '{lang}': {last_err}")


def load_engine(lang: str = "default"):
    """Return (kind, engine, label). kind is 'rapidocr3', 'rapidocr', 'tesseract' or None."""
    notes = []
    try:
        eng, label = _v3_engine(lang)
        return "rapidocr3", eng, label
    except Exception as e:
        notes.append(str(e)[:120])
        if lang != "default":
            try:
                eng, label = _v3_engine("default")
                return "rapidocr3", eng, label + f" (the {lang} model could not be loaded, so the default one is used)"
            except Exception as e2:
                notes.append(str(e2)[:120])
    try:
        from rapidocr_onnxruntime import RapidOCR as OldRapidOCR

        return "rapidocr", OldRapidOCR(), "RapidOCR 1 (older models)"
    except Exception:
        pass
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return "tesseract", pytesseract, "Tesseract (basic fallback, less accurate)"
    except Exception:
        return None, None, "; ".join(notes) or "no text reader installed"


def load_image(file) -> Image.Image:
    # an uploaded file is read from its start every time, so it can be shown and then read again
    src = io.BytesIO(file.getvalue()) if hasattr(file, "getvalue") else file
    img = ImageOps.exif_transpose(Image.open(src))
    return img.convert("RGB")


def crop_width(img: Image.Image, lo: int, hi: int) -> Image.Image:
    w = img.width
    left = int(w * lo / 100)
    right = max(int(w * hi / 100), left + 1)
    return img.crop((left, 0, min(right, w), img.height))


def crop_area(img: Image.Image, crop=(0, 100), vcrop=(0, 100)) -> Image.Image:
    img = crop_width(img, crop[0], crop[1])
    if tuple(vcrop) != (0, 100):
        top, bottom = int(img.height * vcrop[0] / 100), int(img.height * vcrop[1] / 100)
        img = img.crop((0, top, img.width, max(bottom, top + 1)))
    return img


def enhance(img: Image.Image) -> Image.Image:
    """Make small or low-contrast text easier to read: upscale, stretch contrast, sharpen."""
    if img.width * 2 <= 2400:
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=1)
    return img.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=3))


def tiles(img: Image.Image, overlap: float = 0.15):
    """Cut a very tall screenshot into overlapping sections so each is read at full size."""
    w, h = img.size
    tile_h = int(max(w * 1.5, 800))
    if h <= tile_h * 1.15:
        return [(0, img)]
    step = int(tile_h * (1 - overlap))
    out = []
    y = 0
    while True:
        if y + tile_h >= h:
            y = max(0, h - tile_h)
            if not out or out[-1][0] != y:
                out.append((y, img.crop((0, y, w, h))))
            break
        out.append((y, img.crop((0, y, w, y + tile_h))))
        y += step
    return out


def _text_mask(img: Image.Image) -> np.ndarray:
    """True where a pixel is part of text. Adapts to the local background so coloured rows (gold, blue, green) work too."""
    gray = img.convert("L")
    g = np.array(gray)
    blur = np.array(gray.filter(ImageFilter.GaussianBlur(12)))
    return (blur.astype(int) - g.astype(int)) > 38  # clearly darker than the surroundings


def _dark_lines(img: Image.Image, gap: int = 10, min_h: int = 14):
    """Find the text lines in a name column by looking for rows that contain text pixels."""
    has = _text_mask(img).sum(axis=1) >= 3
    lines, start, last = [], None, None
    for y, v in enumerate(has):
        if v:
            if start is None:
                start = y
            last = y
        elif start is not None and y - last > gap:
            if last - start + 1 >= min_h:
                lines.append((start, last + 1))
            start = None
    if start is not None and last - start + 1 >= min_h:
        lines.append((start, last + 1))
    return lines


def _x_runs(mask_rows: np.ndarray, gap: int = 35):
    """Split a text line into separate pieces (name, number...) wherever there is a wide empty gap."""
    cols = np.where(mask_rows.sum(axis=0) >= 1)[0]
    if len(cols) == 0:
        return []
    runs, start, prev = [], cols[0], cols[0]
    for x in cols[1:]:
        if x - prev > gap:
            runs.append((start, prev + 1))
            start = x
        prev = x
    runs.append((start, prev + 1))
    return [r for r in runs if r[1] - r[0] >= 8]


def _tesseract_items(eng, img: Image.Image, lang: str) -> List[L.OcrItem]:
    """Read each piece of each text line on its own (much more reliable than reading the whole column at once)."""
    items: List[L.OcrItem] = []
    mask = _text_mask(img)
    for y0, y1 in _dark_lines(img):
        for x0, x1 in _x_runs(mask[y0:y1]):
            pad = 8
            crop = img.crop((max(0, x0 - pad), max(0, y0 - pad), min(img.width, x1 + pad), min(img.height, y1 + pad)))
            scale = max(1.0, 56.0 / max(crop.height, 1)) * 1.5
            crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS).convert("L")
            crop = ImageOps.autocontrast(crop, cutoff=1)
            crop = ImageOps.expand(crop, border=12, fill=255)
            d = eng.image_to_data(crop, lang=lang, output_type=eng.Output.DICT, config="--psm 7")
            words, confs = [], []
            for i, t in enumerate(d["text"]):
                t = (t or "").strip()
                try:
                    c = float(d["conf"][i])
                except (TypeError, ValueError):
                    c = -1
                if t and c >= 0:
                    words.append(t)
                    confs.append(c)
            if words:
                items.append(L.OcrItem(" ".join(words), x0, y0, x1, y1, (sum(confs) / len(confs)) / 100))
    return items


def _ocr_tile(engine, img: Image.Image, lang: str = "default") -> List[L.OcrItem]:
    kind, eng = engine[0], engine[1]
    items: List[L.OcrItem] = []
    if kind == "rapidocr3":
        res = eng(np.array(img))
        boxes, txts, scores = getattr(res, "boxes", None), getattr(res, "txts", None), getattr(res, "scores", None)
        if boxes is not None and txts is not None:
            for box, text, score in zip(boxes, txts, scores):
                xs, ys = [p[0] for p in box], [p[1] for p in box]
                items.append(L.OcrItem(str(text), min(xs), min(ys), max(xs), max(ys), float(score)))
    elif kind == "rapidocr":
        result, _ = eng(np.array(img))
        for box, text, score in result or []:
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            items.append(L.OcrItem(str(text), min(xs), min(ys), max(xs), max(ys), float(score)))
    else:
        items = _tesseract_items(eng, img, TESS_LANG.get(lang, "eng"))
    return items


def read_image(engine, img: Image.Image, crop=(0, 100), mode: str = L.MODE_ATTENDANCE,
               pick: str = "largest", ignore=(), enhance_image: bool = False, vcrop=(0, 100), lang: str = "default") -> List[dict]:
    img = crop_area(img, crop, vcrop)
    if enhance_image:
        img = enhance(img)
    elif img.width < 900 and engine[0] != "tesseract":
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    sections = tiles(img)
    rows: List[dict] = []
    for idx, (_, tile) in enumerate(sections):
        items = _ocr_tile(engine, tile, lang)
        first, last, th = idx == 0, idx == len(sections) - 1, tile.height
        # rows touching a cut edge are partial; the overlapping neighbour section (or the next screenshot) has them whole
        items = [it for it in items if it.y0 >= 3 and it.y1 <= th - 3]
        rows += L.rows_from_items(items, mode, pick, ignore)
    return rows
