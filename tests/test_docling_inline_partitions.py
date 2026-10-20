from copy import deepcopy

import pytest

from lib.infrastructure.docling_inline_partitions import classify_inline_formula_containers


def setup():
    glyphs = []
    for index, char in enumerate("x+y for real input t."):
        glyphs.append({
            "char_index": index, "text": char,
            "font_name": "CambriaMath" if index < 3 else "Times-Roman",
            "font_size": 10, "origin": [index * 6, 10], "generated": False,
            "bbox": {"l": index * 6, "b": 10, "r": index * 6 + 5, "t": 20,
                     "coord_origin": "BOTTOMLEFT"},
        })
    page = {"source_sha256": "source", "page_no": 1, "height": 100, "glyphs": glyphs}

    def candidate(ids, safe):
        chosen = [glyphs[i] for i in ids]
        bbox = {"l": min(g["bbox"]["l"] for g in chosen) - 1,
                "r": max(g["bbox"]["r"] for g in chosen) + 1, "b": 9, "t": 21,
                "coord_origin": "BOTTOMLEFT"}
        return {"bbox": bbox, "geometry": {
            "source_sha256": "source", "page_no": 1, "source_ref": "#/texts/0",
            "bbox": bbox, "proposed_bbox": bbox, "char_indices": ids,
            "review_required": not safe, "review_reasons": [] if safe else [
                "ordinary_text_formula_boundary_ambiguous"],
            "pixel_evidence": {"original_ink_pixels": 100, "unassigned_original_ink_pixels": 0},
        }}

    candidates = [candidate([i for i, g in enumerate(glyphs) if g["text"].strip()], False),
                  candidate([0], True), candidate([1, 2], True)]
    return page, candidates


def classify(page, candidates, route="mixed_formula_probe"):
    return classify_inline_formula_containers(candidates, page, route=route)


def test_broad_mixed_box_keeps_complete_partition_and_prose_evidence():
    page, candidates = setup()
    original = deepcopy((page, candidates))
    result = classify(page, candidates)
    assert result["container_indices"] == [0]
    proof = result["proofs"][0]
    assert proof["contained_safe_indices"] == [1, 2]
    assert proof["math_char_indices"] == [0, 1, 2]
    assert set(proof["full_source_char_indices"]) == (
        set(proof["covered_source_char_indices"]) | set(proof["ordinary_prose_char_indices"]))
    assert proof["container_evidence_retained"]
    assert result["remaining_unsafe_indices"] == []
    assert not result["automatic_text_replacement_allowed"]
    assert not result["continuation_inferred"]
    assert (page, candidates) == original


@pytest.mark.parametrize("route", ["inline", "display", "preserve_code", None])
def test_ordinary_routes_never_suppress_unsafe_candidates(route):
    page, candidates = setup()
    result = classify(page, candidates, route)
    assert result["container_indices"] == []
    assert result["remaining_unsafe_indices"] == [0]


def test_code_probe_may_classify_a_container_but_not_relabel():
    page, candidates = setup()
    assert classify(page, candidates, "code_math_probe")["container_indices"] == [0]


@pytest.mark.parametrize("reason", [
    "ink_touches_proposed_boundary", "nearby_unselected_script_requires_review",
    "unassigned_ink_in_extension", "glyph_extension_outside_source_paragraph",
])
def test_clipping_or_script_evidence_keeps_broad_box_in_review(reason):
    page, candidates = setup()
    candidates[0]["geometry"]["review_reasons"].append(reason)
    assert classify(page, candidates)["remaining_unsafe_indices"] == [0]


def test_uncovered_math_character_prevents_partition():
    page, candidates = setup()
    candidates.pop()
    assert classify(page, candidates)["rejections"][0]["reason"] == "uncovered_container_math"


@pytest.mark.parametrize("field,value", [
    ("page_no", 2), ("source_ref", "#/texts/other"), ("source_sha256", "other"),
])
def test_other_source_candidates_cannot_cover_math(field, value):
    page, candidates = setup()
    candidates[2]["geometry"][field] = value
    assert classify(page, candidates)["container_indices"] == []


def test_missing_ordinary_glyph_from_broad_set_is_not_silently_dropped():
    page, candidates = setup()
    candidates[0]["geometry"]["char_indices"].remove(4)
    assert classify(page, candidates)["rejections"][0]["reason"] == "incomplete_container_glyph_set"


def test_safe_candidate_must_itself_cover_all_intersected_glyphs():
    page, candidates = setup()
    candidates[2]["geometry"]["char_indices"] = [1]
    assert classify(page, candidates)["container_indices"] == []


def test_broad_box_with_excluded_glyphs_stays_in_review():
    page, candidates = setup()
    candidates[0]["geometry"]["excluded_char_indices"] = [99]
    assert classify(page, candidates)["container_indices"] == []


