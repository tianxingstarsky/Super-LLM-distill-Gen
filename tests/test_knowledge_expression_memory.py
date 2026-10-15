"""Optional editorial expression memory, with real SQLite and sealed sources."""
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import sqlite3

import pytest

from lib.domain.source_line_ranges import number_source_material
from lib.domain.workflow_quality import canonical
from lib.infrastructure.knowledge_agent_session import KnowledgeAgentSession
from lib.infrastructure.knowledge_agent_tools import KnowledgeAgentTools
from lib.infrastructure.knowledge_history_import import verified_knowledge_history

_spec = importlib.util.spec_from_file_location("knowledge_expression_import_fixtures", Path(__file__).with_name("test_knowledge_history_import.py"))
_imports = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_imports)


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


DESIGN = {"form": "简短直接提问", "participant_voice": "像初次了解的人自然交谈",
          "register": "日常口语", "verbosity": "只说这次需要的一两句", "syntax": "可以省略已知主语",
          "noise": "允许自然停顿", "interaction_style": "承接刚才的问题"}


def claim(store, identity="q", *, knowledge="k", run="run", design=DESIGN):
    return store.claim(knowledge, identity, run_id=run, sample_id=identity, question="如何检查压力？",
        qa_type="closed_book", topic="检查", expression_design=deepcopy(design))


def outcome(store, identity="q", *, knowledge="k", run="run", status="final_eligible"):
    store.outcome(knowledge, identity, run_id=run, sample_id=identity, stage="sft", status=status)


def material():
    text = "测量前先关闭电源。\n"
    return number_source_material({"unit_id": "material", "source_id": "source", "source_name": "manual.md",
        "text": text, "cleaning_status": "accepted",
        "cleaned_text_sha256": hashlib.sha256(canonical(text).encode()).hexdigest()})


def test_long_valid_profiles_return_whole_bounded_hits_instead_of_invalid_request():
    from lib.domain.dialogue_expression import EXPRESSION_FIELDS_V2
    profile = {field: "自然表达" * 62 for field in EXPRESSION_FIELDS_V2}
    rows = [{"id": str(index), "question": "这个问题怎么处理", "expression_design": deepcopy(profile)}
            for index in range(4)]
    tools = KnowledgeAgentTools([material()], history_search=lambda **arguments: rows, include_expression=True)
    result = tools.execute("history_search", {"query": "处理", "limit": 4})
    assert 0 < len(result["hits"]) < 4 and result["results_limited"]
    assert len(canonical(result).encode()) <= 32768
    assert all(hit["expression_design"] == profile for hit in result["hits"])
    assert len(rows) == 4 and all(row["expression_design"] == profile for row in rows)


@pytest.fixture
def parent(tmp_path):
    run = tmp_path / "workflows" / ("a" * 32)
    run.mkdir(parents=True)
    reference, knowledge = _imports.make_pack(run)
    return tmp_path, run, reference, knowledge


def test_default_search_and_coverage_shapes_do_not_change_when_design_is_stored(tmp_path):
    store = KnowledgeAgentSession(tmp_path / "session.sqlite3")
    claim(store)
    assert store.search("k", query="") == [{"id": "q", "run_id": "run", "question": "如何检查压力？",
        "qa_type": "closed_book", "topic": "检查", "status": "reserved", "stage": "director", "reason": "",
        "similarity_kind": "lexical_recall_not_semantic_proof", "score": 0.0}]
    assert store.coverage("k") == {"scope": "same_accepted_knowledge", "history_is_not_evidence": True,
        "semantic_uniqueness_guaranteed": False,
        "counts": [{"qa_type": "closed_book", "status": "reserved", "count": 1}]}
    hit = KnowledgeAgentSession(store.path).search("k", query="压力", include_expression=True)[0]
    assert hit["expression_design"] == DESIGN
    assert hit["expression_design_scope"] == "same_knowledge_task_editorial_annotation"
    hit["expression_design"]["form"] = "caller mutation"
    assert store.search("k", query="压力", include_expression=True)[0]["expression_design"] == DESIGN


