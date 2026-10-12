"""History migration is bounded, trusted, resumable and safe around old runs."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from lib.domain.math_tasks import build_gsm8k, verified_arithmetic_fingerprint
from lib.infrastructure import training_workflow as engine
from lib.infrastructure import workflow_arithmetic as migration
from lib.infrastructure.arithmetic_registry import ArithmeticRegistry
from lib.infrastructure.production_checkpoints import ProductionCheckpoints
from lib.io_utils import atomic_json
from tests.test_arithmetic_workflow_independence import NoModels


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield  # Every model is forbidden or injected. No TCP fixture is needed.


def _record():
    sample = build_gsm8k("Old theme", 0, version=2)
    return {"id": "old-id", "source_id": "old-source", "status": "eligible",
            "question": sample["question"], "answer": sample["answer"],
            "arithmetic_expression": sample["_expression"], "verified_result": sample["_result"],
            "calculation_steps": sample["_steps"], "generator_version": 2}


def _old_run(output, *, targets=None, status="completed", registry=False):
    run = output / "workflows" / ("a" * 32)
    run.mkdir(parents=True, exist_ok=True)
    recipe = {"targets": targets or ["gsm8k"], **({"arithmetic_registry_version": 1} if registry else {})}
    atomic_json(run / "recipe.json", recipe)
    atomic_json(run / "state.json", {"status": status, "recipe_hash": engine.digest(recipe), "attempt": 1})
    return run


def _history(run, location, row):
    relative = {
        "stage": "stage-results/gsm8k.jsonl",
        "artifact": "artifacts/gsm8k.records.json",
        "round": "production/round-000001/gsm8k.jsonl",
        "round_artifact": "production/round-000001/artifacts/gsm8k.records.json",
        "checkpoint": "checkpoints/gsm8k/" + "b" * 64 + ".json",
        "sqlite": "production-checkpoints.sqlite3",
    }[location]
    path = run / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if location == "sqlite":
        ProductionCheckpoints(path).put("gsm8k", "old-key", [row])
    elif location == "checkpoint":
        atomic_json(path, {"data": [row], "sha256": engine.digest([row])})
    elif path.suffix == ".jsonl":
        path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        atomic_json(path, [row])
    return path


def _run(output):
    run_id = engine.create_run(output, targets=["gsm8k"], tasks=1)
    workflow = engine.Workflow(output, run_id, output, generator=NoModels(), jev=NoModels())
    state = workflow.execute()
    return workflow, state


@pytest.mark.parametrize("location", ["stage", "artifact", "round", "round_artifact", "checkpoint", "sqlite"])
def test_all_committed_historical_math_locations_are_imported_before_new_allocation(tmp_path, location):
    original = _record()
    old = _old_run(tmp_path)
    path = _history(old, location, original)
    before = path.read_bytes()
    workflow, state = _run(tmp_path)
    assert state["status"] == "completed", state.get("error")
    generated = engine.read_json(workflow.path / "artifacts/gsm8k.records.json")
    assert len(generated) == 1
    assert verified_arithmetic_fingerprint(generated[0]) != verified_arithmetic_fingerprint(original)
    assert path.read_bytes() == before
    assert ArithmeticRegistry(tmp_path / "arithmetic-registry.sqlite3").snapshot()["registered"] == 2


def test_unchanged_legacy_run_is_skipped_without_enumerating_its_checkpoint_files(tmp_path, monkeypatch):
    old = _old_run(tmp_path)
    _history(old, "checkpoint", _record())
    assert _run(tmp_path)[1]["status"] == "completed"

    def no_rescan(run):
        pytest.fail("An unchanged migrated run must not enumerate old checkpoint files again.")

    monkeypatch.setattr(migration, "_history_files", no_rescan)
    assert _run(tmp_path)[1]["status"] == "completed"


@pytest.mark.parametrize("damage", ["calculation", "json", "checksum", "metadata", "missing_targets"])
def test_untrusted_history_fails_closed_before_any_new_problem_is_reserved(tmp_path, damage):
    old = _old_run(tmp_path)
    record = _record()
    if damage == "calculation":
        record["verified_result"] += 1
    path = _history(old, "checkpoint" if damage == "checksum" else "artifact", record)
    if damage == "json":
        path.write_text("[{", encoding="utf-8")
    if damage == "checksum":
        atomic_json(path, {"data": [record], "sha256": "incorrect"})
    if damage in {"metadata", "missing_targets"}:
        atomic_json(old / "recipe.json", {} if damage == "missing_targets" else {"targets": ["sft"]})
    workflow, state = _run(tmp_path)
    assert state["status"] == "failed"
    assert state["error"] == "arithmetic_history_invalid"
    registry = tmp_path / "arithmetic-registry.sqlite3"
    if registry.exists():
        assert ArithmeticRegistry(registry).snapshot()["allocated"] == 0


def test_active_legacy_math_blocks_migration_without_activating_or_creating_registry(tmp_path):
    _old_run(tmp_path, status="running")
    workflow, state = _run(tmp_path)
    assert state["status"] == "failed" and state["error"] == "arithmetic_legacy_run_active"
    assert not (tmp_path / "arithmetic-registry.sqlite3").exists()
    assert not (tmp_path / ".arithmetic-registry-required.json").exists()


def test_other_active_workflow_and_incomplete_creation_do_not_block_math(tmp_path, monkeypatch):
    old = _old_run(tmp_path, targets=["sft"], status="running")
    ProductionCheckpoints(old / "production-checkpoints.sqlite3").put("sft", "request", {"answer": "ok"})
    (tmp_path / "workflows" / ("c" * 32) / "inputs").mkdir(parents=True)

    def unexpected_read(path):
        pytest.fail("Unrelated workflow checkpoints must not be scanned.")

    monkeypatch.setattr(migration, "_history_records", unexpected_read)
    assert _run(tmp_path)[1]["status"] == "completed"


def test_missing_ledger_after_generated_run_is_not_recreated(tmp_path):
    assert _run(tmp_path)[1]["status"] == "completed"
    path = tmp_path / "arithmetic-registry.sqlite3"
    path.unlink()
    workflow, state = _run(tmp_path)
    assert state["status"] == "failed" and state["error"] == "arithmetic_registry_missing"
    assert not path.exists()


def test_new_protocol_outputs_without_a_ledger_are_not_silently_skipped(tmp_path):
    old = _old_run(tmp_path, registry=True)
    _history(old, "stage", _record())
    workflow, state = _run(tmp_path)
    assert state["status"] == "failed" and state["error"] == "arithmetic_registry_missing"
    assert not (tmp_path / "arithmetic-registry.sqlite3").exists()


def test_another_started_new_protocol_run_without_math_does_not_imply_lost_registry(tmp_path):
    old = _old_run(tmp_path, registry=True, status="running")
    ProductionCheckpoints(old / "production-checkpoints.sqlite3")
    workflow, state = _run(tmp_path)
    assert state["status"] == "completed", state.get("error")
    assert ArithmeticRegistry(tmp_path / "arithmetic-registry.sqlite3").snapshot()["allocated"] == 1


def test_legacy_resume_with_registry_can_reuse_checkpoints_but_cannot_generate_more(tmp_path):
    old = _old_run(tmp_path)
    ArithmeticRegistry(tmp_path / "arithmetic-registry.sqlite3")
    workflow = SimpleNamespace(path=old, recipe={}, checkpoint_has=lambda *a, **k: False)
    with pytest.raises(ValueError, match="arithmetic_legacy_run_requires_migration"):
        with migration.legacy_arithmetic_guard(workflow, [{"id": "unfinished"}]):
            pytest.fail("A legacy generation operation must not start after migration.")
    workflow.checkpoint_has = lambda *a, **k: True
    with migration.legacy_arithmetic_guard(workflow, [{"id": "cached"}]):
        pass