def test_roman_extra_digit_is_not_assumed_to_be_prose():
    page, candidates = setup()
    page["glyphs"][-2]["text"] = "2"
    assert classify(page, candidates)["rejections"][0]["reason"] == "extra_glyphs_not_proven_ordinary_prose"


def test_roman_script_outside_safe_child_is_not_assumed_to_be_prose():
    page, candidates = setup()
    glyph = page["glyphs"][-2]
    glyph.update(font_size=6, origin=[glyph["origin"][0], 15])
    assert classify(page, candidates)["container_indices"] == []


def test_clipped_native_ink_cannot_be_declared_container():
    page, candidates = setup()
    candidates[0]["geometry"]["proposed_bbox"]["t"] = 19
    assert classify(page, candidates)["container_indices"] == []


def test_missing_native_source_identity_keeps_review():
    page, candidates = setup()
    page["source_sha256"] = None
    assert classify(page, candidates)["container_indices"] == []


def test_unassigned_raster_mark_is_not_discarded_by_native_partition():
    page, candidates = setup()
    candidates[0]["geometry"]["pixel_evidence"]["unassigned_original_ink_pixels"] = 1
    assert classify(page, candidates)["rejections"][0]["reason"] == "unassigned_container_raster_ink"


def physical_setup():
    from lib.infrastructure.docling_inline_lines import INLINE_LINE_POLICY

    glyphs, rows = [], []
    for text, x, y in [("x+y", 0, 40), ("+z for real input", 12, 10)]:
        row = []
        for position, char in enumerate(text):
            index = len(glyphs)
            row.append(index)
            glyphs.append({
                "char_index": index, "text": char, "generated": False,
                "font_name": "CambriaMath" if y == 40 or position < 2 else "Times-Roman",
                "font_size": 10, "origin": [x + position * 6, y],
                "bbox": {"l": x + position * 6, "r": x + position * 6 + 5,
                         "b": y, "t": y + 10, "coord_origin": "BOTTOMLEFT"},
            })
        rows.append([i for i in row if glyphs[i]["text"].strip()])
    paragraph = {"l": 0, "b": 10, "r": glyphs[-1]["bbox"]["r"], "t": 50,
                 "coord_origin": "BOTTOMLEFT"}
    page = {"source_sha256": "source", "page_no": 1, "height": 100, "glyphs": glyphs}

    def box(indices, margin=0):
        chosen = [glyphs[i]["bbox"] for i in indices]
        return {"l": min(b["l"] for b in chosen) - margin,
                "b": min(b["b"] for b in chosen) - margin,
                "r": max(b["r"] for b in chosen) + margin,
                "t": max(b["t"] for b in chosen) + margin, "coord_origin": "BOTTOMLEFT"}

    def candidate(indices):
        bounds = box(indices, 1)
        return {"bbox": bounds, "view": "page", "geometry": {
            "source_sha256": "source", "page_no": 1, "source_ref": "#/texts/0",
            "paragraph_bbox": paragraph, "bbox": bounds, "proposed_bbox": bounds,
            "char_indices": indices, "review_required": False, "review_reasons": [],
            "excluded_char_indices": [],
            "pixel_evidence": {"original_ink_pixels": 100, "unassigned_original_ink_pixels": 0},
        }}

    # The broad rectangle includes only '+' from row two. A complete '+z'
    # child extends past its right edge; no expression strings are joined.
    candidates = [candidate([0, 1, 2, 3]), candidate([0, 1, 2]), candidate([3, 4])]
    views = [{
        "policy": INLINE_LINE_POLICY, "source_sha256": "source", "page_no": 1,
        "bbox": box(indices), "paragraph_bbox": paragraph,
        "source_line_ids": [f"page-1:line-{i}"], "physical_line_count": 1,
        "pdfium_char_indices": indices, "physical_only": True,
        "continuation_inferred": False, "review_required": False, "review_reasons": [],
    } for i, indices in enumerate(rows)]
    return page, candidates, views


def classify_physical(page, candidates, views, route="mixed_formula_probe"):
    return classify_inline_formula_containers(
        candidates, page, route=route, physical_line_views=views)


def test_partial_second_row_can_be_context_with_complete_row_children():
    page, candidates, views = physical_setup()
    original = deepcopy((page, candidates, views))
    result = classify_physical(page, candidates, views)
    assert result["container_indices"] == [0]
    proof = result["proofs"][0]
    assert proof["partition_kind"] == "physical_line_context"
    assert proof["single_row_safe_indices"] == [1, 2]
    assert proof["full_source_char_indices"] == [0, 1, 2, 3]
    assert proof["covered_source_char_indices"] == [0, 1, 2, 3]
    assert proof["row_formula_char_indices"] == [0, 1, 2, 3, 4]
    assert proof["row_ordinary_prose_char_indices"]
    assert proof["all_native_glyphs_partitioned"]
    assert proof["all_physical_row_glyphs_partitioned"]
    assert not proof["continuation_inferred"]
    assert (page, candidates, views) == original


