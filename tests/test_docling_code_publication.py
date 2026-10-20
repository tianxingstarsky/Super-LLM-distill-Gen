"""Exercise code correction through the real context, classifier and publisher.

Only the local detector is replaced with a tiny test double. No models, worker
processes, application fixtures or HTTP endpoints are started by these tests.
"""
from copy import deepcopy
import hashlib
import io
from types import SimpleNamespace

from PIL import Image
import pytest

from docling_core.types.doc import (
    BoundingBox, CodeItem, CoordOrigin, DocItemLabel, DoclingDocument,
    ProvenanceItem, RefItem, TextItem,
)
from docling_core.types.doc.labels import CodeLanguageLabel

from lib.infrastructure import docling_code_classification as classification
from lib.infrastructure import docling_inline_formulas as inline


def provenance(text, page=2, *, top=40, bottom=0):
    return ProvenanceItem(
        page_no=page, charspan=(0, len(text)),
        bbox=BoundingBox(l=0, b=bottom, r=400, t=top,
                         coord_origin=CoordOrigin.BOTTOMLEFT))


def make_document():
    document = DoclingDocument(name="code label publication")
    heading = document.add_heading(text="Mathematical definitions",
                                   prov=provenance("Mathematical definitions", top=90, bottom=70))
    text = "Given X ∈ ℝ, the result remains well defined."
    item = document.add_code(text=text, orig=text, prov=provenance(text))
    return document, item, heading


def route_for(item):
    return {"original_label": "code", "code_language": item.code_language.value,
            "probe_required": True, "source_ref": item.self_ref, "page_no": item.prov[0].page_no,
            "source_sha256": "native-source",
            "bbox": item.prov[0].bbox.model_dump(mode="json")}


def glyphs_for(text):
    return [{"char_index": index, "text": char,
             "font_name": "CambriaMath" if char in "X∈ℝ" else "Times-Roman",
             "font_flags": 32, "font_size": 12, "origin": [10 + index * 6, 20],
             "bbox": {"l": 10 + index * 6, "b": 20, "r": 15 + index * 6, "t": 32}}
            for index, char in enumerate(text)]


def image_sha(image):
    data = io.BytesIO()
    image.save(data, format="PNG")
    return hashlib.sha256(data.getvalue()).hexdigest()


@pytest.fixture
def classified_item(tmp_path, monkeypatch):
    document, item, heading = make_document()
    route = route_for(item)
    image = Image.new("RGB", (800, 200), "white")
    prediction = {
        "policy": classification.CODE_CLASSIFICATION_POLICY, "status": "layout_predicted",
        "model": "docling-project/docling-layout-egret-medium",
        "model_revision": classification.CODE_STRUCTURE_REVISION,
        "model_sha256": "1" * 64, "source_image_sha256": image_sha(image),
        "image_size": list(image.size), "frame": "full_pdf_page",
        "parameters": dict(classification.CODE_LAYOUT_PARAMETERS),
        "local_files_only": True, "device": "cpu",
        "boxes": [{"label": "text", "score": .9, "coordinate": [0, 120, 800, 200]}],
    }
    asset_root = tmp_path / classification.CODE_STRUCTURE_DIRECTORY
    asset_root.mkdir()
    for name in classification.CODE_STRUCTURE_FILES:
        (asset_root / name).write_text("test fixture only", encoding="utf-8")
    state = SimpleNamespace(
        document=document, item=item, heading=heading, route=route, image=image,
        glyphs=glyphs_for(item.text),
        page=dict(page_no=2, width=400, height=100, source_sha256="native-source"),
        config={"artifacts_path": str(tmp_path)}, routes={item.self_ref: route},
        cache={}, prediction=prediction, load_calls=[], read_calls=[],
        boxes=[{"label": "text", "score": .9, "bbox": dict(route["bbox"])}],
        asset_root=asset_root,
    )

    def reader_factory(path):
        state.load_calls.append(path)

        def read_page(pixels):
            state.read_calls.append(pixels.size)
            return deepcopy(state.prediction)

        return SimpleNamespace(read_page=read_page)

    monkeypatch.setattr(classification, "LocalCodeLayoutReader", reader_factory)

    def run():
        return inline._classify_code_item(
            state.document, state.item, state.route, state.glyphs, state.page,
            state.image, state.boxes, state.config, state.routes, state.cache)

    state.run = run
    yield state
    image.close()


def test_original_heading_is_visible_in_code_context():
    document, item, heading = make_document()
    context = inline._code_source_context(document, item, {})
    assert context["available"] and context["reasons"] == []
    assert heading.orig in context["text"]
    assert {entry["source_ref"] for entry in context["records"]} == {heading.self_ref}


def test_original_parent_code_is_a_veto():
    document = DoclingDocument(name="nested source code")
    parent = document.add_code(text="# beginning of comment", prov=provenance("# beginning of comment"))
    item = document.add_code(text="The mathematical prose continues here.", parent=parent,
                             prov=provenance("The mathematical prose continues here."))
    context = inline._code_source_context(document, item, {})
    assert context["available"]
    assert "original_code_parent" in context["reasons"]
    assert parent.orig in context["text"]


