"""Reading names (and might) from screenshots with free, offline text recognition.

RapidOCR (PaddleOCR models running on ONNX Runtime) is used when it is installed. Tesseract is the fallback.
"""
from __future__ import annotations

import io
from typing import List, Tuple

import numpy as np
from PIL import Image, ImageOps

import logic as L


def load_engine():
    """Return (kind, engine). kind is 'rapidocr', 'tesseract' or None."""
    try:
        from rapidocr_onnxruntime import RapidOCR

        return "rapidocr", RapidOCR()
    except Exception:
        pass
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return "tesseract", pytesseract
    except Exception:
        return None, None


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


def tiles(img: Image.Image, overlap: float = 0.15) -> List[Tuple[int, Image.Image]]:
    """Cut a very tall screenshot into overlapping sections so each is read at full size."""
    w, h = img.size
    tile_h = int(max(w * 1.5, 800))
    if h <= tile_h * 1.15:
        return [(0, img)]
    step = int(tile_h * (1 - overlap))
    out: List[Tuple[int, Image.Image]] = []
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


def _ocr_tile(engine, img: Image.Image) -> List[L.OcrItem]:
    kind, eng = engine
    items: List[L.OcrItem] = []
    if kind == "rapidocr":
        result, _ = eng(np.array(img))
        for box, text, score in result or []:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            items.append(L.OcrItem(str(text), min(xs), min(ys), max(xs), max(ys), float(score)))
    else:
        d = eng.image_to_data(img, output_type=eng.Output.DICT, config="--psm 6")
        for i, t in enumerate(d["text"]):
            t = (t or "").strip()
            if not t:
                continue
            try:
                conf = float(d["conf"][i])
            except (TypeError, ValueError):
                conf = -1
            if conf < 0:
                continue
            x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
            items.append(L.OcrItem(t, x, y, x + w, y + h, conf / 100))
    return items


def read_image(engine, img: Image.Image, crop=(0, 100), mode: str = L.MODE_ATTENDANCE,
               pick: str = "largest", ignore=()) -> List[dict]:
    img = crop_width(img, crop[0], crop[1])
    if img.width < 900:
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    sections = tiles(img)
    rows: List[dict] = []
    for idx, (_, tile) in enumerate(sections):
        items = _ocr_tile(engine, tile)
        first, last, th = idx == 0, idx == len(sections) - 1, tile.height
        # rows touching a cut edge are partial; the overlapping neighbour section has them whole
        items = [it for it in items if (first or it.y0 >= 3) and (last or it.y1 <= th - 3)]
        rows += L.rows_from_items(items, mode, pick, ignore)
    return rows
