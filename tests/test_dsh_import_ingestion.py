"""DSH normalized sources retain observed roles and pass existing ingestion gates."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import pytest

from lib.contexts.workflows.domain.conversation_import import validate_import_record
from lib.domain.review_edit import normalize_sample
from lib.domain.source_conversation import source_conversation_issue
from lib.infrastructure.training_workflow import TrainingWorkflowRuntime


@pytest.fixture(autouse=True)
def mock_llm_server():
    """These tests read normalized local sources and never request a model."""
    yield


def normalized_record(*, reasoning=False, tools=False):
    messages = [{"role": "user", "content": "设备已经断电了吗？"}]
    schemas = []
    if tools:
        messages.extend([
            {"role": "assistant", "content": "", "tool_calls": [{"id": "observed-call", "type": "function",
                "function": {"name": "read_power", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "observed-call", "content": "电源已断开。", "is_error": False},
        ])
        schemas = [{"type": "function", "function": {"name": "read_power",
                    "description": "读取电源状态", "parameters": {"type": "object", "properties": {}}}}]
    messages.append({"role": "assistant", "content": "电源已经断开，可以进行线路检查。"})
    if reasoning:
        messages[-1]["reasoning_content"] = "  已断电满足检查线路的必要条件。\n"
    return {"id": "dsh-observed-session", "messages": messages, "tools": schemas,
            "import_metadata": {"source": "deepseek-harness", "projection": "append_origin_transcript",
                                "session_id": "fixture-session", "format_version": 4}}


def ingest(tmp_path, record, targets=("sft",)):
    """Use the real frozen-source reader without constructing a model client."""
    directory = tmp_path / "inputs"
    directory.mkdir(exist_ok=True)
    source = directory / "dsh-normalized.jsonl"
    content = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    source.write_bytes(content)
    runtime = object.__new__(TrainingWorkflowRuntime)
    runtime.path = tmp_path
    runtime.recipe = {"targets": list(targets), "qa_director": {}, "document_parser": {"mode": "native"}}
    runtime.check_cancel = lambda: None
    manifest = {"file": source.name, "name": source.name, "sha256": hashlib.sha256(content).hexdigest()}
    rows = list(runtime._iter_source_units_uncached(manifest))
    assert source.read_bytes() == content
    return rows


@pytest.mark.parametrize("reasoning,tools", [(False, False), (False, True), (True, True)])
def test_imported_dsh_conversation_reaches_ingest_without_invented_reasoning_or_verification(tmp_path, reasoning, tools):
    record = normalized_record(reasoning=reasoning, tools=tools)
    before = deepcopy(record)
    counts = validate_import_record(record)
    assert counts["reasoning_messages"] == int(reasoning)
    assert source_conversation_issue(normalize_sample(record), ["sft"]) is None
    rows = ingest(tmp_path, record)
    assert len(rows) == 1 and rows[0]["status"] == "ready"
    assert rows[0]["messages"] == record["messages"]
    assert rows[0]["tools"] == record["tools"]
    assert rows[0]["verification_status"] is None
    assert rows[0]["tool_snapshots"] is None
    assert "reasoning_origin" not in rows[0]
    if not reasoning:
        assert all("reasoning_content" not in message for message in rows[0]["messages"])
    assert record == before


def test_imported_tool_error_is_observed_failure_not_fabricated_success(tmp_path):
    record = normalized_record(tools=True)
    record["messages"][2].update(content="无法读取设备状态。", is_error=True)
    record["messages"][-1]["content"] = "读取失败，暂时不能确认是否断电。"
    before = deepcopy(record)
    assert validate_import_record(record)["tool_results"] == 1
    assert ingest(tmp_path, record)[0]["reason"] == "unresolved_tool_error"
    rows = ingest(tmp_path, record, targets=("agent",))
    assert rows[0]["status"] == "ready"
    assert rows[0]["messages"][2] == record["messages"][2]
    assert rows[0]["verification_status"] is None
    assert rows[0]["tool_snapshots"] is None
    assert record == before


@pytest.mark.parametrize("problem,expected", [
    ("developer", "invalid_or_incomplete_conversation"),
    ("reasoning_only", "empty_message"),
    ("long", "context_exceeds_auto_limit"),
    ("secret", "potential_secret"),
])
def test_unsupported_dsh_sources_still_obey_existing_ingestion_rules(tmp_path, problem, expected):
    record = normalized_record()
    if problem == "developer":
        record["messages"].insert(0, {"role": "developer", "content": "保留原始操作规则。"})
    elif problem == "reasoning_only":
        record["messages"].insert(1, {"role": "assistant", "content": "", "reasoning_content": "先确认设备当前状态。"})
    elif problem == "long":
        record["messages"][0]["content"] = "A" * 80001
    else:
        record["messages"][0]["content"] = "api_key=" + "fixture-not-a-real-credential"
    before = deepcopy(record)
    validate_import_record(record)
    rows = ingest(tmp_path, record)
    assert len(rows) == 1 and rows[0]["status"] == "quarantined"
    assert rows[0]["reason"] == expected
    assert record == before


class RecordedAdapterPort:
    """External adapter result only; neither a harness SDK nor an API is loaded."""
    def __init__(self, record):
        self.record = deepcopy(record)
        self.staged = None
        self.saved = []

    def convert(self, name, source):
        normalized = (json.dumps(self.record, ensure_ascii=False) + "\n").encode("utf-8")
        report = {"protocol": "dataforge.conversation_import", "version": 1, "status": "ready",
                  "source_sha256": hashlib.sha256(source).hexdigest(),
                  "output_sha256": hashlib.sha256(normalized).hexdigest(), "output_bytes": len(normalized),
                  **validate_import_record(self.record)}
        return normalized, report

    def stage(self, name, source_sha256, normalized, report):
        self.staged = ({"source_name": name, "source_sha256": source_sha256}, normalized, report)
        return "staged-fixture"

    def load(self, import_id):
        assert import_id == "staged-fixture"
        return self.staged

    def store_source(self, name, content):
        self.saved.append((name, content))
        return [{"name": name}]


@pytest.mark.parametrize("problem,expected", [
    ("developer", "invalid_role"),
    ("reasoning_only", "empty_message"),
    ("long", "context_exceeds_auto_limit"),
    ("secret", "potential_secret"),
])
def test_import_preview_reports_downstream_blockers_without_altering_original(problem, expected):
    from lib.contexts.workflows.application.conversation_import import ImportConversations
    record = normalized_record()
    if problem == "developer":
        record["messages"].insert(0, {"role": "developer", "content": "保留原始操作规则。"})
    elif problem == "reasoning_only":
        record["messages"].insert(1, {"role": "assistant", "content": "", "reasoning_content": "先确认设备状态。"})
    elif problem == "long":
        record["messages"][0]["content"] = "A" * 80001
    else:
        record["messages"][0]["content"] = "api_key=" + "fixture-not-a-real-credential"
    before = deepcopy(record)
    port = RecordedAdapterPort(record)
    preview = ImportConversations(port).preview("session.jsonl", b"fixture source session")
    # Conversion success is not a training approval; every issue is explicit.
    assert preview["counts"]["records"] == 1
    assert preview["conversations"][0]["training_issues"] == {"sft": expected, "agent": expected}
    for target in ("sft", "agent"):
        assert preview["eligibility"][target] == {"eligible_records": 0, "blocked_records": 1,
                                                  "reasons": {expected: 1}, "eligible_ids": [], "eligible_ids_truncated": False}
    assert json.loads(port.staged[1]) == before
    assert not port.saved
    with pytest.raises(ValueError, match="conversation_import_training_ineligible"):
        ImportConversations(port).confirm(preview["import_id"])
    assert not port.saved


def test_import_preview_and_confirmation_preserve_usable_tool_trace_without_marking_it_verified(tmp_path):
    from lib.contexts.workflows.application.conversation_import import ImportConversations
    record = normalized_record(tools=True)
    port = RecordedAdapterPort(record)
    application = ImportConversations(port)
    preview = application.preview("session.v4.jsonl.zstd", b"fixture source session")
    assert preview["conversations"][0]["training_issues"] == {"sft": None, "agent": None}
    for target in ("sft", "agent"):
        assert preview["eligibility"][target] == {"eligible_records": 1, "blocked_records": 0, "reasons": {},
                                                  "eligible_ids": [record["id"]], "eligible_ids_truncated": False}
    application.confirm(preview["import_id"])
    assert len(port.saved) == 1
    imported = json.loads(port.saved[0][1])
    assert imported == record
    unit = ingest(tmp_path, imported)[0]
    assert unit["status"] == "ready" and unit["messages"] == record["messages"]
    assert unit["verification_status"] is None and unit["tool_snapshots"] is None
    assert all("reasoning_content" not in message for message in unit["messages"])



def test_mixed_import_requires_explicit_selection_instead_of_silently_dropping_blocked_records():
    from lib.contexts.workflows.application.conversation_import import ImportConversations
    ready = normalized_record()
    blocked = normalized_record()
    blocked["id"] = "dsh-blocked-session"
    blocked["messages"].insert(0, {"role": "developer", "content": "保留角色。"})
    records = [ready, blocked]

    class MixedAdapterPort(RecordedAdapterPort):
        def convert(self, name, source):
            normalized = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records).encode("utf-8")
            counts = {field: sum(validate_import_record(record)[field] for record in records)
                      for field in validate_import_record(ready)}
            return normalized, {"protocol": "dataforge.conversation_import", "version": 1, "status": "ready",
                "source_sha256": hashlib.sha256(source).hexdigest(),
                "output_sha256": hashlib.sha256(normalized).hexdigest(), "output_bytes": len(normalized), **counts}

    port = MixedAdapterPort(ready)
    application = ImportConversations(port)
    preview = application.preview("source.jsonl", b"mixed source")
    assert preview["eligibility"]["sft"]["blocked_records"] == 1
    assert preview["eligibility"]["sft"]["eligible_ids"] == [ready["id"]]
    with pytest.raises(ValueError, match="conversation_import_selection_required"):
        application.confirm(preview["import_id"])
    assert not port.saved
    application.confirm(preview["import_id"], selected_ids=[ready["id"]])
    assert json.loads(port.saved[0][1]) == ready
    assert [json.loads(line) for line in port.staged[1].splitlines()] == records
