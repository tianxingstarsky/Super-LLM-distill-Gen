"""Actual tool history, bounded continuation and unchanged draft provenance offline."""
from copy import deepcopy
import json

import pytest

from lib.contexts.workflows.application.continue_native_feedback import ContinueNativeFeedback
from lib.contexts.workflows.domain.native_feedback import READ_REVIEW_FEEDBACK, feedback_tool


def session():
    return {"version": 1, "author": "authenticated-flash-author", "stage": "multiturn",
        "role": "generation", "request_options": {"max_tokens": 32768}, "tools": [],
        "messages": [{"role": "system", "content": "Use the actual user's constraints."},
                     {"role": "user", "content": "I only need a small handout demo."}]}


def original():
    return {"role": "assistant", "content": "If it is scanned, you need OCR before the demo.",
            "reasoning_content": "The user needs a small demo. I considered OCR necessary."}


def findings():
    return [{"quote": "you need OCR before the demo", "issue": "Is OCR necessary for a small demo?",
             "disposition": "confirmed", "source_ref": "actual-user-message:1"}]


def requesting_feedback():
    return {"role": "assistant", "content": "", "reasoning_content": "I should read the recorded objection.",
        "tool_calls": [{"id": "real-provider-call-1", "type": "function", "function": {
            "name": READ_REVIEW_FEEDBACK, "arguments": "{}"}}]}


def revised():
    return {"role": "assistant", "content": "OCR is optional; a short manual transcription also works.",
            "reasoning_content": "A small manually transcribed excerpt meets the actual requirement.", "tool_calls": []}


def response(assistant, finish_reason="stop"):
    return {"assistant": deepcopy(assistant), "response_details": {"finish_reason": finish_reason}}


class Port:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls, self.saved = [], {}
        self.checks = 0

    def check_reasoning_cancel(self):
        self.checks += 1

    def turn(self, key, author_session, messages, tools, *, final):
        identity = json.dumps(key, sort_keys=True)
        if identity not in self.saved:
            value = next(self.responses)
            if isinstance(value, Exception):
                raise value
            self.calls.append(deepcopy({"session": author_session, "messages": messages,
                                       "tools": tools, "final": final}))
            self.saved[identity] = deepcopy(value)
        return deepcopy(self.saved[identity])


def test_first_person_guidance_is_versioned_tool_feedback_not_invented_native_history():
    old, author = original(), session()
    suggestions = [{"quote": "you need OCR before the demo",
        "issue": "等等，我把 OCR 当成了必需条件。我还没有排除按页核对的手工摘录。",
        "source_ref": {"kind": "saved_model_review"}, "disposition": "insufficient_evidence"}]
    frozen = deepcopy((old, author, suggestions))
    port = Port([response(requesting_feedback(), "tool_calls"), response(revised()),
                 response(requesting_feedback(), "tool_calls"), response(revised())])
    _, legacy = ContinueNativeFeedback(port).execute(["same-case"], author, old, findings=suggestions)
    _, current = ContinueNativeFeedback(port, guidance_version=2).execute(["same-case"], author, old, findings=suggestions)
    assert len(port.calls) == 4  # A new guidance version cannot hit the legacy request checkpoint.
    assert legacy["observation"]["version"] == 1 and "guidance_voice" not in legacy["observation"]
    observation = current["observation"]
    assert observation["version"] == 2 and observation["guidance_voice"] == "original_author_first_person"
    assert observation["guidance_origin"] == "attributed_feedback_not_provider_reasoning"
    assert observation["findings"] == suggestions and current["original_assistant"] == old
    assert observation["semantic_status"] == "not_assessed" and not current["independent_review"]
    assert port.calls[-1]["messages"][-1]["role"] == "tool"
    assert suggestions[0]["issue"] not in port.calls[-1]["messages"][-2]["reasoning_content"]
    assert (old, author, suggestions) == frozen


