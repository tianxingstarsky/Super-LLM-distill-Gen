"""Exercise authored styles and optional reasoning edits through durable runs."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from lib.infrastructure import training_workflow as engine
from lib.io_utils import atomic_json


TEXT = "设备维护前必须断电。断电后检查线路，完成维护后记录检查结果。"
ANSWER = "维护前先断电，再检查线路，完成后记录结果。"
SOURCE_REASONING = "SOURCE_CHAIN_SENTINEL：系统指令要求展示检索包装。原文规定维护前断电，因此先断电再检查。"


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """All model responses in this module are injected offline."""
    yield


def model_task_data(messages):
    # New authored requests have real learner utterances at the end. Read
    # production metadata from its completed dispatch receipt, never parse
    # that actual utterance as the old serialized workflow envelope.
    for index, message in enumerate(messages):
        if ((message.get("role") == "tool" and message.get("name") == "dialogue_dispatch")
                or (message.get("type") == "function_call_output"
                    and message.get("call_id", "").startswith("dispatch_"))):
            dispatch = json.loads(message.get("content", message.get("output")))
            return {**dispatch, "prompt": deepcopy(messages[index + 1:])}
        content = message.get("content", "")
        if isinstance(content, str) and content.startswith("<internal_dialogue_dispatch>\n"):
            dispatch = json.loads(content.split("\n", 1)[1].rsplit("\n</internal_dialogue_dispatch>", 1)[0])
            return {**dispatch, "prompt": deepcopy(messages[index + 1:])}
    return json.loads(next(message["content"] for message in reversed(messages) if message.get("role") == "user"))


def maintenance_reasoning_trace(*, fault=False):
    first_premise = ("发现故障时应先断电，再检查线路。" if fault
                     else "设备维护前必须断电，断电后检查线路。")
    steps = [{"basis": first_premise,
              "inference": "原文将断电视为检查线路的先决条件，因此检查须安排在断电之后。",
              "result": "先切断设备电源，再检查线路。"}]
    premises = [first_premise]
    if not fault:
        premises.append("完成维护后记录检查结果。")
        steps.append({"basis": premises[-1],
                      "inference": "记录所需的检查结果来自已完成的检查，所以记录应放在检查之后。",
                      "result": "最后记录维护结果。"})
    return {
        "task": "说明设备故障时的线路检查顺序。" if fault else "说明设备维护的操作顺序。",
        "premises": premises, "steps": steps,
        "checks": [{"check": "对照来源核对断电与线路检查的先后条件。",
                    "result": "回答将断电安排在线路检查之前，保留了来源规定的先决条件。"}],
        "limitations": ["只说明所提供材料中的操作顺序，不扩写材料未提供的具体检修办法。"],
    }


def score(keep=True):
    value = 5 if keep else 2
    return {"keep": keep, "grounded": keep, "reasoning_valid": keep, "correctness": value,
            "scores": {key: value for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "依据、推导与结论一致。" if keep else "推导遗漏必要条件。"}


class Generator:
    model = "offline-writer"

    def __init__(self, *, malformed_trim=False):
        self.calls = []
        self.kwargs_calls = []
        self.malformed_trim = malformed_trim
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.kwargs_calls.append(dict(kwargs))
        self.usage["calls"] += 1
        instruction = messages[0]["content"]
        if "只生成 replacement reasoning" in instruction:
            if self.malformed_trim and sum("只生成 replacement reasoning" in call[0]["content"] for call in self.calls) == 1:
                return json.dumps({"reasoning": "有效推导", "answer": "试图修改答案"})
            return json.dumps({"reasoning": "维护需要先断电，随后检查线路，最后记录维护结果。"})
        if ("新撰写指定 generation_style 的教学解释" in instruction
                or "新撰写指定 generation_style 的推理文本" in instruction):
            return json.dumps({"reasoning": "新撰写的分步推导：先确认断电前提，再检查线路，最后记录。", "answer": ANSWER,
                               "reasoning_trace": maintenance_reasoning_trace()})
        return json.dumps({"question": "设备应如何维护？", "answer": ANSWER,
                           "reasoning": "先核对断电这一必要前提，然后依次检查与记录。", "quotes": [TEXT]})


class Reviewer:
    model = "offline-reviewer"

    def __init__(self, *, invalid_style=False, reject_style=0, reject_meaning=0):
        self.calls = []
        self.kwargs_calls = []
        self.usage = {"calls": 0}
        self.invalid_style = invalid_style
        self.reject_style = reject_style
        self.reject_meaning = reject_meaning

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.kwargs_calls.append(dict(kwargs))
        self.usage["calls"] += 1
        instruction = messages[0]["content"]
        if "你是实际对话上下文与角色边界审查员" in instruction:
            return json.dumps({"keep": True, "context_complete": True, "leakage_free": True,
                               "reason": "实际问题与回复自包含。"})
        if "adherence 是" in instruction or "你是风格与表达审校员" in instruction:
            if self.invalid_style:
                return json.dumps({"keep": True, "adherence": True, "reason": "不合法的分数"})
            reject = self.reject_style > 0
            self.reject_style -= int(reject)
            return json.dumps({"keep": not reject, "adherence": 2 if reject else 5,
                               "reason": "需要按步骤组织推导。" if reject else "推导组织符合指定规则。"})
        if "replacement_reasoning 是否仍支持原结论" in instruction and self.reject_meaning:
            self.reject_meaning -= 1
            return json.dumps(score(False))
        return json.dumps(score())


def run(tmp_path, *, targets=("sft", "cot"), conversation=False, **options):
    source = tmp_path / ("source.jsonl" if conversation else "source.txt")
    if conversation:
        source.write_text(json.dumps({"messages": [{"role": "user", "content": "设备应如何维护？"},
            {"role": "assistant", "content": ANSWER, "reasoning_content": SOURCE_REASONING}]}, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        source.write_text(TEXT, encoding="utf-8")
    output = tmp_path / "output"
    rid = engine.create_run(output, sources=[source], targets=targets, **options)
    return output, rid, engine.run_path(output, rid)


def records(path, target):
    return engine.read_json(path / "artifacts" / f"{target}.records.json")


def test_style_generation_authors_new_cot_and_never_supplies_source_chain(tmp_path):
    output, rid, path = run(tmp_path, conversation=True, node_generation={
        "sft": {"enabled": True, "style": "concise", "instruction": "保留必要条件"},
        "cot": {"enabled": True, "style": "structured", "instruction": "逐步教学"}})
    generator, reviewer = Generator(), Reviewer()
    state = engine.Workflow(output, rid, tmp_path, generator=generator, jev=reviewer).execute()
    assert state["status"] == "completed"
    assert len(generator.calls) == 2
    assert all(call.get("allow_reasoning_fallback") is False for call in generator.kwargs_calls)
    assert all(call.get("allow_reasoning_fallback") is False for call in reviewer.kwargs_calls)
    assert SOURCE_REASONING not in json.dumps(generator.calls, ensure_ascii=False)
    assert "逐步教学" in generator.calls[-1][0]["content"]
    cot_input = model_task_data(generator.calls[-1])
    assert cot_input["reference_answer"] == ANSWER
    assert "reasoning_content" not in json.dumps(cot_input, ensure_ascii=False)
    assert generator.calls[-1][-1] == {"role": "user", "content": "设备应如何维护？"}
    cot = records(path, "cot")[0]
    assert cot["reasoning_origin"] == "prompt_styled_generation"
    assert cot["generation_style"]["preset"] == "structured"
    assert cot["style_check"]["adherence"] == 5
    training = json.loads((path / "artifacts/cot.jsonl").read_text(encoding="utf-8"))
    assert set(training) == {"question", "reasoning", "answer"}
    assert training["reasoning"] == ["新撰写的分步推导：先确认断电前提，再检查线路，最后记录。"]


@pytest.mark.parametrize("upstream_judge", [None, {}, score(False)])
def test_unassessed_sft_answer_cannot_anchor_cot_or_its_trim_request(tmp_path, upstream_judge):
    output, rid, _ = run(tmp_path, targets=["cot"], node_generation={"cot": {"style": "structured"}},
                         reasoning_trim={"enabled": True, "template": "leakage"})
    generator, reviewer = Generator(), Reviewer()
    workflow = engine.Workflow(output, rid, tmp_path, generator=generator, jev=reviewer)
    workflow.stage = "cot"
    sentinel = "UNASSESSED_ANSWER_MUST_NOT_ANCHOR_THE_DERIVATION"
    sample = {"id": "unassessed", "source_id": "source-1", "status": "eligible",
              "evidence_level": "document_quote", "quotes": [TEXT],
              "source_context": {"kind": "document", "text": TEXT},
              "messages": [{"role": "user", "content": "设备应如何维护？"},
                           {"role": "assistant", "content": sentinel, "reasoning_content": SOURCE_REASONING}],
              **({"judge": upstream_judge} if upstream_judge is not None else {})}
    generated = workflow.styled_cot(sample, workflow.generation_style("cot", sample["id"]))[0]
    assert generated["status"] == "eligible" and generated["answer"] == ANSWER
    workflow.stage = "trim"
    trimmed = workflow.trim_reasoning({"target": "cot", "sample": generated})[0]
    assert trimmed["status"] == "eligible" and trimmed["answer"] == ANSWER
    assert len(generator.calls) == 2
    request_body = json.dumps(generator.calls, ensure_ascii=False)
    assert sentinel not in request_body and SOURCE_REASONING not in request_body
    assert TEXT in request_body
    assert "独立依据真实问题和原始知识" in generator.calls[0][0]["content"]
    assert "reference_answer" not in model_task_data(generator.calls[0])
    for messages in generator.calls:
        assert messages[-1] == {"role": "user", "content": "设备应如何维护？"}


def test_disabled_styles_keep_ordinary_distillation_and_enabled_default_is_reviewed(tmp_path):
    output, rid, path = run(tmp_path, conversation=True, node_generation={
        "sft": {"enabled": False, "style": "custom"}, "cot": {"enabled": False, "style": "structured"}})
    generator, reviewer = Generator(), Reviewer()
    assert engine.Workflow(output, rid, tmp_path, generator=generator, jev=reviewer).execute()["status"] == "completed"
    assert generator.calls == []
    assert records(path, "sft")[0]["reasoning_origin"] == "source"
    assert SOURCE_REASONING in "".join(records(path, "cot")[0]["reasoning"])
    other = tmp_path / "other"
    other.mkdir()
    output, rid, path = run(other, targets=["sft"], node_generation={"sft": {"style": "default"}})
    generator, reviewer = Generator(), Reviewer()
    assert engine.Workflow(output, rid, other, generator=generator, jev=reviewer).execute()["status"] == "completed"
    assert len(generator.calls) == 1 and len(reviewer.calls) == 2
    assert "generation_style" in model_task_data(generator.calls[0])
    assert records(path, "sft")[0]["style_check"]["adherence"] == 5


def test_style_rejection_repairs_once_then_isolates_with_separate_evidence(tmp_path):
    output, rid, path = run(tmp_path, targets=["sft"], node_generation={"sft": {"style": "structured"}})
    generator, reviewer = Generator(), Reviewer(reject_style=2)
    state = engine.Workflow(output, rid, tmp_path, generator=generator, jev=reviewer).execute()
    assert state["status"] == "needs_attention"
    assert len(generator.calls) == 2
    feedback = model_task_data(generator.calls[-1])["feedback"]
    assert feedback == {"correctness": None, "style": "需要按步骤组织推导。"}
    row = records(path, "sft")[0]
    assert row["status"] == "quarantined" and row["judge"]["keep"]
    assert row["style_check"]["keep"] is False and row["repair_attempts"] == 1
    assert (path / "artifacts/sft.jsonl").read_text(encoding="utf-8") == ""


def test_invalid_style_score_can_resume_without_regenerating_answer(tmp_path):
    output, rid, path = run(tmp_path, targets=["sft"], node_generation={"sft": {"style": "concise"}})
    generator = Generator()
    state = engine.Workflow(output, rid, tmp_path, generator=generator, jev=Reviewer(invalid_style=True)).execute()
    assert state["status"] == "failed" and state["error"] == "invalid_style_judge_schema"
    state = engine.resume(output, rid, tmp_path, generator=generator, jev=Reviewer())
    assert state["status"] == "completed" and len(generator.calls) == 1


def test_cot_style_rejection_is_separate_from_correctness_and_repairs_once(tmp_path):
    output, rid, path = run(tmp_path, node_generation={"cot": {"style": "structured"}})
    generator = Generator()
    state = engine.Workflow(output, rid, tmp_path, generator=generator, jev=Reviewer(reject_style=2)).execute()
    assert state["status"] == "needs_attention" and len(generator.calls) == 3
    row = records(path, "cot")[0]
    assert row["reason"] == "cot_generation_failed_after_repair"
    assert row["judge"]["keep"] is True and row["style_check"]["keep"] is False
    assert row["feedback"] == {"correctness": None, "style": "需要按步骤组织推导。"}


class MissingTraceGenerator(Generator):
    def chat(self, messages, **kwargs):
        value = json.loads(super().chat(messages, **kwargs))
        value.pop("reasoning_trace", None)
        return json.dumps(value, ensure_ascii=False)


def test_failed_authored_cot_retains_exact_retry_input_and_complete_source_reference(tmp_path):
    from lib.domain.source_line_ranges import number_source_material
    from lib.infrastructure.workflow_source_context import _save_pack, load_context_pack

    output, rid, path = run(tmp_path, targets=["cot"], node_generation={"cot": {"style": "structured"}})
    generator, reviewer = MissingTraceGenerator(), Reviewer()
    workflow = engine.Workflow(output, rid, tmp_path, generator=generator, jev=reviewer)
    workflow.stage = "cot"
    # Long cleaned evidence and a final unique fact must survive a failed call
    # without being clipped to a history or preview limit.
    text = (TEXT + "\r\n") * 2000 + "TAIL_FACT：完成检查后，记录编号为 MX-2049。"
    directory = path / "source-context"
    (directory / "packs").mkdir(parents=True, exist_ok=True)
    material = number_source_material({"unit_id": "retry-source", "source_id": "source-1",
        "text": text, "cleaning_status": "accepted", "cleaned_text_sha256": engine.digest(text)})
    reference = _save_pack(directory, [material])
    source = {"kind": "document", "text": text, "cleaned_text_sha256": engine.digest(text),
              "source_context_pack": reference}
    prompt = [{"role": "user", "content": "设备应如何维护？保留字面量\\n和真实换行\n说明记录编号。"}]
    unchecked_answer = "UNCHECKED_FINAL_ANSWER_MUST_NOT_BE_RETAINED"
    sample = {"id": "failed-cot", "source_id": "source-1", "status": "eligible",
        "evidence_level": "source_text", "source_context": source,
        "messages": [*deepcopy(prompt), {"role": "assistant", "content": unchecked_answer,
                                        "reasoning_content": SOURCE_REASONING}]}
    original = deepcopy(sample)
    row = workflow.styled_cot(sample, workflow.generation_style("cot", sample["id"]))[0]

    assert len(generator.calls) == 2 and reviewer.calls == []
    assert row["status"] == "quarantined" and row["reason"] == "cot_generation_failed_after_repair"
    assert row["feedback"]["reasoning_protocol"] == "authored_reasoning_missing_trace"
    assert row["question"] == prompt and row["source_context"] == source
    assert row["input_digest"] == engine.digest({"question": prompt, "source_context": source})
    assert row["sft_parent_id"] == sample["id"] and row["generation_style"]["preset"] == "structured"
    recovered = load_context_pack(path, row["source_context"]["source_context_pack"])
    assert recovered["materials"][0]["text"].encode("utf-8") == text.encode("utf-8")
    assert recovered["materials"][0]["cleaned_text_sha256"] == engine.digest(text)
    assert all(key not in row for key in ("answer", "reasoning", "messages", "reasoning_trace"))
    assert unchecked_answer not in json.dumps(row, ensure_ascii=False)
    assert SOURCE_REASONING not in json.dumps(row, ensure_ascii=False)
    assert unchecked_answer not in json.dumps(generator.calls, ensure_ascii=False)
    assert SOURCE_REASONING not in json.dumps(generator.calls, ensure_ascii=False)
    assert sample == original


def test_missing_trace_twice_is_quarantined_and_never_published_as_cot_or_sft(tmp_path):
    output, rid, path = run(tmp_path, node_generation={"cot": {"style": "structured"}})
    generator, reviewer = MissingTraceGenerator(), Reviewer()
    state = engine.Workflow(output, rid, tmp_path, generator=generator, jev=reviewer).execute()
    assert state["status"] == "needs_attention" and len(generator.calls) == 3
    cot = records(path, "cot")[0]
    with (path / "stage-results" / "sft.jsonl").open(encoding="utf-8") as handle:
        original_sft = json.loads(next(handle))
    assert cot["question"] == engine.Workflow.without_source_reasoning(original_sft["messages"][:-1])
    assert cot["source_context"] and cot["input_digest"] == engine.digest({
        "question": cot["question"], "source_context": cot["source_context"]})
    assert all(key not in cot for key in ("answer", "reasoning", "messages", "reasoning_trace"))
    for target in ("cot", "sft"):
        assert records(path, target)[0]["status"] == "quarantined"
        assert (path / "artifacts" / f"{target}.jsonl").read_text(encoding="utf-8") == ""
    assert engine.read_json(path / "artifacts" / "manifest.json")["counts"] == {"sft": 0, "cot": 0}


def test_trim_invalid_rules_score_can_resume_without_regenerating(tmp_path):
    output, rid, path = run(tmp_path, targets=["sft"], conversation=True,
                            reasoning_trim={"enabled": True, "template": "leakage"})
    generator = Generator()
    state = engine.Workflow(output, rid, tmp_path, generator=generator, jev=Reviewer(invalid_style=True)).execute()
    assert state["status"] == "failed" and state["error"] == "invalid_style_judge_schema"
    assert state["stages"]["sft"]["status"] == "completed"
    state = engine.resume(output, rid, tmp_path, generator=generator, jev=Reviewer())
    assert state["status"] == "completed" and len(generator.calls) == 1
    assert records(path, "sft")[0]["reasoning_trim"]["status"] == "applied"


def test_trim_does_not_rewrite_preference_pair_evidence(tmp_path):
    class PreferenceWriter(Generator):
        def chat(self, messages, **kwargs):
            if "另一个独立" in messages[0]["content"]:
                self.calls.append(deepcopy(messages))
                self.usage["calls"] += 1
                return json.dumps({"answer": "BAD 无需断电即可检查线路。", "reasoning": "无需核对断电条件。"})
            return super().chat(messages, **kwargs)

    class PreferenceReviewer(Reviewer):
        def chat(self, messages, **kwargs):
            if "BAD" in messages[1]["content"]:
                self.calls.append(deepcopy(messages))
                self.usage["calls"] += 1
                return json.dumps(score(False))
            return super().chat(messages, **kwargs)

    output, rid, path = run(tmp_path, targets=["sft", "dpo", "orpo", "rlaif"], conversation=True,
                            reasoning_trim={"enabled": True, "template": "leakage"})
    state = engine.Workflow(output, rid, tmp_path, generator=PreferenceWriter(), jev=PreferenceReviewer()).execute()
    assert state["status"] == "completed"
    assert "SOURCE_CHAIN_SENTINEL" not in json.dumps(records(path, "sft")[0], ensure_ascii=False)
    for target in ("dpo", "orpo", "rlaif"):
        row = records(path, target)[0]
        assert row["chosen"][0]["reasoning_content"] == SOURCE_REASONING
        assert "reasoning_trim" not in row
    assert engine.read_json(path / "artifacts/quality.json")["reasoning_trim"]["applied_targets"] == ["sft"]


def test_recipe_pins_style_and_trim_instructions_before_execution(tmp_path, monkeypatch):
    from lib.domain import workflow_generation, reasoning_trim
    config = {"sft": {"style": "structured", "instruction": "固定附加要求"}}
    trim = {"enabled": True, "template": "leakage"}
    output, rid, path = run(tmp_path, targets=["sft"], node_generation=config, reasoning_trim=trim)
    config["sft"]["instruction"] = "外部改动"
    trim["template"] = "concise"
    monkeypatch.setitem(workflow_generation.STYLE_PRESETS, "structured", "CHANGED_STYLE_SENTINEL")
    monkeypatch.setitem(reasoning_trim.TRIM_TEMPLATES, "leakage", "CHANGED_TRIM_SENTINEL")
    generator = Generator()
    assert engine.Workflow(output, rid, tmp_path, generator=generator, jev=Reviewer()).execute()["status"] == "completed"
    assert "固定附加要求" in generator.calls[0][0]["content"]
    assert "node_task_instructions" not in model_task_data(generator.calls[0])
    assert "CHANGED_STYLE_SENTINEL" not in str(generator.calls[0])
    assert "CHANGED_TRIM_SENTINEL" not in str(generator.calls[-1])
    recipe = engine.read_json(path / "recipe.json")
    assert recipe["version"] == engine.SOURCE_CLEANING_RECIPE_VERSION and recipe["reasoning_trim"]["template"] == "leakage"


def test_trim_keeps_answers_and_upstream_rows_but_does_not_duplicate_original_chain_in_bundle(tmp_path):
    output, rid, path = run(tmp_path, conversation=True, reasoning_trim={"enabled": True, "template": "leakage"})
    # Historical independent deliveries retain their original two trim jobs.
    recipe = engine.read_json(path / "recipe.json")
    recipe["version"] = 14
    atomic_json(path / "recipe.json", recipe)
    saved = engine.read_json(path / "state.json")
    saved["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "state.json", saved)
    generator, reviewer = Generator(malformed_trim=True), Reviewer()
    state = engine.Workflow(output, rid, tmp_path, generator=generator, jev=reviewer).execute()
    assert state["status"] == "completed" and state["reasoning_trim_enabled"] is True
    assert state["stages"]["trim"]["done"] == 2
    for target in ("sft", "cot"):
        row = records(path, target)[0]
        assert row["reasoning_trim"]["status"] == "applied"
        assert row["reasoning_trim"]["fields"][0]["input_sha256"]
        assert "SOURCE_CHAIN_SENTINEL" not in json.dumps(row, ensure_ascii=False)
        payload = json.loads((path / "artifacts" / f"{target}.jsonl").read_text(encoding="utf-8"))
        assert (payload["messages"][-1]["content"] if target == "sft" else payload["answer"]) == ANSWER
        assert "SOURCE_CHAIN_SENTINEL" in (path / "stage-results" / f"{target}.jsonl").read_text(encoding="utf-8")
    assert records(path, "sft")[0]["reasoning_trim"]["fields"][0]["repair_attempts"] == 1
    assert engine.verify_artifacts(path)["counts"] == {"sft": 1, "cot": 1}


def test_custom_trim_is_a_fixed_system_instruction_and_failed_meaning_is_quarantined(tmp_path):
    output, rid, path = run(tmp_path, targets=["sft"], conversation=True, reasoning_trim={
        "enabled": True, "template": "custom", "custom_prompt": "CUSTOM_TRIM_SENTINEL：只精简无关包装。"})
    generator = Generator()
    state = engine.Workflow(output, rid, tmp_path, generator=generator, jev=Reviewer(reject_meaning=2)).execute()
    assert state["status"] == "needs_attention"
    assert len(generator.calls) == 2
    assert all("CUSTOM_TRIM_SENTINEL" in call[0]["content"] for call in generator.calls)
    row = records(path, "sft")[0]
    assert row["reason"] == "reasoning_trim_failed_after_repair"
    assert row["reasoning_trim"]["judge"]["keep"] is False
    assert row["reasoning_trim"]["rules_check"]["keep"] is True
    assert "SOURCE_CHAIN_SENTINEL" not in json.dumps(row, ensure_ascii=False)


def test_new_prompt_ids_do_not_break_legacy_recipe_execution(tmp_path):
    output, rid, path = run(tmp_path, conversation=True)
    recipe = engine.read_json(path / "recipe.json")
    recipe["version"] = 7
    for key in ("node_generation", "node_generation_prompts", "reasoning_trim", "reasoning_trim_prompt"):
        recipe.pop(key, None)
    for key in ("workflow.sft_styled", "workflow.cot_generate", "workflow.style_check",
                "workflow.trim", "workflow.trim_check", "workflow.trim_rules_check"):
        recipe["prompts"].pop(key, None)
    atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "state.json", state)
    generator = Generator()
    assert engine.Workflow(output, rid, tmp_path, generator=generator, jev=Reviewer()).execute()["status"] == "completed"
    assert generator.calls == []


def test_trim_rejects_unsupported_export_targets_before_creating_run(tmp_path):
    with pytest.raises(ValueError, match="reasoning_trim_requires_reasoning_target"):
        engine.create_run(tmp_path / "output", brief="maintenance", targets=["dpo"],
                          reasoning_trim={"enabled": True})
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("allow_fallback", [False, True])
def test_native_only_json_cannot_become_styled_content_but_ordinary_fallback_is_retained(streaming, allow_fallback):
    from types import SimpleNamespace as Obj
    from lib.llm_client import ChatClient, chat_json
    from lib.model_request_reliability import ModelJSONError

    client = ChatClient("http://127.0.0.1:1/v1", "test", "offline-native-reasoner")
    native = '{"answer":"native-channel-only"}'
    calls, events = [], []

    def create(**kwargs):
        calls.append(kwargs)
        assert "allow_reasoning_fallback" not in kwargs
        if kwargs.get("stream"):
            return iter([
                Obj(choices=[Obj(index=0, delta=Obj(content=None, reasoning_content=native), finish_reason=None)], usage=None),
                Obj(choices=[Obj(index=0, delta=Obj(), finish_reason="stop")],
                    usage=Obj(prompt_tokens=17, completion_tokens=8))])
        return Obj(choices=[Obj(message=Obj(content=None, reasoning_content=native), finish_reason="stop")],
                   usage=Obj(prompt_tokens=17, completion_tokens=8))

    client.client.chat.completions.create = create
    try:
        kwargs = {"on_stream": events.append} if streaming else {}
        if allow_fallback:
            assert chat_json(client, [{"role": "user", "content": "JSON"}],
                             allow_reasoning_fallback=True, **kwargs) == {"answer": "native-channel-only"}
        else:
            with pytest.raises(ModelJSONError, match="model_json_invalid"):
                chat_json(client, [{"role": "user", "content": "JSON"}],
                          retries=2, allow_reasoning_fallback=False, **kwargs)
        count = 1 if allow_fallback else 2
        assert len(calls) == count
        assert client.usage == {"calls": count, "prompt_tokens": 17 * count, "completion_tokens": 8 * count}
    finally:
        client.client.close()
