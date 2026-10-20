"""Alternate views cannot silently drop source terms or join expressions."""
from copy import deepcopy
from itertools import permutations

import pytest

from lib.infrastructure.docling_inline_candidates import select_inline_formula_candidates


def candidate(ids, *, safe=True, page=4, ref="#/texts/40", view="paragraph"):
    return {
        "bbox": {"l": 10, "b": 10, "r": 90, "t": 30},
        "view": view,
        "geometry": {
            "review_required": not safe, "char_indices": ids,
            "page_no": page, "source_ref": ref, "source_sha256": "same-pdf",
        },
    }


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_full_expression_survives_smaller_paragraph_view(order):
    originals = [candidate([11, 12]), candidate([11, 12, 13, 14], view="page")]
    inputs = [originals[index] for index in order]
    result = select_inline_formula_candidates(inputs)
    selected = [inputs[index]["geometry"]["char_indices"] for index in result["selected_indices"]]
    assert selected == [[11, 12, 13, 14]]
    assert not result["review_required"]
    assert result["alternatives"][0]["reason"] == "source_glyph_subset"


def test_equal_glyph_sets_are_the_only_duplicate_case():
    result = select_inline_formula_candidates([candidate([1, 2]), candidate([2, 1], view="page")])
    assert result["selected_indices"] == [0]
    assert result["alternatives"] == [{
        "index": 1, "disposition": "covered_alternative",
        "reason": "equivalent_source_glyphs", "covered_by": [0],
        "competing_with": [], "source_glyph_ids": [1, 2],
    }]


@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_crossing_candidates_never_pick_first_and_keep_unrelated_formula(order):
    originals = [candidate([1, 2]), candidate([2, 3]), candidate([20, 21])]
    inputs = [originals[index] for index in order]
    result = select_inline_formula_candidates(inputs)
    assert [inputs[i]["geometry"]["char_indices"] for i in result["selected_indices"]] == [[20, 21]]
    assert len(result["review_indices"]) == 2
    assert all(row["reason"] == "competing_source_glyph_sets" for row in result["alternatives"])


def test_conflicting_chain_and_its_subset_cannot_reenter_as_fallback():
    result = select_inline_formula_candidates([
        candidate([1]), candidate([1, 2]), candidate([2, 3]), candidate([3, 4]),
    ])
    assert result["selected_indices"] == []
    assert result["review_indices"] == [0, 1, 2, 3]


@pytest.mark.parametrize("unsafe_ids", [[1, 2], [2]])
def test_unsafe_alternative_can_be_covered_by_one_complete_safe_candidate(unsafe_ids):
    result = select_inline_formula_candidates([
        candidate(unsafe_ids, safe=False), candidate([1, 2]), candidate([8, 9]),
    ])
    assert result["selected_indices"] == [1, 2]
    assert result["review_indices"] == []
    assert result["alternatives"][0]["covered_by"] == [1]


def test_unsafe_extra_ordinary_character_blocks_overlapping_safe_candidate():
    # 99 may be ordinary prose, a digit, or a roman subscript. Its font/category
    # cannot justify discarding it from a competing detector view.
    result = select_inline_formula_candidates([
        candidate([1, 2]), candidate([1, 2, 99], safe=False), candidate([20, 21]),
    ])
    assert result["selected_indices"] == [2]
    assert result["review_indices"] == [0, 1]


def test_unsafe_union_does_not_blindly_merge_two_safe_line_fragments():
    result = select_inline_formula_candidates([
        candidate([1, 2]), candidate([3, 4]), candidate([1, 2, 3, 4], safe=False),
    ])
    assert result["selected_indices"] == []
    assert result["review_indices"] == [0, 1, 2]
    assert result["continuation_inferred"] is False


def test_unsafe_disjoint_fragment_quarantines_without_discarding_safe_evidence():
    result = select_inline_formula_candidates([candidate([1]), candidate([5], safe=False)])
    assert result["selected_indices"] == [0]
    assert result["review_indices"] == [1]
    assert result["review_required"]


@pytest.mark.parametrize("ids", [[], None, [True], [1.0], [-1], [1, 1], ["1"]])
def test_missing_or_invalid_glyph_proof_is_not_an_empty_subset(ids):
    result = select_inline_formula_candidates([candidate([1, 2]), candidate(ids)])
    assert result["selected_indices"] == [0]
    assert result["review_indices"] == [1]


def test_identical_character_numbers_on_different_pages_are_not_duplicates():
    result = select_inline_formula_candidates([candidate([1, 2]), candidate([1, 2], page=5)])
    assert result["selected_indices"] == [0, 1]
    assert result["alternatives"] == []


def test_explicit_review_reasons_cannot_be_bypassed_by_false_flag():
    item = candidate([1, 2])
    item["geometry"]["review_reasons"] = ["ordinary_text_formula_boundary_ambiguous"]
    result = select_inline_formula_candidates([item])
    assert result["selected_indices"] == []
    assert result["review_indices"] == [0]


def test_source_candidates_remain_unchanged():
    inputs = [candidate([2, 1]), candidate([1, 2, 3]), candidate([9], safe=False)]
    before = deepcopy(inputs)
    select_inline_formula_candidates(inputs)
    assert inputs == before
