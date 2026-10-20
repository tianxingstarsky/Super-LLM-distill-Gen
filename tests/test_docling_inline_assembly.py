from lib.infrastructure.docling_inline_assembly import assemble_inline_formulas
import pytest


def assembly(original, native, spans):
    chars = [{"index": index, "text": char} for index, char in enumerate(native)]
    fragments = [{"fragment_id": str(index), "status": "transcribed_unverified",
                  "native_char_indices": list(range(start, end)), "transcribed_text": latex}
                 for index, (start, end, latex) in enumerate(spans)]
    return assemble_inline_formulas(original, chars, fragments)


def test_reordered_script_glyphs_do_not_require_rewriting_prose():
    output = assembly("Apply x 2 i, then stop.", "Applyx2i,thenstop.", [(5, 8, r"x_i^2")])
    assert output["text"] == r"Apply $x_i^2$, then stop."
    assert output["prose_preserved"]


def test_prose_between_formulas_and_original_whitespace_survive():
    output = assembly("Use  x. It differs\nfrom y!", "Usex.Itdiffersfromy!",
                      [(3, 4, "x"), (18, 19, "y")])
    assert output["text"] == "Use  $x$. It differs\nfrom $y$!"
    assert len(output["patches"]) == 2


def test_adjacent_boxes_cannot_invent_a_formula_join():
    output = assembly("Apply xy now.", "Applyxynow.", [(5, 6, "x"), (6, 7, "y")])
    assert output["status"] == "review_required"
    assert output["text"] == "Apply xy now."


def test_exact_native_wrapped_spans_keep_separate_math_and_source_newline():
    native = "g(t)=a0\n+a1t, for real t."
    output = assembly(native, native, [(0, 7, r"g(t)=a_0"), (8, 12, r"+a_1t")])
    assert output["text"] == "$g(t)=a_0$\n$+a_1t$, for real t."
    assert output["alignment_method"] == "exact_native_sequence"
    assert output["prose_preserved"]
    assert len(output["patches"]) == 2


def test_adjacent_independent_equations_remain_two_spans():
    output = assembly("u=x2  v=3x", "u=x2 v=3x", [(0, 4, r"u=x^2"), (5, 9, "v=3x")])
    assert output["text"] == "$u=x^2$  $v=3x$"
    assert len(output["patches"]) == 2


def test_exact_alignment_cannot_split_one_unicode_scalar():
    output = assembly("ﬁ", "fi", [(0, 1, "f"), (1, 2, "i")])
    assert output["status"] == "review_required"
    assert output["text"] == "ﬁ"


def test_prose_missing_from_source_or_target_refuses_whole_patch():
    output = assembly("Use x but keep this.", "Usexkeepthis.", [(3, 4, "x")])
    assert output["status"] == "review_required"
    assert output["text"] == "Use x but keep this."


def test_repeated_prose_boundaries_are_ambiguous():
    output = assembly("x and y and z", "xandy",
                      [(0, 1, "x"), (4, 5, "y")])
    assert output["status"] == "review_required"


def test_native_order_crossing_prose_cannot_delete_it():
    chars = [{"index": i, "text": char} for i, char in enumerate("x prose y")]
    output = assemble_inline_formulas("x prose y", chars, [
        {"status": "transcribed_unverified", "native_char_indices": [0, 8],
         "transcribed_text": "x+y"}])
    assert output["review_reasons"] == ["noncontiguous_native_formula"]


def test_one_review_fragment_blocks_partial_paragraph_publication():
    output = assemble_inline_formulas("x", [{"index": 0, "text": "x"}], [
        {"status": "transcribed_unverified", "native_char_indices": [0],
         "transcribed_text": "y", "review_required": True}])
    assert output["text"] == "x"
    assert output["patches"] == []


def test_ligature_outside_math_retains_original_glyph():
    output = assembly("ﬁnd x.", "findx.", [(4, 5, "x")])
    assert output["text"] == "ﬁnd $x$."


@pytest.mark.parametrize("latex", [
    "x$ unexpected $", "$x$", "$$x$$", r"x\\$ prose", r"\(x\)", r"\[x\]",
    "x\nnew paragraph", "x\rnew paragraph", "x\u2028line", "x\u2029paragraph", "x\x00", "", None,
])
def test_invalid_inline_math_body_preserves_entire_paragraph(latex):
    result = assembly("Use x and y.", "Usexandy.", [(3, 4, "x"), (7, 8, latex)])
    assert result["status"] == "review_required"
    assert result["review_reasons"] == ["invalid_inline_latex_body"]
    assert result["text"] == "Use x and y."
    assert result["patches"] == []
    assert not result["prose_preserved"]


