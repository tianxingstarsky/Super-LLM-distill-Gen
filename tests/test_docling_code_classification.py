"""Code label corrections require independent layout and source protection."""
from copy import deepcopy

import pytest

from lib.infrastructure.docling_code_classification import (
    CODE_CLASSIFICATION_POLICY, CODE_LAYOUT_PARAMETERS,
    assess_code_math_reclassification, protect_source_code,
)


def source(text="Choose X ∈ ℝ, so the result remains well defined."):
    glyphs = []
    x, y = 10, 20
    for char in text:
        if char == "\n":
            y -= 16
            x = 10
        glyphs.append({"char_index": len(glyphs), "text": char,
                       "font_name": "CambriaMath" if char in "X∈ℝ" else "Times-Roman",
                       "font_flags": 32, "font_size": 12,
                       "origin": [x, y],
                       "bbox": {"l": x, "b": y, "r": x + 5, "t": y + 12}})
        x += 6
    route = {"original_label": "code", "code_language": "unknown",
             "page_no": 2, "source_sha256": "native-source",
             "bbox": {"l": 0, "b": 0, "r": 400, "t": 40}}
    page = {"source_bbox": {"l": 0, "b": 0, "r": 400, "t": 100}, "page_no": 2,
            "source_sha256": route["source_sha256"],
            "image_sha256": "page-sha", "pixel_size": [800, 200]}
    prediction = {"policy": CODE_CLASSIFICATION_POLICY,
                  "status": "layout_predicted", "local_files_only": True,
                  "device": "cpu", "source_image_sha256": page["image_sha256"],
                  "image_size": list(page["pixel_size"]), "frame": "full_pdf_page",
                  "parameters": dict(CODE_LAYOUT_PARAMETERS),
                  "boxes": [{"label": "text", "score": .87, "coordinate": [0, 120, 800, 200]}]}
    boxes = [{"label": "text", "score": .87, "bbox": dict(route["bbox"])}]
    return route, glyphs, text, boxes, prediction, page


def assess(state, context="Mathematical definitions"):
    route, glyphs, text, boxes, prediction, page = state
    return assess_code_math_reclassification(
        route, glyphs, text, layout_boxes=boxes,
        layout_prediction=prediction, page_proof=page, source_context=context)


def test_independent_layout_approves_only_a_label_proposal_without_editing_source():
    state = source()
    before = deepcopy(state)
    proof = assess(state)
    assert proof["approved"] and proof["reasons"] == []
    assert not proof["transcription_used_for_publication"]
    assert proof["pp_missing_source_char_indices"] == []
    assert proof["egret_missing_source_char_indices"] == []
    assert state == before


@pytest.mark.parametrize("text", [
    "X = 3\nprint(X)  # X ∈ ℝ, choose a real value",
    '{"X": 3, "description": "X ∈ ℝ, choose a real value"}',
    "for X ∈ ℝ do compute the next value",
    "1: X ∈ ℝ so the result remains well defined",
    "while X ∈ ℝ keep the current candidate",
    "Return X ∈ ℝ after the calculation is complete",
    "Let X := ℝ so the result remains well defined",
    "matrix X: ℝ so the result remains well defined",
    "param X ∈ ℝ; so the result remains well defined",
    "assert X ∈ ℝ so the result remains well defined",
    "SELECT X ∈ ℝ so the result remains well defined",
    "Choose X ∈ ℝ, then\n  keep the result well defined",
    'caption := "Let X ∈ ℝ so the result remains well defined.";',
    '# Let X ∈ ℝ so the result remains well defined.\nresult = multiply(X, X)',
    'α = x**2\nβ = α + 1',
    'if (x > 0) { y = x * x; }',
])
def test_actual_code_pseudocode_and_declarative_syntax_remain_code(text):
    assert not assess(source(text))["approved"]


@pytest.mark.parametrize("language", ["Python", "Julia", "Mathematica", "SQL", "unknown-new-dsl"])
def test_declared_language_remains_protected(language):
    state = source()
    state[0]["code_language"] = language
    assert "declared_code_language" in assess(state)["reasons"]


@pytest.mark.parametrize("context", ["Algorithm 2", "Pseudocode", "Source code listing", "算法示例", "伪代码"])
def test_math_in_algorithm_context_stays_code_even_when_both_models_vote_text(context):
    assert "algorithm_or_code_context" in assess(source(), context)["reasons"]


@pytest.mark.parametrize("context", [
    'caption := "a comment begins here";\n/*',
    '# continuation from a previous code block\nresult = multiply(X, X)',
    '/* prose-form math lies in this comment\n*/',
    'for i = 1,...,m do',
])
def test_math_only_comment_continuation_requires_surrounding_source_context(context):
    assert "surrounding_source_code_syntax" in assess(source(), context)["reasons"]