def test_old_database_migrates_without_inventing_labels_and_owner_can_backfill(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE agent_tasks(row_id INTEGER PRIMARY KEY,knowledge TEXT NOT NULL,"
            "identity TEXT NOT NULL,run_id TEXT NOT NULL,sample_id TEXT NOT NULL,question TEXT NOT NULL,"
            "qa_type TEXT NOT NULL,topic TEXT NOT NULL,stage TEXT NOT NULL,status TEXT NOT NULL,"
            "reason TEXT NOT NULL,UNIQUE(knowledge,identity))")
        db.execute("INSERT INTO agent_tasks VALUES(1,'k','q','run','q','如何检查压力？','closed_book','检查','sft','final_eligible','')")
    store = KnowledgeAgentSession(path)
    assert "expression_design" not in store.search("k", query="", include_expression=True)[0]
    assert store.coverage("k", include_expression=True)["accepted_dimensions"] == {}
    claim(store)
    assert KnowledgeAgentSession(path).search("k", query="", include_expression=True)[0]["expression_design"] == DESIGN
    assert store.coverage("k", include_expression=True)["accepted_dimensions"]["expression_register"] == [
        {"value": DESIGN["register"], "accepted": 1}]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM agent_tasks").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM agent_task_dimensions").fetchone()[0] == len(DESIGN)


def test_claim_replay_preserves_metadata_and_another_owner_cannot_rewrite_it(tmp_path):
    store = KnowledgeAgentSession(tmp_path / "session.sqlite3")
    assert claim(store) is None
    assert claim(store, design=None) is None
    assert claim(store, run="other", design={"form": "另一种设计"}) == {"id": "q", "run_id": "run", "status": "reserved"}
    with pytest.raises(ValueError, match="^knowledge_agent_expression_changed$"):
        claim(store, design={"form": "同owner不同设计"})
    assert store.search("k", query="", include_expression=True)[0]["expression_design"] == DESIGN
    outcome(store)
    assert store.coverage("k", include_expression=True)["accepted_dimensions"]["expression_form"] == [
        {"value": DESIGN["form"], "accepted": 1}]


def test_dimensions_follow_current_final_status_and_exact_knowledge_owner(tmp_path):
    store = KnowledgeAgentSession(tmp_path / "session.sqlite3")
    claim(store, "a", design={"form": "普通提问"})
    claim(store, "b", design={"form": "短促承接"})
    claim(store, "c", knowledge="other", design={"form": "不能混入"})
    outcome(store, "a", status="draft_eligible")
    outcome(store, "b")
    outcome(store, "c", knowledge="other")
    counts = lambda: store.coverage("k", include_expression=True)["accepted_dimensions"]
    assert counts() == {"expression_form": [{"value": "短促承接", "accepted": 1}]}
    outcome(store, "a")
    assert {item["value"] for item in counts()["expression_form"]} == {"普通提问", "短促承接"}
    outcome(store, "b", status="final_quarantined")
    outcome(store, "b", run="wrong-owner")
    assert counts() == {"expression_form": [{"value": "普通提问", "accepted": 1}]}
    outcome(store, "a", status="final_quarantined")
    assert counts() == {}


def test_dimension_coverage_is_bounded_and_uses_indexed_rows_without_loading_design_json(tmp_path, monkeypatch):
    store = KnowledgeAgentSession(tmp_path / "session.sqlite3")
    for index in range(25):
        claim(store, str(index), design={"form": f"open editorial note {index:02}"})
        outcome(store, str(index))
    statements = []
    connect = store._connect
    def traced():
        db = connect()
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(store, "_connect", traced)
    result = store.coverage("k", include_expression=True)
    assert len(result["accepted_dimensions"]["expression_form"]) == 12
    assert result["accepted_dimensions"]["expression_form"][0] == {"value": "open editorial note 00", "accepted": 1}
    assert result["expression_coverage_is_annotation"] is True
    assert result["expression_coverage_scope"] == "same_knowledge_final_eligible_editorial_designs"
    assert not any("expression_design" in statement for statement in statements)
    with connect() as db:
        plan = list(db.execute("EXPLAIN QUERY PLAN SELECT d.value,COUNT(*) FROM agent_tasks t "
            "JOIN agent_task_dimensions d ON d.task_row_id=t.row_id WHERE t.knowledge=? "
            "AND t.status='final_eligible' AND d.dimension=? GROUP BY d.value LIMIT 12", ("k", "expression_form")))
    assert any("INDEX" in row[3] and "agent_tasks_knowledge_status" in row[3] for row in plan)


