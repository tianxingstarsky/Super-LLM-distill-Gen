"""Recorded native prefix, existing feedback and one beta continuation offline."""
from copy import deepcopy
import hashlib
import json

import pytest

from lib.contexts.workflows.application.continue_native_feedback import ContinueNativeFeedback
from lib.contexts.workflows.domain.native_feedback_provenance import authenticate_native_feedback_receipt
from lib.contexts.workflows.domain.native_prefix_feedback import (
    DEFAULT_NATIVE_REFLECTION_SEED, NATIVE_PREFIX_PARENT_RECEIPT,
    complete_native_prefix_feedback, prepare_native_prefix_finalization,
)
from lib.contexts.workflows.domain.review_guidance import review_guidance_message
from lib.contexts.workflows.domain.native_prefix_feedback import native_feedback_guidance_version


@pytest.mark.parametrize("pins,expected", [
    (None, 7),
    ({"multiturn": {"generation": {"endpoint_origin": "https://api.deepseek.com", "api_format": "chat"}}}, 8),
    ({"multiturn": {"generation": {"endpoint_origin": "https://api.deepseek.com", "api_format": "responses"}}}, 7),
    ({"multiturn": {"generation": {"endpoint_origin": "https://proxy.example", "api_format": "chat"}}}, 7),
])
def test_new_native_tasks_freeze_beta_only_for_known_official_chat_authors(pins, expected):
    nodes = {"multiturn": {"enabled": True, "answer_mode": "native_joint"}}
    assert native_feedback_guidance_version(nodes, pins) == expected
    assert native_feedback_guidance_version({}, pins) == 7
    assert native_feedback_guidance_version({**nodes, "sft": nodes["multiturn"]}, pins) == 7


def source():
    session = {"version": 1, "author": "same-frozen-author", "role": "generation", "stage": "multiturn",
        "request_options": {"max_tokens": 32768, "temperature": .7}, "tools": [],
        "messages": [{"role": "system", "content": "Use only the established task and evidence."},
            {"role": "user", "content": "我只有一页节选，能说明整篇都没有这个内容吗？"}]}
    assistant = {"role": "assistant", "content": "整篇都没有这个内容。",
        "reasoning_content": " \n我没有在这一页找到，便判断全文没有。\n "}
    findings = [{"quote": "全文没有", "issue": "一页节选不能证明全文都没有，应限定为已见的一页。",
        "source_ref": {"kind": "saved_model_review", "id": "existing-review"}, "disposition": "confirmed"}]
    return session, assistant, findings


class PrefixPort:
    def __init__(self, *, mutation=None):
        self.calls, self.cache, self.mutation = [], {}, mutation

    def check_reasoning_cancel(self):
        pass

    def turn(self, *args, **kwargs):
        pytest.fail("beta repair must not ask for a tool decision")

    def continue_prefix(self, key, session, pending_messages, *, reasoning_prefix):
        identity = json.dumps(key, sort_keys=True)
        if identity not in self.cache:
            self.calls.append(deepcopy((key, session, pending_messages, reasoning_prefix)))
            raw = {"role": "assistant", "content": "只能说这一页节选未见，不能断言全文没有。",
                "reasoning_content": "\n等等，这个推断超过了已检查范围。全文情况仍然未知。"}
            result = {"assistant": raw, "response_details": {"finish_reason": "stop",
                "reasoning_content": raw["reasoning_content"], "reasoning_prefill": {
                    "version": 3, "transport": "deepseek_chat_beta", "visible_prefix": "",
                    "continuation_mode": "reasoning", "prefix_sha256": hashlib.sha256(reasoning_prefix.encode()).hexdigest(),
                    "prefix_utf8_bytes": len(reasoning_prefix.encode()), "response_reasoning": "provider_continuation"}}}
            if self.mutation:
                self.mutation(result)
            self.cache[identity] = deepcopy(result)
        return deepcopy(self.cache[identity])


