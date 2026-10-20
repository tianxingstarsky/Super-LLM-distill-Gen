"""Retain a mixed detector box as container evidence after a native partition.

Only probe routes may use this classification. It neither selects competing
formula candidates nor approves OCR, prose replacement, relabeling, or joining.
"""
from __future__ import annotations

import math
import unicodedata

from .docling_inline_routing import _multiword_runs
from .docling_math_regions import _box, is_math_font


INLINE_PARTITION_POLICY = "native_formula_container_partition_v2"
_PROBE_ROUTES = {"mixed_formula_probe", "code_math_probe"}
_BOUNDARY_REASONS = {
    "ordinary_text_formula_boundary_ambiguous",
    "ordinary_delimiter_boundary_ambiguous",
}


def _math(glyph):
    return is_math_font(glyph["font_name"]) or any(
        unicodedata.category(char) == "Sm" for char in glyph["text"]
    )


def _contains(outer, inner):
    return (outer[0] <= inner[0] and outer[1] <= inner[1]
            and outer[2] >= inner[2] and outer[3] >= inner[3])


def _intersects(first, second):
    return (min(first[2], second[2]) > max(first[0], second[0])
            and min(first[3], second[3]) > max(first[1], second[1]))


def _ids(geometry):
    values = geometry.get("char_indices")
    if (not isinstance(values, list) or not values
            or any(type(value) is not int or value < 0 for value in values)
            or len(set(values)) != len(values)):
        return None
    return set(values)


def _scope(geometry):
    return (geometry.get("source_sha256"), geometry.get("page_no"),
            geometry.get("source_ref"))


def _complete_set(geometry, glyphs, height, *, proposed=False):
    key = "proposed_bbox" if proposed else "bbox"
    bounds = _box(geometry.get(key, geometry["bbox"]), height)
    ids = _ids(geometry)
    visible = [g for g in glyphs if g["text"].strip()
               and _intersects(_box(g["bbox"], height), bounds)]
    if (ids is None or {g["char_index"] for g in visible} != ids
            or any(not _contains(bounds, _box(g["bbox"], height)) for g in visible)):
        return None, None
    return bounds, ids


def _ordinary_prose_components(extra_ids, page_glyphs):
    """Require contiguous ordinary-font phrases, including their punctuation.

    A roman number, isolated script, operator, or lone word is not enough.
    Multiword evidence selects a conservative subset of prose; it does not
    recognize prose semantically or supply any replacement text.
    """
    extra = [g for g in page_glyphs if g["char_index"] in extra_ids]
    if not extra:
        return None
    for glyph in extra:
        if (_math(glyph) or glyph.get("generated")
                or any(not (char.isalpha() or unicodedata.category(char).startswith("P"))
                       for char in glyph["text"] if not char.isspace())):
            return None
    by_id = {g["char_index"]: g for g in page_glyphs}
    components, current = [], []
    for glyph in extra:
        adjacent = False
        if current:
            previous = current[-1]
            size = max(previous["font_size"], glyph["font_size"])
            between = [g for index, g in by_id.items()
                       if previous["char_index"] < index < glyph["char_index"]]
            adjacent = (
                all(not g["text"].strip() for g in between)
                and abs(previous["font_size"] - glyph["font_size"]) <= .1 * size
                and abs(previous["origin"][1] - glyph["origin"][1]) <= .12 * size
                and -.25 * size <= glyph["bbox"]["l"] - previous["bbox"]["r"] <= size
            )
        if not adjacent and current:
            components.append(current)
            current = []
        current.append(glyph)
    if current:
        components.append(current)
    proofs = []
    for component in components:
        lo, hi = component[0]["char_index"], component[-1]["char_index"]
        span = [g for g in page_glyphs if lo <= g["char_index"] <= hi]
        words = _multiword_runs(span)
        if not words:
            return None
        proofs.append({"char_indices": [g["char_index"] for g in component],
                       "ordinary_multiword_runs": words})
    return proofs


