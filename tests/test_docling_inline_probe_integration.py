"""Exercise probe publication with mocked local extraction and recognition only."""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest


@pytest.fixture
def probe_pipeline(monkeypatch, tmp_path):
    from lib.infrastructure import docling_inline_boundaries
    monkeypatch.setattr(docling_inline_boundaries, "read_pdf_citation_links", lambda source: {})
    from lib.infrastructure import docling_native_font_evidence
    monkeypatch.setattr(docling_native_font_evidence, "read_native_font_evidence", lambda *a, **k: {})
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
        page=page, math_index=math_index, formula_box=formula_box,
        read_calls=[], worker_calls=[], closed_pdfs=[],
        prediction={"status": "transcribed_unverified", "transcribed_text": "x"},
        prediction_sequence=None, detector_enabled=True, geometry_review_required=False,
    )

    def open_pdf(source):
        assert source == "mock-native.pdf"
        return SimpleNamespace(close=lambda: state.closed_pdfs.append(source))

    def read_crop(image):
        assert isinstance(image, Image.Image)
        call_index = len(state.read_calls)
        state.read_calls.append(image.size)
        if state.prediction_sequence is not None:
            assert call_index < len(state.prediction_sequence), "unexpected recognition retry"
            return dict(state.prediction_sequence[call_index])
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
                     if state.detector_enabled and image["id"] == "page-1" else [])
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
            review_required=state.geometry_review_required,
            review_reasons=["ambiguous_boundary"] if state.geometry_review_required else [],
            excluded_char_indices=[],
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
    assert len(state.read_calls) == (3 if failure == "changed_symbol" else 1)
    assert len(state.worker_calls) == 1
    assert len(state.closed_pdfs) == 2
    if failure == "changed_native_prose":
        assert proof["assembly"]["review_reasons"] == ["ambiguous_or_changed_prose"]
    elif failure == "changed_symbol":
        assert "formula_variable_or_digit_changed" in proof["fragments"][0]["character_consistency"]["reasons"]
        assert len(proof["fragments"][0]["recognition_attempts"]) == 3
    else:
        assert proof["fragments"][0]["reason"] == "document_docling_formula_truncated"


def _recognized(text):
    return {"status": "transcribed_unverified", "transcribed_text": text}


def test_retry_publishes_raw_success_and_retains_original_wrong_prediction(probe_pipeline):
    from docling_core.types.doc import TextItem
    from lib.infrastructure.docling_inline_markdown import export_inline_markdown

    state = probe_pipeline
    state.prediction_sequence = [_recognized("y"), _recognized(r"\mathit{x}")]
    original_item = state.item.model_dump(mode="python")
    original_predictions = deepcopy(state.prediction_sequence)
    proof = state.run()[state.item.self_ref]
    fragment = proof["fragments"][0]
    attempts = fragment["recognition_attempts"]
    published = state.document.texts[0]
    expected = r"Given $\mathit{x}$, use  this value."

    assert type(published) is TextItem
    assert published.text == expected and published.orig == state.source_text
    assert state.item.model_dump(mode="python") == original_item
    assert proof["published"] and proof["assembly"]["prose_preserved"]
    assert fragment["transcribed_text"] == r"\mathit{x}"
    assert not fragment["character_consistency"]["review_required"]
    assert [attempt["prediction"] for attempt in attempts] == original_predictions
    assert attempts[0]["character_consistency"]["reasons"] == ["formula_variable_or_digit_changed"]
    assert not attempts[1]["character_consistency"]["review_required"]
    assert [attempt["crop"]["dpi"] for attempt in attempts] == [480, 240]
    assert all(attempt["crop"]["crop_bbox"] == state.formula_box for attempt in attempts)
    assert fragment["crop"] == attempts[1]["crop"]
    assert attempts[1]["crop"]["white_padding_pixels"] > 0
    assert attempts[1]["crop"]["recognition_pixel_size"] == list(state.read_calls[1])
    assert len(attempts[1]["crop"]["recognition_input_sha256"]) == 64
    assert state.prediction_sequence == original_predictions
    assert export_inline_markdown(state.document, 1, {published.self_ref: proof}) == expected
    assert len(state.read_calls) == 2
    assert len(state.worker_calls) == 1 and len(state.closed_pdfs) == 2


