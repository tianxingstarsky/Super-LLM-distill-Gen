"""Prove flat native text is already preserved; never recognize or rewrite math.

Call on ONE provenance region, with that region's native characters and backend
substring. The caller owns source/page binding and whole-paragraph review.
These proofs exempt only the returned glyph IDs from *missing transcription*
checks; they do not approve other formulas or authorize relabeling.
"""
from __future__ import annotations

import math
import re
import unicodedata

from .docling_math_regions import is_math_font


NATIVE_PRESERVATION_POLICY = "native_flat_text_and_linked_annotation_preservation_v4"
_MARKERS = frozenset("*∗†‡")
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_TOKEN = re.compile(
    r"(?:[^\W\d_][\w]*|\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?|[-−]?∞|[=+<>])",
    re.UNICODE,
)


def _signal(g):
    return is_math_font(g["font_name"]) or any(unicodedata.category(c) == "Sm" for c in g["text"])


def _box(g):
    b = g["bbox"]
    return tuple(float(b[k]) for k in ("l", "b", "r", "t"))


def _same_row(a, b):
    size = max(a["font_size"], b["font_size"])
    return (abs(a["origin"][1] - b["origin"][1]) <= .08 * size
            and abs(a["font_size"] - b["font_size"]) <= .1 * size)


def _flat_without_scripts(token, all_glyphs):
    """Require a single baseline and reject a base OR a neighboring script."""
    first = token[0]
    if any(not _same_row(first, g) for g in token):
        return False
    left = min(_box(g)[0] for g in token)
    right = max(_box(g)[2] for g in token)
    ordinary_row = [c for c in all_glyphs
                    if c["text"].isalpha() and not is_math_font(c["font_name"])
                    and _same_row(first, c)
                    and left-6*first["font_size"] < c["origin"][0] < right+6*first["font_size"]]
    prose_sandwich = (sum(_box(c)[2] <= left for c in ordinary_row) >= 2
                      and sum(_box(c)[0] >= right for c in ordinary_row) >= 2)
    # Include touching math-font base letters in the structural check, even
    # when PDFium inserted an inter-letter space (e.g. P E with a subscript).
    structural = list(token)
    ids = {g["char_index"] for g in structural}
    changed = True
    while changed:
        changed = False
        for other in all_glyphs:
            if other["char_index"] in ids or not other["text"].isalpha() or not is_math_font(other["font_name"]):
                continue
            for g in structural:
                size = max(g["font_size"], other["font_size"])
                gap = max(_box(g)[0], _box(other)[0]) - min(_box(g)[2], _box(other)[2])
                if (g["text"].isalpha() and is_math_font(g["font_name"])
                        and _same_row(g, other) and -.1*size <= gap <= .15*size):
                    structural.append(other); ids.add(other["char_index"]); changed = True
                    break
    for g in structural:
        l, b, r, t = _box(g)
        for other in all_glyphs:
            if other["char_index"] in ids or not other["text"].isalnum():
                continue
            ol, ob, ore, ot = _box(other)
            size = max(g["font_size"], other["font_size"])
            gap = max(l, ol) - min(r, ore)
            offset = abs(g["origin"][1] - other["origin"][1])
            size_change = min(g["font_size"], other["font_size"]) < .9 * size
            vertical_limit = (2.0 if not prose_sandwich and is_math_font(g["font_name"])
                              and is_math_font(other["font_name"]) else 1.05) * size
            if (gap <= .65 * size
                    and offset < vertical_limit
                    and (size_change or offset > .1 * size)):
                return False
    return True


def _native_span(token, native_chars):
    """Match glyph centers to actual native cells, preserving source order."""
    chosen = []
    infinity = "".join(g["text"] for g in token) in ("−∞", "-∞")
    for g in token:
        l, b, r, t = _box(g)
        x, y = (l + r) / 2, (b + t) / 2
        hits = []
        for index, c in enumerate(native_chars):
            cl, cb, cr, ct = _box(c)
            same = c["text"] == g["text"] or infinity and {c["text"], g["text"]} == {"-", "−"}
            if same and cl - .05 <= x <= cr + .05 and cb - .05 <= y <= ct + .05:
                hits.append(index)
        if len(hits) != 1:
            return None
        chosen.append(hits[0])
    if chosen != sorted(set(chosen)):
        return None
    begin, end = chosen[0], chosen[-1] + 1
    if [i for i in range(begin, end) if native_chars[i]["text"].strip()] != chosen:
        return None
    return begin, end