def _physical_rows(views, page):
    """Revalidate native row membership; a detector view name is not evidence."""
    from .docling_inline_lines import INLINE_LINE_POLICY

    if not isinstance(views, list) or len(views) < 2:
        return None
    height, glyphs = page["height"], page["glyphs"]
    paragraph, rows, covered, line_ids = None, [], set(), set()
    for view in views:
        if (view.get("policy") != INLINE_LINE_POLICY
                or view.get("physical_only") is not True
                or view.get("continuation_inferred") is not False
                or view.get("review_required") is not False or view.get("review_reasons")
                or view.get("physical_line_count") != 1
                or (view.get("source_sha256"), view.get("page_no")) != (
                    page["source_sha256"], page["page_no"])):
            return None
        source_ids = view.get("source_line_ids")
        if (not isinstance(source_ids, list) or len(source_ids) != 1
                or not isinstance(source_ids[0], str) or not source_ids[0]
                or source_ids[0] in line_ids):
            return None
        line_ids.add(source_ids[0])
        current_paragraph = _box(view["paragraph_bbox"], height)
        if paragraph is not None and current_paragraph != paragraph:
            return None
        paragraph = current_paragraph
        bounds, ids = _complete_set(
            {"bbox": view["bbox"], "char_indices": view.get("pdfium_char_indices")},
            glyphs, height,
        )
        if not ids or ids & covered:
            return None
        if any(_intersects(bounds, row["bounds"]) for row in rows):
            return None
        covered.update(ids)
        rows.append({"line_id": source_ids[0], "bounds": bounds, "ids": ids})
    paragraph_ids = {g["char_index"] for g in glyphs if g["text"].strip()
                     and _intersects(_box(g["bbox"], height), paragraph)}
    if covered != paragraph_ids or any(
            g.get("generated") for g in glyphs if g["char_index"] in covered):
        return None
    return paragraph, rows


