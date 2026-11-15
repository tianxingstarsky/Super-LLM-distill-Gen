"""Native beta repair through the production dialogue and export projections."""
from copy import deepcopy
import hashlib

from lib.contexts.workflows.application.continue_native_dialogue import ContinueNativeDialogue
from lib.contexts.workflows.domain.dialogue import dialogue_digest
from lib.contexts.workflows.domain.native_dialogue_continuation import native_dialogue_continuation_input
from lib.contexts.workflows.domain.native_prefix_feedback import DEFAULT_NATIVE_REFLECTION_SEED
from lib.contexts.workflows.domain.targets import training_record
from test_native_dialogue_continuation import NativeToolRepairPort, tool_repair_parent_and_recipe


class NativePrefixRepairPort(NativeToolRepairPort):
    guidance_version = 8

    def __init__(self):
        super().__init__()
        self.prefix_requests = []

    def continue_native_feedback(self, key, author_session, assistant, *, findings):
        from lib.contexts.workflows.application.continue_native_feedback import ContinueNativeFeedback
        return ContinueNativeFeedback(self, guidance_version=8,
            prefix_reflection_seed=getattr(self, "reflection_seed", "")).execute(
                key, author_session, assistant, findings=findings)

    def continue_prefix(self, key, author_session, messages, *, reasoning_prefix):
        self.prefix_requests.append(deepcopy({"session": author_session, "messages": messages,
                                            "reasoning_prefix": reasoning_prefix}))
        assistant = {"role": "assistant", "content": "Check a short extracted passage first; use a prepared excerpt if needed.",
            "reasoning_content": "\nHowever, the proposed fallback does not establish extraction success. I must keep that condition."}
        return {"assistant": assistant, "response_details": {"api_format": "chat", "finish_reason": "stop",
            "reasoning_content": assistant["reasoning_content"], "checkpoint": "prefix-response-" + str(len(self.prefix_requests)),
            "reasoning_prefill": {"version": 3, "transport": "deepseek_chat_beta", "visible_prefix": "",
                "continuation_mode": "reasoning", "prefix_sha256": hashlib.sha256(reasoning_prefix.encode()).hexdigest(),
                "prefix_utf8_bytes": len(reasoning_prefix.encode()), "response_reasoning": "provider_continuation"}}}


def repaired_dialogue():
    item, recipe = tool_repair_parent_and_recipe()
    recipe["native_feedback_guidance_version"] = 8
    recipe["native_instruction_scope_version"] = 1
    author = NativePrefixRepairPort()
    row, = ContinueNativeDialogue(author, recipe, "beta-child").execute(item)
    return item, recipe, author, row


def test_native_beta_generation_retains_prefix_suffix_roles_and_training_projection():
    item, _, author, row = repaired_dialogue()
    native = row["native_generation"]
    receipt = native["review_guidance"]
    original = item["record"]["messages"][-1]
    note = receipt["guidance_message"]
    suffix = receipt["transcript"][0]["response"]["assistant"]["reasoning_content"]
    combined = original["reasoning_content"] + suffix
    assert len(author.prefix_requests) == 1 and not author.tool_requests
    assert author.prefix_requests[0]["reasoning_prefix"] == original["reasoning_content"]
    assert author.prefix_requests[0]["messages"] == [*item["record"]["question"], note]
    assert [m["role"] for m in author.prefix_requests[0]["messages"]] == ["user", "system"]
    assert note["role"] == "system" and item["instruction"] in note["content"]
    assert "tool_feedback" not in native and receipt["guidance_version"] == 8
    assert receipt["original_assistant"] == native["previous_candidate"] == original
    assert row["reasoning"] == [combined]
    assert row["messages"][-1]["reasoning_content"] == combined
    assert row["messages"][:-1] == row["question"] == [*item["record"]["question"], note]
    assert receipt["revised_assistant"]["reasoning_content"] == combined
    assert receipt["reasoning_segments"][1]["text"] == suffix
    assert receipt["request_messages"][-1] == {"role": "assistant", "content": "", "prefix": True,
                                                 "reasoning_content": original["reasoning_content"]}
    assert native["author_session"] == receipt["continuation_session"]
    assert training_record("sft", row)["messages"] == row["messages"]
    assert training_record("cot", row)["question"] == row["question"]
    assert training_record("cot", row)["reasoning"] == [combined]
    assert row["cot_review_policy"]["semantic_status"] == "not_assessed"
    assert row["native_dialogue_continuation"]["assessments"] == "not_assessed"
    assert not author.assessments and not author.styles and not author.contracts
    assert not author.requests and not author.next_steps


