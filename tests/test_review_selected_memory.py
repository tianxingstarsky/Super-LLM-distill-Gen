"""Review and repair consume authenticated selected blocks through real memory."""
from copy import deepcopy
import json
from pathlib import Path
import uuid

import pytest

from lib.domain.source_line_ranges import resolve_source_line_ranges
from lib.domain.source_quote import quote_spans
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.human_revision import record_messages, revision_evidence
from lib.infrastructure.workflow_rows import WorkflowRows, write_jsonl
from lib.infrastructure.workflow_source_context import load_context_pack, prepare_source_context_packs
from lib.io_utils import atomic_json


ROOT = Path(__file__).resolve().parents[1]
OUTSIDE_BEFORE = "FULL_MEMORY_OUTSIDE_HEADER"
OUTSIDE_AFTER = "FULL_MEMORY_OUTSIDE_FOOTER"
POWER_RULE = "Turn off the power before inspection."
RESTART_RULE = "Keep the isolation switch open until the supervisor approves restart."
RECORD_RULE = "Record the result only after inspection."
SOURCE = f"{OUTSIDE_BEFORE}\n{POWER_RULE}\r\n{RESTART_RULE}\n{RECORD_RULE}\n{OUTSIDE_AFTER}"
WRONG = "Inspect with the power on."
RIGHT = "Disconnect power before starting the inspection."


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """The test clients below are in memory; no API or local TCP is used."""
    yield


def verdict(keep=False):
    score = 5 if keep else 1
    return {"keep": keep, "grounded": keep, "reasoning_valid": keep, "correctness": score,
            "scores": {name: score for name in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "The power-off condition is satisfied." if keep else "The power-off prerequisite is reversed."}


def request_data(messages):
    return json.loads(next(message["content"] for message in reversed(messages) if message["role"] == "user"))


def tool_packs(messages):
    return [pack for message in messages if message["role"] == "tool"
            for pack in json.loads(message["content"])["teacher_context_pack"]]


class ReviewClient:
    model = "offline-selected-review"

    def __init__(self, quotes, *, accept_repaired=False):
        self.quotes = quotes
        self.accept_repaired = accept_repaired
        self.calls, self.message_history, self.usage = [], [], {"calls": 0}

    def chat(self, messages, **kwargs):
        data = request_data(messages)
        self.calls.append(deepcopy(data))
        self.message_history.append(deepcopy(messages))
        self.usage["calls"] += 1
        assert "source_evidence" in data
        if "review_feedback" in data:
            payload = deepcopy(data["candidate"])
            payload["messages"][-1]["content"] = RIGHT
            return json.dumps({"record": payload, "quotes": self.quotes, "uncertain": False})
        return json.dumps(verdict(self.accept_repaired and data["candidate"]["messages"][-1]["content"] == RIGHT))


def review_fixture(tmp_path, *, selections=((2, 3),), numbered=True, quotes=None):
    source = tmp_path / "manual.txt"
    with source.open("w", encoding="utf-8", newline="") as handle:
        handle.write(SOURCE)
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"],
                               review_repair={"mode": "auto", "max_rounds": 1})
    client = ReviewClient([POWER_RULE] if quotes is None else quotes)
    workflow = engine.Workflow(output, run_id, ROOT, generator=client, judge=client)
    if not numbered:
        workflow.recipe.pop("source_line_protocol_version", None)
        workflow.recipe.pop("source_memory_protocol_version", None)
    source_id = workflow.recipe["sources"][0]["sha256"]
    identity = engine.digest([source_id, "accepted-unit"])
    unit = {"id": identity, "source_id": source_id, "kind": "document", "status": "ready",
            "text": SOURCE, "source_cleaning": {"status": "accepted", "cleaned_text_sha256": engine.digest(SOURCE)}}
    clean_path = workflow.path / "accepted-sources.jsonl"
    write_jsonl(clean_path, iter([unit]))
    prepared = next(iter(prepare_source_context_packs(workflow, WorkflowRows(clean_path, 1))))
    pack = load_context_pack(workflow.path, prepared["source_context_pack"])
    ranges = [{"material_id": identity, "start_line": start, "end_line": end} for start, end in selections]
    row = {"id": engine.digest([identity, "candidate"]), "source_id": source_id, "status": "eligible",
           "messages": [{"role": "user", "content": "Which precaution must precede equipment inspection?"},
                        {"role": "assistant", "content": WRONG}],
           "source_context": prepared,
           "qa_contract": {"qa_type": "closedbook", "answer_policy": "answer", "source_line_ranges": ranges},
           "quotes": [POWER_RULE], "evidence_level": "source_and_model_assessed"}
    workflow.stage = "review"
    blocks = ([item["text"] for item in resolve_source_line_ranges(pack["materials"], ranges)]
              if numbered else [item["text"] for item in pack["materials"]])
    return workflow, row, pack, blocks, client


