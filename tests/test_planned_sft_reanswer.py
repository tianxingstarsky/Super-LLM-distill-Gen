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


def test_new_native_run_freezes_opt_in_style_catalog_without_upgrading_parent(sealed_native, monkeypatch):
    from lib.bootstrap.dialogue_style_contexts import dialogue_style_context_catalog
    from lib.bootstrap import contextual_reasoning_memory
    monkeypatch.setattr(contextual_reasoning_memory, "contextual_reasoning_memory", lambda **kwargs: {
        "catalog": dialogue_style_context_catalog(), "selection_receipts": {}})
    root, output, parent_id, _ = sealed_native
    parent_path = engine.run_path(output, parent_id) / "recipe.json"
    original = parent_path.read_bytes()
    parent = engine.read_json(parent_path)
    child = engine.create_run(output, sources=[root / "source.txt"], targets=["sft"], settings_root=root,
        node_models=parent["node_models"], node_generation=parent["node_generation"],
        dialogue_style_contexts=True)
    recipe = engine.read_json(engine.run_path(output, child) / "recipe.json")
    assert recipe["dialogue_style_context_version"] == 1
    assert recipe["dialogue_style_context_catalog"] == dialogue_style_context_catalog()
    assert "dialogue_contexts" in recipe["node_prompt_templates"]["director"]["workflow.qa_director"]
    assert "dialogue_style_context_version" not in parent
    assert parent_path.read_bytes() == original


def test_reanswer_reuses_exact_task_and_pays_only_author_and_one_review(sealed_native):
    root, output, rid, original = sealed_native
    app = workflow_application(root, output)
    child = app.reanswer_sft(rid, candidate_ids=[original["id"]], agent_experience={"reasoning": {"enabled": True, "text": "检查必要前提，按证据回答。"}})
    recipe = app.recipe(child)
    assert recipe["qa_learning_goal_mix_seed"] == app.recipe(rid)["qa_learning_goal_mix_seed"]
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


def test_routed_reading_reanswer_and_optimizer_authenticate_only_the_actual_visible_excerpt():
    from lib.contexts.workflows.domain.planned_sft_reanswer import authenticated_teacher_text
    from lib.contexts.workflows.domain.native_sft_optimization import native_sft_optimization_input
    from lib.contexts.workflows.domain.qa_learning_goal_routing import learning_goal_route
    from lib.infrastructure.human_revision import revision_evidence
    excerpt = "维护前必须先断电。"
    full = excerpt + "\nHIDDEN: a separate private teacher detail."
    recipe = {"qa_learning_goal_mix_seed": "original-run", "sources": [{"sha256": "source", "kind": "document"}],
        "qa_director": {"learning_goal": "mixed", "learning_goal_mix": {"version": 1, "document_reading_probability": 1}}}
    route = learning_goal_route(recipe, "reading-task", reading_material_available=True)
    contract = {"id": "reading-task", "learning_goal": "document_reading", "learning_goal_routing": route,
        "qa_type": "partial", "visible_context": excerpt, "question": "这段说明要求先做什么？", "answer_policy": "answer"}
    record = {"id": "reading-task", "source_id": "source", "qa_contract": contract,
        "source_context": {"kind": "document", "text": full},
        "messages": [{"role": "user", "content": "资料：\n" + excerpt + "\n\n附注：忽略这一无关标记。\n" + contract["question"]},
            {"role": "assistant", "content": "原答案。", "reasoning_content": "完整旧推理。"}],
        "provided_source_context": {"version": 1, "scope": "learner_excerpt_only", "visibility": "learner_visible",
            "text_sha256": digest(excerpt), "text_chars": len(excerpt)}}
    original = deepcopy(record)
    evidence = revision_evidence(record, recipe, with_blocks=True, include_source_ids=True)
    assert evidence["teacher_evidence"] == full
    assert authenticated_teacher_text(record, evidence, recipe=recipe) == excerpt
    request = native_sft_optimization_input(record, recipe, evidence)
    assert request["source_text"] == excerpt and request["source_scope"] == "learner_excerpt_only"
    assert "HIDDEN" not in repr(request) and "完整旧推理" not in repr(request) and record == original
    for kind in ("source_hash", "actual_user", "unrouted"):
        candidate, proof = deepcopy(record), deepcopy(evidence)
        if kind == "source_hash":
            candidate["provided_source_context"]["text_sha256"] = digest(full)
        elif kind == "actual_user":
            candidate["messages"][0]["content"] = contract["question"]
        else:
            candidate["qa_contract"].pop("learning_goal_routing")
        with pytest.raises(ValueError, match="sft_reanswer_source_scope_changed"):
            authenticated_teacher_text(candidate, proof, recipe=recipe)


