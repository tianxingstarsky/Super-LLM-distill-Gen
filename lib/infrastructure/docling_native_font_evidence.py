"""Source-font evidence for comparison, without changing extracted source text.

Only an exact native horizontal extent with a contained raw baseline and a
unique PDFium font/origin match may connect two character layers. Unicode comes
from the PDF's actual font resource, decoded by pypdf. No OCR output or global
character aliases participate in this process.
"""
from __future__ import annotations

from collections import Counter
from io import BytesIO
import hashlib
import math
from pathlib import Path
import re
import unicodedata


NATIVE_FONT_POLICY = "source_type1_cmap_glyph_origin_style_v2"
_ORIGIN_TOLERANCE = .05  # PDF-point rounding, not glyph overlap or word distance.


def _normalized(text):
    return unicodedata.normalize("NFKC", text)


def _font_name(name):
    return re.sub(r"^[A-Z]{6}\+", "", str(name).lstrip("/"))


def _bbox_key(bbox):
    if bbox.get("coord_origin", "BOTTOMLEFT") != "BOTTOMLEFT":
        raise ValueError("unsupported_font_evidence_coordinates")
    values = tuple(float(bbox[key]) for key in ("l", "b", "r", "t"))
    if not all(math.isfinite(value) for value in values):
        raise ValueError("invalid_font_evidence_coordinates")
    return tuple(round(value, 6) for value in values)


def _font_maps(font):
    """Decode only simple embedded Type1 fonts; other font types stay unchanged."""
    from pypdf._cmap import _character_map_from_type1_font_file, _parse_to_unicode

    if font.get("/Subtype") != "/Type1":
        return None
    descriptor = font.get("/FontDescriptor")
    descriptor = descriptor.get_object() if descriptor is not None else None
    stream = descriptor.get("/FontFile") if descriptor else None
    if stream is None:
        return None
    font_bytes = stream.get_object().get_data()
    embedded, _ = _character_map_from_type1_font_file(font_bytes, {}, [])
    cmap = {}
    cmap_bytes = b""
    if "/ToUnicode" in font:
        cmap, _ = _parse_to_unicode(font)
        cmap_bytes = font["/ToUnicode"].get_data()
    # /Encoding overrides the embedded font's byte -> glyph mapping. Defer
    # those fonts rather than assuming the embedded Encoding still applies.
    if "/Encoding" in font:
        return None
    source_weight = None
    try:
        from fontTools.misc.psLib import suckfont
        parsed = suckfont(font_bytes)
        weight = parsed.get("FontInfo", {}).get("Weight")
        if (isinstance(weight, str) and weight.strip()
                and _font_name(parsed.get("FontName", "")) == _font_name(font.get("/BaseFont", ""))):
            source_weight = {
                "source": "fontTools.misc.psLib.suckfont:FontInfo.Weight",
                "declaration": weight, "font_program_name": parsed["FontName"],
                # This is a declared style, never a font-name guess. Other
                # declarations remain unproven rather than inventing a scale.
                "supports_bold": (True if weight.strip().casefold() == "bold" else
                                  False if weight.strip().casefold() in {
                                      "regular", "normal", "medium", "light", "book", "thin"
                                  } else None),
            }
    except Exception:
        # A malformed/unsupported Type1 program must not erase its independent
        # Unicode evidence or silently become a positive style certificate.
        pass
    return {
        "font_base_name": str(font.get("/BaseFont", "")),
        "cmap": cmap, "embedded": embedded,
        "font_program_sha256": hashlib.sha256(font_bytes).hexdigest(),
        "tounicode_sha256": hashlib.sha256(cmap_bytes).hexdigest() if cmap_bytes else None,
        "source_weight": source_weight,
    }


def decode_source_font_character(font_maps, char_code, glyph_name):
    """Return a source-only decoding proof, or a reason to retain the mismatch.

    ``font_maps`` comes from this module's source-font reader. The glyph name is
    the official native renderer's name for the painted character code.
    """
    from pypdf._cmap import _glyph_name_to_unicode

    result = {"review_required": True, "reason": "unsupported_source_font"}
    if not font_maps or type(char_code) is not int or not 0 <= char_code <= 255:
        return result
    code = chr(char_code)
    cmap = font_maps["cmap"].get(code)
    embedded = font_maps["embedded"].get(code)
    named = _glyph_name_to_unicode("/" + str(glyph_name).lstrip("/"))
    evidence = [value for value in (cmap, embedded, named) if value]
    result.update(
        font_base_name=font_maps["font_base_name"], char_code=char_code,
        glyph_name=glyph_name, tounicode_text=cmap, embedded_text=embedded,
        glyph_name_text=named, font_program_sha256=font_maps["font_program_sha256"],
        tounicode_sha256=font_maps["tounicode_sha256"],
        source_weight=font_maps.get("source_weight"),
    )
    if not (cmap or embedded):
        result["reason"] = "missing_source_font_mapping"
        return result
    if len({_normalized(value) for value in evidence}) != 1:
        result["reason"] = "conflicting_source_font_mappings"
        return result
    canonical = cmap or embedded
    if not canonical or any(unicodedata.category(char).startswith("C") for char in canonical):
        result["reason"] = "invalid_source_font_unicode"
        return result
    result.update(review_required=False, reason=None, comparison_text=canonical,
                  decoding_source="ToUnicode" if cmap else "embedded_Type1_Encoding")
    return result