def assert_selected_tool(messages, ranges, blocks):
    packs = tool_packs(messages)
    assert len(packs) == 1
    assert packs[0]["source_line_scope"] == "selected"
    materials = packs[0]["materials"]
    assert [material["text"] for material in materials] == blocks
    assert [{key: material[key] for key in ("material_id", "start_line", "end_line")}
            for material in materials] == ranges
    assert all("numbered_text" not in material for material in materials)
    tool_call = next(message["tool_calls"][0] for message in messages if message.get("tool_calls"))
    arguments = json.loads(tool_call["function"]["arguments"])
    assert arguments["scope"] == "selected" and arguments["ranges"] == ranges
    body = json.dumps(packs, ensure_ascii=False)
    assert OUTSIDE_BEFORE not in body and OUTSIDE_AFTER not in body
    task = request_data(messages)
    assert task["source_line_scope"] == "selected" and task["source_line_ranges"] == ranges
    assert "teacher_evidence" not in task["source_evidence"]
    assert "teacher_evidence_blocks" not in task["source_evidence"]
    task_body = json.dumps(task, ensure_ascii=False)
    assert all(block.strip() not in task_body for block in blocks)
    assert OUTSIDE_BEFORE not in task_body and OUTSIDE_AFTER not in task_body


def test_revision_evidence_slices_verified_raw_lines_and_preserves_context_schema(tmp_path):
    workflow, row, pack, blocks, _ = review_fixture(tmp_path)
    row["source_context"]["text"] = "FORGED_CACHED_ANCHOR"
    row["qa_contract"]["source_line_focus"] = [{"text": "FORGED_CACHED_SELECTION"}]
    evidence = revision_evidence(row, workflow.recipe, workflow.path)
    assert set(evidence) == {"teacher_evidence", "source_id", "source_kind", "evidence_pack_sha256"}
    assert evidence["teacher_evidence"] == "\n\n".join(blocks).strip()
    assert evidence["source_kind"] == "document"
    assert evidence["evidence_pack_sha256"] == pack["prefix_sha256"]
    detailed = revision_evidence(row, workflow.recipe, workflow.path, with_blocks=True)
    assert {key: detailed[key] for key in evidence} == evidence
    assert detailed["source_line_scope"] == "selected"
    assert detailed["source_line_ranges"] == row["qa_contract"]["source_line_ranges"]
    assert detailed["teacher_evidence_blocks"] == blocks
    assert blocks == [f"{POWER_RULE}\r\n{RESTART_RULE}\n"]
    assert "FORGED_CACHED" not in json.dumps(detailed)


def test_same_run_score_and_repair_only_receive_selected_raw_blocks_as_tool_results(tmp_path):
    workflow, row, _, blocks, client = review_fixture(tmp_path)
    evidence = workflow._review_evidence(row)
    assert evidence["teacher_evidence_blocks"] == blocks
    check = workflow._review_score("sft", row, evidence, [row["id"], "review"], "Correct the unsafe prerequisite.")
    updated, issue = workflow._repair_content("sft", row, evidence, [row["id"], "review"], check, "Correct it.")
    assert issue is None and updated["messages"][-1]["content"] == RIGHT
    assert row["messages"][-1]["content"] == WRONG
    assert len(client.message_history) == 2
    for messages in client.message_history:
        assert_selected_tool(messages, row["qa_contract"]["source_line_ranges"], blocks)
    span = updated["quote_spans"][0]
    assert span["source_quote"] == POWER_RULE
    assert span["source_block_index"] == 0 and span["offset_scope"] == "selected_source_block"
    assert {key: span[key] for key in ("material_id", "start_line", "end_line")} == row["qa_contract"]["source_line_ranges"][0]


