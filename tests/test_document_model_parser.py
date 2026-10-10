"""Model-assisted document reading uses real source pages and durable calls."""
from copy import deepcopy
import json
from pathlib import Path

from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, DecodedStreamObject, NameObject
from pypdf.errors import PdfReadError
import pytest
import yaml

from lib.domain.document_parser import validate_document_parser
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.document_vision import visual_parts


PAGE_ONE = "Inspect the power cable before starting the equipment. Keep the machine dry."
PAGE_TWO = "Disconnect the power before maintenance. Record the result after every inspection."


def model_parser():
    return {"mode": "model", "binding": {"backend": "reader", "model": "text-reader",
            "context_window_tokens": 131072, "max_output_tokens": 32768}}


def configure(root):
    folder = root / "configs"
    folder.mkdir(exist_ok=True)
    preferences = Path(__file__).resolve().parents[1] / "configs/preferences.yaml"
    (folder / "preferences.yaml").write_bytes(preferences.read_bytes())
    endpoint = {"base_url": "https://offline.invalid/v1", "models": ["text-reader"],
                "api_format": "chat", "api_key_env": "MODEL_PARSER_OFFLINE_KEY"}
    (folder / "backends.yaml").write_text(
        yaml.safe_dump({"backends": {"reader": endpoint}}), encoding="utf-8")


