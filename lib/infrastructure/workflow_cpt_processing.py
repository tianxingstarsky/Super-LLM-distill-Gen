"""Source-grounded CPT cleaning with independent, checkpointed review."""
from __future__ import annotations

from pathlib import Path

from lib.domain.cpt_processing import cleaned_corpus, corpus_preservation, validate_cpt_processing
from lib.domain.corpus_quality import inspect_corpus
from lib.domain.workflow_quality import accepted


class WorkflowCPTProcessing:
    def cpt_processing(self) -> dict:
        return validate_cpt_processing(self.recipe.get("cpt_processing"))

    def model_clean_corpus(self, unit: dict) -> list[dict]:
        """Do not synthesize extra facts or compensate for a quantity target."""
        config = self.cpt_processing()
        from lib.infrastructure.training_workflow import digest
        original = unit["text"]
        reading = unit.get("document_reading")
        reading = reading if isinstance(reading, dict) else {}
        image = None
        review_mode = config["review_mode"]
        if config["review_mode"] == "vision":
            # Visual fidelity requires the original pinned page. Never change
            # an explicitly requested image review into an ordinary text judge.
            image = self.cpt_source_image(unit)
            if not image:
                location = unit.get("source_location") or {}
                suffix = Path(location.get("file") or unit.get("source_name", "")).suffix.lower()
                visual_expected = (suffix in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
                                   or ":image:" in str(location.get("record", ""))
                                   or bool(reading.get("image_sha256")))
                if visual_expected:
                    return [{**self.rejected(unit, "cpt_visual_source_unavailable"),
                             "source_text_sha256": digest(original)}]
                # The UI explicitly defines mixed-source review: plain text
                # and DOCX text blocks are checked against their source text.
                review_mode = "text"
        key = [unit["id"], "cpt_clean", digest(original)]
        data = self.ask(key, "generation", "workflow.cpt_clean", {
            "source_text": original, "source_location": unit.get("source_location"),
            "source_reading": reading,
            "scope": "Clean this source unit only; retain substantive facts, never expand to meet a sample goal.",
        }, allow_reasoning_fallback=False)
        try:
            data = cleaned_corpus(data)
        except ValueError:
            self.invalidate_checkpoint(["call", key])
            raise
        trace = {"mode": "model", "review_mode": review_mode,
                 "requested_review_mode": config["review_mode"],
                 "source_text_sha256": digest(original), "cleaned_text_sha256": digest(data["text"]),
                 "removed_noise": data["removed_noise"], "cleaning_reason": data["reason"]}
        quality = inspect_corpus(data["text"])
        if data["uncertain"] or not quality["keep"]:
            return [{**self.rejected(unit, "cpt_clean_uncertain" if data["uncertain"] else quality["reason"]),
                     "quality": quality, "cpt_processing": trace}]
        reading_evidence = {"source_text": reading.get("source_text"), "parsed_text": reading.get("parsed_text")}
        if any(value is not None for value in reading_evidence.values()):
            trace["reading_evidence_sha256"] = digest(reading_evidence)
        key = [unit["id"], "cpt_source_review", trace["source_text_sha256"], trace["cleaned_text_sha256"]]
        if reading.get("parsed_text") is not None:
            key.append(trace["reading_evidence_sha256"])
        check = self.ask(key, "jev", "workflow.cpt_review", {
            "original_source_text": original, "cleaned_text": data["text"],
            "reading_source_text": reading.get("source_text"),
            "parsed_source_text": reading.get("parsed_text"),
            "source_reading": {key: value for key, value in reading.items()
                               if key not in {"source_text", "parsed_text"}},
            "source_location": unit.get("source_location"), "removed_noise": data["removed_noise"],
            "review_mode": review_mode,
            "scope": "First verify the full reading_source_text to parsed_source_text for substantive coverage and fidelity when both are supplied. Then verify this original_source_text chunk to cleaned_text. Other chunks must be retained in parsed_source_text, not copied into this cleaned chunk. A failure in either comparison rejects the chunk.",
        }, image=image, allow_reasoning_fallback=False)
        try:
            check = corpus_preservation(check)
        except ValueError:
            self.invalidate_checkpoint(["call", key])
            raise
        if not accepted(check) or not all(check["preservation"].values()):
            return [{**self.rejected(unit, "cpt_source_review_rejected"), "quality": quality,
                     "judge": check, "cpt_processing": trace}]
        return [{**unit, "text": data["text"], "status": "eligible", "quality": quality,
                 **({"source_context": unit} if self.recipe.get("review_repair") is not None else {}),
                 "judge": check, "cpt_processing": trace,
                 "retention_reason": "source_text_cleaned_and_independently_reviewed",
                 "evidence_level": ("model_reviewed_visual_source" if image else "model_reviewed_source_text")}]
