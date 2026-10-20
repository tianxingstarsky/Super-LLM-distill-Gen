from copy import deepcopy
import hashlib

from PIL import Image
import pytest

from lib.infrastructure import docling_inline_continuation as continuation
from lib.infrastructure.docling_inline_lines import INLINE_LINE_POLICY


def syntax(rows, whole=None):
    return continuation.validate_continuation_syntax(rows, whole)


def test_whole_aligned_matches_separate_rows_with_only_style_and_spacing_changes():
    result = syntax(
        [r"\mathrm{g(t)=a_0+a_1t+a_2t^{2}}", r"+a_3\mathrm{t}^{3}+a_4t^4,"],
        r"\begin{aligned}{g(t)=a_{0}+a_1t+a_2t^2}\\{}&{\quad+a_3t^{3}+a_4t^{4},}\\\end{aligned}",
    )
    assert not result["review_required"]
    assert result["all_rows_structurally_equal"]


@pytest.mark.parametrize("rows", [
    ["u=x", "v=y"],
    ["u=x", "+v=y"],
    ["A=B", r"C\in D"],
    ["x+y", "+z"],
    ["u=x,", "+y"],
    ["u=", "+y"],
    ["u=x", r"+y\leq z"],
    ["u=x", r"\text{and }+y"],
])
def test_independent_equations_relation_lists_or_unproven_continuations_rejected(rows):
    assert syntax(rows)["review_required"]


@pytest.mark.parametrize("whole", [
    r"y=x+z",
    r"\begin{aligned}y=x\\+w\end{aligned}",
    r"\begin{aligned}y=x\\+z\\+w\end{aligned}",
    r"\begin{aligned}y=x\\z+\end{aligned}",
    r"\begin{aligned}y=x\\+z\end{aligned} discarded",
    r"\begin{array}{c}y=x\\+z\end{array}",
])
def test_whole_ocr_cannot_change_rows_or_replace_structure(whole):
    assert syntax(["y=x", "+z"], whole)["review_required"]


def test_equal_symbol_inventory_does_not_allow_changed_script_attachment():
    result = syntax(["y=x_i^2", "+z"], r"\begin{aligned}y=x_2^i\\+z\end{aligned}")
    assert result["reasons"] == ["whole_formula_row_ast_mismatch"]


def test_fraction_numerator_and_denominator_are_compared_structurally():
    result = syntax([r"y=\frac{a}{b}", "+z"],
                    r"\begin{aligned}y=\frac{b}{a}\\+z\end{aligned}")
    assert result["review_required"]


@pytest.mark.parametrize("row,whole_row", [
    (r"y=\mathbf{x}", "y=x"),
    (r"y=\mathsf{A}", "y=A"),
    (r"y=\mathtt{v}", "y=v"),
    ("y=x", r"y=\mathbf{x}"),
    (r"y=\mathbf{x}", r"y=\mathsf{x}"),
    (r"y=\mathbf{x}_i", r"y=x_i"),
])
def test_whole_ocr_must_preserve_semantic_symbol_font(row, whole_row):
    result = syntax([row, "+z"], rf"\begin{{aligned}}{whole_row}\\+z\end{{aligned}}")
    assert result["reasons"] == ["whole_formula_row_ast_mismatch"]


def test_semantic_symbol_font_and_script_survive_harmless_grouping_changes():
    result = syntax(
        [r"y=\mathbf{x}_i", r"+\mathsf{A}"],
        r"\begin{aligned}y={\mathbf{{x}}}_{i}\\{}&+\mathsf{{A}}\end{aligned}",
    )
    assert not result["review_required"], result


def test_grouped_script_operand_and_base_are_not_flattened():
    assert syntax(["y=x^{ab}", "+z"],
                  r"\begin{aligned}y=x^ab\\+z\end{aligned}")["review_required"]
    assert syntax(["y={ab}^2", "+z"],
                  r"\begin{aligned}y=ab^2\\+z\end{aligned}")["review_required"]


