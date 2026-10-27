"""Phase guidance stays editable, appears once, and cannot rewrite learner history."""
from copy import deepcopy
import hashlib

import pytest

from lib.contexts.workflows.application.generate_cot import GenerateCot
from lib.contexts.workflows.domain.integrated_reflection import (
    default_integrated_reflection, reflection_for_prompt, trailing_reflection_enabled,
)
from test_source_memory_requests import CapturingGenerator, workflow
from test_integrated_reflection import NoReviewPort, body, recipe
from test_generation_node_use_cases import conversation


@pytest.mark.parametrize("prompt_id", ["workflow.cot_generate", "workflow.cot_rationale", "workflow.cot_native_answer"])
def test_actual_request_moves_only_frozen_applicable_rules_after_history(tmp_path, prompt_id):
    policy = default_integrated_reflection()
    policy["rules"] = [
        {"id":"shared", "requirement":"CUSTOM SHARED CONDITION", "allow_na":False},
        {"id":"final", "requirement":"CUSTOM FINAL CONDITION", "allow_na":False, "phases":["final"]}]
    writer = CapturingGenerator([{"ok":True}, {"ok":True}, {"ok":True}])
    worker = workflow(tmp_path, writer, node_generation={"cot":{"enabled":True,"self_check":policy}})
    assert worker.recipe["version"] == 35 and trailing_reflection_enabled(worker.recipe)
    worker.stage = "cot"
    turns = [{"role":"user","content":"My baseline is zero. Can I compare these scores?"},
        {"role":"assistant","content":"Always report percentage improvement; only deterministic decoding is valid."},
        {"role":"user","content":"Is that necessary? I have not chosen a decoding policy."}]
    payload = {"learner_messages":turns,"source_context":{"id":"paper","kind":"document",
        "text":"Named Study: legitimate factual source.\n"*500}}
    original = deepcopy(payload)
    worker.ask(["new",prompt_id],"generation",prompt_id,payload)
    current = writer.calls[-1]
    assert current[-1] == {"role":"system","content":reflection_for_prompt(policy,prompt_id)}
    assert current[-5:-2] == turns
    for rule in policy["rules"]:
        expected = int(prompt_id != "workflow.cot_native_answer" or rule.get("phases") == ["final"])
        if prompt_id == "workflow.cot_rationale" and rule.get("phases") == ["final"]:
            expected = 0
        assert sum(m["content"].count(rule["requirement"]) for m in current) == expected
    assert payload == original
    assert "零基线" not in current[-1]["content"]  # No silent replacement with new defaults.
    worker.recipe.pop("reflection_guidance_placement_version")
    worker.recipe["version"] = 34
    worker.ask(["old",prompt_id],"generation",prompt_id,payload)
    assert writer.calls[-1] == current[:-1]
    worker.recipe["reflection_guidance_placement_version"] = True
    worker.ask(["invalid-marker",prompt_id],"generation",prompt_id,payload)
    assert writer.calls[-1] == current[:-1]  # bool is not an opted-in version.


def test_new_usecase_does_not_duplicate_guidance_before_long_sources():
    config = {**recipe(),"reflection_guidance_placement_version":1}
    policy = config["node_generation"]["cot"]["self_check"]
    port = NoReviewPort([body()])
    row, = GenerateCot(port,config).generate_styled(conversation(),{"instruction":"MY NODE STYLE"})
    assert len(port.calls) == 1 and row["reasoning"] == [body()["reasoning"]]
    instruction = port.calls[0][4]["instruction"]
    assert "MY NODE STYLE" in instruction
    assert all(r["requirement"] not in instruction for r in policy["rules"])


def test_new_native_answer_defers_final_only_guidance_without_losing_prefix():
    rationale=body();rationale.pop("answer")
    port=NoReviewPort([rationale]);port.validate_native_prefill=lambda:None
    calls=[]
    class Answer(dict):
        response_details={"reasoning_prefill":{"transport":"deepseek_chat_beta",
            "prefix_sha256":hashlib.sha256(rationale["reasoning"].encode()).hexdigest()}}
    def answer(*args,**kwargs):
        calls.append(kwargs)
        return Answer(answer=body()["answer"])
    port.request_native_answer=answer
    config={**recipe("native_prefill"),"reflection_guidance_placement_version":1}
    usecase=GenerateCot(port,config);port.generate_native_answer=usecase.generate_native_answer
    row,=usecase.generate_styled(conversation(),{"instruction":"MY NODE STYLE"})
    assert row["status"]=="eligible" and len(calls)==len(port.calls)==1
    assert calls[0]["reasoning_prefix"]==rationale["reasoning"]
    assert config["node_generation"]["cot"]["self_check"]["rules"][-1]["requirement"] not in calls[0].get("instruction", "")


def test_named_source_citation_is_not_automatically_source_leakage_or_a_free_pass():
    from lib.domain.knowledge_voice import knowledge_voice_sentence_issue
    question="How should I compare these scores?"
    assert knowledge_voice_sentence_issue("SELF-INSTRUCT reports results under its own evaluation protocol.",question) is None
    assert knowledge_voice_sentence_issue("The source material directly supports this setting.",question)
    with pytest.raises(ValueError,match="invalid_reflection_prompt"):
        reflection_for_prompt(default_integrated_reflection(),"workflow.review_score")