def test_parent_previously_relabelled_as_text_still_uses_original_code_label():
    document = DoclingDocument(name="original parent source")
    parent = document.add_text(label=DocItemLabel.TEXT, text="The block begins here.",
                               prov=provenance("The block begins here."))
    item = document.add_code(text="The mathematical prose continues here.", parent=parent,
                             prov=provenance("The mathematical prose continues here."))
    context = inline._code_source_context(document, item, {parent.self_ref: {"original_label": "code"}})
    assert "original_code_parent" in context["reasons"]


def test_adjacent_previously_relabelled_code_still_protects_comment_continuation():
    document, item, _ = make_document()
    adjacent = document.add_text(label=DocItemLabel.TEXT, text="# source comment resumes",
                                 prov=provenance("# source comment resumes"))
    context = inline._code_source_context(document, item, {adjacent.self_ref: {"original_label": "code"}})
    assert "adjacent_original_code_block" in context["reasons"]
    assert adjacent.orig in context["text"]


def test_code_on_a_different_page_is_not_the_current_block_context():
    document, item, _ = make_document()
    document.add_code(text="# separate page", prov=provenance("# separate page", page=3))
    context = inline._code_source_context(document, item, {})
    assert context["available"] and context["reasons"] == []


@pytest.mark.parametrize("damage", ["absent_from_order", "missing_parent", "cyclic_parent", "multiple_provenance"])
def test_unrecoverable_original_context_remains_unavailable(damage):
    document, item, _ = make_document()
    if damage == "absent_from_order":
        document.body.children = []
    elif damage == "missing_parent":
        item.parent = RefItem(cref="#/groups/999")
    elif damage == "cyclic_parent":
        document.body.parent = document.body.get_ref()
    else:
        item.prov.append(provenance(item.text, page=3))
    assert not inline._code_source_context(document, item, {})["available"]


def test_missing_optional_assets_holds_without_loading_reader(classified_item):
    state = classified_item
    (state.asset_root / classification.CODE_STRUCTURE_FILES[0]).unlink()
    proof = state.run()
    assert not proof["approved"]
    assert proof["reasons"] == ["independent_code_layout_assets_unavailable"]
    assert state.load_calls == state.read_calls == []


def test_declared_code_is_protected_before_optional_model_loading(classified_item):
    state = classified_item
    state.item.code_language = CodeLanguageLabel.PYTHON
    state.route["code_language"] = "Python"
    proof = state.run()
    assert not proof["approved"] and "declared_code_language" in proof["reasons"]
    assert state.load_calls == state.read_calls == []


def test_missing_original_context_holds_before_model_loading(classified_item):
    state = classified_item
    state.document.body.children = []
    proof = state.run()
    assert not proof["approved"]
    assert not proof["source_context"]["available"]
    assert state.load_calls == state.read_calls == []


def test_weak_or_missing_pp_support_does_not_load_an_extra_model(classified_item):
    state = classified_item
    state.boxes[0]["score"] = .49
    proof = state.run()
    assert not proof["approved"]
    assert state.load_calls == state.read_calls == []


def test_two_positive_detectors_produce_a_bound_proof_without_changing_code(classified_item):
    state = classified_item
    original = state.document.model_dump(mode="python")
    proof = state.run()
    assert proof["approved"] and proof["reasons"] == []
    assert proof["source_ref"] == state.item.self_ref
    assert proof["source_page"]["source_sha256"] == state.route["source_sha256"]
    assert proof["source_page"]["image_sha256"] == image_sha(state.image)
    assert proof["source_context"]["available"]
    assert proof["pp_missing_source_char_indices"] == proof["egret_missing_source_char_indices"] == []
    assert not proof["transcription_used_for_publication"]
    assert state.document.model_dump(mode="python") == original
    assert len(state.load_calls) == len(state.read_calls) == 1


def test_prediction_cannot_assert_its_own_unrelated_page_hash(classified_item):
    state = classified_item
    state.prediction["source_image_sha256"] = "wrong-page-image"
    proof = state.run()
    assert not proof["approved"]
    assert proof["source_page"]["image_sha256"] == image_sha(state.image)


def test_local_reader_and_same_page_prediction_are_reused(classified_item):
    state = classified_item
    assert state.run()["approved"]
    assert state.run()["approved"]
    assert len(state.load_calls) == len(state.read_calls) == 1


def test_two_distinct_items_on_one_page_share_one_prediction(classified_item):
    state = classified_item
    state.document.add_text(label=DocItemLabel.TEXT, text="An explanatory paragraph.",
                            prov=provenance("An explanatory paragraph.", top=45, bottom=40))
    state.document.add_heading(text="Additional mathematical definitions",
                               prov=provenance("Additional mathematical definitions", top=70, bottom=65))
    second = state.document.add_code(text=state.item.text,
                                     prov=provenance(state.item.text, top=65, bottom=45))
    state.prediction["boxes"].append(
        {"label": "text", "score": .9, "coordinate": [0, 70, 800, 110]})
    state.boxes.append({"label": "text", "score": .9,
                       "bbox": second.prov[0].bbox.model_dump(mode="json")})
    assert state.run()["approved"]
    state.item = second
    state.route = route_for(second)
    state.routes[second.self_ref] = state.route
    for glyph in state.glyphs:
        glyph["char_index"] += 100
        glyph["origin"][1] += 30
        glyph["bbox"]["b"] += 30
        glyph["bbox"]["t"] += 30
    proof = state.run()
    assert proof["approved"] and proof["source_ref"] == second.self_ref
    assert len(state.load_calls) == len(state.read_calls) == 1


