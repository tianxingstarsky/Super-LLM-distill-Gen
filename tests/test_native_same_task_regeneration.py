"""Same-question native regeneration uses real SDK prefill and no paid service."""
from copy import deepcopy
import hashlib

import pytest

from lib.infrastructure.workflow_native_regeneration import regenerate_native_cot_item
from lib.domain.workflow_quality import canonical
from lib.domain.review_repair import validate_review_repair
from lib.infrastructure import training_workflow as engine
from test_workflow_native_prefill import setup_workflow, PREFIX, ANSWER, TEXT
from test_native_answer_visible_retry import controlled_sdk
from test_workflow_styled_generation import run


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def selected_parent(tmp_path, monkeypatch, *, missing=False):
    workflow, _, client, reviewer, sent, _ = setup_workflow(tmp_path, monkeypatch)
    output, rid, _ = run(tmp_path, conversation=True, targets=["cot"],
        review_repair={"mode": "auto"}, package_review={"enabled": True, "mode": "all", "node": "jev"},
        node_generation={"cot": {"style": "default", "answer_mode": "native_prefill"}})
    workflow = engine.Workflow(output, rid, tmp_path, generator=client, jev=reviewer)
    workflow.stage = "review"
    assert workflow.state["stages"]["review"] == {"label": engine.STAGES["review"], "status": "pending", "done": 0, "total": 0}
    workflow.recipe.update(native_regeneration_version=1,
        review_repair={"mode": "auto", "max_rounds": 1, "score_threshold": .8, "native_regeneration": True},
        repair_inputs=[{"authentication": "handled_by_execute_repair_branch"}])
    workflow._authenticated_repair_branch = True
    question = [{"role": "user", "content": "设备应如何维护？"}]
    record = {"id": "sealed-parent", "source_id": "source", "question": question,
              "status": "quarantined" if missing else "eligible", "evidence_level": "source_and_model_assessed",
              "source_context": {"kind": "document", "text": TEXT}}
    if not missing:
        record.update(reasoning=["OLD_PREFIX_SENTINEL"], answer="OLD_BAD_ANSWER_SENTINEL",
            messages=[*question, {"role": "assistant", "content": "OLD_SFT_DRAFT_SENTINEL"}],
            reasoning_origin="authored_prefix_native_answer", reasoning_prefill={"transport": "deepseek_chat_beta",
                "prefix_sha256": "old-prefix"}, answer_response_details={"reasoning_content": "OLD_PROVIDER_SENTINEL"})
    else:
        record["native_answer_terminal"] = {"code": "model_visible_output_missing", "prefix_sha256": "OLD_EMPTY_PREFIX"}
    item = {"target": "cot", "record": deepcopy(record), "instruction": "EDITORIAL_FEEDBACK_SENTINEL：保留断电前提，勿将旧稿当事实。",
            "revision_context": {"native_regeneration": True, "candidate_id": record["id"], "parent_run_id": "sealed-parent-run"}}
    evidence = {"source_id": "source", "source_kind": "document", "teacher_evidence": TEXT,
                "teacher_evidence_blocks": [TEXT], "evidence_pack_sha256": "parent-evidence-receipt"}
    monkeypatch.setattr(workflow, "_review_evidence", lambda row: deepcopy(evidence))
    # Source authentication itself is exercised by the parent-loader tests;
    # this probe uses its output, through the real sealed-memory transport.
    return workflow, item, client, reviewer, sent, record


