"""Real provider payloads preserve selected evidence as tools, never user uploads."""
from copy import deepcopy
import json
from types import SimpleNamespace as Obj

import pytest

from lib.domain.source_line_ranges import number_source_material
from lib.domain.workflow_quality import canonical
from lib.infrastructure.model_context_prefix import split_context_request
from lib.infrastructure.source_memory_messages import source_memory_messages, sealed_review_memory_context
from lib.infrastructure.workflow_source_context import _pack_payload
from lib.infrastructure import training_workflow as engine
from lib.llm_client import ChatClient
from lib.model_protocols import responses_input, anthropic_request


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


RAW = "OUTSIDE_START\r\nMX pump requires power off.\r\nWait 10 minutes before opening.\r\nOUTSIDE_END"
SELECTED = "MX pump requires power off.\r\nWait 10 minutes before opening.\r\n"
RANGES = [{"material_id": "material-a", "start_line": 2, "end_line": 3}]


def pack():
    return _pack_payload([number_source_material({"unit_id": "material-a", "text": RAW,
        "source_id": "manual", "source_name": "pump.md", "cleaning_status": "accepted",
        "cleaned_text_sha256": engine.digest(RAW)})])


def request():
    full = {**pack(), "source_line_protocol_version": 1}
    return {"source_line_scope": "selected", "source_line_ranges": deepcopy(RANGES),
        "source_context": {"id": "material-a", "kind": "document", "text": RAW,
                           "source_context_pack": full, "source_line_focus": [{"text": "FORGED"}]},
        "qa_contract": {"question": "How should the MX pump be prepared?", "visible_context": "FORGED"}}


def memory(data=None, **kwargs):
    return source_memory_messages("Create self-contained training samples.",
        split_context_request("unused", request() if data is None else data), **kwargs)


def test_exact_selected_original_lines_are_only_in_tool_result_and_inputs_unchanged():
    data = request()
    original = deepcopy(data)
    messages = memory(data)
    tool = next(message for message in messages if message["role"] == "tool")
    body = json.loads(tool["content"])
    assert body["teacher_context_pack"][0]["materials"][0]["text"] == SELECTED
    assert "OUTSIDE" not in canonical(messages) and "FORGED" not in canonical(messages)
    assert "MX pump requires" not in canonical([m for m in messages if m["role"] == "user"])
    assert data == original


def test_identical_selected_memory_keeps_prefix_through_task_and_feedback_changes():
    first = memory()
    data = request()
    data.update(feedback="Please repair the answer", question="Another task")
    second = memory(data)
    assert first[:-1] == second[:-1] and first[-1] != second[-1]


def test_wrapped_judge_context_preserves_selected_scope_without_raw_source_suffix():
    data = {"context": {**request(), "source_text": RAW}, "answer": "Power off and wait ten minutes."}
    messages = memory(data)
    assert "OUTSIDE" not in canonical(messages)
    assert json.loads(messages[3]["content"])["teacher_context_pack"][0]["materials"][0]["text"] == SELECTED
    assert "MX pump requires" not in messages[-1]["content"]


def test_conflicting_nested_scopes_are_rejected_without_transmission():
    nested = request()
    nested["source_line_ranges"] = [{"material_id": "material-a", "start_line": 1, "end_line": 1}]
    with pytest.raises(ValueError, match="conflicting_source_line_ranges"):
        memory({**request(), "context": nested})


def test_sealed_revision_memory_is_private_and_keeps_separate_original_blocks():
    data = {"source_evidence": {"teacher_evidence": SELECTED, "source_id": "manual", "source_kind": "document",
            "teacher_evidence_blocks": [SELECTED], "source_line_scope": "selected", "source_line_ranges": RANGES},
            "candidate": {"messages": [{"role": "user", "content": "How should the pump be prepared?"}]}}
    context = sealed_review_memory_context(data)
    messages = source_memory_messages("Repair the actual candidate.", context)
    assert SELECTED == json.loads(messages[3]["content"])["teacher_context_pack"][0]["materials"][0]["text"]
    assert "MX pump requires" not in messages[-1]["content"]
    assert json.loads(messages[-1]["content"])["candidate"] == data["candidate"]
    data["instruction"] = "Please clarify the waiting time."
    second = source_memory_messages("Repair the actual candidate.", sealed_review_memory_context(data))
    assert messages[:-1] == second[:-1]


