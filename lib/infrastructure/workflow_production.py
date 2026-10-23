"""Finite, incremental delivery of accepted samples with durable round receipts."""
from __future__ import annotations

from contextlib import ExitStack, closing
from collections import deque
from copy import deepcopy
import hashlib
import json
import os
from itertools import islice
from pathlib import Path
import sqlite3
from uuid import uuid4

from lib.contexts.datasets.infrastructure.corpus_index import CorpusNearDuplicateIndex
from lib.domain.open_task_plan import MAX_TASK_CHARS, task_identity, task_plan_issue, validate_short_task_plan
from lib.domain.workflow_production import (production_batch_size, validate_production,
    quality_first_production, soft_expectation_production, production_completion_status,
    evaluated_quality_stop)
from lib.domain.workflow_quality import canonical
from lib.domain.workflow_reasoning_route import cot_updates_sft
from lib.domain.workflow_package_review import package_review_stage
from lib.domain.workflow_scale import PLAN_BATCH_SIZE, generation_variant
from lib.domain.workflow_targets import PREFERENCE_TARGETS, STAGES, TARGETS, rlaif_feedback_issue
from lib.infrastructure.json_stream import iter_json_records
from lib.infrastructure.workflow_arithmetic import arithmetic_completion_status
from lib.infrastructure.planning_identities import PlanningIdentities
from lib.infrastructure.workflow_rows import RowSpool, WorkflowRows, write_json_array, write_jsonl
from lib.io_utils import atomic_json
from lib.model_request_reliability import classify_request_error