def _alignment(native_chars, span, backend):
    """Exact ordered text, or a unique exact local source-context match.

    Whitespace may differ across physical lines. No character inventory,
    Unicode compatibility folding, edit distance, or expected text is used.
    """
    native = []
    for i, c in enumerate(native_chars):
        native.extend((ch, i) for ch in c["text"] if not ch.isspace())
    output = [(ch, i) for i, ch in enumerate(backend) if not ch.isspace()]
    ns, bs = "".join(ch for ch, _ in native), "".join(ch for ch, _ in output)
    selected = [i for i, (_, ci) in enumerate(native) if span[0] <= ci < span[1]]
    if not selected or selected != list(range(selected[0], selected[-1] + 1)):
        return None
    a, b = selected[0], selected[-1] + 1
    token = ns[a:b]
    variants = [(token, "exact_native_text")]
    if token == "−∞":
        variants.append(("-∞", "unicode_minus_to_ascii_minus_before_infinity"))
    if ns == bs:
        start, end, method, alias = a, b, "exact_complete_native_sequence", None
    else:
        matches = []
        # Require exact source context on BOTH sides. A paragraph edge can act
        # as one anchor, but the other must contain at least four letters.
        for width in (8, 16, 24, 40):
            left, right = ns[max(0, a-width):a], ns[b:b+width]
            if left and sum(ch.isalpha() for ch in left) < 4:
                continue
            if right and sum(ch.isalpha() for ch in right) < 4:
                continue
            if not left and not right:
                continue
            for variant, alias_name in variants:
                needle = left + variant + right
                positions = [m.start() for m in re.finditer(re.escape(needle), bs)]
                if len(positions) != 1:
                    continue
                pos = positions[0]
                if not left and pos != 0 or not right and pos + len(needle) != len(bs):
                    continue
                matches.append((pos + len(left), pos + len(left) + len(variant), alias_name))
            if matches:
                break
        if len(set(matches)) != 1:
            return None
        start, end, alias = matches[0]
        method = "unique_exact_native_context"
    out_a, out_b = output[start][1], output[end-1][1] + 1
    # Removing line whitespace for alignment must not merge an identifier into
    # neighboring prose, nor split an intact identifier/decimal in the output.
    identifier_char = lambda ch: ch.isalnum() or ch == "_"
    if identifier_char(token[0]):
        if out_a and identifier_char(backend[out_a-1]):
            return None
    if identifier_char(token[-1]):
        if out_b < len(backend) and identifier_char(backend[out_b]):
            return None
    if any(ch.isspace() for ch in backend[out_a:out_b]):
        return None
    return {
        "backend_charspan": [out_a, out_b],
        "backend_text": backend[out_a:out_b],
        "native_text": token, "alignment_method": method, "explicit_alias": alias,
    }


