"""Data dependencies for the persisted automatic training workflow.

The graph describes which records feed a stage. Execution remains ordered and
checkpointed; edges must not be interpreted as parallel scheduling.
"""
from __future__ import annotations

from lib.domain.workflow_targets import PREFERENCE_TARGETS, TARGETS


BASE_STAGES = ("cpt", "sft", "multiturn", "agent", "gsm8k")
DERIVED_STAGES = ("preference", "cot")


def execution_graph(targets, *, reasoning_trim=False, qa_director=False) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
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
    # The canvas also renders incomplete editable drafts. Creation performs
    # full validation; dependencies only depend on the explicit switch.
    directed = (qa_director.get("enabled") is True if isinstance(qa_director, dict)
                else bool(qa_director)) and bool(active.intersection({"sft", "multiturn"}))
    if directed:
        active.add("director")
    trimmed_outputs = selected.intersection({"sft", "cot"}) if reasoning_trim else set()
    if trimmed_outputs:
        active.add("trim")

    nodes = tuple(key for key in ("ingest", "director", *BASE_STAGES, *DERIVED_STAGES, "trim", "package") if key in active)
    edges = []
    if directed:
        edges.append(("ingest", "director"))
    edges.extend(("director" if directed and key in {"sft", "multiturn"} else "ingest", key)
                 for key in BASE_STAGES if key in active)
    edges.extend(("sft", key) for key in DERIVED_STAGES if key in active)
    # The SFT stage is an intermediate candidate set for preference/CoT-only
    # runs. Only a requested SFT export feeds its own packaged artifact.
    outputs = {"cpt", "multiturn", "agent", "gsm8k", "preference", "cot"}
    if "sft" in selected:
        outputs.add("sft")
    edges.extend((key, "trim" if key in trimmed_outputs else "package")
                 for key in (*BASE_STAGES, *DERIVED_STAGES) if key in active and key in outputs)
    if trimmed_outputs:
        edges.append(("trim", "package"))
    return nodes, tuple(edges)
