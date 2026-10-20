from copy import deepcopy

import pytest

from lib.infrastructure.docling_native_font_evidence import (
    _font_maps, decode_source_font_character, reconcile_native_font_evidence,
)


def font_resource(glyph_name="summationtext", *, code=80, tounicode=None):
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    program = DecodedStreamObject()
    program.set_data(
        f"%!PS-AdobeFont-1.0\n/Encoding 256 array\ndup {code} /{glyph_name} put\nreadonly def\neexec\n".encode()
    )
    descriptor = DictionaryObject({NameObject("/FontFile"): program})
    font = DictionaryObject({
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/ABCDEF+Symbolic"),
        NameObject("/FontDescriptor"): descriptor,
    })
    if tounicode is not None:
        stream = DecodedStreamObject()
        stream.set_data(
            ("begincmap\n1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
             "1 beginbfchar\n<%02X> <%s>\nendbfchar\nendcmap\n" %
             (code, tounicode.encode("utf-16-be").hex())).encode()
        )
        font[NameObject("/ToUnicode")] = stream
    return font


def source_record(native_text="∑", *, canonical_name="summationtext", code=80):
    bounds = dict(l=10, b=20, r=20, t=30, coord_origin="BOTTOMLEFT")
    proof = decode_source_font_character(_font_maps(font_resource(canonical_name, code=code)),
                                         code, canonical_name)
    return dict(
        native_index=1, native_text=native_text, native_bbox=bounds,
        font_name="/ABCDEF+Symbolic", font_key="/F7", origin=[10, 22], font_decoding=proof,
    )


def pair(native_text="∑", pdfium_text="P", *, record=None):
    record = record or source_record(native_text)
    native = [{"index": 1, "text": native_text, "bbox": dict(record["native_bbox"])}]
    glyphs = [{"char_index": 9, "text": pdfium_text, "font_name": "Symbolic",
               "origin": [10.00001, 22.00001], "generated": False}]
    page = dict(page_no=2, source_sha256="same-source", records=[record], review_required=False)
    return native, glyphs, page


def test_missing_tounicode_uses_embedded_encoding_and_upstream_glyph_database():
    result = decode_source_font_character(_font_maps(font_resource()), 80, "summationtext")
    assert not result["review_required"]
    assert result["comparison_text"] == "∑"
    assert result["decoding_source"] == "embedded_Type1_Encoding"
    assert result["font_program_sha256"]
    assert result["tounicode_text"] is None


def test_explicit_minus_tounicode_agrees_with_embedded_font():
    result = decode_source_font_character(_font_maps(
        font_resource("minus", code=0, tounicode="−")), 0, "")
    assert not result["review_required"]
    assert result["comparison_text"] == "−"
    assert result["decoding_source"] == "ToUnicode"
    assert result["embedded_text"] == "−"
    assert result["tounicode_sha256"]


def test_tounicode_conflicting_with_painted_glyph_encoding_is_not_overridden():
    result = decode_source_font_character(_font_maps(font_resource(tounicode="P")), 80, "")
    assert result["review_required"]
    assert result["reason"] == "conflicting_source_font_mappings"


def test_unknown_glyph_is_not_guessed_from_character_code():
    result = decode_source_font_character(_font_maps(font_resource("privateUnknownShape")), 80, "")
    assert result["review_required"]
    assert result["reason"] == "missing_source_font_mapping"


def test_pdf_encoding_override_defers_instead_of_assuming_embedded_encoding():
    from pypdf.generic import NameObject
    font = font_resource()
    font[NameObject("/Encoding")] = NameObject("/WinAnsiEncoding")
    assert _font_maps(font) is None


def test_literal_p_font_stays_p_without_any_global_sum_alias():
    maps = _font_maps(font_resource("P"))
    result = decode_source_font_character(maps, 80, "P")
    assert result["comparison_text"] == "P"
    native, glyphs, page = pair("P", "P", record=source_record("P", canonical_name="P"))
    assert not reconcile_native_font_evidence(native, glyphs, page)["corrections"]


def test_source_bound_sum_correction_retains_original_records():
    inputs = pair()
    before = deepcopy(inputs)
    result = reconcile_native_font_evidence(*inputs)
    assert inputs == before
    assert result["native_comparison_text"] == {1: "∑"}
    assert result["glyph_comparison_text"] == {9: "∑"}
    assert result["corrections"][0]["pdfium_original_text"] == "P"
    assert not result["source_text_modified"]
    assert not result["formula_correctness_verified"]


def test_source_bound_minus_corrects_comparison_only():
    record = source_record("-", canonical_name="minus", code=0)
    inputs = pair("-", "−", record=record)
    result = reconcile_native_font_evidence(*inputs)
    assert result["native_comparison_text"] == {1: "−"}
    assert inputs[0][0]["text"] == "-"


def test_latin_v_never_aliases_greek_upsilon():
    record = source_record("v", canonical_name="v", code=118)
    result = reconcile_native_font_evidence(*pair("v", "v", record=record))
    assert result["native_comparison_text"] == result["glyph_comparison_text"] == {}


def test_neither_source_layer_corroborates_font_decoding():
    result = reconcile_native_font_evidence(*pair("Q", "P", record=source_record("Q")))
    assert not result["corrections"]
    assert result["rejections"][0]["reason"] == "font_decoding_not_corroborated"


@pytest.mark.parametrize("change", [
    {"font_name": "DifferentFont"},
    {"origin": [10.1, 22]},
    {"generated": True},
    {"origin": None},
    {"origin": [float("nan"), 22]},
])
def test_font_origin_or_generated_character_cannot_bind_evidence(change):
    native, glyphs, page = pair()
    glyphs[0].update(change)
    result = reconcile_native_font_evidence(native, glyphs, page)
    assert not result["corrections"]
    assert result["rejections"][0]["reason"] == "nonunique_pdfium_origin"