@pytest.mark.parametrize("also_in_answer", [False, True])
@pytest.mark.parametrize("guidance_version", [3, 4])
def test_feedback_locates_the_live_verbosity_quote_in_its_original_native_channels(also_in_answer, guidance_version):
    old, author = original(), session()
    # Excerpt from the real failure: the revised model called this original
    # reasoning quote fabricated because it was absent from the answer.
    old["reasoning_content"] = "Need answer directly. Desired oververbosity 5. Keep concise comprehensive."
    if also_in_answer:
        old["content"] = "The note says Desired oververbosity 5."
    issues = [{"quote": "Desired oververbosity 5.",
        "issue": "The original request did not specify this numerical response setting.",
        "disposition": "insufficient_evidence"}]
    frozen = deepcopy((old, author, issues))
    port = Port([response(requesting_feedback(), "tool_calls"), response(revised())])
    _, receipt = ContinueNativeFeedback(port, guidance_version=guidance_version).execute(
        ["verbosity-quote"], author, old, findings=issues)
    observation = json.loads(port.calls[-1]["messages"][-1]["content"])
    finding, = observation["findings"]
    locations = finding["quote_locations"]
    expected = {("reasoning_content", 22, 46)}
    if also_in_answer:
        expected.add(("content", 14, 38))
    assert {(item["channel"], item["start"], item["end"]) for item in locations} == expected
    assert len(locations) == len(expected)
    for item in locations:
        text = old[item["channel"]]
        assert text[item["start"]:item["end"]] == issues[0]["quote"]
        assert item["excerpt"] == text[item["excerpt_start"]:item["excerpt_end"]]
        assert item["excerpt_start"] <= item["start"] < item["end"] <= item["excerpt_end"]
    assert {key: value for key, value in finding.items() if key != "quote_locations"} == issues[0]
    assert observation["quote_location_basis"] == "exact_match_in_original_response_not_review_truth"
    assert observation["semantic_status"] == "not_assessed" and not observation["independent_review"]
    assert receipt["original_assistant"] == old and (old, author, issues) == frozen


@pytest.mark.parametrize("guidance_version", [3, 4])
def test_feedback_separates_application_revision_from_the_actual_last_user_message(guidance_version):
    old, author, issues = original(), session(), findings()
    actual_user = {"role": "user", "content": "Let’s narrow it to cited Q&A, not a benchmark."}
    author["messages"].extend([revised(), actual_user,
        {"role": "system", "content": "Saved editorial feedback is available."}])
    frozen = deepcopy((old, author, issues))
    port = Port([response(requesting_feedback(), "tool_calls"), response(revised())])
    _, receipt = ContinueNativeFeedback(port, guidance_version=guidance_version).execute(
        ["user-origin"], author, old, findings=issues)
    first, last = port.calls
    assert first["messages"][:-2] == author["messages"]
    assert first["messages"][-2] == old and first["messages"][-1]["role"] == "system"
    if guidance_version == 3:
        assert "application-requested revision, outside the user dialogue" in first["messages"][-1]["content"]
        assert "The user has sent no new message." in first["messages"][-1]["content"]
    observation = json.loads(last["messages"][-1]["content"])
    assert observation["operation"] == {"initiator": "application", "new_user_turn": False,
        "feedback_source": "recorded_editorial_observations"}
    task = observation["user_task"]
    assert (task["message_index"], task["role"], task["text"]) == (3, "user", actual_user["content"])
    if guidance_version == 4:
        assert observation["delivery"] == {"mode": "replace_reviewed_assistant_turn",
            "task_user_message_index": 3,
            "feedback_role": "reviewer_observation_not_user_request",
            "length_policy": "task_driven_without_default_length_target",
            "private_operation": "not_a_user_visible_conversation_event"}
    assert not task["nontext_parts_retained_in_original_history"]
    assert [message for message in last["messages"] if message["role"] == "user"] == [
        author["messages"][1], actual_user]
    assert receipt["observation"] == observation and (old, author, issues) == frozen


