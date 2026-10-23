"""Resolve alternate detector views using native source glyph coverage.

This module does not recognize formulas or infer continuation. Geometry must
already have been reviewed for each candidate. A smaller source-glyph set can
be covered by a safe larger candidate; crossing sets remain a conflict.
"""
from __future__ import annotations


INLINE_CANDIDATE_POLICY = "native_glyph_set_alternatives_v1"


def _glyphs(candidate):
    geometry = candidate.get("geometry", {})
    values = geometry.get("char_indices")
    if not isinstance(values, (list, tuple)) or not values:
        return None, "missing_source_glyph_ids"
    if any(type(value) is not int or value < 0 for value in values):
        return None, "invalid_source_glyph_ids"
    if len(set(values)) != len(values):
        return None, "duplicate_source_glyph_ids"
    return frozenset(values), None


def _scope(candidate):
    geometry = candidate.get("geometry", {})
    return (
        geometry.get("source_sha256"),
        geometry.get("page_no"),
        geometry.get("source_ref"),
    )


def select_inline_formula_candidates(candidates):
    """Return selected/review indices and an explanation for every alternative.

    Inputs normally belong to one paragraph. When source/page/paragraph identity
    is supplied, glyph IDs from different scopes are never compared.

    Safe strict supersets take precedence over safe subsets. Only equivalent
    glyph sets are duplicates. Two maximal safe sets that intersect without
    containment both require review. An unsafe candidate may be suppressed only
    if its complete, nonempty glyph set is contained in one selected safe set.
    An unsafe candidate with additional intersecting glyphs also blocks that
    safe selection. The caller must quarantine the paragraph whenever
    ``review_indices`` is nonempty; selected indices alone are not approval.
    """
    infos = []
    for index, candidate in enumerate(candidates):
        glyphs, invalid = _glyphs(candidate)
        geometry = candidate.get("geometry", {})
        safe = (invalid is None and geometry.get("review_required") is False
                and not geometry.get("review_reasons"))
        infos.append({
            "index": index, "glyphs": glyphs, "scope": _scope(candidate),
            "safe": safe, "invalid": invalid,
        })

    safe = [item for item in infos if item["safe"]]
    # Equivalent sets alone may use stable input order to choose a representative.
    representatives = []
    for item in safe:
        if not any(item["scope"] == other["scope"] and item["glyphs"] == other["glyphs"]
                   for other in representatives):
            representatives.append(item)
    maximal = [
        item for item in representatives
        if not any(item["scope"] == other["scope"] and item["glyphs"] < other["glyphs"]
                   for other in representatives)
    ]
    conflicts = {item["index"]: set() for item in maximal}
    for position, first in enumerate(maximal):
        for second in maximal[position + 1:]:
            if first["scope"] == second["scope"] and first["glyphs"] & second["glyphs"]:
                conflicts[first["index"]].add(second["index"])
                conflicts[second["index"]].add(first["index"])

    unsafe = [item for item in infos if not item["safe"]]
    for item in maximal:
        for other in unsafe:
            if (other["glyphs"] is not None and item["scope"] == other["scope"]
                    and item["glyphs"] & other["glyphs"]
                    and not other["glyphs"] <= item["glyphs"]):
                # Extra ordinary prose characters count too; do not inspect only
                # math-font anchors when deciding whether a view is equivalent.
                conflicts[item["index"]].add(other["index"])
    selected = [item for item in maximal if not conflicts[item["index"]]]
    selected_indices = [item["index"] for item in selected]
    alternatives, review_indices = [], []
    for item in infos:
        index = item["index"]
        if index in selected_indices:
            continue
        covered_by = [
            other["index"] for other in selected
            if item["glyphs"] is not None and item["scope"] == other["scope"]
            and item["glyphs"] <= other["glyphs"]
        ]
        if covered_by:
            equivalent = any(
                item["glyphs"] == infos[other]["glyphs"] for other in covered_by
            )
            reason = "equivalent_source_glyphs" if equivalent else "source_glyph_subset"
            alternatives.append({
                "index": index, "disposition": "covered_alternative", "reason": reason,
                "covered_by": covered_by, "competing_with": [],
                "source_glyph_ids": sorted(item["glyphs"]),
            })
            continue
        competing = set()
        if item["glyphs"] is not None:
            for other in infos:
                if (index != other["index"] and other["glyphs"] is not None
                        and item["scope"] == other["scope"]
                        and item["glyphs"] & other["glyphs"]):
                    competing.add(other["index"])
        review_indices.append(index)
        alternatives.append({
            "index": index, "disposition": "review_required",
            "reason": item["invalid"] or (
                "competing_source_glyph_sets" if competing else "unsafe_geometry"
            ),
            "covered_by": [], "competing_with": sorted(competing),
            "source_glyph_ids": sorted(item["glyphs"]) if item["glyphs"] is not None else None,
        })
    return {
        "policy": INLINE_CANDIDATE_POLICY,
        "selected_indices": selected_indices,
        "review_indices": review_indices,
        "alternatives": alternatives,
        "review_required": bool(review_indices),
        "continuation_inferred": False,
    }
