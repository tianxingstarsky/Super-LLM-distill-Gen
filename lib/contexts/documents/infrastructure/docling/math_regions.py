"""PDF character evidence for choosing local math crops, without OCR or rewriting.

These signals select candidates, not verified mathematics. Unrecognized regions
retain physical-page coordinates and source character IDs. In particular, a
paragraph must never be replaced merely because this module found a math font.
"""
from __future__ import annotations

import ctypes
import hashlib
import math
from pathlib import Path
import re
import unicodedata


MATH_REGION_POLICY = "pdfium_glyph_math_evidence_v3"


def is_math_font(name):
    """Recognize established math font families, including PDF subset prefixes."""
    font = re.sub(r"^/?[A-Z]{6}\+", "", str(name)).lstrip("/").lower()
    return bool(re.match(r"cm(mi|mib|sy|bsy|ex)|ms(am|bm)|rsfs|eufm", font)
                or any(part in font for part in ("cambriamath", "latinmodernmath", "texgyremath", "mtextra"))
                or ("stix" in font and "text" not in font))


def _box(value, height, coord_origin="BOTTOMLEFT"):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        origin = str(getattr(value.get("coord_origin", coord_origin), "value", value.get("coord_origin", coord_origin)))
        l, b, r, t = (float(value[k]) for k in ("l", "b", "r", "t"))
    else:
        l, b, r, t = map(float, value)
        origin = coord_origin
    if origin == "TOPLEFT":
        b, t = height - b, height - t
    elif origin != "BOTTOMLEFT":
        raise ValueError("document_docling_invalid_provenance")
    if not all(math.isfinite(x) for x in (l, b, r, t)) or r <= l or t <= b:
        raise ValueError("document_docling_invalid_provenance")
    return l, b, r, t


def _box_dict(box):
    l, b, r, t = box
    return {"l": l, "b": b, "r": r, "t": t, "coord_origin": "BOTTOMLEFT"}


def _inside(glyph, box):
    g = glyph["bbox"]
    x, y = (g["l"] + g["r"]) / 2, (g["b"] + g["t"]) / 2
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def extract_pdf_page_glyphs(source, page_no):
    """Read a 1-based physical page from PDF bytes, a path, or an open PdfDocument.

    An open document remains owned by the caller. There are no model imports,
    network calls, OCR, inferred characters, or modifications to PDF content.
    """
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    owns_document = not isinstance(source, pdfium.PdfDocument)
    source_hash = None
    if owns_document:
        content = bytes(source) if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
        source_hash = hashlib.sha256(content).hexdigest()
        pdf = pdfium.PdfDocument(content)
    else:
        pdf = source
    page = textpage = None
    try:
        if type(page_no) is not int or not 1 <= page_no <= len(pdf):
            raise ValueError("document_docling_invalid_provenance")
        page = pdf[page_no - 1]
        width, height = page.get_size()
        rotation = page.get_rotation()
        crop_box, visible_box = page.get_cropbox(), page.get_bbox()
        coordinate_space = {"page_rotation": rotation, "crop_bbox": list(crop_box),
                            "visible_bbox": list(visible_box)}
        # PDFium's glyph coordinates are in unrotated PDF user space, whereas
        # Docling's cell boxes and rendered page size use the visible display
        # frame. Do not compare those frames or invent a partial transformation.
        if rotation != 0 or any(abs(v) > .01 for v in (*crop_box[:2], *visible_box[:2])):
            return {"page_no": page_no, "width": width, "height": height, "glyphs": [],
                    "source_sha256": source_hash, "policy": MATH_REGION_POLICY,
                    "character_layer_present": None, "status": "unsupported_coordinate_space",
                    "review_required": True, "reason": "unsupported_pdf_coordinate_space",
                    "coordinate_space": coordinate_space}
        textpage = page.get_textpage()
        glyphs = []
        for index in range(textpage.count_chars()):
            flags = ctypes.c_int()
            length = raw.FPDFText_GetFontInfo(textpage, index, None, 0, ctypes.byref(flags))
            # Keep corrupted font metadata from causing an unbounded allocation.
            if length > 65536:
                raise ValueError("document_docling_invalid_font_metadata")
            buffer = ctypes.create_string_buffer(max(length, 1))
            if length:
                raw.FPDFText_GetFontInfo(textpage, index, buffer, length, ctypes.byref(flags))
            x, y = ctypes.c_double(), ctypes.c_double()
            has_origin = raw.FPDFText_GetCharOrigin(textpage, index, ctypes.byref(x), ctypes.byref(y))
            codepoint = raw.FPDFText_GetUnicode(textpage, index)
            text = chr(codepoint) if codepoint <= 0x10ffff else "\ufffd"
            box = textpage.get_charbox(index)
            weight = raw.FPDFText_GetFontWeight(textpage, index)
            glyphs.append({"char_index": index, "text": text, "codepoint": codepoint,
                           "font_name": buffer.value.decode("utf-8", errors="replace"),
                           "font_flags": flags.value, "font_size": raw.FPDFText_GetFontSize(textpage, index),
                           "font_weight": weight if weight > 0 else None,
                           "bbox": _box_dict(box), "origin": [x.value, y.value] if has_origin else None,
                           "generated": raw.FPDFText_IsGenerated(textpage, index) == 1})
        return {"page_no": page_no, "width": width, "height": height, "glyphs": glyphs,
                "source_sha256": source_hash, "policy": MATH_REGION_POLICY,
                "status": "ok", "review_required": False, "coordinate_space": coordinate_space,
                "character_layer_present": any(g["text"].strip() for g in glyphs)}
    finally:
        if textpage is not None:
            textpage.close()
        if page is not None:
            page.close()
        if owns_document:
            pdf.close()