def test_beta_reuses_exact_prefix_existing_feedback_and_one_durable_request():
    session, assistant, findings = source()
    original = deepcopy((session, assistant, findings))
    port = PrefixPort()
    usecase = ContinueNativeFeedback(port, guidance_version=8)
    value, receipt = usecase.execute(["same"], session, assistant, findings=findings)
    again = usecase.execute(["same"], session, assistant, findings=findings)
    assert again == (value, receipt) and len(port.calls) == 1
    assert (session, assistant, findings) == original
    _, passed_session, pending, prefix = port.calls[0]
    assert passed_session == session and prefix == assistant["reasoning_content"]
    assert pending[:-1] == session["messages"] and pending[-1]["role"] == "system"
    assert findings[0]["issue"] in pending[-1]["content"]
    assert [message["role"] for message in pending] == ["system", "user", "system"]
    assert assistant["content"] not in json.dumps(pending, ensure_ascii=False)
    suffix = receipt["transcript"][0]["response"]["assistant"]["reasoning_content"]
    assert value["reasoning"] == prefix + suffix
    assert receipt["revised_assistant"]["reasoning_content"] == value["reasoning"]
    assert receipt["reasoning_segments"] == [
        {"phase": "supplied_prefix", "origin": "original_provider_reasoning_content", "text": prefix},
        {"phase": "continue_answer", "origin": "provider_reasoning_content", "text": suffix}]
    assert receipt["request_messages"] == [*pending,
        {"role": "assistant", "content": "", "prefix": True, "reasoning_content": prefix}]
    assert receipt["semantic_status"] == "not_assessed" and receipt["independent_review"] is False
    authenticated = authenticate_native_feedback_receipt(receipt)
    assert authenticated["value"] == value
    assert review_guidance_message(receipt) == pending[-1]


@pytest.mark.parametrize("change", ["tool_definition", "tool_history", "function_history", "prefix_history", "unbound_review"])
def test_inapplicable_source_stops_before_any_request(change):
    session, assistant, findings = source()
    if change == "tool_definition":
        session["tools"] = [{"type": "function", "function": {"name": "real_tool"}}]
    elif change == "tool_history":
        session["messages"].insert(1, {"role": "tool", "tool_call_id": "recorded", "content": "evidence"})
    elif change == "function_history":
        session["messages"][0]["function_call"] = {"name": "recorded"}
    elif change == "prefix_history":
        session["messages"][0]["prefix"] = True
    else:
        findings[0]["quote"] = "This did not occur in the original response."
    port = PrefixPort()
    value, receipt = ContinueNativeFeedback(port, guidance_version=8).execute([], session, assistant, findings=findings)
    assert value is None and receipt["status"] == "incomplete" and receipt["model_turns"] == 0
    assert receipt["issue"] and not port.calls


@pytest.mark.parametrize("change", ["prefix", "suffix", "note", "origin", "calls", "quality", "request", "value"])
def test_complete_receipt_rejects_tampered_provenance(change):
    session, assistant, findings = source()
    _, receipt = ContinueNativeFeedback(PrefixPort(), guidance_version=8).execute([], session, assistant, findings=findings)
    if change == "prefix":
        receipt["reasoning_prefix"] += "changed"
    elif change == "suffix":
        receipt["reasoning_segments"][1]["text"] += "changed"
    elif change == "note":
        receipt["guidance_message"]["content"] += "changed"
    elif change == "origin":
        receipt["continuation_transport"] = "ordinary_chat"
    elif change == "calls":
        receipt["model_turns"] = 2
    elif change == "quality":
        receipt["semantic_status"] = "passed"
    elif change == "request":
        receipt["request_messages"][-1]["content"] = "\n"
    else:
        receipt["revised_assistant"]["reasoning_content"] = "replaced entire trace"
    with pytest.raises(ValueError, match="native_feedback_receipt_provenance_mismatch"):
        authenticate_native_feedback_receipt(receipt)


@pytest.mark.parametrize("change,issue", [
    (lambda r: r["assistant"].update(reasoning_content=""), "native_feedback_reasoning_missing"),
    (lambda r: r["assistant"].update(content=""), "native_feedback_answer_missing"),
    (lambda r: r["response_details"].update(finish_reason="length"), "native_feedback_response_incomplete"),
    (lambda r: r["response_details"]["reasoning_prefill"].update(continuation_mode="answer"), "native_prefix_transport_receipt_mismatch")])
def test_invalid_completion_retains_raw_response_without_retry(change, issue):
    session, assistant, findings = source()
    port = PrefixPort(mutation=change)
    value, receipt = ContinueNativeFeedback(port, guidance_version=8).execute([], session, assistant, findings=findings)
    assert value is None and receipt["issue"] == issue and receipt["model_turns"] == 1
    assert len(port.calls) == 1 and len(receipt["transcript"]) == 1
    assert receipt["original_assistant"] == assistant
    assert receipt["response_details"] == receipt["transcript"][0]["response"]["response_details"]
    assert receipt["reasoning_prefix"] == assistant["reasoning_content"]


