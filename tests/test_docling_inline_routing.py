from copy import deepcopy
from enum import Enum
from types import SimpleNamespace

import pytest

from lib.infrastructure import docling_inline_routing as routing


def glyphs_for(runs):
    glyphs = []
    for text, font, size, baseline in runs:
        x = glyphs[-1]["bbox"]["r"] + 1 if glyphs else 0
        for char in text:
            glyphs.append({
                "char_index": len(glyphs), "text": char, "font_name": font,
                "font_flags": 32, "font_size": size, "origin": [x, baseline],
                "bbox": {"l": x, "b": baseline, "r": x + 5, "t": baseline + size,
                         "coord_origin": "BOTTOMLEFT"},
            })
            x += 6
    return glyphs


def math_then_prose():
    return glyphs_for([("x+1, ", "CambriaMath", 12, 20),
                       ("for real input", "Times-Roman", 12, 20)])


def test_mixed_display_requires_probe_and_never_approves_whole_box():
    result = routing.route_inline_math_item("formula", math_then_prose())
    assert result["route"] == "mixed_formula_probe"
    assert result["ordinary_multiword_runs"][0]["words"] == ["for", "real", "input"]
    assert result["probe_required"] and result["review_required"]
    assert not result["allow_whole_formula_ocr"]
    assert not result["automatic_relabel_allowed"]
    assert not result["automatic_text_replacement_allowed"]
    assert not result["continuation_inferred"]


def test_spelling_does_not_control_mixed_display_detection():
    glyphs = glyphs_for([("q=2 ", "CambriaMath", 12, 20),
                        ("zebra velvet", "Times-Roman", 12, 20)])
    assert routing.route_inline_math_item("formula", glyphs)["route"] == "mixed_formula_probe"


def test_separate_named_operators_preserve_display_path():
    glyphs = glyphs_for([("Attention", "Times-Roman", 12, 20),
                        ("(Q,K,V)=", "CambriaMath", 12, 20),
                        ("softmax", "Times-Roman", 12, 20),
                        ("(QK)V", "CambriaMath", 12, 20)])
    result = routing.route_inline_math_item("formula", glyphs)
    assert result["route"] == "display"
    assert result["allow_whole_formula_ocr"]
    assert result["ordinary_multiword_runs"] == []


def test_words_on_distinct_baselines_do_not_form_prose_sequence():
    glyphs = glyphs_for([("x+1", "CambriaMath", 12, 20),
                        ("alpha ", "Times-Roman", 12, 20),
                        ("beta", "Times-Roman", 12, 3)])
    assert routing.route_inline_math_item("formula", glyphs)["route"] == "display"


def test_roman_script_labels_do_not_become_main_line_prose():
    glyphs = glyphs_for([("d", "CambriaMath", 12, 20),
                        ("model head", "Times-Roman", 7, 16)])
    result = routing.route_inline_math_item("formula", glyphs)
    assert result["route"] == "display"


def test_unknown_code_with_math_font_is_probe_only():
    result = routing.route_inline_math_item("code", math_then_prose())
    assert result["route"] == "code_math_probe"
    assert result["preserve_original_on_failure"]
    assert not result["automatic_relabel_allowed"]


def test_declared_language_protects_code_even_with_math_fonts():
    class Language(str, Enum):
        PYTHON = "Python"

    result = routing.route_inline_math_item(
        "code", math_then_prose(), code_language=Language.PYTHON)
    assert result["route"] == "preserve_code"
    assert not result["probe_required"]


def test_native_fixed_pitch_flag_protects_unknown_code():
    glyphs = math_then_prose()
    glyphs[-1]["font_flags"] |= 1
    result = routing.route_inline_math_item("code", glyphs)
    assert result["route"] == "preserve_code"
    assert result["reasons"] == ["fixed_pitch_code"]


def test_ordinary_code_operators_do_not_trigger_math_probe():
    glyphs = glyphs_for([("x = a + b", "Times-Roman", 12, 20)])
    assert routing.route_inline_math_item("code", glyphs)["route"] == "preserve_code"


@pytest.mark.parametrize("bad", [None, [0], [0, float("nan")]])
def test_missing_native_baseline_cannot_clear_a_formula_guard(bad):
    glyphs = math_then_prose()
    glyphs[0]["origin"] = bad
    result = routing.route_inline_math_item("formula", glyphs)
    assert result["route"] == "review"
    assert not result["allow_whole_formula_ocr"]


def item(ref, label, page=1, bbox=None, language="unknown"):
    return SimpleNamespace(self_ref=ref, label=label, code_language=language,
                           prov=[SimpleNamespace(page_no=page, bbox=bbox or {
                               "l": 0, "b": 0, "r": 200, "t": 40,
                               "coord_origin": "BOTTOMLEFT"})])


def test_route_map_caches_page_and_only_contains_formula_code(monkeypatch):
    glyphs = math_then_prose()
    calls = []

    def extract(source, page):
        calls.append((source, page))
        return {"height": 100, "glyphs": glyphs, "source_sha256": "source",
                "coordinate_space": {"page_rotation": 0}}

    monkeypatch.setattr(routing, "extract_pdf_page_glyphs", extract)
    document = SimpleNamespace(texts=[
        item("formula", "formula"), item("code", "code"), item("text", "text")])
    original = deepcopy(document)
    result = routing.build_inline_route_map(document, "source.pdf")
    assert calls == [("source.pdf", 1)]
    assert set(result) == {"formula", "code"}
    assert result["formula"]["source_sha256"] == "source"
    assert result["formula"]["original_label"] == "formula"
    assert result["code"]["route"] == "code_math_probe"
    assert document == original


def test_route_map_includes_glyph_intersection_when_center_is_outside(monkeypatch):
    glyphs = glyphs_for([("x", "CambriaMath", 12, 20)])
    monkeypatch.setattr(routing, "extract_pdf_page_glyphs", lambda *args: {
        "height": 100, "glyphs": glyphs, "source_sha256": "source"})
    document = SimpleNamespace(texts=[item("code", "code", bbox={
        "l": 0, "b": 0, "r": 1, "t": 40, "coord_origin": "BOTTOMLEFT"})])
    assert routing.build_inline_route_map(document, "source.pdf")["code"]["native_char_indices"] == [0]


def test_orphan_formula_rejects_unassignable_review():
    orphan = item("formula", "formula")
    orphan.prov = []
    with pytest.raises(ValueError, match="invalid_provenance"):
        routing.build_inline_route_map(SimpleNamespace(texts=[orphan]), "unused.pdf")
