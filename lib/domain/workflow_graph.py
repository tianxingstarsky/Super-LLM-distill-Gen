"""Data dependencies for the persisted automatic training workflow.

The graph describes which records feed a stage. Execution remains ordered and
checkpointed; edges must not be interpreted as parallel scheduling.
"""
from __future__ import annotations

from pathlib import PurePath

from lib.domain.workflow_targets import PREFERENCE_TARGETS, TARGETS
from lib.domain.workflow_reasoning_route import cot_updates_sft


BASE_STAGES = ("cpt", "sft", "multiturn", "agent", "gsm8k")
DERIVED_STAGES = ("preference", "cot")
SOURCE_CLEANING_RECIPE_VERSION = 19
DOCUMENT_SOURCE_EXTENSIONS = frozenset({".md", ".txt", ".tex", ".latex", ".pdf", ".docx",
                                       ".png", ".jpg", ".jpeg", ".webp"})
SOURCE_QA_TARGETS = PREFERENCE_TARGETS | {"sft", "cot", "multiturn"}


def uses_shared_source_cleaning(targets, sources=(), *, source_mode=None) -> bool:
    """Document-grounded dialogues share cleaning, independent of CPT export.

    File descriptors and draft source modes are accepted without opening files.
    Recorded conversations, human designs and open briefs keep their own path.
    """
    if not set(targets).intersection(SOURCE_QA_TARGETS):
        return False
    if source_mode is not None:
        return source_mode in {"文档资料", "模型辅助文档", "多模态文档", "知识库检索"}
    for source in sources:
        if isinstance(source, dict):
            if source.get("kind") == "human_design":
                continue
            if source.get("kind") in {"document", "document_collection"}:
                return True
            source = source.get("file") or source.get("name") or ""
        if PurePath(str(source)).suffix.lower() in DOCUMENT_SOURCE_EXTENSIONS:
            return True
    return False


def sft_uses_cot_output(targets, recipe_version=15) -> bool:
    """New combined runs publish SFT from the final CoT result, never its input."""
    return cot_updates_sft({"version": recipe_version, "targets": targets})


def execution_graph(targets, *, reasoning_trim=False, qa_director=False,
                    package_review=None, recipe_version=16,
                    repair_only=False, source_cleaning=None,
                    review_repair=None) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Return only stages needed for *targets*, including implicit SFT work."""
    selected = set(targets)
    unknown = selected.difference(TARGETS)
    if unknown:
        raise ValueError(f"unknown training targets: {', '.join(sorted(unknown))}")
    if repair_only and recipe_version >= 18:
        return ("ingest", "review", "package"), (("ingest", "review"), ("review", "package"))

    active = {"ingest", "package"}
    active.update(selected.intersection({"cpt", "sft", "multiturn", "agent", "gsm8k", "cot"}))
    if selected.intersection(PREFERENCE_TARGETS):
        active.add("preference")
    if "preference" in active or "cot" in active:
        active.add("sft")
    shared_cleaning = recipe_version >= SOURCE_CLEANING_RECIPE_VERSION and (
        source_cleaning is True or source_cleaning is None and bool(selected.intersection(SOURCE_QA_TARGETS)))
    if shared_cleaning:
        active.add("cpt")
    # The canvas also renders incomplete editable drafts. Creation performs
    # full validation; dependencies only depend on the explicit switch.
    directed = (qa_director.get("enabled") is True if isinstance(qa_director, dict)
                else bool(qa_director)) and bool(active.intersection({"sft", "multiturn"}))
    if directed:
        active.add("director")
    trimmed_outputs = selected.intersection({"sft", "cot"}) if reasoning_trim else set()
    if trimmed_outputs:
        active.add("trim")
    # New recipes run scoring and repair together after the requested outputs
    # are complete. Their repair loop stays within this stage: rejected answers
    # must never be fed back to the SFT candidate generator as training inputs.
    unified_review = recipe_version == 18 or recipe_version >= SOURCE_CLEANING_RECIPE_VERSION and review_repair is not None
    reviewed = (not unified_review and bool(package_review and package_review.get("enabled")
                                           and package_review.get("node") == "jev"))
    if unified_review:
        active.add("review")
    elif reviewed:
        active.add("jev")

    base_order = ("cpt", "director", *BASE_STAGES[1:]) if shared_cleaning else ("director", *BASE_STAGES)
    nodes = tuple(key for key in ("ingest", *base_order, *DERIVED_STAGES,
                                 "trim", "review", "jev", "package") if key in active)
    edges = []
    generation_source = "cpt" if shared_cleaning else "ingest"
    if directed:
        edges.append((generation_source, "director"))
    edges.extend(("director" if directed and key in {"sft", "multiturn"}
                  else generation_source if key in {"sft", "multiturn"} else "ingest", key)
                 for key in BASE_STAGES if key in active)
    edges.extend(("sft", key) for key in DERIVED_STAGES if key in active)
    # Combined runs publish the final CoT records in both requested formats.
    # The earlier SFT candidates must not bypass CoT verification or trimming.
    # Retain independent outputs when displaying a persisted legacy recipe.
    outputs = {"multiturn", "agent", "gsm8k", "preference", "cot"}
    if "cpt" in selected:
        outputs.add("cpt")
    if "sft" in selected and not sft_uses_cot_output(selected, recipe_version):
        outputs.add("sft")
    terminal = "review" if unified_review else "jev" if reviewed else "package"
    edges.extend((key, "trim" if key in trimmed_outputs else terminal)
                 for key in (*BASE_STAGES, *DERIVED_STAGES) if key in active and key in outputs)
    if trimmed_outputs:
        edges.append(("trim", terminal))
    if unified_review or reviewed:
        edges.append((terminal, "package"))
    return nodes, tuple(edges)