def text_pdf(root, pages=(PAGE_ONE, PAGE_TWO), name="manual.pdf"):
    """Build genuine text pages without depending on ReportLab or font files."""
    destination = root / name
    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    font_ref = writer._add_object(font)
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        if text is None:
            continue
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
            DictionaryObject({NameObject("/F1"): font_ref})})
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 11 Tf 36 744 Td ({escaped}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    with destination.open("wb") as handle:
        writer.write(handle)
    return destination


class Reader:
    model = "offline-document-reader"

    def __init__(self, *, response=None):
        self.calls, self.usage = [], {"calls": 0}
        self.response = response

    def chat(self, messages, **options):
        content = messages[1]["content"]
        assert isinstance(content, str), "A text parser must never imply image support."
        data = json.loads(content)
        self.calls.append((deepcopy(messages), deepcopy(options), data))
        self.usage["calls"] += 1
        if self.response is not None:
            return json.dumps(self.response)
        assert isinstance(data.get("text"), str)
        return json.dumps({"text": data["text"], "uncertain": False})


def create(root, source, *, parser=None):
    configure(root)
    output = root / "output"
    run_id = engine.create_run(output, sources=[source], targets=["cpt"], settings_root=root,
                               document_parser=parser if parser is not None else model_parser())
    return output, run_id


def test_model_parser_requires_real_binding_before_run_creation(tmp_path):
    source = text_pdf(tmp_path)
    for value in ({"mode": "model"}, {"mode": "model", "binding": None}):
        with pytest.raises(ValueError):
            engine.create_run(tmp_path / "output", sources=[source], targets=["cpt"],
                              document_parser=value)
    assert not (tmp_path / "output").exists()
    assert validate_document_parser(model_parser())["binding"]["max_output_tokens"] == 32768


def test_model_parser_reads_text_pdf_per_page_and_pins_generation_role(tmp_path):
    output, run_id = create(tmp_path, text_pdf(tmp_path))
    reader = Reader()
    run = engine.Workflow(output, run_id, tmp_path, generator=reader)
    state = run.execute()
    assert state["status"] == "completed"
    assert run.recipe["version"] >= 14
    assert run.recipe["document_parser"]["mode"] == "model"
    assert run.recipe["node_models"]["ingest"]["generation"]["model"] == "text-reader"
    assert "vision" not in run.recipe["node_models"]["ingest"]
    records = engine.read_json(run.path / "artifacts/cpt.records.json")
    assert [row["text"].strip() for row in records] == [PAGE_ONE, PAGE_TWO]
    assert [row["source_location"]["record"] for row in records] == ["page:1", "page:2"]
    assert len(reader.calls) == 2
    assert all(call[1]["max_tokens"] == 32768 for call in reader.calls)
    assert reader.calls[0][2]["text"].strip() == PAGE_ONE
    assert reader.calls[1][2]["text"].strip() == PAGE_TWO
    engine.verify_artifacts(run.path)


def test_page_transcriptions_are_reused_after_worker_restart(tmp_path):
    output, run_id = create(tmp_path, text_pdf(tmp_path))
    reader = Reader()
    initial = engine.Workflow(output, run_id, tmp_path, generator=reader)
    initial.stage = "ingest"
    first = list(initial.iter_source_units(initial.recipe["sources"][0]))
    assert len(reader.calls) == 2
    restored = engine.Workflow(output, run_id, tmp_path, generator=reader)
    restored.stage = "ingest"
    second = list(restored.iter_source_units(restored.recipe["sources"][0]))
    assert first == second
    assert len(reader.calls) == 2


def test_completed_page_call_survives_interruption_before_the_next_page(tmp_path):
    output, run_id = create(tmp_path, text_pdf(tmp_path))
    reader = Reader()
    initial = engine.Workflow(output, run_id, tmp_path, generator=reader)
    initial.stage = "ingest"
    source = initial.iter_source_units(initial.recipe["sources"][0])
    assert next(source)["text"].strip() == PAGE_ONE
    source.close()
    assert len(reader.calls) == 1
    restored = engine.Workflow(output, run_id, tmp_path, generator=reader)
    assert restored.execute()["status"] == "completed"
    assert len(reader.calls) == 2
    assert len(engine.read_json(restored.path / "artifacts/cpt.records.json")) == 2


def test_empty_pdf_page_is_skipped_without_model_or_unknown_failure(tmp_path):
    output, run_id = create(tmp_path, text_pdf(tmp_path, pages=(None, PAGE_ONE)))
    reader = Reader()
    run = engine.Workflow(output, run_id, tmp_path, generator=reader)
    state = run.execute()
    assert state["status"] == "completed"
    records = engine.read_json(run.path / "artifacts/cpt.records.json")
    assert len(records) == 1 and records[0]["text"].strip() == PAGE_ONE
    assert records[0]["source_location"]["record"] == "page:2"
    assert len(reader.calls) == 1


def test_all_blank_pdf_keeps_a_clear_skipped_input_reason(tmp_path):
    output, run_id = create(tmp_path, text_pdf(tmp_path, pages=(None, None)))
    reader = Reader()
    run = engine.Workflow(output, run_id, tmp_path, generator=reader)
    state = run.execute()
    assert state["status"] == "needs_attention"
    assert reader.calls == []
    inputs = engine.read_json(run.path / "input_records.json")
    assert len(inputs) == 2
    assert all(row["status"] == "skipped" and row["reason"] == "empty_document_page" for row in inputs)
    assert engine.verify_artifacts(run.path)["counts"]["cpt"] == 0


def test_image_only_pdf_requires_vision_instead_of_fake_text_recognition(tmp_path):
    source = tmp_path / "scan.pdf"
    Image.new("RGB", (140, 100), "blue").save(source)
    output, run_id = create(tmp_path, source)
    reader = Reader()
    run = engine.Workflow(output, run_id, tmp_path, generator=reader)
    state = run.execute()
    assert state["status"] == "needs_attention"
    assert reader.calls == []
    inputs = engine.read_json(run.path / "input_records.json")
    assert any(row.get("reason") == "document_text_requires_vision" for row in inputs)
    assert engine.verify_artifacts(run.path)["counts"]["cpt"] == 0


def test_corrupt_pdf_never_becomes_an_exported_corpus(tmp_path):
    source = tmp_path / "corrupt.pdf"
    source.write_bytes(b"%PDF-1.7\nnot a real PDF body\n")
    output, run_id = create(tmp_path, source)
    reader = Reader()
    run = engine.Workflow(output, run_id, tmp_path, generator=reader)
    state = run.execute()
    assert state["status"] in {"failed", "needs_attention"}
    assert reader.calls == []
    inputs = engine.read_json(run.path / "input_records.json")
    assert any(row.get("reason") == "document_pdf_parse_failed" for row in inputs)
    if (run.path / "artifacts" / "manifest.json").exists():
        assert engine.verify_artifacts(run.path)["counts"]["cpt"] == 0


def test_default_native_recipe_isolates_corrupt_pdf_and_keeps_other_documents(tmp_path):
    invalid = tmp_path / "broken.pdf"
    invalid.write_bytes(b"%PDF-1.7\nnot a real PDF body\n")
    valid = tmp_path / "valid.txt"
    valid.write_text(PAGE_ONE + " " + PAGE_TWO, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[invalid, valid], targets=["cpt"])
    run = engine.Workflow(output, run_id, tmp_path)
    assert run.recipe["version"] == 11
    assert run.recipe["source_processing_version"] == 2
    assert run.execute()["status"] == "needs_attention"
    assert engine.verify_artifacts(run.path)["counts"]["cpt"] == 1
    inputs = engine.read_json(run.path / "input_records.json")
    assert any(row.get("reason") == "document_pdf_parse_failed" and row["source_name"] == invalid.name
               for row in inputs)
    assert any(row.get("text") == PAGE_ONE + " " + PAGE_TWO for row in inputs)


def test_native_source_policy_preserves_historical_pdf_locations(tmp_path):
    source = text_pdf(tmp_path)
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["cpt"])
    run = engine.Workflow(output, run_id, tmp_path)
    assert run.recipe["version"] == 11
    rows = run.parse_source(run.recipe["sources"][0])
    assert [row["source_location"]["record"] for row in rows] == ["page:1", "page:2"]
    assert [row["text"] for row in rows] == [PAGE_ONE, PAGE_TWO]

    run.recipe.pop("source_processing_version")
    legacy = run.parse_source(run.recipe["sources"][0])
    assert len(legacy) == 1
    assert legacy[0]["source_location"]["record"] == "document"
    assert PAGE_ONE in legacy[0]["text"] and PAGE_TWO in legacy[0]["text"]
    assert legacy[0]["id"] == engine.digest([run.recipe["sources"][0]["sha256"],
                                            run.recipe["sources"][0]["file"], "document:chunk:0"])