def _physical_contexts(candidates, page, views, existing):
    """Prefer complete row partitions over clean boxes spanning physical rows.

    Row children may extend beyond the broad detector rectangle. No candidate
    with unassigned raster ink or a clipping/script warning can be demoted.
    This proof changes detector context only, never mathematical semantics.
    """
    try:
        validated = _physical_rows(views, page)
    except (KeyError, TypeError, ValueError):
        validated = None
    if validated is None:
        return [], []
    from .docling_inline_candidates import select_inline_formula_candidates

    paragraph, rows = validated
    glyphs, height = page["glyphs"], page["height"]
    all_row_ids = set().union(*(row["ids"] for row in rows))
    proofs, rejections = [], []
    for index, candidate in enumerate(candidates):
        if index in existing:
            continue
        geometry = candidate.get("geometry", {})
        full_ids = _ids(geometry)
        touched = [row for row in rows if full_ids and row["ids"] & full_ids]
        if len(touched) < 2:
            continue
        scope = _scope(geometry)
        reason = None
        reasons = geometry.get("review_reasons", [])
        if (geometry.get("excluded_char_indices")
                or (geometry.get("review_required") is not False and not reasons)
                or not set(reasons) <= _BOUNDARY_REASONS):
            reason = "unsafe_geometry_requires_review"
        elif (scope[:2] != (page["source_sha256"], page["page_no"])
              or not isinstance(scope[2], str) or not scope[2]):
            reason = "source_scope_mismatch"
        pixels = geometry.get("pixel_evidence") or {}
        if reason is None and (pixels.get("unassigned_original_ink_pixels") != 0
                              or pixels.get("original_ink_pixels", 0) <= 0):
            reason = "unassigned_container_raster_ink"
        try:
            bounds, complete_ids = _complete_set(
                geometry, glyphs, height, proposed=geometry.get("review_required") is not False)
            paragraph_matches = _box(geometry["paragraph_bbox"], height) == paragraph
        except (KeyError, TypeError, ValueError):
            bounds, complete_ids, paragraph_matches = None, None, False
        if reason is None and (not complete_ids or not complete_ids <= all_row_ids
                               or not paragraph_matches):
            reason = "incomplete_physical_context_glyph_set"
        children, child_indices, child_rows = [], [], []
        if reason is None:
            for child_index, child in enumerate(candidates):
                proof = child.get("geometry", {})
                child_pixels = proof.get("pixel_evidence") or {}
                if (child_index == index or child_index in existing
                        or proof.get("review_required") is not False
                        or proof.get("review_reasons") or proof.get("excluded_char_indices")
                        or _scope(proof) != scope
                        or child_pixels.get("unassigned_original_ink_pixels") != 0
                        or child_pixels.get("original_ink_pixels", 0) <= 0):
                    continue
                try:
                    _, child_ids = _complete_set(proof, glyphs, height)
                    same_paragraph = _box(proof["paragraph_bbox"], height) == paragraph
                except (KeyError, TypeError, ValueError):
                    continue
                owners = [row for row in touched if child_ids and child_ids <= row["ids"]]
                if same_paragraph and len(owners) == 1:
                    children.append(child)
                    child_indices.append(child_index)
                    child_rows.append(owners[0]["line_id"])
            selected = select_inline_formula_candidates(children)
            if selected["review_indices"]:
                reason = "competing_single_line_candidates"
            else:
                chosen = selected["selected_indices"]
                safe_indices = [child_indices[i] for i in chosen]
                covered = set().union(*(_ids(children[i]["geometry"]) for i in chosen))
                row_ids = set().union(*(row["ids"] for row in touched))
                math_ids = {g["char_index"] for g in glyphs
                            if g["char_index"] in row_ids and _math(g)}
                extra = row_ids - covered
                prose = _ordinary_prose_components(extra, glyphs) if extra else []
                if not chosen or not math_ids or not math_ids <= covered:
                    reason = "uncovered_physical_row_math"
                elif prose is None:
                    reason = "physical_row_extras_not_proven_ordinary_prose"
        if reason is not None:
            rejections.append({"index": index, "reason": reason,
                               "partition_kind": "physical_line_context"})
            continue
        proofs.append({
            "index": index, "partition_kind": "physical_line_context",
            "source_sha256": scope[0], "page_no": scope[1], "source_ref": scope[2],
            "source_line_ids": [row["line_id"] for row in touched],
            "single_row_safe_indices": safe_indices,
            "safe_candidate_rows": [
                {"index": child_indices[i], "source_line_id": child_rows[i]} for i in chosen],
            "full_source_char_indices": sorted(complete_ids),
            "covered_source_char_indices": sorted(complete_ids & covered),
            "math_char_indices": sorted(complete_ids & math_ids),
            "ordinary_prose_char_indices": sorted(complete_ids & extra),
            "accounted_row_char_indices": sorted(row_ids),
            "row_formula_char_indices": sorted(covered),
            "row_ordinary_prose_char_indices": sorted(extra),
            "ordinary_prose_components": prose,
            "container_bbox": dict(zip(("l", "b", "r", "t"), bounds),
                                   coord_origin="BOTTOMLEFT"),
            "original_ink_pixels": pixels["original_ink_pixels"],
            "unassigned_original_ink_pixels": pixels["unassigned_original_ink_pixels"],
            "all_native_glyphs_partitioned": complete_ids <= covered | extra,
            "all_physical_row_glyphs_partitioned": row_ids == covered | extra,
            "container_evidence_retained": True, "continuation_inferred": False,
        })
    return proofs, rejections


