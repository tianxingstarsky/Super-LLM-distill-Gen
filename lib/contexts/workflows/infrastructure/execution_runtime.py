"""Filesystem and checkpoint operations behind the execution use-case ports.

This adapter bridges the remaining generation engine. It owns I/O and runtime
state; target dependencies and stage ordering live in the application layer.
"""
from __future__ import annotations

from collections import Counter
import re

from filelock import FileLock

from ..application.execution_ports import PreparedBatch
from ..domain.execution import BatchGenerationPlan, BatchOutcome, TargetOutcome, execution_route
from ..domain.targets import STAGES, rlaif_feedback_issue
from lib.domain.workflow_node_prompts import validate_node_prompt_snapshot
from lib.domain.workflow_quality import canonical
from lib.infrastructure.workflow_candidates import prepare_generation_rows
from lib.infrastructure.workflow_row_checkpoint import row_checkpoint
from lib.infrastructure.workflow_rows import WorkflowRows, write_json_array
from lib.infrastructure.workflow_store import digest, file_hash, now, read_json, verify_artifacts


class FilesystemRunSession:
    """The persisted state and exclusive lock for a single generation attempt."""

    def __init__(self, runtime, *, supported_recipe_versions):
        self._runtime = runtime
        self._supported_recipe_versions = supported_recipe_versions

    def exclusive(self):
        return FileLock(str(self._runtime.path / ".run.lock"), timeout=0)

    def reload_status(self):
        self._runtime.state = read_json(self._runtime.path / "state.json")
        return self._runtime.state["status"]

    def verify_existing_delivery(self):
        verify_artifacts(self._runtime.path)

    def start_attempt(self, *, resume: bool):
        runtime = self._runtime
        if resume:
            (runtime.path / "cancel.json").unlink(missing_ok=True)
        runtime.state["attempt"] += 1
        runtime.state["status"] = "running"
        runtime.state.pop("error", None)
        runtime.state.pop("request_failure", None)
        runtime.save()

    def verify_pinned_inputs(self):
        runtime = self._runtime
        # Read under the run lock: construction may precede execution.
        runtime.recipe = read_json(runtime.path / "recipe.json")
        if digest(runtime.recipe) != runtime.state["recipe_hash"] or runtime.recipe["version"] not in self._supported_recipe_versions:
            raise ValueError("recipe_changed_create_new_run")
        if runtime.recipe["version"] >= 9:
            validate_node_prompt_snapshot(runtime.recipe.get("node_prompt_templates"),
                                          runtime.recipe.get("node_prompt_system"),
                                          recipe_version=runtime.recipe["version"],
                                          learner_context_review_version=runtime.recipe.get("learner_context_review_version"),
                                          claim_review_protocol_version=runtime.recipe.get("claim_review_protocol_version"),
                                          claim_review_format_feedback_version=runtime.recipe.get("claim_review_format_feedback_version"),
                                          claim_refutation_release_version=runtime.recipe.get("claim_refutation_release_version"),
                                          claim_reference_policy_version=runtime.recipe.get("claim_reference_policy_version"),
                                          claim_review_anchor_protocol_version=runtime.recipe.get("claim_review_anchor_protocol_version"),
                                          claim_review_adversarial_protocol_version=runtime.recipe.get("claim_review_adversarial_protocol_version"),
                                          claim_review_wire_schema_version=runtime.recipe.get("claim_review_wire_schema_version"),
                                          claim_review_focus_version=runtime.recipe.get("claim_review_focus_version"),
                                          claim_review_condition_contract_version=runtime.recipe.get("claim_review_condition_contract_version"),
                                          reasoning_depth_contract_version=runtime.recipe.get("reasoning_depth_contract_version"))
        else:
            if not isinstance(runtime.recipe.get("prompts"), dict):
                raise ValueError("prompts_changed_create_new_run")
            for key in runtime.recipe["prompts"]:
                runtime.historical_prompt(key)
        if runtime.recipe.get("agent_experience_version") == 1:
            from lib.domain.agent_experience import validate_agent_experience_snapshot
            validate_agent_experience_snapshot(runtime.recipe.get("agent_experience"))
        for source in runtime.recipe["sources"]:
            if file_hash(runtime.path / "inputs" / source["file"]) != source["sha256"]:
                raise ValueError("source_snapshot_changed")

    def route(self):
        repair_requested = bool(self._runtime.recipe.get("repair_inputs"))
        return execution_route(repair_requested=repair_requested,
                               production_enabled=not repair_requested and self._runtime.production_enabled())

    def run_repair(self):
        return self._runtime.execute_repair_branch()

    def run_production(self):
        return self._runtime._execute_production()

    def complete(self, outcome: BatchOutcome):
        self._runtime.state["status"] = outcome.status
        self._runtime.event("run_finished")

    def record_cancellation(self):
        runtime = self._runtime
        runtime.state["status"] = "cancelled"
        if runtime.production_enabled() and "production" in runtime.state:
            runtime.state["production"].update(status="cancelled", stop_reason="cancelled")
            runtime._production_partial_export("cancelled", "cancelled")
        runtime.state["stages"][runtime.stage]["status"] = "cancelled"
        runtime.event("run_cancelled")

    def record_failure(self, error: Exception):
        runtime = self._runtime
        # Persist only stable error codes and explicitly filtered diagnostics.
        # Provider exception messages can contain credentials and source text.
        code = str(error) if isinstance(error, ValueError) and re.fullmatch(r"[a-z_]+", str(error)) else type(error).__name__
        from lib.model_request_reliability import (
            ModelRequestError, classify_request_error, filter_request_diagnostic,
        )
        failure = classify_request_error(error)
        request_failure = None
        if isinstance(error, ModelRequestError):
            code = failure["code"]
            request_failure = filter_request_diagnostic(getattr(error, "diagnostics", None))
            if request_failure is not None:
                runtime.state["request_failure"] = request_failure
        if runtime.production_enabled():
            # The request classifier's fallback is not evidence of an API
            # failure. Keep internal exceptions distinguishable in the UI.
            if not isinstance(error, ValueError) and failure["code"] != "model_request_failed":
                code = failure["code"]
            if "production" in runtime.state:
                runtime.state["production"].update(status="paused", stop_reason=code)
                if code not in {"checkpoint_integrity_error", "source_snapshot_changed",
                                           "recipe_changed_create_new_run", "production_round_integrity_error",
                                           "production_review_integrity_error"}:
                    runtime._production_partial_export("paused", code)
        runtime.state.update(status="failed", error=code)
        runtime.state["stages"][runtime.stage].update(status="failed", error=code)
        runtime.event("run_failed", error=code,
                      **({"request_failure": request_failure} if request_failure is not None else {}))

    def result(self):
        return self._runtime.state


