"""Full source readings persist across independent runs and concurrent workers."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
import time

import pytest

from lib.infrastructure.source_library import SourceLibrary, native_parse_signature
from lib.infrastructure import training_workflow as engine
from tests.test_document_model_parser import PAGE_ONE, PAGE_TWO, Reader, configure, model_parser, text_pdf
from tests.test_document_vision import Vision, config as vision_config, parser as vision_parser, image


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Readers are in-memory doubles; this module needs no shared TCP server."""
    yield


def registered(tmp_path):
    path = tmp_path / "original.md"
    path.write_text(PAGE_ONE + "\n\n" + PAGE_TWO, encoding="utf-8")
    library = SourceLibrary(tmp_path / "output")
    source = library.register_source(path, hashlib.sha256(path.read_bytes()).hexdigest(), path.name)
    return library, source, path


def parse(run):
    return list(run.iter_source_units(run.recipe["sources"][0]))


def test_native_reading_is_persistent_and_not_truncated_by_first_run(tmp_path, monkeypatch):
    source = tmp_path / "long.md"
    source.write_text("\n\n".join(f"Section {i}. " + PAGE_ONE * 8 for i in range(50)), encoding="utf-8")
    output = tmp_path / "output"
    first_id = engine.create_run(output, sources=[source], targets=["cpt"], max_units=1, chunk_chars=400)
    first = engine.Workflow(output, first_id, tmp_path)
    first_rows = parse(first)
    assert len(first_rows) > 1
    assert first.state["source_library"]["misses"] == 1
    from lib import doc2corpus
    monkeypatch.setattr(doc2corpus, "import_text", lambda *args, **kwargs: pytest.fail("No repeated extraction"))
    second_id = engine.create_run(output, sources=[source], targets=["sft"], max_units=1000,
                                  chunk_chars=1000, sample_count=3000)
    second = engine.Workflow(output, second_id, tmp_path)
    second_rows = parse(second)
    assert second.state["source_library"]["hits"] == 1
    combined = "\n".join(row["text"] for row in second_rows)
    assert all(f"Section {i}." in combined for i in range(50))
    assert len(second_rows) < len(first_rows)
    assert len(second_rows) > first.recipe["max_units"]


def test_model_reading_reused_across_generation_targets_and_chunk_sizes(tmp_path):
    configure(tmp_path)
    source = text_pdf(tmp_path)
    output = tmp_path / "output"
    reader = Reader()
    first_id = engine.create_run(output, sources=[source], targets=["cpt"], settings_root=tmp_path,
                                document_parser=model_parser(), chunk_chars=600)
    first = engine.Workflow(output, first_id, tmp_path, generator=reader)
    first_rows = parse(first)
    assert len(reader.calls) == 2
    second_id = engine.create_run(output, sources=[source], targets=["sft", "dpo"], settings_root=tmp_path,
                                 document_parser=model_parser(), chunk_chars=2000, sample_count=5000)
    second = engine.Workflow(output, second_id, tmp_path, generator=reader)
    second_rows = parse(second)
    assert len(reader.calls) == 2
    assert [row["text"] for row in second_rows] == [row["text"] for row in first_rows]
    assert second.state["source_library"]["hits"] == 1
    assert all(row["source_location"]["record"] == f"page:{i}" for i, row in enumerate(second_rows, 1))


def test_model_prompt_change_reparses_but_generation_prompt_change_does_not(tmp_path):
    configure(tmp_path)
    source = text_pdf(tmp_path, pages=(PAGE_ONE,))
    output = tmp_path / "output"
    reader = Reader()
    def run(prompts=None):
        identity = engine.create_run(output, sources=[source], targets=["sft"], settings_root=tmp_path,
            document_parser=model_parser(), node_prompts=prompts)
        current = engine.Workflow(output, identity, tmp_path, generator=reader)
        return current, parse(current)
    initial, _ = run()
    generation, _ = run({"sft": {"workflow.sft": "Produce another independent question and answer."}})
    assert len(reader.calls) == 1
    prompt_id = "workflow.document_parse"
    changed_prompt = initial.prompt_text(prompt_id, stage="ingest") + "\nPreserve every heading."
    changed, _ = run({"ingest": {prompt_id: changed_prompt}})
    assert len(reader.calls) == 2
    assert generation.state["source_library"]["hits"] == 1
    assert changed.state["source_library"]["misses"] == 1


