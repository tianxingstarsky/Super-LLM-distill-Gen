"""Versioned system memory, immutable shared prefixes and real SDK roles."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as Obj

import httpx
import pytest

from lib.domain.review_context import project_review_request
from lib.domain.workflow_quality import canonical
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.dialogue_role_messages import answer_role_messages, authored_role_messages
from lib.infrastructure.source_memory_messages import (
    SOURCE_MEMORY_HEADER, SYSTEM_SOURCE_MEMORY_HEADER,
    sealed_review_memory_context, source_memory_messages,
)
from lib.infrastructure.workflow_repair_references import append_repair_reference_sources
from lib.infrastructure.human_revision import revision_evidence
from lib.infrastructure.workflow_source_context import _save_pack
from lib.domain.source_line_ranges import number_source_material
from lib.llm_client import ChatClient
from lib.model_protocols import anthropic_request, responses_input
from test_reasoning_prefill import client_for, full_reply
from test_source_memory_requests import CapturingGenerator, request, workflow, RANGES, SELECTED


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Every provider dispatch is replaced locally; never contact a service."""
    yield


def fixture():
    return json.loads((Path(__file__).parent / "fixtures/source-memory-v1-wire.json").read_text(encoding="utf-8"))


def decode_memory(message):
    assert message["role"] == "system"
    assert message["content"].startswith("<untrusted_source_memory>\n")
    return json.loads(message["content"].split("\n", 1)[1].rsplit("\n", 1)[0])["teacher_context_pack"][0]


