"""Finite, incremental delivery of accepted samples with durable round receipts."""
from __future__ import annotations

from ..domain.execution import WorkflowCancelled
from ..domain.production_rounds import (build_production_state, apply_stop_signals,
    quality_assessment_valid as _quality_assessment_valid)
from .production_storage import (_digest, _hash_file, _production_collection_file,
    ProductionInputs, ProductionCollections)

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

from lib.domain.open_task_plan import MAX_TASK_CHARS, task_identity, task_plan_issue, validate_short_task_plan
from lib.domain.workflow_production import (production_batch_size, validate_production,
    quality_first_production, soft_expectation_production, production_completion_status,
    evaluated_quality_stop)
from lib.domain.workflow_quality import canonical
from lib.domain.workflow_reasoning_route import cot_updates_sft
from lib.domain.workflow_package_review import package_review_stage
from lib.domain.workflow_scale import PLAN_BATCH_SIZE, generation_variant
from ..domain.targets import PREFERENCE_TARGETS, STAGES, TARGETS, rlaif_feedback_issue
from lib.infrastructure.json_stream import iter_json_records
from lib.infrastructure.workflow_arithmetic import arithmetic_completion_status
from lib.infrastructure.planning_identities import PlanningIdentities
from lib.infrastructure.workflow_rows import RowSpool, WorkflowRows, write_json_array, write_jsonl
from lib.io_utils import atomic_json
from lib.model_request_reliability import classify_request_error




















class WorkflowProduction:
    def production_enabled(self):
        return self.recipe.get("version", 0) >= 12 and self.recipe.get("production") is not None

    @staticmethod
    def _production_stop_signals(config, receipt, production, low_rounds, director_rounds,
                                 *, may_stop_expansion, directed_targets):
        return apply_stop_signals(config, receipt, production, low_rounds, director_rounds,
            may_stop_expansion=may_stop_expansion, directed_targets=directed_targets)

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
                if isinstance(error, WorkflowCancelled):
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
                    from lib.contexts.review.contracts import validate_review_repair
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
                        if isinstance(error, WorkflowCancelled):
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
                            if isinstance(error, WorkflowCancelled):
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
        state = build_production_state(self.recipe, config, receipts,
            spent_usd=self._task_budget.spent if self._task_budget is not None else None)
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
        from lib.bootstrap.production_execution import production_batch
        return production_batch(self).execute(generated, source_rows, active)

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
        from lib.bootstrap.production_execution import production_execution
        return production_execution(self).execute()

    def _execute_production_once(self):
        from lib.bootstrap.production_execution import production_execution
        return production_execution(self).execute_once()
