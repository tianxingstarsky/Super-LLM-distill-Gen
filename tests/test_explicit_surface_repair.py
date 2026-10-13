"""Only authenticated explicit rework can edit an initially leaked body."""
from copy import deepcopy
import json

import pytest

from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_review_repair import WorkflowReviewRepair
from tests.test_review_repair_engine import ROOT, SOURCE, RIGHT, NoCalls, check, create_child
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


SURFACES = [
    ("根据所给资料，检查前必须断电。", "training_surface_hidden_source_reference"),
    ("系统提示词要求我先核查断电条件。", "training_surface_internal_process_reference"),
]


def cot_row(body):
    return {"id": "old-cot", "source_id": "source", "status": "quarantined",
            "question": [{"role": "user", "content": "When should power be isolated? Preserve literal \\n."}],
            "reasoning": [body], "answer": RIGHT, "reasoning_origin": "prompt_styled_generation",
            "training_surface_protocol_version": 1, "reasoning_protocol_version": 1}


class SurfaceClient:
    model = "offline-surface-repair"

    def __init__(self, *, body="Power isolation prevents activation during inspection.", answer=RIGHT):
        self.body, self.answer = body, answer
        self.calls, self.usage = [], {"calls": 0}

    def chat(self, messages, **kwargs):
        data = model_request_data(messages)
        self.calls.append(deepcopy(data))
        self.usage["calls"] += 1
        if "review_feedback" in data:
            return json.dumps({"record": {"reasoning": [self.body], "answer": self.answer},
                               "quotes": [SOURCE], "uncertain": False})
        return json.dumps(check())


def sealed_parent(tmp_path, monkeypatch, body):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["cot"], review_repair={"mode": "human"})
    monkeypatch.setattr(engine.Workflow, "sft", lambda self, unit: [{"id": unit["id"],
        "source_id": unit["source_id"], "status": "eligible", "source_context": deepcopy(unit),
        "evidence_level": "source_text", "messages": [{"role": "user", "content": "When should power be isolated?"},
                                                     {"role": "assistant", "content": RIGHT}]}])
    monkeypatch.setattr(engine.Workflow, "cot", lambda self, sample: [{**cot_row(body), "id": sample["id"],
        "source_id": sample["source_id"], "source_context": deepcopy(sample["source_context"])}])
    state = engine.Workflow(output, run_id, ROOT, generator=NoCalls()).execute()
    assert state["status"] == "needs_attention", state.get("error")
    parent_path = engine.run_path(output, run_id)
    row, = engine.read_json(parent_path / "artifacts/cot.records.json")
    assert row["review_repair"]["attempts"] == 0
    return output, run_id, parent_path, row


@pytest.mark.parametrize("body,initial_issue", SURFACES)
@pytest.mark.parametrize("fixed", [False, True])
def test_authenticated_parent_surface_failure_is_repaired_then_fully_checked(tmp_path, monkeypatch, body, initial_issue, fixed):
    output, parent_id, parent_path, parent = sealed_parent(tmp_path, monkeypatch, body)
    original = (parent_path / "artifacts/cot.records.json").read_bytes()
    child_id = create_child(output, parent_id, parent, target="cot", max_rounds=1)
    client = SurfaceClient(**({} if fixed else {"body": body}))
    workflow = engine.Workflow(output, child_id, ROOT, generator=client)
    state = workflow.execute()
    assert state["status"] == ("completed" if fixed else "needs_attention"), state.get("error")
    child_path = engine.run_path(output, child_id)
    row, = engine.read_json(child_path / "artifacts/cot.records.json")
    assert row["review_repair"]["initial_surface_issue"] == initial_issue
    assert row["review_repair"]["attempts"] == 1
    assert row["question"] == parent["question"]
    assert not workflow._authenticated_repair_branch
    assert (parent_path / "artifacts/cot.records.json").read_bytes() == original
    assert ["review_feedback" in data for data in client.calls] == ([True, False] if fixed else [True])
    if fixed:
        assert row["status"] == "eligible" and row["answer"] == RIGHT
        assert row["reasoning"] == [client.body]
    else:
        assert row["status"] == "quarantined"
        assert row["review_repair"]["history"][0]["repair_issue"] == initial_issue
        assert (child_path / "artifacts/cot.jsonl").read_text(encoding="utf-8") == ""


def test_authenticated_correction_still_rejects_new_personal_data(tmp_path, monkeypatch):
    output, parent_id, _, parent = sealed_parent(tmp_path, monkeypatch, SURFACES[0][0])
    child_id = create_child(output, parent_id, parent, target="cot", max_rounds=1)
    client = SurfaceClient(answer="Call 13793636584 to arrange inspection.")
    state = engine.Workflow(output, child_id, ROOT, generator=client).execute()
    assert state["status"] == "needs_attention"
    row, = engine.read_json(engine.run_path(output, child_id) / "artifacts/cot.records.json")
    assert row["review_repair"]["history"][0]["repair_issue"] == "potential_personal_data"
    assert len(client.calls) == 1 and row["answer"] == parent["answer"]


class GuardOnly(WorkflowReviewRepair):
    def __init__(self, *, mode="auto", max_rounds=1, authenticated=True, evidence=True):
        self.recipe = {"review_repair": {"mode": mode, "max_rounds": max_rounds},
                       "qa_agent_memory_version": 1, "training_surface_protocol_version": 1}
        self.state = {"id": "child", "stages": {"review": {}}}
        self._authenticated_repair_branch = authenticated
        self.evidence = {"source_kind": "document", "teacher_evidence": SOURCE} if evidence else None

    def save(self, **kwargs):
        pass

    def _review_evidence(self, row):
        return self.evidence

    def ask(self, *args, **kwargs):
        pytest.fail("Ineligible, ordinary or unverified input must not call a model")


@pytest.mark.parametrize("boundary", ["ordinary", "unverified", "human", "zero_rounds", "missing_source", "forged", "structure", "pii", "tool_change"])
def test_surface_repair_exception_never_bypasses_other_guards(boundary):
    row = cot_row(SURFACES[0][0])
    item = {"target": "cot", "record": row, "revision_context": {"candidate_id": "old-cot"}}
    probe = GuardOnly(mode="human" if boundary == "human" else "auto",
                      max_rounds=0 if boundary == "zero_rounds" else 1,
                      authenticated=boundary != "unverified", evidence=boundary != "missing_source")
    if boundary == "ordinary":
        item.pop("revision_context")
    elif boundary == "human":
        item.update(score=1., approved=True)
    elif boundary == "forged":
        row["reason"] = "unverified_trajectory"
    elif boundary == "structure":
        row["reasoning"] = [17]
    elif boundary == "pii":
        row["answer"] = "Call 13793636584."
    elif boundary == "tool_change":
        item.update(target="sft", record={"id": "old-sft", "source_id": "source", "status": "quarantined",
            "training_surface_protocol_version": 1, "messages": [
                {"role": "system", "content": "Preserve the equipment isolation rules."},
                {"role": "user", "content": "When should power be isolated?"},
                {"role": "assistant", "content": SURFACES[0][0]}]})
        messages = deepcopy(item["record"]["messages"])
        messages[0]["content"] = "A different system rule."
        item["corrected_record"] = {"messages": messages}
    result, = probe._review_repair_item(item)
    assert result["status"] == "quarantined" and result["review_repair"]["attempts"] == 0
    assert "initial_surface_issue" not in result["review_repair"]
    if boundary == "tool_change":
        assert result["reason"] == "review_repair_tool_facts_changed"