def test_model_limit_and_endpoint_change_reparse(tmp_path):
    configure(tmp_path)
    source = text_pdf(tmp_path, pages=(PAGE_ONE,))
    output = tmp_path / "output"
    reader = Reader()
    first_parser = model_parser()
    for index in range(3):
        parser = deepcopy(first_parser)
        if index:
            parser["binding"]["max_output_tokens"] = 16000
        if index == 2:
            import yaml
            path = tmp_path / "configs/backends.yaml"
            settings = yaml.safe_load(path.read_text(encoding="utf-8"))
            settings["backends"]["reader"]["base_url"] = "https://another-offline.invalid/v1"
            path.write_text(yaml.safe_dump(settings), encoding="utf-8")
        identity = engine.create_run(output, sources=[source], targets=["cpt"], settings_root=tmp_path,
                                    document_parser=parser)
        parse(engine.Workflow(output, identity, tmp_path, generator=reader))
    assert len(reader.calls) == 3


def test_native_version_changes_do_not_reuse_another_parser_policy(tmp_path):
    library, source, _ = registered(tmp_path)
    first = list(library.native_parts(source, source_processing_version=1))
    second = list(library.native_parts(source, source_processing_version=2))
    assert first == second
    assert len(library.list_parses(source["id"])) == 2


def test_same_key_concurrent_workers_execute_one_parser(tmp_path):
    library, source, _ = registered(tmp_path)
    calls = []
    gate = threading.Barrier(4)
    def worker():
        gate.wait()
        def build():
            calls.append(threading.get_ident())
            time.sleep(.03)
            yield {"location": "document", "text": PAGE_ONE}
        return list(SourceLibrary(tmp_path / "output").iter_parts(source, {"mode": "native"}, build))
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: worker(), range(4)))
    assert len(calls) == 1
    assert rows == [[{"location": "document", "text": PAGE_ONE}]] * 4


def test_failed_parser_and_incomplete_stream_never_publish_a_cache_hit(tmp_path):
    library, source, _ = registered(tmp_path)
    signature = {"mode": "native"}
    def failed():
        yield {"location": "page:1", "text": PAGE_ONE}
        raise RuntimeError("Interrupted parser")
    with pytest.raises(RuntimeError):
        list(library.iter_parts(source, signature, failed))
    assert library.list_parses(source["id"]) == []
    stream = library.iter_parts(source, signature, lambda: iter([{"location": "page:1", "text": PAGE_ONE}]))
    assert next(stream)["text"] == PAGE_ONE
    stream.close()
    assert library.list_parses(source["id"]) == []
    assert list(library.iter_parts(source, signature,
        lambda: iter([{"location": "page:1", "text": PAGE_ONE}])))
    assert len(library.list_parses(source["id"])) == 1


def test_uncertain_model_output_not_reused_in_a_different_run(tmp_path):
    configure(tmp_path)
    source = text_pdf(tmp_path, pages=(PAGE_ONE,))
    output = tmp_path / "output"
    invalid = Reader(response={"text": PAGE_ONE, "uncertain": True})
    good = Reader()
    for reader in (invalid, good):
        identity = engine.create_run(output, sources=[source], targets=["cpt"], settings_root=tmp_path,
                                    document_parser=model_parser())
        run = engine.Workflow(output, identity, tmp_path, generator=reader)
        rows = parse(run)
    assert len(invalid.calls) == 1 and len(good.calls) == 1
    assert rows[0]["status"] == "ready"


