"""Math candidate evidence preserves PDF geometry without recognizing content."""
import pytest

from lib.infrastructure.docling_math_regions import (
    candidate_inline_math_regions, extract_pdf_page_glyphs, inspect_math_region, is_math_font,
)


def glyph(index, text, x, y=20, size=10, font="Helvetica"):
    return {"char_index": index, "text": text, "font_name": font, "font_size": size,
            "origin": [x, y], "bbox": {"l": x, "b": y, "r": x+4, "t": y+size}, "generated": False}


def page(*glyphs):
    return {"page_no": 1, "width": 200, "height": 100, "glyphs": list(glyphs), "source_sha256": "source"}


@pytest.mark.parametrize("font", ["/ABCDEF+CMMI10", "CMSY7", "CMEX9", "MSBM10", "CambriaMath", "STIXTwoMath"])
def test_math_family_including_subset_prefix(font):
    assert is_math_font(font)


@pytest.mark.parametrize("font", ["Helvetica", "CMR10", "STIXTwoText", "NimbusRomNo9L-ReguItal"])
def test_ordinary_roman_and_italic_text_is_not_a_math_font(font):
    assert not is_math_font(font)


def test_numeric_cell_does_not_trigger_recognition_just_because_it_has_digits():
    data = page(glyph(0,"4",10),glyph(1,".",14),glyph(2,"9",18))
    result = inspect_math_region(data, {"l": 5,"b": 10,"r": 25,"t": 40})
    assert not result["contains_math"]
    assert result["status"] == "no_math_evidence"


def test_neighboring_math_cell_does_not_contaminate_plain_cell():
    data = page(glyph(0,"3",10),glyph(1,"x",34,font="CMMI10"))
    plain = inspect_math_region(data, {"l":5,"b":10,"r":25,"t":40})
    math = inspect_math_region(data, {"l":30,"b":10,"r":45,"t":40})
    assert not plain["contains_math"]
    assert math["contains_math"] and math["char_indices"] == [1]
    assert math["recognized_latex"] is None


@pytest.mark.parametrize("text", ["Encoder+Decoder", "GNMT+RL", "MoE+RL"])
def test_weak_symbols_in_model_names_are_benign_without_quarantine(text):
    data=page(*(glyph(i,c,10+i*4) for i,c in enumerate(text)))
    evidence=inspect_math_region(data,{'l':0,'b':0,'r':150,'t':50})
    assert evidence['has_math_signal']
    assert not evidence['contains_math']
    assert evidence['status']=='weak_symbol_in_text'
    assert not evidence["review_required"]
    assert evidence["reason"] is None and evidence["review_flags"] == []
    retained=candidate_inline_math_regions(data,[{'bbox':{'l':0,'b':0,'r':150,'t':50}}])
    assert retained == []


@pytest.mark.parametrize("prose", ["Accuracy for ", "精度为", "Среднее "])
def test_math_font_in_ordinary_prose_requires_boundaries_before_recognition(prose):
    glyphs = [glyph(i, char, 10+i*4) for i, char in enumerate(prose)]
    glyphs.append(glyph(len(prose), "x", 10+len(prose)*4, font="CMMI10"))
    evidence = inspect_math_region(page(*glyphs), {"l": 0, "b": 0, "r": 150, "t": 50})
    assert evidence["has_math_signal"] and not evidence["contains_math"]
    assert evidence["status"] == "mixed_prose_math"
    assert evidence["review_required"]
    assert evidence["reason"] == "mixed_prose_math_requires_boundary_review"
    assert evidence["ordinary_prose_runs"]
    assert evidence["char_indices"][-1] == len(prose)
    retained = candidate_inline_math_regions(page(*glyphs), [
        {"bbox": {"l": 0, "b": 0, "r": 150, "t": 50}, "self_ref": "cell:1"}])
    assert retained[0]["review_required"]
    assert not retained[0]["automatic_text_replacement_allowed"]


def test_generic_font_superscript_in_prose_does_not_authorize_whole_cell_ocr():
    prose = "Accuracy x"
    glyphs = [glyph(i, char, 10+i*4) for i, char in enumerate(prose)]
    glyphs.append(glyph(len(prose), "2", 10+len(prose)*4, y=24, size=7))
    evidence = inspect_math_region(page(*glyphs), {"l": 0, "b": 0, "r": 150, "t": 50})
    assert "numeric_script_geometry" in evidence["reasons"]
    assert not evidence["contains_math"] and evidence["review_required"]
    assert evidence["status"] == "mixed_prose_math"


@pytest.mark.parametrize("label", ["model", "hidden", "input"])
def test_roman_subscript_word_is_not_treated_as_surrounding_prose(label):
    glyphs = [glyph(0, "d", 10, font="CMMI10")]
    glyphs.extend(glyph(i+1, char, 14+i*4, y=16, size=7, font="CMR7")
                  for i, char in enumerate(label))
    evidence = inspect_math_region(page(*glyphs), {"l": 0, "b": 0, "r": 150, "t": 50})
    assert evidence["contains_math"] and not evidence["review_required"]
    assert evidence["ordinary_prose_runs"] == []
    assert evidence["source_text_in_character_order"] == "d"+label


