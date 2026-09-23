"""Data dependencies for the persisted automatic training workflow.

The graph describes which records feed a stage. Execution remains ordered and
checkpointed; edges must not be interpreted as parallel scheduling.
"""
from __future__ import annotations

from lib.domain.workflow_targets import PREFERENCE_TARGETS, TARGETS


BASE_STAGES = ("cpt", "sft", "multiturn", "agent", "gsm8k")
DERIVED_STAGES = ("preference", "cot")


def execution_graph(targets) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Return only stages needed for *targets*, including implicit SFT work."""
    selected = set(targets)
    unknown = selected.difference(TARGETS)
    if unknown:
        raise ValueError(f"unknown training targets: {', '.join(sorted(unknown))}")

    active = {"ingest", "package"}
    active.update(selected.intersection({"cpt", "sft", "multiturn", "agent", "gsm8k", "cot"}))
    if selected.intersection(PREFERENCE_TARGETS):
        active.add("preference")
    if "preference" in active or "cot" in active:
        active.add("sft")

    nodes = tuple(key for key in ("ingest", *BASE_STAGES, *DERIVED_STAGES, "package") if key in active)
    edges = []
    edges.extend(("ingest", key) for key in BASE_STAGES if key in active)
    edges.extend(("sft", key) for key in DERIVED_STAGES if key in active)
    # The SFT stage is an intermediate candidate set for preference/CoT-only
    # runs. Only a requested SFT export feeds its own packaged artifact.
    outputs = {"cpt", "multiturn", "agent", "gsm8k", "preference", "cot"}
    if "sft" in selected:
        outputs.add("sft")
    edges.extend((key, "package") for key in (*BASE_STAGES, *DERIVED_STAGES) if key in active and key in outputs)
    return nodes, tuple(edges)
