from copy import deepcopy

import pytest

from lib.infrastructure.docling_math_style import validate_math_style


def source(text, weights=400):
    weights = [weights] * len(text) if not isinstance(weights, (list, tuple)) else weights
    return [dict(char_index=i, text=char, font_weight=weights[i], generated=False)
            for i, char in enumerate(text)]


def program_proof(index=0, text="x", supports=True):
    return {index: dict(pdfium_char_index=index, comparison_text=text, pdfium_original_text=text,
                        font_program_sha256="program-hash", source_sha256="pdf-hash",
                        source_weight={"declaration": "Bold" if supports else "Regular",
                                       "supports_bold": supports})}


@pytest.mark.parametrize("command", ["mathbf", "boldsymbol", "bm", "textbf"])
def test_explicit_bold_on_regular_source_is_rejected_without_changing_latex(command):
    glyphs = source("b2")
    latex = "\\" + command + "{b}_2"
    before = deepcopy(glyphs)
    result = validate_math_style(glyphs, latex)
    assert result["review_required"]
    assert result["reasons"] == ["source_font_weight_does_not_support_bold"]
    assert result["bold_commands"][0]["command"] == command
    assert [target["text"] for target in result["targets"]] == ["b"]
    assert not result["latex_modified"] and not result["mathematical_correctness_certified"]
    assert glyphs == before


@pytest.mark.parametrize("weight", [None, -1, 0, "700", True, float("nan"), 1001])
def test_unknown_or_invalid_source_weight_is_not_inferred_from_bold_font_name_or_flags(weight):
    glyphs = source("x", weight)
    glyphs[0].update(font_name="Looks-Bold", font_flags=1 << 18)
    result = validate_math_style(glyphs, r"\mathbf{x}")
    assert result["reasons"] == ["source_font_weight_unknown"]
    assert result["review_required"]


@pytest.mark.parametrize("latex", [r"\mathbb{R}", r"\mathrm{x}", r"\mathit{x}", "x_2"])
def test_nonbold_styles_do_not_require_font_weight(latex):
    result = validate_math_style([], latex)
    assert not result["review_required"]
    assert result["bold_commands"] == result["targets"] == []


def test_bold_scope_does_not_include_a_subscript_outside_the_macro():
    result = validate_math_style(source("b2", [700, 400]), r"\mathbf{b}_2")
    assert not result["review_required"]
    assert [target["source_char_index"] for target in result["targets"]] == [0]
    result = validate_math_style(source("b2", [700, 400]), r"\mathbf{b_2}")
    assert result["review_required"]
    assert result["targets"][1]["source_char_index"] == 1
    assert not result["targets"][1]["supported"]


@pytest.mark.parametrize("command", ["boldsymbol", "bm"])
def test_greek_target_is_decoded_by_latex_ast(command):
    result = validate_math_style(source("α", 700), "\\" + command + r"{\alpha}")
    assert not result["review_required"]
    assert result["targets"][0]["text"] == "α"


def test_repeated_variables_require_exact_full_order_and_never_use_heaviest_match():
    glyphs = source("x+x", [400, 400, 700])
    first = validate_math_style(glyphs, r"\mathbf{x}+x")
    assert first["review_required"] and first["targets"][0]["source_char_index"] == 0
    second = validate_math_style(list(reversed(glyphs)), r"x+\mathbf{x}")
    assert not second["review_required"]
    assert second["targets"][0]["source_char_index"] == 2
    assert second["targets"][0]["alignment_method"] == "exact_complete_ordered_sequence"
    ambiguous = validate_math_style(glyphs, r"\mathbf{x}x")
    assert ambiguous["reasons"] == ["bold_source_character_not_unique"]


def test_structural_source_order_difference_only_allows_a_unique_target():
    result = validate_math_style(source("ba", [400, 700]), r"\frac{\mathbf{a}}{b}")
    assert not result["review_required"]
    assert result["targets"][0]["source_char_index"] == 1
    assert result["targets"][0]["alignment_method"] == "unique_source_symbol"


@pytest.mark.parametrize("latex", [r"\mathbf{", r"\bm", r"\boldsymbol{}",
                                   r"\mathbf{\unknown}", r"\mathbf{\mathbb{R}}",
                                   r"\boldsymbol{\frac{a}{b}}"])
def test_unsupported_bold_syntax_does_not_silently_pass(latex):
    assert validate_math_style(source("Rab", 700), latex)["review_required"]


def test_exact_source_program_bold_declaration_takes_precedence_over_estimated_weight():
    glyphs, proofs = source("x", 570), program_proof()
    before = deepcopy((glyphs, proofs))
    result = validate_math_style(glyphs, r"\textbf{x}", glyph_style_proofs=proofs)
    assert not result["review_required"]
    assert result["targets"][0]["weight_source"] == "embedded_font_program"
    assert result["targets"][0]["source_font_proof"] == proofs[0]
    assert (glyphs, proofs) == before


def test_regular_program_and_unknown_declaration_cannot_fall_back_to_high_pdfium_weight():
    regular = validate_math_style(source("x", 900), r"\mathbf{x}",
                                  glyph_style_proofs=program_proof(supports=False))
    assert regular["reasons"] == ["source_font_program_does_not_support_bold"]
    unknown = validate_math_style(source("x", 900), r"\mathbf{x}",
                                  glyph_style_proofs=program_proof(supports=None))
    assert unknown["reasons"] == ["source_font_program_weight_unknown"]


@pytest.mark.parametrize("change", [
    {"pdfium_char_index": 99}, {"comparison_text": "y", "pdfium_original_text": "y"},
    {"font_program_sha256": None}, {"source_sha256": None},
])
def test_stale_or_unbound_program_proof_is_rejected(change):
    proofs = program_proof()
    proofs[0].update(change)
    result = validate_math_style(source("x", 900), r"\mathbf{x}", glyph_style_proofs=proofs)
    assert result["reasons"] == ["invalid_source_font_style_binding"]


def test_generated_and_duplicate_source_characters_do_not_supply_style_proof():
    glyphs = source("x", 700)
    glyphs[0]["generated"] = True
    assert validate_math_style(glyphs, r"\mathbf{x}")["reasons"] == ["source_style_glyph_generated"]
    glyphs[0]["generated"] = False
    assert validate_math_style(glyphs * 2, r"\mathbf{x}")["reasons"] == ["invalid_source_style_glyphs"]


def test_pdfium_font_weight_is_recorded_without_inferring_missing_values(monkeypatch):
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    import pypdfium2.raw as raw
    from lib.infrastructure.docling_math_regions import extract_pdf_page_glyphs

    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 40 Td (x) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    content = BytesIO()
    writer.write(content)
    values = []
    for weight in (400, 700, -1, 0):
        monkeypatch.setattr(raw, "FPDFText_GetFontWeight", lambda *args, value=weight: value)
        glyphs = extract_pdf_page_glyphs(content.getvalue(), 1)["glyphs"]
        values.append(next(glyph["font_weight"] for glyph in glyphs if glyph["text"] == "x"))
    assert values == [400, 700, None, None]
