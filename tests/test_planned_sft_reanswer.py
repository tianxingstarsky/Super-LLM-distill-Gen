"""A new experience changes only the author context, not the saved learner task."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from lib.bootstrap.automatic_review import reviewed_checkpoint_input
from lib.bootstrap.workflows import workflow_application
from lib.contexts.workflows.domain.public_experience import with_public_experience
from lib.contexts.workflows.domain.planned_sft_reanswer import planned_sft_prompt, valid_reanswer_receipt
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_store import digest, file_hash
from lib.io_utils import atomic_json


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


@pytest.fixture
def sealed_native(tmp_path, monkeypatch):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/preferences.yaml").write_bytes((Path(__file__).resolve().parents[1] / "configs/preferences.yaml").read_bytes())
    source = tmp_path / "source.txt"
    source.write_text("维护前必须先断电。", encoding="utf8")
    output = tmp_path / "output"
    binding = {"backend": "offline", "model": "author"}
    pin = {"version": 1, "backend": "offline", "model": "author"}
    monkeypatch.setattr(engine, "snapshot_backend_endpoint", lambda *a: deepcopy(pin))
    rid = engine.create_run(output, sources=[source], targets=["sft"], settings_root=tmp_path,
        node_models={"sft": {"generation": binding}, "review": {"generation": binding}},
        node_generation={"sft": {"enabled": True, "style": "default", "answer_mode": "native_joint"}},
        review_repair={"mode": "auto", "max_rounds": 0}, package_review={"enabled": False})
    path = engine.run_path(output, rid)
    recipe = engine.read_json(path / "recipe.json")
    text = source.read_text("utf8")
    contract = {"id": "fixed", "question": "维护前做什么？", "visible_context": "",
                "qa_type": "closed_book", "answer_policy": "answer", "evidence_quotes": [text], "guidance": "按来源解释。"}
    prompt = [{"role": "user", "content": contract["question"]}]
    prompt = with_public_experience(prompt, recipe["agent_experience"]["scopes"]["reasoning"]["text"])
    source_id = recipe["sources"][0]["sha256"]
    row = {"id": "fixed", "source_id": source_id, "status": "eligible", "qa_contract": contract,
        "family_id": "family", "source_context": {"kind": "document", "text": text},
        "provided_source_context": {"scope": "prepared_source", "text_sha256": digest(text)},
        "messages": [*prompt, {"role": "assistant", "content": "旧答案。", "reasoning_content": "旧推理。"}],
        "native_generation": {"version": 1}, "review_observation": {"verdict": {"keep": True}},
        "review_repair": {"status": "released_advisory"}}
    stage = path / "stage-results/review-sft.jsonl"; stage.parent.mkdir(exist_ok=True)
    stage.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf8")
    atomic_json(path / "checkpoints/sft/paid.json", {"data": {"answer": "旧答案。"}, "sha256": digest({"answer": "旧答案。"})})
    atomic_json(path / "artifacts/sft.records.json", [row])
    atomic_json(path / "artifacts/manifest.json", {"run_id": rid, "status": "complete", "recipe_hash": digest(recipe),
        "sha256": {"sft.records.json": file_hash(path / "artifacts/sft.records.json")}})
    state = engine.read_json(path / "state.json"); state["status"] = "completed"; atomic_json(path / "state.json", state)
    return tmp_path, output, rid, row


def test_reanswer_reuses_exact_task_and_pays_only_author_and_one_review(sealed_native):
    root, output, rid, original = sealed_native
    app = workflow_application(root, output)
    child = app.reanswer_sft(rid, candidate_ids=[original["id"]], agent_experience={"reasoning": {"enabled": True, "text": "检查必要前提，按证据回答。"}})
    recipe = app.recipe(child)
    assert recipe["agent_experience"]["scopes"]["review"] == app.recipe(rid)["agent_experience"]["scopes"]["review"]
    assert recipe["sft_reanswer_version"] == 1
    summary = next(run for run in app.task_runs() if run["id"] == child)
    assert summary["recipe_readable"] is True
    assert summary["source_mode"] == "planned_sft"
    assert summary["scheduled_stages"] == ["ingest", "sft", "review", "package"]
    assert recipe["node_models"]["sft"]["generation"] == app.recipe(rid)["node_models"]["sft"]["generation"]
    calls = []
    worker = engine.Workflow(output, child, root)
    def ask(key, role, prompt, data, **kwargs):
        calls.append((worker.stage, prompt, deepcopy(data)))
        if worker.stage == "sft":
            assert "旧答案" not in json.dumps(data, ensure_ascii=False)
            assert "旧推理" not in json.dumps(data, ensure_ascii=False)
            return {"reasoning": "用户问维护的前提。我先确认断电要求，不能在带电时维护。", "answer": "维护前必须先断电。"}
        assert worker.stage == "review"
        return {"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
                "scores": {name: 5 for name in ("correctness", "reasoning", "grounding", "instruction", "safety")},
                "reason": "符合来源。"}
    worker.ask = ask
    state = worker.execute()
    assert state["status"] == "completed", state.get("error")
    assert [stage for stage, _, _ in calls] == ["sft", "review"]
    row = json.loads((worker.path / "artifacts/sft.records.json").read_text("utf8"))[0]
    assert row["qa_contract"] == original["qa_contract"]
    assert valid_reanswer_receipt(row)
    assert planned_sft_prompt(row, recipe) == planned_sft_prompt(original, app.recipe(rid))
    assert row["messages"][-1]["content"] != original["messages"][-1]["content"]
    assert worker.state["stages"]["director"]["status"] == "skipped"
    assert reviewed_checkpoint_input(output, child, "sft", row["id"])["record"] == row
    from lib.contexts.workflows.infrastructure.planned_sft_reanswer import reanswer_duplicate_allowed
    assert reanswer_duplicate_allowed(worker, row, {"run_id": rid, "id": rid + ":fixed"})
    assert not reanswer_duplicate_allowed(worker, row, {"run_id": "b" * 32, "id": "b" * 32 + ":fixed"})
    assert not reanswer_duplicate_allowed(worker, row, {"run_id": child, "id": child + ":another"})


def test_reanswer_rejects_unknown_system_or_source_change_before_model(sealed_native):
    root, output, rid, original = sealed_native
    parent = workflow_application(root, output).recipe(rid)
    changed = deepcopy(original)
    changed["messages"].insert(1, {"role": "system", "content": "Unknown instructions"})
    with pytest.raises(ValueError, match="unsupported_history"):
        planned_sft_prompt(changed, parent)
    item = reviewed_checkpoint_input(output, rid, "sft", original["id"])
    item["record"]["qa_contract"]["question"] = "改过的问题？"
    with pytest.raises(ValueError):
        engine.create_run(output, targets=["sft"], repair_inputs=[item], reanswer_sft=True,
            review_repair={"mode": "auto", "max_rounds": 0})


def test_no_auto_review_does_not_claim_advisory_receipt(tmp_path):
    rid = engine.create_run(tmp_path, brief="解释维护前的安全操作。", targets=["sft"])
    assert "review_advisory_policy_version" not in engine.read_json(engine.run_path(tmp_path, rid) / "recipe.json")


def test_reanswer_preserves_original_selected_block_newline():
    from lib.contexts.workflows.domain.planned_sft_reanswer import authenticated_teacher_text
    text = "选定资料第一行。\n"
    record = {"provided_source_context": {"scope": "selected_lines", "text_sha256": digest(text), "text_chars": len(text)}}
    evidence = {"teacher_evidence": text.strip(), "teacher_evidence_blocks": [text]}
    assert authenticated_teacher_text(record, evidence) == text
    with pytest.raises(ValueError, match="source_scope_changed"):
        authenticated_teacher_text(record, {"teacher_evidence": text.strip(), "teacher_evidence_blocks": [text.strip()]})
