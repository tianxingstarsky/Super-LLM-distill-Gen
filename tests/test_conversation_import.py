"""External import contracts use fake local converters, never provider APIs."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from lib.bootstrap.conversation_import import conversation_import_application
from lib.contexts.workflows.application.conversation_import import ImportConversations
from lib.contexts.workflows.domain.conversation_import import (
    COUNT_FIELDS, import_digest, inspect_import, validate_import_record,
)
from lib.contexts.workflows.infrastructure.conversation_import import ExternalConversationImporter
from lib.bootstrap.local_inputs import local_input_application


def record(identifier="dialogue"):
    return {"id": identifier, "messages": [{"role": "user", "content": "What should I check?"},
        {"role": "assistant", "content": "Disconnect power first."}], "tools": [],
        "import_metadata": {"source": "test-adapter", "original_event": 7}}


def normalized(records):
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records).encode("utf-8")


def receipt(source, output):
    counts = dict.fromkeys(COUNT_FIELDS, 0)
    for line in output.splitlines():
        for key, value in validate_import_record(json.loads(line)).items():
            counts[key] += value
    return {"protocol": "dataforge.conversation_import", "version": 1, "status": "ready",
            "source_sha256": import_digest(source), "output_sha256": import_digest(output),
            "output_bytes": len(output), "warnings": [], **counts}


class MemoryPort:
    def __init__(self, records):
        self.output = normalized(records)
        self.saved = []

    def convert(self, name, source):
        self.report = receipt(source, self.output)
        return self.output, self.report

    def stage(self, name, digest, output, report):
        self.metadata = {"source_name": name, "source_sha256": digest}
        return "memory-import"

    def load(self, import_id):
        assert import_id == "memory-import"
        return self.metadata, self.output, self.report

    def store_source(self, name, content):
        self.saved.append((name, content))
        return [{"path": name, "name": name, "size": len(content)}]


def test_preview_is_bounded_and_confirmation_keeps_complete_original_bytes():
    records = [record(str(index)) for index in range(8)]
    records[0]["messages"][1]["reasoning_content"] = "Complete original rationale. " * 200
    port = MemoryPort(records)
    app = ImportConversations(port)
    preview = app.preview("session.jsonl.zstd", b"original source")
    assert preview["counts"]["records"] == 8 and len(preview["conversations"]) == 3
    assert preview["conversations"][0]["preview_truncated"]
    assert not port.saved
    app.confirm(preview["import_id"])
    assert port.saved[0][1] == normalized(records)
    assert "reasoning_content" not in json.loads(port.saved[0][1].splitlines()[1])["messages"][1]


def test_selection_preserves_source_mapping_and_original_record_order():
    records = [record(str(index)) for index in range(3)]
    port = MemoryPort(records)
    app = ImportConversations(port)
    app.preview("session.jsonl", b"source")
    app.confirm("memory-import", ["2", "0"])
    assert [json.loads(line) for line in port.saved[0][1].splitlines()] == [records[0], records[2]]
    with pytest.raises(ValueError, match="unknown_selection"):
        app.confirm("memory-import", ["unknown"])
    assert len(port.saved) == 1


@pytest.mark.parametrize("name", ["../session.jsonl", "NUL.jsonl", "CON", "..", "bad?.jsonl", "bad.jsonl ", "数" * 100 + ".jsonl"])
def test_unsafe_names_fail_before_converter(name):
    port = MemoryPort([record()])
    port.convert = lambda *args: pytest.fail("must not execute converter")
    with pytest.raises(ValueError, match="invalid_conversation_import_name"):
        ImportConversations(port).preview(name, b"source")


@pytest.mark.parametrize("field,value", [("version", True), ("source_sha256", "wrong"),
    ("output_sha256", "wrong"), ("output_bytes", 1), ("records", 99), ("messages", True)])
def test_receipt_mismatch_never_stages_or_stores(field, value):
    output = normalized([record()])
    report = receipt(b"source", output)
    report[field] = value
    with pytest.raises(ValueError, match="conversation_import_(receipt|count)_mismatch"):
        inspect_import(output, report, import_digest(b"source"))


def tool_record():
    return {"id": "tool-session", "messages": [{"role": "user", "content": "Read the saved note."},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1", "type": "function",
            "function": {"name": "read_note", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "Power must be off."},
        {"role": "assistant", "content": "Disconnect power before the check."}], "tools": []}


def test_tool_history_validated_without_executing_functions():
    original = tool_record()
    assert validate_import_record(original)["tool_results"] == 1
    port = MemoryPort([original])
    app = ImportConversations(port)
    app.preview("tools.jsonl", b"source")
    app.confirm("memory-import")
    assert json.loads(port.saved[0][1]) == original
    broken = deepcopy(original)
    broken["messages"][2]["tool_call_id"] = "unknown"
    with pytest.raises(ValueError, match="orphan_tool_result"):
        validate_import_record(broken)
    broken = deepcopy(original)
    broken["messages"].pop(2)
    with pytest.raises(ValueError, match="missing_tool_result"):
        validate_import_record(broken)


def fake_converter(tmp_path):
    script = tmp_path / "converter.py"
    script.write_text('''import argparse,hashlib,json
from pathlib import Path
p=argparse.ArgumentParser()
for key in ('input','output','report'):p.add_argument('--'+key,required=True)
a=p.parse_args();source=Path(a.input).read_bytes()
row={'id':'converted','messages':[{'role':'user','content':'What should I check?'},{'role':'assistant','content':'Disconnect power first.'}], 'import_metadata':{'line':1}}
output=(json.dumps(row)+'\\n').encode()
Path(a.output).write_bytes(output)
report={'protocol':'dataforge.conversation_import','version':1,'status':'ready','source_sha256':hashlib.sha256(source).hexdigest(),'output_sha256':hashlib.sha256(output).hexdigest(),'output_bytes':len(output),'records':1,'messages':2,'assistant_messages':1,'reasoning_messages':0,'tool_calls':0,'tool_results':0,'warnings':[]}
Path(a.report).write_text(json.dumps(report))
with Path(__file__).with_suffix('.calls').open('a') as stream:stream.write('called\\n')
''', encoding="utf-8")
    return script


def test_external_converter_persists_only_after_confirmation_and_reuses_cache(tmp_path):
    script = fake_converter(tmp_path)
    app = conversation_import_application(tmp_path, command=(sys.executable, str(script)))
    first = app.preview("session.jsonl.zstd", b"original untouched source")
    assert not (tmp_path / "data/seeds/uploads").exists()
    sources = app.confirm(first["import_id"])
    result = Path(sources[0]["path"])
    assert result.is_file() and json.loads(result.read_text())["id"] == "converted"
    second = app.preview("renamed.jsonl.zstd", b"original untouched source")
    assert first["import_id"] == second["import_id"]
    assert app.confirm(second["import_id"]) == sources
    assert script.with_suffix(".calls").read_text().splitlines() == ["called"]
    # A new adapter implementation invalidates its content-addressed conversion.
    script.write_text(script.read_text() + "\n# adapter update\n")
    third = app.preview("session.jsonl.zstd", b"original untouched source")
    assert third["import_id"] != first["import_id"]
    assert len(script.with_suffix(".calls").read_text().splitlines()) == 2


def test_uploaded_output_filename_cannot_collide_with_converter_output(tmp_path):
    script = fake_converter(tmp_path)
    # Production adapters require a new output path, not an existing source file.
    script.write_text(script.read_text().replace(
        "Path(a.output).write_bytes(output)",
        "assert not Path(a.output).exists()\n"
        "assert Path(a.input).name == 'normalized.jsonl'\n"
        "Path(a.output).write_bytes(output)",
    ))
    app = conversation_import_application(tmp_path, command=(sys.executable, str(script)))
    source = b"original event log"
    preview = app.preview("normalized.jsonl", source)
    assert preview["source_sha256"] == import_digest(source)
    saved = app.confirm(preview["import_id"])
    assert json.loads(Path(saved[0]["path"]).read_text())["id"] == "converted"


def test_staged_content_or_identity_tampering_is_rejected_before_source_store(tmp_path):
    script = fake_converter(tmp_path)
    app = conversation_import_application(tmp_path, command=(sys.executable, str(script)))
    preview = app.preview("session.jsonl", b"source")
    directory = tmp_path / "data/conversation-imports" / preview["import_id"]
    original = (directory / "normalized.jsonl").read_bytes()
    (directory / "normalized.jsonl").write_bytes(original.replace(b"Disconnect", b"Connect"))
    with pytest.raises(ValueError, match="receipt_mismatch"):
        app.confirm(preview["import_id"])
    (directory / "normalized.jsonl").write_bytes(original)
    metadata = json.loads((directory / "ready.json").read_text())
    metadata["source_sha256"] = "0" * 64
    (directory / "ready.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="cache_invalid"):
        app.confirm(preview["import_id"])
    assert not (tmp_path / "data/seeds/uploads").exists()


def test_missing_adapter_does_not_break_constructing_the_page_service(tmp_path):
    app = conversation_import_application(tmp_path)
    with pytest.raises(ValueError, match="conversation_import_(adapter|runtime)_unavailable"):
        app.preview("session.jsonl", b"source")


def test_invalid_report_type_becomes_safe_error(tmp_path):
    script = tmp_path / "bad.py"
    script.write_text("import sys,pathlib;pathlib.Path(sys.argv[sys.argv.index('--report')+1]).write_text('[]')")
    app = conversation_import_application(tmp_path, command=(sys.executable, str(script)))
    with pytest.raises(ValueError, match="conversation_import_invalid_report"):
        app.preview("session.jsonl", b"source")


def test_converter_modifying_staged_input_is_detected(tmp_path):
    script = tmp_path / "modify.py"
    script.write_text("import sys,pathlib;pathlib.Path(sys.argv[sys.argv.index('--input')+1]).write_bytes(b'changed')")
    app = conversation_import_application(tmp_path, command=(sys.executable, str(script)))
    with pytest.raises(ValueError, match="conversation_import_source_modified"):
        app.preview("session.jsonl", b"source")