def read_native_font_evidence(source, *, page_numbers=None):
    """Read font resources and official painted-glyph instructions, without models.

    The currently pinned native parser exposes render instructions through its
    decoder; no character code is guessed from the extracted Unicode string.
    Pages with Form XObjects, rotated frames or shifted CropBoxes are excluded:
    their font-resource scope or coordinate frame needs separate support.
    """
    from docling_parse.pdf_parser import (
        ContentConfig, ContentLevel, DoclingThreadedPdfParser, ThreadedPdfParserConfig,
    )
    from pypdf import PdfReader

    content = bytes(source) if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    pdf = PdfReader(BytesIO(content))
    config = ContentConfig(
        char_cells_content_level=ContentLevel.COMPUTE_AND_MATERIALIZE,
        word_cells_content_level=ContentLevel.SKIP, line_cells_content_level=ContentLevel.SKIP,
        shapes_content_level=ContentLevel.SKIP, bitmaps_content_level=ContentLevel.SKIP,
    )
    parser = DoclingThreadedPdfParser(parser_config=ThreadedPdfParserConfig(
        loglevel="fatal", threads=1, page_content_config=config, render_config=None))
    key = parser.load(BytesIO(content), page_numbers=page_numbers)
    pages, font_cache = {}, {}
    try:
        for response in parser.iterate_results():
            number = response.page_number
            page = response.get_page()
            result = dict(policy=NATIVE_FONT_POLICY, page_no=number, source_sha256=digest,
                          records=[], review_required=False, reasons=[])
            pages[number] = result
            resources = pdf.pages[number - 1]["/Resources"]
            xobjects = resources.get("/XObject")
            xobjects = xobjects.get_object() if xobjects is not None else {}
            crop = page.dimension.crop_bbox.to_bottom_left_origin(page.dimension.height)
            if (abs(page.dimension.angle) > .01 or abs(crop.l) > .01 or abs(crop.b) > .01
                    or any(value.get_object().get("/Subtype") == "/Form" for value in xobjects.values())):
                result.update(review_required=True, reasons=["unsupported_font_scope_or_coordinates"])
                continue
            fonts = resources.get("/Font")
            fonts = fonts.get_object() if fonts is not None else {}
            decoded_fonts = {}
            for name, value in fonts.items():
                font = value.get_object()
                identity = id(font)
                if identity not in font_cache:
                    font_cache[identity] = _font_maps(font)
                decoded_fonts[str(name)] = font_cache[identity]
            instructions = response._require_page_decoder().export_render_instructions_json()["instructions"]
            raw_by_extent = {}
            for ordinal, instruction in enumerate(instructions):
                if instruction.get("type") != "text" or not instruction.get("quad"):
                    continue
                quad = instruction["quad"]
                bounds = dict(l=min(quad[f"r_x{i}"] for i in range(4)),
                              r=max(quad[f"r_x{i}"] for i in range(4)),
                              b=min(quad[f"r_y{i}"] for i in range(4)),
                              t=max(quad[f"r_y{i}"] for i in range(4)))
                extent = _bbox_key(bounds)
                identity = (instruction["font_key"], instruction["font_name"], extent[0], extent[2])
                raw_by_extent.setdefault(identity, []).append((ordinal, instruction))
            for char in page.char_cells:
                if char.rendering_mode in (3, 7) or not char.text.strip() or char.rect.angle_360 != 0:
                    continue
                bbox = char.rect.to_bounding_box().to_bottom_left_origin(
                    page.dimension.height).model_dump(mode="json")
                bounds = _bbox_key(bbox)
                matched = [
                    pair for pair in raw_by_extent.get(
                        (char.font_key, char.font_name, bounds[0], bounds[2]), [])
                    if bbox["b"] - _ORIGIN_TOLERANCE <= pair[1]["base_y0"] <= bbox["t"] + _ORIGIN_TOLERANCE
                ]
                if len(matched) != 1:
                    continue
                ordinal, raw = matched[0]
                font = decoded_fonts.get(raw["font_key"])
                if not font or font["font_base_name"] != raw.get("base_font"):
                    continue
                proof = decode_source_font_character(font, raw.get("char_code"), raw.get("glyph_name"))
                if proof["review_required"]:
                    continue
                result["records"].append({
                    "native_index": char.index, "native_text": char.text, "native_bbox": bbox,
                    "font_name": char.font_name, "font_key": char.font_key,
                    "render_instruction_index": ordinal,
                    "native_raw_alignment": "unique_exact_horizontal_extent_contained_origin",
                    "raw_quad": raw["quad"],
                    "origin": [raw["base_x0"], raw["base_y0"]], "font_decoding": proof,
                })
    finally:
        while parser.has_tasks():
            parser.get_task()
        parser.unload(key)
    return pages


