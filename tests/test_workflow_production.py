"""Offline production delivery, bounded replenishment, and restart accounting."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tracemalloc

import pytest

from lib.domain.workflow_creation import validate_creation
from lib.domain.workflow_production import validate_production, production_batch_size
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_production import ProductionInputs
from lib.llm_client import BudgetExceeded


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """These tests inject offline clients; no shared TCP port is needed."""
    yield


TEXT = "设备启动前必须检查电源连接。发现故障时应先断电，再检查线路。维护结束后记录检查结果。"


def verdict(keep=True):
    score = 5 if keep else 1
    return {"keep": keep, "grounded": keep, "reasoning_valid": keep, "correctness": score,
            "scores": {key: score for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "Evidence agrees with answer." if keep else "Rejected for correctness."}


class Generator:
    model = "production-offline-generator"

    def __init__(self, *, duplicate_first=0, bad_first=0, budget_at=None):
        self.usage = {"calls": 0}
        self.calls, self.sft_calls = [], 0
        self.duplicate_first, self.bad_first, self.budget_at = duplicate_first, bad_first, budget_at

    def chat(self, messages, **kwargs):
        data = json.loads(messages[1]["content"])
        self.calls.append(data)
        self.usage["calls"] += 1
        if "count" in data:
            return json.dumps({"tasks": [f"解释维护操作的第 {index} 种情况及其条件和边界"
                                        for index in range(data["offset"], data["offset"] + data["count"])]})
        if "另一个独立" in messages[0]["content"]:
            return json.dumps({"answer": "BAD 不需要断电即可检查。", "reasoning": "忽略必要的断电步骤。"})
        self.sft_calls += 1
        if self.sft_calls == self.budget_at:
            self.budget_at = None
            raise BudgetExceeded("private provider payload")
        source = data["source"]
        identity = "same" if self.sft_calls <= self.duplicate_first else source["id"][:16]
        bad = self.sft_calls <= self.bad_first
        return json.dumps({"question": f"设备故障时应如何检查？情境 {identity}",
            "answer": ("BAD " if bad else "GOOD ") + "应先断电，再检查线路。",
            "reasoning": "原文规定先断电，因此线路检查在断电之后。", "quotes": [TEXT]})


class Reviewer:
    model = "production-offline-reviewer"

    def __init__(self, reject_package=0):
        self.usage = {"calls": 0}
        self.package_calls = 0
        self.reject_package = reject_package

    def chat(self, messages, **kwargs):
        self.usage["calls"] += 1
        data = json.loads(messages[1]["content"])
        if "target" in data.get("context", {}):
            self.package_calls += 1
            return json.dumps(verdict(self.package_calls > self.reject_package))
        return json.dumps(verdict("BAD " not in json.dumps(data["answer"], ensure_ascii=False)))


def create(tmp_path, *, goals=None, source=True, targets=("sft",), **production):
    paths = []
    if source:
        path = tmp_path / "source.txt"
        path.write_text(TEXT, encoding="utf-8")
        paths = [path]
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=paths, brief="" if source else "维护问答生成",
        targets=targets, max_units=100, production={"goals": goals if goals is not None else {"sft": 3},
            "round_size": 2, "min_acceptance_rate": 0, **production})
    return output, run_id, engine.run_path(output, run_id)


@pytest.mark.parametrize("value", [
    {"goals": {"sft": True}}, {"goals": {"sft": 1_000_001}}, {"goals": {"dpo": 2}},
    {"goals": {"sft": 3}, "max_rounds": 0}, {"goals": {}, "budget_usd": float("nan")},
    {"goals": {}, "item_retries": 6}, {"enabled": True}, {"version": True},
])
def test_production_rejects_invalid_or_ambiguous_limits(value):
    with pytest.raises(ValueError):
        validate_production(value, ["sft"])


def test_million_goal_is_valid_only_with_bounded_production():
    validate_creation(targets=["sft"], tasks=1_000_000, sample_count=1_000_000,
                      production={"goals": {"sft": 1_000_000}})
    with pytest.raises(ValueError):
        validate_creation(targets=["sft"], tasks=1_000_000, sample_count=1_000_000)
    config = validate_production({"goals": {"sft": 1_000_000}, "round_size": 997}, ["sft"])
    config["max_rounds"] = 2000
    attempted = rounds = 0
    tracemalloc.start()
    while size := production_batch_size(config, {"sft": attempted}, attempted, rounds):
        assert size <= 997
        attempted += size
        rounds += 1
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert attempted == 1_000_000 and rounds == 1004 and peak < 1_000_000


def test_delivery_fills_post_dedup_gap_without_reusing_ids(tmp_path):
    output, run_id, path = create(tmp_path, goals={"sft": 3})
    generator = Generator(duplicate_first=2)
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert state["production"]["goals"]["sft"] == {
        "goal": 3, "eligible": 3, "remaining": 0, "attempted": 4, "stop_reason": "goal_reached"}
    assert generator.sft_calls == 4
    records = engine.read_json(path / "artifacts/sft.records.json")
    assert len({row["id"] for row in records}) == 4
    assert sum(row["status"] == "eligible" for row in records) == 3
    assert sum(row["status"] == "duplicate" for row in records) == 1
    assert engine.verify_artifacts(path)["counts"] == {"sft": 3}
    assert not (path / "checkpoints/sft").exists()


def test_delivery_fills_quality_rejection_gap(tmp_path):
    output, run_id, _ = create(tmp_path, goals={"sft": 2})
    generator = Generator(bad_first=2)
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert state["production"]["goals"]["sft"]["eligible"] == 2
    assert state["production"]["attempted"] == 3


def test_package_review_rejection_is_replenished_and_finalization_reuses_reviews(tmp_path):
    output, run_id, path = create(tmp_path, goals={"sft": 3})
    recipe = engine.read_json(path / "recipe.json")
    recipe["package_review"] = {"enabled": True, "mode": "all", "sample_percent": 1, "max_samples_per_target": 1000}
    from lib.io_utils import atomic_json
    atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "state.json", state)
    reviewer = Reviewer(reject_package=1)
    state = engine.Workflow(output, run_id, tmp_path, generator=Generator(), jev=reviewer).execute()
    assert state["status"] == "completed", state.get("error")
    assert state["production"]["goals"]["sft"]["eligible"] == 3
    assert reviewer.package_calls == 4
    assert engine.verify_artifacts(path)["counts"]["sft"] == 3


def test_budget_failure_pauses_task_and_resume_keeps_paid_success(tmp_path):
    output, run_id, path = create(tmp_path, goals={"sft": 2})
    generator = Generator(budget_at=2)
    reviewer = Reviewer()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=reviewer).execute()
    assert state["status"] == "failed" and state["error"] == "budget_exhausted"
    assert state["production"]["status"] == "paused"
    assert generator.sft_calls == 2
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=reviewer).execute(resume_run=True)
    assert state["status"] == "completed", state.get("error")
    assert generator.sft_calls == 3  # First success was durably cached before interruption.
    assert engine.verify_artifacts(path)["counts"]["sft"] == 2


def test_source_preserving_cpt_stops_honestly_without_synthetic_copies(tmp_path):
    output, run_id, path = create(tmp_path, goals={"cpt": 50}, targets=["cpt"])
    state = engine.Workflow(output, run_id, tmp_path).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert state["production"]["goals"]["cpt"]["eligible"] == 1
    assert state["production"]["goals"]["cpt"]["remaining"] == 49
    assert state["production"]["goals"]["cpt"]["stop_reason"] == "source_exhausted"
    assert state["usage"] == {} and engine.verify_artifacts(path)["counts"]["cpt"] == 1


def test_cpt_can_use_no_delivery_goal(tmp_path):
    output, run_id, _ = create(tmp_path, goals={}, targets=["cpt"])
    state = engine.Workflow(output, run_id, tmp_path).execute()
    assert state["status"] == "completed", state.get("error")
    assert state["production"]["goals"] == {} and state["production"]["counts"] == {"cpt": 1}


@pytest.mark.parametrize("limits,reason", [
    ({"max_attempts": 2}, "max_attempts"), ({"max_rounds": 1}, "max_rounds"),
    ({"min_acceptance_rate": .1, "low_acceptance_rounds": 1}, "low_acceptance_rate"),
])
def test_unproductive_runs_stop_at_explicit_limits(tmp_path, limits, reason):
    output, run_id, path = create(tmp_path, goals={"sft": 4}, **limits)
    state = engine.Workflow(output, run_id, tmp_path, generator=Generator(bad_first=1000), jev=Reviewer()).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert state["production"]["goals"]["sft"]["remaining"] == 4
    assert state["production"]["goals"]["sft"]["stop_reason"] == reason
    assert engine.verify_artifacts(path)["counts"]["sft"] == 0


def test_transient_one_item_failure_does_not_abort_healthy_items(tmp_path):
    class Intermittent(Generator):
        def chat(self, messages, **kwargs):
            data = json.loads(messages[1]["content"])
            if data.get("source", {}).get("generation_variant") is None:
                raise TimeoutError("source text and key must not be saved")
            return super().chat(messages, **kwargs)
    output, run_id, path = create(tmp_path, goals={"sft": 2})
    state = engine.Workflow(output, run_id, tmp_path, generator=Intermittent(), jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    records = engine.read_json(path / "artifacts/sft.records.json")
    rejected = [row for row in records if row["status"] == "quarantined"]
    assert len(rejected) == 1 and rejected[0]["request_attempts"] == 3
    assert rejected[0]["request_error"] == "service_timeout"
    assert "source text and key" not in (path / "state.json").read_text(encoding="utf-8")


def test_open_requirement_keeps_planning_offsets_across_rounds(tmp_path):
    output, run_id, _ = create(tmp_path, goals={"sft": 3}, source=False)
    generator = Generator()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert [call["offset"] for call in generator.calls if "count" in call] == [0, 2]


def test_different_target_goals_do_not_overproduce_sft(tmp_path):
    output, run_id, path = create(tmp_path, goals={"sft": 1, "dpo": 3}, targets=["sft", "dpo"])
    state = engine.Workflow(output, run_id, tmp_path, generator=Generator(), jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert engine.verify_artifacts(path)["counts"] == {"sft": 1, "dpo": 3}


def test_source_index_generates_millionth_candidate_without_materializing_prefix(tmp_path):
    unit = {"id": "a" * 64, "source_id": "b" * 64, "kind": "document", "text": TEXT, "status": "ready"}
    with __import__("contextlib").closing(ProductionInputs(tmp_path / "index.sqlite3", [unit])) as index:
        tracemalloc.start()
        rows = list(index.rows(999_999, 2, expand=True))
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    assert len(rows) == 2 and rows[0]["id"] != rows[1]["id"] and rows[0]["id"] != unit["id"]
    assert peak < 2_000_000


def test_completed_round_survives_cancellation_and_is_exportable_before_resume(tmp_path):
    output, run_id, path = create(tmp_path, goals={"sft": 3})
    class StopAfterRound(engine.Workflow):
        def event(self, kind, **fields):
            super().event(kind, **fields)
            if kind == "production_round_completed":
                engine.cancel(output, run_id)
    generator, reviewer = Generator(), Reviewer()
    state = StopAfterRound(output, run_id, tmp_path, generator=generator, jev=reviewer).execute()
    assert state["status"] == "cancelled"
    assert state["production"]["partial_export"] is True
    assert generator.sft_calls == 2
    manifest = engine.verify_artifacts(path)
    assert manifest["counts"]["sft"] == 2 and manifest["production"]["status"] == "cancelled"
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=reviewer).execute(resume_run=True)
    assert state["status"] == "completed", state.get("error")
    assert generator.sft_calls == 3 and state["production"]["round"] == 2


def test_fatal_after_committed_round_exports_only_committed_rows(tmp_path):
    output, run_id, path = create(tmp_path, goals={"sft": 3})
    generator, reviewer = Generator(budget_at=3), Reviewer()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=reviewer).execute()
    assert state["status"] == "failed" and state["error"] == "budget_exhausted"
    assert state["stages"]["sft"]["status"] == "failed"
    assert state["stages"]["package"]["phase"] == "partial_export"
    manifest = engine.verify_artifacts(path)
    assert manifest["counts"]["sft"] == 2
    assert manifest["production"]["partial_export"] is True and manifest["production"]["status"] == "paused"
    assert manifest["production"]["goals"]["sft"]["remaining"] == 1
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=reviewer).execute(resume_run=True)
    assert state["status"] == "completed" and generator.sft_calls == 4


def test_committed_round_tampering_fails_before_new_paid_calls(tmp_path):
    output, run_id, path = create(tmp_path, goals={"sft": 3})
    generator = Generator(budget_at=3)
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "failed"
    committed = path / "production/round-000001/sft.jsonl"
    committed.write_text(committed.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
    calls = generator.sft_calls
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute(resume_run=True)
    assert state["status"] == "failed" and state["error"] == "production_round_integrity_error"
    assert generator.sft_calls == calls


def test_final_publication_loss_is_replenished_without_repeating_existing_generation(tmp_path):
    output, run_id, path = create(tmp_path, goals={"sft": 3})
    class ConcurrentPublication(engine.Workflow):
        lost = False
        def final_qa_duplicate(self, row, target, history):
            if getattr(self, "_production_finalizing", False) and not self.lost and row.get("status") == "eligible":
                self.lost = True
                return {"id": "other-run:published-same-contract"}
            return None
    generator = Generator()
    state = ConcurrentPublication(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert generator.sft_calls == 4 and state["production"]["attempted"] == 4
    manifest = engine.verify_artifacts(path)
    assert manifest["counts"]["sft"] == 3 and manifest["production"]["status"] == "completed"
    assert manifest["production"]["goals"]["sft"]["remaining"] == 0


def test_final_report_and_state_agree_on_failed_delivery_status(tmp_path):
    output, run_id, path = create(tmp_path, goals={"sft": 4}, min_acceptance_rate=.1, low_acceptance_rounds=1)
    state = engine.Workflow(output, run_id, tmp_path, generator=Generator(bad_first=100), jev=Reviewer()).execute()
    report = engine.read_json(path / "artifacts/quality.json")
    assert report["production"]["status"] == state["production"]["status"] == "needs_attention"
    assert report["production"]["stop_reason"] == state["production"]["stop_reason"] == "low_acceptance_rate"
    assert report["production"]["goals"] == state["production"]["goals"]


def test_production_checkpoint_verifies_digest_before_returning_paid_response(tmp_path):
    output, run_id, path = create(tmp_path)
    workflow = engine.Workflow(output, run_id, tmp_path)
    value = {"answer": "paid response"}
    assert workflow.checkpoint(["call", "test"], lambda: value) == value
    with sqlite3.connect(path / "production-checkpoints.sqlite3") as connection:
        connection.execute("UPDATE checkpoints SET payload=?", ('{"answer":"tampered"}',))
    with pytest.raises(ValueError, match="checkpoint_integrity_error"):
        workflow.checkpoint(["call", "test"], lambda: pytest.fail("must not repeat paid request"))


def test_authentication_failure_is_not_quarantined_as_one_bad_sample(tmp_path):
    class AuthenticationFailure(Exception):
        status_code = 401
    class Unauthorized(Generator):
        def chat(self, messages, **kwargs):
            raise AuthenticationFailure("private credential and endpoint")
    output, run_id, path = create(tmp_path, goals={"sft": 2})
    state = engine.Workflow(output, run_id, tmp_path, generator=Unauthorized(), jev=Reviewer()).execute()
    assert state["status"] == "failed" and state["error"] == "service_authentication_failed"
    assert state["production"]["stop_reason"] == "service_authentication_failed"
    assert not list((path / "production").glob("round-*/receipt.json"))
    assert "private credential" not in (path / "state.json").read_text(encoding="utf-8")


def test_post_trim_loss_is_replenished(tmp_path):
    from lib.io_utils import atomic_json
    output, run_id, path = create(tmp_path, goals={"sft": 2})
    recipe = engine.read_json(path / "recipe.json")
    recipe["reasoning_trim"] = {"enabled": True, "template": "leakage", "instruction": "", "custom_prompt": ""}
    atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "state.json", state)
    class RejectFirstTrim(engine.Workflow):
        trim_rejected = False
        def trim_reasoning(self, tagged):
            sample = tagged["sample"]
            if not self.trim_rejected:
                self.trim_rejected = True
                return [{**self.rejected_row(sample), "trim_target": tagged["target"]}]
            return [{**sample, "trim_target": tagged["target"]}]
        def rejected_row(self, sample):
            return super().rejected(sample, "reasoning_trim_failed_after_repair")
    generator = Generator()
    state = RejectFirstTrim(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert state["production"]["goals"]["sft"]["eligible"] == 2 and generator.sft_calls == 3


def test_source_processing_cap_is_distinct_from_exhausted_sources(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps({"text": TEXT}, ensure_ascii=False) + "\n" +
                      json.dumps({"text": "保养结束后关闭设备，并把维修时间记录在日志中。"}, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["cpt"], max_units=1,
                              production={"goals": {"cpt": 2}})
    state = engine.Workflow(output, run_id, tmp_path).execute()
    assert state["status"] == "needs_attention"
    assert state["input_summary"]["deferred"] == 1
    assert state["production"]["goals"]["cpt"]["stop_reason"] == "source_limit_reached"
    assert state["production"]["stop_reason"] == "source_limit_reached"


@pytest.mark.parametrize("commit_side,round_number", [("before", 1), ("after", 1), ("before", 2), ("after", 2)])
def test_final_reconciliation_resume_is_safe_on_both_receipt_commit_boundaries(tmp_path, monkeypatch,
                                                                            commit_side, round_number):
    from lib.infrastructure import workflow_production as production_engine
    output, run_id, path = create(tmp_path, goals={"sft": 3})
    original_atomic_json = production_engine.atomic_json
    committed_originals = {}
    interrupted = False
    published = set()

    class SimulatedProcessDeath(BaseException):
        pass

    class ConcurrentPublication(engine.Workflow):
        def final_qa_duplicate(self, row, target, history):
            if getattr(self, "_production_finalizing", False) and row.get("status") == "eligible":
                if not published:
                    published.add(row["id"])
                if row["id"] in published:
                    return {"id": "other-run:published-same-contract"}
            return None

    def crash_at_receipt_commit(destination, value):
        nonlocal interrupted
        is_receipt = destination.name == "receipt.json"
        if is_receipt and "files" not in value:
            for name, sha256 in value["sha256"].items():
                committed_originals[destination.parent / name] = sha256
        is_selected_commit = (is_receipt and "files" in value and value["round"] == round_number
                              and not interrupted)
        if is_selected_commit and commit_side == "before":
            interrupted = True
            raise SimulatedProcessDeath()
        original_atomic_json(destination, value)
        if is_selected_commit and commit_side == "after":
            interrupted = True
            raise SimulatedProcessDeath()

    monkeypatch.setattr(production_engine, "atomic_json", crash_at_receipt_commit)
    generator, reviewer = Generator(), Reviewer()
    with pytest.raises(SimulatedProcessDeath):
        ConcurrentPublication(output, run_id, tmp_path, generator=generator, jev=reviewer).execute()
    assert interrupted and generator.sft_calls == 3
    # A process death before switching the receipt leaves the old files valid.
    # After switching, the new files are complete. Original paid data is never
    # overwritten in either case, including a partially reconciled round set.
    for original, sha256 in committed_originals.items():
        assert engine.file_hash(original) == sha256
    pending_versions = list((path / "production").glob("round-*/*.reconciled-*.jsonl"))
    assert pending_versions
    restored = ConcurrentPublication(output, run_id, tmp_path, generator=generator, jev=reviewer)
    receipts = restored._production_receipts(path / "production")
    assert len(receipts) == 2
    for receipt in receipts:
        for target, name in receipt.get("files", {}).items():
            assert name.startswith(target + ".reconciled-")
            assert engine.file_hash(path / "production" / f"round-{receipt['round']:06d}" / name) == receipt["sha256"][name]
    state = restored.execute(resume_run=True)
    assert state["status"] == "completed", state.get("error")
    assert state["production"]["goals"]["sft"]["eligible"] == 3
    assert state["production"]["goals"]["sft"]["remaining"] == 0
    assert generator.sft_calls == 4  # Only the missing sample requires a new paid response.
    assert engine.verify_artifacts(path)["counts"]["sft"] == 3


@pytest.mark.parametrize("files", [
    {"sft": "../sft.jsonl"}, {"sft": "F:/outside/sft.jsonl"}, {"sft": "sft/../sft.jsonl"},
    {"sft": "sft.\\..\\outside.jsonl"}, {"sft": "dpo.jsonl"}, {"sft": "sft.unhashed.jsonl"},
    {"unknown": "sft.jsonl"}, [],
])
def test_receipt_file_selection_cannot_escape_round_or_select_wrong_target(tmp_path, files):
    from lib.io_utils import atomic_json
    output, run_id, path = create(tmp_path, goals={"sft": 1})
    workflow = engine.Workflow(output, run_id, tmp_path, generator=Generator(), jev=Reviewer())
    assert workflow.execute()["status"] == "completed"
    receipt_path = path / "production/round-000001/receipt.json"
    receipt = engine.read_json(receipt_path)
    assert "files" not in receipt  # Legacy target.jsonl receipts remain supported.
    assert workflow._production_receipts(path / "production")[0] == receipt
    receipt["files"] = files
    atomic_json(receipt_path, receipt)
    with pytest.raises(ValueError, match="production_round_integrity_error"):
        workflow._production_receipts(path / "production")


def test_reconciliation_interrupted_between_target_versions_keeps_whole_old_receipt(tmp_path, monkeypatch):
    from lib.infrastructure import workflow_production as production_engine
    output, run_id, path = create(tmp_path, goals={"sft": 3, "dpo": 2}, targets=["sft", "dpo"])
    real_hash_file = production_engine._hash_file
    interrupted = False
    published = set()
    class SimulatedProcessDeath(BaseException):
        pass
    class ConcurrentPublication(engine.Workflow):
        def final_qa_duplicate(self, row, target, history):
            if getattr(self, "_production_finalizing", False) and row.get("status") == "eligible":
                if not published:
                    published.add(row["id"])
                if row["id"] in published:
                    return {"id": "other-run:published-same-contract"}
            return None
    def fail_after_first_version_written(candidate):
        nonlocal interrupted
        if candidate.name.startswith("sft.reconciled-") and not interrupted:
            interrupted = True
            raise SimulatedProcessDeath()
        return real_hash_file(candidate)
    monkeypatch.setattr(production_engine, "_hash_file", fail_after_first_version_written)
    generator, reviewer = Generator(), Reviewer()
    with pytest.raises(SimulatedProcessDeath):
        ConcurrentPublication(output, run_id, tmp_path, generator=generator, jev=reviewer).execute()
    assert interrupted and generator.sft_calls == 3
    restored = ConcurrentPublication(output, run_id, tmp_path, generator=generator, jev=reviewer)
    receipts = restored._production_receipts(path / "production")
    assert all("files" not in receipt for receipt in receipts)
    assert list((path / "production/round-000001").glob("sft.reconciled-*.jsonl"))
    assert not list((path / "production/round-000001").glob("dpo.reconciled-*.jsonl"))
    state = restored.execute(resume_run=True)
    assert state["status"] == "completed", state.get("error")
    assert generator.sft_calls == 4
    assert engine.verify_artifacts(path)["counts"] == {"sft": 3, "dpo": 2}


def test_reconciled_selected_version_is_still_integrity_checked(tmp_path):
    output, run_id, path = create(tmp_path, goals={"sft": 3})
    class ConcurrentPublication(engine.Workflow):
        lost = False
        def final_qa_duplicate(self, row, target, history):
            if getattr(self, "_production_finalizing", False) and not self.lost and row.get("status") == "eligible":
                self.lost = True
                return {"id": "other-run:published-same-contract"}
            return None
    workflow = ConcurrentPublication(output, run_id, tmp_path, generator=Generator(), jev=Reviewer())
    assert workflow.execute()["status"] == "completed"
    receipt_path = path / "production/round-000001/receipt.json"
    receipt = engine.read_json(receipt_path)
    selected = receipt_path.parent / receipt["files"]["sft"]
    selected.write_text(selected.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="production_round_integrity_error"):
        workflow._production_receipts(path / "production")
