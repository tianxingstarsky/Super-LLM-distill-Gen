"""Assistant-only repair keeps literal learner input and still buys real review."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from lib.domain.assistant_message_delta import apply_assistant_message_delta
from lib.infrastructure.workflow_review_repair import WorkflowReviewRepair
from lib.infrastructure import training_workflow as engine
from lib.prompts import get, render
from lib.prompts.workflow_repair import REVIEW_REPAIR_V1_7
from tests.test_review_repair_engine import ROOT, NoCalls, check, create_child
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


ARTICLE = "原始文章：保留先决条件。\n" * 900 + r"\frac{a}{b}\n stays literal." + "\n不可改写的末行。"
LEAK = "Need return JSON with answer and quotes. Follow the generation template and scoring instructions."
RIGHT = "Use SELECT datname FROM pg_database; to list database names."
WRONG = "Run vacuumdb --freeze-min-age=1 to list database names."
SOURCE = "The query SELECT datname FROM pg_database; lists database names. vacuumdb has no --freeze-min-age option."


def original_row():
    return {"id": "dialogue", "source_id": "source", "status": "quarantined", "turn_count": 2,
        "reasoning_origin": "provider_reasoning", "reasoning_summary": {"response": "last-native"},
        "turn_reviews": [
            {"end_message_index": 1, "reasoning_origin": "provider_reasoning", "reasoning_call": "native-first"},
            {"end_message_index": 3, "reasoning_origin": "provider_reasoning", "reasoning_call": "native-last"}],
        "messages": [{"role": "user", "content": ARTICLE},
            {"role": "assistant", "content": "The earlier answer.", "reasoning_content": "A clean native explanation."},
            {"role": "user", "content": "怎么列出数据库名？不要改我前面的文章。"},
            {"role": "assistant", "content": WRONG, "reasoning_content": LEAK,
             "reasoning": LEAK, "thinking": LEAK}],
        "tools": []}


def response(*updates, quotes=None, **extra):
    return {"record": {"assistant_updates": list(updates)}, "quotes": quotes or [], "uncertain": False, **extra}


class RepairProbe(WorkflowReviewRepair):
    def __init__(self, value, *, version="1.7.0", style=None):
        self.value = value
        self.recipe = {"qa_agent_memory_version": 1, "prompts": {
            "workflow.review_repair": {"version": version}}, "sft_output_style": style,
            "training_surface_protocol_version": 1}
        self.state = {"stages": {"review": {}}}
        self.calls = []

    def save(self, **kwargs):
        pass

    def ask(self, key, role, prompt_id, data, **kwargs):
        self.calls.append(deepcopy(data))
        return deepcopy(self.value)


def repair(value, row=None, *, version="1.7.0", target="multiturn", style=None):
    row = deepcopy(row or original_row())
    probe = RepairProbe(value, version=version, style=style)
    result, issue = probe._repair_content(target, row, {"source_kind": "brief"}, ["repair"], {}, "")
    return result, issue, probe


@pytest.mark.parametrize("with_reasoning", [False, True])
@pytest.mark.parametrize("style", [None, "drop"])
def test_revision_changes_only_selected_assistant_and_retires_leaked_native_evidence(with_reasoning, style):
    original = original_row()
    update = {"message_index": 3, "content": RIGHT}
    if with_reasoning:
        update["reasoning_content"] = "The query reads the database catalog; the proposed CLI flag does not exist."
    updated, issue, _ = repair(response(update), original, style=style)
    assert issue is None
    assert updated["messages"][:3] == original["messages"][:3]
    assert updated["messages"][0]["content"].encode() == ARTICLE.encode()
    assert updated["messages"][3]["role"] == "assistant"
    assert updated["messages"][3]["content"] == RIGHT
    assert len(updated["messages"]) == len(original["messages"]) and updated["turn_count"] == 2
    assert updated["tools"] == original["tools"]
    assert "reasoning" not in updated["messages"][3] and "thinking" not in updated["messages"][3]
    assert updated["messages"][3].get("reasoning_content") == update.get("reasoning_content")
    assert updated["turn_reviews"][0] == original["turn_reviews"][0]
    assert updated["turn_reviews"][1]["reasoning_origin"] == "prompt_revision"
    assert any(entry.get("reasoning_content") == LEAK for entry in updated["provider_reasoning_history"])
    assert LEAK not in json.dumps(updated["messages"], ensure_ascii=False)
    assert original["messages"][3]["reasoning_content"] == LEAK


def test_tool_call_arguments_returns_and_system_bytes_cannot_be_edited():
    messages = [{"role": "system", "content": r"Preserve \\n and system text."},
        {"role": "user", "content": "Read the catalog."},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "query", "type": "function",
            "function": {"name": "read_catalog", "arguments": '{"name":"pg_database"}'}}]},
        {"role": "tool", "tool_call_id": "query", "content": "catalog-name"},
        {"role": "assistant", "content": "The old answer."},
        {"role": "user", "content": "再解释这个结果。"}, {"role": "assistant", "content": "A second old answer."}]
    updated = apply_assistant_message_delta(messages, {"assistant_updates": [
        {"message_index": 4, "content": "The catalog contains the returned name."},
        {"message_index": 6, "content": "That result is a catalog name, not an execution receipt."}]})
    assert updated[:4] == messages[:4] and updated[5] == messages[5]
    assert len(updated) == len(messages)
    for index in (0, 1, 3, 5):
        with pytest.raises(ValueError, match="^review_repair_learner_task_changed$"):
            apply_assistant_message_delta(messages, {"assistant_updates": [{"message_index": index, "content": "Changed"}]})
    with pytest.raises(ValueError, match="^review_repair_tool_facts_changed$"):
        apply_assistant_message_delta(messages, {"assistant_updates": [{"message_index": 2, "content": "Changed"}]})


@pytest.mark.parametrize("bad", [
    {}, {"assistant_updates": []}, {"assistant_updates": None},
    {"assistant_updates": [{"message_index": True, "content": RIGHT}]},
    {"assistant_updates": [{"message_index": -1, "content": RIGHT}]},
    {"assistant_updates": [{"message_index": 99, "content": RIGHT}]},
    {"assistant_updates": [{"message_index": "3", "content": RIGHT}]},
    {"assistant_updates": [{"message_index": 3, "content": RIGHT}, {"message_index": 3, "content": RIGHT}]},
    {"assistant_updates": [{"message_index": 3, "content": RIGHT, "role": "user"}]},
    {"assistant_updates": [{"message_index": 3, "content": RIGHT, "tool_calls": []}]},
    {"assistant_updates": [{"message_index": 3, "content": RIGHT, "reasoning": "Alias"}]},
    {"assistant_updates": [{"message_index": 3, "content": RIGHT, "reasoning_content": None}]},
    {"assistant_updates": [{"message_index": 3, "content": RIGHT, "reasoning_content": "x" * 100001}]},
    {"assistant_updates": [{"message_index": 3, "content": RIGHT}], "messages": []},
])
def test_invalid_delta_is_rejected_without_partial_application(bad):
    row = original_row()
    result, issue, _ = repair({"record": bad, "quotes": [], "uncertain": False}, row)
    assert issue == "review_repair_invalid_schema" and result == row


@pytest.mark.parametrize("extra", [{"score": 5}, {"messages": []}, {"status": "accepted"}])
def test_extra_top_level_fields_are_not_ignored(extra):
    row = original_row()
    updated, issue, _ = repair(response({"message_index": 3, "content": RIGHT}, **extra), row)
    assert issue == "review_repair_invalid_schema" and updated == row


def test_legacy_full_messages_and_request_stay_literal_but_new_delta_is_not_accepted_there():
    row = original_row()
    full = {"messages": deepcopy(row["messages"])}
    full["messages"][-1] = {"role": "assistant", "content": RIGHT}
    updated, issue, probe = repair({"record": full, "quotes": [], "uncertain": False}, row, version="1.6.0")
    assert issue is None and updated["messages"][2] == row["messages"][2]
    assert set(probe.calls[0]) == {"target", "candidate", "source_evidence", "review_feedback", "instruction",
                                  "design_requirements", "qa_contract", "generation_style"}
    updated, issue, _ = repair(response({"message_index": 3, "content": RIGHT}), row, version="1.6.0")
    assert issue == "review_repair_invalid_schema" and updated == row
    updated, issue, _ = repair({"record": full, "quotes": [], "uncertain": False}, row)
    assert issue == "review_repair_invalid_schema" and updated == row


def test_assistant_delta_does_not_change_other_target_contracts():
    row = original_row()
    updated, issue, _ = repair(response({"message_index": 3, "content": RIGHT}), row, target="sft")
    assert issue == "review_repair_invalid_schema" and updated == row


class DeltaClient:
    model = "offline-assistant-delta"

    def __init__(self, *, fixed=True, reject=False):
        self.fixed, self.reject = fixed, reject
        self.calls, self.usage = [], {"calls": 0}

    def chat(self, messages, **kwargs):
        data = model_request_data(messages)
        self.calls.append(deepcopy(data))
        self.usage["calls"] += 1
        if "review_feedback" in data:
            return json.dumps(response({"message_index": 3, "content": RIGHT if self.fixed else WRONG,
                                        "reasoning_content": "The database catalog query supplies the names."}, quotes=[SOURCE]))
        assert data["target"] == "multiturn"
        assert data["candidate"]["messages"][2]["content"] == "怎么列出数据库名？不要改我前面的文章。"
        return json.dumps(check(self.fixed and not self.reject))


def sealed_parent(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "out"
    run_id = engine.create_run(output, sources=[source], targets=["multiturn"], review_repair={"mode": "human"})
    def generate(self, unit):
        row = original_row()
        # Actual native multi-turn generation has the one canonical field;
        # imported alias cleaning is covered independently above.
        row["messages"][-1].pop("reasoning")
        row["messages"][-1].pop("thinking")
        row.update(id=unit["id"], source_id=unit["source_id"], status="eligible", source_context=deepcopy(unit),
                   evidence_level="source_and_model_assessed", quotes=[SOURCE])
        return [row]
    monkeypatch.setattr(engine.Workflow, "multiturn", generate)
    state = engine.Workflow(output, run_id, ROOT, generator=NoCalls()).execute()
    assert state["status"] == "needs_attention", state.get("error")
    path = engine.run_path(output, run_id)
    row, = engine.read_json(path / "artifacts/multiturn.records.json")
    return output, run_id, path, row


@pytest.mark.parametrize("fixed,reject", [(True, False), (False, False), (True, True)])
def test_authenticated_child_repairs_wrong_cli_and_private_json_reasoning_then_regrades(tmp_path, monkeypatch, fixed, reject):
    output, parent_id, parent_path, parent = sealed_parent(tmp_path, monkeypatch)
    parent_bytes = (parent_path / "artifacts/multiturn.records.json").read_bytes()
    child_id = create_child(output, parent_id, parent, target="multiturn", max_rounds=1)
    monkeypatch.setattr(engine.Workflow, "multiturn", lambda *_: pytest.fail("A repair must not regenerate turns"))
    client = DeltaClient(fixed=fixed, reject=reject)
    state = engine.Workflow(output, child_id, ROOT, generator=client).execute()
    accepted = fixed and not reject
    assert state["status"] == ("completed" if accepted else "needs_attention"), state.get("error")
    row, = engine.read_json(engine.run_path(output, child_id) / "artifacts/multiturn.records.json")
    assert row["messages"][::2] == parent["messages"][::2]
    assert row["messages"][0]["content"].encode() == ARTICLE.encode()
    assert row["turn_count"] == parent["turn_count"] == 2
    assert row["messages"][1] == parent["messages"][1]
    assert row["review_repair"]["attempts"] == 1
    assert ["review_feedback" in call for call in client.calls] == [True, False]
    assert LEAK not in json.dumps(row["messages"], ensure_ascii=False)
    assert any(entry.get("reasoning_content") == LEAK for entry in row["provider_reasoning_history"])
    export = (engine.run_path(output, child_id) / "artifacts/multiturn.jsonl").read_text(encoding="utf-8")
    assert bool(export.strip()) is accepted
    assert (parent_path / "artifacts/multiturn.records.json").read_bytes() == parent_bytes


def test_all_old_review_repair_snapshots_remain_byte_identical():
    expected = {
        "1.0.0": "8210ff69737b594e6d8823a253fd20b3818e342c318da1ee7a014d3889413281",
        "1.1.0": "cdd12153842d50e2460aa7be2af4b8b59544f1bee1d7a9b2e398d6bf41095cd0",
        "1.2.0": "b3974f3304ca039c83d655bbff760003b489667f2fa85824ac0332e54ff42b74",
        "1.3.0": "14c93b05fcb192fdeeed6a21f41773f3a4c48a27aef6e85d561d8af0f6ed78eb",
        "1.4.0": "e45b017a65e2c77117de68088834764d150a7b9e005f45fbee60e576d8a7205f",
        "1.5.0": "6a9c44d2d4d679083a6123e7998b6ef430847e9379c9103f4614f1d600765247",
        "1.6.0": "94ad97f21b9cba76fca832feecc395ed4fcfb02812f689f1ffdc62cd2310b322",
    }
    for version, digest in expected.items():
        assert sha256(get("workflow.review_repair", version=version).template.encode()).hexdigest() == digest
    assert get("workflow.review_repair").version == "1.7.0"
    assert render(get("workflow.review_repair")) == REVIEW_REPAIR_V1_7