def reconcile_native_font_evidence(native_chars, pdfium_glyphs, page_proof):
    """Return comparison-text overrides bound to exact source character IDs.

    Original records remain unchanged. At least one existing character layer
    must already agree with the source-font decoding. Multiple possible native
    quads or PDFium origins never select an arbitrary character.
    """
    result = {
        "policy": NATIVE_FONT_POLICY, "source_sha256": page_proof.get("source_sha256"),
        "page_no": page_proof.get("page_no"), "native_comparison_text": {},
        "glyph_comparison_text": {}, "corrections": [], "rejections": [],
        "glyph_style_proofs": {},
        "source_text_modified": False, "formula_correctness_verified": False,
    }
    if page_proof.get("review_required"):
        result["rejections"].append({"reason": "unsupported_page_font_evidence"})
        return result
    native_counts = Counter(char["index"] for char in native_chars)
    glyph_counts = Counter(glyph["char_index"] for glyph in pdfium_glyphs)
    native = {char["index"]: char for char in native_chars if native_counts[char["index"]] == 1}
    glyph_buckets = {}
    for glyph in pdfium_glyphs:
        origin = glyph.get("origin")
        if (glyph.get("generated") or not glyph["text"].strip()
                or glyph_counts[glyph["char_index"]] != 1
                or not isinstance(origin, (list, tuple)) or len(origin) != 2
                or not all(math.isfinite(value) for value in origin)):
            continue
        key = (_font_name(glyph.get("font_name", "")),
               math.floor(origin[0] / _ORIGIN_TOLERANCE), math.floor(origin[1] / _ORIGIN_TOLERANCE))
        glyph_buckets.setdefault(key, []).append(glyph)
    record_counts = Counter(record["native_index"] for record in page_proof.get("records", []))
    bindings = []
    for record in page_proof.get("records", []):
        index = record["native_index"]
        char = native.get(index)
        if (char is None or record_counts[index] != 1 or char["text"] != record["native_text"]
                or _bbox_key(char["bbox"]) != _bbox_key(record["native_bbox"])):
            continue
        origin = record["origin"]
        font = _font_name(record["font_name"])
        bx, by = (math.floor(value / _ORIGIN_TOLERANCE) for value in origin)
        candidates = [
            glyph for dx in (-1, 0, 1) for dy in (-1, 0, 1)
            for glyph in glyph_buckets.get((font, bx + dx, by + dy), [])
            if max(abs(glyph["origin"][i] - origin[i]) for i in (0, 1)) <= _ORIGIN_TOLERANCE
        ]
        if len(candidates) != 1:
            result["rejections"].append({"native_index": index, "reason": "nonunique_pdfium_origin"})
            continue
        glyph = candidates[0]
        proof = record["font_decoding"]
        canonical = proof["comparison_text"]
        if proof.get("review_required") or _normalized(canonical) not in {
                _normalized(char["text"]), _normalized(glyph["text"])}:
            result["rejections"].append({"native_index": index, "reason": "font_decoding_not_corroborated"})
            continue
        bindings.append((index, glyph["char_index"]))
        if proof.get("source_weight") is not None:
            result["glyph_style_proofs"][glyph["char_index"]] = {
                "policy": NATIVE_FONT_POLICY, "native_index": index,
                "pdfium_char_index": glyph["char_index"], "pdfium_original_text": glyph["text"],
                "comparison_text": canonical, "source_sha256": page_proof.get("source_sha256"),
                "page_no": page_proof.get("page_no"), "font_key": record["font_key"],
                "font_name": record["font_name"], "origin": list(origin),
                "pdfium_origin": list(glyph["origin"]), "origin_tolerance_points": _ORIGIN_TOLERANCE,
                "font_program_sha256": proof["font_program_sha256"],
                "source_weight": proof["source_weight"],
            }
        if _normalized(char["text"]) == _normalized(glyph["text"]):
            continue
        result["native_comparison_text"][index] = canonical
        result["glyph_comparison_text"][glyph["char_index"]] = canonical
        result["corrections"].append({
            "native_index": index, "pdfium_char_index": glyph["char_index"],
            "native_original_text": char["text"], "pdfium_original_text": glyph["text"],
            "comparison_text": canonical, "font_key": record["font_key"],
            "font_name": record["font_name"], "origin": list(origin),
            "pdfium_origin": list(glyph["origin"]), "origin_tolerance_points": _ORIGIN_TOLERANCE,
            "font_decoding": proof,
        })
    # One PDFium glyph cannot corroborate multiple native source characters.
    reused = {index for index, count in Counter(glyph_id for _, glyph_id in bindings).items() if count > 1}
    if reused:
        for native_id, glyph_id in bindings:
            if glyph_id not in reused:
                continue
            result["native_comparison_text"].pop(native_id, None)
            result["glyph_comparison_text"].pop(glyph_id, None)
            result["glyph_style_proofs"].pop(glyph_id, None)
            result["rejections"].append({"native_index": native_id,
                                         "reason": "reused_pdfium_character"})
        result["corrections"] = [c for c in result["corrections"] if c["pdfium_char_index"] not in reused]
    return result
