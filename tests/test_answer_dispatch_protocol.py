"""Editorial plans are not established learner facts or verified evidence."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from lib.domain.workflow_quality import canonical
from lib.infrastructure.dialogue_role_messages import answer_role_messages
from tests.test_dialogue_role_messages import CONTEXT, PRIVATE, answer_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def rich_data():
    data = answer_data()
    data["qa_contract"].update(question="Which action first?", guidance="GUIDANCE_SENTINEL",
        dialogue_design={"interaction_goal": "PLAN_SENTINEL"},
        human_design={"reference_answer": "HUMAN_CANDIDATE_SENTINEL"})
    data.update(dialogue_state={"progress": "UNVERIFIED_STATE_SENTINEL"},
        dialogue_step={"response_policy": "conditional", "reason": "FUTURE_CONDITION_SENTINEL"},
        requirement="CUSTOM_REQUIREMENT_SENTINEL")
    return data


# Recorded from the original implementation before the protocol branch.
LEGACY_HASHES = {
    ("tool", False): "73da2f7af1f31f401318927059dbe1e7faba1032f4e1f509260c01d4ceea863e",
    ("tool", True): "221f3e1e59747d93648a5b52bf3419f0d8d0714459fbbf51a6390eaaff6b8e79",
    ("system", False): "55b497d6b58497a49def717368c8ba9a7c9cd2c8c2ec66431be440764a4770df",
    ("system", True): "da1debab76a1643ae7eaa05209180c4d1027d41ad1b02f50cf4a42255217ae07",
}


@pytest.mark.parametrize("transport,include_question", list(LEGACY_HASHES))
def test_default_and_explicit_v1_preserve_complete_request_bytes(transport, include_question):
    data = rich_data()
    default = answer_role_messages("CUSTOM_SYSTEM_SENTINEL", CONTEXT, data,
        transport=transport, include_question=include_question)
    explicit = answer_role_messages("CUSTOM_SYSTEM_SENTINEL", CONTEXT, data,
        transport=transport, include_question=include_question, answer_dispatch_protocol_version=1)
    assert default == explicit
    assert sha256(canonical(default).encode()).hexdigest() == LEGACY_HASHES[transport, include_question]


@pytest.mark.parametrize("transport,include_question", list(LEGACY_HASHES))
def test_v2_changes_only_trusted_system_scope_and_preserves_all_dialogue_and_design_data(transport, include_question):
    data = rich_data()
    data["learner_messages"] = [
        {"role": "user", "content": "I have two runs, not one."},
        {"role": "assistant", "content": "Both runs need to be compared under matching conditions."},
        {"role": "user", "content": "Use the shorter run as the baseline."},
    ]
    frozen, context = deepcopy(data), deepcopy(CONTEXT)
    legacy = answer_role_messages("CUSTOM_SYSTEM_SENTINEL", CONTEXT, data,
        transport=transport, include_question=include_question)
    current = answer_role_messages("CUSTOM_SYSTEM_SENTINEL", CONTEXT, data,
        transport=transport, include_question=include_question, answer_dispatch_protocol_version=2)
    assert current[0]["role"] == "system"
    assert current[1:] == legacy[1:]
    assert current[-3:] == data["learner_messages"]
    system = current[0]["content"]
    for phrase in ("CUSTOM_SYSTEM_SENTINEL", "ANSWER_RULE_SENTINEL", "经过结构检查的设计简报",
                   "尚未经过独立事实核验", "计划、预设和推测不是已发生的事实",
                   "不得倒灌为当前已知事实", "以真实对话为准", "不能替用户确认其个人情况"):
        assert phrase in system
    assert "dialogue_dispatch 是经工作流验证的设计简报" not in system
    body = json.dumps(current, ensure_ascii=False)
    for unchanged in (PRIVATE, "GUIDANCE_SENTINEL", "PLAN_SENTINEL", "UNVERIFIED_STATE_SENTINEL",
                      "FUTURE_CONDITION_SENTINEL", "HUMAN_CANDIDATE_SENTINEL", "CUSTOM_REQUIREMENT_SENTINEL"):
        assert unchanged in body
    assert "answer_dispatch_protocol_version" not in body
    assert data == frozen and CONTEXT == context


def test_untrusted_payload_version_cannot_opt_in_or_override_trusted_version():
    data = rich_data()
    before = answer_role_messages("CUSTOM_SYSTEM_SENTINEL", CONTEXT, data)
    data["answer_dispatch_protocol_version"] = 2
    assert answer_role_messages("CUSTOM_SYSTEM_SENTINEL", CONTEXT, data) == before
    data["answer_dispatch_protocol_version"] = 1
    current = answer_role_messages("CUSTOM_SYSTEM_SENTINEL", CONTEXT, data, answer_dispatch_protocol_version=2)
    assert "经过结构检查的设计简报" in current[0]["content"]


@pytest.mark.parametrize("version", [None, True, False, 0, 3, "2", 2.0])
def test_unknown_protocol_is_a_closed_config_failure(version):
    with pytest.raises(ValueError, match="^invalid_answer_dispatch_protocol_version$"):
        answer_role_messages("system", CONTEXT, rich_data(), answer_dispatch_protocol_version=version)


def make_request(tmp_path, *, prompt_id, stage, separated=True, version="factory"):
    from lib.infrastructure import training_workflow as engine
    from tests.test_directed_answer_contracts import Writer, Reviewer
    output = tmp_path / "output"
    custom = "CUSTOM_NODE_PROMPT_SENTINEL: respond to the actual final user turn."
    run_id = engine.create_run(output, brief="Create useful dialogue data.", targets=["sft", "multiturn"],
        qa_director={"enabled": True, "question_separation": separated},
        node_prompts={stage: {prompt_id: custom}})
    writer = Writer([{"answer": "Both runs can be compared after checking their conditions.", "quotes": []}])
    worker = engine.Workflow(output, run_id, tmp_path, generator=writer, jev=Reviewer())
    assert worker.recipe["answer_dispatch_protocol_version"] == 2
    if version == "legacy":
        worker.recipe.pop("answer_dispatch_protocol_version")
    elif version != "factory":
        worker.recipe["answer_dispatch_protocol_version"] = version
    worker.stage = stage
    data = rich_data()
    data["source_context"] = {"kind": "document", "text": PRIVATE}
    data["learner_messages"] = [
        {"role": "user", "content": "I have two runs."},
        {"role": "assistant", "content": "What comparison do you need?"},
        {"role": "user", "content": "Compare the shorter run with the longer one."},
    ]
    return worker, writer, data, custom


@pytest.mark.parametrize("prompt_id,stage,separated", [
    ("workflow.sft_answer", "sft", True),
    ("workflow.multiturn_assistant", "multiturn", True),
    ("workflow.sft_directed", "sft", False),
])
@pytest.mark.parametrize("version", ["factory", "legacy"])
def test_actual_workflow_ask_transmits_the_frozen_scope_as_system_without_rewriting_user_or_custom_prompt(
        tmp_path, prompt_id, stage, separated, version):
    worker, writer, data, custom = make_request(tmp_path, prompt_id=prompt_id, stage=stage,
        separated=separated, version=version)
    frozen = deepcopy(data)
    worker.ask(["dispatch-scope-test"], "generation", prompt_id, data)
    assert len(writer.calls) == 1
    actual = writer.calls[0]
    assert actual[0]["role"] == "system" and custom in actual[0]["content"]
    assert ("经过结构检查的设计简报" in actual[0]["content"]) == (version == "factory")
    assert ("dialogue_dispatch 是经工作流验证的设计简报" in actual[0]["content"]) == (version == "legacy")
    assert actual[-3:] == data["learner_messages"]
    assert PRIVATE in json.dumps(actual, ensure_ascii=False)
    assert "answer_dispatch_protocol_version" not in json.dumps(actual)
    assert data == frozen


@pytest.mark.parametrize("version", [None, True, "2", 2.0, 3])
def test_actual_workflow_rejects_unknown_protocol_before_any_model_call(tmp_path, version):
    worker, writer, data, _ = make_request(tmp_path, prompt_id="workflow.sft_answer", stage="sft", version=version)
    with pytest.raises(ValueError, match="^invalid_answer_dispatch_protocol_version$"):
        worker.ask(["invalid-dispatch-scope-test"], "generation", "workflow.sft_answer", data)
    assert writer.calls == []