@pytest.mark.parametrize("missing", [False, True])
def test_regeneration_preserves_exact_question_has_new_prefix_receipt_and_never_calls_director_or_sft(tmp_path, monkeypatch, missing):
    workflow, item, _, reviewer, sent, original = selected_parent(tmp_path, monkeypatch, missing=missing)
    row, = regenerate_native_cot_item(workflow, item)
    assert row["status"] == "eligible" and row["question"] == original["question"]
    assert row["answer"] == ANSWER and row["reasoning"] == [PREFIX]
    assert len(sent) == 2 and len(reviewer.calls) == 3  # prefix, ordinary fact review, style
    assert row["reasoning_origin"] == "authored_prefix_native_answer"
    assert row["reasoning_prefill"]["prefix_sha256"] == hashlib.sha256(PREFIX.encode()).hexdigest()
    assert row["answer_response_details"]["reasoning_content"] == "provider-only continuation"
    assert row["review_repair"]["native_regeneration"] is True
    assert row["review_repair"]["status"] == "accepted"
    wire = canonical(sent)
    for sentinel in ("OLD_PREFIX_SENTINEL", "OLD_BAD_ANSWER_SENTINEL", "OLD_SFT_DRAFT_SENTINEL", "OLD_PROVIDER_SENTINEL", "OLD_EMPTY_PREFIX"):
        assert sentinel not in wire
    assert "EDITORIAL_FEEDBACK_SENTINEL" in wire
    for request in sent:
        learner = request["messages"][-2] if request["messages"][-1].get("prefix") else request["messages"][-1]
        assert learner == original["question"][-1]
        assert TEXT not in learner["content"] and "EDITORIAL_FEEDBACK" not in learner["content"]
    assert sent[-1]["messages"][-1] == {"role": "assistant", "content": "", "prefix": True, "reasoning_content": PREFIX}
    assert item["record"] == original
    if not missing:
        assert row["reasoning_prefill_history"][0]["reasoning_prefill"]["prefix_sha256"] == "old-prefix"
        assert row["native_regeneration_history"][0]["answer_response_details"]["reasoning_content"] == "OLD_PROVIDER_SENTINEL"
    again, = regenerate_native_cot_item(workflow, item)
    assert again == row and len(sent) == 2 and len(reviewer.calls) == 3


def test_empty_answer_retries_only_answer_and_never_reuses_old_draft(tmp_path, monkeypatch):
    workflow, item, client, _, sent, _ = selected_parent(tmp_path, monkeypatch, missing=True)
    controlled_sdk(client, sent, monkeypatch, ["empty", "empty"])
    row, = regenerate_native_cot_item(workflow, item)
    assert row["status"] == "quarantined" and row["reason"] == "native_answer_missing_after_retry"
    assert row["native_answer_terminal"]["answer_attempts"] == 2
    assert all(field not in row for field in ("answer", "reasoning", "messages", "reasoning_prefill", "answer_response_details"))
    assert len(sent) == 3 and sent[1] == sent[2]
    regenerate_native_cot_item(workflow, item)
    assert len(sent) == 3  # negative receipts and verified prefix survive resume


@pytest.mark.parametrize("broken", ["auth", "recipe_marker", "context_marker", "patch", "question"])
def test_no_requests_without_explicit_authenticated_immutable_input(tmp_path, monkeypatch, broken):
    workflow, item, _, _, sent, _ = selected_parent(tmp_path, monkeypatch)
    if broken == "auth":
        workflow._authenticated_repair_branch = False
    elif broken == "recipe_marker":
        workflow.recipe.pop("native_regeneration_version")
    elif broken == "context_marker":
        item["revision_context"].pop("native_regeneration")
    elif broken == "patch":
        item["patch"] = {"question": "changed task"}
    else:
        item["record"].pop("question")
    with pytest.raises(ValueError):
        regenerate_native_cot_item(workflow, item)
    assert sent == []


@pytest.mark.parametrize("field", ["patch", "corrected_record", "messages", "question", "answer", "reasoning", "score", "approved"])
def test_native_route_rejects_even_empty_task_or_score_patches(tmp_path, monkeypatch, field):
    workflow, item, _, _, sent, _ = selected_parent(tmp_path, monkeypatch)
    item[field] = None
    with pytest.raises(ValueError, match="native_regeneration_authenticated_parent_required"):
        regenerate_native_cot_item(workflow, item)
    assert not sent