def fixture(monkeypatch):
    source = b"native-source-identity-for-unit-test"
    digest = hashlib.sha256(source).hexdigest()
    glyphs, rows = [], []
    for text, y in [("y=x", 40), ("+z", 10)]:
        members = []
        for position, char in enumerate(text):
            index = len(glyphs)
            glyphs.append({
                "char_index": index, "text": char, "generated": False,
                "font_name": "CambriaMath", "font_size": 10, "origin": [position * 6, y],
                "bbox": {"l": position * 6, "r": position * 6 + 5, "b": y, "t": y + 10,
                         "coord_origin": "BOTTOMLEFT"},
            })
            members.append(index)
        rows.append(members)
    paragraph = {"l": 0, "r": 17, "b": 10, "t": 50, "coord_origin": "BOTTOMLEFT"}
    page = {"source_sha256": digest, "page_no": 1, "height": 100, "glyphs": glyphs}
    scope = {"source_sha256": digest, "page_no": 1, "source_ref": "#/texts/0"}
    fragments, views = [], []
    for i, ids in enumerate(rows):
        boxes = [glyphs[index]["bbox"] for index in ids]
        bounds = {"l": min(b["l"] for b in boxes), "r": max(b["r"] for b in boxes),
                  "b": min(b["b"] for b in boxes), "t": max(b["t"] for b in boxes),
                  "coord_origin": "BOTTOMLEFT"}
        fragments.append({
            "fragment_id": f"#/texts/0:{i}", "transcribed_text": ["y=x", "+z"][i],
            "status": "transcribed_unverified", "character_consistency": {"review_required": False},
            "native_char_indices": [[0, 1, 2], [4, 5]][i],
            "geometry": {
                **scope, "bbox": bounds, "paragraph_bbox": paragraph, "char_indices": ids,
                "review_required": False, "review_reasons": [], "excluded_char_indices": [],
                "pixel_evidence": {
                    "unassigned_original_ink_pixels": 0, "unassigned_added_ink_pixels": 0,
                    "unassigned_removed_ink_pixels": 0, "retained_glyph_ink_removed_pixels": 0,
                    "proposed_edge_ink_pixels": dict.fromkeys(("left", "right", "top", "bottom"), 0),
                },
            },
        })
        views.append({
            **scope, "policy": INLINE_LINE_POLICY, "bbox": bounds, "paragraph_bbox": paragraph,
            "source_line_ids": [f"page-1:line-{i}"], "physical_line_count": 1,
            "pdfium_char_indices": ids, "physical_only": True, "continuation_inferred": False,
            "review_required": False, "review_reasons": [],
        })
    native = [{"index": i, "text": char} for i, char in enumerate("y=x\n+z")]
    route = {**scope, "route": "mixed_formula_probe"}

    class Reader:
        calls = 0
        text = r"\begin{aligned}y=x\\+z\end{aligned}"
        def read(self, image):
            self.calls += 1
            return {"status": "transcribed_unverified", "transcribed_text": self.text,
                    "raw_output": f"<s>{self.text}</s>", "tokens": 20}
    reader = Reader()
    monkeypatch.setattr(continuation, "_masked_crop", lambda *_args: (
        Image.new("RGB", (20, 20), "white"),
        {"formula_ink_removed_pixels": 0, "unassigned_ink_pixels": 0}))
    arguments = dict(source=source, page=page, source_ref=scope["source_ref"],
                     paragraph_bbox=paragraph, fragments=fragments, native_chars=native,
                     route=route, reader=reader, physical_line_views=views)
    return arguments, reader


def test_proven_whole_formula_returns_one_fragment_and_preserves_row_evidence(monkeypatch):
    arguments, reader = fixture(monkeypatch)
    original = deepcopy(arguments["fragments"])
    result = continuation.recognize_inline_formula_continuation(**arguments)
    assert result["status"] == "transcribed_unverified", result
    assert reader.calls == 1
    assert result["fragment"]["native_char_indices"] == [0, 1, 2, 4, 5]
    assert result["fragment"]["geometry"]["recognition_input_is_masked"]
    assert result["fragment"]["continuation"]["latex_strings_joined"] is False
    assert result["fragment"]["raw_output"] == f"<s>{reader.text}</s>"
    assert result["original_fragments"] == original == arguments["fragments"]
    assert not result["automatic_text_replacement_allowed"]


@pytest.mark.parametrize("label", ["text", "list_item", "caption", "footnote"])
def test_native_ordinary_inline_scope_can_recognize_continuation(monkeypatch, label):
    arguments, reader = fixture(monkeypatch)
    arguments["route"].pop("route")
    arguments["route"]["original_label"] = label
    result = continuation.recognize_inline_formula_continuation(**arguments)
    assert result["status"] == "transcribed_unverified", result
    assert reader.calls == 1


def test_original_code_label_overrides_a_mistaken_mixed_formula_route(monkeypatch):
    arguments, reader = fixture(monkeypatch)
    arguments["route"]["original_label"] = "code"
    result = continuation.recognize_inline_formula_continuation(**arguments)
    assert result["status"] == "not_applicable"
    assert reader.calls == 0


