from copy import deepcopy

import pytest

from lib.infrastructure.docling_inline_native_preservation import prove_native_preservation


def source(text, *, math_positions=(), shifts=None):
    glyphs, native = [], []
    x, y = 0.0, 100.0
    shifts = shifts or {}
    for i, char in enumerate(text):
        if char == "\n":
            x, y = 0.0, y - 14
            continue
        dy, size = shifts.get(i, (0, 10.0))
        box = {"l": x, "b": y+dy-2, "r": x+4, "t": y+dy+7}
        glyphs.append({"char_index": i, "text": char, "codepoint": ord(char),
                       "font_name": "CMMI10" if i in math_positions else "Times-Roman",
                       "font_size": size, "origin": [x, y+dy], "bbox": box.copy(),
                       "generated": False})
        native.append({"index": i+1000, "text": char, "bbox": box.copy()})
        x += 5
    return glyphs, native


@pytest.mark.parametrize("marker", ["∗", "†", "‡"])
def test_footnote_symbols_are_preserved_without_claiming_math_correctness(marker):
    text = marker + "Generic contribution note."
    gs, ns = source(text, math_positions=[0], shifts={0: (3, 7)})
    before = deepcopy((gs, ns))
    p = prove_native_preservation(gs, ns, text, label="footnote")
    assert p["proven_glyph_ids"] == [0]
    assert p["proofs"][0]["kind"] == "footnote_symbol"
    assert not p["automatic_text_replacement_allowed"]
    assert not p["mathematical_correctness_certified"]
    assert (gs, ns) == before


def test_author_marker_needs_name_email_geometry_and_page_footnote_proof():
    foot = "†Generic affiliation note."
    fgs, fns = source(foot, math_positions=[0], shifts={0: (3, 7)})
    markers = prove_native_preservation(fgs, fns, foot, label="footnote")["proofs"]
    text = "Ada Example†\nLab\nada@example.org"
    i = text.index("†")
    gs, ns = source(text, math_positions=[i], shifts={i: (3, 7)})
    assert not prove_native_preservation(gs, ns, text)["proven_glyph_ids"]
    p = prove_native_preservation(gs, ns, text, annotation_markers=markers)
    assert p["proven_glyph_ids"] == [i]
    assert p["proofs"][0]["kind"] == "author_symbol_with_name_and_email"


def test_email_does_not_turn_a_math_star_into_an_author_marker():
    text = "Use xx∗ and contact a@example.org"
    i = text.index("∗")
    gs, ns = source(text, math_positions=[4, 5, i], shifts={i: (3, 7)})
    markers = [{"kind": "footnote_symbol", "source_text": "∗",
                "glyph_ids": [1], "native_char_ids": [2]}]
    p = prove_native_preservation(gs, ns, text, annotation_markers=markers)
    assert i not in p["proven_glyph_ids"]


@pytest.mark.parametrize("token", ["2.75", "1/4", "Q", "pos", "i", "warmup_steps", "−∞"])
def test_exact_flat_source_token_is_preserved(token):
    text = "Use " + token + " as written."
    ids = list(range(4, 4+len(token)))
    gs, ns = source(text, math_positions=ids)
    p = prove_native_preservation(gs, ns, text)
    assert set(ids) <= set(p["proven_glyph_ids"])
    proof = next(x for x in p["proofs"] if x["source_text"] == token)
    a, b = proof["backend_charspan"]
    assert text[a:b] == token


def test_math_font_decimal_point_is_part_of_complete_decimal_evidence():
    text = "Score 28.75 remains."
    point = text.index(".")
    gs, ns = source(text, math_positions=[point])
    p = prove_native_preservation(gs, ns, text)
    proof = next(x for x in p["proofs"] if point in x["glyph_ids"])
    assert proof["source_text"] == "28.75"
    assert proof["backend_text"] == "28.75"


