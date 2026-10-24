"""Connect the review use case to authenticated sources and checkpointed model calls."""
from __future__ import annotations
from .review_style import _record_accepted_revision_style


class CheckpointedReviewAdapter:
    def __init__(self, runtime):
        self._runtime = runtime

    @property
    def run_id(self):
        return self._runtime.state["id"]

    @property
    def authenticated_revision(self):
        return getattr(self._runtime, "_authenticated_repair_branch", False)

    def regenerate_native(self, item):
        from lib.infrastructure.workflow_native_regeneration import regenerate_native_cot_item
        return regenerate_native_cot_item(self._runtime, item)

    def record_revision_style(self, target, original, updated):
        return _record_accepted_revision_style(self._runtime, target, original, updated)

    def payload(self, *args, **kwargs):
        return self._runtime._review_payload(*args, **kwargs)

    def evidence(self, *args, **kwargs):
        return self._runtime._review_evidence(*args, **kwargs)

    def repair(self, *args, **kwargs):
        return self._runtime._repair_content(*args, **kwargs)

    def score(self, *args, **kwargs):
        return self._runtime._review_score(*args, **kwargs)

    def independent_cot_assessment(self, *args, **kwargs):
        return self._runtime.independent_cot_assessment(*args, **kwargs)


class StreamingReviewCollectionsAdapter(CheckpointedReviewAdapter):
    @property
    def finalizing(self):
        return getattr(self._runtime, "_production_finalizing", False)

    def skip_review(self):
        stages = self._runtime.state["stages"]
        if "review" in stages:
            stages["review"].update(status="skipped", done=0, total=0, outputs=0, eligible=0)

    def check_cancel(self):
        self._runtime.check_cancel()

    def review_item(self, item):
        return self._runtime._review_repair_item(item)

    def review_items(self, items, action):
        return self._runtime.stage_items("review", items, action)

    def split_reviewed(self, rows, collections, *, targets):
        return self._runtime._split_review_rows(rows, collections, targets=targets)

    def align_cot_sft(self, result, collections):
        from copy import deepcopy
        from lib.domain.workflow_reasoning_route import finalized_cot_sft_row
        from lib.infrastructure.workflow_rows import RowSpool
        from ..domain.candidate_content import candidate_digest as digest
        runtime = self._runtime
        canonical, unlinked = iter(result["cot"]), iter(result["sft"])
        spool = RowSpool(runtime.path / "stage-results" / "review-sft-from-cot.jsonl")
        try:
            for original in collections["sft"]:
                runtime.check_cancel()
                derived = original.get("reasoning_route") == "sft_via_cot"
                row = next(canonical if derived else unlinked, None)
                if row is None or row.get("id") != original.get("id"):
                    raise ValueError("cot_sft_lineage_mismatch")
                if derived:
                    row = finalized_cot_sft_row(row)
                    receipt = deepcopy(row.get("review_repair", {}))
                    try:
                        candidate_hash = digest(runtime._review_payload("sft", row))
                    except (KeyError, TypeError, ValueError):
                        # A failed canonical row may intentionally have no
                        # trainable content. Keep the failure and lineage;
                        # never reconstruct the earlier accepted SFT body.
                        candidate_hash = None
                    receipt.update(derived_from={"target": "cot", "candidate_id": row["id"]},
                                   candidate_sha256=candidate_hash)
                    row["review_repair"] = receipt
                spool.append(row)
            if next(canonical, None) is not None or next(unlinked, None) is not None:
                raise ValueError("cot_sft_lineage_mismatch")
        finally:
            spool.close()
            for iterator in (canonical, unlinked):
                if callable(close := getattr(iterator, "close", None)):
                    close()
        result["sft"] = spool


class AuthenticatedRepairAdapter:
    def __init__(self, runtime):
        self._runtime = runtime

    @property
    def run_id(self):
        return self._runtime.state["id"]

    def authenticate(self, inputs):
        from lib.infrastructure.human_revision import verified_revision_batch, verified_revision_parent
        from ..domain.candidate_content import candidate_digest as digest
        runtime = self._runtime
        # Authenticate every selected immutable parent before any model request.
        with verified_revision_batch(runtime.path.parent.parent, runtime.state["id"],
                                     [item["revision_context"] for item in inputs]):
            for item in inputs:
                actual = verified_revision_parent(runtime.path.parent.parent, runtime.state["id"], item["revision_context"])
                if digest(actual) != digest(item["record"]):
                    raise ValueError("human_session_result_changed")

    def prepare(self, inputs):
        from lib.infrastructure.workflow_rows import write_json_array
        runtime = self._runtime
        for stage, metrics in runtime.state["stages"].items():
            if stage not in {"review", "package"}:
                metrics.update(status="skipped", done=0, total=0)
        runtime.state["stages"]["ingest"].update(status="completed", phase="verified_repair_inputs",
            done=len(inputs), total=len(inputs), outputs=len(inputs), eligible=len(inputs))
        runtime.state["input_summary"] = {"units": len(inputs), "ready": len(inputs), "quarantined": 0,
            "skipped": 0, "deferred": 0, "generation_candidates": 0, "repair_candidates": len(inputs),
            "targets": list(runtime.recipe["targets"])}
        write_json_array(runtime.path / "input_records.json", [{"id": item["record"]["id"], "status": "ready",
            "source_id": item["revision_context"]["source_id"], "kind": "repair_candidate"} for item in inputs])

    def review(self, inputs):
        runtime = self._runtime
        # Only authenticated explicit inputs can repair an initially leaked
        # body. Ordinary review and arbitrary revision metadata cannot opt in.
        runtime._authenticated_repair_branch = True
        try:
            rows = runtime.stage_items("review", inputs, runtime._review_repair_item)
        finally:
            runtime._authenticated_repair_branch = False
        return rows

    def publish(self, rows):
        from lib.infrastructure.workflow_store import now
        runtime = self._runtime
        collections = runtime._split_review_rows(rows)
        runtime.stage = "package"
        runtime.state["stages"]["package"].update(status="running", done=0, total=1)
        runtime.save()
        runtime.package(collections)
        runtime.state["stages"]["package"].update(status="completed", phase="completed", done=1, total=1, finished_at=now())
        return runtime.state["quality"]["targets"]

    def complete(self, status):
        self._runtime.state["status"] = status
        self._runtime.event("run_finished")
        return self._runtime.state