def test_detector_and_view_order_are_normalized_using_proven_physical_rows(monkeypatch):
    arguments, reader = fixture(monkeypatch)
    arguments["fragments"].reverse()
    arguments["physical_line_views"].reverse()
    original = deepcopy(arguments["fragments"])
    result = continuation.recognize_inline_formula_continuation(**arguments)
    assert result["status"] == "transcribed_unverified", result
    assert result["ordered_row_fragment_ids"] == ["#/texts/0:0", "#/texts/0:1"]
    assert result["original_fragments"] == arguments["fragments"] == original
    assert reader.calls == 1


@pytest.mark.parametrize("route", ["code_math_probe", "inline", "display", "preserve_code"])
def test_no_whole_recognition_for_code_or_unproven_block_type(monkeypatch, route):
    arguments, reader = fixture(monkeypatch)
    arguments["route"]["route"] = route
    assert continuation.recognize_inline_formula_continuation(**arguments)["fragment"] is None
    assert reader.calls == 0


def test_intervening_prose_is_not_masked_to_make_a_continuation(monkeypatch):
    arguments, reader = fixture(monkeypatch)
    arguments["native_chars"][3]["text"] = "and"
    result = continuation.recognize_inline_formula_continuation(**arguments)
    assert result["reasons"] == ["prose_or_other_math_between_formula_rows"]
    assert result["status"] == "review_required"
    assert reader.calls == 0


@pytest.mark.parametrize("field,value", [
    ("source_sha256", "other"), ("page_no", 2), ("source_ref", "#/texts/other"),
])
def test_cross_source_fragment_is_rejected_before_recognition(monkeypatch, field, value):
    arguments, reader = fixture(monkeypatch)
    arguments["fragments"][1]["geometry"][field] = value
    result = continuation.recognize_inline_formula_continuation(**arguments)
    assert result["fragment"] is None
    assert result["status"] == "review_required"
    assert reader.calls == 0


def test_raster_unknown_or_clipped_ink_blocks_whole_crop(monkeypatch):
    arguments, reader = fixture(monkeypatch)
    arguments["fragments"][1]["geometry"]["pixel_evidence"]["unassigned_added_ink_pixels"] = 1
    result = continuation.recognize_inline_formula_continuation(**arguments)
    assert result["fragment"] is None
    assert result["status"] == "review_required"
    assert reader.calls == 0


def test_one_physical_row_cannot_be_counted_twice(monkeypatch):
    arguments, reader = fixture(monkeypatch)
    arguments["physical_line_views"][1]["source_line_ids"] = ["page-1:line-0"]
    assert continuation.recognize_inline_formula_continuation(**arguments)["fragment"] is None
    assert reader.calls == 0


def test_whole_ocr_mismatch_keeps_source_fragments_and_raw_failure(monkeypatch):
    arguments, reader = fixture(monkeypatch)
    reader.text = r"\begin{aligned}y=x\\+q\end{aligned}"
    result = continuation.recognize_inline_formula_continuation(**arguments)
    assert result["status"] == "review_required"
    assert result["fragment"] is None
    assert result["whole_prediction"]["raw_output"] == f"<s>{reader.text}</s>"
    assert result["original_fragments"] == arguments["fragments"]


def test_suspected_continuation_without_physical_rows_requires_review(monkeypatch):
    arguments, reader = fixture(monkeypatch)
    arguments["physical_line_views"] = []
    result = continuation.recognize_inline_formula_continuation(**arguments)
    assert result["status"] == "review_required"
    assert result["reasons"] == ["unproven_physical_rows"]
    assert reader.calls == 0


def test_unsupported_first_row_keeps_suspected_continuation_in_review(monkeypatch):
    arguments, reader = fixture(monkeypatch)
    arguments["fragments"][0]["transcribed_text"] = r"y=\boxed{x}"
    result = continuation.recognize_inline_formula_continuation(**arguments)
    assert result["status"] == "review_required"
    assert result["reasons"] == ["row_syntax_is_not_proven_continuation"]
    assert reader.calls == 0


def test_later_equation_with_its_own_lhs_is_never_joined(monkeypatch):
    arguments, reader = fixture(monkeypatch)
    arguments["fragments"][1]["transcribed_text"] = "+z=w"
    result = continuation.recognize_inline_formula_continuation(**arguments)
    assert result["status"] == "not_applicable"
    assert result["reasons"] == ["independent_equations_have_separate_lhs"]
    assert reader.calls == 0


def test_malformed_whole_latex_is_review_evidence_not_an_uncaught_exception(monkeypatch):
    arguments, reader = fixture(monkeypatch)
    reader.text = r"\begin{aligned}y={x\\+z\end{aligned}"
    result = continuation.recognize_inline_formula_continuation(**arguments)
    assert result["status"] == "review_required"
    assert result["fragment"] is None
    assert result["syntax_evidence"]["reasons"] == ["invalid_latex_parse"]
    assert result["whole_prediction"]["transcribed_text"] == reader.text