def test_cached_prediction_is_not_rebound_to_changed_page_pixels(classified_item):
    state = classified_item
    assert state.run()["approved"]
    state.image.putpixel((0, 0), (0, 0, 0))
    proof = state.run()
    assert not proof["approved"]
    assert proof["source_page"]["image_sha256"] == image_sha(state.image)
    assert len(state.load_calls) == 1 and len(state.read_calls) == 2


def test_next_page_reuses_reader_but_gets_a_new_page_prediction(classified_item):
    state = classified_item
    assert state.run()["approved"]
    state.item.prov[0].page_no = 3
    state.route["page_no"] = state.page["page_no"] = 3
    assert state.run()["approved"]
    assert len(state.load_calls) == 1 and len(state.read_calls) == 2


def publish_inputs(state):
    route = dict(state.route, code_reclassification=state.run())
    assembly = {"status": "transcribed_unverified", "review_reasons": [],
                "backend_text": state.item.text, "prose_preserved": True,
                "text": state.item.text.replace("X ∈ ℝ", r"$X \in \mathbb{R}$")}
    return route, assembly


def test_publication_preserves_original_identity_and_source_geometry(classified_item):
    state = classified_item
    route, assembly = publish_inputs(state)
    original = state.item.model_dump(mode="python")
    assert inline._publish_inline_item(state.document, state.item, assembly, route)
    published = next(item for item in state.document.texts if item.self_ref == state.item.self_ref)
    assert type(published) is TextItem and type(state.item) is CodeItem
    assert published.self_ref == state.item.self_ref and published.parent == state.item.parent
    assert published.orig == state.item.orig and published.text == assembly["text"]
    assert published.prov[0].bbox == state.item.prov[0].bbox
    assert published.prov[0].page_no == state.item.prov[0].page_no
    assert published.prov[0].charspan == (0, len(assembly["text"]))
    assert state.item.model_dump(mode="python") == original


@pytest.mark.parametrize("damage", [
    "missing_proof", "unapproved", "wrong_policy", "wrong_ref", "wrong_source",
    "wrong_page", "native_protected", "missing_egret_glyph", "missing_pp_glyph",
    "model_text_used", "classification_reasons", "unavailable_context", "context_reasons",
    "declared_language", "changed_backend", "prose_not_preserved", "assembly_review",
])
def test_unsafe_or_unbound_publication_holds_atomically(classified_item, damage):
    state = classified_item
    route, assembly = publish_inputs(state)
    proof = route["code_reclassification"]
    if damage == "missing_proof":
        route.pop("code_reclassification")
    elif damage == "unapproved":
        proof["approved"] = False
    elif damage == "wrong_policy":
        proof["policy"] = "unverified"
    elif damage == "wrong_ref":
        proof["source_ref"] = "#/texts/999"
    elif damage == "wrong_source":
        proof["source_page"]["source_sha256"] = "other-source"
    elif damage == "wrong_page":
        proof["source_page"]["page_no"] = 3
    elif damage == "native_protected":
        proof["native_protection"]["protected"] = True
    elif damage == "missing_egret_glyph":
        proof["egret_missing_source_char_indices"] = [0]
    elif damage == "missing_pp_glyph":
        proof["pp_missing_source_char_indices"] = [0]
    elif damage == "model_text_used":
        proof["transcription_used_for_publication"] = True
    elif damage == "classification_reasons":
        proof["reasons"] = ["unresolved_source_context"]
    elif damage == "unavailable_context":
        proof["source_context"]["available"] = False
    elif damage == "context_reasons":
        proof["source_context"]["reasons"] = ["original_code_parent"]
    elif damage == "declared_language":
        state.item.code_language = CodeLanguageLabel.PYTHON
        route["code_language"] = "Python"
    elif damage == "changed_backend":
        assembly["backend_text"] = "An unrelated source."
    elif damage == "prose_not_preserved":
        assembly["prose_preserved"] = False
    else:
        assembly.update(status="review_required", review_reasons=["ambiguous_alignment"])
    before = state.document.model_dump(mode="python")
    assert not inline._publish_inline_item(state.document, state.item, assembly, route)
    assert state.document.model_dump(mode="python") == before


@pytest.mark.parametrize("owned_field", ["captions", "references", "footnotes", "children"])
def test_owned_code_references_are_not_relabelled_or_dropped(classified_item, owned_field):
    state = classified_item
    route, assembly = publish_inputs(state)
    setattr(state.item, owned_field, [state.heading.get_ref()])
    before = state.document.model_dump(mode="python")
    assert not inline._publish_inline_item(state.document, state.item, assembly, route)
    assert state.document.model_dump(mode="python") == before
