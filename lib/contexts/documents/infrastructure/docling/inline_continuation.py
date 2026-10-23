"""Recognize a proven arithmetic continuation as one image, never join LaTeX.

This intentionally narrow route requires audited physical rows, no intervening
prose, and structural agreement between whole-image OCR and every row's OCR.
Independent equations and code stay separate. Suspected continuations whose
source or syntax cannot be proved require review, rather than split publication.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import math
from pathlib import Path
import unicodedata


INLINE_CONTINUATION_POLICY = "native_masked_whole_formula_ast_v3"
_PRESENTATION_STYLES = {"mathrm", "mathit", "mathnormal"}
_SYMBOL_STYLES = {"mathbf", "mathsf", "mathtt"}
_SPACING = {"quad", "qquad", ",", ";", ":", "!", " ", "enspace", "thinspace"}
_FUNCTIONS = {"sin", "cos", "tan", "log", "ln", "exp", "lim", "max", "min"}
_RELATIONS = set("=<>≤≥≠≈≡∈∉⊂⊃⊆⊇≅∼≃≔→←↔⇒⇐⇔")
_CONTINUING = {"+", "-", "−", "×", "⋅", "·"}
_INLINE_LABELS = {"text", "list_item", "footnote", "caption"}


def _canonical(tokens):
    """Keep script/fraction attachment while ignoring bare style groups."""
    result, index = [], 0
    while index < len(tokens):
        token = tokens[index]
        if token[0] == "group":
            contents = _canonical(token[1])
            if (len(contents) > 1 and index + 1 < len(tokens)
                    and tokens[index + 1] in (("symbol", "^"), ("symbol", "_"))):
                result.append(("base_group", contents))
            else:
                result.extend(contents)
        elif token in (("symbol", "^"), ("symbol", "_")):
            if not result or index + 1 >= len(tokens):
                raise ValueError("invalid_script_attachment")
            operand = tokens[index + 1]
            value = _canonical(operand[1]) if operand[0] == "group" else (operand,)
            if not value or any(atom in (("symbol", "^"), ("symbol", "_")) for atom in value):
                raise ValueError("invalid_script_operand")
            base = result.pop()
            sub, sup = None, None
            if base[0] == "script":
                _, base, sub, sup = base
            if token[1] == "_":
                if sub is not None:
                    raise ValueError("duplicate_subscript")
                sub = value
            else:
                if sup is not None:
                    raise ValueError("duplicate_superscript")
                sup = value
            result.append(("script", base, sub, sup))
            index += 1
        else:
            result.append(token)
        index += 1
    return tuple(result)


def _node_tokens(nodes, *, alignment=False):
    from pylatexenc.latex2text import LatexNodes2Text
    from pylatexenc.latexwalker import (
        LatexCharsNode, LatexGroupNode, LatexMacroNode, LatexSpecialsNode,
    )
    result = []
    for node in nodes:
        if isinstance(node, LatexCharsNode):
            result.extend(("symbol", char) for char in node.chars if not char.isspace())
        elif isinstance(node, LatexGroupNode):
            result.append(("group", _node_tokens(node.nodelist, alignment=alignment)))
        elif isinstance(node, LatexSpecialsNode) and alignment and node.specials_chars == "&":
            continue
        elif isinstance(node, LatexMacroNode):
            name = node.macroname
            args = node.nodeargd.argnlist if node.nodeargd else []
            if name in _SPACING:
                if any(arg is not None for arg in args):
                    raise ValueError("unsupported_spacing_argument")
            elif name in _PRESENTATION_STYLES | _SYMBOL_STYLES:
                if len(args) != 1 or not isinstance(args[0], LatexGroupNode):
                    raise ValueError("unsupported_style_argument")
                contents = _node_tokens(args[0].nodelist, alignment=alignment)
                if name in _SYMBOL_STYLES:
                    # Bold, sans-serif and typewriter symbols may denote
                    # different mathematical objects; never erase that identity.
                    result.append(("symbol_style", name, _canonical(contents)))
                else:
                    result.append(("group", contents))
            elif name in {"frac", "sqrt"}:
                if len(args) != 2:
                    raise ValueError("unsupported_math_argument")
                normalized = []
                for arg in args:
                    if arg is None:
                        normalized.append(None)
                    elif isinstance(arg, LatexGroupNode):
                        normalized.append(_canonical(_node_tokens(arg.nodelist)))
                    else:
                        normalized.append(_canonical(_node_tokens([arg])))
                result.append(("macro", name, tuple(normalized)))
            elif not args:
                decoded = LatexNodes2Text().latex_to_text(node.latex_verbatim()).strip()
                if len(decoded) == 1 and (decoded.isalpha() or unicodedata.category(decoded) == "Sm"
                                          or decoded in _CONTINUING):
                    result.append(("symbol", decoded))
                elif name in _FUNCTIONS:
                    result.append(("operator", name))
                else:
                    raise ValueError("unsupported_math_macro")
            else:
                raise ValueError("unsupported_math_macro")
        else:
            raise ValueError("unsupported_latex_node")
    return result


def _parse_nodes(latex):
    from pylatexenc.latexwalker import LatexWalker, LatexWalkerError
    if not isinstance(latex, str) or not latex.strip() or len(latex) > 16000:
        raise ValueError("invalid_formula_text")
    try:
        nodes, position, length = LatexWalker(latex, tolerant_parsing=False).get_latex_nodes()
    except LatexWalkerError as error:
        raise ValueError("invalid_latex_parse") from error
    if position != 0 or length != len(latex):
        raise ValueError("incomplete_latex_parse")
    return nodes


def _relations(ast):
    found = []
    def visit(value):
        if isinstance(value, tuple):
            if len(value) == 2 and value[0] == "symbol" and value[1] in _RELATIONS:
                found.append(value[1])
            else:
                for child in value:
                    visit(child)
    visit(ast)
    return found


def _arithmetic_start(fragment, native_by_id):
    """An audit trigger only; a leading operator never authorizes a join."""
    chosen = fragment.get("native_char_indices", [])
    native = "".join(native_by_id[index]["text"] for index in sorted(chosen)
                     if index in native_by_id).lstrip()
    if native and native[0] in _CONTINUING:
        return True
    try:
        ast = _canonical(_node_tokens(_parse_nodes(fragment.get("transcribed_text"))))
    except (KeyError, TypeError, ValueError):
        return False
    return bool(ast and ast[0] in {("symbol", value) for value in _CONTINUING})


def _independent_equalities(row_latex):
    """Only complete equations with their own lhs prove this exclusion."""
    try:
        for text in row_latex:
            ast = _canonical(_node_tokens(_parse_nodes(text)))
            if _relations(ast) != ["="] or ("symbol", "=") not in ast:
                return False
            position = ast.index(("symbol", "="))
            if position == 0 or position == len(ast) - 1:
                return False
    except (KeyError, TypeError, ValueError):
        return False
    return True


def validate_continuation_syntax(row_latex, whole_latex=None):
    """Conservative pylatexenc AST check; no mathematical simplification."""
    from pylatexenc.latexwalker import LatexCharsNode, LatexEnvironmentNode, LatexMacroNode

    result = {"parser": "pylatexenc.LatexWalker", "review_required": True, "reasons": []}
    try:
        rows = [_canonical(_node_tokens(_parse_nodes(text))) for text in row_latex]
        if len(rows) < 2 or any(not row for row in rows):
            raise ValueError("not_multiple_math_rows")
        first = rows[0]
        if _relations(first) != ["="] or ("symbol", "=") not in first:
            raise ValueError("first_row_requires_one_top_level_equality")
        equality = first.index(("symbol", "="))
        if equality == 0 or equality == len(first) - 1:
            raise ValueError("first_row_incomplete_equality")
        for row in rows[1:]:
            if row[0] not in {("symbol", value) for value in _CONTINUING} or _relations(row):
                raise ValueError("later_row_is_not_arithmetic_continuation")
        for row in rows[:-1]:
            if row[-1] in {("symbol", value) for value in _CONTINUING | {",", ";", ".", "="}}:
                raise ValueError("row_boundary_not_supported")
        result["row_ast"] = rows
        if whole_latex is not None:
            nodes = [node for node in _parse_nodes(whole_latex)
                     if not isinstance(node, LatexCharsNode) or node.chars.strip()]
            if len(nodes) != 1 or not isinstance(nodes[0], LatexEnvironmentNode) \
                    or nodes[0].environmentname != "aligned":
                raise ValueError("whole_formula_requires_one_aligned_environment")
            parts, current = [], []
            for node in nodes[0].nodelist:
                if isinstance(node, LatexMacroNode) and node.macroname == "\\":
                    if node.nodeargd and any(arg is not None for arg in node.nodeargd.argnlist):
                        raise ValueError("unsupported_row_break_argument")
                    parts.append(_canonical(_node_tokens(current, alignment=True)))
                    current = []
                else:
                    current.append(node)
            tail = _canonical(_node_tokens(current, alignment=True))
            if tail:
                parts.append(tail)
            if parts != rows:
                raise ValueError("whole_formula_row_ast_mismatch")
            result.update(whole_row_ast=parts, all_rows_structurally_equal=True)
        result["review_required"] = False
    except (KeyError, TypeError, ValueError) as error:
        result["reasons"].append(str(error))
    return result


def _png(image):
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _masked_crop(source, page, paragraph, fragments, directory):
    import numpy as np
    from PIL import Image
    import pypdfium2 as pdfium
    from .formula_crops import render_formula_crop
    from .inline_partitions import _ordinary_prose_components
    from .math_regions import _box, _box_dict

    formula_ids = set().union(*(set(f["geometry"]["char_indices"]) for f in fragments))
    boxes = [_box(paragraph, page["height"])] + [
        _box(f["geometry"]["bbox"], page["height"]) for f in fragments]
    bounds = _box_dict((min(b[0] for b in boxes), min(b[1] for b in boxes),
                        max(b[2] for b in boxes), max(b[3] for b in boxes)))
    pdf = pdfium.PdfDocument(source)
    try:
        image, crop = render_formula_crop(pdf, page["page_no"], bounds, dpi=480, padding_points=2)
    finally:
        pdf.close()
    source_pixels = np.asarray(image)
    height, width = source_pixels.shape[:2]
    b = crop["crop_bbox"]
    sx, sy = width / (b["r"] - b["l"]), height / (b["t"] - b["b"])
    glyphs = [g for g in page["glyphs"] if g["text"].strip()
              and min(g["bbox"]["r"], b["r"]) > max(g["bbox"]["l"], b["l"])
              and min(g["bbox"]["t"], b["t"]) > max(g["bbox"]["b"], b["b"])]
    ids = {g["char_index"] for g in glyphs}
    if not formula_ids <= ids or any(g.get("generated") for g in glyphs):
        image.close()
        raise ValueError("incomplete_native_crop")
    prose_ids = ids - formula_ids
    prose_proof = _ordinary_prose_components(prose_ids, page["glyphs"]) if prose_ids else []
    if prose_proof is None:
        image.close()
        raise ValueError("unproven_extra_crop_glyphs")
    formula_mask = np.zeros((height, width), bool)
    prose_mask = np.zeros((height, width), bool)
    records = []
    for glyph in glyphs:
        box = glyph["bbox"]
        if box["l"] < b["l"] or box["r"] > b["r"] or box["b"] < b["b"] or box["t"] > b["t"]:
            image.close()
            raise ValueError("crop_cuts_native_glyph")
        rect = [max(0, math.floor((box["l"] - b["l"]) * sx) - 1),
                max(0, math.floor((b["t"] - box["t"]) * sy) - 1),
                min(width, math.ceil((box["r"] - b["l"]) * sx) + 1),
                min(height, math.ceil((b["t"] - box["b"]) * sy) + 1)]
        target = formula_mask if glyph["char_index"] in formula_ids else prose_mask
        target[rect[1]:rect[3], rect[0]:rect[2]] = True
        records.append({"char_index": glyph["char_index"], "pixel_box": rect,
                        "role": "formula" if glyph["char_index"] in formula_ids else "prose"})
    ink = source_pixels.min(axis=2) < 128
    if np.any(ink & formula_mask & prose_mask) or np.any(ink & ~(formula_mask | prose_mask)):
        image.close()
        raise ValueError("unassigned_or_shared_crop_ink")
    masked = source_pixels.copy()
    masked[prose_mask] = 255
    if np.any(np.any(source_pixels != masked, axis=2) & formula_mask):
        image.close()
        raise ValueError("formula_pixels_changed_by_mask")
    ys, xs = np.where(masked.min(axis=2) < 128)
    if not len(xs):
        image.close()
        raise ValueError("empty_masked_formula")
    tight_box = [max(0, int(xs.min()) - 4), max(0, int(ys.min()) - 4),
                 min(width, int(xs.max()) + 5), min(height, int(ys.max()) + 5)]
    masked_image = Image.fromarray(masked)
    tight = masked_image.crop(tuple(tight_box))
    mask_image = Image.fromarray((prose_mask * 255).astype("uint8"))
    artifacts = {}
    for name, value in (("source", image), ("masked", masked_image),
                        ("prose-mask", mask_image), ("recognizer-input", tight)):
        data = _png(value)
        artifacts[name] = {"sha256": hashlib.sha256(data).hexdigest(), "pixel_size": list(value.size)}
        if directory is not None:
            path = Path(directory) / f"{name}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            artifacts[name]["path"] = str(path.resolve())
    proof = {
        "source_sha256": page["source_sha256"], "page_no": page["page_no"],
        "crop": crop, "mask_records": records, "glyph_mask_dilation_pixels": 1, "ink_threshold": 128,
        "formula_char_indices": sorted(formula_ids), "prose_char_indices": sorted(prose_ids),
        "ordinary_prose_proof": prose_proof, "tight_pixel_box": tight_box,
        "original_line_geometry_preserved": True,
        "original_ink_pixels": int(ink.sum()), "prose_ink_removed_pixels": int((ink & prose_mask).sum()),
        "formula_ink_removed_pixels": 0, "unassigned_ink_pixels": 0, "artifacts": artifacts,
    }
    image.close()
    masked_image.close()
    mask_image.close()
    return tight, proof


def recognize_inline_formula_continuation(
        source, page, source_ref, paragraph_bbox, fragments, native_chars, route, reader, *,
        physical_line_views, artifact_directory=None):
    """Return an optional single fragment; callers retain row evidence on failure.

    Recognition is local through the supplied existing reader. A successful
    result still requires the caller's exact prose-preserving source assembly.
    """
    from .inline_partitions import _complete_set, _physical_rows, _scope
    from .math_regions import _box
    result = {
        "policy": INLINE_CONTINUATION_POLICY, "status": "not_applicable", "fragment": None,
        "original_fragments": deepcopy(fragments), "mask_evidence": None,
        "syntax_evidence": None, "reasons": [], "automatic_text_replacement_allowed": False,
    }
    try:
        label = getattr(route.get("original_label"), "value", route.get("original_label"))
        if label == "code" or route.get("route") in {"code_math_probe", "preserve_code"}:
            raise ValueError("code_is_not_a_formula_continuation")
        if route.get("route") != "mixed_formula_probe" and label not in _INLINE_LABELS:
            raise ValueError("not_an_inline_formula_continuation_scope")
        if len(fragments) < 2:
            raise ValueError("not_multiple_formula_fragments")
        native_by_id = {char["index"]: char for char in native_chars}
        hints = [f.get("fragment_id") for f in fragments if _arithmetic_start(f, native_by_id)]
        result["continuation_assessment"] = {
            "arithmetic_start_fragment_ids": hints, "candidate_only": True,
        }
        if not hints:
            raise ValueError("no_arithmetic_row_start")
        # From this point a plausible continuation must not fall back to split
        # publication merely because its source, segmentation or syntax failed.
        result["status"] = "review_required"
        content = bytes(source) if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
        if hashlib.sha256(content).hexdigest() != page["source_sha256"] or page.get("review_required"):
            raise ValueError("source_identity_or_coordinate_mismatch")
        if (route.get("source_ref"), route.get("page_no"), route.get("source_sha256")) != (
                source_ref, page["page_no"], page["source_sha256"]):
            raise ValueError("route_source_identity_mismatch")
        rows = _physical_rows(physical_line_views, page)
        if rows is None or rows[0] != _box(paragraph_bbox, page["height"]):
            raise ValueError("unproven_physical_rows")
        rows = (rows[0], sorted(rows[1], key=lambda row: (
            -row["bounds"][3], row["bounds"][0], row["line_id"])))
        row_positions, formula_ids = [], set()
        expected_scope = (page["source_sha256"], page["page_no"], source_ref)
        for fragment in fragments:
            geometry = fragment["geometry"]
            pixels = geometry.get("pixel_evidence", {})
            if (fragment.get("status") != "transcribed_unverified"
                    or fragment.get("character_consistency", {}).get("review_required") is not False
                    or geometry.get("review_required") is not False or geometry.get("review_reasons")
                    or geometry.get("excluded_char_indices") or _scope(geometry) != expected_scope
                    or _box(geometry["paragraph_bbox"], page["height"]) != rows[0]
                    or any(pixels.get(key) != 0 for key in (
                        "unassigned_original_ink_pixels", "unassigned_added_ink_pixels",
                        "unassigned_removed_ink_pixels", "retained_glyph_ink_removed_pixels"))
                    or any(pixels.get("proposed_edge_ink_pixels", {}).get(edge) != 0
                           for edge in ("left", "right", "top", "bottom"))):
                raise ValueError("unaudited_formula_fragment")
            _, ids = _complete_set(geometry, page["glyphs"], page["height"])
            owners = [i for i, row in enumerate(rows[1]) if ids and ids <= row["ids"]]
            if len(owners) != 1 or formula_ids & ids:
                raise ValueError("fragment_not_one_disjoint_physical_row")
            formula_ids.update(ids)
            row_positions.append(owners[0])
        ordered = sorted(zip(row_positions, fragments), key=lambda value: value[0])
        row_positions = [position for position, _ in ordered]
        fragments = [fragment for _, fragment in ordered]
        result["ordered_row_fragment_ids"] = [fragment["fragment_id"] for fragment in fragments]
        if len(set(row_positions)) == 1:
            result["status"] = "not_applicable"
            raise ValueError("formula_fragments_share_one_physical_row")
        if row_positions != list(range(row_positions[0], row_positions[0] + len(fragments))):
            raise ValueError("nonconsecutive_or_duplicate_formula_rows")
        native_ids = [char["index"] for char in native_chars]
        if len(set(native_ids)) != len(native_ids) or native_ids != sorted(native_ids):
            raise ValueError("invalid_native_character_order")
        used, previous = set(), None
        for fragment in fragments:
            chosen = fragment.get("native_char_indices", [])
            if (not chosen or len(set(chosen)) != len(chosen)
                    or not set(chosen) <= native_by_id.keys() or used & set(chosen)
                    or (previous is not None and min(chosen) <= previous)):
                raise ValueError("invalid_fragment_native_span")
            used.update(chosen)
            previous = max(chosen)
        if any(char["text"].strip() and char["index"] not in used
               for char in native_chars if min(used) <= char["index"] <= max(used)):
            raise ValueError("prose_or_other_math_between_formula_rows")
        if _independent_equalities([f["transcribed_text"] for f in fragments]):
            result["status"] = "not_applicable"
            raise ValueError("independent_equations_have_separate_lhs")
        if not any(_arithmetic_start(f, native_by_id) for f in fragments[1:]):
            result["status"] = "not_applicable"
            raise ValueError("no_arithmetic_start_on_later_physical_rows")
        syntax = validate_continuation_syntax([f["transcribed_text"] for f in fragments])
        result["syntax_evidence"] = syntax
        if syntax["review_required"]:
            raise ValueError("row_syntax_is_not_proven_continuation")
        image, proof = _masked_crop(source, page, paragraph_bbox, fragments, artifact_directory)
        result["mask_evidence"] = proof
        try:
            prediction = reader.read(image)
        finally:
            image.close()
        result["whole_prediction"] = deepcopy(prediction)
        if prediction.get("status") != "transcribed_unverified":
            raise ValueError("whole_formula_ocr_requires_review")
        syntax = validate_continuation_syntax(
            [f["transcribed_text"] for f in fragments], prediction.get("transcribed_text"))
        result["syntax_evidence"] = syntax
        if syntax["review_required"]:
            raise ValueError("whole_formula_ast_does_not_match_rows")
        boxes = [f["geometry"]["bbox"] for f in fragments]
        bbox = {"l": min(b["l"] for b in boxes), "r": max(b["r"] for b in boxes),
                "b": min(b["b"] for b in boxes), "t": max(b["t"] for b in boxes),
                "coord_origin": "BOTTOMLEFT"}
        identifier = f"{source_ref}:whole-continuation"
        result["fragment"] = {
            **prediction, "fragment_id": identifier, "native_char_indices": sorted(used),
            "geometry": {
                "policy": INLINE_CONTINUATION_POLICY, "page_no": page["page_no"],
                "source_sha256": page["source_sha256"], "source_ref": source_ref,
                "fragment_id": identifier, "bbox": bbox, "paragraph_bbox": paragraph_bbox,
                "char_indices": sorted(formula_ids), "review_required": False, "review_reasons": [],
                "status": "prose_masked_whole_formula", "recognition_input_is_masked": True,
            },
            "continuation": {
                "policy": INLINE_CONTINUATION_POLICY, "row_fragment_ids": [
                    f["fragment_id"] for f in fragments],
                "source_line_ids": [rows[1][position]["line_id"] for position in row_positions],
                "syntax_evidence": syntax, "mask_evidence": proof,
                "whole_crop_recognized": True, "latex_strings_joined": False,
            },
        }
        result["status"] = "transcribed_unverified"
    except (KeyError, TypeError, ValueError) as error:
        result["reasons"].append(str(error))
    return result