@pytest.mark.parametrize("guidance_version", [1, 2])
def test_legacy_feedback_keeps_its_observation_fields_and_instruction_messages(guidance_version):
    old, author, issues = original(), session(), findings()
    port = Port([response(requesting_feedback(), "tool_calls"), response(revised())])
    _, receipt = ContinueNativeFeedback(port, guidance_version=guidance_version).execute(
        ["legacy-origin"], author, old, findings=issues)
    # Frozen v1/v2 wire instructions: v3 must not add origin messages or fields
    # to already frozen work or change the historical request identity.
    instruction = (
        "Recorded editorial feedback is available for the preceding draft. Read it through "
        "read_review_feedback, consider the observations against the actual task and evidence, "
        "then continue your reasoning and write the complete revised answer. The observations "
        "are not a new user request or established facts. Preserve supported conclusions and "
        "uncertainty; do not invent premises to accommodate an objection.")
    fields = {"version", "kind", "draft_sha256", "findings", "scope", "semantic_status",
              "independent_review", "action"}
    if guidance_version == 2:
        instruction += (
            " The recorded reviewer suggestions may be written from your first-person perspective. "
            "They remain attributed tool feedback, not your prior native thoughts or new user instructions. "
            "After reading them, reconsider the specific concern in your own reasoning, using a natural "
            "transition when useful rather than a review report.")
        fields.update({"guidance_voice", "guidance_origin"})
    first, last = port.calls
    assert first["messages"] == author["messages"] + [old, {"role": "system", "content": instruction}]
    assert last["messages"] == first["messages"] + [requesting_feedback(), receipt["tool_feedback"]]
    observation = json.loads(receipt["tool_feedback"]["content"])
    assert set(observation) == fields and observation["version"] == guidance_version
    assert observation["findings"] == issues


def test_native_cot_repair_uses_tool_continuation_without_buying_an_independent_review():
    from lib.contexts.workflows.application.generate_native_cot import GenerateNativeCot
    tools_port = Port([response(requesting_feedback(), "tool_calls"), response(revised())])

    class NativePort:
        def source_evidence(self, source):
            return source.get("text", "")

        def request(self, *args, **kwargs):
            pytest.fail("The original native draft must not be generated again")

        def assess(self, *args, **kwargs):
            pytest.fail("Explicit tool repair must not purchase a new independent review")

        def continue_native_feedback(self, key, author, assistant, *, findings):
            return ContinueNativeFeedback(tools_port).execute(key, author, assistant, findings=findings)

        def reject(self, record, reason):
            return {"status": "quarantined", "reason": reason}

    old = original()
    sample = {"id": "native-repair", "source_id": "source", "messages": session()["messages"],
        "native_previous_candidate": old,
        "native_tool_feedback": {"session": session(), "findings": findings()}}
    recipe = {"native_revision_context_version": 2,
        "node_generation": {"multiturn": {"enabled": True, "answer_mode": "native_joint"}}}
    row, = GenerateNativeCot(NativePort(), recipe, stage="multiturn").execute(sample, {})
    assert len(tools_port.calls) == 2
    assert row["answer"] == revised()["content"]
    assert revised()["reasoning_content"] in row["reasoning"][0]
    assert row["native_generation"]["tool_feedback"]["original_assistant"] == old
    assert row["native_generation"]["tool_feedback"]["status"] == "completed"
    assert row["status"] == "quarantined"
    assert row["cot_review_policy"]["semantic_status"] == "not_assessed"
    assert row["cot_review_policy"]["selected"] is False and "judge" not in row


def test_native_feedback_uses_real_call_id_preserves_draft_and_never_certifies_quality():
    old, author, issues = original(), session(), findings()
    frozen = deepcopy((old, author, issues))
    port = Port([response(requesting_feedback(), "tool_calls"), response(revised())])
    value, receipt = ContinueNativeFeedback(port).execute(["case"], author, old, findings=issues)
    assert len(port.calls) == receipt["model_turns"] == 2
    first, last = port.calls
    assert first["messages"][:len(author["messages"])] == author["messages"]
    assert first["messages"][-2] == old and first["messages"][-1]["role"] == "system"
    assert issues[0]["issue"] not in first["messages"][-1]["content"]
    assert not first["final"] and last["final"]
    assert last["messages"][-2] == requesting_feedback()
    assert last["messages"][-1]["role"] == "tool"
    assert last["messages"][-1]["tool_call_id"] == requesting_feedback()["tool_calls"][0]["id"]
    observation = json.loads(last["messages"][-1]["content"])
    assert observation["findings"] == issues and observation["semantic_status"] == "not_assessed"
    assert value == {"reasoning": requesting_feedback()["reasoning_content"] + "\n\n" + revised()["reasoning_content"],
                     "answer": revised()["content"]}
    assert receipt["status"] == "completed" and receipt["semantic_status"] == "not_assessed"
    assert not receipt["independent_review"] and "accepted" not in receipt and "training_eligible" not in receipt
    assert receipt["original_assistant"] == old and receipt["revised_assistant"] == revised()
    assert receipt["continuation_session"] == {**author, "messages": last["messages"], "tools": last["tools"]}
    assert receipt["continuation_session"]["messages"][-1]["role"] == "tool"
    assert (old, author, issues) == frozen
    assert ContinueNativeFeedback(port).execute(["case"], author, old, findings=issues) == (value, receipt)
    assert len(port.calls) == 2