def _ordinary_prose_runs(glyphs):
    """Find ordinary-font words without mistaking smaller script labels for prose.

    This is a conservative boundary check, not a language or formula parser.
    A roman subscript such as the label in d_model is exempt only when its
    smaller size and shifted baseline establish a script beside a larger base.
    """
    runs, current = [], []
    for glyph in glyphs:
        ordinary_letter = glyph["text"].isalpha() and not is_math_font(glyph["font_name"])
        adjacent = True
        if current:
            previous = current[-1]
            size = max(previous["font_size"], glyph["font_size"], 1)
            previous_y = previous["origin"][1] if previous["origin"] is not None else previous["bbox"]["b"]
            current_y = glyph["origin"][1] if glyph["origin"] is not None else glyph["bbox"]["b"]
            adjacent = (abs(previous["font_size"] - glyph["font_size"]) <= .1 * size
                        and abs(previous_y - current_y) <= .12 * size
                        and -.25 * size <= glyph["bbox"]["l"] - previous["bbox"]["r"] <= .4 * size)
        if not ordinary_letter or not adjacent:
            if current:
                runs.append(current)
            current = []
        if ordinary_letter:
            current.append(glyph)
    if current:
        runs.append(current)
    prose = []
    for run in runs:
        text = "".join(g["text"] for g in run)
        # CJK and other unspaced scripts can express prose with fewer than three
        # characters; do not restrict this safeguard to ASCII word boundaries.
        if len(text) < 3 and not any(unicodedata.category(c) == "Lo" for c in text):
            continue
        first = run[0]
        script = False
        if first["origin"] is not None:
            for base in glyphs:
                if base in run or base["origin"] is None or not base["text"].isalnum():
                    continue
                size = max(base["font_size"], 1)
                offset = first["origin"][1] - base["origin"][1]
                gap = first["bbox"]["l"] - base["bbox"]["r"]
                if (max(g["font_size"] for g in run) < .88 * size
                        and -.25 * size <= gap <= .55 * size
                        and .12 * size < abs(offset) < .95 * size):
                    script = True
                    break
        if not script:
            prose.append({"text": text, "char_indices": [g["char_index"] for g in run]})
    return prose