def test_repair_cannot_quote_an_authentic_line_outside_the_director_selection(tmp_path):
    workflow, row, pack, blocks, client = review_fixture(tmp_path, quotes=[OUTSIDE_AFTER])
    assert quote_spans(pack["materials"][0]["text"], [OUTSIDE_AFTER]) is not None
    evidence = workflow._review_evidence(row)
    updated, issue = workflow._repair_content("sft", row, evidence, [row["id"], "out-of-scope"], verdict(), "Correct it.")
    assert issue == "review_repair_source_quotes_missing"
    assert updated == row and updated["messages"][-1]["content"] == WRONG
    assert_selected_tool(client.message_history[0], row["qa_contract"]["source_line_ranges"], blocks)


def test_repair_rejects_a_quote_spanning_the_separator_between_disjoint_blocks(tmp_path):
    workflow, row, _, blocks, client = review_fixture(tmp_path, selections=((2, 2), (4, 4)))
    evidence = workflow._review_evidence(row)
    seam = "\n\n".join(blocks).strip()
    assert quote_spans(evidence["teacher_evidence"], [seam]) is not None
    assert all(quote_spans(block, [seam]) is None for block in blocks)
    client.quotes = [seam]
    updated, issue = workflow._repair_content("sft", row, evidence, [row["id"], "disjoint-seam"], verdict(), "Correct it.")
    assert issue == "review_repair_source_quotes_missing" and updated == row
    assert_selected_tool(client.message_history[0], row["qa_contract"]["source_line_ranges"], blocks)
    assert RESTART_RULE not in json.dumps(tool_packs(client.message_history[0]))


def test_legacy_recipe_keeps_the_full_accepted_pack_and_old_quote_offsets(tmp_path):
    workflow, row, pack, _, client = review_fixture(tmp_path, numbered=False, quotes=[OUTSIDE_AFTER])
    evidence = revision_evidence(row, workflow.recipe, workflow.path, with_blocks=True)
    assert set(evidence) == {"teacher_evidence", "source_id", "source_kind", "evidence_pack_sha256"}
    assert evidence["teacher_evidence"] == SOURCE
    evidence = workflow._review_evidence(row)
    updated, issue = workflow._repair_content("sft", row, evidence, [row["id"], "legacy-full"], verdict(), "Correct it.")
    assert issue is None
    assert tool_packs(client.message_history[0]) == []
    request = client.calls[0]
    assert "source_line_scope" not in request
    assert request["teacher_context_pack"] == [pack]
    assert updated["quote_spans"][0]["source_quote"] == OUTSIDE_AFTER
    assert "source_block_index" not in updated["quote_spans"][0]


@pytest.mark.parametrize("change", ["missing", "body"])
def test_selected_review_never_falls_back_to_cached_evidence_when_pack_is_unavailable(tmp_path, change):
    workflow, row, _, _, client = review_fixture(tmp_path)
    path = workflow.path / row["source_context"]["source_context_pack"]["relative_path"]
    if change == "missing":
        path.unlink()
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["materials"][0]["text"] = "FORGED_PACK_BODY"
        path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="source_context_pack_(missing|invalid|changed)"):
        workflow._review_evidence(row)
    assert client.calls == []


def test_selected_review_requires_an_authenticated_pack_instead_of_a_raw_anchor(tmp_path):
    workflow, row, _, _, client = review_fixture(tmp_path)
    row["source_context"].pop("source_context_pack")
    assert row["source_context"]["text"] == SOURCE
    with pytest.raises(ValueError, match="human_session_source_evidence_unavailable"):
        revision_evidence(row, workflow.recipe, workflow.path, with_blocks=True)
    with pytest.raises(ValueError, match="human_session_source_evidence_unavailable"):
        workflow._review_evidence(row)
    assert client.calls == []