@pytest.mark.parametrize("guidance_version", [1, 3, 4, 5, 6])
def test_recorded_feedback_request_needs_only_one_new_call(guidance_version):
    from lib.contexts.workflows.domain.native_feedback import feedback_work_instruction
    old = {**original(), "tool_calls": requesting_feedback()["tool_calls"]}
    author = session()
    author["tools"] = [feedback_tool()]
    port = Port([response(revised())])
    value, receipt = ContinueNativeFeedback(port, guidance_version=guidance_version).execute(
        ["existing-call"], author, old, findings=findings())
    assert value["reasoning"] == revised()["reasoning_content"]
    assert receipt["model_turns"] == 1 and receipt["tools_preserved"]
    assert receipt["feedback_request_origin"] == "recorded_provider_tool_call"
    extra = [{"role": "system", "content": feedback_work_instruction(guidance_version)}] if guidance_version in {4, 5, 6} else []
    assert port.calls[0]["messages"][:-1] == author["messages"] + extra + [old]
    assert receipt["continuation_session"]["messages"] == port.calls[0]["messages"]


@pytest.mark.parametrize("change,expected", [
    (lambda old: old.pop("reasoning_content"), "native_feedback_reasoning_missing"),
    (lambda old: old.update(content=""), "native_feedback_answer_missing"),
    (lambda old: old.update(tool_calls={}), "native_feedback_invalid_tool_calls"),
])
def test_incomplete_original_is_retained_without_a_request(change, expected):
    old, port = original(), Port([])
    change(old)
    value, receipt = ContinueNativeFeedback(port).execute([], session(), old, findings=findings())
    assert value is None and receipt["issue"] == expected and receipt["original_assistant"] == old
    assert not port.calls and receipt["model_turns"] == 0


@pytest.mark.parametrize("invalid", [
    [{"quote": "Absent from either native channel", "issue": "Invented finding"}],
    [{**findings()[0], "replacement_answer": "A predetermined answer"}],
    [{**findings()[0], "disposition": "approved"}],
])
def test_feedback_requires_exact_original_text_and_no_replacement_fields(invalid):
    port = Port([])
    value, receipt = ContinueNativeFeedback(port).execute([], session(), original(), findings=invalid)
    assert value is None and receipt["status"] == "incomplete" and not port.calls


@pytest.mark.parametrize("change,issue", [
    (lambda answer: answer.pop("tool_calls"), "native_feedback_tool_missing_or_ambiguous"),
    (lambda answer: answer["tool_calls"][0]["function"].update(name="unrelated_tool"), "native_feedback_unrelated_tool_call"),
    (lambda answer: answer["tool_calls"].append(deepcopy(answer["tool_calls"][0])), "native_feedback_tool_missing_or_ambiguous"),
    (lambda answer: answer.update(reasoning_content=""), "native_feedback_reasoning_missing"),
])
def test_tool_protocol_failure_keeps_response_and_does_not_buy_an_answer(change, issue):
    answer = requesting_feedback()
    change(answer)
    raw = response(answer, "tool_calls")
    port = Port([raw])
    value, receipt = ContinueNativeFeedback(port).execute([], session(), original(), findings=findings())
    assert value is None and receipt["issue"] == issue and receipt["model_turns"] == 1
    assert receipt["transcript"][0]["response"] == raw and len(port.calls) == 1


