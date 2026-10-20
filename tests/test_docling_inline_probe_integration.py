"""Exercise probe publication with mocked local extraction and recognition only."""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest


@pytest.fixture
def probe_pipeline(monkeypatch, tmp_path):
    import pypdfium2
    from docling_core.types.doc import BoundingBox, CoordOrigin, DoclingDocument, ProvenanceItem
    from lib.infrastructure import docling_formula_crops as crops
    from lib.infrastructure import docling_inline_formulas as inline
    from lib.infrastructure import docling_inline_lines as lines
    from lib.infrastructure import docling_inline_routing as routing
    from lib.infrastructure import docling_math_regions as regions
    from lib.infrastructure import docling_worker_client as client

    source_text = "Given x, use  this value."
    document = DoclingDocument(name="native formula probe")
    paragraph = dict(l=0, b=30, r=120, t=60, coord_origin="BOTTOMLEFT")
    item = document.add_formula(
        text="", orig=source_text,
        prov=ProvenanceItem(
            page_no=1, charspan=(0, len(source_text)),
            bbox=BoundingBox(l=0, b=30, r=120, t=60, coord_origin=CoordOrigin.BOTTOMLEFT)),
    )
    native, glyphs = [], []
    for index, char in enumerate(source_text):
        bounds = dict(l=4 + index * 4, b=40, r=7 + index * 4, t=48,
                      coord_origin="BOTTOMLEFT")
        native.append(dict(index=index, text=char, bbox=bounds))
        glyphs.append(dict(
            char_index=index, text=char, bbox=bounds, generated=False,
            font_name="CambriaMath" if char == "x" else "Times-Roman",
            font_flags=32, font_size=10, origin=[bounds["l"], 40],
        ))
    math_index = source_text.index("x")
    formula_box = dict(l=27, b=39, r=32, t=49, coord_origin="BOTTOMLEFT")
    page = dict(page_no=1, width=120, height=100, source_sha256="source-hash",
                glyphs=glyphs, review_required=False, status="ok")
    state = SimpleNamespace(
        document=document, item=item, source_text=source_text, native=native,
        read_calls=[], worker_calls=[], closed_pdfs=[],
        prediction={"status": "transcribed_unverified", "transcribed_text": "x"},
    )

    def open_pdf(source):
        assert source == "mock-native.pdf"
        return SimpleNamespace(close=lambda: state.closed_pdfs.append(source))

    def read_crop(image):
        assert isinstance(image, Image.Image)
        state.read_calls.append(image.size)
        return dict(state.prediction)

    def render_crop(pdf, number, bbox, *, dpi=240, padding_points=2, **kwargs):
        assert number == 1
        width = max(1, round((bbox["r"] - bbox["l"]) * dpi / 72))
        height = max(1, round((bbox["t"] - bbox["b"]) * dpi / 72))
        return Image.new("RGB", (width, height), "white"), {
            "crop_bbox": dict(bbox), "page_no": number, "dpi": dpi,
        }

    def detect(runtime, payload):
        assert runtime == "mock-inline-runtime"
        assert payload["service"] == "inline_layout" and payload["action"] == "detect"
        state.worker_calls.append(payload)
        results = []
        for image in payload["images"]:
            path = Path(image["path"])
            with Image.open(path) as pixels:
                size = list(pixels.size)
            # A single fake model box in the page frame, independent of native
            # character matching. All coordinate inversion is production code.
            boxes = ([dict(label="inline_formula", coordinate=[108, 204, 128, 244], score=.9)]
                     if image["id"] == "page-1" else [])
            results.append(dict(id=image["id"], size=size,
                                image_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                boxes=boxes))
        return dict(protocol=1, network_disabled=True, images=results)

    def geometry(page, detector_bbox, paragraph_bbox, *, source_ref, fragment_id, **kwargs):
        assert detector_bbox == formula_box
        assert paragraph_bbox == paragraph
        return dict(
            source_sha256=page["source_sha256"], page_no=1, source_ref=source_ref,
            fragment_id=fragment_id, char_indices=[math_index], bbox=dict(formula_box),
            paragraph_bbox=dict(paragraph), detector_bbox=dict(detector_bbox),
            review_required=False, review_reasons=[], excluded_char_indices=[],
        )

    monkeypatch.setattr(pypdfium2, "PdfDocument", open_pdf)
    monkeypatch.setattr(crops, "LocalFormulaReader", lambda root: SimpleNamespace(read=read_crop))
    monkeypatch.setattr(crops, "render_formula_crop", render_crop)
    monkeypatch.setattr(regions, "extract_pdf_page_glyphs", lambda *args: deepcopy(page))
    monkeypatch.setattr(routing, "extract_pdf_page_glyphs", lambda *args: deepcopy(page))
    monkeypatch.setattr(inline, "read_native_character_pages", lambda source: {1: state.native})
    monkeypatch.setattr(lines, "read_native_line_pages", lambda source: {})
    monkeypatch.setattr(inline, "render_inline_page_evidence",
                        lambda *args: (deepcopy(page), Image.new("RGB", (480, 400), "white")))
    monkeypatch.setattr(inline, "refine_inline_formula_geometry", geometry)
    monkeypatch.setattr(client, "run_docling_worker", detect)

    def run():
        return crops.enrich_formulas(
            document, "mock-native.pdf", tmp_path,
            inline_config={"inline_runtime_python": "mock-inline-runtime",
                           "artifacts_path": str(tmp_path)},
        )

    state.run = run
    return state