class NoCalls:
    model = "unused-selected-memory-parent"
    usage = {}

    def chat(self, *args, **kwargs):
        raise AssertionError("The native-cleaned, manually reviewed parent must not call a model.")


def sealed_parent(tmp_path, monkeypatch):
    """Seal a real native-cleaned run whose director chose two separated lines."""
    source = tmp_path / "sealed-manual.txt"
    with source.open("w", encoding="utf-8", newline="") as handle:
        handle.write(SOURCE)
    output = tmp_path / "sealed-output"
    parent_id = engine.create_run(output, sources=[source], targets=["sft"],
        cpt_processing={"mode": "native"}, review_repair={"mode": "human"}, chunk_chars=1000)

    def selected_sample(workflow, unit):
        pack = load_context_pack(workflow.path, unit["source_context_pack"])
        identity = unit["source_line_material_id"]
        material = next(item for item in pack["materials"] if item["material_id"] == identity)
        assert material["line_count"] == 5
        ranges = [{"material_id": identity, "start_line": line, "end_line": line} for line in (2, 4)]
        return [{"id": engine.digest([unit["id"], "selected-record"]), "source_id": unit["source_id"],
            "status": "eligible", "family_id": engine.digest([unit["source_id"], "inspection-task"]), "messages": [
                {"role": "user", "content": "Which precaution must precede equipment inspection?"},
                {"role": "assistant", "content": WRONG}],
            "source_context": deepcopy(unit), "quotes": [POWER_RULE, RECORD_RULE],
            "qa_contract": {"qa_type": "closedbook", "answer_policy": "answer", "source_line_ranges": ranges,
                            "question": "Which precaution must precede equipment inspection?", "visible_context": "",
                            "guidance": "Apply the selected safety prerequisite.", "evidence_quotes": [POWER_RULE, RECORD_RULE]},
            "evidence_level": "source_and_model_assessed"}]

    monkeypatch.setattr(engine.Workflow, "sft", selected_sample)
    parent = engine.Workflow(output, parent_id, ROOT, generator=NoCalls(), judge=NoCalls())
    state = parent.execute()
    assert state["status"] in {"completed", "needs_attention"}, state.get("error")
    record = engine.read_json(parent.path / "artifacts" / "sft.records.json")[0]
    engine.verify_artifacts(parent.path)
    evidence = revision_evidence(record, parent.recipe, parent.path, with_blocks=True)
    assert evidence["source_line_scope"] == "selected"
    assert len(evidence["teacher_evidence_blocks"]) == 2
    assert OUTSIDE_BEFORE not in evidence["teacher_evidence"] and RESTART_RULE not in evidence["teacher_evidence"]
    return output, parent_id, record, evidence


def registered_child(output, parent_id, record):
    """Use the same immutable session authorization for each revision depth."""
    parent_path = engine.run_path(output, parent_id)
    parent_recipe = engine.read_json(parent_path / "recipe.json")
    prior = record.get("revision_context", {})
    ancestors = [*prior.get("ancestors", []), {"run_id": parent_id, "candidate_id": record["id"]}]
    session_id, round_id = uuid.uuid4().hex, uuid.uuid4().hex
    context = {"session_id": session_id, "round_id": round_id, "parent_run_id": parent_id,
        "target": "sft", "candidate_id": record["id"], "content_sha256": engine.digest(record),
        "messages": record_messages(record), "instruction": "Correct the unsafe prerequisite using the selected facts.",
        "depth": prior.get("depth", 0) + 1, "ancestors": ancestors,
        **revision_evidence(record, parent_recipe, parent_path)}
    child_id = engine.create_run(output, targets=["sft"], review_repair={"mode": "auto", "max_rounds": 1},
        repair_inputs=[{"target": "sft", "record": record, "revision_context": context,
                        "instruction": context["instruction"]}])
    atomic_json(output / "human-sessions" / session_id / "session.json", {"rounds": [{"run_id": child_id,
        "kind": "revision", "status": "prepared", "parent_results": [{"run_id": parent_id, "round_id": round_id,
            "target": "sft", "candidate_id": record["id"], "content_sha256": engine.digest(record)}]}]})
    return child_id, context


