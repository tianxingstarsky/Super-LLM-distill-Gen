"""Persistent arithmetic uniqueness, bounded exhaustion and trusted history."""
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from copy import deepcopy
import math
import multiprocessing
import sqlite3

import pytest

from lib.domain.math_tasks import (
    GSM8K_V2_SEED_CAPACITY,
    GSM8K_V2_SEED_MULTIPLIER,
    build_gsm8k,
    canonical_arithmetic_expression,
    validate_gsm8k,
    verified_arithmetic_fingerprint,
)
from lib.infrastructure.arithmetic_registry import ArithmeticRegistry


def _persisted(sample, *, version=2):
    return {"question": sample["question"], "answer": sample["answer"],
            "arithmetic_expression": sample["_expression"], "verified_result": sample["_result"],
            "calculation_steps": sample.get("_steps", []), "generator_version": version}


def _sequence(position, *, capacity=GSM8K_V2_SEED_CAPACITY, theme="History"):
    return build_gsm8k(theme, position * GSM8K_V2_SEED_MULTIPLIER % capacity, version=2)


def _process_reserve(arguments):
    path, run_id, ordinal, theme = arguments
    return ArithmeticRegistry(path).reserve(run_id, ordinal, theme)


@pytest.mark.parametrize("left,right", [
    (" 2 + 3 ", "(3+2)"),
    ("(2 + 3) + 4", "4 + (3 + 2)"),
    ("2 * (3 * 4)", "(4 * 2) * 3"),
    ("2 * 3 + 4", "4 + 3 * 2"),
    ("(2 + 3) * 4", "4 * (3 + 2)"),
])
def test_expression_identity_normalizes_only_safe_structure(left, right):
    assert canonical_arithmetic_expression(left) == canonical_arithmetic_expression(right)


@pytest.mark.parametrize("left,right", [
    ("5 - 2", "2 - 5"),
    ("12 // 3", "3 // 12"),
    ("2 + 3", "10 // 2"),
    ("2 * (3 + 4)", "2 * 3 + 2 * 4"),
    ("(2 + 3) * 4", "2 + 3 * 4"),
])
def test_identity_does_not_claim_equal_results_or_general_algebra_are_unique(left, right):
    assert canonical_arithmetic_expression(left) != canonical_arithmetic_expression(right)


@pytest.mark.parametrize("version", [1, 2])
def test_verified_fingerprint_accepts_legacy_and_persisted_records_but_ignores_themes(version):
    first = build_gsm8k("First theme", 248, version=version)
    second = build_gsm8k("Another theme", 248, version=version)
    assert first["question"] != second["question"]
    expected = verified_arithmetic_fingerprint(first)
    assert verified_arithmetic_fingerprint(second) == expected
    assert verified_arithmetic_fingerprint(_persisted(first, version=version)) == expected


def test_verified_fingerprint_does_not_trust_caller_digest_or_broken_calculations():
    sample = build_gsm8k("Theme", 0, version=2)
    expected = verified_arithmetic_fingerprint(sample)
    sample["_registry_fingerprint"] = "forged"
    assert verified_arithmetic_fingerprint(sample) == expected
    sample["_steps"][0]["result"] += 1
    with pytest.raises(ValueError, match="invalid_arithmetic_record"):
        verified_arithmetic_fingerprint(sample)


def test_seed_permutation_covers_a_finite_period_and_varies_early_parameters():
    assert GSM8K_V2_SEED_CAPACITY == 2_866_156
    assert math.gcd(GSM8K_V2_SEED_MULTIPLIER, GSM8K_V2_SEED_CAPACITY) == 1
    for seed in (0, 1, 2, 3, 96, 8633, 200_000, GSM8K_V2_SEED_CAPACITY - 1):
        assert build_gsm8k("Theme", seed, version=2) == build_gsm8k(
            "Theme", seed + GSM8K_V2_SEED_CAPACITY, version=2)
    samples = [_sequence(position) for position in range(16)]
    assert {sample["_family"] for sample in samples} == {0, 1, 2, 3}
    assert len({(sample["_steps"][-1]["expression"]) for sample in samples}) == 16


def test_reservation_survives_restart_and_repeated_ordinal_keeps_original_theme(tmp_path):
    path = tmp_path / "arithmetic.sqlite3"
    registry = ArithmeticRegistry(path)
    original = registry.reserve("run-a", 0, "Garden")
    expected = deepcopy(original)
    original["question"] = "Caller-side changes must not change the checkpoint."
    restored = ArithmeticRegistry(path).reserve("run-a", 0, "A changed theme")
    assert restored == expected
    assert validate_gsm8k(restored)
    later = ArithmeticRegistry(path).reserve("run-b", 0, "Garden")
    assert later["_registry_fingerprint"] != restored["_registry_fingerprint"]
    assert ArithmeticRegistry(path).snapshot()["allocated"] == 2


