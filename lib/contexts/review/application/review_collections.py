"""Review streamed collections once and preserve canonical CoT-to-SFT lineage."""
from __future__ import annotations
from .automatic_ports import ReviewCollectionsPort
from ..domain.repair import validate_review_repair, skips_post_generation_review
from ..domain.candidate_content import candidate_digest as digest
from lib.domain.workflow_reasoning_route import cot_updates_sft


class ReviewGeneratedCollections:
    def __init__(self, port: ReviewCollectionsPort, recipe: dict):
        self._port, self.recipe = port, recipe

    def execute(self, collections):
        config = validate_review_repair(self.recipe.get("review_repair"))
        if config is None:
            return collections
        port = self._port
        review_targets = tuple(target for target in self.recipe["targets"]
                               if not skips_post_generation_review(self.recipe, target))
        if not review_targets:
            self._port.skip_review()
            return collections
        linked = bool(cot_updates_sft(self.recipe) and len(collections.get("cot", []))
                      and not self.recipe.get("repair_inputs")
                      and not self._port.finalizing)
        # Native repair already authenticated and reviewed these inputs before
        # packaging. A terminal rejection intentionally has no training body
        # to hash; bind its negative receipt to that saved selection instead.
        native_terminals = {}
        if config.get("native_regeneration") is True and self.recipe.get("native_regeneration_version") == 1:
            for supplied in self.recipe.get("repair_inputs") or []:
                context = supplied.get("revision_context") or {}
                original = supplied.get("record") or {}
                if supplied.get("target") == "cot" and context.get("native_regeneration") is True:
                    native_terminals[digest([self._port.run_id, "cot", context.get("candidate_id")])] = (
                        digest(context), digest(original.get("question")), original.get("source_id"))
        skipped_sft = (sum(row.get("reasoning_route") == "sft_via_cot" for row in collections.get("sft", []))
                       if linked else 0)
        class Items:
            def __len__(self):
                return sum(len(collections.get(target, [])) for target in review_targets) - skipped_sft

            def __iter__(self):
                for target in review_targets:
                    for record in collections.get(target, []):
                        port.check_cancel()
                        if linked and target == "sft" and record.get("reasoning_route") == "sft_via_cot":
                            continue
                        yield {"target": target, "record": record}

        def process(item):
            record = item["record"]
            receipt = record.get("review_repair", {})
            try:
                valid = (receipt.get("status") in {"accepted", "rejected", "waiting_manual_review"}
                    and receipt.get("config_sha256") == digest(config)
                    and receipt.get("candidate_sha256") == digest(port.payload(item["target"], item["record"])))
            except (KeyError, TypeError, ValueError):
                valid = False
            if not valid and item["target"] == "cot" and record.get("id") in native_terminals:
                valid = (record.get("status") == "quarantined"
                    and record.get("reason") in {"native_regeneration_failed_after_prefix_review", "native_answer_missing_after_retry"}
                    and receipt.get("status") == "rejected" and receipt.get("native_regeneration") is True
                    and receipt.get("config_sha256") == digest(config) and receipt.get("candidate_sha256") is None
                    and not any(record.get(field) for field in ("answer", "reasoning", "messages", "reasoning_prefill"))
                    and (record.get("revision_context") or {}).get("native_regeneration") is True
                    and native_terminals[record["id"]] == (digest(record["revision_context"]),
                        digest(record.get("question")), record.get("source_id")))
            return [{**item["record"], "_review_target": item["target"]}] if valid else port.review_item(item)
        reviewed = self._port.review_items(Items(), process)
        result = self._port.split_reviewed(reviewed, collections, targets=review_targets)
        if linked and skipped_sft:
            self._port.align_cot_sft(result, collections)
        return result