def test_missing_baseline_metadata_does_not_allow_mixed_prose_to_be_rewritten():
    glyphs = [glyph(i, char, 10+i*4) for i, char in enumerate("Accuracy ")]
    for char in glyphs:
        char["origin"] = None
    glyphs.append(glyph(len(glyphs), "x", 50, font="CMMI10"))
    evidence = inspect_math_region(page(*glyphs), {"l": 0, "b": 0, "r": 150, "t": 50})
    assert not evidence["contains_math"] and evidence["review_required"]


def test_generic_font_superscript_is_a_candidate_without_guessing_expression():
    data = page(glyph(0,"x",10),glyph(1,"2",14,y=24,size=7))
    result = inspect_math_region(data, {"l":5,"b":10,"r":25,"t":40})
    assert result["reasons"] == ["numeric_script_geometry"]
    assert result["script_candidates"][0]["relation_candidate"] == "superscript"
    assert result["source_text_in_character_order"] == "x2"
    assert result["recognized_latex"] is None


def test_top_left_coordinates_and_scanned_region_are_explicit():
    data = page(glyph(0,"∈",10))
    result = inspect_math_region(data, {"l":5,"t":60,"r":25,"b":90,"coord_origin":"TOPLEFT"})
    assert result["contains_math"]
    assert inspect_math_region(page(), {"l":5,"b":10,"r":25,"t":40})["status"] == "no_text_layer"


def test_line_candidate_retains_source_and_never_authorizes_paragraph_rewrite():
    data = page(glyph(0,"x",10,font="CMMI10"))
    results = candidate_inline_math_regions(data,[{"bbox":{"l":5,"b":10,"r":25,"t":40},"self_ref":"line:1","kind":"line"}])
    assert results[0]["source_ref"] == "line:1"
    assert results[0]["boundary_status"] == "review_required"
    assert results[0]["automatic_text_replacement_allowed"] is False


@pytest.mark.parametrize("box", [(0,0,0,10),(0,0,10,float('nan'))])
def test_reject_invalid_geometry(box):
    with pytest.raises(ValueError,match="invalid_provenance"):
        inspect_math_region(page(),box)


def test_real_pdf_character_fonts_origins_and_document_ownership(tmp_path):
    pdfium = pytest.importorskip("pypdfium2")
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter();sheet=writer.add_blank_page(width=200,height=100)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    sheet[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    content=DecodedStreamObject();content.set_data(b'BT /F1 12 Tf 10 30 Td (x) Tj /F1 8 Tf 4 Ts (2) Tj ET')
    sheet[NameObject('/Contents')]=writer._add_object(content)
    path=tmp_path/'glyphs.pdf';writer.write(path)
    pdf=pdfium.PdfDocument(path)
    try:
        data=extract_pdf_page_glyphs(pdf,1)
        assert len(pdf)==1
        visible=[g for g in data['glyphs'] if g['text'].strip()]
        assert ''.join(g['text'] for g in visible)=='x2'
        assert visible[0]['font_name']=='Helvetica'
        assert visible[1]['origin'][1]>visible[0]['origin'][1]
        evidence=inspect_math_region(data,{'l':0,'b':0,'r':200,'t':100})
        assert 'numeric_script_geometry' in evidence['reasons']
        with pytest.raises(ValueError,match='invalid_provenance'):
            extract_pdf_page_glyphs(pdf,0)
        assert len(pdf)==1
    finally:
        pdf.close()


@pytest.mark.parametrize("geometry", ["rotate_90", "rotate_180", "rotate_270", "crop_origin", "media_origin"])
def test_unsupported_pdf_coordinate_spaces_skip_selection_with_explicit_review(geometry):
    from io import BytesIO
    pdfium = pytest.importorskip("pypdfium2")
    from pypdf import PdfWriter
    from pypdf.generic import RectangleObject
    writer = PdfWriter()
    sheet = writer.add_blank_page(width=200, height=100)
    if geometry.startswith("rotate_"):
        sheet.rotate(int(geometry.removeprefix("rotate_")))
    elif geometry == "crop_origin":
        sheet.cropbox = RectangleObject((10, 20, 190, 90))
    else:
        sheet.mediabox = RectangleObject((10, 20, 210, 120))
    content = BytesIO()
    writer.write(content)
    pdf = pdfium.PdfDocument(content.getvalue())
    try:
        data = extract_pdf_page_glyphs(pdf, 1)
        assert len(pdf) == 1
        assert data["status"] == "unsupported_coordinate_space"
        assert data["review_required"]
        assert data["character_layer_present"] is None
        assert data["glyphs"] == []
        bbox = {"l": 0, "b": 0, "r": data["width"], "t": data["height"]}
        evidence = inspect_math_region(data, bbox)
        assert not evidence["contains_math"] and not evidence["has_math_signal"]
        assert evidence["review_required"]
        assert evidence["reason"] == "unsupported_pdf_coordinate_space"
        assert evidence["coordinate_space"] == data["coordinate_space"]
        assert evidence["char_indices"] == []
        assert evidence["source_text_in_character_order"] is None
        retained = candidate_inline_math_regions(data, [{"bbox": bbox, "self_ref": "cell:1"}])
        assert len(retained) == 1 and retained[0]["review_required"]
        assert not retained[0]["automatic_text_replacement_allowed"]
    finally:
        pdf.close()