@pytest.mark.parametrize("final_response,issue", [
    (response({**revised(), "reasoning_content": ""}), "native_feedback_reasoning_missing"),
    (response({**revised(), "content": ""}), "native_feedback_answer_missing"),
    (response(revised(), "length"), "native_feedback_response_incomplete"),
    (response({**revised(), "tool_calls": requesting_feedback()["tool_calls"]}, "tool_calls"),
     "native_feedback_tool_call_after_observation"),
])
def test_incomplete_final_response_is_retained_without_retry_or_pass(final_response, issue):
    port = Port([response(requesting_feedback(), "tool_calls"), final_response])
    value, receipt = ContinueNativeFeedback(port).execute([], session(), original(), findings=findings())
    assert value is None and receipt["issue"] == issue and receipt["model_turns"] == 2
    assert receipt["transcript"][-1]["response"] == final_response and len(port.calls) == 2
    assert receipt["semantic_status"] == "not_assessed"


def test_resuming_after_interruption_reuses_the_real_feedback_request():
    port = Port([response(requesting_feedback(), "tool_calls"), RuntimeError("interrupted"), response(revised())])
    usecase = ContinueNativeFeedback(port)
    with pytest.raises(RuntimeError, match="interrupted"):
        usecase.execute(["resume"], session(), original(), findings=findings())
    value, receipt = usecase.execute(["resume"], session(), original(), findings=findings())
    assert value["answer"] == revised()["content"] and len(port.calls) == 2
    assert receipt["model_turns"] == 2


def test_recovered_history_requires_native_fields_and_preserves_recovery_receipt():
    author = session()
    author["messages"].extend([{"role": "assistant", "content": "Earlier draft."},
                               {"role": "user", "content": "Make it shorter."}])
    port = Port([response(requesting_feedback(), "tool_calls"), response(revised())])
    value, receipt = ContinueNativeFeedback(port).execute([], author, original(), findings=findings())
    assert value is None and receipt["issue"] == "native_feedback_original_history_reasoning_missing" and not port.calls
    author["messages"][-2]["reasoning_content"] = "Authenticated original native thought."
    author["recovery"] = {"original_messages_sha256": "original", "native_history_refs": ["verified-checkpoint"]}
    value, receipt = ContinueNativeFeedback(port).execute([], author, original(), findings=findings())
    assert value and receipt["session_recovery"] == author["recovery"]
    assert "message_prefix_preserved" not in receipt


@pytest.mark.parametrize("guidance_version", [5, 6])
def test_reusing_delivered_feedback_keeps_every_parent_channel_and_can_reuse_again(guidance_version):
    from lib.contexts.workflows.domain.native_feedback import feedback_work_instruction
    from lib.contexts.workflows.domain.native_feedback_provenance import authenticate_native_feedback_receipt
    old, author, issues = original(), session(), findings()
    author["recovery"] = {"kind": "authenticated_legacy_wire_and_checkpoint", "history_enrichment": []}
    fresh = {**revised(), "reasoning_content": "A selected passage supports only a local claim.\n\n"
        "I cannot establish that the entire handout lacks the answer.\n",
        "content": "Use the supplied text and say when it does not contain the answer."}
    port = Port([response(requesting_feedback(), "tool_calls"), response(revised()), response(fresh), response(fresh)])
    _, parent = ContinueNativeFeedback(port, guidance_version=4).execute(["parent"], author, old, findings=issues)
    frozen = deepcopy((author, old, issues, parent))
    usecase = ContinueNativeFeedback(port, guidance_version=guidance_version)
    value, receipt = usecase.execute_from_feedback(["reused"], author, old, findings=issues, previous_receipt=parent)
    assert value == {"reasoning": fresh["reasoning_content"], "answer": fresh["content"]}
    assert len(port.calls) == 3 and receipt["model_turns"] == 1
    assert receipt["feedback_request_origin"] == "reused_delivered_feedback"
    assert receipt["previous_receipt"] == parent
    assert receipt["tool_feedback"] == parent["tool_feedback"] and receipt["observation"] == parent["observation"]
    assert receipt["session_recovery"] == author["recovery"]
    sent = port.calls[-1]
    assert sent["session"] == parent["continuation_session"] and sent["final"]
    assert sent["messages"] == parent["continuation_session"]["messages"] + [
        {"role": "system", "content": feedback_work_instruction(guidance_version)}]
    assert sent["tools"] == parent["continuation_session"]["tools"]
    assert receipt["reasoning_segments"] == [{"phase": "continue_answer", "origin": "provider_reasoning_content",
                                               "text": fresh["reasoning_content"]}]
    assert authenticate_native_feedback_receipt(receipt)["original_session"] == author
    assert usecase.execute_from_feedback(["reused"], author, old,
        findings=issues, previous_receipt=parent) == (value, receipt)
    assert len(port.calls) == 3
    _, repeated = usecase.execute_from_feedback(["again"], author, old,
        findings=issues, previous_receipt=receipt)
    assert len(port.calls) == 4 and repeated["previous_receipt"] == receipt
    assert authenticate_native_feedback_receipt(repeated)["value"] == value
    assert (author, old, issues, parent) == frozen


