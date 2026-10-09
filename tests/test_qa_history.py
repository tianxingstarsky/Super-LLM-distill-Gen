"""Persistent QA memory must preserve contracts and bound history context."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from lib.infrastructure.qa_history import (
    MAX_ANSWER_SNIPPET,
    MAX_CONTEXT_SNIPPET,
    MAX_HISTORY_ITEMS,
    MAX_INDEX_TERMS,
    MAX_POSTINGS_PER_TERM,
    MAX_QUESTION_SNIPPET,
    MAX_QUERY_TERMS,
    QAHistory,
    contract_identity,
)


def accepted(sample_id="sample-1", question="如何设置模型上下文长度？", **changes):
    return {"id": sample_id, "status": "eligible", "question": question,
            "answer": "在生成节点中设置上下文长度。", "qa_type": "closed_book",
            "answer_policy": "answer", "visible_context": "", "source_id": "source-1",
            "source_name": "model-manual.md", "family_id": "family-1", "run_id": "run-1",
            **changes}


def test_history_persists_only_accepted_qa_and_keeps_provenance(tmp_path):
    path = tmp_path / "history" / "qa.sqlite3"
    with QAHistory(path) as history:
        assert history.add(accepted())
        for status in ("rejected", "ready", "failed", "pending", None):
            assert not history.add(accepted("rejected-" + str(status), status=status))
        assert history.coverage() == {"closed_book": 1}
    with QAHistory(path) as history:
        item = history.recent()[0]
        assert item["id"] == "sample-1"
        assert item["source_id"] == "source-1"
        assert item["source_name"] == "model-manual.md"
        assert item["family_id"] == "family-1"
        assert item["run_id"] == "run-1"
        assert history.coverage() == {"closed_book": 1}


def test_question_identity_normalises_composed_unicode_and_exterior_whitespace(tmp_path):
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        assert history.add(accepted(question="  How do I process cafe\u0301 text?\r\n "))
        duplicate = history.duplicate("How do I process café text?")
        assert duplicate["id"] == "sample-1"
        assert duplicate["match_kind"] == "exact_contract"
        assert not history.add(accepted("other", "How do I process café text?"))
        assert history.coverage() == {"closed_book": 1}
        assert contract_identity("How do I process café text?") == duplicate["identity"]


@pytest.mark.parametrize("original,other", [
    ("What is the value of variable Foo?", "What is the value of variable foo?"),
    ("Compute x².", "Compute x2."),
    ("What does 'a  b' contain?", "What does 'a b' contain?"),
])
def test_exact_identity_preserves_literal_case_math_and_internal_spaces(tmp_path, original, other):
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        assert history.add(accepted(question=original))
        assert history.duplicate(other) is None
        assert history.add(accepted("other", other))


def test_exact_evidence_identity_preserves_case_and_code_indentation(tmp_path):
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        assert history.add(accepted(visible_context="if Ready:\n    run()"))
        assert history.duplicate("如何设置模型上下文长度？", visible_context="if ready:\n    run()") is None
        assert history.duplicate("如何设置模型上下文长度？", visible_context="if Ready:\nrun()") is None
        assert history.duplicate("如何设置模型上下文长度？", visible_context="    if Ready:\n    run()") is None


@pytest.mark.parametrize("change", [
    {"question": "Set the timeout to 31 seconds?"},
    {"question": "Do not set the timeout to 30 seconds?"},
    {"visible_context": "The new release uses a different timeout."},
    {"qa_type": "grounded"},
    {"answer_policy": "clarify"},
])
def test_changed_conditions_evidence_and_policies_are_distinct_contracts(tmp_path, change):
    original = accepted(question="Set the timeout to 30 seconds?")
    other = {**original, "id": "other", **change}
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        assert history.add(original)
        assert history.duplicate(other["question"], visible_context=other["visible_context"],
                                 qa_type=other["qa_type"], answer_policy=other["answer_policy"]) is None
        assert history.add(other)


def test_math_operators_are_not_removed_from_identity(tmp_path):
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        assert history.add(accepted(question="What is x+1 when x=3?"))
        assert history.duplicate("What is x-1 when x=3?") is None
        assert history.add(accepted("minus", "What is x-1 when x=3?"))


def test_full_evidence_identity_is_not_based_on_truncated_summary(tmp_path):
    prefix = "Shared source text. " * 100
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        assert history.add(accepted(visible_context=prefix + "version one"))
        assert history.duplicate("如何设置模型上下文长度？", visible_context=prefix + "version two") is None
        assert history.add(accepted("other", visible_context=prefix + "version two"))
        assert len(history.recent()[0]["visible_context"]) <= MAX_CONTEXT_SNIPPET


def test_namespaces_do_not_merge_legitimate_dataset_targets(tmp_path):
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        assert history.add(accepted(), namespace="sft")
        assert history.duplicate("如何设置模型上下文长度？", namespace="multiturn") is None
        assert history.similar("如何设置模型上下文长度？", namespace="multiturn") == []
        assert history.add(accepted(), namespace="multiturn")
        assert history.coverage(namespace="sft") == {"closed_book": 1}
        assert history.coverage(namespace="multiturn") == {"closed_book": 1}
        assert history.recent(namespace="default") == []


def test_chinese_lexical_neighbors_are_candidates_not_semantic_verdicts(tmp_path):
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        assert history.add(accepted("near", "如何设置大模型的上下文长度？"))
        assert history.add(accepted("unrelated", "如何修剪花园里的树枝？"))
        rows = history.similar("怎样设置大模型上下文长度？")
        assert rows[0]["id"] == "near"
        assert rows[0]["match_kind"] == "lexical"
        assert 0 < rows[0]["score"] < 1
        assert history.duplicate("怎样设置大模型上下文长度？") is None


def test_numeric_changes_are_marked_in_similar_results_without_removing_sample(tmp_path):
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        assert history.add(accepted(question="How do I set the timeout to 30 seconds?"))
        rows = history.similar("How do I set the timeout to 60 seconds?")
        assert rows[0]["id"] == "sample-1"
        assert rows[0]["same_numbers"] is False
        assert history.duplicate("How do I set the timeout to 60 seconds?") is None


def test_replay_is_idempotent_and_conflicting_id_cannot_rewrite_history(tmp_path):
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        assert history.add(accepted())
        assert history.add(accepted())
        assert history.duplicate("如何设置模型上下文长度？", exclude_id="sample-1") is None
        with pytest.raises(ValueError, match="qa_history_sample_id_conflict"):
            history.add(accepted(question="How do I set a timeout?"))
        assert history.coverage() == {"closed_book": 1}
        assert history.recent()[0]["question"] == "如何设置模型上下文长度？"


def test_concurrent_connections_accept_exact_contract_once(tmp_path):
    path = tmp_path / "qa.sqlite3"
    # Initialise once before workers to test the writer race, not DDL setup.
    with QAHistory(path):
        pass

    def submit(index):
        with QAHistory(path) as history:
            return history.add(accepted("candidate-" + str(index)))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(16)))
    assert results.count(True) == 1
    with QAHistory(path) as history:
        assert history.coverage() == {"closed_book": 1}
        assert len(history.recent()) == 1


def test_context_and_retrieval_limits_are_enforced(tmp_path):
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        for index in range(40):
            assert history.add(accepted(str(index), "How should model context length be set " + str(index) + "? " + "detail " * 300,
                                        answer="a" * 10000, visible_context="c" * 10000,
                                        api_key="must-not-persist", endpoint={"secret": "must-not-persist"}))
        recent = history.recent(limit=10000)
        similar = history.similar("How should model context length be set?", limit=10000)
        assert len(recent) == MAX_HISTORY_ITEMS
        assert len(similar) == MAX_HISTORY_ITEMS
        for row in recent + similar:
            assert len(row["question"]) <= MAX_QUESTION_SNIPPET
            assert len(row["answer"]) <= MAX_ANSWER_SNIPPET
            assert len(row["visible_context"]) <= MAX_CONTEXT_SNIPPET
            assert "api_key" not in row and "endpoint" not in row
        assert history.recent(limit=0) == []
        assert history.similar("How should model context length be set?", limit=0) == []
        assert history.connection.execute("SELECT MAX(n) FROM (SELECT COUNT(*) n FROM qa_history_terms GROUP BY row_id)").fetchone()[0] <= MAX_INDEX_TERMS


def test_lookup_uses_indexed_bounded_postings_not_a_history_scan(tmp_path):
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        for index in range(500):
            assert history.add(accepted(str(index), "How do I configure model context for document " + str(index) + "?"))
        statements = []
        history.connection.set_trace_callback(statements.append)
        result = history.similar("How do I configure model context for a document?", limit=5)
        assert result
        posting_reads = [statement for statement in statements if statement.startswith("SELECT row_id FROM qa_history_terms")]
        assert 1 <= len(posting_reads) <= MAX_QUERY_TERMS
        assert all(f"LIMIT {MAX_POSTINGS_PER_TERM}" in statement for statement in posting_reads)
        candidate_reads = [statement for statement in statements if statement.startswith("SELECT * FROM qa_history")]
        assert len(candidate_reads) == 1 and "WHERE row_id IN (" in candidate_reads[0]
        plan = history.connection.execute(
            "EXPLAIN QUERY PLAN SELECT row_id FROM qa_history_terms WHERE namespace=? AND term=? ORDER BY row_id DESC LIMIT ?",
            ("default", "w:model", MAX_POSTINGS_PER_TERM)).fetchall()
        assert any("SEARCH" in row[3] and "PRIMARY KEY" in row[3] for row in plan)


def test_neighbor_order_is_stable_and_coverage_does_not_count_replays(tmp_path):
    path = tmp_path / "qa.sqlite3"
    with QAHistory(path) as history:
        assert history.add(accepted("b", "How do I set model context?", visible_context="second"))
        assert history.add(accepted("a", "How do I set model context?", visible_context="first"))
        assert history.add(accepted("a", "How do I set model context?", visible_context="first"))
        first = history.similar("How do I set model context?")
        assert [row["id"] for row in first] == ["a", "b"]
        assert history.coverage() == {"closed_book": 2}
    with QAHistory(path) as history:
        assert history.similar("How do I set model context?") == first


def test_invalid_question_does_not_create_partial_rows(tmp_path):
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        for question in ("", None, 23, "q" * 32769):
            with pytest.raises(ValueError):
                history.add(accepted(question=question))
        assert history.coverage() == {}
        assert history.recent() == []


def test_adaptive_same_opening_can_have_distinct_activity_goals_and_intents(tmp_path):
    question = "帮我处理一下这份材料。"
    first = {"interaction_goal": "根据材料完成操作卡。", "user_intent": "制作操作卡。"}
    second = {"interaction_goal": "根据材料比较操作方案。", "user_intent": "选择适用方案。"}
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        assert history.add(accepted("card", question, dialogue_design=first))
        assert history.duplicate(question, dialogue_design=second) is None
        assert history.add(accepted("compare", question, dialogue_design=second))
        assert not history.add(accepted("same", question, dialogue_design=first))
        assert history.duplicate(question, dialogue_design=first)["id"] == "card"
        assert history.duplicate(question) is None
        summaries = history.similar(question)
        assert {item["dialogue_design"]["interaction_goal"] for item in summaries} == {
            first["interaction_goal"], second["interaction_goal"]}
        assert history.coverage() == {"closed_book": 2}


def test_adaptive_identity_uses_full_goal_but_does_not_hash_wording_only_guidance(tmp_path):
    goal = "保留实际数据的全部必要条件。" * 120
    first = {"interaction_goal": goal + "版本一", "user_intent": "共同分析。", "turn_guidance": ["先比较。"]}
    second = {**first, "interaction_goal": goal + "版本二"}
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        assert history.add(accepted("one", dialogue_design=first))
        assert history.add(accepted("two", dialogue_design=second))
        changed_wording = {**first, "turn_guidance": ["换个说法先比较。"]}
        assert history.duplicate("如何设置模型上下文长度？", dialogue_design=changed_wording)["id"] == "one"
        assert all(len(item["dialogue_design"]["interaction_goal"]) <= 1024 for item in history.recent())


def test_pre_design_history_schema_upgrades_without_rehashing_old_contracts(tmp_path):
    path = tmp_path / "old.sqlite3"
    row = accepted()
    identity = contract_identity(row["question"])
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE qa_history (row_id INTEGER PRIMARY KEY, namespace TEXT NOT NULL, "
            "sample_id TEXT NOT NULL, identity TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL, "
            "qa_type TEXT NOT NULL, visible_context TEXT NOT NULL, answer_policy TEXT NOT NULL, "
            "family_id TEXT NOT NULL, parent_id TEXT NOT NULL, source_id TEXT NOT NULL, source_name TEXT NOT NULL, "
            "run_id TEXT NOT NULL, UNIQUE(namespace,sample_id), UNIQUE(namespace,identity))")
        connection.execute("INSERT INTO qa_history (namespace,sample_id,identity,question,answer,qa_type,"
            "visible_context,answer_policy,family_id,parent_id,source_id,source_name,run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("default", row["id"], identity, row["question"], row["answer"], row["qa_type"], "", "answer",
             row["family_id"], "", row["source_id"], row["source_name"], row["run_id"]))
    with QAHistory(path) as history:
        found = history.duplicate(row["question"])
        assert found["identity"] == identity and "dialogue_design" not in found
        assert history.add(row)  # replay retains its original identity
        assert history.add(accepted("new", dialogue_design={"interaction_goal": "实际新活动。"}))
    with QAHistory(path) as history:
        assert history.duplicate(row["question"])["identity"] == identity


def test_adaptive_design_identity_is_bounded_before_any_history_write(tmp_path):
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        for design in ({}, {"interaction_goal": "g" * 4001}, {"interaction_goal": "a\x00b"},
                       {"interaction_goal": "可用", "user_intent": False}):
            with pytest.raises(ValueError, match="qa_history_dialogue_design_invalid"):
                history.add(accepted(dialogue_design=design))
        assert not history.recent()
