from copy import deepcopy

import pytest

from lib.infrastructure.docling_inline_boundaries import (
    mathematical_boundary_proof, separate_linked_citation,
)


def glyphs(text, *, roman=""):
    return [{"char_index": i, "text": char,
             "font_name": "Times" if char in roman or not char.isalpha() else "CMMI10",
             "font_size": 10, "origin": [i * 5, 10],
             "bbox": {"l": i * 5, "r": i * 5 + 4, "b": 10, "t": 18}}
            for i, char in enumerate(text)]


def test_balanced_roman_parentheses_do_not_make_a_formula_ambiguous():
    for text in ("(x1,...,xn)", "O(logk(n))", "O(k*n*d+n*d2)"):
        proof = mathematical_boundary_proof(glyphs(text))
        assert proof["proven"] and proof["balanced_pairs"]


def test_unbalanced_or_ordinary_words_still_require_review():
    assert not mathematical_boundary_proof(glyphs("(x+y"))["proven"]
    assert not mathematical_boundary_proof(glyphs("xforrealinputt", roman="forrealinput"))["proven"]
    assert not mathematical_boundary_proof(glyphs("xandy", roman="and"))["proven"]


def test_roman_function_call_and_bold_assignment_are_source_backed():
    assert mathematical_boundary_proof(glyphs("(x+Sublayer(x))", roman="Sublayer"))["proven"]
    assert mathematical_boundary_proof(glyphs("z=(x,y)", roman="z"))["proven"]
    assert not mathematical_boundary_proof(glyphs("use(x)", roman="usex"))["proven"]


@pytest.mark.parametrize("text", [
    "where(x)", "where (x)", "x+where (x)", "x+whe re(x)", "x+ please(x)",
])
@pytest.mark.parametrize("omit_spaces", [False, True])
def test_prose_and_source_spaces_cannot_be_reinterpreted_as_roman_functions(text, omit_spaces):
    data = glyphs(text, roman="whereplas")
    # Match geometry's nonspace view without renumbering source character IDs.
    if omit_spaces:
        data = [g for g in data if not g["text"].isspace()]
    before = deepcopy(data)
    proof = mathematical_boundary_proof(data)
    assert not proof["proven"]
    assert proof["reason"] == "unclassified_ordinary_letters"
    assert data == before


def test_roman_function_proof_uses_source_structure_not_a_name_allowlist():
    data = glyphs("(x+Transform(y))", roman="Transform")
    proof = mathematical_boundary_proof(data)
    assert proof["proven"]
    assert proof["roman_math_char_indices"] == list(range(3, 12))


@pytest.mark.parametrize("change", ["missing_operator", "operator_other_line", "variable_other_line"])
def test_roman_function_needs_an_operator_and_existing_same_line_math_variable(change):
    data = glyphs("x+Transform(y)", roman="Transform")
    if change == "missing_operator":
        data[1]["text"] = ","
    else:
        data[1 if change == "operator_other_line" else 0]["origin"][1] += 15
    assert not mathematical_boundary_proof(data)["proven"]


def test_missing_source_character_in_function_name_is_never_assumed_to_be_whitespace_free():
    data = glyphs("x+Function(y)", roman="Function")
    for g in data[5:]:
        g["char_index"] += 1
    assert not mathematical_boundary_proof(data)["proven"]


def spaced_function_source(text="(x + Transform(y))"):
    data = glyphs(text, roman="Transform")
    for glyph in data:
        if glyph["text"].isspace():
            # PDFium may generate zero-area spacing with no original font.
            x, y = glyph["origin"]
            glyph.update(generated=True, font_name="", font_size=1,
                         bbox={"l": x, "r": x, "b": y, "t": y})
    return data


@pytest.mark.parametrize("omit_spaces", [False, True])
def test_parenthesized_math_allows_only_independently_proven_source_spacing(omit_spaces):
    source = spaced_function_source()
    chosen = [g for g in source if not g["text"].isspace()] if omit_spaces else source
    before = deepcopy(source)
    assert not mathematical_boundary_proof(chosen)["proven"]
    proof = mathematical_boundary_proof(chosen, source_glyphs=source)
    assert proof["proven"]
    assert proof["roman_function_contexts"] == [{
        "method": "enclosed_math_source_whitespace",
        "outer_pair": [0, 17], "function_pair": [14, 16],
        "variable_char_index": 1, "operator_char_index": 3, "name_char_index": 5,
        "source_whitespace_char_indices": [2, 4],
    }]
    assert source == before


@pytest.mark.parametrize("fault", [
    "missing", "duplicate", "visible", "newline", "other_line",
    "outside_gap", "source_endpoint_mismatch",
])
def test_parenthesized_function_rejects_incomplete_or_conflicting_whitespace_proof(fault):
    source = spaced_function_source()
    chosen = deepcopy([g for g in source if not g["text"].isspace()])
    if fault == "missing":
        source = [g for g in source if g["char_index"] != 4]
    elif fault == "duplicate":
        source.append(deepcopy(source[4]))
    elif fault in {"visible", "newline"}:
        source[4]["text"] = "a" if fault == "visible" else "\n"
    elif fault == "other_line":
        source[4]["origin"][1] += 10
    elif fault == "outside_gap":
        source[4]["origin"][0] += 50
    else:
        source[3]["text"] = "-"
    assert not mathematical_boundary_proof(chosen, source_glyphs=source)["proven"]


@pytest.mark.parametrize("text", [
    "where (x)", "where(x)", "x+ please(x)", "(x)+ Transform(y)",
    "(x + Trans form(y))", "(x + Transform (y))",
])
def test_full_source_spacing_evidence_does_not_relax_other_prose_boundaries(text):
    source = spaced_function_source(text)
    for glyph in source:
        if glyph["text"].isalpha() and glyph["text"] not in "xy":
            glyph["font_name"] = "Times"
    chosen = [g for g in source if not g["text"].isspace()]
    assert not mathematical_boundary_proof(chosen, source_glyphs=source)["proven"]


def test_only_pdf_linked_trailing_citation_is_separated():
    data = glyphs("x=0.1[36]")
    page = {"glyphs": data}
    candidate = {"bbox": {"l": 0, "r": 46, "b": 9, "t": 20}, "label": "inline_formula"}
    before = deepcopy(candidate)
    links = [{"bbox": {"l": 30, "r": 39, "b": 9, "t": 20}, "destination": "cite.reference"}]
    result = separate_linked_citation(candidate, page, links)
    assert result["bbox"]["r"] < 25
    assert result["citation_boundary"]["preserved_prose_char_indices"] == [5, 6, 7, 8]
    assert candidate == before
    assert separate_linked_citation(candidate, page, []) == candidate


def test_internal_math_interval_or_nonterminal_reference_is_never_trimmed():
    data = glyphs("x[36]+y")
    candidate = {"bbox": {"l": 0, "r": 40, "b": 9, "t": 20}}
    links = [{"bbox": {"l": 10, "r": 19, "b": 9, "t": 20}, "destination": "cite.reference"}]
    assert separate_linked_citation(candidate, {"glyphs": data}, links) == candidate