def test_misread_bold_variable_absent_from_source_can_retry_but_must_pass_all_guards(probe_pipeline):
    state = probe_pipeline
    state.prediction_sequence = [_recognized(r"\mathbf{X}"), _recognized(r"\mathrm{x}")]
    original = deepcopy(state.prediction_sequence)
    proof = state.run()[state.item.self_ref]
    fragment = proof["fragments"][0]
    attempts = fragment["recognition_attempts"]
    assert proof["published"] and len(state.read_calls) == 2
    assert fragment["transcribed_text"] == r"\mathrm{x}"
    assert not fragment["character_consistency"]["review_required"]
    assert not fragment["style_consistency"]["review_required"]
    assert attempts[0]["style_consistency"]["reasons"] == ["bold_source_character_not_unique"]
    assert attempts[0]["character_consistency"]["reasons"] == ["formula_variable_or_digit_changed"]
    assert [a["prediction"] for a in attempts] == original
    assert state.prediction_sequence == original


def test_whole_formula_failure_blocks_otherwise_publishable_parts(probe_pipeline, monkeypatch):
    from lib.infrastructure import docling_inline_continuation as continuation
    state = probe_pipeline
    original = state.document.model_dump(mode="python")
    monkeypatch.setattr(
        continuation, "recognize_inline_formula_continuation",
        lambda *args, **kwargs: {
            "status": "review_required", "fragment": None,
            "reasons": ["whole_formula_ast_does_not_match_rows"]})
    proof = state.run()[state.item.self_ref]
    assert proof["assembly"]["status"] == "transcribed_unverified"
    assert proof["fragments"][0]["status"] == "transcribed_unverified"
    assert proof["status"] == "review_required" and not proof["published"]
    assert proof["publication_block_reason"] == "whole_formula_continuation_requires_review"
    assert state.document.model_dump(mode="python") == original


def test_retries_cannot_replace_original_variable_with_another_variable(probe_pipeline):
    state = probe_pipeline
    state.prediction_sequence = [_recognized(text) for text in ("y", "z", r"\upsilon")]
    original_document = state.document.model_dump(mode="python")
    original_predictions = deepcopy(state.prediction_sequence)
    proof = state.run()[state.item.self_ref]
    fragment = proof["fragments"][0]
    attempts = fragment["recognition_attempts"]

    assert state.document.model_dump(mode="python") == original_document
    assert state.document.texts[0] is state.item
    assert not proof["published"] and proof["status"] == "review_required"
    assert fragment["transcribed_text"] == "y"
    assert fragment["reason"] == "formula_character_review"
    assert [attempt["prediction"] for attempt in attempts] == original_predictions
    assert all(attempt["character_consistency"]["reasons"] == ["formula_variable_or_digit_changed"]
               for attempt in attempts)
    assert [attempt["crop"]["dpi"] for attempt in attempts] == [480, 240, 360]
    assert state.prediction_sequence == original_predictions
    assert len(state.read_calls) == 3
    assert len(state.worker_calls) == 1 and len(state.closed_pdfs) == 2


def test_retry_with_added_period_is_rejected_without_trimming_ocr_output(probe_pipeline):
    state = probe_pipeline
    state.prediction_sequence = [_recognized(text) for text in ("y", "x.", r"\mathit{x}.")]
    original_document = state.document.model_dump(mode="python")
    original_predictions = deepcopy(state.prediction_sequence)
    proof = state.run()[state.item.self_ref]
    attempts = proof["fragments"][0]["recognition_attempts"]

    assert state.document.model_dump(mode="python") == original_document
    assert state.document.texts[0] is state.item
    assert not proof["published"] and proof["status"] == "review_required"
    assert [attempt["prediction"] for attempt in attempts] == original_predictions
    assert attempts[0]["character_consistency"]["reasons"] == ["formula_variable_or_digit_changed"]
    assert all(attempt["character_consistency"]["review_required"] for attempt in attempts[1:])
    assert all(attempt["character_consistency"]["reasons"] == ["source_punctuation_changed"]
               for attempt in attempts[1:])
    assert proof["fragments"][0]["transcribed_text"] == "y"
    assert state.prediction_sequence == original_predictions
    assert len(state.read_calls) == 3
    assert len(state.worker_calls) == 1 and len(state.closed_pdfs) == 2


def test_initial_prediction_with_added_period_is_preserved_for_review_without_retry(probe_pipeline):
    state = probe_pipeline
    state.prediction_sequence = [_recognized("x."), _recognized("x")]
    original_document = state.document.model_dump(mode="python")
    proof = state.run()[state.item.self_ref]
    fragment = proof["fragments"][0]

    assert state.document.model_dump(mode="python") == original_document
    assert state.document.texts[0] is state.item
    assert proof["status"] == "review_required" and not proof["published"]
    assert fragment["transcribed_text"] == "x."
    assert fragment["character_consistency"]["reasons"] == ["source_punctuation_changed"]
    assert fragment["recognition_attempts"] == []
    assert len(state.read_calls) == 1
    assert len(state.worker_calls) == 1 and len(state.closed_pdfs) == 2