@pytest.mark.parametrize("empty_text", ["", " \n\t"])
def test_nonempty_source_model_erasure_is_quarantined_and_not_cached(tmp_path, empty_text):
    configure(tmp_path)
    source = text_pdf(tmp_path, pages=(PAGE_ONE,))
    output = tmp_path / "output"
    invalid = Reader(response={"text": empty_text, "uncertain": False})
    good = Reader()
    results = []
    for reader in (invalid, good):
        identity = engine.create_run(output, sources=[source], targets=["cpt"], settings_root=tmp_path,
                                    document_parser=model_parser())
        run = engine.Workflow(output, identity, tmp_path, generator=reader)
        results.append(parse(run))
    assert len(results[0]) == 1
    assert results[0][0]["status"] == "quarantined"
    assert results[0][0]["reason"] == "invalid_document_parse_schema"
    assert len(invalid.calls) == len(good.calls) == 1
    assert results[1][0]["text"] == PAGE_ONE


def test_stricter_text_reading_does_not_reuse_previous_empty_success_cache(tmp_path):
    configure(tmp_path)
    source = text_pdf(tmp_path, pages=(PAGE_ONE,))
    output = tmp_path / "output"
    identity = engine.create_run(output, sources=[source], targets=["cpt"], settings_root=tmp_path,
                                document_parser=model_parser())
    reader = Reader()
    run = engine.Workflow(output, identity, tmp_path, generator=reader)
    library = SourceLibrary(output)
    snapshot = run.recipe["sources"][0]
    saved = library.register_source(run.path / "inputs" / snapshot["file"], snapshot["sha256"], source.name)
    old_signature = run._source_library_signature("model")
    list(library.iter_parts(saved, old_signature, lambda: iter([
        {"location": "page:1", "parse_segment": 0, "text": "",
         "document_reading": {"mode": "model", "source_text": PAGE_ONE, "parsed_text": ""}}])))
    rows = parse(run)
    assert len(reader.calls) == 1
    assert rows[0]["status"] == "ready" and rows[0]["text"] == PAGE_ONE
    assert run.state["source_library"]["hits"] == 0


def test_repairing_an_old_empty_page_reuses_the_good_paid_page(tmp_path):
    configure(tmp_path)
    source = text_pdf(tmp_path)
    output = tmp_path / "output"
    identity = engine.create_run(output, sources=[source], targets=["cpt"], settings_root=tmp_path,
                                document_parser=model_parser())
    reader = Reader()
    run = engine.Workflow(output, identity, tmp_path, generator=reader)
    library = SourceLibrary(output)
    snapshot = run.recipe["sources"][0]
    saved = library.register_source(run.path / "inputs" / snapshot["file"], snapshot["sha256"], source.name)
    signature = run._source_library_signature("model")
    old_parts = []
    for index, original in enumerate((PAGE_ONE, PAGE_TWO), 1):
        text = "" if index == 1 else original
        result = {"text": text, "uncertain": False}
        library.cached_value(saved, signature, [f"page:{index}", 0, engine.digest(original)],
                             lambda value=result: value, valid=lambda value: True)
        old_parts.append({"location": f"page:{index}", "parse_segment": 0, "text": text,
                          "document_reading": {"mode": "model", "source_text": original,
                                               "parsed_text": text}})
    list(library.iter_parts(saved, signature, lambda: iter(old_parts)))
    rows = parse(run)
    assert [row["text"] for row in rows] == [PAGE_ONE, PAGE_TWO]
    assert len(reader.calls) == 1 and reader.calls[0][2]["text"] == PAGE_ONE
    assert [row["text"] for row in parse(run)] == [PAGE_ONE, PAGE_TWO]
    assert len(reader.calls) == 1


def test_genuinely_blank_pdf_page_still_skips_without_model_call(tmp_path):
    configure(tmp_path)
    source = text_pdf(tmp_path, pages=(None, PAGE_ONE))
    output = tmp_path / "output"
    identity = engine.create_run(output, sources=[source], targets=["cpt"], settings_root=tmp_path,
                                document_parser=model_parser())
    reader = Reader()
    rows = parse(engine.Workflow(output, identity, tmp_path, generator=reader))
    assert rows[0]["status"] == "skipped" and rows[0]["reason"] == "empty_document_page"
    assert rows[1]["status"] == "ready" and rows[1]["text"] == PAGE_ONE
    assert len(reader.calls) == 1