def test_second_explicit_beta_repair_reuses_composed_parent_and_keeps_old_record():
    item, recipe, author, first = repaired_dialogue()
    frozen = deepcopy(first)
    continuation = native_dialogue_continuation_input(first, "Keep extraction success conditional.", guidance_version=8)
    assert continuation["initial_question_sha256"] == dialogue_digest(item["record"]["question"][-1])
    assert continuation["previous_candidate"] == first["messages"][-1]
    second, = ContinueNativeDialogue(author, recipe, "beta-second").execute({**item, "record": first,
        "instruction": "Keep extraction success conditional.",
        "revision_context": {"parent_run_id": "beta-child", "content_sha256": dialogue_digest(first)}})
    assert first == frozen and len(author.prefix_requests) == 2 and not author.tool_requests
    assert author.prefix_requests[1]["reasoning_prefix"] == first["reasoning"][0]
    assert author.prefix_requests[1]["messages"][:-1] == author.prefix_requests[0]["messages"]
    assert second["question"][:-1] == first["question"]
    assert second["native_generation"]["previous_candidate"] == first["messages"][-1]
    assert second["messages"][:-1] == second["question"]
    assert second["reasoning"][0].startswith(first["reasoning"][0])
    assert native_dialogue_continuation_input(second, "Retain those conditions.", guidance_version=8)
    assert not author.assessments and not author.requests and not author.next_steps


def test_seeded_generation_export_and_second_repair_keep_application_seed_provenance():
    item, recipe = tool_repair_parent_and_recipe()
    recipe.update(native_feedback_guidance_version=8, native_instruction_scope_version=1,
                  native_prefix_reflection_seed=DEFAULT_NATIVE_REFLECTION_SEED)
    author = NativePrefixRepairPort()
    author.reflection_seed = recipe["native_prefix_reflection_seed"]
    first, = ContinueNativeDialogue(author, recipe, "seeded").execute(item)
    receipt = first["native_generation"]["review_guidance"]
    original = item["record"]["messages"][-1]["reasoning_content"]
    suffix = receipt["transcript"][0]["response"]["assistant"]["reasoning_content"]
    seed = author.reflection_seed
    assert receipt["reasoning_segments"] == [
        {"phase": "supplied_prefix", "origin": "original_provider_reasoning_content", "text": original},
        {"phase": "reflection_seed", "origin": "application_reflection_seed", "text": seed},
        {"phase": "continue_answer", "origin": "provider_reasoning_content", "text": suffix}]
    assert author.prefix_requests[0]["reasoning_prefix"] == original + seed
    assert first["reasoning"] == [original + seed + suffix]
    assert training_record("sft", first)["messages"][-1]["reasoning_content"] == original + seed + suffix
    assert training_record("cot", first)["reasoning"] == [original + seed + suffix]
    assert first["question"][-1] == receipt["guidance_message"]
    assert first["question"][-1]["role"] == "system"
    second, = ContinueNativeDialogue(author, recipe, "seeded-again").execute({**item, "record": first,
        "instruction": "Keep the scope conditional.",
        "revision_context": {"parent_run_id": "seeded", "content_sha256": dialogue_digest(first)}})
    assert author.prefix_requests[1]["reasoning_prefix"] == first["reasoning"][0] + seed
    assert second["question"][:-1] == first["question"]
    second_receipt = second["native_generation"]["review_guidance"]
    assert second_receipt["reasoning_segments"][:3] == receipt["reasoning_segments"]
    assert [segment["origin"] for segment in second_receipt["reasoning_segments"]] == [
        "original_provider_reasoning_content", "application_reflection_seed", "provider_reasoning_content",
        "application_reflection_seed", "provider_reasoning_content"]
    assert native_dialogue_continuation_input(second, "Keep the scope.", guidance_version=8)
    assert len(author.prefix_requests) == 2 and not author.tool_requests
    assert not author.assessments and not author.requests and not author.next_steps
