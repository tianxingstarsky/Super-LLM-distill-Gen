from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from lib.domain.workflow_qa_director import validate_qa_director
from lib.infrastructure.knowledge_agent_session import KnowledgeAgentSession, digest, knowledge_identity


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def claim(store, knowledge="k", identity="q", run="run", sample="sample", question="如何检查压力？"):
    return store.claim(knowledge, identity, run_id=run, sample_id=sample, question=question,
                       qa_type="closed_book", topic="检查压力")


def test_same_knowledge_identity_survives_order_and_duplicate_materials():
    a, b = {"cleaned_text_sha256": "a" * 64}, {"cleaned_text_sha256": "b" * 64}
    assert knowledge_identity([a, b, a]) == knowledge_identity([b, a])
    assert knowledge_identity([a]) != knowledge_identity([a, b])


def test_history_survives_process_restart_and_keeps_real_tool_pairs(tmp_path):
    path = tmp_path / "sessions.sqlite3"
    messages = [{"role": "user", "content": "plan"},
        {"role": "assistant", "reasoning_content": "real reasoning", "tool_calls": [{"id": "call-a"}]},
        {"role": "tool", "tool_call_id": "call-a", "content": "actual source result"},
        {"role": "assistant", "content": "final", "provider_state": {"signature": "opaque"}}]
    assert KnowledgeAgentSession(path).remember_turn("s", "op", messages)
    assert KnowledgeAgentSession(path).history("s") == messages
    assert not KnowledgeAgentSession(path).remember_turn("s", "op", messages)
    with pytest.raises(ValueError, match="operation_changed"):
        KnowledgeAgentSession(path).remember_turn("s", "op", [{"role": "user", "content": "different"}])


def test_context_compacts_whole_turns_without_deleting_search_memory(tmp_path):
    store = KnowledgeAgentSession(tmp_path / "sessions.sqlite3")
    old = [{"role": "user", "content": "old" * 100}]
    current = [{"role": "user", "content": "new"}, {"role": "assistant", "content": "done"}]
    store.remember_turn("s", "old", old)
    store.remember_turn("s", "new", current)
    claim(store)
    assert store.history("s", max_bytes=150) == current
    assert store.history("other") == []
    assert store.history("s", exclude_operation="new") == old
    assert store.search("k", query="压力", limit=1)[0]["question"] == "如何检查压力？"


def test_corrupt_saved_turn_is_never_replayed(tmp_path):
    path = tmp_path / "sessions.sqlite3"
    store = KnowledgeAgentSession(path)
    store.remember_turn("s", "op", [{"role": "user", "content": "verified"}])
    with sqlite3.connect(path) as db:
        db.execute("UPDATE agent_turns SET payload='[]'")
    with pytest.raises(ValueError, match="integrity"):
        store.history("s")


def test_exact_claim_is_atomic_across_competing_jobs(tmp_path):
    store = KnowledgeAgentSession(tmp_path / "sessions.sqlite3")
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda index: claim(store, run=str(index), sample=str(index)), range(16)))
    assert sum(result is None for result in results) == 1
    winner = next(index for index, result in enumerate(results) if result is None)
    assert claim(store, run=str(winner), sample=str(winner)) is None
    assert claim(store, knowledge="different-knowledge") is None


def test_scope_and_final_vs_draft_state_cannot_be_confused(tmp_path):
    store = KnowledgeAgentSession(tmp_path / "sessions.sqlite3")
    claim(store)
    claim(store, knowledge="other", question="私人财务数据")
    store.outcome("k", "q", run_id="run", sample_id="sample", stage="sft", status="draft_eligible")
    assert store.coverage("k")["counts"] == [{"qa_type": "closed_book", "status": "draft_eligible", "count": 1}]
    store.outcome("k", "q", run_id="wrong-owner", sample_id="sample", stage="package", status="final_eligible")
    assert store.search("k", query="检查")[0]["status"] == "draft_eligible"
    assert "财务" not in str(store.search("k", query="财务"))
    store.outcome("k", "q", run_id="run", sample_id="sample", stage="package", status="final_quarantined", reason="context review")
    assert store.search("k", query="压力")[0]["reason"] == "context review"
    assert store.coverage("k")["semantic_uniqueness_guaranteed"] is False


def test_lexical_history_has_no_answers_and_is_bounded(tmp_path):
    store = KnowledgeAgentSession(tmp_path / "sessions.sqlite3")
    for index in range(80):
        claim(store, identity=str(index), sample=str(index), question=f"设备{index}怎样检查压力？")
    hits = store.search("k", query="设备检查压力", limit=3)
    assert len(hits) == 3
    assert all("answer" not in hit and hit["similarity_kind"] == "lexical_recall_not_semantic_proof" for hit in hits)


def test_context_controls_opt_in_without_changing_legacy_configs():
    old = validate_qa_director({"enabled": True})
    assert "context_mode" not in old and "tools_mode" not in old
    new = validate_qa_director({"enabled": True, "context_mode": "persistent"})
    assert new["tools_mode"] == "auto"
    with pytest.raises(ValueError, match="require_persistent"):
        validate_qa_director({"enabled": True, "context_mode": "batch", "tools_mode": "auto"})
    for bad in ([], {}, 3):
        with pytest.raises(ValueError, match="invalid_qa_director_context_mode"):
            validate_qa_director({"enabled": True, "context_mode": bad})