def test_escaped_literal_dollar_does_not_escape_math_wrapper():
    result = assembly("Use x.", "Usex.", [(3, 4, r"x+\$")])
    assert result["text"] == r"Use $x+\$$."
    assert result["prose_preserved"]


def wrapped_native(text, hyphen_index):
    chars = []
    for index, char in enumerate(text):
        wrapped = index > hyphen_index
        x = 10 + (index - hyphen_index - 1) * 4 if wrapped else 80 + index * 4
        y = 20 if wrapped else 40
        b, t = (y + 3, y + 4) if index == hyphen_index else (y, y + 8)
        chars.append({"index": index, "text": char,
                      "bbox": {"l": x, "b": b, "r": x + 3, "t": t, "coord_origin": "BOTTOMLEFT"}})
    return chars


def test_native_line_end_hyphen_fallback_preserves_backend_prose_and_audits_id():
    native = "A transfor-mation x."
    index = native.index("-")
    chars = wrapped_native(native, index)
    result = assemble_inline_formulas("A transformation x.", chars, [{
        "status": "transcribed_unverified", "native_char_indices": [native.index("x")],
        "transcribed_text": "x",
    }])
    assert result["text"] == "A transformation $x$."
    assert result["alignment_method"] == "native_line_end_hyphen_anchor_fallback"
    assert result["discarded_native_hyphen_ids"] == [index]
    assert result["prose_preserved"]


def test_genuine_hyphenated_word_uses_strict_alignment_first():
    native = "A cross-section x."
    chars = wrapped_native(native, native.index("-"))
    result = assemble_inline_formulas(native, chars, [{
        "status": "transcribed_unverified", "native_char_indices": [native.index("x")],
        "transcribed_text": "x",
    }])
    assert result["text"] == "A cross-section $x$."
    assert result["alignment_method"] == "strict"
    assert result["discarded_native_hyphen_ids"] == []


@pytest.mark.parametrize("damage", ["same_line", "missing_box", "not_line_end", "not_line_start", "nonletter"])
def test_unproved_native_hyphen_never_disappears_from_alignment(damage):
    native = "A transfor-mation x."
    index = native.index("-")
    chars = wrapped_native(native, index)
    if damage == "same_line":
        chars[index + 1]["bbox"] = {"l": 200, "b": 40, "r": 203, "t": 48}
    elif damage == "missing_box":
        chars[index].pop("bbox")
    elif damage == "not_line_end":
        chars[0]["bbox"] = {"l": 230, "b": 40, "r": 233, "t": 48}
    elif damage == "not_line_start":
        chars[0]["bbox"] = {"l": 1, "b": 20, "r": 4, "t": 28}
    else:
        chars[index + 1]["text"] = "2"
    result = assemble_inline_formulas("A transformation x.", chars, [{
        "status": "transcribed_unverified", "native_char_indices": [native.index("x")],
        "transcribed_text": "x",
    }])
    assert result["status"] == "review_required"
    assert result["text"] == "A transformation x."
    assert result["discarded_native_hyphen_ids"] == []


def test_hyphen_inside_formula_is_never_removed_even_with_wrap_geometry():
    native = "Use x-y."
    chars = wrapped_native(native, native.index("-"))
    result = assemble_inline_formulas("Use xy.", chars, [{
        "status": "transcribed_unverified",
        "native_char_indices": [native.index("x"), native.index("-"), native.index("y")],
        "transcribed_text": "x-y",
    }])
    assert result["status"] == "review_required"
    assert result["review_reasons"] == ["source_formula_characters_changed"]
    assert result["text"] == "Use xy."
    assert result["discarded_native_hyphen_ids"] == []


def test_ambiguous_alignment_remains_blocked_after_dehyphenation():
    native = "x transfor-mation y"
    chars = wrapped_native(native, native.index("-"))
    result = assemble_inline_formulas("x transformation y transformation z", chars, [
        {"status": "transcribed_unverified", "native_char_indices": [0], "transcribed_text": "x"},
        {"status": "transcribed_unverified", "native_char_indices": [len(native) - 1], "transcribed_text": "y"},
    ])
    assert result["status"] == "review_required"
    assert result["text"] == "x transformation y transformation z"