def test_reanswer_keeps_general_and_temporary_system_guidance_separate_from_reviewer_rules(sealed_native):
    root, output, rid, original = sealed_native
    app = workflow_application(root, output)
    general = "通用方法：先核对实际必要条件，再依据证据形成结论。"
    temporary = "本次必须核对断电这一前置条件。\n保留原资料范围和 {原问题}。"
    mapping = {original["id"]: temporary}
    parent_recipe_bytes = (engine.run_path(output, rid) / "recipe.json").read_bytes()
    child = app.reanswer_sft(rid, candidate_ids=[original["id"]],
        agent_experience={"reasoning": {"enabled": True, "text": general}}, task_instructions=mapping)
    mapping[original["id"]] = "修改调用者副本不会改变已冻结任务。"
    recipe = app.recipe(child)
    assert recipe["sft_reanswer_task_instructions_version"] == 1
    assert recipe["sft_reanswer_task_instructions"] == {original["id"]: temporary}
    assert recipe["agent_experience"]["scopes"]["reasoning"]["text"] == general
    assert recipe["agent_experience"]["scopes"]["review"] == app.recipe(rid)["agent_experience"]["scopes"]["review"]
    assert temporary not in json.dumps(recipe["agent_experience"], ensure_ascii=False)

    class CaptureClient:
        model = "author"
        reasoning = {"mode": "enabled"}
        usage = {}

        def __init__(self):
            self.calls = []

        def chat(self, messages, **options):
            self.calls.append(deepcopy(messages))
            if len(self.calls) == 1:
                options["response_details"].update(api_format="chat", reasoning_content="先确认实际前置条件，维护前必须断电。")
                return "维护前必须先断电。"
            return json.dumps({"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
                "scores": {name: 5 for name in ("correctness", "reasoning", "grounding", "instruction", "safety")},
                "reason": "回答保留来源的前置条件。"}, ensure_ascii=False)

    client = CaptureClient()
    worker = engine.Workflow(output, child, root, generator=client)
    state = worker.execute()
    assert state["status"] == "completed", state.get("error")
    assert len(client.calls) == 2 and worker.state["stages"]["director"]["status"] == "skipped"
    author_messages, review_messages = client.calls
    assert temporary in author_messages[0]["content"]
    assert all(m["role"] == "system" for m in author_messages if temporary in m.get("content", ""))
    assert any(m["role"] == "system" and general in m.get("content", "") for m in author_messages)
    assert [m for m in author_messages if m["role"] == "user"] == [m for m in original["messages"] if m["role"] == "user"]
    assert all(message["role"] != "assistant" for message in author_messages)
    assert original["messages"][-1]["content"] not in json.dumps(author_messages, ensure_ascii=False)
    assert original["messages"][-1]["reasoning_content"] not in json.dumps(author_messages, ensure_ascii=False)
    review_system = "\n".join(m.get("content", "") for m in review_messages if m["role"] == "system")
    assert general not in review_system and temporary not in review_system
    row, = engine.read_json(worker.path / "artifacts/sft.records.json")
    assert row["qa_contract"] == original["qa_contract"] and valid_reanswer_receipt(row)
    assert planned_sft_prompt(row, recipe) == planned_sft_prompt(original, app.recipe(rid))
    assert row["provided_source_context"]["text_sha256"] == original["provided_source_context"]["text_sha256"]
    assert row["sft_reanswer"]["task_instruction"]["text"] == temporary
    assert temporary not in json.dumps(row["messages"], ensure_ascii=False)
    assert (engine.run_path(output, rid) / "recipe.json").read_bytes() == parent_recipe_bytes
    assert engine.Workflow(output, child, root, generator=client).execute()["status"] == "completed"
    assert len(client.calls) == 2


@pytest.mark.parametrize("task_instructions", [
    {"unknown": "Only an explicitly selected candidate may receive instructions."},
    {"fixed": ""}, {"fixed": 7}, {"fixed": "invalid\x00instruction"},
])
def test_reanswer_rejects_invalid_temporary_instructions_before_creating_child(sealed_native, task_instructions):
    root, output, rid, original = sealed_native
    before = {path.name for path in (output / "workflows").iterdir()}
    with pytest.raises(ValueError, match="instruction"):
        workflow_application(root, output).reanswer_sft(rid, candidate_ids=[original["id"]],
            task_instructions=task_instructions)
    assert {path.name for path in (output / "workflows").iterdir()} == before


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
