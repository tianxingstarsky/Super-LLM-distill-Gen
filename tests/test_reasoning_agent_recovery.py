"""Real failure shapes: malformed tools, endless retries, denied numbers and waived rules."""
from copy import deepcopy
import json

import pytest

from lib.contexts.workflows.application.check_reasoning import CheckReasoning
from lib.contexts.workflows.domain.reasoning_evidence import ReasoningEvidence
from lib.contexts.workflows.domain.reasoning_tool_contracts import evidence_tools, tool_input
from test_reasoning_investigation import AgentPort, BODY, CONTEXT, RULES, check, claim, turn


def checked(status="met", observation="satisfied"):
    entry = check(status)
    return {"check": entry, "observations": [{"requirement_quote": RULES[0]["requirement"],
            "status": observation, "evidence": deepcopy(entry["evidence"]), "reason": entry["reason"]}]}


def claimed(anchor, status="supported", stance="asserted"):
    return {"claim": claim(anchor, status), "interpretation": {"stance": stance,
            "context_quote": anchor["quote"], "explanation": "The author asserts this source-backed count."}}


def execute(port):
    return CheckReasoning(port, {"version": 4, "rules": RULES}).execute(["sample"], CONTEXT, BODY, phase="final")


def batch():
    w = ReasoningEvidence(RULES, CONTEXT, BODY)
    return {"checks": [checked()], "claims": [claimed(a) for a in w.anchors]}


def test_complete_schema_and_batch_reduce_turns_without_removing_any_checks():
    from jsonschema import Draft202012Validator
    for tool in evidence_tools():
        Draft202012Validator.check_schema(tool["function"]["parameters"])
    port = AgentPort([turn(("submit_review", batch()))], [turn(("submit_review", batch()))])
    audit = execute(port)
    assert audit["passed"] and len(port.calls) == 2
    assert audit["self_check"]["checks"] == [check()]
    assert len(audit["independent_check"]["claims"]) == 2
    inputs = json.loads(port.calls[0][2][0]["content"])
    assert inputs["provided_sources"] == [{"id": "source.text", "text": CONTEXT["source"]["text"]}]
    assert len(port.calls[1][2]) == 1  # independent, no author's positive opinions
    assert inputs["candidate"] == BODY


def test_bad_sibling_keeps_valid_evidence_and_only_failed_entry_is_resubmitted():
    initial = batch()
    bad = initial["claims"][0]
    bad["claim"].pop("no_calculation_reason")
    port = AgentPort([turn(("submit_review", initial)),
                      turn(("record_claim", batch()["claims"][0]))], [turn(("submit_review", batch()))])
    audit = execute(port)
    assert audit["passed"] and len(port.calls) == 3
    feedback = audit["self_check_session"]["trace"][0]["observation"]
    assert len(feedback["pending"]["anchor_ids"]) == 1 and not feedback["pending"]["rule_ids"]
    errors = feedback["entries"][1]["result"]["recovery"]["details"]
    assert errors[0]["path"] == "$" and "no_calculation_reason" in errors[0]["message"]


def test_lossless_envelope_repair_cannot_change_votes_values_or_drop_unknown_fields():
    entry = batch()["claims"][0]
    flat = {**entry["claim"], "interpretation": entry["interpretation"]}
    fixed, repairs, errors = tool_input("record_claim", flat)
    assert fixed == entry and repairs and not errors
    assert ReasoningEvidence(RULES, CONTEXT, BODY).execute("record_claim", flat)["ok"]
    _, _, errors = tool_input("record_claim", {**flat, "invented_permission": "pass"})
    assert errors


def test_missing_or_fuzzy_quote_is_not_repaired_into_approval():
    entry = batch()["claims"][0]
    entry["claim"]["evidence"][0]["quote"] += " invented words"
    w = ReasoningEvidence(RULES, CONTEXT, BODY)
    result = w.execute("record_claim", entry)
    assert not result["ok"] and result["issue"] == "claim_unbound_evidence"
    assert result["recovery"]["details"][0]["path"] == "$.evidence[0]"
    assert not w.claims and not w.snapshot()["failed"]


def test_self_reported_gap_cannot_be_waived_by_positive_summary_or_later_vote():
    entry = checked("met", "violated")
    entry["observations"][0]["reason"] = "English was required, but the answer is Chinese; technical meaning is still correct."
    w = ReasoningEvidence(RULES, CONTEXT, BODY)
    result = w.execute("record_check", entry)
    assert result["summary_conflict"] and result["failed"] and not result["passed"]
    assert w.checks["scope"]["status"] == "unmet"
    assert not w.execute("record_check", checked())["ok"]
    assert w.snapshot()["failed"]
    audit = execute(AgentPort([turn(("record_check", entry))], [turn(("record_check", entry))]))
    assert audit["issue"] == "reasoning_agent_found_gap"