@pytest.mark.parametrize("error_type", [PdfReadError, RuntimeError, MemoryError])
@pytest.mark.parametrize("source_processing_version", [1, 2])
def test_pdf_source_errors_preserve_unknown_failures_and_legacy_policy(
        tmp_path, monkeypatch, error_type, source_processing_version):
    import pypdf
    from lib.infrastructure.document_text import text_parts

    class BrokenPage:
        def extract_text(self):
            raise error_type("offline page extraction failure")

    class ReaderFailure:
        is_encrypted = False
        pages = [BrokenPage()]

    monkeypatch.setattr(pypdf, "PdfReader", lambda *_: ReaderFailure())
    if source_processing_version == 1 or error_type is PdfReadError:
        with pytest.raises(ValueError, match="^document_pdf_parse_failed$") as failure:
            list(text_parts(tmp_path / "mock.pdf", source_processing_version=source_processing_version))
        assert type(failure.value.__cause__) is error_type
    else:
        with pytest.raises(error_type, match="offline page extraction failure"):
            list(text_parts(tmp_path / "mock.pdf", source_processing_version=source_processing_version))


@pytest.mark.parametrize("response", [
    {"text": PAGE_ONE, "uncertain": True},
    {"text": PAGE_ONE},
    {"text": 42, "uncertain": False},
    {"text": PAGE_ONE, "uncertain": "false"},
])
def test_unusable_model_parse_is_quarantined(tmp_path, response):
    output, run_id = create(tmp_path, text_pdf(tmp_path, pages=(PAGE_ONE,)))
    run = engine.Workflow(output, run_id, tmp_path, generator=Reader(response=response))
    state = run.execute()
    assert state["status"] == "needs_attention"
    expected = ("document_parse_uncertain" if response.get("uncertain") is True
                else "invalid_document_parse_schema")
    inputs = engine.read_json(run.path / "input_records.json")
    assert any(row.get("reason") == expected for row in inputs)
    assert engine.verify_artifacts(run.path)["counts"]["cpt"] == 0


