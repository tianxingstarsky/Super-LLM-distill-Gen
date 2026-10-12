"""Large clean evidence windows stay durable, bounded and prefix-reusable."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from lib.domain.workflow_quality import canonical
from lib.infrastructure.workflow_rows import WorkflowRows, write_jsonl
from lib.infrastructure.workflow_source_context import (
    context_capacity, load_context_pack, pack_evidence_text, prepare_source_context_packs,
)


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """No request in this module reaches a model service or local TCP port."""
    yield


def cleaned(index, text, *, source="manual", status="ready"):
    text_hash = hashlib.sha256(canonical(text).encode()).hexdigest()
    return {"id": f"unit-{source}-{index}", "source_id": source, "source_name": source + ".md",
            "kind": "document", "status": status, "text": text,
            "source_location": {"file": source + ".md", "record": "chapter:1", "chunk": index},
            "source_cleaning": {"status": "accepted", "cleaned_text_sha256": text_hash},
            "document_reading": {"source_text": "RAW UNREVIEWED MUST NOT ENTER THE PACK"}}


def workflow(tmp_path, *, context=32768, output=4096, marker=1):
    recipe = {"source_context_pack_version": marker,
              "node_prompt_system": "Teach from verified materials.",
              "node_prompt_templates": {"sft": {"workflow.sft": "Create a useful dialogue."}},
              "node_models": {"sft": {"generation": {
                  "context_window_tokens": context, "max_output_tokens": output}}}}
    return SimpleNamespace(path=tmp_path, recipe=recipe, check_cancel=lambda: None)


def rows(tmp_path, values):
    destination = tmp_path / "clean.jsonl"
    write_jsonl(destination, iter(values))
    return WorkflowRows(destination, len(values), predicate=lambda value: value["status"] in {"ready", "eligible"})


def test_full_clean_document_injected_not_an_isolated_chunk(tmp_path):
    units = [cleaned(0, "Preparation: shut down the pump and wait ten minutes."),
             cleaned(1, "Inspection: remove the cover and inspect the gasket."),
             cleaned(2, "Restart: tighten the cover and reopen the outlet valve.")]
    worker = workflow(tmp_path)
    prepared = list(prepare_source_context_packs(worker, rows(tmp_path, units)))
    assert prepared[0]["text"] == units[0]["text"]
    assert prepared[0]["source_context_pack"] == prepared[1]["source_context_pack"] == prepared[2]["source_context_pack"]
    pack = load_context_pack(tmp_path, prepared[0]["source_context_pack"])
    assert [material["text"] for material in pack["materials"]] == [unit["text"] for unit in units]
    assert "RAW UNREVIEWED" not in canonical(pack)
    assert prepared[2]["text"] in pack_evidence_text(prepared[0], tmp_path)
    assert len(canonical(prepared[0]["source_context_pack"])) < 600


def test_quarantine_raw_and_invalid_clean_receipts_are_excluded(tmp_path):
    valid = cleaned(0, "Verified pressure is 45 kPa.")
    rejected = cleaned(1, "SECRET QUARANTINED FALSE MATERIAL", status="quarantined")
    prepared = list(prepare_source_context_packs(workflow(tmp_path), rows(tmp_path, [valid, rejected])))
    assert len(prepared) == 1
    assert "QUARANTINED" not in pack_evidence_text(prepared[0], tmp_path)
    valid["source_cleaning"]["cleaned_text_sha256"] = "invalid"
    with pytest.raises(ValueError, match="accepted_clean_text"):
        prepare_source_context_packs(workflow(tmp_path), rows(tmp_path, [valid]))


def test_missing_pack_reports_an_actionable_source_error_without_anchor_fallback(tmp_path):
    prepared = list(prepare_source_context_packs(workflow(tmp_path), rows(tmp_path, [
        cleaned(0, "Verified pressure is 45 kPa.")])))
    reference = prepared[0]["source_context_pack"]
    (tmp_path / reference["relative_path"]).unlink()
    with pytest.raises(ValueError, match="source_context_pack_missing"):
        pack_evidence_text(prepared[0], tmp_path)


def test_large_document_uses_complete_neighboring_windows_and_shared_prefix(tmp_path):
    units = [cleaned(index, f"Section {index}: " + ("gasket pressure valve " * 85)) for index in range(28)]
    worker = workflow(tmp_path, context=24000, output=2048)
    prepared = list(prepare_source_context_packs(worker, rows(tmp_path, units)))
    identities = [unit["source_context_pack"]["id"] for unit in prepared]
    assert len(set(identities)) > 1
    assert identities[0] == identities[1]
    for unit in prepared:
        pack = load_context_pack(tmp_path, unit["source_context_pack"])
        assert unit["text"] in [material["text"] for material in pack["materials"]]
        assert len(pack["materials"]) > 1
        assert all(material["text"] in [original["text"] for original in units] for material in pack["materials"])
        assert unit["source_context_pack"]["input_bytes"] < context_capacity(worker.recipe)


def test_clean_library_search_supplies_relevant_complete_materials(tmp_path):
    units = [cleaned(0, "Pump pressure problems require valve inspection.", source="task"),
             cleaned(0, "Valve inspection: close the pump inlet before pressure testing.", source="reference"),
             cleaned(0, "A star emits visible light during nuclear fusion.", source="unrelated")]
    prepared = list(prepare_source_context_packs(workflow(tmp_path), rows(tmp_path, units)))
    task = next(unit for unit in prepared if unit["source_id"] == "task")
    pack = load_context_pack(tmp_path, task["source_context_pack"])
    assert {material["source_id"] for material in pack["materials"]} == {"task", "reference"}


def test_prefix_body_and_index_reused_across_resume_without_mutation(tmp_path):
    worker = workflow(tmp_path)
    source_rows = rows(tmp_path, [cleaned(0, "A complete source paragraph."), cleaned(1, "Its complete follow-up paragraph.")])
    first = list(prepare_source_context_packs(worker, source_rows))
    index = tmp_path / "source-context/index.sqlite3"
    stamp = index.stat().st_mtime_ns
    second = list(prepare_source_context_packs(worker, source_rows))
    assert first == second and index.stat().st_mtime_ns == stamp
    pack = load_context_pack(tmp_path, first[0]["source_context_pack"])
    pack["materials"][0]["text"] = "Caller changed its copy"
    assert load_context_pack(tmp_path, first[0]["source_context_pack"])["materials"][0]["text"] != "Caller changed its copy"
    assert not (tmp_path / "source-context/index.pending.sqlite3").exists()


def test_pinned_body_and_relative_path_cannot_be_changed(tmp_path):
    prepared = list(prepare_source_context_packs(workflow(tmp_path), rows(tmp_path, [cleaned(0, "Pinned clean body.")])))
    reference = deepcopy(prepared[0]["source_context_pack"])
    reference["relative_path"] = "../unreviewed.json"
    with pytest.raises(ValueError, match="pack_invalid"):
        load_context_pack(tmp_path, reference)
    reference = prepared[0]["source_context_pack"]
    location = tmp_path / reference["relative_path"]
    body = json.loads(location.read_text(encoding="utf-8"))
    body["materials"][0]["text"] = "Changed material that passed no review."
    location.write_text(canonical(body), encoding="utf-8")
    with pytest.raises(ValueError, match="pack_invalid|pack_changed"):
        load_context_pack(tmp_path, reference)


def test_capacity_reserves_output_rules_and_whole_anchor_instead_of_head_clipping(tmp_path):
    worker = workflow(tmp_path, context=20000, output=4096)
    source = cleaned(0, "Substantive long body " * 80)
    prepared = list(prepare_source_context_packs(worker, rows(tmp_path, [source])))
    assert pack_evidence_text(prepared[0], tmp_path) == source["text"]
    worker.recipe["qa_director"] = {"question_rules": "Question design rule. " * 220,
                                   "answer_rules": "Answer design rule. " * 220}
    with pytest.raises(ValueError, match="capacity_too_small"):
        prepare_source_context_packs(worker, rows(tmp_path, [source]))


def test_source_free_questioner_does_not_shrink_clean_evidence_capacity(tmp_path):
    worker = workflow(tmp_path, context=131072, output=32768)
    worker.recipe["source_context_capacity_version"] = 2
    worker.recipe["qa_director"] = {"enabled": True, "question_separation": True}
    worker.recipe["node_prompt_templates"]["director"] = {"workflow.qa_director": "Plan the dialogue."}
    worker.recipe["node_models"]["director"] = {
        "generation": {"context_window_tokens": 131072, "max_output_tokens": 32768}}
    expected = context_capacity(worker.recipe)
    worker.recipe["node_models"]["director"]["question"] = {
        "context_window_tokens": 8192, "max_output_tokens": 2048}
    worker.recipe["node_prompt_templates"]["director"]["workflow.question_user"] = "Public role instructions. " * 300
    assert context_capacity(worker.recipe) == expected
    source = cleaned(0, "Complete verified source material. " * 200)
    prepared = list(prepare_source_context_packs(worker, rows(tmp_path, [source])))
    assert pack_evidence_text(prepared[0], tmp_path) == source["text"]

    # The source-aware planner still has to fit its evidence, independently of
    # whether the separate questioner can process only a much smaller brief.
    worker.recipe["node_models"]["director"]["generation"]["context_window_tokens"] = 8192
    with pytest.raises(ValueError, match="capacity_too_small"):
        context_capacity(worker.recipe)


def test_numbered_request_does_not_reserve_a_second_copy_of_source_body(tmp_path):
    from lib.infrastructure.model_context_prefix import split_context_request
    worker = workflow(tmp_path)
    worker.recipe["source_context_capacity_version"] = 2
    worker.recipe["source_line_protocol_version"] = 1
    source = cleaned(0, "Complete verified source material. " * 350)
    prepared = list(prepare_source_context_packs(worker, rows(tmp_path, [source])))
    request = split_context_request(tmp_path, {"source": prepared[0]})
    body = request["teacher_context_pack"][0]["materials"][0]["numbered_text"]
    assert body == "1: " + source["text"]
    assert "text" not in request["task_payload"]["source"]
    assert len(canonical(request).encode("utf-8")) < context_capacity(worker.recipe)
    assert pack_evidence_text(prepared[0], tmp_path) == source["text"]


def test_legacy_capacity_and_pack_remain_pinned_when_new_policy_is_available(tmp_path, monkeypatch):
    from lib.infrastructure import workflow_source_context as context_module
    old_path, new_path = tmp_path / "old-run", tmp_path / "new-run"
    old_path.mkdir()
    new_path.mkdir()
    old = workflow(old_path)
    old.recipe["source_line_protocol_version"] = 1
    old.recipe["qa_director"] = {"enabled": True, "question_separation": True}
    old.recipe["node_prompt_templates"]["director"] = {
        "workflow.qa_director": "Plan the dialogue.",
        "workflow.question_user": "Question style. " * 10}
    old.recipe["node_models"]["director"] = {
        "generation": {"context_window_tokens": 32768, "max_output_tokens": 4096},
        "question": {"context_window_tokens": 20000, "max_output_tokens": 2048}}
    units = [cleaned(index, f"Section {index}. " + "Verified complete source text. " * 90) for index in range(8)]
    old_rows = rows(old_path, units)
    first = list(prepare_source_context_packs(old, old_rows))
    manifest_path = old_path / "source-context/manifest.json"
    original_manifest = manifest_path.read_bytes()
    original_index = (old_path / "source-context/index.sqlite3").stat().st_mtime_ns
    manifest = json.loads(original_manifest)
    pack = load_context_pack(old_path, first[0]["source_context_pack"])
    largest_anchor = max(len(canonical(context_module.teacher_material(
        context_module._material(unit, numbered=True))).encode("utf-8")) + 1 for unit in units)
    assert manifest["capacity_bytes"] == context_capacity(old.recipe) - largest_anchor

    with monkeypatch.context() as patch:
        patch.setattr(context_module, "_save_pack", lambda *_: pytest.fail("Resume must reuse the pinned old pack"))
        resumed = list(prepare_source_context_packs(old, old_rows))
    assert resumed == first
    assert manifest_path.read_bytes() == original_manifest
    assert (old_path / "source-context/index.sqlite3").stat().st_mtime_ns == original_index

    new = SimpleNamespace(path=new_path, recipe=deepcopy(old.recipe), check_cancel=lambda: None)
    new.recipe["source_context_capacity_version"] = 2
    assert context_capacity(new.recipe) > context_capacity(old.recipe)
    updated = list(prepare_source_context_packs(new, rows(new_path, units)))
    new_manifest = json.loads((new_path / "source-context/manifest.json").read_text(encoding="utf-8"))
    assert new_manifest["capacity_bytes"] == context_capacity(new.recipe)
    assert updated[0]["source_context_pack"]["id"] != first[0]["source_context_pack"]["id"]
    assert len(load_context_pack(new_path, updated[0]["source_context_pack"])["materials"]) > len(pack["materials"])


def test_legacy_recipe_rows_and_conversation_records_keep_existing_behavior(tmp_path):
    source_rows = rows(tmp_path, [cleaned(0, "Legacy exact source.")])
    assert prepare_source_context_packs(workflow(tmp_path, marker=None), source_rows) is source_rows
    conversation = {"id": "conversation", "source_id": "session", "kind": "conversation", "status": "ready",
                    "messages": [{"role": "user", "content": "Recorded real exchange"}]}
    assert list(prepare_source_context_packs(workflow(tmp_path), rows(tmp_path, [conversation]))) == [conversation]


def test_many_units_use_disk_assignments_and_do_not_duplicate_pack_bodies(tmp_path):
    count = 1200
    source_rows = rows(tmp_path, [cleaned(index, f"Unique verified paragraph {index}. " + "Pump valve safety. " * 20)
                                 for index in range(count)])
    prepared = prepare_source_context_packs(workflow(tmp_path), source_rows)
    assert len(prepared) == count
    with sqlite3.connect(tmp_path / "source-context/index.sqlite3") as database:
        assert database.execute("SELECT COUNT(*) FROM assignments").fetchone()[0] == count
        assert database.execute("SELECT MAX(LENGTH(reference)) FROM assignments").fetchone()[0] < 600
    assert sum(1 for _ in prepared) == count
