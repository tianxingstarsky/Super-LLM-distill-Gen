"""Stream historical arithmetic into the persistent local allocation ledger."""
from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from filelock import FileLock

from lib.domain.workflow_quality import canonical
from lib.infrastructure.arithmetic_registry import ArithmeticRegistry
from lib.infrastructure.json_stream import iter_json_records
from lib.infrastructure.workflow_row_checkpoint import row_checkpoint
from lib.io_utils import atomic_json


_MAX_RECORD_CHARS = 1_048_576


def _digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _run_metadata(run):
    result = []
    for name in ("recipe.json", "state.json"):
        path = run / name
        if path.is_symlink() or path.stat().st_size > 8 * _MAX_RECORD_CHARS:
            raise ValueError("arithmetic_history_invalid")
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError("arithmetic_history_invalid")
        result.append(value)
    recipe, state = result
    if (not isinstance(recipe.get("targets"), list) or not recipe["targets"]
            or any(not isinstance(target, str) for target in recipe["targets"])
            or state.get("recipe_hash") != _digest(recipe)):
        raise ValueError("arithmetic_history_invalid")
    return result


def _runs(output):
    runs = Path(output) / "workflows"
    if not runs.exists():
        return
    for run in runs.iterdir():
        if not re.fullmatch(r"[a-f0-9]{32}", run.name) or not run.is_dir():
            continue
        if run.is_symlink():
            raise ValueError("arithmetic_history_invalid")
        if not all((run / name).is_file() for name in ("recipe.json", "state.json")):
            if any(_history_files(run)):
                raise ValueError("arithmetic_history_invalid")
            continue  # A concurrent create_run has not committed its recipe yet.
        yield run


def _history_files(run):
    for relative in ("stage-results/gsm8k.jsonl", "artifacts/gsm8k.records.json",
                     "production-checkpoints.sqlite3"):
        path = run / relative
        if path.exists():
            yield path
    checkpoints = run / "checkpoints" / "gsm8k"
    if checkpoints.exists():
        for path in checkpoints.iterdir():
            if re.fullmatch(r"[a-f0-9]{64}\.json", path.name):
                yield path
    rounds = run / "production"
    if rounds.exists():
        for round_path in rounds.iterdir():
            if not re.fullmatch(r"round-\d+", round_path.name) or not round_path.is_dir():
                continue
            for path in round_path.iterdir():
                if path.name == "gsm8k.jsonl" or re.fullmatch(r"gsm8k\.[\w-]+\.jsonl", path.name):
                    yield path
            records = round_path / "artifacts" / "gsm8k.records.json"
            if records.exists():
                yield records