def _annotation_kind(token, glyphs, backend, label, annotation_markers):
    g = token[0]
    if len(token) != 1 or g["text"] not in _MARKERS:
        return None
    index = glyphs.index(g)
    if label == "footnote" and index == 0:
        following = glyphs[1:12]
        letters = [c for c in following if c["text"].isalpha()]
        if len(letters) >= 4 and all(not is_math_font(c["font_name"]) for c in letters):
            return "footnote_symbol"
    if (label != "text"
            or not any(p.get("kind") == "footnote_symbol" and p.get("source_text") == g["text"]
                       and p.get("glyph_ids") and p.get("native_char_ids")
                       for p in annotation_markers)):
        return None
    # Author markers follow at least two ordinary-font name words on a line
    # with no mathematical operators. Email context alone is insufficient.
    before = [c for c in glyphs[:index]
              if abs(c["origin"][1] - g["origin"][1]) < .85 * max(c["font_size"], g["font_size"])
              and _box(c)[2] <= _box(g)[0] + .1]
    if not before:
        return None
    name_letters = [c for c in before if c["text"].isalpha()]
    if not name_letters:
        return None
    nearest_y = name_letters[-1]["origin"][1]
    before = [c for c in before if abs(c["origin"][1] - nearest_y) <= .1 * c["font_size"]]
    # A source author list may put several names on one native line. The
    # annotation belongs to the immediately preceding name, not earlier
    # comma-separated names or their footnote markers.
    separators = [i for i,c in enumerate(before) if c["text"] in ",;"]
    if separators:
        before = before[separators[-1]+1:]
    if not any(c["text"].isalpha() for c in before):
        return None
    if any(_signal(c) and c["text"] not in _MARKERS for c in before):
        return None
    words, current = [], []
    for c in before:
        if current and _box(c)[0] - _box(current[-1])[2] > .22 * c["font_size"]:
            words.append(current); current = []
        if c["text"].isalpha() or c["text"] in ".-'":
            current.append(c)
        elif c["text"] not in _MARKERS:
            return None
    if current:
        words.append(current)
    if len([w for w in words if sum(c["text"].isalpha() for c in w) >= 2]) < 2:
        return None
    base = next(c for c in reversed(before) if c["text"].isalpha())
    rise = g["origin"][1] - base["origin"][1]
    if not .12 * base["font_size"] <= rise <= .85 * base["font_size"]:
        return None
    return ("author_symbol_with_name_and_email" if _EMAIL.search(backend)
            else "source_annotation_after_prose")