def test_physical_partition_requires_opt_in_probe_and_native_views():
    page, candidates, views = physical_setup()
    assert classify(page, candidates)["container_indices"] == []
    assert classify_physical(page, candidates, views, "inline")["container_indices"] == []


@pytest.mark.parametrize("field,value", [
    ("page_no", 2), ("source_sha256", "other"), ("policy", "guessed_lines"),
    ("review_required", True), ("physical_only", False),
    ("physical_line_count", 2), ("continuation_inferred", True),
])
def test_physical_partition_rejects_unproven_row_provenance(field, value):
    page, candidates, views = physical_setup()
    views[1][field] = value
    assert classify_physical(page, candidates, views)["container_indices"] == []


def test_physical_rows_must_cover_exact_native_glyph_sets():
    page, candidates, views = physical_setup()
    views[1]["pdfium_char_indices"].remove(4)
    assert classify_physical(page, candidates, views)["container_indices"] == []


def test_physical_rows_must_not_share_native_identity():
    page, candidates, views = physical_setup()
    views[1]["source_line_ids"] = views[0]["source_line_ids"]
    assert classify_physical(page, candidates, views)["container_indices"] == []


@pytest.mark.parametrize("field,value", [
    ("page_no", 2), ("source_ref", "#/texts/other"), ("source_sha256", "other"),
])
def test_physical_child_from_another_source_cannot_cover_a_row(field, value):
    page, candidates, views = physical_setup()
    candidates[2]["geometry"][field] = value
    assert classify_physical(page, candidates, views)["container_indices"] == []


def test_physical_context_requires_every_math_glyph_in_touched_rows():
    page, candidates, views = physical_setup()
    # 'z' lies outside the broad box. It still requires a complete safe child.
    child = candidates[2]
    child["geometry"]["char_indices"] = [3]
    child["geometry"]["bbox"] = child["geometry"]["proposed_bbox"] = {
        "l": 11, "r": 18, "b": 9, "t": 21, "coord_origin": "BOTTOMLEFT"}
    result = classify_physical(page, candidates, views)
    assert result["container_indices"] == []
    assert result["rejections"][-1]["reason"] == "uncovered_physical_row_math"


def test_crossing_single_row_candidates_remain_competing():
    page, candidates, views = physical_setup()
    first = candidates.pop(1)
    second = deepcopy(first)
    first["geometry"]["char_indices"] = [0, 1]
    first["geometry"]["bbox"] = first["geometry"]["proposed_bbox"] = {
        "l": -1, "r": 12, "b": 39, "t": 51, "coord_origin": "BOTTOMLEFT"}
    second["geometry"]["char_indices"] = [1, 2]
    second["geometry"]["bbox"] = second["geometry"]["proposed_bbox"] = {
        "l": 5, "r": 18, "b": 39, "t": 51, "coord_origin": "BOTTOMLEFT"}
    candidates.extend([first, second])
    result = classify_physical(page, candidates, views)
    assert result["container_indices"] == []
    assert result["rejections"][-1]["reason"] == "competing_single_line_candidates"


@pytest.mark.parametrize("index", [0, 1, 2])
def test_unassigned_context_or_child_raster_ink_stays_blocked(index):
    page, candidates, views = physical_setup()
    candidates[index]["geometry"]["pixel_evidence"]["unassigned_original_ink_pixels"] = 1
    assert classify_physical(page, candidates, views)["container_indices"] == []


@pytest.mark.parametrize("reason", [
    "ink_touches_proposed_boundary", "nearby_unselected_script_requires_review",
    "unassigned_ink_in_extension", "glyph_extension_outside_source_paragraph",
])
def test_multiline_context_never_suppresses_clipping_or_script_warnings(reason):
    page, candidates, views = physical_setup()
    candidates[0]["geometry"].update(review_required=True, review_reasons=[reason])
    result = classify_physical(page, candidates, views)
    assert result["container_indices"] == []
    assert result["remaining_unsafe_indices"] == [0]


def test_multiline_context_requires_complete_accounting_of_ordinary_extras():
    page, candidates, views = physical_setup()
    # A roman-font number is not inferred to be ordinary prose.
    page["glyphs"][-1]["text"] = "2"
    assert classify_physical(page, candidates, views)["container_indices"] == []


def test_physical_partition_does_not_remove_single_row_unsafe_candidates():
    page, candidates, views = physical_setup()
    unsafe = deepcopy(candidates[2])
    unsafe["geometry"].update(review_required=True, review_reasons=["ink_touches_proposed_boundary"])
    candidates.append(unsafe)
    result = classify_physical(page, candidates, views)
    assert result["container_indices"] == [0]
    assert result["remaining_unsafe_indices"] == [3]