def test_zero_rounds_is_not_silently_promoted_to_a_paid_attempt(tmp_path, monkeypatch):
    workflow, item, _, _, sent, _ = selected_parent(tmp_path, monkeypatch)
    workflow.recipe["review_repair"]["max_rounds"] = 0
    with pytest.raises(ValueError, match="native_regeneration_requires_review_round"):
        regenerate_native_cot_item(workflow, item)
    assert not sent
    assert validate_review_repair({"mode": "auto", "max_rounds": 0}) == {
        "mode": "auto", "max_rounds": 0, "score_threshold": .8}


@pytest.mark.parametrize("reason", ["bad\x00reason", "x" * 4001, "   "])
def test_invalid_prefix_verdict_never_reaches_native_answer(tmp_path, monkeypatch, reason):
    import json
    workflow, item, _, reviewer, sent, _ = selected_parent(tmp_path, monkeypatch)
    monkeypatch.setattr(reviewer, "chat", lambda *args, **kwargs: json.dumps({"keep": True, "adherence": 5, "reason": reason}))
    with pytest.raises(ValueError, match="invalid_style_judge_schema"):
        regenerate_native_cot_item(workflow, item)
    assert len(sent) == 1 and not sent[0]["messages"][-1].get("prefix")


def test_native_answer_schema_rejects_an_added_reasoning_field(tmp_path, monkeypatch):
    workflow, item, _, _, sent, _ = selected_parent(tmp_path, monkeypatch)
    ask = workflow.ask
    def corrupt(key, role, prompt_id, data, **kwargs):
        value = ask(key, role, prompt_id, data, **kwargs)
        if prompt_id == "workflow.cot_native_answer":
            value["reasoning"] = "should not be a visible answer wrapper"
        return value
    monkeypatch.setattr(workflow, "ask", corrupt)
    with pytest.raises(ValueError, match="native_prefill_invalid_answer"):
        regenerate_native_cot_item(workflow, item)
    assert len(sent) == 2


def test_rejected_prefix_preserves_parent_only_in_private_history_not_current_body(tmp_path, monkeypatch):
    import json
    workflow, item, _, reviewer, sent, original = selected_parent(tmp_path, monkeypatch)
    monkeypatch.setattr(reviewer, "chat", lambda *args, **kwargs: json.dumps({
        "keep": False, "adherence": 1, "reason": "推理与断电前提矛盾。"}))
    row, = regenerate_native_cot_item(workflow, item)
    assert row["status"] == "quarantined" and row["reason"] == "native_regeneration_failed_after_prefix_review"
    assert all(field not in row for field in (
        "answer", "reasoning", "messages", "reasoning_prefill", "answer_response_details", "judge", "style_check"))
    assert row["question"] == original["question"]
    private = row["native_regeneration_history"][-1]
    assert private["answer"] == "OLD_BAD_ANSWER_SENTINEL"
    assert private["reasoning_prefill"]["prefix_sha256"] == "old-prefix"
    assert len(sent) == 1 and row["review_repair"]["candidate_sha256"] is None