def test_repeated_repair_uses_prior_composed_prefix_without_rewriting_raw_suffix():
    session, assistant, findings = source()
    _, first = ContinueNativeFeedback(PrefixPort(), guidance_version=8).execute([], session, assistant, findings=findings)
    parent = authenticate_native_feedback_receipt(first)
    next_findings = [{"quote": "只能说", "issue": "保留这个范围限制。", "source_ref": "saved-followup"}]
    port = PrefixPort()
    value, second = ContinueNativeFeedback(port, guidance_version=8).execute([], parent["continuation_session"],
        parent["revised_assistant"], findings=next_findings)
    assert value and len(port.calls) == 1
    assert port.calls[0][3] == parent["value"]["reasoning"]
    assert second["original_assistant"] == first["revised_assistant"]
    assert authenticate_native_feedback_receipt(second)["value"] == value


def test_optional_reflection_seed_is_non_native_exact_and_separate_from_original():
    session, assistant, findings = source()
    frozen = deepcopy(assistant)
    port = PrefixPort()
    seed = DEFAULT_NATIVE_REFLECTION_SEED
    usecase = ContinueNativeFeedback(port, guidance_version=8, prefix_reflection_seed=seed)
    value, receipt = usecase.execute(["same"], session, assistant, findings=findings)
    assert receipt["original_assistant"] == assistant == frozen
    assert port.calls[0][3] == receipt["reasoning_prefix"] == assistant["reasoning_content"] + seed
    assert receipt["reflection_seed"] == seed
    assert receipt["reasoning_segments"][0]["text"] == assistant["reasoning_content"]
    assert receipt["reasoning_segments"][1] == {
        "phase": "reflection_seed", "origin": "application_reflection_seed", "text": seed}
    suffix = receipt["reasoning_segments"][2]["text"]
    assert value["reasoning"] == assistant["reasoning_content"] + seed + suffix
    assert authenticate_native_feedback_receipt(receipt)["value"] == value
    assert usecase.execute(["same"], session, assistant, findings=findings) == (value, receipt)
    assert len(port.calls) == 1
    _, unseeded = ContinueNativeFeedback(port, guidance_version=8).execute(["same"], session, assistant, findings=findings)
    assert len(port.calls) == 2 and "reflection_seed" not in unseeded
    assert unseeded["request_messages"] != receipt["request_messages"]
    forged = deepcopy(receipt)
    forged["reflection_seed"] += " changed"
    with pytest.raises(ValueError, match="native_feedback_receipt_provenance_mismatch"):
        authenticate_native_feedback_receipt(forged)


def test_repeated_seeded_repair_authenticates_parent_and_preserves_all_segment_origins():
    session, assistant, findings = source()
    first_port = PrefixPort()
    _, first = ContinueNativeFeedback(first_port, guidance_version=8,
        prefix_reflection_seed=DEFAULT_NATIVE_REFLECTION_SEED).execute([], session, assistant, findings=findings)
    authenticated = authenticate_native_feedback_receipt(first)
    linked = {**authenticated["continuation_session"], NATIVE_PREFIX_PARENT_RECEIPT: deepcopy(first)}
    next_findings = [{"quote": "只能说", "issue": "保留范围限制。", "source_ref": "saved-followup"}]
    port = PrefixPort()
    value, second = ContinueNativeFeedback(port, guidance_version=8, prefix_reflection_seed="\n重新核对：").execute(
        [], linked, authenticated["revised_assistant"], findings=next_findings)
    assert value and len(port.calls) == 1
    assert second["reasoning_segments"][:3] == first["reasoning_segments"]
    assert second["reasoning_segments"][3]["origin"] == "application_reflection_seed"
    assert "".join(segment["text"] for segment in second["reasoning_segments"]) == value["reasoning"]
    assert NATIVE_PREFIX_PARENT_RECEIPT in second["original_session"]
    assert NATIVE_PREFIX_PARENT_RECEIPT not in second["continuation_session"]
    assert not any(NATIVE_PREFIX_PARENT_RECEIPT in message for message in second["request_messages"])
    assert authenticate_native_feedback_receipt(second)["value"] == value
    forged = deepcopy(linked)
    forged[NATIVE_PREFIX_PARENT_RECEIPT]["reasoning_segments"][1]["origin"] = "provider_reasoning_content"
    blocked, failure = ContinueNativeFeedback(port, guidance_version=8).execute(
        [], forged, authenticated["revised_assistant"], findings=next_findings)
    assert blocked is None and failure["model_turns"] == 0 and len(port.calls) == 1
    mismatched = deepcopy(linked)
    mismatched["messages"][0]["content"] += " changed source"
    blocked, failure = ContinueNativeFeedback(port, guidance_version=8).execute(
        [], mismatched, authenticated["revised_assistant"], findings=next_findings)
    assert blocked is None and failure["issue"] == "native_prefix_parent_candidate_mismatch"
    assert len(port.calls) == 1


