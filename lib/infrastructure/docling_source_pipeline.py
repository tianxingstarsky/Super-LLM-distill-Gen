"""Keep Docling's source blocks and source-page reading order intact.

Docling's cross-column/page paragraph merger is a heuristic, not PDF evidence.
Disabling it avoids both invalid dehyphenated charspans (upstream #4217) and
chart-axis strings swallowed into body paragraphs. All recognition stages and
the upstream separator-aware reading-order implementation remain unchanged.
"""
from __future__ import annotations

SOURCE_BLOCK_POLICY = "docling_unmerged_source_blocks_unique_caption_neighbor_v2"


def source_preserving_pipeline():
    """Return an isolated pipeline subclass; never patch installed dependencies."""
    from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
    from docling.models.postprocessing.reading_order_rb import ReadingOrderPredictor

    class SourcePredictor(ReadingOrderPredictor):
        def __init__(self):
            super().__init__()
            self.source_audit = {"policy": SOURCE_BLOCK_POLICY, "merge_candidates": [],
                                 "caption_changes": [], "text_rewritten": False}

        def predict_merges(self, sorted_elements):
            proposed = super().predict_merges(sorted_elements)
            by_id = {e.cid: e for e in sorted_elements}
            self.source_audit["merge_candidates"].extend(
                {"source_ref": by_id[key].ref.cref,
                 "candidate_refs": [by_id[value].ref.cref for value in values],
                 "applied": False} for key, values in proposed.items())
            return {}

        def predict_to_captions(self, sorted_elements):
            original = super().predict_to_captions(sorted_elements)
            owners = [e for e in sorted_elements if str(getattr(e.label, "value", e.label)) in {"table", "picture"}]
            captions = [e for e in sorted_elements if str(getattr(e.label, "value", e.label)) == "caption"]
            result = {key: list(values) for key, values in original.items()}
            for caption in captions:
                candidates = []
                for owner in owners:
                    if owner.page_no != caption.page_no:
                        continue
                    overlap = min(owner.r, caption.r) - max(owner.l, caption.l)
                    # A caption spanning both columns cannot be reassigned to
                    # one column merely because that object's edge is closer.
                    if (overlap < .6 * min(owner.r-owner.l, caption.r-caption.l)
                            or not owner.l<=(caption.l+caption.r)/2<=owner.r):
                        continue
                    # A caption must be outside the object, directly adjacent
                    # vertically. Both above-table and below-figure styles work.
                    gap = max(owner.b-caption.t, caption.b-owner.t)
                    if gap < -.5 or gap > 2 * min(caption.t-caption.b, 14):
                        continue
                    candidates.append((max(0, gap), owner.cid))
                candidates.sort()
                # Between stacked tables, both above and below placements can
                # be plausible. Distance alone cannot displace upstream's
                # caption association; only one eligible owner is sufficient.
                if len(candidates)!=1:
                    continue
                target = candidates[0][1]
                previous = [key for key, values in result.items() if caption.cid in values]
                if previous == [target]:
                    continue
                for values in result.values():
                    if caption.cid in values:
                        values.remove(caption.cid)
                result.setdefault(target, []).append(caption.cid)
                self.source_audit["caption_changes"].append({
                    "caption_ref": caption.ref.cref, "page_no": caption.page_no,
                    "previous_owner_ids": previous, "owner_id": target,
                    "vertical_gap_points": candidates[0][0], "basis": "unique_adjacent_source_geometry"})
            return {key: values for key, values in result.items() if values}

    class SourcePreservingPdfPipeline(StandardPdfPipeline):
        def _init_models(self):
            super()._init_models()
            self.reading_order_model.ro_model = SourcePredictor()

    return SourcePreservingPdfPipeline
