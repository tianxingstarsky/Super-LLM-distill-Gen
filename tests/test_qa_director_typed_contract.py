"""Literal real failure shapes and bounded, source-strict director repair."""
from copy import deepcopy
from pathlib import Path
import json

import pytest

from lib.domain.source_line_ranges import number_source_material
from lib.domain.workflow_qa_director import validate_dialogue_design, validate_director_task
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_qa_director import _plan_validation_code, _typed_director_repair
from lib.prompts import get, render
from lib.prompts.workflow_agent_contracts import _BATCH_TASK_SCHEMA_V1_9
from tests.test_adaptive_dialogue_director import Expert, make_run
from tests.test_workflow_qa_director_engine import request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """All execution uses in-process doubles; no model or HTTP service."""
    yield


def test_complete_new_prompt_example_passes_actual_source_line_and_design_validators():
    task = deepcopy(_BATCH_TASK_SCHEMA_V1_9["tasks"][0])
    material = number_source_material({"id": "示例资料id", "text": "检查设备前必须先断电。"})
    validated = validate_director_task(task, expected_type=None, expected_id="示例候选id",
        source_text=material["text"], source_materials=[material])
    assert validated["dialogue_design"]["turn_guidance"] == task["dialogue_design"]["turn_guidance"]
    assert validated["visible_context"] == material["text"]
    assert task["source_line_ranges"] == [{"material_id": "示例资料id", "start_line": 1, "end_line": 1}]
    body = render(get("workflow.qa_director"))
    assert get("workflow.qa_director").version == "1.10.0"
    assert json.dumps(_BATCH_TASK_SCHEMA_V1_9, ensure_ascii=False) in body
    assert "context_links、success_criteria、turn_guidance是三个可选的字符串数组字段" in body


def test_six_literal_real_responses_reproduce_text_instead_of_array_failure():
    fixture = json.loads((Path(__file__).parent / "fixtures/qa_director_turn_guidance_real.json")
                         .read_text(encoding="utf-8"))
    assert len(fixture["excerpts"]) == 6
    assert [item["task_count"] for item in fixture["excerpts"]] == [7, 7, 7, 3, 3, 3]
    for excerpt in fixture["excerpts"]:
        original = excerpt["dialogue_design"]
        assert isinstance(original["turn_guidance"], str)
        with pytest.raises(ValueError, match="^invalid_dialogue_design$"):
            validate_dialogue_design(original)
        # Only a diagnostic fixture projection. Production never silently
        # normalizes the rejected string or bypasses its independent review.
        diagnostic = deepcopy(original)
        diagnostic["turn_guidance"] = [diagnostic["turn_guidance"]]
        assert validate_dialogue_design(diagnostic)["turn_guidance"] == diagnostic["turn_guidance"]
        assert isinstance(original["turn_guidance"], str)


@pytest.mark.parametrize("field", ["context_links", "success_criteria", "turn_guidance"])
@pytest.mark.parametrize("bad", ["one direction", None, {"direction": "one"}, ["x" * 1001]])
def test_contract_keeps_array_and_item_bounds_strict(field, bad):
    task = deepcopy(_BATCH_TASK_SCHEMA_V1_9["tasks"][0])
    task["dialogue_design"][field] = bad
    material = number_source_material({"id": "示例资料id", "text": "检查设备前必须先断电。"})
    with pytest.raises(ValueError, match="^invalid_dialogue_design$"):
        validate_director_task(task, expected_type=None, source_text=material["text"], source_materials=[material])


@pytest.mark.parametrize("original,rewritten", [
    ("single user-mode", "single-user-mode"),
    ("Section\u00a019.5.5", "Section 19.5.5"),
])
def test_typing_fix_does_not_normalize_real_failed_literal_quote_variants(original, rewritten):
    task = {"id": "candidate", "qa_type": "closed_book", "question": "Explain the requirement.",
            "visible_context": "", "answer_policy": "answer", "guidance": "Keep actual source conditions.",
            "evidence_quotes": [rewritten], "dialogue_design": {"interaction_goal": "Understand the requirement.",
                                                                     "turn_guidance": []}}
    with pytest.raises(ValueError, match="^qa_director_evidence_not_in_source$"):
        validate_director_task(task, expected_type=None, source_text=original)
    task["evidence_quotes"] = [original]
    assert validate_director_task(task, expected_type=None, source_text=original)["evidence_quotes"] == [original]