def test_full_custom_fallback_removes_private_quotes_focus_inline_body_from_user():
    data = request()
    data.pop("source_line_scope")
    data.pop("source_line_ranges")
    data["qa_contract"].update(evidence_quotes=["PRIVATE_QUOTE"], source_line_focus=[{"text": "PRIVATE_FOCUS"}])
    messages = memory(data)
    user = canonical([m for m in messages if m["role"] == "user"])
    assert all(sentinel not in user for sentinel in ("PRIVATE_QUOTE", "PRIVATE_FOCUS", "OUTSIDE_START", "MX pump requires"))
    args = json.loads(messages[2]["tool_calls"][0]["function"]["arguments"])
    assert args["scope"] == "full" and "ranges" not in args
    assert "OUTSIDE_START" in messages[3]["content"]


def test_explicit_non_tool_compatibility_uses_labelled_internal_memory_not_user():
    messages = memory(transport="system")
    assert [m["role"] for m in messages] == ["system", "system", "user"]
    assert "<untrusted_source_memory>" in messages[1]["content"]
    assert SELECTED in json.loads(messages[1]["content"].split("\n", 1)[1].rsplit("\n", 1)[0])["teacher_context_pack"][0]["materials"][0]["text"]
    assert "MX pump requires" not in messages[-1]["content"]


@pytest.mark.parametrize("api_format", ["chat", "responses", "anthropic"])
def test_sdk_request_uses_each_providers_real_completed_tool_history(api_format):
    client = ChatClient("https://api.example.test/v1", "unused", "writer", api_format=api_format)
    captured = []
    def create(**kwargs):
        captured.append(deepcopy(kwargs))
        if api_format == "responses":
            return Obj(output_text="ok", usage=None, status="completed")
        if api_format == "anthropic":
            return Obj(content=[Obj(type="text", text="ok")], usage=None, stop_reason="end_turn")
        return Obj(choices=[Obj(message=Obj(content="ok"), finish_reason="stop")], usage=None)
    if api_format == "responses":
        client.client.responses.create = create
    elif api_format == "anthropic":
        client.client.messages.create = create
    else:
        client.client.chat.completions.create = create
    messages = memory()
    assert client.chat(messages, max_tokens=512) == "ok"
    sent = captured[0]
    if api_format == "chat":
        assert sent["messages"][3]["role"] == "tool"
        assert sent["messages"][3]["tool_call_id"] == sent["messages"][2]["tool_calls"][0]["id"]
    elif api_format == "responses":
        call, result = sent["input"][2:4]
        assert call["type"] == "function_call" and result["type"] == "function_call_output"
        assert call["call_id"] == result["call_id"]
        assert "MX pump requires" not in canonical([m for m in sent["input"] if m.get("role") == "user"])
    else:
        use = sent["messages"][1]["content"][0]
        result, task = sent["messages"][2]["content"]
        assert use["type"] == "tool_use" and result["type"] == "tool_result"
        assert use["id"] == result["tool_use_id"] and task["type"] == "text"
        assert "MX pump requires" not in task["text"]
        assert sent["tool_choice"] == {"type": "none"}


@pytest.mark.parametrize("adapter", [responses_input, lambda messages: anthropic_request(messages, json_mode=False)])
@pytest.mark.parametrize("mutation", ["orphan", "missing", "duplicate", "bad_arguments"])
def test_invalid_tool_history_is_rejected_before_provider(adapter, mutation):
    messages = memory()
    if mutation == "orphan":
        messages[3]["tool_call_id"] = "wrong"
    elif mutation == "missing":
        messages.pop(3)
    elif mutation == "duplicate":
        messages[2]["tool_calls"].append(deepcopy(messages[2]["tool_calls"][0]))
    else:
        messages[2]["tool_calls"][0]["function"]["arguments"] = "[]"
    with pytest.raises(ValueError):
        adapter(messages)


def test_tool_arguments_and_result_are_included_in_context_preflight():
    messages = memory()
    content_bytes = sum(len(m["content"].encode()) for m in messages)
    client = ChatClient("https://api.example.test/v1", "unused", "writer", context_window_tokens=content_bytes + 600)
    with pytest.raises(ValueError, match="model_context_window_exceeded"):
        client._check_context(messages, 128)


class CapturingGenerator:
    model = "offline-writer"
    usage = {}
    def __init__(self, values=None):
        self.calls = []
        self.values = iter(values or [{"ok": True}])
    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        return json.dumps(next(self.values), ensure_ascii=False)


