"""Import-policy wiring through durable source caches, requests and publication."""
from copy import deepcopy
import json

import pytest

from lib.contexts.datasets.infrastructure.corpus_index import CorpusNearDuplicateIndex
from lib.contexts.workflows.infrastructure.production_adapter import FilesystemProductionAdapter
from lib.infrastructure import training_workflow as engine


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """All responses are injected; these integration checks need no network."""
    yield


class ExplanationWriter:
    model = "offline-import-explanation"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.usage = {"calls": 0}

    def chat(self, messages, **options):
        self.calls.append((deepcopy(messages), options))
        self.usage["calls"] += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        options["response_details"]["reasoning_content"] = "Provider's separate native channel must not replace visible explanation."
        return response


def imported_tool_conversation():
    return {"messages": [
        {"role": "user", "content": "Read the maintenance note. What comes first?"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "read-note", "type": "function",
            "function": {"name": "read_file", "arguments": '{"path":"maintenance.txt"}'}}]},
        {"role": "tool", "tool_call_id": "read-note", "content": "Before checking the wiring, disconnect the power."},
        {"role": "assistant", "content": "Disconnect the power before checking the wiring."}],
        "tools": [{"type": "function", "function": {"name": "read_file", "parameters": {
            "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}


def test_production_resume_reuses_completed_turn_and_modes_share_unchanged_raw_cache(tmp_path):
    source = tmp_path / "conversation.jsonl"
    original = imported_tool_conversation()
    source.write_text(json.dumps(original) + "\n", encoding="utf-8")
    source_bytes = source.read_bytes()
    output = tmp_path / "output"
    frozen_prompt = "Explain only this recorded assistant response using its visible history. Do not rewrite the response."
    policy = {"version": 1, "mode": "fill_missing"}
    run_id = engine.create_run(output, sources=[source], targets=["sft"], conversation_reasoning=policy,
        node_prompts={"ingest": {"workflow.conversation_reasoning": frozen_prompt}},
        production={"goals": {"sft": 1}, "round_size": 1, "min_acceptance_rate": 0})
    writer = ExplanationWriter(["I need to read the requested note before answering.", RuntimeError("offline interruption")])
    runtime = engine.Workflow(output, run_id, tmp_path, generator=writer)
    assert runtime.recipe["conversation_reasoning"] == policy
    assert runtime.recipe["node_prompt_templates"]["ingest"]["workflow.conversation_reasoning"] == frozen_prompt
    raw, = list(runtime.iter_source_units(runtime.recipe["sources"][0]))
    assert raw["messages"] == original["messages"] and writer.calls == []
    library = output / ".source-library"
    cache_paths = [*library.rglob("parts.jsonl"), *library.rglob("original.jsonl"), *library.rglob("parse.json")]
    cached_bytes = {path: path.read_bytes() for path in cache_paths}
    assert cache_paths

    with pytest.raises(RuntimeError, match="offline interruption"):
        with FilesystemProductionAdapter(runtime, CorpusNearDuplicateIndex).open_session():
            pytest.fail("The interrupted second turn must not become a ready production input")
    assert len(writer.calls) == 2
    first_payload = json.loads(writer.calls[0][0][-1]["content"])
    assert len(first_payload["history"]) == 1
    assert "disconnect" not in json.dumps(first_payload).lower()
    assert writer.calls[0][0][0] == {"role": "system", "content": frozen_prompt}
    assert writer.calls[0][1]["allow_reasoning_fallback"] is False
    assert writer.calls[0][1]["retries"] == 1

    resumed_writer = ExplanationWriter(["The note requires power disconnection before the wiring check."])
    resumed = engine.Workflow(output, run_id, tmp_path, generator=resumed_writer)
    with FilesystemProductionAdapter(resumed, CorpusNearDuplicateIndex).open_session() as progress:
        assert progress.source_count == 1
    prepared, = engine.read_json(resumed.path / "input_records.json")
    assert prepared["messages"][1]["reasoning_content"] == "I need to read the requested note before answering."
    assert prepared["messages"][3]["reasoning_content"] == "The note requires power disconnection before the wiring check."
    assert prepared["messages"][3]["content"] == original["messages"][3]["content"]
    assert prepared["conversation_reasoning"]["synthesized_turns"] == 2
    assert all(turn["request"]["stage"] == "ingest" for turn in prepared["conversation_reasoning"]["turns"])
    assert len(resumed_writer.calls) == 1
    with FilesystemProductionAdapter(resumed, CorpusNearDuplicateIndex).open_session() as progress:
        assert progress.source_count == 1
    assert len(resumed_writer.calls) == 1

    preserved_id = engine.create_run(output, sources=[source], targets=["sft"],
        conversation_reasoning={"version": 1, "mode": "preserve"})
    preserved = engine.Workflow(output, preserved_id, tmp_path, generator=ExplanationWriter([]))
    preserved_row, = preserved.parse_source(preserved.recipe["sources"][0])
    assert preserved_row["messages"] == original["messages"]
    assert preserved_row["conversation_reasoning"]["missing_turns"] == 2
    assert preserved.state["source_library"]["hits"] == 1
    assert source.read_bytes() == source_bytes
    assert {path: path.read_bytes() for path in cached_bytes} == cached_bytes


def test_invalid_or_insufficient_completion_never_enters_training_export(tmp_path):
    source = tmp_path / "incomplete.jsonl"
    records = [{"messages": [{"role": "user", "content": question},
                              {"role": "assistant", "content": "The record has no supporting explanation."}]}
               for question in ("Which maintenance condition applies?", "Why can that comparison prove an improvement?")]
    source.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    original_bytes = source.read_bytes()
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"],
        conversation_reasoning={"version": 1, "mode": "fill_missing"})
    writer = ExplanationWriter(["", "<INSUFFICIENT_EVIDENCE>"])
    runtime = engine.Workflow(output, run_id, tmp_path, generator=writer)
    state = runtime.execute()
    assert state["status"] != "failed", state.get("error")
    assert state["input_summary"]["quarantined"] == 2 and state["input_summary"]["ready"] == 0
    prepared = engine.read_json(runtime.path / "input_records.json")
    assert {row["reason"] for row in prepared} == {
        "conversation_reasoning_empty_response", "conversation_reasoning_insufficient_evidence"}
    assert [row["messages"] for row in prepared] == [record["messages"] for record in records]
    assert all(row["conversation_reasoning"]["semantic_status"] == "not_assessed" for row in prepared)
    assert (runtime.path / "artifacts" / "sft.jsonl").read_text(encoding="utf-8") == ""
    assert len(writer.calls) == 2 and source.read_bytes() == original_bytes
    engine.Workflow(output, run_id, tmp_path, generator=writer).execute()
    assert len(writer.calls) == 2