def _run_signature(run):
    """Normal workflow writes update state and directory metadata after progress."""
    result = []
    for relative in ("recipe.json", "state.json", "checkpoints/gsm8k", "production", "stage-results/gsm8k.jsonl",
                     "artifacts/gsm8k.records.json", "production-checkpoints.sqlite3", "production-checkpoints.sqlite3-wal"):
        path = run / relative
        if path.exists():
            stat = path.stat()
            result.append((relative, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        else:
            result.append((relative, None))
    return canonical(result)


def _has_generated_math(run):
    for path in _history_files(run):
        if path.suffix != ".sqlite3":
            if path.stat().st_size:
                return True
        else:
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                if db.execute("SELECT 1 FROM checkpoints WHERE stage='gsm8k' LIMIT 1").fetchone():
                    return True
    return False


def _signature(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("arithmetic_history_invalid")
    paths = [path]
    if path.suffix == ".sqlite3":
        # SQLite commits can live in WAL before the main database is updated.
        paths.append(path.with_name(path.name + "-wal"))
    signature = []
    for item in paths:
        if item.exists():
            stat = item.stat()
            signature.append((item.name, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        else:
            signature.append((item.name, None))
    return canonical(signature)


def _candidate_rows(rows):
    for row in rows:
        # Failed operations without any generated question are not old problems.
        if (isinstance(row, dict) and row.get("status") in {"quarantined", "skipped"}
                and not any(key in row for key in ("question", "answer", "arithmetic_expression", "_expression"))):
            continue
        yield row


def _history_records(path):
    if path.suffix == ".jsonl":
        with path.open(encoding="utf-8") as handle:
            while line := handle.readline(_MAX_RECORD_CHARS + 1):
                if len(line) > _MAX_RECORD_CHARS:
                    raise ValueError("arithmetic_history_invalid")
                if line.strip():
                    yield json.loads(line)
        return
    if path.name.endswith(".records.json"):
        yield from iter_json_records(path, max_record_chars=_MAX_RECORD_CHARS)
        return
    if path.suffix == ".sqlite3":
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)) as db:
            db.execute("PRAGMA query_only=ON")
            for payload, checksum in db.execute("SELECT payload, sha256 FROM checkpoints WHERE stage='gsm8k'"):
                if (not isinstance(payload, str) or len(payload) > _MAX_RECORD_CHARS
                        or hashlib.sha256(payload.encode("utf-8")).hexdigest() != checksum):
                    raise ValueError("arithmetic_history_invalid")
                data = json.loads(payload)
                if not isinstance(data, list):
                    raise ValueError("arithmetic_history_invalid")
                yield from data
        return
    if path.stat().st_size > _MAX_RECORD_CHARS:
        raise ValueError("arithmetic_history_invalid")
    with path.open(encoding="utf-8") as handle:
        checkpoint = json.load(handle)
    if (not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("data"), list)
            or _digest(checkpoint["data"]) != checkpoint.get("sha256")):
        raise ValueError("arithmetic_history_invalid")
    yield from checkpoint["data"]


def import_arithmetic_history(output, registry, *, check_cancel=None):
    """Revisit file metadata, but read unchanged datasets only once per ledger."""
    totals = {"files": 0, "cached_files": 0, "added": 0, "duplicate": 0, "invalid": 0}
    with FileLock(str(Path(output) / ".arithmetic-history.lock"), timeout=30):
        try:
            for run in _runs(output):
                if check_cancel is not None:
                    check_cancel()
                recipe, state = _run_metadata(run)
                if "gsm8k" not in recipe.get("targets", ()) or recipe.get("arithmetic_registry_version") == 1:
                    continue
                if state.get("status") == "running":
                    raise ValueError("arithmetic_legacy_run_active")
                run_source, run_before = "run:" + str(run.resolve()), _run_signature(run)
                if registry.history_file_seen(run_source, run_before):
                    totals["cached_files"] += 1
                    continue
                for path in _history_files(run):
                    if check_cancel is not None:
                        check_cancel()
                    source, before = str(path.resolve()), _signature(path)
                    if registry.history_file_seen(source, before):
                        totals["cached_files"] += 1
                        continue
                    result = registry.seed_verified(_candidate_rows(_history_records(path)), source=source,
                                                    check_cancel=check_cancel)
                    for key in ("added", "duplicate", "invalid"):
                        totals[key] += result[key]
                    if result["invalid"]:
                        raise ValueError("arithmetic_history_invalid")
                    if _signature(path) != before:
                        raise ValueError("arithmetic_history_changed")
                    registry.mark_history_file(source, before)
                    totals["files"] += 1
                if _run_signature(run) != run_before:
                    raise ValueError("arithmetic_history_changed")
                registry.mark_history_file(run_source, run_before)
        except (OSError, sqlite3.Error, UnicodeError, ValueError) as error:
            if isinstance(error, ValueError) and str(error) in {"arithmetic_history_changed", "arithmetic_history_invalid",
                                                               "arithmetic_legacy_run_active"}:
                raise
            raise ValueError("arithmetic_history_invalid") from error
    return totals


def registry_for(workflow):
    registry = getattr(workflow, "_arithmetic_registry", None)
    if registry is None:
        output = workflow.path.parent.parent
        path = output / "arithmetic-registry.sqlite3"
        marker = output / ".arithmetic-registry-required.json"
        with FileLock(str(output / ".arithmetic-history.lock"), timeout=30), FileLock(
                str(output / ".arithmetic-initialize.lock"), timeout=30):
            # Do not activate migration while a legacy task is still running:
            # the marker would otherwise block its next unfinished batch.
            for run in _runs(output):
                recipe, state = _run_metadata(run)
                if ("gsm8k" in recipe.get("targets", ()) and recipe.get("arithmetic_registry_version") != 1
                        and state.get("status") == "running"):
                    raise ValueError("arithmetic_legacy_run_active")
            if not path.exists():
                if marker.exists():
                    raise ValueError("arithmetic_registry_missing")
                for run in _runs(output):
                    recipe, state = _run_metadata(run)
                    if recipe.get("arithmetic_registry_version") == 1 and (
                            state.get("arithmetic_registry") or state.get("stages", {}).get("gsm8k", {}).get("done", 0)
                            or _has_generated_math(run)):
                        raise ValueError("arithmetic_registry_missing")
            registry = ArithmeticRegistry(path)
            atomic_json(marker, {"version": 1, "scope": "persistent_local_output_root"})
        history = import_arithmetic_history(output, registry, check_cancel=workflow.check_cancel)
        workflow._arithmetic_registry = registry
        workflow.state["arithmetic_registry"] = {**registry.snapshot(), "history_import": history,
                                                 "scope": "persistent_local_output_root"}
    return registry


@contextmanager
def legacy_arithmetic_guard(workflow, items):
    """Do not mix unregistered legacy generation with the new persistent ledger."""
    if workflow.recipe.get("arithmetic_registry_version") == 1:
        yield
        return
    output = workflow.path.parent.parent
    with FileLock(str(output / ".arithmetic-history.lock"), timeout=30):
        if (output / "arithmetic-registry.sqlite3").exists() or (output / ".arithmetic-registry-required.json").exists():
            if any(not workflow.checkpoint_has(["item", index, _digest(item)], stage="gsm8k")
                   for index, item in enumerate(items)):
                raise ValueError("arithmetic_legacy_run_requires_migration")
        yield


def registered_arithmetic_inputs(workflow, path, count, offset):
    registry = registry_for(workflow)
    run_id = workflow.state["id"]
    theme = workflow.recipe["brief"] or "开放数学推理"
    source_id = _digest(["persistent_arithmetic_registry", run_id])

    def allocate():
        for ordinal in range(offset, offset + count):
            sample = registry.reserve(run_id, ordinal, theme, check_cancel=workflow.check_cancel)
            if sample is None:
                break
            yield {"id": _digest(["persistent_arithmetic_registry", run_id, ordinal]), "source_id": source_id,
                   "kind": "arithmetic_seed", "status": "ready", "synthetic": True,
                   "arithmetic_ordinal": ordinal, "arithmetic_fingerprint": sample["_registry_fingerprint"]}

    rows = row_checkpoint(path, allocate, workflow.check_cancel)
    summary = workflow.state.setdefault("arithmetic_registry", {})
    summary.update(registry.snapshot(), requested=count, allocated_in_batch=len(rows),
                   stop_reason="arithmetic_space_exhausted" if len(rows) < count else None)
    workflow.save()
    return rows


def registered_arithmetic_sample(workflow, item):
    ordinal = item.get("arithmetic_ordinal")
    if type(ordinal) is not int or ordinal < 0:
        raise ValueError("arithmetic_registry_integrity_error")
    sample = registry_for(workflow).reserve(workflow.state["id"], ordinal, workflow.recipe["brief"] or "开放数学推理",
                                          check_cancel=workflow.check_cancel)
    if sample is None or sample["_registry_fingerprint"] != item.get("arithmetic_fingerprint"):
        raise ValueError("arithmetic_registry_integrity_error")
    return sample


def clean_arithmetic_exhaustion(workflow):
    return (workflow.recipe.get("arithmetic_registry_version") == 1
            and workflow.state.get("arithmetic_registry", {}).get("stop_reason") == "arithmetic_space_exhausted"
            and not workflow.state.get("stages", {}).get("gsm8k", {}).get("quarantined", 0)
            and not any(workflow.state.get("quality", {}).get("targets", {}).get("gsm8k", {}).get("reasons", {}).values()))


def arithmetic_completion_status(workflow, config, counts, targets, **options):
    from lib.domain.workflow_production import production_completion_status

    if clean_arithmetic_exhaustion(workflow):
        targets = [target for target in targets if target != "gsm8k"]
        config = {**config, "goals": {target: goal for target, goal in config["goals"].items() if target != "gsm8k"}}
    return production_completion_status(config, counts, targets, **options)