@pytest.mark.parametrize("seed", [None, 1, "\ud800"])
def test_invalid_reflection_seed_stops_before_request(seed):
    session, assistant, findings = source()
    port = PrefixPort()
    value, receipt = ContinueNativeFeedback(port, guidance_version=8, prefix_reflection_seed=seed).execute(
        [], session, assistant, findings=findings)
    assert value is None and receipt["model_turns"] == 0 and not port.calls
    assert receipt["issue"] in {"native_prefix_invalid_reflection_seed", "native_prefix_invalid_reasoning"}


def reasoning_only_receipt():
    session, assistant, findings = source()
    port = PrefixPort(mutation=lambda response: response["assistant"].update(content=""))
    value, receipt = ContinueNativeFeedback(port, guidance_version=8,
        prefix_reflection_seed=DEFAULT_NATIVE_REFLECTION_SEED).execute([], session, assistant, findings=findings)
    assert value is None and len(port.calls) == 1
    return receipt


@pytest.mark.parametrize("final_reasoning", ["", "\n我还应保留未知条件。"])
def test_saved_reasoning_only_stop_has_explicit_authenticated_answer_phase(final_reasoning):
    incomplete = reasoning_only_receipt()
    frozen = deepcopy(incomplete)
    prepared = prepare_native_prefix_finalization(incomplete)
    raw_first = incomplete["transcript"][0]["response"]
    prefix = incomplete["reasoning_prefix"] + raw_first["assistant"]["reasoning_content"]
    assert prepared == {"session": incomplete["original_session"],
        "pending_messages": incomplete["request_messages"][:-1], "reasoning_prefix": prefix}
    final = {"assistant": {"role": "assistant", "content": "只能判断这一页节选，全文情况仍然未知。",
                          "reasoning_content": final_reasoning},
        "response_details": {"finish_reason": "stop", "reasoning_content": final_reasoning,
            "reasoning_prefill": {"version": 2, "transport": "deepseek_chat_beta", "visible_prefix": "\n",
                "prefix_sha256": hashlib.sha256(prefix.encode()).hexdigest(), "prefix_utf8_bytes": len(prefix.encode()),
                "response_reasoning": "provider_continuation"}}}
    value, receipt = complete_native_prefix_feedback(incomplete["original_session"], incomplete["original_assistant"],
        incomplete["findings"], raw_first, reflection_seed=incomplete["reflection_seed"], final_response=final)
    assert incomplete == frozen
    assert receipt["model_turns"] == 2 and receipt["finalization_reason"] == "completed_reasoning_without_answer"
    assert receipt["finalization_request_messages"][-1] == {
        "role": "assistant", "content": "\n", "prefix": True, "reasoning_content": prefix}
    assert [entry["phase"] for entry in receipt["transcript"]] == ["continue_answer", "finalize_answer"]
    assert receipt["transcript"][0]["response"] == raw_first
    assert receipt["reasoning_segments"][-1] == {
        "phase": "finalize_answer", "origin": "provider_reasoning_content", "text": final_reasoning}
    assert value["reasoning"] == prefix + final_reasoning
    assert receipt["response_details"] == final["response_details"]
    assert receipt["semantic_status"] == "not_assessed" and receipt["independent_review"] is False
    assert authenticate_native_feedback_receipt(receipt)["value"] == value
    forged = deepcopy(receipt)
    forged["finalization_request_messages"][-1]["reasoning_content"] += " changed"
    with pytest.raises(ValueError, match="native_feedback_receipt_provenance_mismatch"):
        authenticate_native_feedback_receipt(forged)


@pytest.mark.parametrize("change", ["truncated", "not_stop", "has_answer", "prefix_changed", "wrong_issue"])
def test_unfinished_or_changed_reasoning_cannot_buy_finalization(change):
    receipt = reasoning_only_receipt()
    response = receipt["transcript"][0]["response"]
    if change == "truncated":
        response["response_details"]["incomplete_response"] = True
    elif change == "not_stop":
        response["response_details"]["finish_reason"] = "length"
    elif change == "has_answer":
        response["assistant"]["content"] = "Already a visible answer."
    elif change == "prefix_changed":
        receipt["reasoning_prefix"] += "changed"
    else:
        receipt["issue"] = "native_feedback_reasoning_missing"
    with pytest.raises(ValueError, match="native_prefix_incomplete_receipt_mismatch"):
        prepare_native_prefix_finalization(receipt)