@pytest.mark.parametrize("empty_text", ["", " \n\t"])
def test_legacy_parser_rejects_empty_model_text_and_invalidates_call_checkpoint(tmp_path, empty_text):
    configure(tmp_path)
    source = text_pdf(tmp_path, pages=(PAGE_ONE,))
    output = tmp_path / "output"
    identity = engine.create_run(output, sources=[source], targets=["cpt"], settings_root=tmp_path,
                                document_parser=model_parser())
    path = engine.run_path(output, identity)
    recipe = engine.read_json(path / "recipe.json")
    recipe.pop("source_library_version")
    engine.atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    engine.atomic_json(path / "state.json", state)
    invalid = Reader(response={"text": empty_text, "uncertain": False})
    first = engine.Workflow(output, identity, tmp_path, generator=invalid)
    first.stage = "ingest"
    rows = parse(first)
    assert len(rows) == 1 and rows[0]["status"] == "quarantined"
    assert rows[0]["reason"] == "invalid_document_parse_schema"
    snapshot = recipe["sources"][0]
    unit_id = engine.digest([snapshot["sha256"], snapshot["file"], "page:1:chunk:0"])
    call_key = ["call", [unit_id, "document_parse", engine.digest(PAGE_ONE)]]
    assert len(invalid.calls) == 1 and not first.checkpoint_has(call_key)
    reader = Reader()
    restored = engine.Workflow(output, identity, tmp_path, generator=reader)
    restored.stage = "ingest"
    assert parse(restored)[0]["text"] == PAGE_ONE
    assert len(reader.calls) == 1 and restored.checkpoint_has(call_key)


@pytest.mark.parametrize("mode,expected", [
    ("native", "source_text"),
    ("model", "model_restructured_source_text"),
    ("vision", "model_transcribed_visual_source"),
])
def test_native_cpt_keeps_the_actual_parser_evidence_modality(tmp_path, mode, expected):
    if mode == "vision":
        vision_config(tmp_path)
        source, reader, parser = image(tmp_path), Vision(), vision_parser()
    elif mode == "model":
        configure(tmp_path)
        source, reader, parser = text_pdf(tmp_path, pages=(PAGE_ONE,)), Reader(), model_parser()
    else:
        configure(tmp_path)
        source = tmp_path / "manual.txt"
        source.write_text(PAGE_ONE, encoding="utf-8")
        reader, parser = Reader(), None
    output = tmp_path / "output"
    identity = engine.create_run(output, sources=[source], targets=["cpt"], settings_root=tmp_path,
                                document_parser=parser)
    run = engine.Workflow(output, identity, tmp_path, generator=reader)
    assert run.execute()["status"] == "completed"
    records = engine.read_json(run.path / "artifacts/cpt.records.json")
    assert len(records) == 1 and records[0]["evidence_level"] == expected
    assert len(reader.calls) == (0 if mode == "native" else 1)


def test_partial_model_document_reuses_completed_pages_in_new_run(tmp_path):
    configure(tmp_path)
    source = text_pdf(tmp_path)
    output = tmp_path / "output"
    reader = Reader()
    first_id = engine.create_run(output, sources=[source], targets=["cpt"], settings_root=tmp_path,
                                document_parser=model_parser())
    first = engine.Workflow(output, first_id, tmp_path, generator=reader)
    iterator = first.iter_source_units(first.recipe["sources"][0])
    assert next(iterator)["text"] == PAGE_ONE
    iterator.close()
    assert len(reader.calls) == 1
    second_id = engine.create_run(output, sources=[source], targets=["cpt"], settings_root=tmp_path,
                                 document_parser=model_parser())
    second = engine.Workflow(output, second_id, tmp_path, generator=reader)
    assert [row["text"] for row in parse(second)] == [PAGE_ONE, PAGE_TWO]
    assert len(reader.calls) == 2