@pytest.mark.parametrize("dy", [-3, 3])
def test_true_digit_script_is_not_exempted_when_backend_flattens_it(dy):
    text = "Use x2 here."
    gs, ns = source(text, math_positions=[4, 5], shifts={5: (dy, 7)})
    assert not prove_native_preservation(gs, ns, text)["proven_glyph_ids"]


def test_complete_decimal_inside_a_superscript_is_not_flat_preservation():
    text = "Use x2.5 here."
    gs, ns = source(text, math_positions=range(4, 8),
                    shifts={i: (3, 7) for i in range(5, 8)})
    assert not prove_native_preservation(gs, ns, text)["proven_glyph_ids"]


def test_small_script_offset_still_rejects_base_and_script():
    text = "Use qi here."
    gs, ns = source(text, math_positions=[4, 5], shifts={5: (-1, 6)})
    assert not prove_native_preservation(gs, ns, text)["proven_glyph_ids"]


def test_vertically_stacked_math_letters_are_not_flat_preservation():
    gs, ns = source("x\ny", math_positions=[0, 2])
    assert not prove_native_preservation(gs, ns, "x y")["proven_glyph_ids"]


def test_plain_font_scalar_in_prose_is_proven_but_bare_stacked_letters_are_not():
    text = "Given z, continue."
    gs, ns = source(text)
    assert text.index("z") in prove_native_preservation(gs, ns, text)["proven_glyph_ids"]
    gs, ns = source("z\nw")
    assert not prove_native_preservation(gs, ns, "z w")["proven_glyph_ids"]


def test_complete_roman_subscript_label_is_not_a_flat_identifier():
    text = "Use dmodel here."
    gs, ns = source(text, math_positions=[4],
                    shifts={i: (-3, 7) for i in range(5, 10)})
    assert 4 not in prove_native_preservation(gs, ns, text)["proven_glyph_ids"]


def test_clipped_identifier_cannot_match_a_suffix_inside_backend_identifier():
    gs, ns = source("steps", math_positions=range(5))
    assert not prove_native_preservation(gs, ns, "warmup_steps")["proven_glyph_ids"]


def test_whitespace_alignment_does_not_merge_or_split_identifiers():
    text = "Use q as written."
    gs, ns = source(text, math_positions=[4])
    for backend in ["Useq as written.", "Use qas written."]:
        assert 4 not in prove_native_preservation(gs, ns, backend)["proven_glyph_ids"]
    text = "Use warmup_steps as written."
    ids = list(range(4, 16))
    gs, ns = source(text, math_positions=ids)
    p = prove_native_preservation(gs, ns, "Use warmup_ steps as written.")
    assert not set(ids) & set(p["proven_glyph_ids"])


def test_neighboring_prose_line_does_not_make_a_flat_variable_a_script():
    text = "Use q as written.\nUse k as written."
    ids = [text.index("q"), text.index("k")]
    gs, ns = source(text, math_positions=ids)
    p = prove_native_preservation(gs, ns, text)
    assert set(ids) <= set(p["proven_glyph_ids"])


def test_touching_math_base_letters_keep_neighbor_subscript_guard():
    text = "Use P Epos here."
    ids = [i for i, c in enumerate(text) if 4 <= i <= 9 and not c.isspace()]
    gs, ns = source(text, math_positions=ids,
                    shifts={i: (-1, 6) for i in range(7, 10)})
    # PDFium's generated inter-letter space can make the two base letters
    # separate runs even though they touch in the visible source.
    for g in gs:
        if g["char_index"] >= 6:
            g["origin"][0] -= 5
            g["bbox"]["l"] -= 5
            g["bbox"]["r"] -= 5
    for n in ns:
        if n["index"] >= 1006:
            n["bbox"]["l"] -= 5
            n["bbox"]["r"] -= 5
    p = prove_native_preservation(gs, ns, text)
    assert not set(ids) & set(p["proven_glyph_ids"])