@pytest.mark.parametrize("design", [{}, {"answer": "not expression metadata"}, {"register": "x" * 257},
                                         {"form": "\x00"}, {"form": "\ud800"}])
def test_invalid_metadata_cannot_create_a_task_or_dimensions(tmp_path, design):
    store = KnowledgeAgentSession(tmp_path / "session.sqlite3")
    with pytest.raises(ValueError, match="invalid_expression_design"):
        claim(store, design=design)
    assert store.search("k", query="", include_expression=True) == []


def test_tool_expression_projection_is_optional_validated_and_does_not_expand_callback_args():
    calls = []
    row = {"id": "q", "question": "怎么用？", "expression_design": deepcopy(DESIGN), "answer": "PRIVATE ANSWER"}
    def history(**kwargs):
        calls.append(kwargs)
        return [row]
    plain = KnowledgeAgentTools([material()], history_search=history)
    old = plain.execute("history_search", {"query": "用"})
    assert "expression_design" not in old["hits"][0]
    optional = KnowledgeAgentTools([material()], history_search=history, include_expression=True)
    result = optional.execute("history_search", {"query": "用"})
    assert result["hits"][0]["expression_design"] == DESIGN
    assert result["scope_binding"] == "caller_filtered_current_knowledge" and result["history_is_not_evidence"]
    assert calls == [{"query": "用", "limit": 4}] * 2 and "PRIVATE" not in canonical(result)
    result["hits"][0]["expression_design"]["form"] = "mutated"
    assert row["expression_design"] == DESIGN
    row["expression_design"] = {"answer": "PRIVATE INJECTION"}
    assert plain.execute("history_search", {"query": "用"}) == old
    with pytest.raises(ValueError, match="^knowledge_agent_invalid_result$"):
        optional.execute("history_search", {"query": "用"})


def test_verified_import_expression_comes_only_from_real_contract_when_enabled(parent):
    output, run, reference, knowledge = parent
    row = _imports.record(reference)
    row["qa_contract"]["expression_design"] = deepcopy(DESIGN)
    summary = _imports.example(run)
    summary["expression_design"] = {"form": "FORGED SUMMARY"}
    _imports.seal(run, {"sft": [row]})
    plain = verified_knowledge_history(output, [summary], knowledge)[0]
    optional = verified_knowledge_history(output, [summary], knowledge, include_expression=True)[0]
    assert "expression_design" not in plain and optional["expression_design"] == DESIGN
    assert {key: value for key, value in optional.items() if key != "expression_design"} == plain
    assert optional["task_identity"] == plain["task_identity"] and "PRIVATE" not in canonical(optional)


def test_legacy_import_ignores_invalid_design_only_in_old_mode(parent):
    output, run, reference, knowledge = parent
    row = _imports.record(reference)
    row["qa_contract"]["expression_design"] = {"answer": "PRIVATE OLD ANSWER"}
    _imports.seal(run, {"sft": [row]})
    assert len(verified_knowledge_history(output, [_imports.example(run)], knowledge)) == 1
    assert verified_knowledge_history(output, [_imports.example(run)], knowledge, include_expression=True) == []


@pytest.mark.parametrize("second_design", [None, {"form": "另一个表达"}])
def test_shared_sft_cot_candidate_expression_conflicts_are_optional(parent, second_design):
    output, run, reference, knowledge = parent
    first = _imports.record(reference)
    first["qa_contract"]["expression_design"] = {"form": "自然短问"}
    second = _imports.record(reference, target="cot")
    if second_design is not None:
        second["qa_contract"]["expression_design"] = second_design
    _imports.seal(run, {"sft": [first], "cot": [second]})
    assert len(verified_knowledge_history(output, [_imports.example(run)], knowledge)) == 1
    assert verified_knowledge_history(output, [_imports.example(run)], knowledge, include_expression=True) == []
    second["qa_contract"]["expression_design"] = deepcopy(first["qa_contract"]["expression_design"])
    _imports.seal(run, {"sft": [first], "cot": [second]})
    assert len(verified_knowledge_history(output, [_imports.example(run)], knowledge, include_expression=True)) == 1