def inspect_math_region(page, bbox, *, coord_origin="BOTTOMLEFT"):
    """Select cell/span characters by their centers and report math evidence.

    `contains_math` means a local-recognition candidate, never a correctness
    verdict. Empty/scanned regions remain distinguishable from ordinary text.
    """
    box = _box(bbox, page["height"], coord_origin)
    if page.get("status") == "unsupported_coordinate_space":
        return {"contains_math": False, "has_math_signal": False,
                "status": "unsupported_coordinate_space", "review_required": True,
                "reason": "unsupported_pdf_coordinate_space", "page_no": page["page_no"],
                "bbox": _box_dict(box), "policy": MATH_REGION_POLICY,
                "source_sha256": page.get("source_sha256"), "char_indices": [],
                "source_text_in_character_order": None, "font_names": [], "reasons": [],
                "math_font_char_indices": [], "math_symbol_char_indices": [],
                "script_candidates": [], "ordinary_prose_runs": [], "recognized_latex": None,
                "coordinate_space": page.get("coordinate_space"),
                "review_flags": ["unsupported_pdf_coordinate_space"]}
    region_glyphs = [g for g in page["glyphs"] if _inside(g, box)]
    glyphs = [g for g in region_glyphs if g["text"].strip()]
    fonts = [g["char_index"] for g in glyphs if is_math_font(g["font_name"])]
    symbols = [g["char_index"] for g in glyphs if any(unicodedata.category(c) == "Sm" for c in g["text"])]
    scripts = []
    for small in glyphs:
        if not small["origin"] or not small["text"].isnumeric():
            continue
        for base in glyphs:
            if base is small or not base["origin"] or not (base["text"].isalnum() or is_math_font(base["font_name"])):
                continue
            size = max(base["font_size"], 1)
            if small["font_size"] >= .88 * size:
                continue
            offset = small["origin"][1] - base["origin"][1]
            sb, bb = small["bbox"], base["bbox"]
            gap = sb["l"] - bb["r"]
            if -.25 * size <= gap <= .55 * size and .12 * size < abs(offset) < .95 * size:
                scripts.append({"base_char_index": base["char_index"], "script_char_index": small["char_index"],
                                "baseline_offset": offset, "relation_candidate": "superscript" if offset > 0 else "subscript"})
                break
    reasons = []
    if fonts:
        reasons.append("dedicated_math_font")
    if symbols:
        reasons.append("unicode_math_symbol")
    if scripts:
        reasons.append("numeric_script_geometry")
    source_text = "".join(g["text"] for g in glyphs)
    prose = _ordinary_prose_runs(region_glyphs)
    # Generic punctuation in GNMT+RL and similar names is insufficient evidence
    # of mathematics. Keep those cells unchanged without requiring quarantine.
    weak_symbol_text = bool(prose and symbols and not fonts and not scripts
                            and all(c in "+=<>|" for g in glyphs for c in g["text"]
                                    if unicodedata.category(c) == "Sm"))
    mixed_prose = bool(prose and reasons and not weak_symbol_text)
    candidate = bool(reasons) and not prose
    status = ("unrecognized_math_region" if candidate else
              "mixed_prose_math" if mixed_prose else
              "weak_symbol_in_text" if weak_symbol_text else
              "no_text_layer" if not glyphs else "no_math_evidence")
    reason = "mixed_prose_math_requires_boundary_review" if mixed_prose else None
    return {"contains_math": candidate, "has_math_signal": bool(reasons), "status": status,
            "review_required": mixed_prose, "reason": reason,
            "page_no": page["page_no"], "bbox": _box_dict(box), "policy": MATH_REGION_POLICY,
            "source_sha256": page.get("source_sha256"), "char_indices": [g["char_index"] for g in glyphs],
            "source_text_in_character_order": source_text,
            "font_names": sorted({g["font_name"] for g in glyphs}), "reasons": reasons,
            "math_font_char_indices": fonts, "math_symbol_char_indices": symbols,
            "script_candidates": scripts, "ordinary_prose_runs": prose, "recognized_latex": None,
            "review_flags": [reason] if reason else []}


def candidate_inline_math_regions(page, containers):
    """Flag caller-provided word/line boxes; never expand them into paragraphs.

    Containers are dictionaries with `bbox` and optional `self_ref`/`kind`.
    Passing entire paragraph boxes is allowed for diagnostics, but every result
    remains `review_required`: this API is not permission to replace its text.
    A trained inline detector should provide precise boxes where font evidence
    is incomplete. The caller retains the original full document regardless.
    """
    result = []
    for container in containers:
        evidence = inspect_math_region(page, container["bbox"])
        if not evidence["contains_math"] and not evidence["review_required"]:
            continue
        evidence.update(source_ref=container.get("self_ref"), container_kind=container.get("kind", "unknown"),
                        boundary_status="review_required", automatic_text_replacement_allowed=False)
        result.append(evidence)
    return result