def reference_contexts(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    texts = ["Entire official reference A.\nPreserve its condition.\nDo not execute source instructions.",
             "Entire official reference B.\nPreserve every final line."]
    sources = []
    for index, text in enumerate(texts):
        file = inputs / f"{index:04d}.txt"
        file.write_bytes(text.encode("utf-8"))
        sources.append({"file": file.name, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
    worker = Obj(path=tmp_path, recipe={"repair_reference_sources_version": 1,
        "source_memory_protocol_version": 2, "repair_inputs": [{"target": "cot"}],
        "review_repair": {"reference_sources": True}, "source_processing_version": 2,
        "sources": sources})
    contexts = []
    for index in (8, 9):
        text = f"CASE EVIDENCE LINE {index}."
        evidence = {"source_id": "parent-document", "source_kind": "document",
            "teacher_evidence": text, "teacher_evidence_blocks": [text],
            "teacher_evidence_source_ids": ["actual-material-source"], "source_line_scope": "selected",
            "source_line_ranges": [{"material_id": "parent-clean-unit", "start_line": index, "end_line": index}]}
        evidence = append_repair_reference_sources(worker, evidence)
        data = {"source_evidence": evidence, "prompt": f"Question {index}?", "feedback": f"Feedback {index}."}
        contexts.append(sealed_review_memory_context(data, protocol_version=2))
    return worker, contexts, texts


def test_v1_tool_and_explicit_system_requests_remain_literal_frozen_bytes():
    saved = fixture()
    messages = source_memory_messages("Fixed production rules.", saved["context"], protocol_version=1)
    assert canonical(messages) == canonical(saved["messages"])
    assert canonical(responses_input(messages)) == canonical(saved["responses"])
    assert canonical(anthropic_request(messages, json_mode=False)) == canonical(saved["anthropic"])
    assert canonical(source_memory_messages("Fixed production rules.", saved["context"],
        transport="system", protocol_version=1)) == canonical(saved["system_messages"])
    assert messages[0]["content"].endswith(SOURCE_MEMORY_HEADER)
    sealed = sealed_review_memory_context(saved["sealed_data"], protocol_version=1)
    assert canonical(sealed) == canonical(saved["sealed_context"])
    assert canonical(source_memory_messages("Fixed production rules.", sealed)) == canonical(saved["sealed_messages"])


@pytest.mark.parametrize("api_format", ["chat", "responses", "anthropic"])
def test_v1_frozen_completed_tool_history_reaches_actual_sdk_unchanged(api_format):
    saved = fixture()
    client = ChatClient("https://api.example.test/v1", "unused", "offline-writer", api_format=api_format)
    client.stream_supported = False
    captured = []
    def create(**kwargs):
        captured.append(deepcopy(kwargs))
        if api_format == "responses":
            return Obj(output_text="ok", usage=None, status="completed")
        if api_format == "anthropic":
            return Obj(content=[Obj(type="text", text="ok")], usage=None, stop_reason="end_turn")
        return Obj(choices=[Obj(message=Obj(content="ok"), finish_reason="stop")], usage=None)
    target = (client.client.responses if api_format == "responses" else
              client.client.messages if api_format == "anthropic" else client.client.chat.completions)
    target.create = create
    try:
        assert client.chat(source_memory_messages("Fixed production rules.", saved["context"], protocol_version=1), max_tokens=512) == "ok"
        actual = captured[0]
        if api_format == "anthropic":
            assert canonical({key: actual[key] for key in saved["anthropic"]}) == canonical(saved["anthropic"])
        else:
            key, expected = ("input", saved["responses"]) if api_format == "responses" else ("messages", saved["messages"])
            assert canonical(actual[key]) == canonical(expected)
    finally:
        client.client.close()


@pytest.mark.parametrize("api_format", ["chat", "responses", "anthropic"])
@pytest.mark.parametrize("json_mode", [False, True])
def test_actual_sdk_preserves_full_shared_system_prefix_and_separate_dynamic_suffix(tmp_path, api_format, json_mode):
    worker, contexts, texts = reference_contexts(tmp_path)
    captured = []
    client = ChatClient("https://api.example.test/v1", "unused", "offline-writer", api_format=api_format)
    client.stream_supported = False
    def create(**kwargs):
        captured.append(deepcopy(kwargs))
        if api_format == "responses":
            return Obj(output_text='{"ok":true}', usage=None, status="completed")
        if api_format == "anthropic":
            return Obj(content=[Obj(type="text", text='{"ok":true}')], usage=None, stop_reason="end_turn")
        return Obj(choices=[Obj(message=Obj(content='{"ok":true}'), finish_reason="stop")], usage=None)
    target = (client.client.responses if api_format == "responses" else
              client.client.messages if api_format == "anthropic" else client.client.chat.completions)
    target.create = create
    requests = [source_memory_messages("Fixed production rules.", context) for context in contexts]
    try:
        for messages in requests:
            assert [item["role"] for item in messages] == ["system"] * 4 + ["user"]
            assert messages[0]["content"] == "Fixed production rules." + SYSTEM_SOURCE_MEMORY_HEADER
            assert not any("tool_calls" in item or item.get("role") == "tool" for item in messages)
            assert "CASE EVIDENCE" not in messages[-1]["content"]
            assert client.chat(messages, max_tokens=512, json_mode=json_mode) == '{"ok":true}'
        assert requests[0][:3] == requests[1][:3]
        assert requests[0][3] != requests[1][3] and requests[0][-1] != requests[1][-1]
        references = [decode_memory(message) for message in requests[0][1:3]]
        assert sorted(pack["materials"][0]["text"] for pack in references) == sorted(texts)
        for pack in references:
            material = pack["materials"][0]
            assert pack["memory_scope"] == "complete_reference" and pack["source_line_scope"] == "full"
            assert material["start_line"] == 1 and material["end_line"] == len(material["text"].splitlines())
            assert material["source_id"] == material["material_id"] == material["source_file_sha256"]
            assert material["text_sha256"] == engine.digest(material["text"])
            assert "file" not in material
        selected = decode_memory(requests[0][3])["materials"][0]
        assert selected["source_id"] == "actual-material-source" and selected["start_line"] == selected["end_line"] == 8
        if api_format == "anthropic":
            prefix = "\n\n".join(item["content"] for item in requests[0][:3])
            if json_mode:
                assert captured[0]["system"].endswith(anthropic_request(requests[0], json_mode=True)["system"])
            assert prefix in captured[0]["system"] and prefix in captured[1]["system"]
            assert captured[0]["system"].split(prefix)[0] == captured[1]["system"].split(prefix)[0]
            assert all("tool_choice" not in payload for payload in captured)
        else:
            key = "input" if api_format == "responses" else "messages"
            assert captured[0][key][:3] == captured[1][key][:3] == requests[0][:3]
            assert captured[0][key][-1] == requests[0][-1]
            assert all("tools" not in payload and "tool_choice" not in payload for payload in captured)
    finally:
        client.client.close()


def test_reference_pack_order_is_content_based_not_child_file_numbering(tmp_path):
    worker, contexts, _ = reference_contexts(tmp_path)
    worker.recipe["sources"].reverse()
    evidence = {"teacher_evidence": "NEW CASE.", "source_id": "parent", "source_kind": "document",
                "teacher_evidence_blocks": ["NEW CASE."]}
    other = sealed_review_memory_context({"source_evidence": append_repair_reference_sources(worker, evidence)}, protocol_version=2)
    assert other["teacher_context_pack"][:2] == contexts[0]["teacher_context_pack"][:2]


@pytest.mark.parametrize("mutation", ["text", "source_id", "range"])
def test_system_reference_pack_rejects_corrupt_text_identity_or_full_line_extent(tmp_path, mutation):
    _, contexts, _ = reference_contexts(tmp_path)
    # Reconstruct a loader receipt with complete reference blocks at the end.
    packs = contexts[0]["teacher_context_pack"]
    materials = [packs[-1]["materials"][0], *(pack["materials"][0] for pack in packs[:-1])]
    evidence = {"teacher_evidence_blocks": [m["text"] for m in materials], "source_line_scope": "selected",
        "source_line_ranges": [{key: m[key] for key in ("material_id", "start_line", "end_line")} for m in materials],
        "teacher_evidence_source_ids": [m["source_id"] for m in materials],
        "supplemental_reference_sources": [{"sha256": m["source_id"], "text_sha256": m["text_sha256"]} for m in materials[1:]]}
    if mutation == "text":
        evidence["teacher_evidence_blocks"][1] += " Forged fact."
    elif mutation == "source_id":
        evidence["teacher_evidence_source_ids"][1] = "wrong-source"
    else:
        evidence["source_line_ranges"][1]["end_line"] -= 1
    with pytest.raises(ValueError, match="review_reference_source_changed|invalid_source_line_ranges"):
        sealed_review_memory_context({"source_evidence": evidence}, protocol_version=2)


def test_new_workflow_defaults_to_system_even_when_client_advertises_tools(tmp_path):
    generator = CapturingGenerator()
    generator.source_memory_transport = "tool"
    worker = workflow(tmp_path, generator)
    assert worker.recipe["source_memory_protocol_version"] == 2
    worker.stage = "sft"
    data = request()
    data["learner_messages"] = [{"role": "user", "content": "How should the pump be prepared?"}]
    worker.ask(["unit-a", "new-system"], "generation", "workflow.sft_directed", data)
    messages = generator.calls[0]
    assert messages[-1] == data["learner_messages"][-1]
    assert all(message["role"] == "system" for message in messages[:-1])
    assert "OUTSIDE" not in canonical(messages) and "Load the workflow" not in canonical(messages)
    material = decode_memory(messages[1])["materials"][0]
    assert {key: material[key] for key in ("material_id", "start_line", "end_line")} == RANGES[0]
    assert material["text"] == SELECTED and material["source_id"] == "manual"
    assert worker.state["source_memory_transports"]["sft.generation"] == "system"


@pytest.mark.parametrize("kind", ["answer", "authored"])
def test_application_only_memory_stays_system_without_invented_tool_or_user(kind):
    data = {"source_context": {"text": "PRIVATE APPLICATION FACT.", "kind": "open_task"},
            "learner_messages": [{"role": "user", "content": "Actual request."}]}
    context = {"teacher_context_pack": [], "task_payload": {}, "source_memory_protocol_version": 2}
    call = answer_role_messages if kind == "answer" else authored_role_messages
    kwargs = {} if kind == "answer" else {"role_projection_version": 3, "phase": "rationale"}
    messages = call("Fixed rules.", context, data, transport="system", source_memory_protocol_version=2, **kwargs)
    assert messages[-1] == data["learner_messages"][-1]
    assert all(message["role"] == "system" for message in messages[:-1])
    assert "PRIVATE APPLICATION FACT" not in messages[0]["content"]
    assert "PRIVATE APPLICATION FACT" in messages[1]["content"]


def test_old_qa_lineage_and_provider_thinking_do_not_reenter_system_repair_payload():
    data = {"target": "multiturn", "candidate": {"messages": [
        {"role": "user", "content": "Real question."},
        {"role": "assistant", "content": "Visible old answer.", "reasoning_content": "OLD_NATIVE_REASONING"}]},
        "qa_contract": {"qa_type": "closed_book", "revision_context": {"messages": "OLD_QA_LINEAGE"}},
        "source_evidence": {"teacher_evidence": "Verified fact.", "source_id": "parent"}}
    projected = project_review_request({"review_context_protocol_version": 1}, data, phase="repair")
    messages = source_memory_messages("Fixed rules.", sealed_review_memory_context(projected, protocol_version=2))
    assert "OLD_NATIVE_REASONING" not in canonical(messages) and "OLD_QA_LINEAGE" not in canonical(messages)
    assert "Visible old answer." in messages[-1]["content"] and data["candidate"]["messages"][1]["reasoning_content"] == "OLD_NATIVE_REASONING"


@pytest.mark.parametrize("memory_version", [1, 2])
def test_failed_fact_grade_cannot_be_rescued_by_a_blind_context_pass(tmp_path, memory_version):
    failed = {"keep": False, "grounded": False, "reasoning_valid": False, "correctness": 1,
              "scores": {name: 1 for name in ("correctness", "reasoning", "grounding", "instruction", "safety")},
              "reason": "The visible answer reverses the required power-off condition."}
    blind_pass = {"keep": True, "context_complete": True, "leakage_free": True, "reason": "Self-contained wording."}
    generator = CapturingGenerator([failed, blind_pass])
    worker = workflow(tmp_path, generator, review_repair={"mode": "auto", "max_rounds": 1})
    worker.recipe["source_memory_protocol_version"] = memory_version
    worker.stage = "review"
    row = {"id": "wrong-answer", "status": "eligible", "messages": [
        {"role": "user", "content": "Which precaution precedes equipment inspection?"},
        {"role": "assistant", "content": "Inspect with the power on."}],
        "qa_contract": {"qa_type": "closed_book", "answer_policy": "answer"}}
    evidence = {"source_id": "document", "source_kind": "document", "teacher_evidence": "Turn off the power before inspection."}
    before = deepcopy(row)
    grade = worker._review_score("sft", row, evidence, [row["id"], "test-fact-first"], "Check correctness.")
    assert grade == failed and not grade["keep"]
    assert len(generator.calls) == 1 and row == before and "learner_context_check" not in row
    if memory_version == 2:
        assert "Turn off the power" not in generator.calls[0][-1]["content"]
        assert "Turn off the power" in generator.calls[0][1]["content"]
    else:
        # Historical non-packed ordinary reviews used a task envelope. Its
        # real bytes remain unchanged even when the new-job default improves.
        assert "Turn off the power" in generator.calls[0][-1]["content"]


@pytest.mark.parametrize("selected", [True, False])
def test_verified_multifile_review_keeps_each_real_material_source_id(tmp_path, selected):
    texts = ["A private first line.\nA selected second line.", "B selected first line.\nB private second line."]
    files = []
    for index, text in enumerate(texts):
        file = tmp_path / f"source-{index}.txt"
        file.write_bytes(text.encode("utf-8"))
        files.append(file)
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=files, targets=["sft"], review_repair={"mode": "auto"})
    worker = engine.Workflow(output, run_id, tmp_path, generator=CapturingGenerator())
    ids = [source["sha256"] for source in worker.recipe["sources"]]
    materials = [number_source_material({"unit_id": f"material-{index}", "source_id": ids[index], "text": text,
                 "cleaning_status": "accepted", "cleaned_text_sha256": engine.digest(text)}) for index, text in enumerate(texts)]
    (worker.path / "source-context/packs").mkdir(parents=True)
    reference = _save_pack(worker.path / "source-context", materials)
    row = {"id": "combined", "source_id": ids[0], "source_context": {"kind": "document", "text": texts[0],
                  "source_context_pack": reference}, "qa_contract": {}}
    if selected:
        row["qa_contract"]["source_line_ranges"] = [
            {"material_id": "material-0", "start_line": 2, "end_line": 2},
            {"material_id": "material-1", "start_line": 1, "end_line": 1}]
    old = revision_evidence(row, worker.recipe, worker.path, with_blocks=True)
    assert "teacher_evidence_source_ids" not in old
    current = worker._review_evidence(row)
    assert current["teacher_evidence_source_ids"] == ids and current["source_id"] == ids[0]
    assert {key: current[key] for key in old} == old
    context = sealed_review_memory_context({"source_evidence": current}, protocol_version=2)
    delivered = context["teacher_context_pack"][0]["materials"]
    assert [material["source_id"] for material in delivered] == ids
    expected = ["A selected second line.", "B selected first line.\n"] if selected else texts
    assert [material["text"] for material in delivered] == expected
    if selected:
        assert [{key: material[key] for key in ("material_id", "start_line", "end_line")}
                for material in delivered] == row["qa_contract"]["source_line_ranges"]
    pack_file = worker.path / reference["relative_path"]
    forged = json.loads(pack_file.read_text(encoding="utf-8"))
    forged["materials"][1]["source_id"] = ids[0]
    pack_file.write_text(json.dumps(forged), encoding="utf-8")
    with pytest.raises(ValueError, match="source_context_pack_(changed|invalid)"):
        worker._review_evidence(row)


@pytest.mark.parametrize("memory_version", [1, 2])
def test_source_free_question_actor_has_only_public_brief_and_real_visible_turns(tmp_path, memory_version):
    generator = CapturingGenerator([{"message": "Next real question?"}])
    worker = workflow(tmp_path, generator, qa_director={"enabled": True, "question_separation": True})
    worker.questioner = generator
    worker.recipe["source_memory_protocol_version"] = memory_version
    worker.stage = "director"
    actual = [{"role": "user", "content": "Can you help inspect it?"},
              {"role": "assistant", "content": "Which equipment?", "reasoning_content": "OLD_PRIVATE_THINKING"}]
    data = {"question_brief": {"topic": "设备检查", "communication_goal": "询问下一步。", "behaviors": ["request_action"]},
            "visible_dialogue": actual}
    worker.ask(["actor", "public-only"], "question", "workflow.question_user", data,
        model_stage="director", prompt_stage="director")
    body = canonical(generator.calls[0])
    assert "OLD_PRIVATE_THINKING" not in body and "teacher_context_pack" not in body
    assert "untrusted_source_memory" not in body and "MX pump" not in body
    assert "Which equipment?" in body and "Can you help inspect it?" in body
    with pytest.raises(ValueError, match="private_context_in_question_request"):
        worker.ask(["actor", "reject-private"], "question", "workflow.question_user", {**data, "teacher_evidence": "PRIVATE FACT"},
            model_stage="director", prompt_stage="director")
    assert len(generator.calls) == 1


def test_system_sources_are_not_native_reasoning_and_prefix_remains_real_beta_wire():
    captured = []
    def handler(request):
        captured.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=full_reply("Visible answer.", native="Provider continuation."))
    client = client_for(handler)
    client.stream_supported = False
    context = sealed_review_memory_context({"source_evidence": {"teacher_evidence": "PRIVATE FACT.", "source_id": "parent"}}, protocol_version=2)
    context["task_payload"] = {"question": "Actual question."}
    messages = source_memory_messages("Fixed rules.", context)
    details = {}
    try:
        assert client.chat(messages, reasoning_prefix="AUTHORED PREFIX.", response_details=details,
            allow_reasoning_fallback=False) == "Visible answer."
        path, sent = captured[0]
        assert path == "/beta/chat/completions"
        assert sent["messages"][:-1] == messages
        assert sent["messages"][-1] == {"role": "assistant", "content": "", "prefix": True,
                                       "reasoning_content": "AUTHORED PREFIX."}
        assert all("reasoning_content" not in item for item in sent["messages"][:-1])
        assert details["reasoning_content"] == "Provider continuation."
    finally:
        client.client.close()