class RepairExpert(Expert):
    def __init__(self, *, always_invalid=False):
        super().__init__()
        self.always_invalid = always_invalid
        self.batches = 0

    def chat(self, messages, **kwargs):
        data = request_data(messages)
        result = json.loads(super().chat(messages, **kwargs))
        if data.get("request_phase") == "batch_design":
            self.batches += 1
            if self.batches == 1 or self.always_invalid:
                result["tasks"][0]["dialogue_design"]["turn_guidance"] = "PRIVATE_REJECTED_TEXT_DO_NOT_ECHO"
        return json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("historical", [False, True])
def test_repair_pinpoints_new_type_error_without_private_text_or_old_request_drift(tmp_path, monkeypatch, historical):
    expert = RepairExpert()
    output, run_id, path, expert, worker, reviewer, _ = make_run(tmp_path, monkeypatch, expert)
    if historical:
        recipe = engine.read_json(path / "recipe.json")
        old = get("workflow.qa_director", version="1.8.0")
        recipe["prompts"][old.id] = {"version": old.version, "sha256": engine.digest(old.template)}
        recipe["node_prompt_templates"]["director"][old.id] = render(old)
        state = engine.read_json(path / "state.json")
        state["recipe_hash"] = engine.digest(recipe)
        engine.atomic_json(path / "recipe.json", recipe)
        engine.atomic_json(path / "state.json", state)
    state = engine.Workflow(output, run_id, tmp_path, jev=reviewer).execute()
    assert state["status"] == "completed", state.get("error")
    requests = [request_data(call) for call in expert.calls]
    batches = [data for data in requests if data.get("request_phase") == "batch_design"]
    assert len(batches) == 2 and len(worker.calls) == 2
    repair = batches[1]["plan_repair"]
    assert "PRIVATE_REJECTED_TEXT_DO_NOT_ECHO" not in json.dumps(repair, ensure_ascii=False)
    if historical:
        assert repair["validation_code"] == "qa_director_contract_validation_failed"
        assert "尤其turn_guidance" not in repair["instruction"]
    else:
        assert repair["validation_code"] == "invalid_dialogue_design"
        assert "context_links、success_criteria、turn_guidance" in repair["instruction"]
        assert "不能是单个字符串" in repair["instruction"]
    assert engine.read_json(path / "artifacts/multiturn.records.json")[0]["status"] == "eligible"
    assert len((path / "artifacts/multiturn.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert reviewer.calls  # A repaired plan still completes actual quality and final packaging checks.


def test_three_invalid_design_attempts_quarantine_without_worker_or_publication(tmp_path, monkeypatch):
    expert = RepairExpert(always_invalid=True)
    output, run_id, path, expert, worker, reviewer, _ = make_run(tmp_path, monkeypatch, expert)
    state = engine.Workflow(output, run_id, tmp_path, jev=reviewer).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert expert.batches == 3 and not worker.calls
    record = engine.read_json(path / "artifacts/multiturn.records.json")[0]
    assert record["status"] == "quarantined"
    assert record["reason"] == "qa_director_plan_invalid_after_repair"
    assert not (path / "artifacts/multiturn.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize("pins,expected", [
    (None, False), ({}, False), ({"workflow.qa_director": {"version": "1.8.0"}}, False),
    ({"workflow.qa_director": {"version": "1.9.0"}}, True),
    ({"workflow.qa_director": {"version": "1.10.0"}}, True),
])
def test_only_saved_new_contract_versions_opt_into_typed_repair(pins, expected):
    assert _typed_director_repair({"prompts": pins}) is expected
    assert _plan_validation_code(ValueError("invalid_dialogue_design")) == "invalid_dialogue_design"
    assert _plan_validation_code(ValueError("private arbitrary model string")) == "qa_director_contract_validation_failed"