def workflow(tmp_path, generator, **options):
    source = tmp_path / "source.md"
    source.write_text(RAW, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft", "cot"], **options)
    return engine.Workflow(output, run_id, tmp_path, generator=generator)


def trace():
    return {"task": "Prepare the MX pump safely.", "premises": ["MX pump requires power off.", "Opening must wait ten minutes."],
        "steps": [{"basis": "Power off and a ten-minute wait are required before opening.",
            "inference": "Both prerequisites must be completed before the cover is opened.", "result": "Power off, then wait ten minutes."}],
        "checks": [{"check": "Compare the proposed order with both prerequisites.", "result": "Power is off and ten minutes elapse before opening; neither prerequisite is skipped."}],
        "limitations": ["This applies to the MX pump described in the task."]}


def test_workflow_ask_injects_selected_tool_memory_and_preserves_actual_learner_task(tmp_path):
    generator = CapturingGenerator()
    worker = workflow(tmp_path, generator)
    worker.stage = "sft"
    data = request()
    data["learner_messages"] = [{"role": "user", "content": "How should the MX pump be prepared?"}]
    assert worker.ask(["unit-a", "test"], "generation", "workflow.sft_directed", data) == {"ok": True}
    assert [m["role"] for m in generator.calls[0]] == ["system", "user", "assistant", "tool", "user"]
    assert "OUTSIDE" not in canonical(generator.calls[0])
    assert worker.state["source_memory_transports"]["sft.generation"] == "tool"


def test_review_only_ask_uses_authenticated_receipt_without_resolving_parent_pack_under_child(tmp_path):
    generator = CapturingGenerator()
    worker = workflow(tmp_path, generator)
    worker.recipe["repair_inputs"] = [{"record_id": "sample-a"}]
    worker.recipe.pop("source_context_pack_version", None)
    worker.stage = "review"
    data = {"target": "sft", "candidate": {"messages": [{"role": "user", "content": "Prepare the pump."}]},
            "source_evidence": {"teacher_evidence": SELECTED, "source_id": "manual", "source_kind": "document"}}
    assert worker.ask(["sample-a", "repair-test"], "generation", "workflow.review_repair", data) == {"ok": True}
    messages = generator.calls[0]
    assert messages[3]["role"] == "tool"
    assert "MX pump requires" in messages[3]["content"] and "MX pump requires" not in messages[-1]["content"]


def test_cot_contract_repairs_missing_trace_before_review_node_shortcut(tmp_path):
    authored = {"reasoning": "Power must be off and ten minutes must elapse before opening.",
                "answer": "Power off, wait ten minutes, then open.", "reasoning_trace": trace()}
    generator = CapturingGenerator([{key: value for key, value in authored.items() if key != "reasoning_trace"}, authored])
    worker = workflow(tmp_path, generator, node_generation={"cot": {"style": "structured"}},
                      review_repair={"mode": "auto", "max_rounds": 1}, qa_director={"enabled": True})
    worker.stage = "cot"
    source = {"kind": "document", "source_context_pack": {**pack(), "source_line_protocol_version": 1}}
    sample = {"id": "sample-a", "source_id": "manual", "source_context": source,
        "qa_contract": {"source_line_ranges": RANGES}, "evidence_level": "source_and_model_assessed",
        "messages": [{"role": "user", "content": "How should the MX pump be prepared?"},
                     {"role": "assistant", "content": authored["answer"], "reasoning_content": "NATIVE_HIDDEN_SENTINEL"}]}
    rows = worker.styled_cot(sample, worker.generation_style("cot", sample["id"]))
    assert rows[0]["status"] == "eligible" and rows[0]["repair_attempts"] == 1
    assert rows[0]["reasoning_protocol_version"] == 1 and rows[0]["reasoning_trace"] == trace()
    assert len(generator.calls) == 2
    assert "authored_reasoning_missing_trace" in canonical(generator.calls[1][-1])
    assert "NATIVE_HIDDEN_SENTINEL" not in canonical(generator.calls)
    assert "OUTSIDE" not in canonical(generator.calls)


def test_new_custom_cot_template_still_enforces_output_contract(tmp_path):
    generator = CapturingGenerator([{"reasoning": "Some adequate explanation", "answer": "answer"}] * 2)
    worker = workflow(tmp_path, generator, node_generation={"cot": {"style": "structured"}},
        node_prompts={"cot": {"workflow.cot_generate": "Use my chosen writing style."}},
        review_repair={"mode": "auto"})
    worker.stage = "cot"
    sample = {"id": "sample-a", "source_id": "manual", "evidence_level": "human_provided_and_model_assessed",
        "messages": [{"role": "user", "content": "Explain safe preparation."}, {"role": "assistant", "content": "Wait ten minutes."}]}
    result = worker.styled_cot(sample, worker.generation_style("cot", sample["id"]))
    assert result[0]["status"] == "quarantined"
    assert all("reasoning_trace" in messages[0]["content"] for messages in generator.calls)