def test_native_parser_still_uses_real_text_without_model_calls(tmp_path):
    output, run_id = create(tmp_path, text_pdf(tmp_path, pages=(PAGE_ONE,)), parser={"mode": "native"})
    reader = Reader()
    run = engine.Workflow(output, run_id, tmp_path, generator=reader)
    assert run.execute()["status"] == "completed"
    assert reader.calls == []
    assert run.recipe["document_parser"] == {"mode": "native"}


def test_model_text_input_is_split_before_calling_the_reader(tmp_path):
    configure(tmp_path)
    source = tmp_path / "large-manual.md"
    paragraphs = [f"Section {index} uses {12 + index} V. " + PAGE_ONE * 4 for index in range(12)]
    source.write_text("\n\n".join(paragraphs), encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["cpt"], settings_root=tmp_path,
                              document_parser=model_parser(), chunk_chars=600)
    reader = Reader()
    run = engine.Workflow(output, run_id, tmp_path, generator=reader)
    run.stage = "ingest"
    rows = list(run.iter_source_units(run.recipe["sources"][0]))
    assert len(reader.calls) > 1
    assert all(len(call[2]["text"]) <= 600 for call in reader.calls)
    combined = "\n".join(row["text"] for row in rows if row.get("status") == "ready")
    assert all(f"Section {index} uses {12 + index} V." in combined for index in range(12))


def page_unit(run, page):
    source = run.recipe["sources"][0]
    return {"id": f"offline-page-{page}", "source_id": source["sha256"],
            "source_name": source["name"], "location": f"page:{page}:chunk:0",
            "source_location": {"file": source["name"], "record": f"page:{page}", "chunk": 0},
            "kind": "document", "text": PAGE_ONE, "status": "ready"}


def test_cpt_visual_reference_is_the_exact_source_page_and_is_cached(tmp_path, monkeypatch):
    from lib.infrastructure import document_vision

    output, run_id = create(tmp_path, text_pdf(tmp_path))
    run = engine.Workflow(output, run_id, tmp_path, generator=Reader())
    snapshot = run.path / "inputs" / run.recipe["sources"][0]["file"]
    original = list(visual_parts(snapshot))
    first = run.cpt_source_image(page_unit(run, 2))
    assert first == original[1]["image"]
    assert first != original[0]["image"]

    def forbidden_render(*args, **kwargs):
        raise AssertionError("The same source page should be reused from the bounded cache.")

    monkeypatch.setattr(document_vision, "visual_part", forbidden_render)
    assert run.cpt_source_image(page_unit(run, 2)) == first


def test_cpt_visual_reference_rejects_changed_snapshot_even_after_cache(tmp_path):
    output, run_id = create(tmp_path, text_pdf(tmp_path))
    run = engine.Workflow(output, run_id, tmp_path, generator=Reader())
    unit = page_unit(run, 1)
    assert run.cpt_source_image(unit).startswith("data:image/png;base64,")
    snapshot = run.path / "inputs" / run.recipe["sources"][0]["file"]
    snapshot.write_bytes(snapshot.read_bytes() + b"\nchanged snapshot\n")
    with pytest.raises(ValueError, match="source_snapshot_changed"):
        run.cpt_source_image(unit)


def test_cpt_visual_reference_rejects_a_different_transcribed_image_hash(tmp_path):
    output, run_id = create(tmp_path, text_pdf(tmp_path))
    run = engine.Workflow(output, run_id, tmp_path, generator=Reader())
    unit = page_unit(run, 1)
    unit["document_reading"] = {"image_sha256": "0" * 64}
    with pytest.raises(ValueError, match="document_source_image_changed"):
        run.cpt_source_image(unit)


def test_cpt_visual_reference_does_not_guess_nonvisual_document(tmp_path):
    source = tmp_path / "manual.md"
    source.write_text(PAGE_ONE, encoding="utf-8")
    output, run_id = create(tmp_path, source)
    run = engine.Workflow(output, run_id, tmp_path, generator=Reader())
    unit = page_unit(run, 1)
    unit["source_location"]["record"] = "document"
    assert run.cpt_source_image(unit) is None
