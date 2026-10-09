"""Bounded planner repair isolates invalid production batches only."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from lib.domain.workflow_qa_director import validate_qa_director
from lib.infrastructure.workflow_qa_director import WorkflowQADirector, DirectorPlanError
from lib.model_request_reliability import ModelRequestError


SOURCE = "检查前先断电。"
UNIT = {"id": "candidate-1", "source_id": "source-1", "kind": "document", "text": SOURCE}
VALID = {"tasks": [{"id": "candidate-1", "qa_type": "closed_book", "question": "检查前先做什么？",
    "visible_context": "", "answer_policy": "answer", "guidance": "依据来源回答。",
    "evidence_quotes": [SOURCE]}]}


class Planner(WorkflowQADirector):
    def __init__(self, tmp_path, responses, *, production=True):
        self.path = tmp_path
        self.recipe = {"version": 12 if production else 11, "node_prompt_system": "Return JSON"}
        if production:
            self.recipe["production"] = {}
        self.config = validate_qa_director({"enabled": True, "batch_size": 1, "history_limit": 0,
            "type_weights": {"closed_book": 100, "grounded": 0, "partial": 0,
                             "multi_source": 0, "distractor": 0}})
        self.state = {"stages": {"director": {"done": 0, "outputs": 0, "quarantined": 0,
            "eligible": 0, "batches_done": 0}}, "qa_director": {"coverage": {"closed_book": {
                "planned": 0, "assigned": 0}}, "feedback": {}, "batches_planned": 0}}
        self.responses, self.calls, self.events, self.invalidated = iter(responses), [], [], []
        self.values = {}
        self.stage = "director"

    def activate_directed_stage(self, stage):
        self.stage = stage

    def save(self):
        pass

    def event(self, kind, **fields):
        self.events.append({"kind": kind, **fields})

    def prompt_text(self, prompt_id):
        return "Plan contracts"

    def checkpoint(self, key, action):
        identity = repr(key)
        if identity not in self.values:
            self.values[identity] = deepcopy(action())
        return deepcopy(self.values[identity])

    def invalidate_checkpoint(self, key, *, stage=None):
        self.invalidated.append((key, stage))
        self.values.pop(repr(key), None)

    def ask(self, key, role, prompt, data, **kwargs):
        self.calls.append(deepcopy(data))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return self.checkpoint(["call", key], lambda: value)

    def run_plan(self, batch=None):
        history = SimpleNamespace(similar=lambda *a, **kw: [])
        return self._director_batch_plan(batch or [UNIT], 0, self.config, history, history, ["sft"])


def test_invalid_plan_gets_safe_repair_feedback_and_is_cached_after_success(tmp_path):
    planner = Planner(tmp_path, [{"tasks": []}, VALID])
    result = planner.run_plan()
    assert result[0]["qa_contract"]["question"] == VALID["tasks"][0]["question"]
    assert len(planner.calls) == 2
    assert "plan_repair" not in planner.calls[0]
    assert planner.calls[1]["plan_repair"]["code"] == "qa_director_contract_validation_failed"
    assert planner.invalidated[0][1] == "director"
    planner.run_plan()
    assert len(planner.calls) == 2  # contracts and inputs survive resume


def test_three_invalid_plans_quarantine_only_guided_units(tmp_path):
    planner = Planner(tmp_path, [{"tasks": []}] * 3)
    recorded = {"id": "recorded", "kind": "conversation", "source_id": "source-2"}
    result = planner.run_plan([UNIT, recorded])
    assert len(planner.calls) == 3
    assert result[0]["director_plan_failure"] == DirectorPlanError.code
    assert result[1] == recorded
    assert "qa_contract" not in result[0]
    assert planner.state["stages"]["director"]["quarantined"] == 1
    assert planner.events[-1]["kind"] == "director_batch_quarantined"
    assert len(planner.invalidated) == 3


def test_unhashable_model_id_is_schema_failure_not_a_task_crash(tmp_path):
    bad = deepcopy(VALID)
    bad["tasks"][0]["id"] = ["bad"]
    planner = Planner(tmp_path, [bad, VALID])
    assert planner.run_plan()[0]["qa_contract"]["id"] == "candidate-1"


def test_provider_authentication_is_not_swallowed_as_invalid_plan(tmp_path):
    planner = Planner(tmp_path, [ModelRequestError("service_authentication_failed", "fatal", 1)])
    with pytest.raises(ModelRequestError):
        planner.run_plan()
    assert len(planner.calls) == 1
    assert planner.events == []


def test_old_run_keeps_validation_error_and_no_extra_paid_repair_calls(tmp_path):
    planner = Planner(tmp_path, [{"tasks": []}], production=False)
    with pytest.raises(ValueError, match="invalid_qa_director_batch"):
        planner.run_plan()
    assert len(planner.calls) == 1