def test_vision_reading_reuses_across_runs_without_rendering_again(tmp_path, monkeypatch):
    vision_config(tmp_path)
    source = image(tmp_path)
    output = tmp_path / "output"
    reader = Vision()
    def create():
        identity = engine.create_run(output, sources=[source], targets=["cpt"], settings_root=tmp_path,
                                    document_parser=vision_parser())
        run = engine.Workflow(output, identity, tmp_path, generator=reader)
        return run, parse(run)
    first, initial = create()
    from lib.infrastructure import document_vision
    monkeypatch.setattr(document_vision, "visual_parts", lambda *args, **kwargs: pytest.fail("No repeated rendering"))
    second, reused = create()
    assert initial == reused
    assert second.state["source_library"]["hits"] == 1
    assert len(reader.calls) == 1


def test_renamed_same_format_source_reuses_stable_model_prompt_and_keeps_new_provenance(tmp_path):
    configure(tmp_path)
    first_source = text_pdf(tmp_path, pages=(PAGE_ONE,))
    renamed = tmp_path / "renamed.pdf"
    renamed.write_bytes(first_source.read_bytes())
    output = tmp_path / "output"
    reader = Reader()
    for source in (first_source, renamed):
        identity = engine.create_run(output, sources=[source], targets=["cpt"], settings_root=tmp_path,
                                    document_parser=model_parser())
        run = engine.Workflow(output, identity, tmp_path, generator=reader)
        rows = parse(run)
    assert len(reader.calls) == 1
    assert rows[0]["source_name"] == "renamed.pdf"
    assert rows[0]["source_location"]["file"] == "renamed.pdf"
    assert reader.calls[0][2]["source"] == "document.pdf"


def test_tampered_parse_and_original_are_not_trusted(tmp_path):
    library, source, _ = registered(tmp_path)
    list(library.native_parts(source))
    parse_id = library.parse_id(source, native_parse_signature())
    parts = library.path / "parses" / source["id"] / parse_id / "parts.jsonl"
    parts.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source_library_integrity_error"):
        list(library.native_parts(source))
    original = library.source_path(source["id"])
    original.write_text("tampered original", encoding="utf-8")
    with pytest.raises(ValueError, match="source_library_original_changed"):
        library.source_path(source["id"])
    with pytest.raises(ValueError, match="source_library_original_changed"):
        list(library.native_parts(source))


def test_library_metadata_is_bounded_and_never_includes_provider_secrets(tmp_path):
    library, source, path = registered(tmp_path)
    list(library.native_parts(source))
    status = library.describe_source(path)
    assert status["status"] == "parsed" and status["modes"] == ["native"]
    assert status["parts"] == 1 and status["text_chars"] > 0
    assert library.list_sources(limit=1)[0]["name"] == "original.md"
    with pytest.raises(ValueError, match="invalid_source_library_limit"):
        library.list_sources(limit=5001)
    assert "api_key" not in json.dumps(library.list_sources(), ensure_ascii=False)


def test_original_still_available_after_run_and_upload_are_removed(tmp_path):
    library, source, path = registered(tmp_path)
    list(library.native_parts(source))
    path.unlink()
    restored = SourceLibrary(tmp_path / "output")
    assert restored.source_path(source["id"]).read_text(encoding="utf-8") == PAGE_ONE + "\n\n" + PAGE_TWO
    assert list(restored.native_parts(source))[0]["text"]


def test_preview_and_workflow_reuse_the_same_complete_native_reading(tmp_path, monkeypatch):
    from lib import workspace
    from lib.bootstrap.document_previews import document_preview_application
    folder = tmp_path / "data/seeds"
    folder.mkdir(parents=True)
    path = folder / "manual.md"
    path.write_text(PAGE_ONE + "\n\n" + PAGE_TWO, encoding="utf-8")
    preview = document_preview_application(root=tmp_path).preview(str(path), 200)
    assert preview["characters"] > 0
    monkeypatch.setattr(engine, "import_text", lambda *args, **kwargs: pytest.fail("Preview already extracted this source"))
    output = workspace.out("default", tmp_path)
    identity = engine.create_run(output, sources=[path], targets=["cpt"])
    run = engine.Workflow(output, identity, tmp_path)
    assert parse(run)[0]["status"] == "ready"
    assert run.state["source_library"]["hits"] == 1


