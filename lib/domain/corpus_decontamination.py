"""Conservative overlap checks against explicitly supplied evaluation text.

This is a data-leakage check, not a claim that an unseen benchmark is clean.
Only verbatim embedded matches or conservative whole-record near matches are
quarantined. The evaluation text itself is never returned with a match.
"""
from __future__ import annotations

from dataclasses import dataclass

from lib.domain.corpus_quality import CorpusNearDuplicateIndex, comparison_text


MAX_EVALUATION_RECORDS = 10_000
MAX_EVALUATION_CHARS = 5_000_000
MAX_RECORD_CHARS = 20_000
EMBEDDED_MIN_CHARS = 24
ANCHOR_CHARS = 16


@dataclass(frozen=True)
class EvaluationReference:
    identifier: str
    source_name: str
    record: int
    text: str


class EvaluationOverlapIndex:
    """Index pinned evaluation records without exposing their contents.

    Exact whole-record matches include short questions. Embedded matching
    requires at least 24 characters to avoid treating common short phrases as
    benchmark leakage. Long ordinary texts additionally use the same
    conservative near-duplicate rule as the CPT corpus.
    """

    def __init__(self, references: list[EvaluationReference]):
        if len(references) > MAX_EVALUATION_RECORDS:
            raise ValueError("evaluation_reference_limit_exceeded")
        self._exact: dict[str, EvaluationReference] = {}
        self._anchors: dict[str, list[tuple[str, EvaluationReference]]] = {}
        self._near = CorpusNearDuplicateIndex()
        self._by_id: dict[str, EvaluationReference] = {}
        self.ignored_empty = 0
        self.short_for_embedded = 0
        total_chars = 0
        for reference in references:
            if (not isinstance(reference, EvaluationReference)
                    or not isinstance(reference.identifier, str) or not reference.identifier
                    or reference.identifier in self._by_id
                    or not isinstance(reference.source_name, str)
                    or type(reference.record) is not int or reference.record < 1
                    or not isinstance(reference.text, str)):
                raise ValueError("invalid_evaluation_reference")
            normalized = comparison_text(reference.text)
            if not normalized:
                self.ignored_empty += 1
                continue
            if len(normalized) > MAX_RECORD_CHARS:
                raise ValueError("evaluation_record_too_long")
            total_chars += len(normalized)
            if total_chars > MAX_EVALUATION_CHARS:
                raise ValueError("evaluation_reference_limit_exceeded")
            self._by_id[reference.identifier] = reference
            self._exact.setdefault(normalized, reference)
            if len(normalized) >= EMBEDDED_MIN_CHARS:
                self._anchors.setdefault(normalized[:ANCHOR_CHARS], []).append((normalized, reference))
            else:
                self.short_for_embedded += 1
            self._near.add_reference(reference.text, {"id": reference.identifier,
                                                      "source_name": reference.source_name})
        self.reference_rows = len(self._by_id)

    @staticmethod
    def _result(reference: EvaluationReference, reason: str, *,
                similarity: float, match_type: str) -> dict:
        return {"reason": reason, "reference_id": reference.identifier,
                "reference_source": reference.source_name, "reference_record": reference.record,
                "similarity": similarity, "match_type": match_type}

    def inspect(self, text: str) -> dict | None:
        normalized = comparison_text(text)
        if not normalized:
            return None
        exact = self._exact.get(normalized)
        if exact is not None:
            return self._result(exact, "evaluation_verbatim_overlap", similarity=1.0,
                                match_type="whole_record")
        candidates: dict[str, tuple[str, EvaluationReference]] = {}
        for index in range(max(0, len(normalized) - ANCHOR_CHARS + 1)):
            for snippet, reference in self._anchors.get(normalized[index:index + ANCHOR_CHARS], ()):
                candidates.setdefault(reference.identifier, (snippet, reference))
        for snippet, reference in candidates.values():
            if snippet in normalized:
                return self._result(reference, "evaluation_verbatim_overlap", similarity=1.0,
                                    match_type="embedded")
        near = self._near.match(text)
        if near is not None and near["reason"] == "near_duplicate_corpus":
            reference = self._by_id[near["duplicate_of"]]
            return self._result(reference, "evaluation_near_overlap",
                                similarity=near["similarity"], match_type="whole_record_near")
        return None
