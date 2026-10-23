"""Durable production round I/O behind the production application port."""
from __future__ import annotations
from contextlib import contextmanager, ExitStack, closing
from copy import deepcopy
import sqlite3
from ..domain.production import (validate_production, quality_first_production,
    soft_expectation_production, evaluated_quality_stop)
from ..domain.production_rounds import ProductionProgress, quality_assessment_valid
from ..domain.targets import TARGETS, PREFERENCE_TARGETS
from ..application.production_ports import PreparedProductionRound
from lib.domain.workflow_package_review import package_review_stage
from lib.domain.open_task_plan import task_identity
from lib.infrastructure.planning_identities import PlanningIdentities
from lib.infrastructure.workflow_rows import RowSpool, WorkflowRows, write_json_array, write_jsonl
from lib.infrastructure.json_stream import iter_json_records
from lib.infrastructure.workflow_arithmetic import arithmetic_completion_status
from lib.io_utils import atomic_json
from .production_storage import ProductionInputs, ProductionCollections, _digest, _hash_file


class FilesystemProductionAdapter:
    def __init__(self, runtime, corpus_index_factory):
        self._runtime = runtime
        self._corpus_index = corpus_index_factory

    @contextmanager
    def open_session(self):
        runtime = self._runtime
        config = validate_production(runtime.recipe["production"], runtime.recipe["targets"])
        quality_first = quality_first_production(config)
        soft = soft_expectation_production(config)
        evaluated_stop = evaluated_quality_stop(config)
        reached_reason = "expectation_reached" if soft else "goal_reached"
        directory = runtime.path / "production"
        directory.mkdir(exist_ok=True)
        receipts = runtime._production_receipts(directory)
        production = runtime._production_state(config, receipts)
        needed = {"ingest", "package"} | set(runtime.recipe["targets"])
        review_targets = [target for target in runtime.recipe["targets"]
                          if not runtime.skips_post_generation_review(target)]
        if runtime.recipe.get("review_repair") is not None and review_targets:
            needed.add("review")
        elif (review_targets and runtime.recipe.get("package_review", {}).get("enabled")
                and package_review_stage(runtime.recipe["package_review"]) == "jev"):
            needed.add("jev")
        if set(runtime.recipe["targets"]) & (PREFERENCE_TARGETS | {"cot"}):
            needed.add("sft")
        if set(runtime.recipe["targets"]) & PREFERENCE_TARGETS:
            needed.add("preference")
        if runtime.shared_source_cleaning():
            needed.add("cpt")
        if runtime.recipe.get("qa_director", {}).get("enabled"):
            needed.add("director")
        if (runtime.recipe.get("reasoning_trim") or {}).get("enabled"):
            needed.add("trim")
        for stage in set(runtime.state["stages"]) - needed:
            runtime.state["stages"][stage]["status"] = "skipped"
        runtime.stage = "ingest"
        runtime._research_document = None if runtime.arithmetic_only() else runtime.research()
        if runtime.recipe["sources"] and not runtime.arithmetic_only():
            units = runtime.stage_items("ingest", runtime.recipe["sources"], runtime.parse_source, stream_sources=True)
            from collections import Counter
            input_statuses = Counter(unit["status"] for unit in units)
            ready_count = input_statuses["ready"]
            sources = units.ready(ready_count, runtime.recipe["max_units"])
            runtime.state["input_summary"] = {"units": len(units), "ready": ready_count,
                "quarantined": input_statuses["quarantined"], "skipped": input_statuses["skipped"],
                "deferred": max(0, ready_count - len(sources)),
                "targets": runtime.recipe["targets"], "generation_candidates": production["attempted"]}
            write_json_array(runtime.path / "input_records.json", units)
            sources = runtime.prepare_clean_sources(sources)
        else:
            sources = []
            runtime.state["input_summary"] = {"units": 0, "ready": 0, "quarantined": 0, "deferred": 0,
                "targets": runtime.recipe["targets"], "generation_candidates": production["attempted"]}
            write_json_array(runtime.path / "input_records.json", [])
        if runtime.arithmetic_only():
            runtime.state["stages"]["ingest"].update(status="skipped", done=0, total=0)
        with ExitStack() as resources:
            source_index = resources.enter_context(closing(ProductionInputs(directory / "source-index.sqlite3", sources)))
            seen = resources.enter_context(closing(PlanningIdentities(directory / "planning-identities.sqlite3")))
            identities = resources.enter_context(closing(sqlite3.connect(directory / "accepted-identities.sqlite3")))
            identities.execute("PRAGMA cache_size=-2048")
            identities.executescript("DROP TABLE IF EXISTS accepted; CREATE TABLE accepted (target TEXT, hash TEXT, PRIMARY KEY(target,hash));")
            runtime._production_identities = identities
            runtime._production_corpus = resources.enter_context(closing(self._corpus_index(directory / "accepted-cpt.sqlite3")))
            runtime._production_counts = dict(production["counts"])
            for receipt in receipts:
                for target in runtime.recipe["targets"]:
                    rows = ProductionCollections(directory, [receipt], target)
                    for row in rows:
                        if row["status"] == "eligible":
                            from lib.contexts.datasets.contracts import preferred_training_record
                            payload = preferred_training_record(target, row, runtime.recipe.get("generation_preferences"),
                                                                sft_output_style=runtime.recipe.get("sft_output_style"))
                            identities.execute("INSERT OR IGNORE INTO accepted VALUES (?, ?)", (target, _digest(payload)))
                            if target == "cpt":
                                runtime._production_corpus.add_reference(row["text"], {"id": row["id"]})
                plan = directory / f"round-{receipt['round']:06d}" / "planned.jsonl"
                if plan.exists():
                    seen.update(task_identity(unit["text"]) for unit in WorkflowRows(plan, receipt.get("generation_attempted", receipt["attempted"]))
                                if unit.get("status") == "ready")
            identities.commit()
            self._directory, self._sources, self._seen = directory, source_index, seen
            self._identities = identities
            yield ProductionProgress(runtime.recipe, config, production, receipts,
                source_index.count, runtime.state["input_summary"].get("deferred", 0), runtime.independent_arithmetic())

    def check_cancel(self):
        self._runtime.check_cancel()

    def prepare_round(self, progress, plan):
        runtime = self._runtime
        directory, source_index, seen = self._directory, self._sources, self._seen
        config, production, receipts = progress.config, progress.production, progress.receipts
        size, generation_size, generation_offset, active = plan.size, plan.generation_size, plan.generation_offset, plan.targets
        quality_first, evaluated_stop = quality_first_production(config), evaluated_quality_stop(config)
        number = len(receipts) + 1
        round_dir = directory / f"round-{number:06d}"
        round_dir.mkdir(exist_ok=True)
        production["round"] = number
        production["current_batch"] = {"attempted": size, "targets": list(active)}
        runtime.save()
        runtime._production_destination = round_dir / "artifacts"
        runtime._production_collecting = True
        if runtime.independent_arithmetic() and not generation_size:
            generated = RowSpool(round_dir / "generated.jsonl")
            generated.close()
        elif runtime.recipe["sources"]:
            generated = RowSpool(round_dir / "generated.jsonl")
            # Human enhancement explicitly requests multiple phrasings
            # of the same design. Quality-first still caps the initial
            # candidate window and never replaces rejected variants.
            human = runtime.recipe.get("qa_director", {}).get("human_augmentation", {}).get("enabled", False)
            guided_expansion = evaluated_stop and runtime.recipe.get("qa_director", {}).get("enabled", False)
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
                generated.append({"id": _digest([runtime.recipe["brief"], "gsm8k", index]),
                                  "source_id": _digest(runtime.recipe["brief"]), "kind": "brief", "status": "ready"})
            generated.close()
        else:
            generated = runtime._production_plan(generation_size, generation_offset, round_dir, seen)
        generated_count = sum(unit["status"] == "ready" for unit in generated)
        ready = WorkflowRows(generated.path, generated_count, predicate=lambda unit: unit["status"] == "ready")
        arithmetic_count = 0
        if runtime.independent_arithmetic():
            if "gsm8k" in active:
                math_attempts = sum(receipt["attempts"].get("gsm8k", 0) for receipt in receipts)
                progress = production["goals"].get("gsm8k")
                remaining = (max(0, progress["goal"] - math_attempts) if quality_first else progress["remaining"]) if progress else size
                arithmetic_count = min(size, remaining)
                arithmetic = runtime.arithmetic_inputs(round_dir / "arithmetic-seeds.json", arithmetic_count, math_attempts)
                arithmetic_count = len(arithmetic)
                if runtime.state.get("arithmetic_registry", {}).get("stop_reason") == "arithmetic_space_exhausted":
                    if progress is not None:
                        progress["stop_reason"] = "arithmetic_space_exhausted"
                    runtime.state["stages"]["gsm8k"]["stop_reason"] = "arithmetic_space_exhausted"
                    if not arithmetic_count:
                        active.remove("gsm8k")
            else:
                arithmetic = []
            write_jsonl(round_dir / "arithmetic-inputs.jsonl", arithmetic)
            runtime._production_arithmetic_inputs = WorkflowRows(round_dir / "arithmetic-inputs.jsonl", arithmetic_count)
        source_batch = RowSpool(round_dir / "source-batch.jsonl")
        # Source-preserving targets consume all selected originals exactly once.
        if not receipts:
            originals = (runtime._shared_cleaned_rows if runtime.shared_source_cleaning() and "cpt" in active
                         else source_index.rows(0, source_index.count, expand=False))
            for unit in originals:
                source_batch.append(unit)
        source_batch.close()
        self._round_directory = round_dir
        return PreparedProductionRound(generated, ready, source_batch, arithmetic_count, generated_count,
            runtime.state.get("arithmetic_registry", {}).get("stop_reason"))

    def execute_and_commit(self, progress, plan, batch):
        runtime = self._runtime
        config, production, receipts = progress.config, progress.production, progress.receipts
        number, size, generation_size, generation_offset, active = plan.number, plan.size, plan.generation_size, plan.generation_offset, plan.targets
        generated, ready, source_batch = batch.generated, batch.ready, batch.sources
        arithmetic_count, generated_count = batch.arithmetic_count, batch.generated_count
        round_dir, source_index, identities = self._round_directory, self._sources, self._identities
        soft, evaluated_stop = soft_expectation_production(config), evaluated_quality_stop(config)
        directed_targets, director_rounds = progress.directed_targets, progress.director_rounds
        production["round"] = number
        production["current_batch"] = {"attempted": max(len(generated), arithmetic_count), "targets": list(active)}
        runtime.save()
        collections = runtime._production_round(ready, source_batch, active)
        runtime.stage = "package"
        runtime.state["stages"]["package"].update(status="running", total=1, done=0)
        runtime.package(collections)
        rows, eligible, attempts, assessments = {}, {}, {}, {}
        for target in runtime.recipe["targets"]:
            record_path = runtime._production_destination / f"{target}.records.json"
            spool = RowSpool(round_dir / f"{target}.jsonl")
            assessed = unassessed = 0
            for row in iter_json_records(record_path):
                spool.append(row)
                if evaluated_stop and target in active:
                    valid = quality_assessment_valid(row)
                    assessed += int(valid)
                    unassessed += int(not valid)
            spool.close()
            rows[target] = len(spool)
            eligible[target] = runtime.state["quality"]["targets"][target]["eligible"]
            if target in active:
                attempts[target] = (arithmetic_count if target == "gsm8k" and runtime.independent_arithmetic() else
                    len(source_batch) if target in {"cpt", "agent"} and runtime.recipe["sources"] else len(generated))
                if evaluated_stop:
                    upstream = (runtime._production_blocked_sft_quality or {}
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
                   "package_review": deepcopy(runtime.state["quality"]["package_review"])}
        if runtime.independent_arithmetic():
            receipt.update(generation_attempted=len(generated), arithmetic_attempted=arithmetic_count)
            receipt["sha256"]["arithmetic-inputs.jsonl"] = _hash_file(round_dir / "arithmetic-inputs.jsonl")
            receipt["sha256"][generated.path.name] = _hash_file(generated.path)
        if evaluated_stop:
            receipt["quality_assessments"] = assessments
            if runtime._production_blocked_sft_quality is not None:
                source_file = "sft.upstream-quality.jsonl"
                receipt["sha256"][source_file] = _hash_file(round_dir / source_file)
        if (soft and set(active) & directed_targets and
                runtime.recipe.get("qa_director", {}).get("planning_mode") == "adaptive"):
            feedback = runtime.state.get("qa_director", {}).get("feedback", {})
            guided = runtime.state["stages"].get("director", {}).get("total", 0)
            reasons = {reason: feedback.get(reason, 0)
                       for reason in ("source_exhausted", "no_new_grounded_scenario")}
            skipped = sum(reasons.values())
            if guided and skipped:
                # An exhausted local batch cannot erase unseen original
                # sources. The signal only stops reuse/extension after
                # initial source coverage, or open-requirement planning.
                may_stop = not runtime.recipe["sources"] or generation_offset + len(generated) >= source_index.count
                receipt["director_saturation"] = {"guided": guided, **reasons,
                    "explicit_skip_count": skipped, "fraction": round(skipped / guided, 4),
                    "stop_expansion": may_stop and skipped / guided >= .8}
                if evaluated_stop:
                    may_stop = may_stop and not runtime.state["input_summary"].get("deferred")
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
            metadata = runtime._production_plan_metadata(round_dir, count=generation_size, offset=generation_offset)
            receipt["planning"] = runtime._production_planning_summary(metadata)
            receipt["sha256"]["planning.json"] = _hash_file(round_dir / "planning.json")
        atomic_json(round_dir / "receipt.json", receipt)
        identities.commit()
        return receipt

    def refresh_progress(self, progress):
        progress.production = self._runtime._production_state(progress.config, progress.receipts)

    def record_progress(self, progress, receipt):
        self._runtime.state["input_summary"]["generation_candidates"] = progress.production["attempted"]
        self._runtime.save()
        self._runtime.event("production_round_completed", round=receipt["round"],
                            eligible=receipt["eligible"], attempted=receipt["attempted"])

    def begin_finalization(self, progress):
        runtime = self._runtime
        runtime._production_collecting = False
        runtime._production_destination = None
        runtime._production_finalizing = True
        runtime._production_completed_receipts = progress.receipts
        runtime._production_input_records(self._directory, progress.receipts)

    def completion_status(self, progress, counts, stop):
        return arithmetic_completion_status(self._runtime, progress.config, counts, progress.recipe["targets"],
            source_limited=stop == "source_limit_reached", attention_required=stop == "planning_failed_after_repair"
            or bool(progress.production.get("invalid_assessment_count")))

    def publish_final(self, progress):
        runtime = self._runtime
        collections = {target: ProductionCollections(self._directory, progress.receipts, target) for target in TARGETS}
        runtime.stage = "package"
        runtime.package(collections)
        runtime._production_finalizing = False
        return {target: value["eligible"] for target, value in runtime.state["quality"]["targets"].items()}

    def reconcile_for_replenishment(self, progress):
        runtime = self._runtime
        runtime._production_reconcile_final(self._directory, progress.receipts)
        runtime._production_retry_final_loss = True
        progress.production.update(status="running", stop_reason=None)
        runtime.state["status"] = "running"
        runtime.save()
        runtime.event("production_final_validation_replenishment")
        return runtime.state

    def complete(self, progress):
        runtime = self._runtime
        runtime.state["status"] = progress.production["status"]
        runtime.state["stages"]["package"].update(status="completed", phase="completed", done=1, total=1)
        runtime.save()
        runtime.event("run_finished")
        return runtime.state

    def reset_replenishment(self):
        self._runtime._production_retry_final_loss = False

    def needs_replenishment(self):
        return self._runtime._production_retry_final_loss