def test_exact_local_source_context_can_survive_unrelated_formula_encoding():
    native_text = "∑ elsewhere. Use q as written, then finish."
    backend = "P elsewhere. Use q as written, then finish."
    i = native_text.index("q")
    gs, ns = source(native_text, math_positions=[0, i])
    p = prove_native_preservation(gs, ns, backend)
    assert i in p["proven_glyph_ids"]
    proof = next(x for x in p["proofs"] if i in x["glyph_ids"])
    assert proof["alignment_method"] == "unique_exact_native_context"
    assert 0 not in p["proven_glyph_ids"]


def test_duplicate_local_context_is_not_a_unique_source_mapping():
    text = "Different. Use q as written. Done."
    gs, ns = source(text, math_positions=[text.index("q")])
    backend = "Use q as written. Use q as written."
    assert not prove_native_preservation(gs, ns, backend)["proven_glyph_ids"]


def test_changed_or_missing_token_is_not_preservation():
    text = "Use Q as written."
    gs, ns = source(text, math_positions=[4])
    for backend in ["Use O as written.", "Use as written."]:
        assert not prove_native_preservation(gs, ns, backend)["proven_glyph_ids"]


def test_generated_text_and_duplicate_native_ids_cannot_supply_proof():
    text = "Use q as written."
    gs, ns = source(text, math_positions=[4])
    gs[4]["generated"] = True
    assert 4 not in prove_native_preservation(gs, ns, text)["proven_glyph_ids"]
    gs[4]["generated"] = False
    ns[1]["index"] = ns[0]["index"]
    assert not prove_native_preservation(gs, ns, text)["proven_glyph_ids"]


def test_negative_infinity_alias_is_explicit_and_no_other_symbol_is_folded():
    text = "Mask −∞ before use."
    ids = [5, 6]
    gs, ns = source(text, math_positions=ids)
    ns[5]["text"] = "-"
    p = prove_native_preservation(gs, ns, text.replace("−", "-"))
    proof = next(x for x in p["proofs"] if x["source_text"] == "−∞")
    assert proof["glyph_native_alias"] == "unicode_minus_to_ascii_minus_before_infinity"
    assert proof["backend_text"] == "-∞"


@pytest.mark.parametrize("punctuation", [",", ";", "."])
def test_prose_punctuation_after_proven_flat_literal_has_own_exact_proof(punctuation):
    text = "Given z" + punctuation + " continue normally."
    gs, ns = source(text)
    p = prove_native_preservation(gs, ns, text)
    proof = next(x for x in p["proofs"] if x["kind"] == "adjacent_prose_punctuation")
    assert proof["glyph_ids"] == [7]
    assert proof["preceding_proven_glyph_id"] == 6
    assert proof["backend_charspan"] == [7, 8]
    assert proof["backend_text"] == punctuation


def test_punctuation_cannot_prove_math_delimiters_operators_or_scripts():
    for token in ["z)", "z+", "z!", "z:"]:
        text = "Given " + token + " continue normally."
        gs, ns = source(text)
        p = prove_native_preservation(gs, ns, text)
        assert not any(x["kind"] == "adjacent_prose_punctuation" for x in p["proofs"])
    text = "Given z2, continue normally."
    gs, ns = source(text, math_positions=[6, 7], shifts={7: (-2, 7)})
    assert 8 not in prove_native_preservation(gs, ns, text)["proven_glyph_ids"]


def test_math_separator_or_changed_punctuation_does_not_get_prose_proof():
    text = "Given z, q plus more."
    gs, ns = source(text, math_positions=[6, 9])
    p = prove_native_preservation(gs, ns, text)
    assert 7 not in p["proven_glyph_ids"]
    text = "Given z, continue normally."
    gs, ns = source(text)
    assert 7 not in prove_native_preservation(gs, ns, text.replace(",", ";"))["proven_glyph_ids"]
