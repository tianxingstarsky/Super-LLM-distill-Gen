"""Adaptive tool decisions with strict evidence, resume and independent-review boundaries."""
from copy import deepcopy
import json
from threading import Lock
from types import SimpleNamespace

import pytest

from lib.contexts.workflows.application.check_reasoning import CheckReasoning
from lib.contexts.workflows.domain.reasoning_checklist import checklist_from_text
from lib.contexts.workflows.domain.reasoning_investigation import ReasoningInvestigation
from lib.contexts.workflows.infrastructure.reasoning_agent_adapter import CheckpointedReasoningAgent


RULES = [{"id": "scope", "requirement": "核对范围。", "allow_na": False}]
BODY = {"reasoning": "共有175项任务。", "answer": "共有175项任务。"}
CONTEXT = {"source": {"text": "共有175项任务。"}, "prompt": [{"role": "user", "content": "有多少任务？"}]}


def check(status="met", quote=None):
    return {"rule_id": "scope", "status": status, "evidence": [{"field": "reasoning", "quote": quote or BODY["reasoning"]}],
            "reason": "引用实际数量，不扩大范围。"}


def claim(anchor, status="supported"):
    return {"anchor_id": anchor["id"], "status": status,
            "evidence": [{"source": "source.text", "quote": BODY["reasoning"]}],
            "reason": "原文报告这个任务数量。", "counterexample": "", "calculations": [],
            "no_calculation_reason": "这是单独计数，不是差值或增幅。"}