def test_truncated_prediction_is_not_retried_even_with_a_variable_mismatch(probe_pipeline):
    state = probe_pipeline
    state.prediction_sequence = [
        {"status": "review_required", "transcribed_text": "y",
         "reason": "document_docling_formula_truncated"},
        _recognized("x"),
    ]
    original_document = state.document.model_dump(mode="python")
    proof = state.run()[state.item.self_ref]
    fragment = proof["fragments"][0]

    assert state.document.model_dump(mode="python") == original_document
    assert state.document.texts[0] is state.item
    assert proof["status"] == "review_required" and not proof["published"]
    assert fragment["transcribed_text"] == "y"
    assert fragment["character_consistency"]["reasons"] == ["formula_variable_or_digit_changed"]
    assert fragment["recognition_attempts"] == []
    assert len(state.read_calls) == 1
    assert len(state.worker_calls) == 1 and len(state.closed_pdfs) == 2


@pytest.mark.parametrize("detector_enabled", [False, True])
def test_native_preserved_scalar_keeps_exact_prose_without_formula_ocr(probe_pipeline, detector_enabled):
    from docling_core.types.doc import TextItem
    from lib.infrastructure.docling_inline_markdown import export_inline_markdown

    state = probe_pipeline
    data = state.item.model_dump(mode="python")
    data.update(label="text", text=state.source_text)
    state.item = TextItem.model_validate(data)
    state.document.texts[0] = state.item
    state.detector_enabled = detector_enabled
    state.geometry_review_required = True
    state.prediction_sequence = []
    original_document = state.document.model_dump(mode="python")
    original_glyphs = deepcopy(state.page["glyphs"])
    original_native = deepcopy(state.native)
    proof = state.run()[state.item.self_ref]

    assert state.document.model_dump(mode="python") == original_document
    assert state.document.texts[0] is state.item
    assert proof["status"] == proof["assembly"]["status"] == "native_preserved"
    assert not proof["published"]
    assert proof["backend_text"] == proof["original_text_slot"] == state.source_text
    assert proof["assembly"]["prose_preserved"]
    assert proof["fragments"] == [] and proof["uncovered_math_char_indices"] == []
    assert proof["native_preservation"]["proven_glyph_ids"] == [state.math_index, state.math_index + 1]
    assert proof["native_preservation"]["proofs"][-1]["kind"] == "adjacent_prose_punctuation"
    token_proof = proof["native_preservation"]["proofs"][0]
    assert token_proof["source_text"] == token_proof["native_text"] == token_proof["backend_text"] == "x"
    assert token_proof["native_char_ids"] == [state.math_index]
    assert not proof["native_preservation"]["automatic_text_replacement_allowed"]
    assert not proof["native_preservation"]["mathematical_correctness_certified"]
    assert len(proof["preserved_candidates"]) == int(detector_enabled)
    assert state.page["glyphs"] == original_glyphs and state.native == original_native
    assert export_inline_markdown(state.document, 1, {state.item.self_ref: proof}) == state.source_text
    assert state.read_calls == []
    assert len(state.worker_calls) == 1 and len(state.closed_pdfs) == 2


def test_unsupported_bold_retry_uses_new_raw_prediction_and_preserves_wrong_style_evidence(probe_pipeline):
    from docling_core.types.doc import TextItem
    from lib.infrastructure.docling_inline_markdown import export_inline_markdown

    state = probe_pipeline
    state.page["glyphs"][state.math_index]["font_weight"] = 400
    state.prediction_sequence = [
        dict(_recognized(r"\mathbf{x}"), raw_output=r"<s>\mathbf{x}</s>"),
        dict(_recognized("x"), raw_output="<s>x</s>"),
    ]
    original_item = state.item.model_dump(mode="python")
    original_predictions = deepcopy(state.prediction_sequence)
    proof = state.run()[state.item.self_ref]
    fragment = proof["fragments"][0]
    attempts = fragment["recognition_attempts"]
    published = state.document.texts[0]
    expected = "Given $x$, use  this value."

    assert type(published) is TextItem and published.text == expected
    assert published.orig == state.source_text
    assert state.item.model_dump(mode="python") == original_item
    assert proof["published"] and proof["assembly"]["prose_preserved"]
    assert fragment["original_prediction"] == original_predictions[0]
    assert fragment["raw_output"] == original_predictions[1]["raw_output"]
    assert fragment["transcribed_text"] == original_predictions[1]["transcribed_text"]
    assert [attempt["prediction"] for attempt in attempts] == original_predictions
    assert all(not attempt["character_consistency"]["review_required"] for attempt in attempts)
    assert attempts[0]["style_consistency"]["reasons"] == ["source_font_weight_does_not_support_bold"]
    assert not attempts[1]["style_consistency"]["review_required"]
    assert fragment["style_consistency"] == attempts[1]["style_consistency"]
    assert [attempt["crop"]["dpi"] for attempt in attempts] == [480, 240]
    assert all(attempt["crop"]["crop_bbox"] == state.formula_box for attempt in attempts)
    assert state.prediction_sequence == original_predictions
    assert export_inline_markdown(state.document, 1, {published.self_ref: proof}) == expected
    assert len(state.read_calls) == 2
    assert len(state.worker_calls) == 1 and len(state.closed_pdfs) == 2


