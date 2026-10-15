"""A deterministic working memory backed by the complete accepted QA ledger.

Only compact coverage and actual recent designs enter planner context. Facts
stay in immutable source packs; this summary does not replace evidence or the
exact SQLite duplicate index. No extra model summarisation call is required.
"""
from __future__ import annotations

from copy import deepcopy

from lib.domain.workflow_quality import canonical


VERSION = 1
MAX_MEMORY_BYTES = 8192
MAX_DESIGNS = 6
MAX_FEEDBACK = 16


def build_agent_memory(local_history, published_history, stages, *, history, feedback, candidates,
                       include_expression=False, expression_version=1):
    """Snapshot bounded memory; its caller checkpoints it before requesting."""
    ledgers = {}
    for label, store in (("current_run", local_history), ("published", published_history)):
        ledgers[label] = {stage: {"accepted_by_type": store.coverage(namespace=stage),
            "accepted_dimensions": store.dimension_coverage(namespace=stage, limit=8,
                **({"include_expression": True} if include_expression else {}),
                **({"expression_version": 2} if expression_version == 2 else {}))}
            for stage in stages}
    designs = [{"question": row["question"][:240], "qa_type": row["qa_type"],
                **({"dialogue_design": deepcopy(row["dialogue_design"])} if row.get("dialogue_design") else {}),
                **({"expression_design": deepcopy(row["expression_design"])}
                   if include_expression and row.get("expression_design") else {})}
               for row in history[:MAX_DESIGNS]]
    result = {"version": VERSION, "summary_kind": "deterministic_ledger_projection",
              "history_is_not_evidence": True, "deduplication": "indexed_exact_learner_task",
              "semantic_paraphrases_are_not_proven_unique": True,
              "ledgers": ledgers, "recent_designs": designs,
              "generation_feedback": {key: int(value) for key, value in
                  sorted(feedback.items(), key=lambda pair: (-pair[1], pair[0]))[:MAX_FEEDBACK]
                  if type(value) is int and value >= 0},
              "immutable_source_windows": sorted({candidate["source_context_pack"]["id"]
                  for candidate in candidates if candidate.get("source_context_pack")})}
    while len(canonical(result).encode("utf-8")) > MAX_MEMORY_BYTES and compact_agent_memory(result):
        pass
    return result


def compact_agent_memory(memory):
    """Discard optional working context, never mutate the durable ledger."""
    if memory.get("recent_designs"):
        memory["recent_designs"].pop()
        return True
    dimensions = [values for ledger in memory.get("ledgers", {}).values()
                  for stage in ledger.values() for values in stage.get("accepted_dimensions", {}).values()
                  if values]
    if dimensions:
        max(dimensions, key=len).pop()
        return True
    if memory.get("generation_feedback"):
        memory["generation_feedback"].pop(next(reversed(memory["generation_feedback"])))
        return True
    if memory.get("immutable_source_windows"):
        memory["immutable_source_windows"].pop()
        return True
    return False
