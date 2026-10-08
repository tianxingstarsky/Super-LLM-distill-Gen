"""RAG evidence remains self-contained and bound to the run's exact source bytes."""
from copy import deepcopy
import hashlib

import pytest

from lib.infrastructure import training_workflow as engine


TEXT = "Before servicing the equipment, disconnect its power supply. Check cable connections carefully and record all maintenance findings."


def receipt():
    return {"provider": "qdrant", "query": "Equipment maintenance", "collection": "manuals", "limit": 5,
            "revision": "connection-revision", "retrieved_at": "2026-10-09T00:00:00Z", "hits": [
                {"id": "point-7", "text": TEXT, "source": "Maintenance guide", "location": "qdrant:point:point-7",
                 "score": 0.8, "fingerprint": hashlib.sha256(TEXT.encode()).hexdigest()}]}


def test_retrieval_receipt_is_pinned_and_exported_with_integrity_manifest(tmp_path):
    source = tmp_path / "retrieved.md"
    source.write_text(TEXT, encoding="utf-8")
    original = receipt()
    run_id = engine.create_run(tmp_path / "output", sources=[source], targets=["cpt"], knowledge_retrieval=original)
    original["hits"][0]["text"] = "Changed after submission"
    run = engine.Workflow(tmp_path / "output", run_id, tmp_path)
    pinned = run.recipe["knowledge_retrieval"]
    assert pinned["hits"][0]["text"] == TEXT
    assert pinned["hits"][0]["snapshot"] == {"file": "0000.md", "sha256": hashlib.sha256(TEXT.encode()).hexdigest()}
    assert run.execute()["status"] == "completed"
    manifest = engine.verify_artifacts(run.path)
    assert "knowledge_retrieval.json" in manifest["sha256"]
    exported = engine.read_json(run.path / "artifacts/knowledge_retrieval.json")
    assert exported == pinned
    (run.path / "artifacts/knowledge_retrieval.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact_integrity_error"):
        engine.verify_artifacts(run.path)


@pytest.mark.parametrize("damage", ["text", "extra_key", "nan", "fingerprint", "missing_hit"])
def test_invalid_or_unmatched_retrieval_evidence_is_rejected_before_run_is_created(tmp_path, damage):
    source = tmp_path / "retrieved.md"
    source.write_text(TEXT, encoding="utf-8")
    value = deepcopy(receipt())
    if damage == "text":
        value["hits"][0]["text"] = "Another source"
    elif damage == "extra_key":
        value["api_key"] = "must-never-enter-recipe"
    elif damage == "nan":
        value["hits"][0]["score"] = float("nan")
    elif damage == "fingerprint":
        value["hits"][0]["fingerprint"] = "invalid"
    else:
        value["hits"] = []
    with pytest.raises(ValueError, match="knowledge_retrieval"):
        engine.create_run(tmp_path / "output", sources=[source], targets=["cpt"], knowledge_retrieval=value)
    assert not (tmp_path / "output").exists()