class CheckpointedBatchRuntime:
    """Streaming input, individual stage execution and verified file publication."""

    def __init__(self, runtime):
        self._runtime = runtime

    def plan(self) -> BatchGenerationPlan:
        return BatchGenerationPlan(frozenset(self._runtime.recipe["targets"]),
                                   bool(self._runtime.recipe.get("qa_director", {}).get("enabled")))

    def prepare_inputs(self, plan: BatchGenerationPlan):
        runtime = self._runtime
        selected_targets = plan.targets
        if runtime.arithmetic_only():
            input_records = []
            runtime.state["stages"]["ingest"].update(status="skipped", done=0, total=0)
        elif runtime.recipe["brief"] and not runtime.recipe["sources"] and plan.can_plan_from_brief:
            runtime.stage = "ingest"
            input_records = runtime.plan()
            runtime.state["stages"]["ingest"].update(status="completed", done=len(input_records),
                                                  total=len(input_records), outputs=len(input_records), eligible=len(input_records))
        else:
            input_records = runtime.stage_items("ingest", runtime.recipe["sources"], runtime.parse_source, stream_sources=True)
        if "gsm8k" in selected_targets and not input_records and not runtime.independent_arithmetic():
            brief = runtime.recipe["brief"]
            source_id = digest(brief)
            input_records = row_checkpoint(
                runtime.path / "checkpoints" / "ingest" / "math-inputs.json",
                lambda: ({"id": digest([brief, "gsm8k", i]), "source_id": source_id,
                          "kind": "brief", "text": brief, "status": "ready"}
                         for i in range(runtime.recipe["tasks"])), runtime.check_cancel)
        input_statuses = Counter(record["status"] for record in input_records)
        ready_count = input_statuses["ready"]
        ready_records = (input_records.ready(ready_count, runtime.recipe["max_units"]) if isinstance(input_records, WorkflowRows)
                    else [record for record in input_records if record["status"] == "ready"])
        requested = runtime.recipe.get("sample_count") or runtime.recipe["tasks"]
        planning_deferred = (max(0, requested - runtime.recipe["max_units"])
                             if not runtime.recipe["sources"] and not runtime.arithmetic_only() else 0)
        runtime.state["input_summary"] = {"units": len(input_records), "ready": ready_count,
            "quarantined": input_statuses["quarantined"], "skipped": input_statuses["skipped"],
            "deferred": max(max(0, ready_count - runtime.recipe["max_units"]), planning_deferred),
            "targets": list(runtime.recipe["targets"])}
        write_json_array(runtime.path / "input_records.json", input_records)
        selected_sources = ready_records if isinstance(ready_records, WorkflowRows) else ready_records[:runtime.recipe["max_units"]]
        generation_sources = runtime.prepare_clean_sources(selected_sources)
        generation_candidates = (prepare_generation_rows(runtime.path / "stage-results" / "generation-inputs.jsonl",
                                             generation_sources, runtime.recipe.get("sample_count"), runtime.check_cancel,
                                             variant_policy_version=2 if runtime.recipe["version"] >= 11 else 1)
                     if plan.needs_candidates else [])
        runtime.state["input_summary"]["generation_candidates"] = len(generation_candidates)
        return PreparedBatch(selected_sources, generation_candidates)

    def cpt_records(self, sources):
        runtime = self._runtime
        return (runtime.shared_cpt_collection() if runtime.shared_source_cleaning()
                else runtime.stage_items("cpt", sources, runtime.cpt))

    def run_stage(self, stage, records):
        runtime = self._runtime
        actions = {"sft": runtime.sft, "multiturn": runtime.multiturn, "agent": runtime.agent,
                   "preference": runtime.preference, "cot": runtime.cot}
        runtime.state["stages"].setdefault(stage, {"label": STAGES[stage], "status": "pending",
                                                  "done": 0, "total": 0})
        return runtime.stage_items(stage, records, actions[stage])

    def skip_stage(self, stage):
        runtime = self._runtime
        runtime.state["stages"].setdefault(stage, {"label": STAGES[stage], "done": 0, "total": 0})
        runtime.state["stages"][stage]["status"] = "skipped"

    def directed_dialogues(self, candidates, *, needs_sft, needs_multiturn):
        return self._runtime.directed_generation(candidates, needs_sft=needs_sft,
                                                needs_multiturn=needs_multiturn)

    def eligible_records(self, stage, records):
        return records.eligible(self._runtime.state["stages"][stage]["eligible"])

    def preference_exports(self, pairs, targets):
        def checked_rlaif(row):
            if row["status"] == "eligible":
                issue = rlaif_feedback_issue(row)
                if issue:
                    row.update(status="quarantined", reason=issue)
            return row

        return {"dpo": pairs if "dpo" in targets else [],
                "orpo": pairs if "orpo" in targets else [],
                "rlaif": WorkflowRows(pairs.path, len(pairs), transform=checked_rlaif)
                         if "rlaif" in targets else []}

    def arithmetic_records(self):
        runtime = self._runtime
        seed_base = runtime.recipe["brief"] or canonical(runtime.recipe["sources"])
        source_id = digest(seed_base)
        count = min(runtime.recipe.get("sample_count") or runtime.recipe["tasks"], runtime.recipe["max_units"])
        items = runtime.arithmetic_inputs(
            runtime.path / "checkpoints" / "gsm8k" / "independent-math-seeds.json",
            runtime.recipe.get("sample_count") or runtime.recipe["tasks"]) if runtime.independent_arithmetic() else row_checkpoint(
            runtime.path / "checkpoints" / "gsm8k" / "math-seeds.json",
            lambda: ({"id": digest([seed_base, "gsm8k", index]),
                      "source_id": source_id, "status": "ready"}
                     for index in range(count)), runtime.check_cancel)
        from lib.infrastructure.workflow_arithmetic import legacy_arithmetic_guard
        with legacy_arithmetic_guard(runtime, items):
            records = runtime.stage_items("gsm8k", items, runtime.gsm8k)
        if runtime.state.get("arithmetic_registry", {}).get("stop_reason") == "arithmetic_space_exhausted":
            runtime.state["stages"]["gsm8k"]["stop_reason"] = "arithmetic_space_exhausted"
            runtime.event("arithmetic_space_exhausted", generated=len(items))
        return records

    def finalize_reasoning(self, collections):
        self._runtime.finalize_reasoning_outputs(collections)

    def publish(self, collections):
        runtime = self._runtime
        runtime.stage = "package"
        runtime.state["stages"]["package"].update(status="running", total=1, done=0)
        runtime.save()
        runtime.check_cancel()
        runtime.package(collections)
        runtime.state["stages"]["package"].update(
            status="completed", phase="completed", done=runtime.state["stages"]["package"]["total"], finished_at=now())

    def outcome(self, collections) -> BatchOutcome:
        from lib.infrastructure.workflow_arithmetic import clean_arithmetic_exhaustion

        runtime = self._runtime
        metrics = runtime.state["quality"]["targets"]
        inputs = runtime.state["input_summary"]
        return BatchOutcome(
            targets=tuple(TargetOutcome(target=target, eligible=counts["eligible"],
                                        negative=counts.get("negative", 0),
                                        review_rejected=counts.get("package_review", {}).get("rejected", 0))
                          for target, counts in metrics.items()),
            quarantined_targets=frozenset(target for target in ("cpt", "multiturn", "agent")
                                         if any(row["status"] == "quarantined" for row in collections[target])),
            source_quarantined=inputs.get("source_cleaning", {}).get("quarantined", 0),
            input_quarantined=inputs["quarantined"], deferred_inputs=inputs["deferred"],
            arithmetic_exhausted_cleanly=clean_arithmetic_exhaustion(runtime))