def test_missing_original_block_context_is_not_permission_to_relabel():
    assert "source_block_context_unavailable" in assess(source(), None)["reasons"]


@pytest.mark.parametrize("font,flag", [("CourierNewPSMT", 32), ("Consolas", 32), ("FiraCode", 32), ("Times-Roman", 33)])
def test_monospace_font_name_and_fixed_pitch_flag_independently_protect(font, flag):
    state = source()
    state[1][-1]["font_name"] = font
    state[1][-1]["font_flags"] = flag
    assert not assess(state)["approved"]


def test_plain_math_without_native_prose_is_not_a_code_relabel_proof():
    assert not assess(source("X ∈ ℝ"))["approved"]


@pytest.mark.parametrize("key,value", [
    ("status", "review_required"), ("source_image_sha256", "wrong-page"),
    ("image_size", [2, 3]), ("local_files_only", False), ("device", "cuda"),
    ("frame", "crop"), ("parameters", {}),
])
def test_invalid_incomplete_or_wrong_source_layout_cannot_approve(key, value):
    state = source()
    state[4][key] = value
    assert not assess(state)["approved"]


@pytest.mark.parametrize("key,value", [("page_no", 3), ("source_sha256", "changed"),
                                       ("source_bbox", {"l": 20, "b": 0, "r": 400, "t": 40})])
def test_wrong_page_binding_blocks_label_changes(key, value):
    state = source()
    state[5][key] = value
    assert not assess(state)["approved"]


@pytest.mark.parametrize("label,score", [("algorithm", .3), ("code", .9), ("text", float("nan")), ("text", True)])
@pytest.mark.parametrize("model", ["pp", "egret"])
def test_competing_layout_and_invalid_scores_block(label, score, model):
    state = source()
    if model == "pp":
        state[3].append({"label": label, "score": score, "bbox": dict(state[0]["bbox"])})
    else:
        state[4]["boxes"].append({"label": label, "score": score, "coordinate": [0, 120, 800, 200]})
    assert not assess(state)["approved"]


@pytest.mark.parametrize("model", ["pp", "egret"])
def test_absence_of_code_boxes_is_not_positive_text_evidence(model):
    state = source()
    (state[3] if model == "pp" else state[4]["boxes"]).clear()
    assert not assess(state)["approved"]


def test_missing_or_duplicated_native_evidence_stays_review():
    state = source()
    state[1][0]["char_index"] = state[1][1]["char_index"]
    assert "invalid_native_code_evidence" in assess(state)["reasons"]


def test_standard_library_parser_confirms_python_code_without_executing_it():
    route, glyphs, _, *_ = source()
    result = protect_source_code(route, glyphs, "X = 3\nprint(X)", source_context="")
    assert "python_ast_parsed" in result["syntax_checks"]
    assert "valid_python_syntax" in result["reasons"]


@pytest.mark.parametrize("model", ["pp", "egret"])
def test_only_part_of_a_source_row_does_not_cover_the_whole_code_block(model):
    state = source()
    if model == "pp":
        state[3][0]["bbox"]["r"] = 100
    else:
        state[4]["boxes"][0]["coordinate"][2] = 200
    proof = assess(state)
    assert not proof["approved"]
    assert f"{model}_incomplete_positive_text_coverage" in proof["reasons"]


def test_union_of_two_physical_text_rows_can_cover_one_mislabeled_item():
    state = source("Choose X ∈ ℝ,\nso the result remains well defined.")
    state[0]["bbox"]["b"] = 0
    state[3][:] = [{"label": "text", "score": .8, "bbox": dict(l=0, b=18, r=400, t=35)},
                  {"label": "text", "score": .8, "bbox": dict(l=0, b=0, r=400, t=18)}]
    state[4]["boxes"] = [{"label": "text", "score": .8, "coordinate": [0, 130, 800, 164]},
                          {"label": "text", "score": .8, "coordinate": [0, 164, 800, 200]}]
    assert assess(state)["approved"]


def test_unrelated_positive_text_box_does_not_approve_other_code_block():
    state = source()
    state[3][0]["bbox"] = {"l": 500, "b": 0, "r": 700, "t": 40}
    assert not assess(state)["approved"]


def test_low_confidence_text_is_not_promoted_to_positive_evidence():
    state = source()
    state[4]["boxes"][0]["score"] = CODE_LAYOUT_PARAMETERS["text_threshold"] - .001
    assert not assess(state)["approved"]


def test_classification_never_has_model_generated_text_to_replace_source():
    proof = assess(source())
    assert not proof["transcription_used_for_publication"]
    assert "text" not in proof and "raw_output" not in proof["layout_prediction"]