def test_empty_formula_text_uses_orig_and_publishes_exact_prose(probe_pipeline):
    from docling_core.types.doc import FormulaItem, TextItem
    from lib.infrastructure.docling_inline_markdown import export_inline_markdown

    state = probe_pipeline
    original = state.item.model_dump(mode="python")
    assert type(state.item) is FormulaItem
    proof = state.run()[state.item.self_ref]
    published = state.document.texts[0]
    expected = "Given $x$, use  this value."

    assert type(published) is TextItem
    assert published.text == expected
    assert published.orig == state.source_text
    assert published.self_ref == state.item.self_ref
    assert published.prov[0].bbox == state.item.prov[0].bbox
    assert published.prov[0].charspan == (0, len(expected))
    assert state.item.model_dump(mode="python") == original
    assert proof["published"] and proof["status"] == "transcribed_unverified"
    assert proof["original_text_slot"] == ""
    assert proof["backend_text"] == proof["assembly"]["backend_text"] == state.source_text
    assert proof["assembly"]["prose_preserved"]
    assert proof["assembly"]["patches"][0]["backend_text"] == "x"
    assert export_inline_markdown(state.document, 1, {published.self_ref: proof}) == expected
    assert len(state.read_calls) == len(state.worker_calls) == 1
    assert len(state.closed_pdfs) == 2


@pytest.mark.parametrize("failure", ["changed_symbol", "incomplete_recognition", "changed_native_prose"])
def test_failed_probe_retains_empty_formula_and_original_model_atomically(probe_pipeline, failure):
    from docling_core.types.doc import FormulaItem

    state = probe_pipeline
    if failure == "changed_symbol":
        state.prediction["transcribed_text"] = "y"
    elif failure == "incomplete_recognition":
        state.prediction.update(status="review_required", reason="document_docling_formula_truncated")
    else:
        state.native[0] = dict(state.native[0], text="Z")
    original_document = state.document.model_dump(mode="python")
    proof = state.run()[state.item.self_ref]

    assert state.document.model_dump(mode="python") == original_document
    assert state.document.texts[0] is state.item
    assert type(state.item) is FormulaItem
    assert state.item.text == "" and state.item.orig == state.source_text
    assert proof["status"] == "review_required" and not proof["published"]
    assert proof["original_text_slot"] == ""
    assert proof["backend_text"] == state.source_text
    assert len(state.read_calls) == len(state.worker_calls) == 1
    assert len(state.closed_pdfs) == 2
    if failure == "changed_native_prose":
        assert proof["assembly"]["review_reasons"] == ["ambiguous_or_changed_prose"]
    elif failure == "changed_symbol":
        assert "formula_variable_or_digit_changed" in proof["fragments"][0]["character_consistency"]["reasons"]
    else:
        assert proof["fragments"][0]["reason"] == "document_docling_formula_truncated"
