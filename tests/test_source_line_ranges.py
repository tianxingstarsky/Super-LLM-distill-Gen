"""Addressed clean knowledge blocks are deterministic and remain private tools."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from lib.domain.source_line_ranges import (
    number_source_material, normalize_source_line_ranges, resolve_source_line_ranges,
)
from lib.domain.workflow_qa_director import validate_director_task
from lib.domain.workflow_quality import canonical
from lib.domain.workflow_node_prompts import snapshot_node_prompts
from lib.infrastructure.model_context_prefix import serialize_context_request, split_context_request
from lib.infrastructure.workflow_rows import WorkflowRows, write_jsonl
from lib.infrastructure.workflow_source_context import (
    load_context_pack, prepare_source_context_packs, teacher_context_pack,
)
from lib.prompts import get, render


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """These tests have no model requests or local TCP fixture dependencies."""
    yield


def material(identity="chapter", text="12: Original heading\r\n\r\nCondition: closed valve.\nPressure: 45 kPa.\nOUTSIDE_SENTINEL"):
    return number_source_material({"unit_id": identity, "source_id": "manual", "text": text,
                                  "cleaning_status": "accepted",
                                  "cleaned_text_sha256": hashlib.sha256(canonical(text).encode()).hexdigest()})


def range_for(identity="chapter", start=3, end=4):
    return {"material_id": identity, "start_line": start, "end_line": end}


def pack_fixture():
    body = material()
    pack = {"version": 1, "id": "shared-pack", "prefix_sha256": "digest", "materials": [body]}
    reference = {"version": 1, "id": pack["id"], "prefix_sha256": pack["prefix_sha256"],
                 "source_line_protocol_version": 1}
    def loader(path, ref):
        assert ref == reference
        return deepcopy(pack)
    return pack, reference, loader


def test_addresses_preserve_blank_lines_line_endings_and_original_numeric_text():
    body = material()
    assert body["numbered_text"].startswith("1: 12: Original heading\r\n2: \r\n3: Condition")
    assert body["line_count"] == 5
    assert body == material()
    assert resolve_source_line_ranges([body], [range_for(start=1, end=2)])[0]["text"] == "12: Original heading\r\n\r\n"
    assert resolve_source_line_ranges([body], [range_for()])[0]["text"] == "Condition: closed valve.\nPressure: 45 kPa.\n"


@pytest.mark.parametrize("ranges", [[], [range_for(start=True)], [range_for(start=0)],
    [range_for(start=4, end=3)], [range_for(), range_for()], [{**range_for(), "text": "rewrite"}]])
def test_malformed_addresses_are_rejected_instead_of_becoming_source_text(ranges):
    with pytest.raises(ValueError, match="invalid_source_line_ranges"):
        normalize_source_line_ranges(ranges)


def test_multiple_blocks_resolve_in_requested_order_and_cannot_escape_materials():
    bodies = [material("one", "One fact.\nOne condition."), material("two", "Two fact.\nTwo condition.")]
    ranges = [range_for("two", 1, 2), range_for("one", 1, 2)]
    assert [block["text"] for block in resolve_source_line_ranges(bodies, ranges)] == [bodies[1]["text"], bodies[0]["text"]]
    with pytest.raises(ValueError, match="material_not_found"):
        resolve_source_line_ranges(bodies, [range_for("missing", 1, 1)])
    with pytest.raises(ValueError, match="out_of_bounds"):
        resolve_source_line_ranges(bodies, [range_for("one", 1, 3)])


def test_director_boundaries_reconstruct_raw_context_and_verify_quotes_within_selected_blocks():
    body = material()
    task = {"id": "candidate", "qa_type": "grounded", "question": "Which pressure applies with the valve closed?",
            "visible_context": "MODEL_REWRITE_SENTINEL", "answer_policy": "answer", "guidance": "Explain the condition.",
            "evidence_quotes": ["Pressure: 45 kPa."], "source_line_ranges": [range_for()]}
    checked = validate_director_task(task, expected_type="grounded", source_text=body["text"], source_materials=[body])
    assert checked["visible_context"] == "Condition: closed valve.\nPressure: 45 kPa."
    assert "MODEL_REWRITE_SENTINEL" not in canonical(checked)
    assert task["visible_context"] == "MODEL_REWRITE_SENTINEL"
    task["evidence_quotes"] = ["OUTSIDE_SENTINEL"]
    with pytest.raises(ValueError, match="not_in_source_line_ranges"):
        validate_director_task(task, expected_type="grounded", source_text=body["text"], source_materials=[body])


@pytest.mark.parametrize("visible", ["", "Condition: closed valve."])
def test_partial_clues_do_not_expand_to_private_teacher_blocks(visible):
    body = material()
    task = {"id": "candidate", "qa_type": "partial", "question": "What pressure applies?",
            "visible_context": visible, "answer_policy": "clarify", "guidance": "Ask for the missing setting.",
            "evidence_quotes": ["Pressure: 45 kPa."], "source_line_ranges": [range_for()]}
    checked = validate_director_task(task, expected_type="partial", source_text=body["text"], source_materials=[body])
    assert checked["visible_context"] == visible
    assert "Pressure: 45 kPa." in checked["source_line_focus"][0]["text"]
    assert "Pressure: 45 kPa." not in checked["visible_context"]


def test_partial_clues_cannot_reintroduce_unselected_material_lines():
    body = material()
    task = {"id": "candidate", "qa_type": "partial", "question": "What pressure applies?",
            "visible_context": "OUTSIDE_SENTINEL", "answer_policy": "clarify", "guidance": "Ask for the missing setting.",
            "evidence_quotes": ["Pressure: 45 kPa."], "source_line_ranges": [range_for()]}
    with pytest.raises(ValueError, match="qa_director_visible_context_not_in_source"):
        validate_director_task(task, expected_type="partial", source_text=body["text"], source_materials=[body])


def test_selected_tool_projection_never_transmits_outside_anchor_or_cached_focus_in_user_payload():
    pack, ref, loader = pack_fixture()
    data = {"source_line_scope": "selected", "source_line_ranges": [range_for()],
            "source_context": {"id": "chapter", "source_context_pack": ref, "text": pack["materials"][0]["text"],
                               "document_reading": {"source_text": "RAW_READING_SENTINEL"}},
            "qa_contract": {"question": "Explain the pressure condition.", "visible_context": "OUTSIDE_SENTINEL",
                            "evidence_quotes": ["OUTSIDE_SENTINEL"],
                            "source_line_focus": [{"text": "FORGED_CACHE_SENTINEL"}]}}
    before = deepcopy(data)
    result = split_context_request("unused", data, pack_loader=loader)
    teacher = result["teacher_context_pack"]
    assert teacher[0]["materials"][0]["text"] == "Condition: closed valve.\nPressure: 45 kPa.\n"
    assert "OUTSIDE_SENTINEL" not in canonical(result)
    assert "RAW_READING_SENTINEL" not in canonical(result)
    assert "FORGED_CACHE_SENTINEL" not in canonical(result)
    assert "Pressure: 45" not in canonical(result["task_payload"])
    assert result["task_payload"]["qa_contract"]["question"] == "Explain the pressure condition."
    assert data == before
    assert json.loads(serialize_context_request("unused", data, pack_loader=loader))["teacher_context_pack"] == teacher


def test_selected_context_is_revalidated_and_never_falls_back_to_the_full_pack():
    _, ref, loader = pack_fixture()
    data = {"source_line_scope": "selected", "source_context_pack": ref}
    with pytest.raises(ValueError, match="ranges_required"):
        split_context_request("unused", data, pack_loader=loader)
    data["source_line_ranges"] = [range_for(end=200)]
    with pytest.raises(ValueError, match="out_of_bounds"):
        split_context_request("unused", data, pack_loader=loader)


def test_full_numbered_director_prefix_is_stable_across_tasks_without_duplicate_raw_body():
    pack, ref, loader = pack_fixture()
    def data(question):
        return {"source_context": {"id": "chapter", "text": pack["materials"][0]["text"], "source_context_pack": ref},
                "question": question}
    first = serialize_context_request("unused", data("One task"), pack_loader=loader)
    second = serialize_context_request("unused", data("Another task"), pack_loader=loader)
    prefix = '{"teacher_context_pack":' + json.dumps([teacher_context_pack(pack)], ensure_ascii=False, separators=(",", ":"))
    assert first.startswith(prefix + ",") and second.startswith(prefix + ",")
    assert "text" not in json.loads(first)["teacher_context_pack"][0]["materials"][0]
    assert "text" not in json.loads(first)["source_context"]
    assert json.loads(first)["source_context"]["source_line_anchor"] == range_for(start=1, end=5)


def build_packs(directory, text, *, numbered):
    directory.mkdir()
    unit = {"id": "unit", "source_id": "manual", "kind": "document", "status": "ready", "text": text,
            "source_cleaning": {"status": "accepted", "cleaned_text_sha256": hashlib.sha256(canonical(text).encode()).hexdigest()}}
    path = directory / "clean.jsonl"
    write_jsonl(path, iter([unit]))
    recipe = {"source_context_pack_version": 1, "node_prompt_templates": {"sft": {"workflow.sft": "Generate."}},
              "node_prompt_system": "System", "node_models": {"sft": {"generation": {"context_window_tokens": 32768,
                                                                                       "max_output_tokens": 4096}}}}
    if numbered:
        recipe["source_line_protocol_version"] = 1
    worker = SimpleNamespace(path=directory, recipe=recipe, check_cancel=lambda: None)
    prepared = list(prepare_source_context_packs(worker, WorkflowRows(path, 1)))
    return prepared[0], load_context_pack(directory, prepared[0]["source_context_pack"])


def test_pack_retains_original_hash_but_budgets_the_numbered_teacher_projection(tmp_path):
    unit, pack = build_packs(tmp_path / "numbered", "A condition.\nIts complete fact.\n", numbered=True)
    body = pack["materials"][0]
    assert body["text"] == unit["text"]
    assert body["cleaned_text_sha256"] == unit["source_cleaning"]["cleaned_text_sha256"]
    assert unit["source_context_pack"]["input_bytes"] == len(canonical(teacher_context_pack(pack)).encode())
    build_packs(tmp_path / "legacy", "x\n" * 2250, numbered=False)
    with pytest.raises(ValueError, match="capacity_too_small"):
        build_packs(tmp_path / "cannot_fit_numbering", "x\n" * 2250, numbered=True)


def test_prompt_upgrade_preserves_prior_versions_and_literal_custom_templates():
    assert "source_line_ranges" in render(get("workflow.qa_director", version="1.3.0"))
    assert "source_line_ranges" not in render(get("workflow.qa_director", version="1.2.0"))
    assert "source_line_scope=selected" in render(get("workflow.sft_directed", version="1.3.0"))
    assert "source_line_scope=selected" not in render(get("workflow.sft_directed", version="1.2.0"))
    literal = "CUSTOM {literal} body"
    assert snapshot_node_prompts({"director": {"workflow.qa_director": literal}}, recipe_version=19)["director"]["workflow.qa_director"] == literal