def test_conflicting_semantic_votes_are_preserved_not_used_for_unjustified_rewrite():
    port = AgentPort([turn(("record_check", checked("unmet", "violated")))], [turn(("submit_review", batch()))])
    audit = execute(port)
    assert not audit["passed"] and audit["issue"] == "reasoning_agent_review_disagreement"
    assert audit["self_check"]["checks"][0]["status"] == "unmet"
    assert audit["independent_check_session"]["passed"]
    assert len(port.calls) == 2


def test_independent_negative_is_not_silently_discarded_or_resampled():
    port = AgentPort([turn(("submit_review", batch()))], [turn(("record_check", checked("unmet", "violated")))])
    audit = execute(port)
    assert audit["issue"] == "reasoning_agent_review_disagreement" and not audit["passed"]
    assert audit["independent_check"]["checks"][0]["status"] == "unmet"
    assert len(port.calls) == 2


def test_unknown_is_blocked_not_a_forced_body_rewrite_or_approval():
    audit = execute(AgentPort([turn(("record_check", checked("met", "unknown")))]))
    assert audit["issue"] == "reasoning_agent_evidence_unresolved"
    assert audit["self_check_session"]["blocked"] and not audit["self_check_session"]["failed"]


def test_valid_negative_observation_survives_later_invalid_sibling():
    w = ReasoningEvidence(RULES, CONTEXT, BODY)
    entry = checked("met", "violated")
    entry["observations"].append({**deepcopy(entry["observations"][0]), "requirement_quote": "Invented requirement"})
    response = w.execute("record_check", entry)
    assert not response["ok"] and w.snapshot()["failed"]
    w.execute("record_check", checked())
    assert w.snapshot()["failed"] and not w.snapshot()["passed"]


def test_recovery_names_failed_rule_and_committed_record_instead_of_ambiguous_batch_index():
    rule = {**RULES[0], "allow_na": False}
    w = ReasoningEvidence([rule], CONTEXT, BODY)
    entry = checked("not_applicable", "not_applicable")
    result = w.execute("submit_review", {"checks": [entry], "claims": []})
    failure = result["entries"][0]["result"]
    assert failure["target"] == {"rule_id": "scope"}
    assert failure["recovery"]["target"]["definition"] == rule
    assert failure["recovery"]["target"]["committed_record"] is None
    assert w.execute("record_check", checked())["ok"]
    duplicate = w.execute("record_check", checked("unmet", "violated"))
    assert duplicate["issue"] == "evidence_already_committed"
    assert duplicate["recovery"]["target"]["committed_record"] == check()
    assert duplicate["pending"]["rule_ids"] == []


def test_observations_cannot_drop_task_language_or_invent_a_new_requirement():
    rule = {"id": "scope", "requirement": "Check accuracy and answer in English.", "allow_na": False}
    w = ReasoningEvidence([rule], CONTEXT, BODY)
    entry = checked()
    entry["observations"][0]["requirement_quote"] = "Check accuracy"
    assert w.execute("record_check", entry)["issue"] == "observation_requirement_coverage"
    entry["observations"][0]["requirement_quote"] = "Language does not matter"
    assert w.execute("record_check", entry)["issue"] == "observation_requirement_unbound"
    entry["observations"][0].update(requirement_quote=rule["requirement"], status="not_applicable")
    assert w.execute("record_check", entry)["issue"] == "checklist_required_rule_waived"


def test_denied_percentage_is_a_binding_error_not_an_arithmetic_defect():
    body = {"reasoning": "The difference is 33.1 score points. It is not a relative increase of 33.1%."}
    w = ReasoningEvidence([], {"source": "Scores were 6.8 and 39.9."}, body)
    a = w.anchors[1]
    entry = claimed(a)
    entry["claim"].update(evidence=[{"source": "source", "quote": "Scores were 6.8 and 39.9."}],
        calculations=[{"operation": "relative_change", "baseline": "6.8", "value": "39.9", "reported": "33.1",
            "reported_quote": "33.1%", "metric_kind": "score", "operand_scale": "native", "reported_unit": "percent"}])
    entry["interpretation"]["stance"] = "negated"
    result = w.execute("record_claim", entry)
    assert result["issue"] == "claim_stance_conflict" and not w.snapshot()["failed"]
    entry["claim"].update(status="not_claim", calculations=[], no_calculation_reason="The author explicitly rejects this relative percentage.")
    assert w.execute("record_claim", entry)["ok"]
    assert not w.snapshot()["passed"]  # the positive score-point claim still needs checking