def _digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _hash_file(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _quality_assessment_valid(row):
    """Only an actual quality outcome may contribute to saturation evidence.

    Parse/provider errors are operational failures. Older records sometimes
    conflated failed shape repairs with quality rejection; remain conservative
    when they do not carry an explicit verdict or failure classification.
    """
    if (row.get("request_error") or row.get("generation_failure_kind") == "invalid_output"
            or row.get("reason") in {"invalid_model_result", "request_retries_exhausted",
                                     "qa_director_plan_invalid_after_repair"}):
        return False
    for name in ("review_repair", "package_review"):
        if isinstance(row.get(name), dict) and row[name].get("error"):
            return False
    if row.get("status") in {"eligible", "duplicate", "surplus"}:
        return True
    if row.get("reason") in {"source_exhausted", "no_new_grounded_scenario"}:
        return True
    if row.get("generation_failure_kind") == "quality":
        return True
    review = row.get("review_repair")
    checks = [row.get("judge"), row.get("checks"),
              review.get("verdict") if isinstance(review, dict) else None]
    from lib.domain.workflow_quality import verdict
    for check in checks:
        for value in check if isinstance(check, list) else [check]:
            try:
                verdict(value)
            except ValueError:
                continue
            return True
    return False


def _production_collection_file(directory, receipt, target):
    """A receipt selects one immutable file; legacy receipts select target.jsonl."""
    files = receipt.get("files", {})
    if not isinstance(files, dict) or set(files) - set(receipt["rows"]):
        raise ValueError("production_round_integrity_error")
    name = files.get(target, f"{target}.jsonl")
    if (not isinstance(name, str) or Path(name).name != name or
            not name.startswith(target + ".") or not name.endswith(".jsonl") or name not in receipt["sha256"]):
        raise ValueError("production_round_integrity_error")
    return directory / f"round-{receipt['round']:06d}" / name


class ProductionInputs:
    """Random-access source units on disk; source text is never replicated per round."""
    def __init__(self, path, units):
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA cache_size=-2048")
        self.connection.executescript("DROP TABLE IF EXISTS units; DROP TABLE IF EXISTS documents; "
            "CREATE TABLE units (idx INTEGER PRIMARY KEY, payload TEXT); "
            "CREATE TABLE documents (idx INTEGER PRIMARY KEY, unit_idx INTEGER);")
        self.count = self.documents = 0
        for unit in units:
            self.connection.execute("INSERT INTO units VALUES (?, ?)", (self.count, canonical(unit)))
            if unit.get("kind") == "document":
                self.connection.execute("INSERT INTO documents VALUES (?, ?)", (self.documents, self.count))
                self.documents += 1
            self.count += 1
        self.connection.commit()

    def close(self):
        self.connection.close()

    def rows(self, offset, count, *, expand):
        stop = offset + count if expand and self.documents else min(offset + count, self.count)
        for index in range(offset, stop):
            if index < self.count:
                row = self.connection.execute("SELECT payload FROM units WHERE idx=?", (index,)).fetchone()
                yield json.loads(row[0])
            else:
                number = (index - self.count) % self.documents
                row = self.connection.execute("SELECT payload FROM units JOIN documents ON units.idx=documents.unit_idx "
                                              "WHERE documents.idx=?", (number,)).fetchone()
                yield generation_variant(json.loads(row[0]), index, self.documents)


class ProductionCollections:
    def __init__(self, directory, receipts, target):
        self.directory, self.receipts, self.target = directory, receipts, target
        self.count = sum(item["rows"].get(target, 0) for item in receipts)

    def __len__(self):
        return self.count

    def __iter__(self):
        for receipt in self.receipts:
            if self.target in receipt["rows"]:
                path = _production_collection_file(self.directory, receipt, self.target)
                yield from WorkflowRows(path, receipt["rows"][self.target])


class WorkflowProduction:
    def production_enabled(self):
        return self.recipe.get("version", 0) >= 12 and self.recipe.get("production") is not None

    @staticmethod
    def _production_stop_signals(config, receipt, production, low_rounds, director_rounds,
                                 *, may_stop_expansion, directed_targets):
        """Rebuild decisions solely from committed receipts, including resume."""
        evaluated = evaluated_quality_stop(config)
        consecutive = max(2, config["low_acceptance_rounds"]) if evaluated else config["low_acceptance_rounds"]
        saturation = receipt.get("director_saturation", {})
        for target in config["goals"]:
            tried = receipt["attempts"].get(target, 0)
            valid = True
            if evaluated:
                assessment = receipt.get("quality_assessments", {}).get(target, {})
                valid = bool(assessment.get("valid_for_saturation"))
                tried = assessment.get("assessed", 0)
            low_rounds[target] = (low_rounds[target] + 1 if valid and tried and
                receipt["eligible"].get(target, 0) / tried < config["min_acceptance_rate"] else 0)
            if (may_stop_expansion and low_rounds[target] >= consecutive
                    and production["goals"][target]["remaining"]):
                production["goals"][target]["stop_reason"] = (
                    "diminishing_returns" if soft_expectation_production(config) else "low_acceptance_rate")
            if evaluated:
                director_rounds[target] = (director_rounds[target] + 1 if target in directed_targets
                    and saturation.get("valid_assessment") and saturation.get("fraction", 0) >= .8 else 0)
                exhausted = may_stop_expansion and director_rounds[target] >= consecutive
            else:
                exhausted = saturation.get("stop_expansion")
            if (soft_expectation_production(config) and target in directed_targets
                    and production["goals"][target]["remaining"] and exhausted):
                production["goals"][target]["stop_reason"] = "director_saturation"

    def run_item_safely(self, stage, unit, action):
        if not self.production_enabled():
            return action(unit)
        attempts = self.recipe["production"]["item_retries"] + 1
        for attempt in range(attempts):
            self.check_cancel()
            try:
                return action(unit)
            except Exception as error:
                # Cancellation is control flow, never an isolated candidate failure.
                if type(error).__name__ == "Cancelled":
                    raise
                # ChatClient already exhausted its bounded transport retries.
                # A provider failure is not a quality verdict on this source;
                # stop this run with checkpoints intact rather than retrying
                # again or quarantining every remaining candidate.
                from lib.model_request_reliability import ModelRequestError
                if isinstance(error, ModelRequestError):
                    raise
                failure = classify_request_error(error)
                if failure["kind"] == "fatal":
                    raise
                if failure["kind"] == "transient" and attempt + 1 < attempts:
                    self.event("item_retry", error=failure["code"], retry=attempt + 1)
                    continue
                if failure["kind"] not in {"transient", "invalid"}:
                    raise
                if stage == "review":
                    # Preserve the candidate and target identity when a review
                    # response is malformed; it is not an input source record.
                    from lib.domain.review_repair import validate_review_repair
                    try:
                        candidate_hash = _digest(self._review_payload(unit["target"], unit["record"]))
                    except (KeyError, TypeError, ValueError):
                        candidate_hash = None
                    return [{**deepcopy(unit["record"]), "_review_target": unit["target"],
                             "status": "quarantined", "reason": "invalid_model_result",
                             "request_error": failure["code"], "request_attempts": attempt + 1,
                             "review_repair": {"mode": self.recipe["review_repair"]["mode"],
                                               "status": "rejected", "score": None,
                                               "attempts": 0, "history": [], "error": failure["code"],
                                               "candidate_sha256": candidate_hash,
                                               "config_sha256": _digest(validate_review_repair(self.recipe["review_repair"]))}}]
                row = unit.get("sample", unit)
                rejected = {**self.rejected(row, "request_retries_exhausted" if failure["kind"] == "transient"
                                           else "invalid_model_result"),
                            "request_error": failure["code"], "request_attempts": attempt + 1}
                if stage == "trim":
                    rejected["trim_target"] = unit["target"]
                if stage in {"package", "jev"}:
                    return [{"target": unit["target"], "index": unit["index"], "status": "quarantined",
                             "package_review": {"status": "rejected", "mode": self.recipe["package_review"]["mode"],
                                 "candidate_sha256": _digest(unit["payload"]), "error": failure["code"]}}]
                return [rejected]

    def _production_plan(self, count, offset, directory, seen):
        if soft_expectation_production(self.recipe["production"]):
            return self._production_plan_soft(count, offset, directory, seen)
        self.stage = "ingest"
        metrics = self.state["stages"]["ingest"]
        metrics.update(status="running", phase="planning", done=0, total=count, outputs=0,
                       eligible=0, quarantined=0, cached=0, batch_size=PLAN_BATCH_SIZE,
                       concurrency=1, batches_done=0, batches_total=(count + PLAN_BATCH_SIZE - 1) // PLAN_BATCH_SIZE)
        self.event("stage_started")
        path = directory / "planned.jsonl"
        recent = deque(maxlen=10)
        with path.open("w", encoding="utf-8") as handle:
            for start in range(0, count, PLAN_BATCH_SIZE):
                self.check_cancel()
                size = min(PLAN_BATCH_SIZE, count - start)
                base = ["production_plan", offset + start]
                feedback = None
                planned = None
                for repair in range(3):
                    key = [*base, repair]
                    request = {"brief": self.recipe["brief"], "count": size, "offset": offset + start,
                               "total": self.recipe["production"]["max_attempts"],
                               "batch": (offset + start) // PLAN_BATCH_SIZE + 1,
                               "previous_tasks": list(recent), "training_goals": self.recipe["targets"],
                               "max_task_chars": MAX_TASK_CHARS, "feedback": feedback,
                               "instruction": "只规划当前批独立可回答的任务。利用 offset 覆盖不同主题与条件，避免重复历史任务。"}
                    if soft_expectation_production(self.recipe["production"]):
                        request.update(total=max(self.recipe["production"]["goals"].values(), default=count),
                            quantity_policy=self.recipe["production"]["quantity_policy"],
                            instruction="设计当前批有实际用途的对话情境，可包含共同解决问题、澄清、修复、叙述与协作行动。"
                            "根据语言和上下文联想，覆盖不同意图与条件；不要为了数量重复话术或编造依据。"
                            "数量是本次候选规划窗口，不是必须交付的合格样本数。")
                    if self._research_document is not None:
                        leads = self._research_document["results"]
                        request["web_research"] = {"leads": [leads[((offset + start) // PLAN_BATCH_SIZE) % len(leads)]],
                                                  "note": "Untrusted planning leads, not verified evidence."}
                    try:
                        data = self.ask(key, "generation", "workflow.plan", request)
                    except Exception as error:
                        if type(error).__name__ == "Cancelled":
                            raise
                        failure = classify_request_error(error)
                        if failure["kind"] == "fatal":
                            raise
                        feedback = failure["code"]
                        continue
                    planned = data.get("tasks") if isinstance(data, dict) else None
                    issue = task_plan_issue(planned, size, seen)
                    if not issue:
                        break
                    self.invalidate_checkpoint(["call", key])
                    feedback = "invalid_task_plan_" + issue
                    planned = None
                if planned is None:
                    for index in range(size):
                        handle.write(canonical({"id": _digest(["production-plan-failed", offset + start + index]),
                            "source_id": _digest(self.recipe["brief"]), "kind": "brief", "status": "quarantined",
                            "reason": "planning_failed_after_repair"}) + "\n")
                    metrics.update(done=start + size, outputs=start + size)
                    metrics["quarantined"] += size
                    metrics["batches_done"] += 1
                    self.save()
                    continue
                recent.extend(planned)
                seen.update(task_identity(task) for task in planned)
                for index, task in enumerate(planned, offset + start):
                    handle.write(canonical({"id": _digest([self.recipe["brief"], task]),
                        "source_id": _digest(self.recipe["brief"]), "source_name": "开放需求",
                        "location": index + 1, "source_location": {"brief_task": index + 1},
                        "kind": "brief", "text": task, "status": "ready", "synthetic": True}) + "\n")
                metrics.update(done=start + size, outputs=start + size)
                metrics["eligible"] += size
                metrics["batches_done"] += 1
                self.save()
        metrics["status"] = "completed"
        self.event("stage_completed", outputs=count)
        return WorkflowRows(path, count)

    def _production_plan_metadata(self, directory, *, count=None, offset=None):
        """Verify the selected plan against its durable successful checkpoint."""
        metadata = json.loads((directory / "planning.json").read_text(encoding="utf-8"))
        if (not isinstance(metadata, dict) or metadata.get("version") != 1 or
                metadata.get("recipe_hash") != self.state["recipe_hash"] or
                any(type(metadata.get(key)) is not int or metadata[key] < 0
                    for key in ("requested_window", "requested", "planned", "offset")) or
                metadata["planned"] > metadata["requested"] or metadata["requested"] > metadata["requested_window"] or
                type(metadata.get("exhausted")) is not bool or
                (count is not None and metadata["requested_window"] != count) or
                (offset is not None and metadata["offset"] != offset) or
                _hash_file(directory / "planned.jsonl") != metadata.get("planned_sha256")):
            raise ValueError("production_round_integrity_error")
        key = ["production_plan_v2_result", directory.name, metadata["requested_window"], metadata["offset"]]
        cached, expected = self._production_checkpoints.get("ingest", _digest(key))
        if not cached or metadata != expected:
            raise ValueError("production_round_integrity_error")
        actual = sum(1 for _ in WorkflowRows(directory / "planned.jsonl", metadata["planned"]))
        if actual != metadata["planned"]:
            raise ValueError("production_round_integrity_error")
        return metadata

    @staticmethod
    def _production_planning_summary(metadata):
        return {key: deepcopy(metadata[key]) for key in
                ("requested", "requested_window", "planned", "offset", "exhausted", "stop_reason", "reason", "exhaustion_source")}

    def _production_plan_soft(self, count, offset, directory, seen):
        self.stage = "ingest"
        metrics = self.state["stages"]["ingest"]
        metrics.update(status="running", phase="planning", done=0, total=count, outputs=0,
            eligible=0, quarantined=0, cached=0, batch_size=PLAN_BATCH_SIZE, concurrency=1,
            batches_done=0, batches_total=(count + PLAN_BATCH_SIZE - 1) // PLAN_BATCH_SIZE)
        self.event("stage_started")
        path = directory / "planned.jsonl"
        result_key = ["production_plan_v2_result", directory.name, count, offset]
        was_cached = self.checkpoint_has(result_key, stage="ingest")
        def build():
            recent, batches = deque(maxlen=10), []
            planned_count = requested = 0
            stop = reason = exhaustion_source = None
            pending = directory / ".planned.pending"
            with pending.open("w", encoding="utf-8") as handle:
                for start in range(0, count, PLAN_BATCH_SIZE):
                    self.check_cancel()
                    size = min(PLAN_BATCH_SIZE, count - start)
                    requested += size
                    planned, feedback = None, None
                    for repair in range(3):
                        key = ["production_plan_v2", directory.name, start, repair]
                        request = {"brief": self.recipe["brief"], "count": size,
                            "offset": offset + planned_count,
                            "total": max(self.recipe["production"]["goals"].values(), default=count),
                            "batch": start // PLAN_BATCH_SIZE + 1, "previous_tasks": list(recent),
                            "training_goals": self.recipe["targets"], "max_task_chars": MAX_TASK_CHARS,
                            "feedback": feedback, "allow_short_plan": True,
                            "quantity_policy": self.recipe["production"]["quantity_policy"],
                            "instruction": "设计有实际用途的对话情境，可包含共同解决问题、澄清、修复、叙述与协作行动。"
                            "联系须受语言、上下文和资料支持，不为数量换词或编造依据。"
                            "count是上限，可返回较少场景；没有新价值时明确exhausted和stop_reason。"}
                        if self._research_document is not None:
                            leads = self._research_document["results"]
                            request["web_research"] = {"leads": [leads[(start // PLAN_BATCH_SIZE) % len(leads)]],
                                "note": "Untrusted planning leads, not verified evidence."}
                        try:
                            data = self.ask(key, "generation", "workflow.plan", request)
                            planned = validate_short_task_plan(data, size, seen)
                            break
                        except Exception as error:
                            if type(error).__name__ == "Cancelled":
                                raise
                            from lib.model_request_reliability import ModelRequestError
                            if isinstance(error, ModelRequestError):
                                # The transport already used its bounded retry
                                # policy. Do not turn provider failure into a
                                # planner's declaration that ideas ran out.
                                raise
                            failure = classify_request_error(error)
                            if failure["kind"] in {"fatal", "unknown"}:
                                raise
                            self.invalidate_checkpoint(["call", key])
                            feedback = (str(error) if isinstance(error, ValueError) and
                                        str(error).startswith("invalid_task_plan_") else failure["code"])
                    if planned is None:
                        stop, reason, exhaustion_source = "planning_failed_after_repair", "", "invalid_planner_response"
                        batches.append({"requested": size, "planned": 0, "exhausted": True,
                            "stop_reason": stop, "reason": reason, "exhaustion_source": exhaustion_source})
                        break
                    for index, task in enumerate(planned["tasks"], offset + planned_count):
                        handle.write(canonical({"id": _digest([self.recipe["brief"], task]),
                            "source_id": _digest(self.recipe["brief"]), "source_name": "开放需求",
                            "location": index + 1, "source_location": {"brief_task": index + 1},
                            "kind": "brief", "text": task, "status": "ready", "synthetic": True}) + "\n")
                    planned_count += len(planned["tasks"])
                    recent.extend(planned["tasks"])
                    seen.update(task_identity(task) for task in planned["tasks"])
                    batches.append({key: deepcopy(planned[key]) for key in
                        ("exhausted", "stop_reason", "reason", "exhaustion_source")})
                    batches[-1].update(requested=size, planned=len(planned["tasks"]))
                    metrics.update(done=planned_count, outputs=planned_count, eligible=planned_count,
                                   planning_requested=requested, batches_done=len(batches))
                    self.save()
                    if planned["exhausted"]:
                        stop, reason, exhaustion_source = planned["stop_reason"], planned["reason"], planned["exhaustion_source"]
                        break
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(pending, path)
            return {"version": 1, "recipe_hash": self.state["recipe_hash"], "offset": offset,
                "requested_window": count, "requested": requested, "planned": planned_count,
                "exhausted": stop is not None, "stop_reason": stop, "reason": reason or "",
                "exhaustion_source": exhaustion_source, "batches": batches, "planned_sha256": _hash_file(path)}
        metadata = self.checkpoint(result_key, build)
        metadata_path = directory / "planning.json"
        if metadata_path.exists() and json.loads(metadata_path.read_text(encoding="utf-8")) != metadata:
            raise ValueError("production_round_integrity_error")
        atomic_json(metadata_path, metadata)
        self._production_plan_metadata(directory, count=count, offset=offset)
        if was_cached:
            seen.update(task_identity(row["text"]) for row in WorkflowRows(path, metadata["planned"]))
        metrics.update(status="completed", total=metadata["planned"], done=metadata["planned"], outputs=metadata["planned"], eligible=metadata["planned"],
            cached=metadata["planned"] if was_cached else 0, batches_done=len(metadata["batches"]),
            planning_requested=metadata["requested"])
        self.save()
        self.event("production_plan_committed", planned=metadata["planned"], requested=metadata["requested"],
                   exhausted=metadata["exhausted"], reason=metadata["stop_reason"])
        self.event("stage_completed", outputs=metadata["planned"])
        return WorkflowRows(path, metadata["planned"])

    def _production_receipts(self, directory):
        receipts = []
        attempted = 0
        generation_offset = 0
        for path in sorted(directory.glob("round-*/receipt.json")):
            receipt = json.loads(path.read_text(encoding="utf-8"))
            targets = set(self.recipe["targets"])
            if (not isinstance(receipt, dict) or type(receipt.get("round")) is not int
                    or receipt["round"] != len(receipts) + 1
                    or any(not isinstance(receipt.get(key), dict) for key in ("rows", "eligible", "attempts", "sha256"))
                    or set(receipt["rows"]) != targets or set(receipt["eligible"]) != targets
                    or set(receipt["attempts"]) - targets
                    or type(receipt.get("attempted")) is not int or receipt["attempted"] < 0
                    or any(type(count) is not int or count < 0
                           for key in ("rows", "eligible", "attempts") for count in receipt[key].values())):
                raise ValueError("production_round_integrity_error")
            for target in receipt["rows"]:
                _production_collection_file(directory, receipt, target)
            for name, expected in receipt["sha256"].items():
                candidate = path.parent / name
                if Path(name).name != name or candidate.is_symlink() or _hash_file(candidate) != expected:
                    raise ValueError("production_round_integrity_error")
            generated_attempted = receipt["attempted"]
            if self.independent_arithmetic():
                generated_attempted = receipt.get("generation_attempted")
                arithmetic_attempted = receipt.get("arithmetic_attempted")
                generation_file = "planned.jsonl" if "planned.jsonl" in receipt["sha256"] else "generated.jsonl"
                if (type(generated_attempted) is not int or generated_attempted < 0
                        or type(arithmetic_attempted) is not int or arithmetic_attempted < 0
                        or receipt["attempted"] != max(generated_attempted, arithmetic_attempted)
                        or arithmetic_attempted != receipt["attempts"].get("gsm8k", 0)
                        or generation_file not in receipt["sha256"]
                        or sum(1 for _ in WorkflowRows(path.parent / generation_file, generated_attempted)) != generated_attempted
                        or "arithmetic-inputs.jsonl" not in receipt["sha256"]
                        or sum(1 for _ in WorkflowRows(path.parent / "arithmetic-inputs.jsonl", arithmetic_attempted))
                           != arithmetic_attempted):
                    raise ValueError("production_round_integrity_error")
            if "planning" in receipt:
                if "planning.json" not in receipt["sha256"] or "planned.jsonl" not in receipt["sha256"]:
                    raise ValueError("production_round_integrity_error")
                metadata = self._production_plan_metadata(path.parent,
                    offset=generation_offset if self.independent_arithmetic() else attempted)
                if (receipt["planning"] != self._production_planning_summary(metadata) or
                        generated_attempted != metadata["planned"] or
                        receipt.get("planned_ready") != metadata["planned"]):
                    raise ValueError("production_round_integrity_error")
            elif "planning.json" in receipt["sha256"]:
                raise ValueError("production_round_integrity_error")
            # Hashes protect the selected files, not the separately stored
            # counters. Rebuild counts from those files before allowing a
            # resumed run to use them for completion or saturation decisions.
            file_assessments = {}
            for target, expected_rows in receipt["rows"].items():
                rows = eligible = assessed = unassessed = 0
                selected_file = _production_collection_file(directory, receipt, target)
                for row in WorkflowRows(selected_file, expected_rows):
                    if not isinstance(row, dict):
                        raise ValueError("production_round_integrity_error")
                    rows += 1
                    eligible += int(row.get("status") == "eligible")
                    if evaluated_quality_stop(self.recipe["production"]):
                        valid = _quality_assessment_valid(row)
                        assessed += int(valid)
                        unassessed += int(not valid)
                if rows != expected_rows or eligible != receipt["eligible"][target]:
                    raise ValueError("production_round_integrity_error")
                file_assessments[target] = assessed, unassessed
            if evaluated_quality_stop(self.recipe["production"]):
                assessments = receipt.get("quality_assessments")
                if not isinstance(assessments, dict) or set(assessments) != set(receipt["attempts"]):
                    raise ValueError("production_round_integrity_error")
                for target, saved in assessments.items():
                    if (not isinstance(saved, dict) or type(saved.get("assessed")) is not int
                            or type(saved.get("unassessed")) is not int
                            or type(saved.get("valid_for_saturation")) is not bool):
                        raise ValueError("production_round_integrity_error")
                    assessed, unassessed = file_assessments[target]
                    expected = {"assessed": assessed, "unassessed": unassessed}
                    if "upstream_stage" in saved:
                        source_file = "sft.upstream-quality.jsonl"
                        if (saved["upstream_stage"] != "sft" or source_file not in receipt["sha256"]
                                or type(saved.get("upstream_assessed")) is not int
                                or type(saved.get("upstream_unassessed")) is not int):
                            raise ValueError("production_round_integrity_error")
                        blocked_assessed = blocked_unassessed = 0
                        for row in WorkflowRows(path.parent / source_file,
                                                saved["upstream_assessed"] + saved["upstream_unassessed"]):
                            valid = _quality_assessment_valid(row)
                            blocked_assessed += int(valid)
                            blocked_unassessed += int(not valid)
                        assessed += blocked_assessed
                        unassessed += blocked_unassessed
                        expected.update(upstream_stage="sft", upstream_assessed=blocked_assessed,
                                        upstream_unassessed=blocked_unassessed)
                    expected.update(assessed=assessed, unassessed=unassessed,
                                    valid_for_saturation=bool(assessed) and not unassessed)
                    if saved != expected:
                        raise ValueError("production_round_integrity_error")
            receipts.append(receipt)
            attempted += receipt["attempted"]
            generation_offset += generated_attempted
        return receipts

    def _production_state(self, config, receipts):
        counts = {target: sum(item["eligible"].get(target, 0) for item in receipts) for target in self.recipe["targets"]}
        attempts = {target: sum(item["attempts"].get(target, 0) for item in receipts) for target in config["goals"]}
        state = {"version": 1, "round": len(receipts), "attempted": sum(item["attempted"] for item in receipts),
                 "counts": counts, "goals": {target: {"goal": goal, "eligible": counts[target],
                     "remaining": max(0, goal - counts[target]), "attempted": attempts[target], "stop_reason": None}
                     for target, goal in config["goals"].items()}, "status": "running", "stop_reason": None,
                 "budget_usd": config["budget_usd"]}
        if self._task_budget is not None:
            state["spent_usd"] = self._task_budget.spent
        if soft_expectation_production(config):
            state.update(version=2, quantity_policy=config["quantity_policy"],
                yield_history={target: [{"round": receipt["round"], "attempted": receipt["attempts"].get(target, 0),
                    "eligible": receipt["eligible"].get(target, 0)} for receipt in receipts[-config["low_acceptance_rounds"]:]]
                    for target in config["goals"]})
            evidence = [dict(receipt["director_saturation"], round=receipt["round"])
                        for receipt in receipts if receipt.get("director_saturation")]
            if evidence:
                state["director_saturation_evidence"] = evidence
            if evaluated_quality_stop(config):
                state["quality_stop_version"] = 1
                state["quality_assessment_history"] = [
                    {"round": receipt["round"], "targets": deepcopy(receipt.get("quality_assessments", {}))}
                    for receipt in receipts[-max(2, config["low_acceptance_rounds"]):]]
                state["invalid_assessment_count"] = sum(
                    item.get("unassessed", 0) for receipt in receipts
                    for item in receipt.get("quality_assessments", {}).values())
            if not self.recipe["sources"]:
                state["planning_requested"] = sum(receipt.get("planning", {}).get("requested", receipt["attempted"])
                                                   for receipt in receipts)
                exhausted = [dict(receipt["planning"], round=receipt["round"])
                             for receipt in receipts if receipt.get("planning", {}).get("exhausted")]
                if exhausted:
                    state["planning_exhaustion"] = exhausted[-1]
            state["below_expectation"] = {target: progress["remaining"] for target, progress in state["goals"].items()
                                          if progress["remaining"]}
        self.state["production"] = state
        self.save()
        return state

    def _production_report(self, report):
        """Keep the actual per-round review coverage, including rejected candidates."""
        production = deepcopy(self.state["production"])
        config = self.recipe["production"]
        soft = soft_expectation_production(config)
        production["counts"] = {target: result["eligible"] for target, result in report["targets"].items()}
        if (not soft and production["status"] in {"completed", "needs_attention"} and
                any(production["counts"][target] == 0 for target in self.recipe["targets"] if target not in production["goals"])):
            production.update(status="needs_attention", stop_reason="source_exhausted")
        for target, progress in production["goals"].items():
            progress["eligible"] = production["counts"][target]
            progress["remaining"] = max(0, progress["goal"] - progress["eligible"])
            if progress["remaining"] and progress["stop_reason"] in {"goal_reached", "expectation_reached"}:
                progress["stop_reason"] = "final_validation_loss"
                if production["status"] in {"completed", "needs_attention"}:
                    production.update(status="needs_attention", stop_reason="final_validation_loss")
        if soft:
            production["below_expectation"] = {target: progress["remaining"] for target, progress in production["goals"].items()
                                              if progress["remaining"]}
            if production["status"] in {"completed", "needs_attention"}:
                production["status"] = arithmetic_completion_status(self, config, production["counts"], self.recipe["targets"],
                    source_limited=production["stop_reason"] == "source_limit_reached",
                    attention_required=production["stop_reason"] == "planning_failed_after_repair" or
                        bool(production.get("invalid_assessment_count")))
        report["production"] = production
        if not report["package_review"].get("enabled"):
            return
        for target in self.recipe["targets"]:
            if self.skips_post_generation_review(target):
                continue
            original = [receipt["package_review"].get("targets", {}).get(target, {}) for receipt in self._production_completed_receipts]
            result = {key: sum(plan.get(key, 0) for plan in original)
                      for key in ("candidates", "planned", "reviewed", "accepted", "rejected", "unreviewed")}
            result["coverage_percent"] = round(100 * result["reviewed"] / result["candidates"], 4) if result["candidates"] else 0.
            result["status"] = "no_candidates" if not result["candidates"] else "all_reviewed" if not result["unreviewed"] else "sampled"
            escalations = [dict(plan["escalation"], round=receipt["round"])
                           for receipt, plan in zip(self._production_completed_receipts, original) if plan.get("escalation")]
            if escalations:
                result["escalation"] = escalations[0] if len(escalations) == 1 else {"escalated": True, "rounds": escalations}
            report["package_review"]["targets"][target] = result
            report["targets"][target]["package_review"] = result

    def _production_accept_row(self, target, row, payload):
        """Only accepted samples enter the across-round duplicate index."""
        if not getattr(self, "_production_collecting", False):
            return
        goal = self.recipe["production"]["goals"].get(target)
        source_preserving = bool(self.recipe["sources"]) and target in {"cpt", "agent"}
        if (goal is not None and self._production_counts[target] >= goal and
                not (source_preserving and soft_expectation_production(self.recipe["production"]))):
            row.update(status="surplus", reason="production_goal_already_met")
            return
        if target == "cpt":
            duplicate = self._production_corpus.check_and_add(row["text"], {"id": row["id"], "source_name": row.get("source_name")})
            if duplicate:
                row.update(status="duplicate", duplicate_scope="production_rounds", **duplicate)
                return
        changed = self._production_identities.execute("INSERT OR IGNORE INTO accepted VALUES (?, ?)",
                                                      (target, _digest(payload))).rowcount
        if not changed:
            row.update(status="duplicate", reason="duplicate_training_content", duplicate_scope="production_rounds")
            return
        self._production_counts[target] += 1

    def _production_round(self, generated, source_rows, active):
        self._production_blocked_sft_quality = None
        selected = set(active)
        collections = {target: [] for target in TARGETS}
        needs_sft = bool(selected & (PREFERENCE_TARGETS | {"sft", "cot"}))
        if "cpt" in selected:
            collections["cpt"] = (self.shared_cpt_collection() if self.shared_source_cleaning()
                                   else self.stage_items("cpt", source_rows if self.recipe["sources"] else generated, self.cpt))
        if "agent" in selected:
            collections["agent"] = self.stage_items("agent", source_rows, self.agent)
        directed = (self.directed_generation(generated, needs_sft=needs_sft, needs_multiturn="multiturn" in selected)
                    if self.recipe.get("qa_director", {}).get("enabled") and (needs_sft or "multiturn" in selected) else None)
        if needs_sft:
            collections["sft"] = directed["sft"] if directed is not None else self.stage_items("sft", generated, self.sft)
        if "multiturn" in selected:
            collections["multiturn"] = directed["multiturn"] if directed is not None else self.stage_items("multiturn", generated, self.multiturn)
        if selected & PREFERENCE_TARGETS:
            rows = collections["sft"].eligible(self.state["stages"]["sft"]["eligible"])
            pairs = self.stage_items("preference", rows, self.preference)
            for target in selected & PREFERENCE_TARGETS:
                if target == "rlaif":
                    def checked(row):
                        issue = rlaif_feedback_issue(row) if row["status"] == "eligible" else None
                        if issue:
                            row.update(status="quarantined", reason=issue)
                        return row
                    collections[target] = WorkflowRows(pairs.path, len(pairs), transform=checked)
                else:
                    collections[target] = pairs
        if "cot" in selected or ("sft" in selected and cot_updates_sft(self.recipe)):
            rows = collections["sft"].eligible(self.state["stages"]["sft"]["eligible"])
            collections["cot"] = self.stage_items("cot", rows, self.cot)
        if "gsm8k" in selected:
            arithmetic = getattr(self, "_production_arithmetic_inputs", generated) if self.independent_arithmetic() else generated
            from lib.infrastructure.workflow_arithmetic import legacy_arithmetic_guard
            with legacy_arithmetic_guard(self, arithmetic):
                collections["gsm8k"] = self.stage_items("gsm8k", arithmetic, self.gsm8k)
        if cot_updates_sft(self.recipe):
            self.finalize_reasoning_outputs(collections, delivery_targets=selected)
        if evaluated_quality_stop(self.recipe.get("production") or {}) and selected & (PREFERENCE_TARGETS | {"cot"}):
            blocked = RowSpool(self._production_destination.parent / "sft.upstream-quality.jsonl")
            assessed = unassessed = 0
            for row in collections["sft"]:
                if row.get("status") == "eligible":
                    continue
                evidence = {key: deepcopy(row[key]) for key in (
                    "id", "status", "reason", "request_error", "generation_failure_kind", "judge", "checks",
                    "review_repair", "package_review") if key in row}
                blocked.append(evidence)
                valid = _quality_assessment_valid(evidence)
                assessed += int(valid)
                unassessed += int(not valid)
            blocked.close()
            self._production_blocked_sft_quality = {"upstream_stage": "sft", "upstream_assessed": assessed,
                                                    "upstream_unassessed": unassessed}
        # Intermediate candidates are processed before suppressing unrequested
        # formats, including CoT required after its own delivery goal is met.
        for target in set(collections) - selected:
            collections[target] = []
        if not cot_updates_sft(self.recipe):
            self.apply_reasoning_trim(collections)
        return collections

    def _production_partial_export(self, status, reason):
        """A paused task can export committed rounds without issuing another request."""
        directory = self.path / "production"
        failed_stage = self.stage
        try:
            receipts = self._production_receipts(directory)
            if not receipts:
                return
            self._production_completed_receipts = receipts
            self._production_input_records(directory, receipts)
            self._production_collecting = False
            self._production_destination = None
            self._production_finalizing = True
            self._production_exporting_partial = True
            self.state["production"].update(status=status, stop_reason=reason, partial_export=True)
            if self._task_budget is not None:
                self.state["production"]["spent_usd"] = self._task_budget.spent
            self.stage = "package"
            self.package({target: ProductionCollections(directory, receipts, target) for target in TARGETS})
            actual = self.state["quality"]["production"]
            self.state["production"].update(counts=actual["counts"], goals=actual["goals"])
            if soft_expectation_production(self.recipe["production"]):
                self.state["production"]["below_expectation"] = actual["below_expectation"]
            self.state["stages"]["package"].update(status="completed", phase="partial_export", done=1, total=1)
        except (OSError, ValueError, sqlite3.Error):
            # Preserve the original pause cause; incomplete or corrupt data is never released.
            self.state["production"]["partial_export"] = False
        finally:
            self._production_finalizing = False
            self._production_exporting_partial = False
            self.stage = failed_stage

    def _production_input_records(self, directory, receipts):
        if self.recipe["sources"]:
            return
        def rows():
            for receipt in receipts:
                path = directory / f"round-{receipt['round']:06d}" / "planned.jsonl"
                if path.exists():
                    yield from WorkflowRows(path, receipt.get("generation_attempted", receipt["attempted"]))
        write_json_array(self.path / "input_records.json", rows())
        total = sum(receipt.get("generation_attempted", receipt["attempted"]) for receipt in receipts if "planned.jsonl" in receipt["sha256"])
        ready = sum(receipt.get("planned_ready", receipt["attempted"]) for receipt in receipts if "planned.jsonl" in receipt["sha256"])
        self.state["input_summary"].update(units=total, ready=ready, quarantined=total - ready)

    def _production_reconcile_final(self, directory, receipts):
        """Commit a complete immutable version by atomically switching its receipt.

        Unselected version files are harmless after interruption. Neither a
        partial write nor failure to publish the new receipt changes files
        referenced by the old receipt. Failure after receipt publication is
        equally safe: its selected files were flushed before that publication.
        """
        with closing(sqlite3.connect(directory / "final-validation.sqlite3")) as connection:
            connection.execute("PRAGMA cache_size=-2048")
            connection.executescript("DROP TABLE IF EXISTS final_rows; CREATE TABLE final_rows "
                                    "(target TEXT, id TEXT, payload TEXT, PRIMARY KEY(target,id));")
            for target in self.recipe["targets"]:
                for row in iter_json_records(self.path / "artifacts" / f"{target}.records.json"):
                    connection.execute("INSERT OR REPLACE INTO final_rows VALUES (?, ?, ?)",
                                       (target, row["id"], canonical(row)))
            connection.commit()
            for receipt in receipts:
                round_dir = directory / f"round-{receipt['round']:06d}"
                replacement = deepcopy(receipt)
                replacement["files"] = {target: _production_collection_file(directory, receipt, target).name
                                        for target in receipt["rows"]}
                revision = uuid4().hex
                for target in self.recipe["targets"]:
                    path = _production_collection_file(directory, receipt, target)
                    version_path = round_dir / f"{target}.reconciled-{revision}.jsonl"
                    count = 0
                    assessed = unassessed = 0
                    with version_path.open("x", encoding="utf-8") as handle:
                        for original in WorkflowRows(path, receipt["rows"][target]):
                            found = connection.execute("SELECT payload FROM final_rows WHERE target=? AND id=?",
                                                       (target, original["id"])).fetchone()
                            row = json.loads(found[0]) if found else original
                            handle.write(canonical(row) + "\n")
                            count += int(row["status"] == "eligible")
                            if target in replacement.get("quality_assessments", {}):
                                valid = _quality_assessment_valid(row)
                                assessed += int(valid)
                                unassessed += int(not valid)
                        handle.flush()
                        os.fsync(handle.fileno())
                    replacement["eligible"][target] = count
                    if target in replacement.get("quality_assessments", {}):
                        prior = replacement["quality_assessments"][target]
                        upstream = {key: prior[key] for key in (
                            "upstream_stage", "upstream_assessed", "upstream_unassessed") if key in prior}
                        assessed += upstream.get("upstream_assessed", 0)
                        unassessed += upstream.get("upstream_unassessed", 0)
                        replacement["quality_assessments"][target] = {"assessed": assessed, "unassessed": unassessed,
                            "valid_for_saturation": bool(assessed) and not unassessed, **upstream}
                    replacement["files"][target] = version_path.name
                    replacement["sha256"].pop(path.name)
                    replacement["sha256"][version_path.name] = _hash_file(version_path)
                atomic_json(round_dir / "receipt.json", replacement)
                receipt.clear()
                receipt.update(replacement)

    def _execute_production(self):
        while True:
            self._production_retry_final_loss = False
            state = self._execute_production_once()
            if not self._production_retry_final_loss:
                return state

    def _execute_production_once(self):
        config = validate_production(self.recipe["production"], self.recipe["targets"])
        quality_first = quality_first_production(config)
        soft = soft_expectation_production(config)
        evaluated_stop = evaluated_quality_stop(config)
        reached_reason = "expectation_reached" if soft else "goal_reached"
        directory = self.path / "production"
        directory.mkdir(exist_ok=True)
        receipts = self._production_receipts(directory)
        production = self._production_state(config, receipts)
        needed = {"ingest", "package"} | set(self.recipe["targets"])
        review_targets = [target for target in self.recipe["targets"]
                          if not self.skips_post_generation_review(target)]
        if self.recipe.get("review_repair") is not None and review_targets:
            needed.add("review")
        elif (review_targets and self.recipe.get("package_review", {}).get("enabled")
                and package_review_stage(self.recipe["package_review"]) == "jev"):
            needed.add("jev")
        if set(self.recipe["targets"]) & (PREFERENCE_TARGETS | {"cot"}):
            needed.add("sft")
        if set(self.recipe["targets"]) & PREFERENCE_TARGETS:
            needed.add("preference")
        if self.shared_source_cleaning():
            needed.add("cpt")
        if self.recipe.get("qa_director", {}).get("enabled"):
            needed.add("director")
        if (self.recipe.get("reasoning_trim") or {}).get("enabled"):
            needed.add("trim")
        for stage in set(self.state["stages"]) - needed:
            self.state["stages"][stage]["status"] = "skipped"
        self.stage = "ingest"
        self._research_document = None if self.arithmetic_only() else self.research()
        if self.recipe["sources"] and not self.arithmetic_only():
            units = self.stage_items("ingest", self.recipe["sources"], self.parse_source, stream_sources=True)
            from collections import Counter
            input_statuses = Counter(unit["status"] for unit in units)
            ready_count = input_statuses["ready"]
            sources = units.ready(ready_count, self.recipe["max_units"])
            self.state["input_summary"] = {"units": len(units), "ready": ready_count,
                "quarantined": input_statuses["quarantined"], "skipped": input_statuses["skipped"],
                "deferred": max(0, ready_count - len(sources)),
                "targets": self.recipe["targets"], "generation_candidates": production["attempted"]}
            write_json_array(self.path / "input_records.json", units)
            sources = self.prepare_clean_sources(sources)
        else:
            sources = []
            self.state["input_summary"] = {"units": 0, "ready": 0, "quarantined": 0, "deferred": 0,
                "targets": self.recipe["targets"], "generation_candidates": production["attempted"]}
            write_json_array(self.path / "input_records.json", [])
        if self.arithmetic_only():
            self.state["stages"]["ingest"].update(status="skipped", done=0, total=0)
        with ExitStack() as resources:
            source_index = resources.enter_context(closing(ProductionInputs(directory / "source-index.sqlite3", sources)))
            seen = resources.enter_context(closing(PlanningIdentities(directory / "planning-identities.sqlite3")))
            identities = resources.enter_context(closing(sqlite3.connect(directory / "accepted-identities.sqlite3")))
            identities.execute("PRAGMA cache_size=-2048")
            identities.executescript("DROP TABLE IF EXISTS accepted; CREATE TABLE accepted (target TEXT, hash TEXT, PRIMARY KEY(target,hash));")
            self._production_identities = identities
            self._production_corpus = resources.enter_context(closing(CorpusNearDuplicateIndex(directory / "accepted-cpt.sqlite3")))
            self._production_counts = dict(production["counts"])
            for receipt in receipts:
                for target in self.recipe["targets"]:
                    rows = ProductionCollections(directory, [receipt], target)
                    for row in rows:
                        if row["status"] == "eligible":
                            from lib.infrastructure.training_workflow import preferred_training_record
                            payload = preferred_training_record(target, row, self.recipe.get("generation_preferences"),
                                                                sft_output_style=self.recipe.get("sft_output_style"))
                            identities.execute("INSERT OR IGNORE INTO accepted VALUES (?, ?)", (target, _digest(payload)))
                            if target == "cpt":
                                self._production_corpus.add_reference(row["text"], {"id": row["id"]})
                plan = directory / f"round-{receipt['round']:06d}" / "planned.jsonl"
                if plan.exists():
                    seen.update(task_identity(unit["text"]) for unit in WorkflowRows(plan, receipt.get("generation_attempted", receipt["attempted"]))
                                if unit.get("status") == "ready")
            identities.commit()
            low_rounds = {target: 0 for target in config["goals"]}
            director_rounds = {target: 0 for target in config["goals"]}
            directed_targets = set(self.recipe["targets"]) & (PREFERENCE_TARGETS | {"sft", "cot", "multiturn"})
            covered = 0
            for receipt in receipts:
                covered += receipt.get("generation_attempted", receipt["attempted"])
                may_stop_expansion = (not soft or not self.recipe["sources"] or covered >= source_index.count)
                if evaluated_stop and self.state["input_summary"].get("deferred"):
                    may_stop_expansion = False
                self._production_stop_signals(config, receipt, production, low_rounds, director_rounds,
                    may_stop_expansion=may_stop_expansion, directed_targets=directed_targets)
            stop = None
            while True:
                self.check_cancel()
                planning_stop = production.get("planning_exhaustion", {}).get("stop_reason")
                math_progress = production["goals"].get("gsm8k", {})
                math_pending = (self.independent_arithmetic() and math_progress.get("remaining", 0)
                                and not math_progress.get("stop_reason"))
                if planning_stop and not math_pending:
                    stop = planning_stop
                    break
                if planning_stop:
                    for target, progress in production["goals"].items():
                        if target != "gsm8k" and progress["remaining"]:
                            progress["stop_reason"] = planning_stop
                active = [target for target in self.recipe["targets"] if
                    (target in config["goals"] and production["goals"][target]["remaining"] > 0
                     and not production["goals"][target]["stop_reason"])
                    or (target not in config["goals"] and not receipts)]
                if not active:
                    stop = reached_reason if all(not item["remaining"] for item in production["goals"].values()) else "source_exhausted"
                    if soft and self.recipe["sources"] and not config["goals"]:
                        stop = "source_limit_reached" if self.state["input_summary"]["deferred"] else "source_coverage_complete"
                    if stop == "source_exhausted" and any(item["stop_reason"] == "source_limit_reached"
                                                          for item in production["goals"].values()):
                        stop = "source_limit_reached"
                    break
                size = production_batch_size(config, production["counts"], production["attempted"], len(receipts),
                    planning_requested=production.get("planning_requested") if quality_first else None)
                if not size and receipts:
                    stop = ("max_attempts" if production["attempted"] >= config["max_attempts"] else
                            "max_rounds" if len(receipts) >= config["max_rounds"] else
                            "candidate_budget_reached" if quality_first else "max_rounds")
                    break
                if not size:
                    size = min(source_index.count or (self.recipe.get("sample_count") or self.recipe["tasks"]), config["round_size"])
                generation_size, generation_offset = size, production["attempted"]
                if self.independent_arithmetic():
                    # Arithmetic's large delivery window must never expand
                    # paid generation. The other targets share only their own
                    # candidate window and their own contiguous source offset.
                    generation_offset = sum(receipt["generation_attempted"] for receipt in receipts)
                    remaining_generation = []
                    for target in list(active):
                        if target == "gsm8k" or (self.recipe["sources"] and target in {"cpt", "agent"}):
                            continue
                        progress = production["goals"].get(target)
                        if progress is not None:
                            remaining = (max(0, progress["goal"] - progress["attempted"])
                                         if quality_first else progress["remaining"])
                            if not remaining and quality_first:
                                progress["stop_reason"] = "candidate_budget_reached"
                                active.remove(target)
                        else:
                            remaining = self.recipe.get("sample_count") or self.recipe["tasks"]
                        remaining_generation.append(remaining)
                    generation_size = min(size, max(remaining_generation, default=0))
                number = len(receipts) + 1
                round_dir = directory / f"round-{number:06d}"
                round_dir.mkdir(exist_ok=True)
                production["round"] = number
                production["current_batch"] = {"attempted": size, "targets": list(active)}
                self.save()
                self._production_destination = round_dir / "artifacts"
                self._production_collecting = True
                if self.independent_arithmetic() and not generation_size:
                    generated = RowSpool(round_dir / "generated.jsonl")
                    generated.close()
                elif self.recipe["sources"]:
                    generated = RowSpool(round_dir / "generated.jsonl")
                    # Human enhancement explicitly requests multiple phrasings
                    # of the same design. Quality-first still caps the initial
                    # candidate window and never replaces rejected variants.
                    human = self.recipe.get("qa_director", {}).get("human_augmentation", {}).get("enabled", False)
                    guided_expansion = evaluated_stop and self.recipe.get("qa_director", {}).get("enabled", False)
                    # A document may support several useful dialogue acts.
                    # The director may revisit it inside the same initial
                    # candidate cap, until valid feedback shows saturation.
                    # Rejecting a sample never creates additional quota debt.
                    for unit in source_index.rows(generation_offset, generation_size,
                                                  expand=not quality_first or human or guided_expansion):
                        generated.append(unit)
                    generated.close()
                elif set(active) == {"gsm8k"}:
                    generated = RowSpool(round_dir / "generated.jsonl")
                    for index in range(production["attempted"], production["attempted"] + size):
                        generated.append({"id": _digest([self.recipe["brief"], "gsm8k", index]),
                                          "source_id": _digest(self.recipe["brief"]), "kind": "brief", "status": "ready"})
                    generated.close()
                else:
                    generated = self._production_plan(generation_size, generation_offset, round_dir, seen)
                generated_count = sum(unit["status"] == "ready" for unit in generated)
                ready = WorkflowRows(generated.path, generated_count, predicate=lambda unit: unit["status"] == "ready")
                arithmetic_count = 0
                if self.independent_arithmetic():
                    if "gsm8k" in active:
                        math_attempts = sum(receipt["attempts"].get("gsm8k", 0) for receipt in receipts)
                        progress = production["goals"].get("gsm8k")
                        remaining = (max(0, progress["goal"] - math_attempts) if quality_first else progress["remaining"]) if progress else size
                        arithmetic_count = min(size, remaining)
                        arithmetic = self.arithmetic_inputs(round_dir / "arithmetic-seeds.json", arithmetic_count, math_attempts)
                        arithmetic_count = len(arithmetic)
                        if self.state.get("arithmetic_registry", {}).get("stop_reason") == "arithmetic_space_exhausted":
                            if progress is not None:
                                progress["stop_reason"] = "arithmetic_space_exhausted"
                            self.state["stages"]["gsm8k"]["stop_reason"] = "arithmetic_space_exhausted"
                            if not arithmetic_count:
                                active.remove("gsm8k")
                    else:
                        arithmetic = []
                    write_jsonl(round_dir / "arithmetic-inputs.jsonl", arithmetic)
                    self._production_arithmetic_inputs = WorkflowRows(round_dir / "arithmetic-inputs.jsonl", arithmetic_count)
                source_batch = RowSpool(round_dir / "source-batch.jsonl")
                # Source-preserving targets consume all selected originals exactly once.
                if not receipts:
                    originals = (self._shared_cleaned_rows if self.shared_source_cleaning() and "cpt" in active
                                 else source_index.rows(0, source_index.count, expand=False))
                    for unit in originals:
                        source_batch.append(unit)
                source_batch.close()
                for target in ("cpt", "agent"):
                    if self.recipe["sources"] and receipts and target in active:
                        active.remove(target)
                        if target in production["goals"]:
                            production["goals"][target]["stop_reason"] = (
                                "source_limit_reached" if self.state["input_summary"]["deferred"] else
                                "source_coverage_complete" if soft else "source_exhausted")
                if not active:
                    stop = ("arithmetic_space_exhausted" if self.state.get("arithmetic_registry", {}).get("stop_reason") == "arithmetic_space_exhausted" else
                            "source_limit_reached" if any(item["stop_reason"] == "source_limit_reached"
                                                         for item in production["goals"].values()) else
                            "source_coverage_complete" if soft and self.recipe["sources"] else "source_exhausted")
                    break
                if not ready and not source_batch and not arithmetic_count:
                    if self.recipe["sources"]:
                        stop = ("source_limit_reached" if self.state["input_summary"]["deferred"] else
                                "source_coverage_complete" if quality_first else "source_exhausted")
                        break
                if not ready and not source_batch and arithmetic_count and self.recipe["sources"]:
                    for target in list(active):
                        if target != "gsm8k":
                            active.remove(target)
                            if target in production["goals"]:
                                production["goals"][target]["stop_reason"] = "source_exhausted"
                production["round"] = number
                production["current_batch"] = {"attempted": max(len(generated), arithmetic_count), "targets": list(active)}
                self.save()
                collections = self._production_round(ready, source_batch, active)
                self.stage = "package"
                self.state["stages"]["package"].update(status="running", total=1, done=0)
                self.package(collections)
                rows, eligible, attempts, assessments = {}, {}, {}, {}
                for target in self.recipe["targets"]:
                    record_path = self._production_destination / f"{target}.records.json"
                    spool = RowSpool(round_dir / f"{target}.jsonl")
                    assessed = unassessed = 0
                    for row in iter_json_records(record_path):
                        spool.append(row)
                        if evaluated_stop and target in active:
                            valid = _quality_assessment_valid(row)
                            assessed += int(valid)
                            unassessed += int(not valid)
                    spool.close()
                    rows[target] = len(spool)
                    eligible[target] = self.state["quality"]["targets"][target]["eligible"]
                    if target in active:
                        attempts[target] = (arithmetic_count if target == "gsm8k" and self.independent_arithmetic() else
                            len(source_batch) if target in {"cpt", "agent"} and self.recipe["sources"] else len(generated))
                        if evaluated_stop:
                            upstream = (self._production_blocked_sft_quality or {}
                                if target in PREFERENCE_TARGETS | {"cot"} else {})
                            assessed += upstream.get("upstream_assessed", 0)
                            unassessed += upstream.get("upstream_unassessed", 0)
                            assessments[target] = {"assessed": assessed, "unassessed": unassessed,
                                "valid_for_saturation": bool(assessed) and not unassessed, **upstream}
                attempted = max(len(generated), arithmetic_count)
                receipt = {"round": number, "attempted": attempted, "attempts": attempts,
                           "planned_ready": generated_count,
                           "rows": rows, "eligible": eligible, "sha256": {
                               f"{target}.jsonl": _hash_file(round_dir / f"{target}.jsonl") for target in rows},
                           "package_review": deepcopy(self.state["quality"]["package_review"])}
                if self.independent_arithmetic():
                    receipt.update(generation_attempted=len(generated), arithmetic_attempted=arithmetic_count)
                    receipt["sha256"]["arithmetic-inputs.jsonl"] = _hash_file(round_dir / "arithmetic-inputs.jsonl")
                    receipt["sha256"][generated.path.name] = _hash_file(generated.path)
                if evaluated_stop:
                    receipt["quality_assessments"] = assessments
                    if self._production_blocked_sft_quality is not None:
                        source_file = "sft.upstream-quality.jsonl"
                        receipt["sha256"][source_file] = _hash_file(round_dir / source_file)
                if (soft and set(active) & directed_targets and
                        self.recipe.get("qa_director", {}).get("planning_mode") == "adaptive"):
                    feedback = self.state.get("qa_director", {}).get("feedback", {})
                    guided = self.state["stages"].get("director", {}).get("total", 0)
                    reasons = {reason: feedback.get(reason, 0)
                               for reason in ("source_exhausted", "no_new_grounded_scenario")}
                    skipped = sum(reasons.values())
                    if guided and skipped:
                        # An exhausted local batch cannot erase unseen original
                        # sources. The signal only stops reuse/extension after
                        # initial source coverage, or open-requirement planning.
                        may_stop = not self.recipe["sources"] or generation_offset + len(generated) >= source_index.count
                        receipt["director_saturation"] = {"guided": guided, **reasons,
                            "explicit_skip_count": skipped, "fraction": round(skipped / guided, 4),
                            "stop_expansion": may_stop and skipped / guided >= .8}
                        if evaluated_stop:
                            may_stop = may_stop and not self.state["input_summary"].get("deferred")
                            valid_assessment = (skipped <= guided and
                                not feedback.get("qa_director_plan_invalid_after_repair", 0) and
                                all(assessments.get(target, {}).get("valid_for_saturation")
                                    for target in active if target in directed_targets))
                            prior = min((director_rounds[target] for target in active if target in director_rounds
                                         and target in directed_targets), default=0)
                            continued = prior + 1 if valid_assessment and skipped / guided >= .8 else 0
                            receipt["director_saturation"].update(valid_assessment=valid_assessment,
                                consecutive_rounds=continued, stop_expansion=may_stop and
                                    continued >= max(2, config["low_acceptance_rounds"]))
                if (round_dir / "planned.jsonl").exists():
                    receipt["sha256"]["planned.jsonl"] = _hash_file(round_dir / "planned.jsonl")
                if soft and (round_dir / "planning.json").exists():
                    metadata = self._production_plan_metadata(round_dir, count=generation_size, offset=generation_offset)
                    receipt["planning"] = self._production_planning_summary(metadata)
                    receipt["sha256"]["planning.json"] = _hash_file(round_dir / "planning.json")
                atomic_json(round_dir / "receipt.json", receipt)
                identities.commit()
                receipts.append(receipt)
                previous = production
                production = self._production_state(config, receipts)
                generation_covered = (sum(receipt["generation_attempted"] for receipt in receipts)
                                      if self.independent_arithmetic() else production["attempted"])
                may_stop_expansion = not soft or not self.recipe["sources"] or generation_covered >= source_index.count
                if evaluated_stop and self.state["input_summary"].get("deferred"):
                    may_stop_expansion = False
                for target in config["goals"]:
                    if previous["goals"][target]["stop_reason"]:
                        production["goals"][target]["stop_reason"] = previous["goals"][target]["stop_reason"]
                self._production_stop_signals(config, receipt, production, low_rounds, director_rounds,
                    may_stop_expansion=may_stop_expansion, directed_targets=directed_targets)
                self.state["input_summary"]["generation_candidates"] = production["attempted"]
                self.save()
                self.event("production_round_completed", round=number, eligible=eligible, attempted=attempted)
            self._production_collecting = False
            self._production_destination = None
            self._production_finalizing = True
            self._production_completed_receipts = receipts
            self._production_input_records(directory, receipts)
            low_reason = "diminishing_returns" if soft else "low_acceptance_rate"
            if stop == "source_exhausted" and any(item["stop_reason"] == "director_saturation"
                                                  for item in production["goals"].values()):
                stop = "director_saturation"
            if stop == "source_exhausted" and any(item["stop_reason"] == low_reason
                                                  for item in production["goals"].values()):
                stop = low_reason
            if stop == "source_exhausted" and any(item["stop_reason"] == "arithmetic_space_exhausted"
                                                  for item in production["goals"].values()):
                stop = "arithmetic_space_exhausted"
            unavailable = any(production["counts"][target] == 0 for target in self.recipe["targets"] if target not in config["goals"])
            if unavailable and stop == reached_reason:
                stop = "source_exhausted"
            production.update(stop_reason=stop, status=arithmetic_completion_status(self, config, production["counts"], self.recipe["targets"],
                source_limited=stop == "source_limit_reached", attention_required=stop == "planning_failed_after_repair" or
                    bool(production.get("invalid_assessment_count"))))
            for progress in production["goals"].values():
                if not progress["remaining"]:
                    progress["stop_reason"] = reached_reason
                elif not progress["stop_reason"]:
                    progress["stop_reason"] = stop
            collections = {target: ProductionCollections(directory, receipts, target) for target in TARGETS}
            # Reuse pinned per-round review receipts. Final aggregation makes no new model calls.
            self.stage = "package"
            self.package(collections)
            self._production_finalizing = False
            counts = {target: value["eligible"] for target, value in self.state["quality"]["targets"].items()}
            final_loss = any(counts.get(target, 0) < production["counts"].get(target, 0)
                             for target in config["goals"])
            production["counts"] = counts
            for target, progress in production["goals"].items():
                progress.update(eligible=counts[target], remaining=max(0, progress["goal"] - counts[target]))
                if not progress["remaining"]:
                    progress["stop_reason"] = reached_reason
                elif progress["stop_reason"] in {None, reached_reason}:
                    progress["stop_reason"] = stop if stop != reached_reason else "final_validation_loss"
            unmet = any(progress["remaining"] for progress in production["goals"].values())
            if (not quality_first and not production.get("planning_exhaustion") and unmet and final_loss and
                    production["attempted"] < config["max_attempts"] and len(receipts) < config["max_rounds"]):
                self._production_reconcile_final(directory, receipts)
                self._production_retry_final_loss = True
                production.update(status="running", stop_reason=None)
                self.state["status"] = "running"
                self.save()
                self.event("production_final_validation_replenishment")
                return self.state
            if unmet and stop == reached_reason:
                stop = "final_validation_loss"
            if unmet and stop == "source_exhausted" and any(progress["stop_reason"] == low_reason
                                                            for progress in production["goals"].values()):
                stop = low_reason
            production.pop("current_batch", None)
            unavailable = any(counts[target] == 0 for target in self.recipe["targets"] if target not in config["goals"])
            production.update(status=arithmetic_completion_status(self, config, counts, self.recipe["targets"],
                source_limited=stop == "source_limit_reached", attention_required=stop == "planning_failed_after_repair" or
                    bool(production.get("invalid_assessment_count"))), stop_reason=stop)
            if soft:
                production["below_expectation"] = {target: progress["remaining"] for target, progress in production["goals"].items()
                                                  if progress["remaining"]}
            self.state["status"] = production["status"]
            self.state["stages"]["package"].update(status="completed", phase="completed", done=1, total=1)
            self.save()
            self.event("run_finished")
        return self.state