def test_unknown_source_style_preserves_paragraph_without_retrying_to_remove_bold(probe_pipeline):
    state = probe_pipeline
    state.page["glyphs"][state.math_index]["font_weight"] = None
    state.prediction_sequence = [
        dict(_recognized(r"\mathbf{x}"), raw_output=r"<s>\mathbf{x}</s>"),
        _recognized("x"),
    ]
    original_document = state.document.model_dump(mode="python")
    original_prediction = deepcopy(state.prediction_sequence[0])
    proof = state.run()[state.item.self_ref]
    fragment = proof["fragments"][0]

    assert state.document.model_dump(mode="python") == original_document
    assert state.document.texts[0] is state.item
    assert proof["status"] == "review_required" and not proof["published"]
    assert fragment["original_prediction"] == original_prediction
    assert fragment["raw_output"] == original_prediction["raw_output"]
    assert fragment["transcribed_text"] == original_prediction["transcribed_text"]
    assert fragment["reason"] == "formula_style_review"
    assert not fragment["character_consistency"]["review_required"]
    assert fragment["style_consistency"]["reasons"] == ["source_font_weight_unknown"]
    assert fragment["recognition_attempts"] == []
    assert len(state.read_calls) == 1
    assert len(state.worker_calls) == 1 and len(state.closed_pdfs) == 2


@pytest.mark.parametrize("remaining_failure", ["bold_style", "variable_identity"])
def test_bold_retry_cannot_publish_when_style_or_character_check_still_fails(probe_pipeline, remaining_failure):
    state = probe_pipeline
    state.page["glyphs"][state.math_index]["font_weight"] = 400
    alternatives = ((r"\boldsymbol{x}", r"\textbf{x}") if remaining_failure == "bold_style"
                    else ("y", r"\mathit{y}"))
    state.prediction_sequence = [
        dict(_recognized(text), raw_output=f"<s>{text}</s>")
        for text in (r"\mathbf{x}", *alternatives)
    ]
    original_document = state.document.model_dump(mode="python")
    original_predictions = deepcopy(state.prediction_sequence)
    proof = state.run()[state.item.self_ref]
    fragment = proof["fragments"][0]
    attempts = fragment["recognition_attempts"]

    assert state.document.model_dump(mode="python") == original_document
    assert state.document.texts[0] is state.item
    assert proof["status"] == "review_required" and not proof["published"]
    assert fragment["original_prediction"] == original_predictions[0]
    assert fragment["raw_output"] == original_predictions[0]["raw_output"]
    assert fragment["transcribed_text"] == r"\mathbf{x}"
    assert fragment["reason"] == "formula_style_review"
    assert [attempt["prediction"] for attempt in attempts] == original_predictions
    assert attempts[0]["style_consistency"]["reasons"] == ["source_font_weight_does_not_support_bold"]
    if remaining_failure == "bold_style":
        assert all(not attempt["character_consistency"]["review_required"] for attempt in attempts)
        assert all(attempt["style_consistency"]["reasons"] == ["source_font_weight_does_not_support_bold"]
                   for attempt in attempts)
    else:
        assert all(attempt["character_consistency"]["reasons"] == ["formula_variable_or_digit_changed"]
                   for attempt in attempts[1:])
        assert all(not attempt["style_consistency"]["review_required"] for attempt in attempts[1:])
    assert [attempt["crop"]["dpi"] for attempt in attempts] == [480, 240, 360]
    assert state.prediction_sequence == original_predictions
    assert len(state.read_calls) == 3
    assert len(state.worker_calls) == 1 and len(state.closed_pdfs) == 2