def test_many_runs_and_themes_share_one_expression_identity_space(tmp_path):
    registry = ArithmeticRegistry(tmp_path / "arithmetic.sqlite3")
    samples = [registry.reserve(f"run-{index // 10}", index % 10, f"Theme {index}")
               for index in range(120)]
    assert all(validate_gsm8k(sample) for sample in samples)
    assert len({verified_arithmetic_fingerprint(sample) for sample in samples}) == len(samples)
    state = registry.snapshot()
    assert state["allocated"] == state["registered"] == len(samples)
    assert state["cursor"] >= len(samples)
    assert state["exhausted"] is False


def test_concurrent_threads_resume_one_slot_and_allocate_distinct_other_slots(tmp_path):
    path = tmp_path / "arithmetic.sqlite3"
    ArithmeticRegistry(path)
    with ThreadPoolExecutor(max_workers=8) as workers:
        same = list(workers.map(_process_reserve, [(path, "same-run", 0, f"Theme {index}")
                                                  for index in range(12)]))
        others = list(workers.map(_process_reserve, [(path, f"run-{index}", 0, "Same theme")
                                                    for index in range(12)]))
    assert all(sample == same[0] for sample in same)
    assert len({sample["_registry_fingerprint"] for sample in [same[0], *others]}) == 13
    assert ArithmeticRegistry(path).snapshot()["allocated"] == 13


def test_separate_processes_share_durable_unique_constraints(tmp_path):
    path = tmp_path / "arithmetic.sqlite3"
    ArithmeticRegistry(path)
    requests = [(str(path), f"run-{index}", 0, f"Theme {index}") for index in range(8)]
    requests.extend((str(path), "same-run", 1, f"Other theme {index}") for index in range(4))
    with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as workers:
        samples = list(workers.map(_process_reserve, requests))
    assert len({row["_registry_fingerprint"] for row in samples[:8]}) == 8
    assert all(row == samples[8] for row in samples[8:])
    assert samples[8]["_registry_fingerprint"] not in {row["_registry_fingerprint"] for row in samples[:8]}
    assert ArithmeticRegistry(path).snapshot()["allocated"] == 9


def test_history_import_is_validated_audited_and_idempotent_across_themes(tmp_path):
    path = tmp_path / "arithmetic.sqlite3"
    registry = ArithmeticRegistry(path)
    prior = [_sequence(index) for index in range(4)]
    broken = _persisted(prior[0])
    broken["verified_result"] += 1
    result = registry.seed_verified([prior[0], _persisted(prior[1]), broken, None], source="old-run/checkpoint")
    assert result == {"added": 2, "duplicate": 0, "invalid": 2}
    assert registry.seed_verified(prior[:2], source="old-run/checkpoint") == {
        "added": 0, "duplicate": 2, "invalid": 0}
    assert registry.seed_verified(prior[:2], source="old-run/export") == {
        "added": 0, "duplicate": 2, "invalid": 0}
    current = ArithmeticRegistry(path).reserve("new-run", 0, "Completely different theme")
    assert current["_registry_fingerprint"] == verified_arithmetic_fingerprint(prior[2])
    snapshot = registry.snapshot()
    assert snapshot["registered"] == 3
    assert snapshot["history_identities"] == 4
    assert snapshot["allocated"] == 1


def test_history_swap_of_multiplication_operands_blocks_same_structure(tmp_path):
    registry = ArithmeticRegistry(tmp_path / "arithmetic.sqlite3")
    future = _sequence(1)
    old = deepcopy(future)
    # Only change commutative operand order, in every checked representation.
    left, right = old["_steps"][0]["expression"].split(" * ")
    before, after = f"{left} * {right}", f"{right} * {left}"
    old["_expression"] = old["_expression"].replace(before, after)
    old["answer"] = old["answer"].replace(before, after)
    for step in old["_steps"]:
        step["expression"] = step["expression"].replace(before, after)
    assert validate_gsm8k(old)
    assert registry.seed_verified([old], source="legacy/commuted") == {
        "added": 1, "duplicate": 0, "invalid": 0}
    first = registry.reserve("new", 0)
    second = registry.reserve("new", 1)
    assert first["_registry_fingerprint"] == verified_arithmetic_fingerprint(_sequence(0))
    assert second["_registry_fingerprint"] == verified_arithmetic_fingerprint(_sequence(2))


def test_finite_exhaustion_does_not_recycle_and_still_resumes_reserved_slots(tmp_path, monkeypatch):
    from lib.infrastructure import arithmetic_registry as module

    monkeypatch.setattr(module, "GSM8K_V2_SEED_CAPACITY", 8)
    registry = ArithmeticRegistry(tmp_path / "arithmetic.sqlite3")
    original = registry.reserve("first", 0, "Original theme")
    registry.seed_verified([_sequence(index, capacity=8) for index in range(8)], source="complete-history")
    assert registry.reserve("second", 0, "Another theme") is None
    assert registry.reserve("third", 0, "Still another theme") is None
    assert registry.reserve("first", 0, "Changed theme") == original
    state = registry.snapshot()
    assert state["cursor"] == state["capacity"] == 8
    assert state["exhausted"] is True
    assert state["allocated"] == 1