def test_authenticated_native_branch_packages_rejected_rows_and_resumes_without_new_requests(tmp_path, monkeypatch):
    import json
    import uuid
    from lib.io_utils import atomic_json
    from lib.infrastructure.human_revision import revision_evidence, verified_revision_parent
    from test_review_repair_engine import NoCalls

    source = tmp_path / "maintenance.txt"
    source.write_text(TEXT, encoding="utf-8")
    output = tmp_path / "production-output"
    parent_id = engine.create_run(output, sources=[source], targets=["cot"], review_repair={"mode": "human"})
    question = [{"role": "user", "content": "设备应如何维护？"}]
    def sft(workflow, unit):
        return [{"id": unit["id"], "source_id": unit["source_id"], "status": "eligible",
                 "messages": [*question, {"role": "assistant", "content": ANSWER}], "source_context": unit}]
    def cot(workflow, sample):
        common = {"source_id": sample["source_id"], "question": question,
                  "source_context": sample["source_context"], "evidence_level": "document_quote"}
        return [{**common, "id": label, "status": "eligible", "reasoning": [PREFIX], "answer": ANSWER}
                for label in ("accepted-parent", "prefix-rejected-parent")] + [
            {**common, "id": "empty-answer-parent", "status": "quarantined",
             "reason": "native_answer_missing_after_retry"}]
    monkeypatch.setattr(engine.Workflow, "sft", sft)
    monkeypatch.setattr(engine.Workflow, "cot", cot)
    parent = engine.Workflow(output, parent_id, tmp_path, generator=NoCalls(), jev=NoCalls())
    assert parent.execute()["status"] == "needs_attention"
    engine.verify_artifacts(parent.path)
    parents = engine.read_json(parent.path / "artifacts/cot.records.json")
    assert len(parents) == 3
    sid = uuid.uuid4().hex
    items = []
    for record in parents:
        ctx = {"session_id": sid, "round_id": uuid.uuid4().hex, "parent_run_id": parent_id,
               "target": "cot", "candidate_id": record["id"], "content_sha256": engine.digest(record),
               "messages": deepcopy(record["question"]), "instruction": "保留断电前提，按原题重新生成。",
               "depth": 1, "ancestors": [{"run_id": parent_id, "candidate_id": record["id"]}],
               "native_regeneration": True, **revision_evidence(record, parent.recipe, parent.path)}
        items.append({"target": "cot", "record": record, "revision_context": ctx, "instruction": ctx["instruction"]})
    child_id = engine.create_run(output, targets=["cot"], repair_inputs=items,
        review_repair={"mode": "auto", "max_rounds": 1, "native_regeneration": True},
        package_review={"enabled": True, "mode": "all", "node": "jev"},
        node_generation={"cot": {"style": "default", "answer_mode": "native_prefill"}})
    atomic_json(output / "human-sessions" / sid / "session.json", {"rounds": [{
        "run_id": child_id, "kind": "revision", "status": "prepared",
        "parent_results": [{"run_id": parent_id, "round_id": item["revision_context"]["round_id"],
            "target": "cot", "candidate_id": item["record"]["id"],
            "content_sha256": engine.digest(item["record"])} for item in items]}]})
    for item in items:
        assert verified_revision_parent(output, child_id, item["revision_context"]) == item["record"]
    _, _, client, reviewer, sent, _ = setup_workflow(tmp_path, monkeypatch)
    controlled_sdk(client, sent, monkeypatch, ["success", "empty", "empty"])
    original_review = reviewer.chat
    prefix_count = []
    def review(messages, **kwargs):
        if "独立核验待作为原生API推理前缀" in messages[0]["content"]:
            prefix_count.append(1)
            if len(prefix_count) == 2:
                reviewer.calls.append(deepcopy(messages))
                reviewer.usage["calls"] += 1
                return json.dumps({"keep": False, "adherence": 1, "reason": "本条推理遗漏断电条件。"})
        return original_review(messages, **kwargs)
    monkeypatch.setattr(reviewer, "chat", review)
    child = engine.Workflow(output, child_id, tmp_path, generator=client, jev=reviewer)
    def interrupted(collections):
        raise OSError("offline_interruption_after_review")
    monkeypatch.setattr(child, "package", interrupted)
    failed = child.execute()
    assert failed["status"] == "failed" and failed["error"] == "OSError"
    before = [
        json.loads(line) for line in (child.path / "stage-results/review-cot.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["status"] for row in before] == ["eligible", "quarantined", "quarantined"]
    assert [row.get("reason") for row in before[1:]] == [
        "native_regeneration_failed_after_prefix_review", "native_answer_missing_after_retry"]
    assert len(sent) == 6 and len(prefix_count) == 3
    requests_before = (len(sent), len(reviewer.calls))
    resumed = engine.Workflow(output, child_id, tmp_path, generator=client, jev=reviewer)
    completed = resumed.execute(resume_run=True)
    assert completed["status"] == "needs_attention", completed.get("error")
    assert (len(sent), len(reviewer.calls)) == requests_before
    manifest = engine.verify_artifacts(resumed.path)
    assert manifest["run_id"] == child_id
    formal = engine.read_json(resumed.path / "artifacts/cot.records.json")
    assert [row["review_repair"] for row in formal] == [row["review_repair"] for row in before]
    assert [row["status"] for row in formal] == ["eligible", "quarantined", "quarantined"]
    assert all(not row.get("answer") and not row.get("reasoning") for row in formal[1:])
    exported = [json.loads(line) for line in (resumed.path / "artifacts/cot.jsonl").read_text(encoding="utf-8").splitlines()]
    assert exported == [engine.training_record("cot", formal[0])]


@pytest.mark.parametrize("changed", [None, "question", "context", "receipt", "body", "status"])
def test_packaging_negative_reuse_is_bound_to_exact_native_selection(tmp_path, monkeypatch, changed):
    import json
    workflow, item, _, reviewer, sent, _ = selected_parent(tmp_path, monkeypatch)
    monkeypatch.setattr(reviewer, "chat", lambda *args, **kwargs: json.dumps({
        "keep": False, "adherence": 1, "reason": "本条未保留断电前提。"}))
    row, = regenerate_native_cot_item(workflow, item)
    workflow.recipe["repair_inputs"] = [deepcopy(item)]
    workflow._authenticated_repair_branch = False
    if changed == "question":
        row["question"][-1]["content"] = "a different task"
    elif changed == "context":
        row["revision_context"]["parent_run_id"] = "other-parent"
    elif changed == "receipt":
        row["review_repair"]["config_sha256"] = "changed"
    elif changed == "body":
        row["answer"] = "an added answer must be reviewed"
    elif changed == "status":
        row["status"] = "eligible"
    def forbidden(value):
        raise ValueError("unverified_cached_row_requires_review")
    monkeypatch.setattr(workflow, "_review_repair_item", forbidden)
    if changed:
        with pytest.raises(ValueError, match="unverified_cached_row_requires_review"):
            workflow.review_repair_collections({"cot": [row]})
    else:
        result = workflow.review_repair_collections({"cot": [row]})
        reused, = list(result["cot"])
        assert reused["status"] == "quarantined" and reused["review_repair"] == row["review_repair"]
        assert not reused.get("answer") and not reused.get("reasoning")
    assert len(sent) == 1


def test_packaging_never_reuses_changed_positive_body(tmp_path, monkeypatch):
    workflow, item, _, _, sent, _ = selected_parent(tmp_path, monkeypatch)
    row, = regenerate_native_cot_item(workflow, item)
    assert row["status"] == "eligible"
    workflow.recipe["repair_inputs"] = [deepcopy(item)]
    workflow._authenticated_repair_branch = False
    row["answer"] += " changed after grading"
    def forbidden(value):
        raise ValueError("changed_positive_requires_review")
    monkeypatch.setattr(workflow, "_review_repair_item", forbidden)
    with pytest.raises(ValueError, match="changed_positive_requires_review"):
        workflow.review_repair_collections({"cot": [row]})
    assert len(sent) == 2


def test_native_same_question_can_revise_its_published_ancestor_but_regular_duplicates_still_fail(tmp_path, monkeypatch):
    import uuid
    from lib.io_utils import atomic_json
    from lib.infrastructure.human_revision import revision_evidence, verified_revision_parent
    from lib.infrastructure.qa_history import QAHistory
    from test_review_repair_engine import NoCalls
    from test_workflow_styled_generation import Reviewer

    source = tmp_path / "published-maintenance.txt"
    source.write_text(TEXT, encoding="utf-8")
    output = tmp_path / "published-output"
    question = [{"role": "user", "content": "设备应如何维护？"}]
    contract = {"question": question[0]["content"], "visible_context": "", "qa_type": "closed_book", "answer_policy": "answer"}
    def sft(workflow, unit):
        return [{"id": unit["id"], "source_id": unit["source_id"], "status": "eligible", "family_id": "maintenance",
                 "messages": [*question, {"role": "assistant", "content": ANSWER}],
                 "source_context": unit, "qa_contract": deepcopy(contract)}]
    def directed(workflow, units, **kwargs):
        workflow.state["qa_director"] = {"coverage": {"closed_book": {"accepted": 1, "rejected": 0}}, "batches_planned": 1}
        return {"sft": workflow.stage_items("sft", units, workflow.sft), "multiturn": []}
    def cot(workflow, sample):
        return [{**deepcopy(sample), "id": "published-parent", "question": deepcopy(question),
                 "reasoning": [PREFIX], "answer": ANSWER}]
    monkeypatch.setattr(engine.Workflow, "sft", sft)
    monkeypatch.setattr(engine.Workflow, "directed_generation", directed)
    monkeypatch.setattr(engine.Workflow, "cot", cot)
    def ordinary_run():
        rid = engine.create_run(output, sources=[source], targets=["cot"], qa_director={"enabled": True},
            review_repair={"mode": "auto", "max_rounds": 0},
            package_review={"enabled": True, "mode": "all", "node": "jev"})
        workflow = engine.Workflow(output, rid, tmp_path, generator=NoCalls(), jev=Reviewer())
        return workflow, workflow.execute()
    parent, state = ordinary_run()
    assert state["status"] == "completed", state.get("error")
    engine.verify_artifacts(parent.path)
    original, = engine.read_json(parent.path / "artifacts/cot.records.json")
    with QAHistory(output / "qa-history.sqlite3") as history:
        prior, = history.recent(namespace="sft")
        assert prior["run_id"] == parent.state["id"] and prior["id"].endswith(":" + original["id"])
    sid, round_id = uuid.uuid4().hex, uuid.uuid4().hex
    ctx = {"session_id": sid, "round_id": round_id, "parent_run_id": parent.state["id"],
        "target": "cot", "candidate_id": original["id"], "content_sha256": engine.digest(original),
        "messages": deepcopy(question), "instruction": "保留原题的断电前提，重新生成解释。", "depth": 1,
        "ancestors": [{"run_id": parent.state["id"], "candidate_id": original["id"]}],
        "native_regeneration": True, **revision_evidence(original, parent.recipe, parent.path)}
    item = {"target": "cot", "record": original, "revision_context": ctx, "instruction": ctx["instruction"]}
    child_id = engine.create_run(output, targets=["cot"], repair_inputs=[item],
        review_repair={"mode": "auto", "max_rounds": 1, "native_regeneration": True},
        package_review={"enabled": True, "mode": "all", "node": "jev"},
        node_generation={"cot": {"style": "default", "answer_mode": "native_prefill"}})
    atomic_json(output / "human-sessions" / sid / "session.json", {"rounds": [{
        "run_id": child_id, "kind": "revision", "status": "prepared", "parent_results": [{
            "run_id": parent.state["id"], "round_id": round_id, "target": "cot",
            "candidate_id": original["id"], "content_sha256": engine.digest(original)}]}]})
    assert verified_revision_parent(output, child_id, ctx) == original
    _, _, client, reviewer, sent, _ = setup_workflow(tmp_path, monkeypatch)
    child = engine.Workflow(output, child_id, tmp_path, generator=client, jev=reviewer)
    ask, rationale_inputs = child.ask, []
    def captured(key, role, prompt_id, data, **kwargs):
        if prompt_id == "workflow.cot_rationale":
            rationale_inputs.append(deepcopy(data["qa_contract"]))
        return ask(key, role, prompt_id, data, **kwargs)
    monkeypatch.setattr(child, "ask", captured)
    result = child.execute()
    assert result["status"] == "completed", result.get("error")
    engine.verify_artifacts(child.path)
    revised, = engine.read_json(child.path / "artifacts/cot.records.json")
    assert revised["status"] == "eligible" and revised["question"] == original["question"]
    assert revised["qa_contract"]["revision_context"] == ctx
    assert revised["reasoning_prefill"]["prefix_sha256"] == hashlib.sha256(PREFIX.encode()).hexdigest()
    assert rationale_inputs == [original["qa_contract"]]
    assert "revision_context" not in original["qa_contract"]
    assert len(sent) == 2
    ordinary, ordinary_state = ordinary_run()
    assert ordinary_state["status"] == "needs_attention", ordinary_state.get("error")
    duplicate, = engine.read_json(ordinary.path / "artifacts/cot.records.json")
    assert duplicate["reason"] == "released_qa_contract_duplicate" and duplicate["status"] == "quarantined"
    assert engine.read_json(parent.path / "artifacts/cot.records.json") == [original]


@pytest.mark.parametrize("identical_body", [False, True])
def test_provider_parent_metadata_is_historical_after_new_native_response(tmp_path, monkeypatch, identical_body):
    workflow, item, _, _, sent, _ = selected_parent(tmp_path, monkeypatch)
    original = item["record"]
    original.pop("reasoning_prefill")
    original.pop("answer_response_details")
    original.update(reasoning_origin="provider_reasoning", reasoning_summary={"provider_tokens": 77},
        reasoning_api_format="chat", reasoning_call={"stage": "old-cot", "checkpoint": "old-call"})
    if identical_body:
        original.update(reasoning=[PREFIX], answer=ANSWER)
        original["messages"][-1].update(content=ANSWER, reasoning_content=PREFIX)
    before = deepcopy(original)
    row, = regenerate_native_cot_item(workflow, item)
    assert row["status"] == "eligible" and row["reasoning"] == [PREFIX] and row["answer"] == ANSWER
    assert row["reasoning_origin"] == "authored_prefix_native_answer"
    assert row["reasoning_prefill"]["prefix_sha256"] == hashlib.sha256(PREFIX.encode()).hexdigest()
    assert not any(field in row for field in ("reasoning_summary", "reasoning_api_format", "reasoning_call"))
    saved = row["native_regeneration_history"][-1]
    assert saved["reasoning_origin"] == "provider_reasoning"
    assert saved["reasoning_summary"] == {"provider_tokens": 77}
    assert saved["reasoning_call"] == {"stage": "old-cot", "checkpoint": "old-call"}
    if not identical_body:
        retired, = row["provider_reasoning_history"]
        assert retired["scope"] == "historical_provider_response_only"
        assert retired["reasoning_content"] == "OLD_PREFIX_SENTINEL"
    assert original == before and len(sent) == 2


def test_rejected_new_prefix_does_not_display_old_provider_summary_as_current(tmp_path, monkeypatch):
    import json
    workflow, item, _, reviewer, _, _ = selected_parent(tmp_path, monkeypatch)
    item["record"].update(reasoning_origin="provider_reasoning", reasoning_summary={"provider_tokens": 77},
        reasoning_api_format="chat", reasoning_call={"checkpoint": "old-call"})
    monkeypatch.setattr(reviewer, "chat", lambda *args, **kwargs: json.dumps({
        "keep": False, "adherence": 1, "reason": "新前缀遗漏断电条件。"}))
    row, = regenerate_native_cot_item(workflow, item)
    assert row["status"] == "quarantined" and row["reasoning_origin"] == "native_regeneration_no_complete_answer"
    assert not any(field in row for field in (
        "reasoning_summary", "reasoning_api_format", "reasoning_call", "reasoning", "answer"))
    assert row["native_regeneration_history"][-1]["reasoning_summary"] == {"provider_tokens": 77}