def test_overprinted_equal_origins_do_not_choose_first_character():
    native, glyphs, page = pair()
    glyphs.append(dict(glyphs[0], char_index=10))
    assert not reconcile_native_font_evidence(native, glyphs, page)["corrections"]


@pytest.mark.parametrize("native_x,glyph_x", [(.049, .051), (-.001, .001), (10, 10.049)])
def test_origin_buckets_include_neighbors_across_positive_and_negative_boundaries(native_x, glyph_x):
    native, glyphs, page = pair()
    page["records"][0]["origin"][0] = native_x
    glyphs[0]["origin"][0] = glyph_x
    assert reconcile_native_font_evidence(native, glyphs, page)["glyph_comparison_text"] == {9: "∑"}


def test_origin_bucket_lookup_keeps_exact_tolerance_and_ambiguity_guard():
    native, glyphs, page = pair()
    page["records"][0]["origin"][0] = .049
    glyphs[0]["origin"][0] = .051
    glyphs.append(dict(glyphs[0], char_index=10, origin=[.048, 22]))
    assert not reconcile_native_font_evidence(native, glyphs, page)["corrections"]
    glyphs[1]["origin"][0] = .101
    assert reconcile_native_font_evidence(native, glyphs, page)["glyph_comparison_text"] == {9: "∑"}


@pytest.mark.parametrize("field", ["text", "bbox"])
def test_changed_native_character_cannot_reuse_stale_font_proof(field):
    native, glyphs, page = pair()
    if field == "text":
        native[0]["text"] = "Q"
    else:
        native[0]["bbox"]["l"] += 1
    assert not reconcile_native_font_evidence(native, glyphs, page)["corrections"]


def test_one_pdfium_character_cannot_corroborate_two_native_characters():
    native, glyphs, page = pair()
    native.append(dict(native[0], index=2))
    page["records"].append(dict(page["records"][0], native_index=2))
    result = reconcile_native_font_evidence(native, glyphs, page)
    assert not result["corrections"]
    assert result["native_comparison_text"] == result["glyph_comparison_text"] == {}
    assert {r["reason"] for r in result["rejections"]} == {"reused_pdfium_character"}


def test_unsupported_page_scope_produces_no_overrides():
    native, glyphs, page = pair()
    page["review_required"] = True
    assert not reconcile_native_font_evidence(native, glyphs, page)["corrections"]


def style_font_resource(weight="Bold", *, program_name="Symbolic"):
    font = font_resource("x", code=120, tounicode="x")
    program = (
        "%!PS-AdobeFont-1.0: Symbolic 1.0\n"
        "10 dict begin\n"
        f"/FontName /{program_name} def\n"
        "/FontType 1 def\n"
        f"/FontInfo 1 dict dup begin /Weight ({weight}) def end def\n"
        "/Encoding 256 array\n0 1 255 {1 index exch /.notdef put} for\n"
        "dup 120 /x put\nreadonly def\n"
        "currentdict end\n"
        f"/{program_name} exch definefont pop\n"
    )
    font["/FontDescriptor"]["/FontFile"].set_data(program.encode("ascii"))
    return font


@pytest.mark.parametrize("weight,supports", [("Bold", True), ("Regular", False), ("Medium", False),
                                           ("UnspecifiedStyle", None)])
def test_source_weight_is_parsed_from_type1_fontinfo_by_fonttools(weight, supports):
    maps = _font_maps(style_font_resource(weight))
    assert maps["source_weight"]["declaration"] == weight
    assert maps["source_weight"]["supports_bold"] is supports
    assert maps["source_weight"]["source"] == "fontTools.misc.psLib.suckfont:FontInfo.Weight"
    proof = decode_source_font_character(maps, 120, "x")
    assert not proof["review_required"]
    assert proof["source_weight"] == maps["source_weight"]


def test_program_weight_cannot_be_borrowed_from_another_font_name():
    assert _font_maps(style_font_resource(program_name="Other"))["source_weight"] is None


def styled_pair():
    record = source_record("x", canonical_name="x", code=120)
    record["font_decoding"] = decode_source_font_character(_font_maps(style_font_resource()), 120, "x")
    return pair("x", "x", record=record)


def test_equal_character_layers_still_bind_source_style_without_text_replacement():
    inputs = styled_pair()
    before = deepcopy(inputs)
    result = reconcile_native_font_evidence(*inputs)
    assert result["corrections"] == []
    assert result["glyph_comparison_text"] == result["native_comparison_text"] == {}
    proof = result["glyph_style_proofs"][9]
    assert proof["native_index"] == 1 and proof["pdfium_char_index"] == 9
    assert proof["font_program_sha256"] and proof["source_sha256"] == "same-source"
    assert proof["source_weight"]["supports_bold"]
    assert inputs == before


def test_style_binding_retains_nonunique_origin_and_reused_glyph_rejections():
    native, glyphs, page = styled_pair()
    glyphs.append(dict(glyphs[0], char_index=10))
    assert reconcile_native_font_evidence(native, glyphs, page)["glyph_style_proofs"] == {}
    native, glyphs, page = styled_pair()
    native.append(dict(native[0], index=2))
    page["records"].append(dict(page["records"][0], native_index=2))
    result = reconcile_native_font_evidence(native, glyphs, page)
    assert result["glyph_style_proofs"] == {}
    assert {entry["reason"] for entry in result["rejections"]} == {"reused_pdfium_character"}