def test_blank_and_image_only_extractions_are_not_reported_as_usable(tmp_path):
    library = SourceLibrary(tmp_path / "output")
    blank = tmp_path / "blank.txt"
    blank.write_text(" \n\n", encoding="utf-8")
    source = library.register_source(blank, hashlib.sha256(blank.read_bytes()).hexdigest())
    list(library.native_parts(source))
    assert library.describe_source(blank)["status"] == "empty"
    assert library.describe_source(blank)["usable_parts"] == 0
    assert library.describe_source(blank)["empty_parts"] == 1
    scan = text_pdf(tmp_path, pages=(None,), name="scan.pdf")
    scan_source = library.register_source(scan, hashlib.sha256(scan.read_bytes()).hexdigest())
    signature = native_parse_signature()
    list(library.iter_parts(scan_source, signature, lambda: iter([
        {"location": "page:1", "text": "", "requires_vision": True}])))
    status = library.describe_source(scan)
    assert status["status"] == "requires_vision"
    assert status["requires_vision"] == 1 and status["usable_parts"] == 0


def test_selected_source_status_reads_its_id_directly_without_library_scan(tmp_path, monkeypatch):
    library, source, path = registered(tmp_path)
    list(library.native_parts(source))
    actual_iterdir = Path.iterdir
    def guarded_iterdir(path):
        if path == library.path / "sources":
            pytest.fail("Selected sources must not scan the entire source inventory")
        return actual_iterdir(path)
    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)
    assert library.describe_source(path)["status"] == "parsed"


def test_file_lock_coordinates_separate_parser_processes(tmp_path):
    import subprocess
    import sys
    library, source, _ = registered(tmp_path)
    marker = tmp_path / "parser-calls.txt"
    script = """
import json, sys, time
from pathlib import Path
from lib.infrastructure.source_library import SourceLibrary
library = SourceLibrary(sys.argv[1])
source = json.loads(sys.argv[2])
def build():
    with Path(sys.argv[3]).open('a', encoding='utf-8') as handle:
        handle.write('called\\n')
    time.sleep(.2)
    yield {'location': 'document', 'text': 'Verified source text'}
list(library.iter_parts(source, {'mode': 'native'}, build))
"""
    arguments = [sys.executable, "-X", "utf8", "-c", script, str(tmp_path / "output"), json.dumps(source), str(marker)]
    first = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    second = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for process in (first, second):
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr.decode("utf-8")
    assert marker.read_text(encoding="utf-8").splitlines() == ["called"]


def test_rag_reindex_reuses_the_complete_preview_extraction(tmp_path, monkeypatch):
    from lib.bootstrap.document_previews import document_preview_application
    from lib.bootstrap.knowledge import knowledge_application
    from lib.infrastructure import knowledge_driver
    folder = tmp_path / "data/seeds"
    folder.mkdir(parents=True)
    path = folder / "manual.txt"
    path.write_text(PAGE_ONE + "\n\n" + PAGE_TWO, encoding="utf-8")
    document_preview_application(root=tmp_path).preview(str(path), 200)
    monkeypatch.setattr(knowledge_driver, "import_text", lambda *args, **kwargs: pytest.fail("Existing extraction must be reused"))
    application = knowledge_application(root=tmp_path)
    assert application.index([str(path)])["updated"] == 1
    assert application.index([str(path)], chunk_chars=400)["updated"] == 1
    assert application.retrieve("local", "power cable", 2)["hits"]


def test_waiting_for_another_parser_can_be_cancelled_without_duplicate_work(tmp_path):
    library, source, _ = registered(tmp_path)
    started, release = threading.Event(), threading.Event()
    signature = {"mode": "native"}
    def build():
        started.set()
        assert release.wait(5)
        yield {"location": "document", "text": PAGE_ONE}
    checks = []
    def cancelled():
        checks.append(True)
        if len(checks) > 1:
            raise RuntimeError("Cancelled waiting parser")
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(lambda: list(library.iter_parts(source, signature, build)))
        try:
            assert started.wait(3)
            with pytest.raises(RuntimeError, match="Cancelled waiting parser"):
                list(SourceLibrary(tmp_path / "output").iter_parts(source, signature,
                    lambda: pytest.fail("Cancelled waiter must not start parsing"), check_cancel=cancelled))
        finally:
            release.set()
        assert first.result(timeout=5)[0]["text"] == PAGE_ONE