@pytest.mark.parametrize("change", [
    lambda inputs: inputs["session"]["messages"][1].update(content="A different user task."),
    lambda inputs: inputs["original"].update(reasoning_content="Reconstructed private thought."),
    lambda inputs: inputs["findings"][0].update(issue="A different saved review."),
    lambda inputs: inputs["receipt"]["continuation_session"]["request_options"].update(max_tokens=1),
    lambda inputs: inputs["receipt"]["continuation_session"].update(author="different-author"),
    lambda inputs: inputs["receipt"]["tool_feedback"].update(tool_call_id="fabricated-call"),
    lambda inputs: inputs["receipt"]["reasoning_segments"][0].update(text="A substituted tool thought."),
    lambda inputs: inputs["receipt"]["transcript"][-1]["response"]["assistant"].update(content="Another final answer."),
    lambda inputs: inputs["receipt"].update(original_session_sha256="unbound-parent"),
    lambda inputs: inputs["receipt"].update(semantic_status="approved"),
])
def test_reused_feedback_rejects_changed_parent_bindings_before_a_request(change):
    parent_port = Port([response(requesting_feedback(), "tool_calls"), response(revised())])
    _, parent = ContinueNativeFeedback(parent_port, guidance_version=4).execute(
        ["parent"], session(), original(), findings=findings())
    inputs = {"session": session(), "original": original(), "findings": findings(), "receipt": parent}
    change(inputs)
    port = Port([])
    value, receipt = ContinueNativeFeedback(port, guidance_version=5).execute_from_feedback(
        ["refuse"], inputs["session"], inputs["original"], findings=inputs["findings"], previous_receipt=inputs["receipt"])
    assert value is None and receipt["issue"] == "native_feedback_receipt_provenance_mismatch"
    assert receipt["model_turns"] == 0 and not port.calls and not port.checks


def test_reuse_is_explicitly_versioned_and_keeps_incomplete_new_response():
    parent_port = Port([response(requesting_feedback(), "tool_calls"), response(revised())])
    _, parent = ContinueNativeFeedback(parent_port, guidance_version=4).execute(
        ["parent"], session(), original(), findings=findings())
    raw = response({**revised(), "reasoning_content": "Complete received prefix must remain in the trace.\n"}, "length")
    port = Port([raw])
    _, refused = ContinueNativeFeedback(port, guidance_version=4).execute_from_feedback(
        [], session(), original(), findings=findings(), previous_receipt=parent)
    assert refused["issue"] == "native_feedback_reuse_requires_guidance_v5" and not port.calls
    value, incomplete = ContinueNativeFeedback(port, guidance_version=5).execute_from_feedback(
        [], session(), original(), findings=findings(), previous_receipt=parent)
    assert value is None and incomplete["issue"] == "native_feedback_response_incomplete"
    assert incomplete["model_turns"] == len(port.calls) == 1
    assert incomplete["transcript"][-1]["response"] == raw and incomplete["previous_receipt"] == parent
    assert incomplete["semantic_status"] == "not_assessed"