def test_duplicate_scanning_commits_bounded_progress_and_cancel_preserves_prior_chunks(tmp_path):
    registry = ArithmeticRegistry(tmp_path / "arithmetic.sqlite3")
    registry.seed_verified((_sequence(index) for index in range(300)), source="history")
    checks = 0

    def cancel():
        nonlocal checks
        checks += 1
        if checks == 270:
            raise InterruptedError("Cancelled by user")

    with pytest.raises(InterruptedError, match="Cancelled by user"):
        registry.reserve("new", 0, check_cancel=cancel)
    state = registry.snapshot()
    assert state["cursor"] == 256
    assert state["allocated"] == 0
    resumed = registry.reserve("new", 0)
    assert resumed is not None
    assert resumed["_registry_fingerprint"] not in {
        verified_arithmetic_fingerprint(_sequence(index)) for index in range(300)}


def test_cancellation_rolls_back_a_new_reservation_before_it_commits(tmp_path):
    registry = ArithmeticRegistry(tmp_path / "arithmetic.sqlite3")
    checks = 0

    def cancel():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise InterruptedError("Stop")

    with pytest.raises(InterruptedError):
        registry.reserve("run", 0, check_cancel=cancel)
    assert registry.snapshot()["cursor"] == registry.snapshot()["allocated"] == 0
    assert registry.reserve("run", 0)["_registry_seed"] == 0


def test_history_read_failure_is_not_treated_as_success_and_can_be_rescanned(tmp_path):
    registry = ArithmeticRegistry(tmp_path / "arithmetic.sqlite3")

    def broken_history():
        yield from (_sequence(index) for index in range(256))
        raise OSError("Unreadable history")

    with pytest.raises(OSError, match="Unreadable history"):
        registry.seed_verified(broken_history(), source="broken-file")
    prior = registry.snapshot()["registered"]
    assert prior > 0
    rescanned = registry.seed_verified((_sequence(index) for index in range(256)), source="broken-file")
    assert rescanned == {"added": 0, "duplicate": 256, "invalid": 0}
    assert registry.snapshot()["registered"] == prior


def test_corrupt_saved_payload_is_rejected_instead_of_regenerated(tmp_path):
    path = tmp_path / "arithmetic.sqlite3"
    registry = ArithmeticRegistry(path)
    registry.reserve("run", 0)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE arithmetic_allocations SET payload='{}' WHERE run_id='run'")
    with pytest.raises(ValueError, match="arithmetic_registry_integrity_error"):
        ArithmeticRegistry(path).reserve("run", 0)
    assert registry.snapshot()["allocated"] == 1


def test_incompatible_registry_metadata_is_not_reset(tmp_path):
    path = tmp_path / "arithmetic.sqlite3"
    registry = ArithmeticRegistry(path)
    registry.reserve("run", 0)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE arithmetic_state SET schema_version=999")
    with pytest.raises(ValueError, match="arithmetic_registry_integrity_error"):
        ArithmeticRegistry(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM arithmetic_allocations").fetchone()[0] == 1


@pytest.mark.parametrize("damage", ["empty", "missing_table", "missing_state", "not_sqlite"])
def test_existing_broken_registry_is_never_silently_recreated(tmp_path, damage):
    path = tmp_path / "arithmetic.sqlite3"
    registry = ArithmeticRegistry(path)
    registry.reserve("run", 0)
    if damage == "empty":
        path.write_bytes(b"")
    elif damage == "not_sqlite":
        path.write_bytes(b"not a database")
    else:
        with sqlite3.connect(path) as db:
            db.execute("DROP TABLE arithmetic_allocations" if damage == "missing_table" else
                       "DELETE FROM arithmetic_state")
    with pytest.raises(ValueError, match="arithmetic_registry_integrity_error"):
        ArithmeticRegistry(path)


def test_snapshot_uses_transactional_counters_without_scanning_identity_tables(tmp_path, monkeypatch):
    registry = ArithmeticRegistry(tmp_path / "arithmetic.sqlite3")
    registry.seed_verified([_sequence(0), _sequence(0)], source="old")
    registry.reserve("run", 0)
    queries = []
    connect = registry._connect

    def traced():
        db = connect()
        db.set_trace_callback(queries.append)
        return db

    monkeypatch.setattr(registry, "_connect", traced)
    state = registry.snapshot()
    assert state["allocated"] == 1 and state["registered"] == 2 and state["history_identities"] == 1
    assert not any("COUNT(" in query.upper() for query in queries)


@pytest.mark.parametrize("run_id,ordinal", [("", 0), (None, 0), ("run", -1), ("run", True), ("run", 2**63)])
def test_invalid_resume_keys_never_consume_a_problem(tmp_path, run_id, ordinal):
    registry = ArithmeticRegistry(tmp_path / "arithmetic.sqlite3")
    with pytest.raises(ValueError):
        registry.reserve(run_id, ordinal)
    assert registry.snapshot()["cursor"] == 0