def test_actual_unit_error_remains_a_terminal_negative_not_a_format_retry():
    body = {"reasoning": "Score rose from 6.8 to 39.9, an increase of 33.1 percentage points."}
    w = ReasoningEvidence([], {}, body)
    entry = claimed(w.anchors[0])
    entry["claim"].update(evidence=[{"source": "reasoning", "quote": body["reasoning"]}],
        calculations=[{"operation": "difference", "baseline": "6.8", "value": "39.9", "reported": "33.1",
            "reported_quote": "33.1 percentage points", "metric_kind": "score", "operand_scale": "native", "reported_unit": "percentage_point"}])
    assert w.execute("record_claim", entry)["failed"]
    assert not w.snapshot()["passed"]


def test_reviewer_operand_scale_error_is_recoverable_but_does_not_approve_candidate():
    body = {"reasoning": "There are 25 classification tasks out of 175, about 14.3%."}
    w = ReasoningEvidence([], {}, body)
    entry = claimed(w.anchors[0])
    entry["claim"].update(evidence=[{"source":"reasoning", "quote":body["reasoning"]}],
        calculations=[{"operation":"percent_of", "baseline":"175", "value":"25", "reported":"14.3",
            "reported_quote":"14.3%", "metric_kind":"proportion", "operand_scale":"native", "reported_unit":"percent"}])
    failure = w.execute("record_claim", entry)
    assert failure["issue"] == "calculation_scale_mismatch"
    assert not w.snapshot()["failed"] and not w.snapshot()["passed"] and not w.claims
    assert failure["recovery"]["details"][0]["path"] == "$.calculations[0]"
    entry["claim"]["calculations"][0]["metric_kind"] = "quantity"
    assert w.execute("record_claim", entry)["ok"] and w.snapshot()["passed"]
    assert w.candidate == body


def test_changed_rule_word_gets_exact_diff_without_autocorrection_or_approval():
    rule = {"id":"scope", "requirement":"未报告变成未做", "allow_na":False}
    w = ReasoningEvidence([rule], CONTEXT, BODY)
    entry = checked()
    entry["observations"][0]["requirement_quote"] = "未报告变成没做"
    response = w.execute("record_check", entry)
    detail = response["recovery"]["details"][0]
    assert detail["differences"] == [{"operation":"replace", "submitted":"没", "expected":"未"}]
    assert not w.checks and not w.snapshot()["passed"]


def test_repeated_error_target_is_detected_across_changing_calls_and_batches():
    requests = []
    for n in range(3):
        entry = batch()["claims"][0]
        entry["claim"]["reason"] = f"Different wording {n} cannot fix a missing field."
        entry["claim"].pop("no_calculation_reason")
        requests.append(turn(("submit_review", {"checks": [], "claims": [entry]}), ("search_sources", {"query": f"absent {n}"})))
    port = AgentPort(requests)
    audit = execute(port)
    assert len(port.calls) == 3 and audit["issue"] == "reasoning_agent_no_progress"
    assert audit["self_check_session"]["stop_receipt"]["reason"] == "repeated_tool_error"
    assert not audit["self_check_session"]["failed"]


def test_repeated_no_hit_searches_are_not_progress_even_with_different_queries():
    port = AgentPort([turn(("search_sources", {"query": f"not present {i}"})) for i in range(4)])
    audit = execute(port)
    assert len(port.calls) == 4 and audit["issue"] == "reasoning_agent_no_progress"


def test_v4_does_not_reuse_v3_prompt_or_checkpoint_and_v3_remains_available():
    from lib.domain.workflow_node_prompts import snapshot_node_prompts
    from lib.prompts import get, render
    assert snapshot_node_prompts({}, recipe_version=25)["cot"]["workflow.cot_check_agent"] == render(get("workflow.cot_check_agent", version="1.0.0"))
    assert snapshot_node_prompts({}, recipe_version=26)["cot"]["workflow.cot_check_agent"] == render(get("workflow.cot_check_agent", version="1.2.0"))
    port = AgentPort([turn(("submit_review", batch()))], [turn(("submit_review", batch()))])
    execute(port)
    assert "investigation_v2" in port.calls[0][0]


def test_malformed_chat_json_preserves_original_and_thought_for_tool_feedback():
    from lib.contexts.models.infrastructure.tool_transport import _assistant, _history
    raw = '{"check"::"bad"}'
    response = {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "reasoning_content": "complete unchanged thought",
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "record_check", "arguments": raw}}]}}]}
    with pytest.raises(ValueError, match="invalid_model_tool_arguments"):
        _assistant(response, "chat", {"record_check"})
    result = _assistant(response, "chat", {"record_check"}, recover_arguments=True)
    assert result["reasoning_content"] == "complete unchanged thought"
    assert result["tool_argument_errors"][0]["original_arguments"] == raw
    args = json.loads(result["tool_calls"][0]["function"]["arguments"])
    assert not ReasoningEvidence(RULES, CONTEXT, BODY).execute("record_check", args)["ok"]
    _history([{"role": "user", "content": "audit"}, result,
              {"role": "tool", "tool_call_id": "c1", "content": "Malformed JSON; correct arguments."}], "chat")