def test_first_reading_progress_is_published_before_document_completion(tmp_path):
    library, source, _ = registered(tmp_path)
    notes = []
    signature = {"mode": "model"}
    iterator = library.iter_parts(source, signature, lambda: iter([
        {"location": "page:1", "text": PAGE_ONE},
        {"location": "page:2", "text": PAGE_TWO}]), on_status=notes.append)
    assert next(iterator)["text"] == PAGE_ONE
    assert notes[-1]["status"] == "parsing"
    assert notes[-1]["parts"] == 1 and notes[-1]["usable_parts"] == 1
    assert notes[-1]["text_chars"] == len(PAGE_ONE)
    assert notes[-1]["location"] == "page:1"
    assert library.list_parses(source["id"]) == []
    iterator.close()
    assert notes[-1]["status"] == "interrupted" and notes[-1]["parts"] == 1
    assert library.list_parses(source["id"]) == []


def test_import_progress_is_throttled_and_complete_receipt_keeps_exact_counts(tmp_path, monkeypatch):
    from lib.infrastructure import source_library as storage
    library, source, _ = registered(tmp_path)
    notes = []
    clock = [0.0]
    monkeypatch.setattr(storage.time, "monotonic", lambda: clock[0])
    def build():
        for index in range(1000):
            if index == 500:
                clock[0] = .5
            yield {"location": index, "text": "A verified source paragraph."}
    assert len(list(library.iter_parts(source, {"mode": "native"}, build, on_status=notes.append))) == 1000
    # Opening, first part, interval progress, and final publication; not 1000 writes.
    assert len(notes) == 4
    assert [note["parts"] for note in notes] == [0, 1, 501, 1000]
    assert notes[-1]["status"] == "complete" and notes[-1]["usable_parts"] == 1000


def test_workflow_progress_is_durable_before_next_model_request(tmp_path):
    configure(tmp_path)
    source = text_pdf(tmp_path)
    output = tmp_path / "output"
    identity = engine.create_run(output, sources=[source], targets=["cpt"], settings_root=tmp_path,
                                document_parser=model_parser())
    path = engine.run_path(output, identity)
    checked = []
    class InspectingReader(Reader):
        def chat(self, messages, **options):
            if self.calls:
                saved = json.loads((path / "state.json").read_text(encoding="utf-8"))
                assert saved["source_library"]["parts"] == 1
                assert saved["source_library"]["sources"][0]["status"] == "parsing"
                assert saved["stages"]["ingest"]["parsed_parts"] == 1
                assert saved["stages"]["ingest"]["input_chars"] == len(PAGE_ONE)
                assert saved["stages"]["ingest"]["current_source"] == source.name
                assert saved["stages"]["ingest"]["current_location"] == "page:1"
                assert saved["stages"]["ingest"]["parsed_sources"] == 0
                checked.append(True)
            return super().chat(messages, **options)
    run = engine.Workflow(output, identity, tmp_path, generator=InspectingReader())
    assert len(parse(run)) == 2
    assert checked == [True]
    assert run.state["source_library"]["parts"] == 2
    assert run.state["stages"]["ingest"]["parsed_sources"] == 1
    assert run.state["stages"]["ingest"]["current_location"] == "page:2"


def test_failed_parse_progress_remains_failed_without_publishing_successful_cache(tmp_path):
    library, source, _ = registered(tmp_path)
    notes = []
    def build():
        yield {"location": "page:1", "text": PAGE_ONE}
        raise RuntimeError("Offline failure after first page")
    with pytest.raises(RuntimeError):
        list(library.iter_parts(source, {"mode": "model"}, build, on_status=notes.append))
    assert notes[-1]["status"] == "failed"
    assert notes[-1]["parts"] == 1 and notes[-1]["usable_parts"] == 1
    assert not any(note["status"] == "complete" for note in notes)
    assert library.list_parses(source["id"]) == []
