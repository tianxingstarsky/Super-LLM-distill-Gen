"""Insert local formula transcriptions only where native text proves the boundary.

Detection and recognition are separate from this module. It never guesses
missing prose, joins nearby formulas, or uses the recognized LaTeX to locate a
source span. Every character outside a replaced span is retained verbatim.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter


INLINE_ASSEMBLY_POLICY = "native_character_masked_alignment_v4"


def _inline_latex(latex):
    """Require one delimiter-free inline body before adding the math wrapper."""
    if not isinstance(latex, str) or not latex.strip():
        return False
    if any(unicodedata.category(char) in ("Cc", "Zl", "Zp") for char in latex):
        return False
    if re.search(r"(?<!\\)(?:\\\\)*\\[()[\]]", latex):
        return False
    for index, char in enumerate(latex):
        if char != "$":
            continue
        slashes, cursor = 0, index - 1
        while cursor >= 0 and latex[cursor] == "\\":
            slashes += 1
            cursor -= 1
        if slashes % 2 == 0:
            return False
    return True


def _normalized(text):
    # Keep a map back to original offsets, including ligatures represented by
    # more than one Unicode code point. Do not normalize the published prose.
    chars, offsets = [], []
    for offset, char in enumerate(text):
        for value in unicodedata.normalize("NFKC", char):
            if not value.isspace():
                chars.append(value)
                offsets.append(offset)
    return "".join(chars), offsets


def _unique_alignment(text, anchors):
    """Locate variable math spans between exact, ordered prose anchors.

    Exhaustive search is bounded. Ambiguity or excessive work rejects a patch,
    rather than choosing the first occurrence of a repeated word.
    """
    if not text.startswith(anchors[0]) or not text.endswith(anchors[-1]):
        return None
    solutions, visits = [], 0

    def visit(index, start, spans):
        nonlocal visits
        visits += 1
        if visits > 256 or len(solutions) > 1:
            return
        if index == len(anchors) - 1:
            stop = len(text) - len(anchors[index])
            if stop > start:
                solutions.append(spans + [(start, stop)])
            return
        anchor = anchors[index]
        # Two neighboring formula regions have no independent textual boundary.
        if not anchor:
            return
        position = text.find(anchor, start + 1)
        while position >= 0 and visits <= 256 and len(solutions) < 2:
            visit(index + 1, position + len(anchor), spans + [(start, position)])
            position = text.find(anchor, position + 1)

    visit(1, len(anchors[0]), [])
    return solutions[0] if len(solutions) == 1 and visits <= 256 else None


def _line_end_hyphens(native_chars, regions):
    """Find native-only word hyphens with evidence of a physical line break.

    These are candidates for anchor matching only. The strict alignment always
    runs first, and the backend's published prose is never dehyphenated here.
    """
    def box(char):
        value = char.get("bbox")
        if not isinstance(value, dict) or value.get("coord_origin", "BOTTOMLEFT") != "BOTTOMLEFT":
            return None
        try:
            result = tuple(float(value[key]) for key in ("l", "b", "r", "t"))
        except (ValueError, TypeError, KeyError):
            return None
        return result if all(math.isfinite(v) for v in result) and result[2] > result[0] and result[3] > result[1] else None

    visible = [(position, char, box(char)) for position, char in enumerate(native_chars) if char["text"].strip()]
    discarded = set()
    for i in range(1, len(visible) - 1):
        position, char, hb = visible[i]
        _, previous, pb = visible[i - 1]
        _, following, nb = visible[i + 1]
        if (char["text"] not in ("-", "\u2010") or not hb or not pb or not nb
                or not previous["text"].isalpha() or not following["text"].isalpha()
                or any(begin <= position < end for begin, end, _ in regions)):
            continue
        height = max(pb[3] - pb[1], nb[3] - nb[1])
        hyphen_mid = (hb[1] + hb[3]) / 2
        # Tight adjacency on the old line, a lower next line, and a restart to
        # the left distinguish wrapping from an ordinary interior hyphen.
        if not (pb[1] <= hyphen_mid <= pb[3]
                and -.2 * height <= hb[0] - pb[2] <= .6 * height
                and nb[3] < pb[1] + .25 * height
                and nb[0] < pb[0] - height):
            continue
        if any(b and other["index"] != char["index"] and b[0] >= hb[2]
               and pb[1] <= (b[1] + b[3]) / 2 <= pb[3]
               for _, other, b in visible):
            continue
        if any(b and other["index"] != following["index"] and b[2] <= nb[0]
               and nb[1] <= (b[1] + b[3]) / 2 <= nb[3]
               for _, other, b in visible):
            continue
        discarded.add(position)
    return discarded


def assemble_inline_formulas(text, native_chars, fragments):
    """Return an atomic paragraph patch, or preserve the original for review.

    ``native_chars`` are in the native parser's reading order with unique
    ``index`` and ``text`` fields. Each fragment supplies those native indices,
    an independent crop review status, and a completed local transcription.
    """
    result = {
        "policy": INLINE_ASSEMBLY_POLICY, "status": "review_required",
        "backend_text": text, "text": text, "patches": [],
        "review_reasons": [], "prose_preserved": False,
        "alignment_method": "strict", "discarded_native_hyphen_ids": [],
    }

    def fail(reason):
        result["review_reasons"].append(reason)
        return result

    if not fragments:
        return fail("no_inline_formula_fragments")
    ids = [char["index"] for char in native_chars]
    if len(set(ids)) != len(ids):
        return fail("duplicate_native_character_ids")
    positions = {value: index for index, value in enumerate(ids)}
    regions = []
    for fragment in fragments:
        if fragment.get("review_required") or fragment.get("status") != "transcribed_unverified":
            return fail("inline_formula_fragment_requires_review")
        latex = fragment.get("transcribed_text")
        chosen = fragment.get("native_char_indices", [])
        if not _inline_latex(latex):
            return fail("invalid_inline_latex_body")
        if (not chosen
                or len(chosen) != len(set(chosen)) or any(index not in positions for index in chosen)):
            return fail("invalid_inline_formula_fragment")
        begin, end = min(positions[index] for index in chosen), max(positions[index] for index in chosen) + 1
        selected = set(chosen)
        if any(char["text"].strip() and char["index"] not in selected for char in native_chars[begin:end]):
            return fail("noncontiguous_native_formula")
        regions.append((begin, end, fragment))
    regions.sort(key=lambda region: region[0])
    if any(first[1] > second[0] for first, second in zip(regions, regions[1:])):
        return fail("overlapping_inline_formulas")

    def anchors_without(discarded):
        anchors, cursor = [], 0
        for begin, end, _ in regions:
            anchors.append(_normalized("".join(native_chars[i]["text"] for i in range(cursor, begin)
                                                 if i not in discarded))[0])
            cursor = end
        anchors.append(_normalized("".join(native_chars[i]["text"] for i in range(cursor, len(native_chars))
                                             if i not in discarded))[0])
        return anchors

    normalized, offsets = _normalized(text)
    spans = _unique_alignment(normalized, anchors_without(set()))
    exact_native = False
    if spans is None:
        # Adjacent physical math rows need not have prose between them. When the
        # complete source character sequence agrees, its offsets are an exact
        # boundary witness; no mathematical continuation is inferred.
        widths = [len(_normalized(char["text"])[0]) for char in native_chars]
        native_sequence = "".join(_normalized(char["text"])[0] for char in native_chars)
        if native_sequence == normalized:
            positions = [0]
            for width in widths:
                positions.append(positions[-1] + width)
            candidate_spans = [(positions[begin], positions[end]) for begin, end, _ in regions]
            separated = all(
                second[0] > first[1]
                or (0 < first[1] <= second[0] < len(offsets)
                    and offsets[second[0]] > offsets[first[1] - 1] + 1)
                for first, second in zip(candidate_spans, candidate_spans[1:])
            )
            if all(stop > start for start, stop in candidate_spans) and separated:
                spans = candidate_spans
                exact_native = True
    discarded = set()
    if spans is None:
        discarded = _line_end_hyphens(native_chars, regions)
        if discarded:
            spans = _unique_alignment(normalized, anchors_without(discarded))
    if spans is None:
        return fail("ambiguous_or_changed_prose")
    patches = []
    for (begin, end, fragment), (start, stop) in zip(regions, spans):
        lo, hi = offsets[start], offsets[stop - 1] + 1
        # A ligature may not be split across a patch boundary.
        if (start > 0 and offsets[start - 1] == lo
                or stop < len(offsets) and offsets[stop] < hi):
            return fail("split_unicode_character")
        native_text = "".join(char["text"] for char in native_chars[begin:end])
        # Reordered scripts are common, but missing/extra source characters are
        # not evidence that all material between two prose anchors is a formula.
        if Counter(_normalized(text[lo:hi])[0]) != Counter(_normalized(native_text)[0]):
            return fail("source_formula_characters_changed")
        patches.append({
            "fragment_id": fragment.get("fragment_id"), "charspan": [lo, hi],
            "backend_text": text[lo:hi],
            "native_text": native_text,
            "native_char_indices": [char["index"] for char in native_chars[begin:end]],
            "replacement": "$" + fragment["transcribed_text"].strip() + "$",
        })
    output, prose, cursor = [], [], 0
    for patch in patches:
        lo, hi = patch["charspan"]
        unchanged = text[cursor:lo]
        output.extend((unchanged, patch["replacement"]))
        prose.append(unchanged)
        cursor = hi
    output.append(text[cursor:])
    prose.append(text[cursor:])
    result.update(
        status="transcribed_unverified", text="".join(output), patches=patches,
        prose_preserved=True,
        alignment_method=("native_line_end_hyphen_anchor_fallback" if discarded
                          else "exact_native_sequence" if exact_native else "strict"),
        discarded_native_hyphen_ids=[native_chars[i]["index"] for i in sorted(discarded)],
        unchanged_prose_sha256=hashlib.sha256("".join(prose).encode("utf-8")).hexdigest(),
    )
    return result


def native_paragraph_chars(page_chars, bbox):
    """Select native characters by center from one physical paragraph."""
    return [char for char in page_chars
            if bbox["l"] <= (char["bbox"]["l"] + char["bbox"]["r"]) / 2 <= bbox["r"]
            and bbox["b"] <= (char["bbox"]["b"] + char["bbox"]["t"]) / 2 <= bbox["t"]]


def read_native_character_pages(source):
    """Use the same official native backend as the pinned Docling worker."""
    from io import BytesIO
    from pathlib import Path
    from docling_parse.pdf_parser import (
        ContentConfig, ContentLevel, DoclingThreadedPdfParser, ThreadedPdfParserConfig,
    )
    config = ContentConfig(
        char_cells_content_level=ContentLevel.COMPUTE_AND_MATERIALIZE,
        word_cells_content_level=ContentLevel.SKIP,
        line_cells_content_level=ContentLevel.SKIP,
        shapes_content_level=ContentLevel.SKIP,
        bitmaps_content_level=ContentLevel.SKIP,
    )
    parser = DoclingThreadedPdfParser(parser_config=ThreadedPdfParserConfig(
        loglevel="fatal", threads=1, page_content_config=config, render_config=None))
    key = parser.load(BytesIO(Path(source).read_bytes()))
    pages = {}
    try:
        for response in parser.iterate_results():
            page = response.get_page()
            pages[response.page_number] = [
                {"index": cell.index, "text": cell.text,
                 "bbox": cell.rect.to_bounding_box().to_bottom_left_origin(
                     page.dimension.height).model_dump(mode="json")}
                for cell in page.char_cells
                if cell.rendering_mode not in (3, 7)
            ]
    finally:
        while parser.has_tasks():
            parser.get_task()
        parser.unload(key)
    return pages