def turn(*calls):
    return {"role": "assistant", "content": "", "reasoning_content": "preserve native state",
            "tool_calls": [{"id": f"call-{i}", "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
                           for i, (name, args) in enumerate(calls)]}


class AgentPort:
    def __init__(self, author, reviewer=None):
        self.responses = {"generation": iter(author), "jev": iter(reviewer or [])}
        self.calls, self.events = [], []

    def check_reasoning_cancel(self):
        pass

    def prepare_reasoning_context(self, context):
        return deepcopy(context)

    def reasoning_progress(self, *args):
        self.events.append(args)

    def reasoning_tool_turn(self, key, role, messages, tools):
        self.calls.append((deepcopy(key), role, deepcopy(messages), deepcopy(tools)))
        result = deepcopy(next(self.responses[role]))
        for c in result.get("tool_calls", []):
            c["id"] = f"{len(self.calls)}-{c['id']}"
        return result


def execute(port):
    return CheckReasoning(port, {"version": 3, "rules": RULES}).execute(["sample"], CONTEXT, BODY, phase="final")


def test_agent_can_choose_claims_before_rules_and_submit_small_independent_items():
    workspace = ReasoningInvestigation(RULES, CONTEXT, BODY)
    observations = [turn(("record_claim", {"claim": claim(a)})) for a in workspace.anchors]
    observations += [turn(("record_check", {"check": check()}))]
    port = AgentPort(observations, list(reversed(observations)))
    audit = execute(port)
    assert audit["passed"] and audit["version"] == 3
    assert len(port.calls) == 6
    # Persistent tool roles, native state and previous tool results are retained.
    assert port.calls[1][2][1]["reasoning_content"] == "preserve native state"
    assert port.calls[1][2][2]["role"] == "tool"
    # Independent model sees only the frozen task, no author's successful votes.
    assert len(port.calls[3][2]) == 1
    assert "source_index" in json.loads(port.calls[0][2][0]["content"])
    assert "source" not in json.loads(port.calls[0][2][0]["content"])["task"]


def test_invalid_entry_can_be_corrected_without_repeating_committed_evidence():
    workspace = ReasoningInvestigation(RULES, CONTEXT, BODY)
    observations = [turn(*[("record_claim", {"claim": claim(a)}) for a in workspace.anchors]),
                    turn(("record_check", {"check": check(quote="不存在的引文")})),
                    turn(("record_check", {"check": check()}))]
    reviewer = [turn(("record_check", {"check": check()}),
                     *[("record_claim", {"claim": claim(a)}) for a in workspace.anchors])]
    port = AgentPort(observations, reviewer)
    audit = execute(port)
    assert audit["passed"] and len(port.calls) == 4
    bad = audit["self_check_session"]["trace"][2]["observation"]
    assert bad["issue"] == "checklist_evidence_not_in_candidate"
    assert bad["pending"]["anchor_ids"] == []


def test_negative_vote_is_not_overwritten_or_sent_to_another_judge():
    port = AgentPort([turn(("record_check", {"check": check("unmet")}))])
    audit = execute(port)
    assert not audit["passed"] and audit["issue"] == "reasoning_agent_found_gap"
    assert len(port.calls) == 1 and "independent_check" not in audit
    w = ReasoningInvestigation(RULES, CONTEXT, BODY)
    assert w.execute("record_check", {"check": check("unmet")})["ok"]
    assert w.execute("record_check", {"check": check()})["issue"] == "evidence_already_committed"
    assert w.snapshot()["failed"]


def test_uncertain_evidence_is_not_reported_as_a_candidate_defect():
    workspace = ReasoningInvestigation(RULES, CONTEXT, BODY)
    port = AgentPort([turn(("record_claim", {"claim": claim(workspace.anchors[0], "uncertain")}))])
    audit = execute(port)
    assert not audit["passed"] and audit["issue"] == "reasoning_agent_evidence_unresolved"
    assert audit["self_check_session"]["blocked"] and not audit["self_check_session"]["failed"]
    assert len(port.calls) == 1


def test_malformed_calculation_can_be_fixed_without_committing_a_false_defect():
    workspace = ReasoningInvestigation(RULES, CONTEXT, BODY)
    entry = claim(workspace.anchors[0])
    entry["calculations"] = [{"operation": "difference"}]
    observed = workspace.execute("record_claim", {"claim": entry})
    assert not observed["ok"] and observed["issue"] == "calculation_schema"
    assert not workspace.claims and not workspace.snapshot()["failed"]
    entry["calculations"] = []
    assert workspace.execute("record_claim", {"claim": entry})["ok"]


def test_finish_does_not_waive_missing_evidence_and_repeated_nonprogress_stops():
    port = AgentPort([turn(("finish_review", {})) for _ in range(3)])
    audit = execute(port)
    assert not audit["passed"] and audit["issue"] == "reasoning_agent_no_progress"
    assert len(port.calls) == 3
    assert audit["self_check_session"]["pending"]["rule_ids"] == ["scope"]


def test_fake_final_json_or_duplicated_tool_ids_cannot_pass():
    for assistant in ({"role": "assistant", "content": '{"passed": true}'},
                      {"role": "assistant", "tool_calls": [turn(("finish_review", {}))["tool_calls"][0]] * 2}):
        audit = execute(AgentPort([assistant]))
        assert not audit["passed"] and audit["issue"] == "reasoning_agent_missing_or_invalid_tools"


def test_cancel_propagates_without_synthetic_failure_vote():
    from lib.contexts.workflows.domain.execution import WorkflowCancelled
    port = AgentPort([])
    def cancel():
        raise WorkflowCancelled()
    port.check_reasoning_cancel = cancel
    with pytest.raises(WorkflowCancelled):
        execute(port)
    assert not port.calls


def test_resume_replays_received_tool_turn_without_buying_it_again():
    from lib.contexts.workflows.domain.execution import WorkflowCancelled
    workspace = ReasoningInvestigation(RULES, CONTEXT, BODY)
    decision = turn(("record_check", {"check": check()}),
        *[("record_claim", {"claim": claim(a)}) for a in workspace.anchors])
    class ResumablePort(AgentPort):
        interrupted = True
        def __init__(self):
            super().__init__([decision], [decision])
            self.saved = {}
        def reasoning_tool_turn(self, key, *args):
            identity = json.dumps(key)
            if identity not in self.saved:
                self.saved[identity] = super().reasoning_tool_turn(key, *args)
            return deepcopy(self.saved[identity])
        def check_reasoning_cancel(self):
            if self.interrupted and len(self.calls) == 1:
                raise WorkflowCancelled()
    port = ResumablePort()
    with pytest.raises(WorkflowCancelled):
        execute(port)
    port.interrupted = False
    audit = execute(port)
    assert audit["passed"] and len(port.calls) == 2
    assert len(audit["self_check_session"]["trace"]) == 3


def test_source_reads_are_exact_bounded_and_failed_search_does_not_reject_candidate():
    w = ReasoningInvestigation(RULES, CONTEXT, BODY)
    assert w.execute("read_source", {"source_id": "source.text", "start": 2, "length": 3})["text"] == "175"
    assert not w.execute("read_source", {"source_id": "source.text", "start": 0, "length": 10001})["ok"]
    assert not w.execute("read_source", {"source_id": "../secret", "start": 0, "length": 10})["ok"]
    assert w.execute("search_sources", {"query": "找不到的内容"})["hits"] == []
    assert not w.snapshot()["passed"] and not w.snapshot()["failed"]


def test_same_strict_calculation_rejects_unit_substitution():
    body = {"reasoning": "分数由10到20，增加10个百分点。"}
    w = ReasoningInvestigation([], {"source": {"text": "分数10和20，原文不是比例。"}}, body)
    calc = {"operation": "difference", "baseline": "10", "value": "20", "reported": "10",
            "reported_quote": "10个百分点", "metric_kind": "score", "operand_scale": "native", "reported_unit": "percentage_point"}
    receipt = w.execute("calculate_claim", {"anchor_id": w.anchors[0]["id"], "calculation": calc, "evidence": []})
    assert not receipt["receipt"]["passed"] and receipt["receipt"]["issue"] == "calculation_unit_mismatch"
    assert not w.snapshot()["passed"]  # Calling a tool never submits a positive verdict.


def test_versioned_config_and_editable_prompt_leave_old_snapshots_unchanged():
    from lib.domain.workflow_node_prompts import snapshot_node_prompts, active_node_prompt_ids
    from lib.domain.workflow_generation import validate_node_generation
    from lib.domain.creation_draft import validate_creation_draft
    config = checklist_from_text("核对范围。", version=3)
    assert validate_node_generation({"cot": {"self_check": config}})["cot"]["self_check"] == config
    old = snapshot_node_prompts({}, recipe_version=24)
    assert "workflow.cot_check_agent" not in old["cot"]
    custom = "按当前任务自主选择核查工具。"
    new = snapshot_node_prompts({"cot": {"workflow.cot_check_agent": custom}}, recipe_version=25)
    assert new["cot"]["workflow.cot_check_agent"] == custom
    ids = active_node_prompt_ids("cot", "文档", node_generation={"cot": {"enabled": True, "self_check": config}})
    assert "workflow.cot_check_agent" in ids and "workflow.cot_claim_check" not in ids
    assert validate_creation_draft({"workflow-cot-checklist-version:default": 3})


def test_native_adapter_reuses_checkpoint_but_changes_with_frozen_prompt(tmp_path):
    class Client:
        def __init__(self): self.usage, self.calls = {}, []
        def tool_turn(self, messages, tools, **kwargs):
            self.calls.append((messages, tools, kwargs))
            return turn(("finish_review", {}))
    client, cache = Client(), {}
    def checkpoint(key, invoke):
        encoded = json.dumps(key)
        if encoded not in cache: cache[encoded] = invoke()
        return deepcopy(cache[encoded])
    runtime = SimpleNamespace(client=lambda *a, **k: client, _client_locks={id(client): Lock()},
        _lock=Lock(), check_cancel=lambda: None, event=lambda *a, **k: None,
        state={}, path=tmp_path, stage="cot", recipe={"node_models": {"cot": {"jev": {"max_output_tokens": 33000}}}},
        prompt_text=lambda *a, **k: "frozen custom prompt", checkpoint=checkpoint)
    adapter = CheckpointedReasoningAgent(runtime)
    adapter.reasoning_tool_turn(["one"], "jev", [], [])
    adapter.reasoning_tool_turn(["one"], "jev", [], [])
    assert len(client.calls) == 1 and client.calls[0][2]["max_tokens"] == 33000
    assert client.calls[0][0][0]["role"] == "system"
    runtime.prompt_text = lambda *a, **k: "changed custom prompt"
    adapter.reasoning_tool_turn(["one"], "jev", [], [])
    assert len(client.calls) == 2


@pytest.mark.parametrize("selected", [False, True])
def test_agent_resolves_verified_pack_and_honors_director_selected_lines(monkeypatch, tmp_path, selected):
    from test_source_line_ranges import pack_fixture, range_for
    pack, ref, loader = pack_fixture()
    monkeypatch.setattr("lib.infrastructure.workflow_source_context.load_context_pack", loader)
    context = {"source_context": {"id": "chapter", "source_context_pack": ref,
        "text": "STALE_FOCUS_SENTINEL", "document_reading": {"source_text": "RAW_READING_SENTINEL"}},
        "feedback": {"source_context_pack": {"id": "not-a-source"}},
        "prompt": [{"role": "user", "content": "Which pressure applies?"}]}
    if selected:
        context.update(source_line_scope="selected", source_line_ranges=[range_for()])
    original = deepcopy(context)
    adapter = CheckpointedReasoningAgent(SimpleNamespace(path=tmp_path, recipe={}))
    prepared = adapter.prepare_reasoning_context(context)
    workspace = ReasoningInvestigation(RULES, prepared, BODY)
    sources = {p["id"]: p["text"] for p in workspace.passages}
    field = "text" if selected else "numbered_text"
    text = sources[f"source.materials[0].{field}"]
    if selected:
        assert text == "Condition: closed valve.\nPressure: 45 kPa.\n"
        assert "OUTSIDE_SENTINEL" not in json.dumps(prepared)
    else:
        assert text == pack["materials"][0]["numbered_text"]
        assert "OUTSIDE_SENTINEL" in text  # A full-pack task keeps the full source.
    assert "STALE_FOCUS_SENTINEL" not in json.dumps(prepared)
    assert "RAW_READING_SENTINEL" not in json.dumps(prepared)
    assert context == original and "feedback" not in prepared
    if selected:
        context["source_line_ranges"] = [range_for(end=200)]
        with pytest.raises(ValueError, match="out_of_bounds"):
            adapter.prepare_reasoning_context(context)


def test_incomplete_agent_does_not_buy_an_unnecessary_candidate_rewrite(monkeypatch):
    from lib.contexts.workflows.application.generate_cot import GenerateCot
    from test_generation_node_use_cases import MemoryGenerationPort, conversation, REASONING, ANSWER
    monkeypatch.setattr(CheckReasoning, "execute", lambda *a, **k: {
        "version": 3, "passed": False, "issue": "reasoning_agent_no_progress"})
    port = MemoryGenerationPort([{"reasoning": REASONING, "answer": ANSWER}])
    recipe = {"review_repair": {}, "node_generation": {"cot": {"self_check": {"version": 3, "rules": RULES}}}}
    row, = GenerateCot(port, recipe).generate_styled(conversation(), {"instruction": "仔细解释。"})
    assert row["status"] == "quarantined" and row["reason"] == "reasoning_agent_incomplete"
    assert row["reasoning"] == [REASONING] and row["answer"] == ANSWER
    assert len(port.calls) == 1


def test_followup_repair_gets_defects_not_source_and_tool_history():
    from lib.contexts.workflows.domain.reasoning_investigation import investigation_feedback
    port = AgentPort([turn(("record_check", {"check": check("unmet")}))])
    audit = execute(port)
    frozen = deepcopy(audit)
    feedback = investigation_feedback({"reasoning_checklist": audit})
    assert feedback["reasoning_checklist"]["failed_checks"] == [check("unmet")]
    assert "messages" not in json.dumps(feedback) and "trace" not in json.dumps(feedback)
    assert audit == frozen


def test_review_node_waits_on_incomplete_agent_instead_of_repairing_good_body():
    from lib.contexts.review.application.review_candidate import ReviewCandidate
    from test_pipeline_use_cases import ReviewMemory, grade
    class Port(ReviewMemory):
        def score(self, *args): return grade(True)
        def check_reasoning(self, *args):
            return {"version": 3, "passed": False, "issue": "reasoning_agent_no_progress"}
        def repair(self, *args, **kwargs): raise AssertionError("No evidence for rewriting")
    row = {"id": "s", "status": "eligible", "question": [{"role": "user", "content": "有多少任务？"}],
           "reasoning": [BODY["reasoning"]], "answer": BODY["answer"]}
    result, = ReviewCandidate(Port(), {"review_repair": {"mode": "auto", "max_rounds": 2},
        "node_generation": {"cot": {"self_check": {"version": 3, "rules": RULES}}}}).execute(
            {"target": "cot", "record": row})
    assert result["reason"] == "reasoning_agent_incomplete"
    receipt = result["review_repair"]
    assert receipt["attempts"] == 0 and result["reasoning"] == row["reasoning"]


def test_old_incomplete_audit_does_not_block_repair_of_an_established_defect():
    from lib.contexts.review.application.review_candidate import ReviewCandidate
    from test_pipeline_use_cases import ReviewMemory, review_input
    class Port(ReviewMemory):
        def check_reasoning(self, *args): return {"version": 3, "passed": True}
        def independent_cot_assessment(self, *args, **kwargs): return {}
        def payload(self, target, row):
            return {k: row[k] for k in ("question", "reasoning", "answer")}
        def repair(self, target, row, *args):
            self.repairs += 1
            return {**row, "answer": "Equality does not show improvement.",
                "reasoning": ["Improvement requires a positive difference. Equality has zero difference."]}, None
    item = review_input()
    item["target"] = "cot"
    messages = item["record"].pop("messages")
    item["record"].update(question=messages[:-1], reasoning=[messages[-1]["reasoning_content"]],
                           answer=messages[-1]["content"])
    item["record"]["reasoning_checklist"] = {
        "version": 3, "passed": False, "issue": "reasoning_agent_no_progress"}
    port = Port()
    result, = ReviewCandidate(port, {"review_repair": {"mode": "auto", "max_rounds": 2},
        "node_generation": {"cot": {"self_check": {"version": 3, "rules": RULES}}}}).execute(item)
    assert port.repairs == 1 and port.scores == 2
    assert result["status"] == "eligible"
    assert result["answer"] == "Equality does not show improvement."