def prove_native_preservation(glyphs, native_chars, backend_text, *, label="text",
                              annotation_markers=()):
    """Return narrowly proven native glyph IDs without changing any input.

    PDFium glyphs use char_index/text/font_name/font_size/origin/bbox; native
    Docling cells use index/text/bbox. Inputs must cover ONE provenance region.
    Author symbols additionally require footnote_symbol proofs from the same
    physical page; the caller must enforce that source/page association.
    """
    result = {"policy": NATIVE_PRESERVATION_POLICY, "proven_glyph_ids": [],
              "proofs": [], "rejections": [], "automatic_text_replacement_allowed": False,
              "mathematical_correctness_certified": False}
    try:
        if not isinstance(backend_text, str) or not glyphs or not native_chars:
            raise ValueError("missing_source_alignment")
        gs = [g for g in glyphs if g["text"].strip() and not g.get("generated", False)]
        if len({g["char_index"] for g in gs}) != len(gs):
            raise ValueError("duplicate_glyph_ids")
        if len({c["index"] for c in native_chars}) != len(native_chars):
            raise ValueError("duplicate_native_ids")
        for g in gs:
            if (len(g["text"]) != 1 or g["origin"] is None or g["font_size"] <= 0
                    or not all(math.isfinite(v) for v in (*_box(g), *g["origin"], g["font_size"]))):
                raise ValueError("unsupported_glyph_geometry")
        for c in native_chars:
            if not isinstance(c["text"], str) or not all(math.isfinite(v) for v in _box(c)):
                raise ValueError("unsupported_native_geometry")
    except (KeyError, TypeError, ValueError) as exc:
        result["rejections"].append({"reason": str(exc)})
        return result
    # Runs stop at a source space, baseline/size change, or horizontal gap.
    runs, current = [], []
    for g in gs:
        if current:
            last = current[-1]
            between = [x for x in glyphs if last["char_index"] < x["char_index"] < g["char_index"]]
            gap = _box(g)[0] - _box(last)[2]
            if (any(x["text"].isspace() for x in between) or not _same_row(last, g)
                    or gap > .3 * max(last["font_size"], g["font_size"]) or gap < -.2 * last["font_size"]):
                runs.append(current); current = []
        current.append(g)
    if current:
        runs.append(current)
    tokens = [[g] for g in gs if g["text"] in _MARKERS]
    for run in runs:
        text = "".join(g["text"] for g in run)
        # A literal expression/identifier such as A+B or Python+NumPy needs no
        # OCR when both parsers agree and there are no scripts. Treat the
        # complete run atomically; never exempt an unmatched internal operator.
        if (re.fullmatch(r"\w+(?:\+\w+)+", text, re.UNICODE)
                and all(not is_math_font(g["font_name"]) for g in run)):
            tokens.append(run)
        for match in _TOKEN.finditer(text):
            # Don't certify a prefix/suffix of an identifier or decimal.
            a, b = match.span()
            if a and (text[a-1].isalnum() or text[a-1] in "_."):
                continue
            if b < len(text) and (text[b].isalnum() or text[b] in "_"):
                continue
            tokens.append(run[a:b])
    seen = set()
    for token in tokens:
        ids = tuple(g["char_index"] for g in token)
        ordinary_single_letter = len(token) == 1 and token[0]["text"].isalpha()
        if ordinary_single_letter and not _signal(token[0]):
            g = token[0]
            # A plain-font isolated scalar needs surrounding prose on its own
            # baseline. Bare stacked letters are not evidence of flat text.
            ordinary_single_letter = sum(
                c["text"].isalpha() and not is_math_font(c["font_name"])
                and _same_row(g, c)
                and abs(c["origin"][0]-g["origin"][0]) < 6*g["font_size"]
                for c in gs if c["char_index"] != g["char_index"]
            ) >= 3
        if ids in seen or not (ordinary_single_letter or any(_signal(g) or g["text"] in _MARKERS for g in token)):
            continue
        seen.add(ids)
        kind = _annotation_kind(token, gs, backend_text, label, annotation_markers)
        if kind is None:
            if token[0]["text"] in _MARKERS or not _flat_without_scripts(token, gs):
                result["rejections"].append({"glyph_ids": list(ids), "reason": "script_or_annotation_not_proven"})
                continue
            kind = "flat_literal"
        span = _native_span(token, native_chars)
        alignment = _alignment(native_chars, span, backend_text) if span else None
        if not alignment:
            result["rejections"].append({"glyph_ids": list(ids), "reason": "native_backend_alignment_not_unique"})
            continue
        result["proofs"].append({"kind": kind, "glyph_ids": list(ids),
                                 "source_text": "".join(g["text"] for g in token),
                                 "native_char_ids": [c["index"] for c in native_chars[span[0]:span[1]]],
                                 "geometry": "explicit_annotation_marker" if kind != "flat_literal" else "one_baseline_no_neighbor_script",
                                 "glyph_native_alias": ("unicode_minus_to_ascii_minus_before_infinity"
                                      if "".join(g["text"] for g in token) == "−∞"
                                      and alignment["native_text"] == "-∞" else None),
                                 **alignment})
        result["proven_glyph_ids"].extend(ids)
    # A detector can include the prose comma after an already-preserved scalar.
    # Keep punctuation as separate exact evidence, never as part of a formula
    # or a route for accepting brackets, operators, scripts, or missing glyphs.
    flat_end_ids = {p["glyph_ids"][-1] for p in result["proofs"] if p["kind"] == "flat_literal"}
    for index, g in enumerate(gs):
        if (g["text"] not in ",;." or is_math_font(g["font_name"]) or index == 0
                or gs[index-1]["char_index"] not in flat_end_ids):
            continue
        previous = gs[index-1]
        gap = _box(g)[0] - _box(previous)[2]
        if not _same_row(previous, g) or not -.05*g["font_size"] <= gap <= .35*g["font_size"]:
            continue
        following = gs[index+1:index+13]
        letters = [c for c in following if c["text"].isalpha()]
        if (len(letters) < 4 or not following or not following[0]["text"].isalpha()
                or any(is_math_font(c["font_name"]) or not _same_row(g, c) for c in letters)):
            continue
        between = [c for c in glyphs
                   if g["char_index"] < c["char_index"] < following[0]["char_index"]]
        if not any(c["text"].isspace() for c in between):
            continue
        span = _native_span([g], native_chars)
        alignment = _alignment(native_chars, span, backend_text) if span else None
        if alignment is None:
            continue
        result["proofs"].append({
            "kind": "adjacent_prose_punctuation", "glyph_ids": [g["char_index"]],
            "source_text": g["text"], "native_char_ids": [c["index"] for c in native_chars[span[0]:span[1]]],
            "preceding_proven_glyph_id": previous["char_index"],
            "geometry": "same_baseline_after_proven_flat_literal_before_prose",
            "glyph_native_alias": None, **alignment,
        })
        result["proven_glyph_ids"].append(g["char_index"])
    result["proven_glyph_ids"] = sorted(set(result["proven_glyph_ids"]))
    return result