def classify_inline_formula_containers(candidates, page, *, route, physical_line_views=None):
    """Return container indices and complete glyph partitions, without mutation.

    ``page`` is full PDFium page evidence, including native whitespace.
    Candidate geometry uses bottom-left PDF points and source glyph IDs.
    ``physical_line_views`` may contain validated native line records from
    ``select_inline_line_views``. They permit a complete physical-row partition
    without requiring each child rectangle to fit inside a broad context box.
    Callers must retain container evidence and still require exact paragraph
    assembly, candidate conflict resolution, and uncovered-math checks.
    """
    if isinstance(route, dict):
        route = route.get("route")
    result = {
        "policy": INLINE_PARTITION_POLICY, "container_indices": [], "proofs": [],
        "remaining_unsafe_indices": [], "rejections": [],
        "automatic_text_replacement_allowed": False, "continuation_inferred": False,
    }
    unsafe = [i for i, candidate in enumerate(candidates)
              if candidate.get("geometry", {}).get("review_required") is not False
              or candidate.get("geometry", {}).get("review_reasons")]
    result["remaining_unsafe_indices"] = list(unsafe)
    if route not in _PROBE_ROUTES:
        return result
    glyphs = page.get("glyphs", [])
    try:
        ids = [g["char_index"] for g in glyphs]
        if (page.get("review_required") or not page.get("source_sha256")
                or type(page.get("page_no")) is not int
                or any(type(index) is not int or index < 0 for index in ids)
                or len(set(ids)) != len(ids) or ids != sorted(ids)):
            raise ValueError()
        for glyph in glyphs:
            if not glyph["text"].strip():
                continue
            _box(glyph["bbox"], page["height"])
            if (not isinstance(glyph["font_name"], str)
                    or len(glyph["origin"]) != 2
                    or not all(math.isfinite(v) for v in glyph["origin"])
                    or not math.isfinite(glyph["font_size"]) or glyph["font_size"] <= 0):
                raise ValueError()
    except (KeyError, TypeError, ValueError):
        result["rejections"] = [{"index": i, "reason": "invalid_native_partition_evidence"}
                                for i in unsafe]
        return result
    for index in unsafe:
        geometry = candidates[index].get("geometry", {})
        reasons = geometry.get("review_reasons", [])
        reason = None
        scope = _scope(geometry)
        if (not reasons or not set(reasons) <= _BOUNDARY_REASONS
                or geometry.get("excluded_char_indices")):
            reason = "unsafe_geometry_requires_review"
        elif (scope[:2] != (page["source_sha256"], page["page_no"])
              or not isinstance(scope[2], str) or not scope[2]):
            reason = "source_scope_mismatch"
        pixels = geometry.get("pixel_evidence") or {}
        if reason is None and (pixels.get("unassigned_original_ink_pixels") != 0
                               or pixels.get("original_ink_pixels", 0) <= 0):
            # Native character coverage cannot account for an unassigned rule,
            # drawing, or mathematical mark. Keep that broad box in review.
            reason = "unassigned_container_raster_ink"
        try:
            bounds, full_ids = _complete_set(geometry, glyphs, page["height"], proposed=True)
        except (KeyError, TypeError, ValueError):
            bounds, full_ids = None, None
        if reason is None and full_ids is None:
            reason = "incomplete_container_glyph_set"
        covered, children = set(), []
        if reason is None:
            for child_index, child in enumerate(candidates):
                proof = child.get("geometry", {})
                if (proof.get("review_required") is not False or proof.get("review_reasons")
                        or _scope(proof) != scope):
                    continue
                try:
                    child_box, child_ids = _complete_set(proof, glyphs, page["height"])
                except (KeyError, TypeError, ValueError):
                    continue
                if (child_ids and child_ids <= full_ids and _contains(bounds, child_box)):
                    covered.update(child_ids)
                    children.append(child_index)
            math_ids = {g["char_index"] for g in glyphs
                        if g["char_index"] in full_ids and _math(g)}
            if not children or not math_ids or not math_ids <= covered:
                reason = "uncovered_container_math"
            else:
                extra = full_ids - covered
                prose = _ordinary_prose_components(extra, glyphs)
                if prose is None:
                    reason = "extra_glyphs_not_proven_ordinary_prose"
        if reason is not None:
            result["rejections"].append({"index": index, "reason": reason})
            continue
        result["container_indices"].append(index)
        result["proofs"].append({
            "index": index, "source_sha256": scope[0], "page_no": scope[1],
            "source_ref": scope[2], "contained_safe_indices": children,
            "full_source_char_indices": sorted(full_ids),
            "covered_source_char_indices": sorted(covered),
            "math_char_indices": sorted(math_ids),
            "ordinary_prose_char_indices": sorted(extra),
            "ordinary_prose_components": prose,
            "container_bbox": geometry.get("proposed_bbox", geometry["bbox"]),
            "original_ink_pixels": pixels["original_ink_pixels"],
            "unassigned_original_ink_pixels": pixels["unassigned_original_ink_pixels"],
            "all_native_glyphs_partitioned": full_ids == covered | extra,
            "container_evidence_retained": True,
        })
    physical_proofs, physical_rejections = _physical_contexts(
        candidates, page, physical_line_views, set(result["container_indices"]))
    result["proofs"].extend(physical_proofs)
    result["container_indices"].extend(proof["index"] for proof in physical_proofs)
    result["container_indices"].sort()
    result["rejections"] = [rejection for rejection in result["rejections"]
                            if rejection["index"] not in result["container_indices"]]
    result["rejections"].extend(physical_rejections)
    result["remaining_unsafe_indices"] = [
        i for i in unsafe if i not in result["container_indices"]
    ]
    return result