def test_child_and_next_revision_reauthenticate_parent_selection_without_expanding_scope(tmp_path, monkeypatch):
    output, parent_id, original, expected = sealed_parent(tmp_path, monkeypatch)
    child_id, context = registered_child(output, parent_id, original)
    assert "source_line_scope" not in context and "teacher_evidence_blocks" not in context
    first_client = ReviewClient([POWER_RULE, RECORD_RULE], accept_repaired=True)
    first = engine.Workflow(output, child_id, ROOT, generator=first_client, judge=first_client)
    # The foreign source reference is deliberately absent under this run.
    with pytest.raises(ValueError, match="source_context_pack_missing"):
        load_context_pack(first.path, original["source_context"]["source_context_pack"])
    state = first.execute()
    assert state["status"] == "completed", state.get("error")
    repaired = engine.read_json(first.path / "artifacts" / "sft.records.json")[0]
    assert repaired["status"] == "eligible"
    assert repaired["revision_context"] == context
    assert "revision_evidence_blocks" not in repaired
    assert all("source_line_scope" not in context and "teacher_evidence_blocks" not in context
               for context in (repaired["revision_context"], repaired["qa_contract"]["revision_context"]))
    detailed = revision_evidence(repaired, first.recipe, first.path, with_blocks=True)
    assert detailed["teacher_evidence_blocks"] == expected["teacher_evidence_blocks"]
    assert detailed["source_line_ranges"] == expected["source_line_ranges"]
    for messages in first_client.message_history:
        if tool_packs(messages):
            assert_selected_tool(messages, expected["source_line_ranges"], expected["teacher_evidence_blocks"])
    assert any(tool_packs(messages) for messages in first_client.message_history)

    next_id, next_context = registered_child(output, child_id, repaired)
    assert next_context["depth"] == 2 and len(next_context["ancestors"]) == 2
    next_client = ReviewClient([OUTSIDE_AFTER], accept_repaired=True)
    next_revision = engine.Workflow(output, next_id, ROOT, generator=next_client, judge=next_client)
    state = next_revision.execute()
    assert state["status"] in {"completed", "needs_attention"}, state.get("error")
    rejected = engine.read_json(next_revision.path / "artifacts" / "sft.records.json")[0]
    assert rejected["status"] == "quarantined"
    assert rejected["review_repair"]["history"][0]["repair_issue"] == "review_repair_source_quotes_missing"
    assert rejected["qa_contract"]["source_line_ranges"] == expected["source_line_ranges"]
    for messages in next_client.message_history:
        assert_selected_tool(messages, expected["source_line_ranges"], expected["teacher_evidence_blocks"])
    # A local cached field or broadened current contract never supersedes the
    # selected ranges in the authenticated original parent.
    forged = deepcopy(rejected)
    forged["revision_evidence_blocks"] = [OUTSIDE_AFTER]
    forged["qa_contract"]["source_line_ranges"] = [
        {**expected["source_line_ranges"][0], "start_line": 1, "end_line": 5}]
    checked = next_revision._review_evidence(forged)
    assert checked["teacher_evidence_blocks"] == expected["teacher_evidence_blocks"]
    assert checked["source_line_ranges"] == expected["source_line_ranges"]


def test_child_refuses_forged_selected_evidence_before_any_model_request(tmp_path, monkeypatch):
    output, parent_id, original, _ = sealed_parent(tmp_path, monkeypatch)
    child_id, _ = registered_child(output, parent_id, original)
    client = ReviewClient([POWER_RULE])
    child = engine.Workflow(output, child_id, ROOT, generator=client, judge=client)
    forged = deepcopy(original)
    forged["revision_context"] = deepcopy(child.recipe["repair_inputs"][0]["revision_context"])
    forged["revision_context"]["teacher_evidence"] += "\n" + OUTSIDE_AFTER
    with pytest.raises(ValueError, match="human_session_result_changed"):
        child._review_evidence(forged)
    assert client.calls == []
