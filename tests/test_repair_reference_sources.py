"""Correction references are pinned private memory, never learner edits."""
from copy import deepcopy
from types import SimpleNamespace
import hashlib

import pytest

from lib.domain.workflow_creation import validate_creation
from lib.infrastructure.workflow_repair_references import append_repair_reference_sources
from lib.infrastructure.source_memory_messages import sealed_review_memory_context, source_memory_messages
from lib.infrastructure.workflow_review_repair import _evidence_quote_spans
from test_native_regeneration_creation import options


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def configured(tmp_path):
    folder = tmp_path / "inputs"
    folder.mkdir()
    file = folder / "0000.txt"
    file.write_text("官方目录中存在正确字段。\n这是字段适用的范围。", encoding="utf-8")
    recipe = {"repair_reference_sources_version": 1, "repair_inputs": [{"target": "cot"}],
              "review_repair": {"reference_sources": True}, "source_processing_version": 2,
              "sources": [{"file": file.name, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}]}
    evidence = {"teacher_evidence": "原始的有疑问文章。", "source_id": "original-source", "source_kind": "document",
                "teacher_evidence_blocks": ["原始的有疑问文章。"], "source_line_scope": "selected",
                "source_line_ranges": [{"material_id": "original-material", "start_line": 8, "end_line": 8}]}
    return SimpleNamespace(path=tmp_path, recipe=recipe), evidence, file


def test_original_line_selection_and_auxiliary_snapshot_have_distinct_receipts(tmp_path):
    workflow, original, file = configured(tmp_path)
    before = deepcopy(original)
    combined = append_repair_reference_sources(workflow, original)
    assert original == before
    assert combined["source_id"] == original["source_id"]
    assert combined["source_line_ranges"][0] == original["source_line_ranges"][0]
    assert len(combined["source_line_ranges"]) == len(combined["teacher_evidence_blocks"]) == 2
    assert combined["source_line_ranges"][1]["material_id"] == hashlib.sha256(file.read_bytes()).hexdigest()
    learner = [{"role": "user", "content": "如何处理这个问题？"}]
    context = sealed_review_memory_context({"source_evidence": combined, "learner_messages": learner})
    assert context["teacher_context_pack"][0]["materials"][1]["source_id"] == hashlib.sha256(file.read_bytes()).hexdigest()
    messages = source_memory_messages("规则", context)
    assert "正确字段" not in messages[-1]["content"]
    assert any("正确字段" in message.get("content", "") for message in messages if message["role"] == "tool")
    assert learner == [{"role": "user", "content": "如何处理这个问题？"}]


def test_changed_reference_fails_before_request_and_absent_flag_preserves_old_evidence(tmp_path):
    workflow, evidence, file = configured(tmp_path)
    workflow.recipe.pop("repair_reference_sources_version")
    assert append_repair_reference_sources(workflow, evidence) is evidence
    workflow.recipe["repair_reference_sources_version"] = 1
    file.write_text("后来篡改的文档", encoding="utf-8")
    with pytest.raises(ValueError, match="review_reference_source_changed"):
        append_repair_reference_sources(workflow, evidence)


def test_full_document_repair_quotes_use_block_offsets_without_inventing_line_ranges(tmp_path):
    workflow, original, file = configured(tmp_path)
    original.pop("source_line_scope")
    original.pop("source_line_ranges")
    combined = append_repair_reference_sources(workflow, original)
    spans = _evidence_quote_spans(combined, ["原始的有疑问文章。", "官方目录中存在正确字段。"])
    assert [span["source_block_index"] for span in spans] == [0, 1]
    assert [span["source_id"] for span in spans] == [original["source_id"], hashlib.sha256(file.read_bytes()).hexdigest()]
    assert all(span["offset_scope"] == "verified_source_block" and "start_line" not in span for span in spans)
    assert _evidence_quote_spans(combined, ["不存在的引用"]) is None


def test_selected_repair_quotes_keep_exact_original_ranges(tmp_path):
    workflow, original, _ = configured(tmp_path)
    combined = append_repair_reference_sources(workflow, original)
    spans = _evidence_quote_spans(combined, ["原始的有疑问文章。", "官方目录中存在正确字段。"])
    for index, span in enumerate(spans):
        assert span["offset_scope"] == "selected_source_block"
        assert {key: span[key] for key in ("material_id", "start_line", "end_line")} == combined["source_line_ranges"][index]
    combined["source_line_ranges"].pop()
    with pytest.raises(ValueError, match="invalid_source_line_ranges"):
        _evidence_quote_spans(combined, ["官方目录中存在正确字段。"])


@pytest.mark.parametrize("source", [None, "source.pdf", "source.jsonl"])
def test_reference_configuration_requires_real_parsed_text_and_selected_parent(source):
    value = options()
    value["review_repair"]["reference_sources"] = True
    value["sources"] = [] if source is None else [source]
    with pytest.raises(ValueError, match="review_reference_sources_require_repair_and_parsed_text"):
        validate_creation(**value)


def test_reference_configuration_does_not_start_new_generation():
    value = options()
    value["review_repair"]["reference_sources"] = True
    assert validate_creation(**value, sources=["source.txt"])[0] == ["cot"]
    value["repair_inputs"] = None
    with pytest.raises(ValueError):
        validate_creation(**value, sources=["source.txt"])