def test_tool_output_limit_is_not_a_retryable_connection_failure():
    from lib.contexts.models.infrastructure.tool_transport import _assistant
    from lib.contexts.models.domain.request_errors import ModelOutputLimitError
    response = {"choices": [{"finish_reason": "length", "message": {"content": "", "reasoning_content": "partial thought"}}]}
    with pytest.raises(ModelOutputLimitError):
        _assistant(response, "chat", {"record_check"}, recover_arguments=True)


def test_invalid_arguments_recover_through_real_sdk_without_rebuying_original_request():
    import httpx
    from test_model_tool_calls import sdk_client, reply, TOOLS, MESSAGES, THOUGHT
    captured = []
    def handle(request):
        captured.append(json.loads(request.content))
        response = reply("chat")
        if len(captured) == 1:
            response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = '{"start"::1}'
        return httpx.Response(200, json=response)
    client = sdk_client("chat", handle)
    try:
        first = client.tool_turn(MESSAGES, TOOLS, max_tokens=128, recover_arguments=True)
        assert len(captured) == 1 and client.usage["calls"] == 1
        assert first["reasoning_content"] == THOUGHT and first["tool_argument_errors"]
        second = client.tool_turn([*MESSAGES, first,
            {"role": "tool", "tool_call_id": "call-1", "content": "Invalid JSON at start: remove the extra colon. No tool executed."}],
            TOOLS, max_tokens=128, recover_arguments=True)
        assert second["tool_calls"][0]["function"]["arguments"] == '{"start":1}'
        assert len(captured) == 2 and client.usage["calls"] == 2
        assert captured[1]["messages"][2]["reasoning_content"] == THOUGHT
    finally:
        client.client.close()


def test_flat_batch_matches_advertised_schema_and_preserves_every_evidence_field():
    from jsonschema import Draft202012Validator
    entries = batch()
    for field, wrapper in (("checks", "check"), ("claims", "claim")):
        entries[field] = [{**entry[wrapper], **{k:v for k,v in entry.items() if k != wrapper}} for entry in entries[field]]
    schema = evidence_tools()[0]["function"]["parameters"]
    Draft202012Validator(schema).validate(entries)
    audit = execute(AgentPort([turn(("submit_review", entries))], [turn(("submit_review", entries))]))
    assert audit["passed"] and audit["self_check"]["checks"] == [check()]


def test_real_malformed_batches_return_precise_feedback_before_local_recovery():
    from pathlib import Path
    from lib.contexts.models.infrastructure.tool_transport import _assistant
    class RecoveryPort(AgentPort):
        def reasoning_tool_turn(self, *args):
            result = super().reasoning_tool_turn(*args)
            for error in result.get("tool_argument_errors", []):
                error["call_id"] = f"{len(self.calls)}-{error['call_id']}"
            return result
    fixtures = json.loads((Path(__file__).parent / "fixtures/reasoning_agent/malformed_calls.json").read_text(encoding="utf8"))
    assert len(fixtures) >= 5
    for fixture in fixtures:
        raw = fixture["arguments"]
        with pytest.raises(json.JSONDecodeError) as caught:
            json.loads(raw)
        response = {"choices":[{"finish_reason":"tool_calls", "message":{
            "content":"", "reasoning_content":"All native thought retained.", "tool_calls":[{
                "id":"broken", "type":"function", "function":{"name":"submit_review","arguments":raw}}]}}]}
        damaged = _assistant(response, "chat", {"submit_review"}, recover_arguments=True)
        port = RecoveryPort([damaged, turn(("submit_review", batch()))], [turn(("submit_review", batch()))])
        audit = execute(port)
        trace = audit["self_check_session"]["trace"]
        assert audit["passed"] and len(port.calls) == 3
        assert trace[0]["observation"]["issue"] == "invalid_tool_json"
        detail = trace[0]["observation"]["recovery"]["details"][0]
        assert detail["position"] == caught.value.pos and detail["column"] == caught.value.colno
        assert port.calls[1][2][1]["tool_argument_errors"][0]["original_arguments"] == raw
        assert port.calls[1][2][1]["reasoning_content"] == "All native thought retained."
        assert trace[0]["observation"]["pending"]["rule_ids"] == ["scope"]