class FinalizingPrefixPort(PrefixPort):
    def __init__(self, *, first_mutation=None, final_mutation=None):
        def mutate_first(response):
            response["assistant"]["content"] = ""
            if first_mutation:
                first_mutation(response)
        super().__init__(mutation=mutate_first)
        self.final_calls, self.final_mutation = [], final_mutation

    def finalize_prefix(self, key, session, pending_messages, *, reasoning_prefix):
        identity = json.dumps(key, sort_keys=True)
        if identity not in self.cache:
            self.final_calls.append(deepcopy((key, session, pending_messages, reasoning_prefix)))
            result = {"assistant": {"role": "assistant", "content": "只能判断这一页，全文情况仍未知。",
                "reasoning_content": ""}, "response_details": {"finish_reason": "stop", "reasoning_content": "",
                    "reasoning_prefill": {"version": 2, "transport": "deepseek_chat_beta", "visible_prefix": "\n",
                        "prefix_sha256": hashlib.sha256(reasoning_prefix.encode()).hexdigest(),
                        "prefix_utf8_bytes": len(reasoning_prefix.encode()), "response_reasoning": "provider_continuation"}}}
            if self.final_mutation:
                self.final_mutation(result)
            self.cache[identity] = deepcopy(result)
        return deepcopy(self.cache[identity])


def test_optional_answer_phase_uses_saved_native_output_and_replays_exact_two_requests():
    session, assistant, findings = source()
    port = FinalizingPrefixPort()
    usecase = ContinueNativeFeedback(port, guidance_version=8, prefix_reflection_seed=DEFAULT_NATIVE_REFLECTION_SEED,
                                    finalize_empty_answer=True)
    value, receipt = usecase.execute(["same-case"], session, assistant, findings=findings)
    assert value and receipt["model_turns"] == 2
    assert len(port.calls) == len(port.final_calls) == 1
    first = receipt["transcript"][0]["response"]["assistant"]
    assert port.final_calls[0][3] == assistant["reasoning_content"] + DEFAULT_NATIVE_REFLECTION_SEED + first["reasoning_content"]
    assert port.final_calls[0][2] == port.calls[0][2]
    assert usecase.execute(["same-case"], session, assistant, findings=findings) == (value, receipt)
    assert len(port.calls) == len(port.final_calls) == 1
    assert receipt["semantic_status"] == "not_assessed" and receipt["independent_review"] is False


@pytest.mark.parametrize("first_failure", ["truncated", "empty_reasoning", "original_missing_answer"])
def test_optional_answer_phase_does_not_run_for_invalid_or_unfinished_source(first_failure):
    session, assistant, findings = source()
    if first_failure == "original_missing_answer":
        assistant["content"] = ""
    mutation = (lambda response: response["response_details"].update(finish_reason="length")) if first_failure == "truncated" else (
        (lambda response: response["assistant"].update(reasoning_content="")) if first_failure == "empty_reasoning" else None)
    port = FinalizingPrefixPort(first_mutation=mutation)
    value, receipt = ContinueNativeFeedback(port, guidance_version=8, finalize_empty_answer=True).execute(
        [], session, assistant, findings=findings)
    assert value is None and not port.final_calls
    assert len(port.calls) == (0 if first_failure == "original_missing_answer" else 1)
    assert receipt["status"] == "incomplete"


def test_failed_answer_phase_keeps_both_raw_responses_and_cannot_loop():
    session, assistant, findings = source()
    port = FinalizingPrefixPort(final_mutation=lambda response: response["assistant"].update(content=""))
    usecase = ContinueNativeFeedback(port, guidance_version=8, finalize_empty_answer=True)
    value, receipt = usecase.execute(["one-attempt"], session, assistant, findings=findings)
    assert value is None and receipt["status"] == "incomplete" and receipt["model_turns"] == 2
    assert receipt["transcript"][0]["response"]["assistant"]["reasoning_content"]
    assert receipt["finalization_response"]["assistant"]["content"] == ""
    assert len(port.calls) == len(port.final_calls) == 1
    again, rejected = usecase.execute_from_incomplete(["one-attempt"], receipt)
    assert again is None and rejected["finalization_issue"] == "native_prefix_incomplete_receipt_mismatch"
    assert len(port.final_calls) == 1
