"""Exact workflow handoffs for existing review queues and verified delivery."""
from __future__ import annotations

import streamlit as st
from lib.domain.workflow_delivery import has_deliverable_results, delivery_manifest_matches


REVIEW_MODES = {
    "sft": ("SFT 数据调整", "sft-review-run"),
    "dpo": ("DPO 偏好优化", "preference-review-run"),
    "orpo": ("ORPO 偏好优化", "preference-review-run:orpo"),
    "rlaif": ("RLAIF 反馈审核", "preference-review-run:rlaif"),
    "cpt": ("CPT 语料审核", "corpus-review-run"),
    "agent": ("Agent 轨迹审查", None),
    "agent_negative": ("Agent 失败轨迹", None),
}


def request_app_navigation(run_id: str) -> None:
    """Defer a fragment callback's cross-page rerun to its next function body."""
    st.session_state["workflow-app-navigation-pending"] = {
        "workspace": st.session_state["ws"], "run_id": run_id,
    }


def consume_app_navigation(run_id: str) -> None:
    """A callback can only rerun its fragment; the body must rerun the app.

    Consume first so the full rerun cannot enter a navigation loop. Calling
    st.rerun from an on_click callback would be ignored by Streamlit.
    """
    pending = st.session_state.pop("workflow-app-navigation-pending", None)
    if pending == {"workspace": st.session_state.get("ws"), "run_id": run_id}:
        st.rerun(scope="app")


def review_choices(state: dict, manifest: dict) -> list[tuple[str, str]]:
    """Only offer implemented queues with nonempty native workflow artifacts.

    Multiturn, CoT and arithmetic remain separate training targets. Their
    records must never be redirected into the unrelated SFT review store.
    """
    if ("status" in state and not has_deliverable_results(state)) or not delivery_manifest_matches(state, manifest):
        return []
    targets = set(state.get("targets", []))
    counts = manifest.get("counts") or {}
    choices = [(target, mode[0]) for target, mode in REVIEW_MODES.items()
               if target in targets and int(counts.get(target, 0)) > 0]
    if "agent" in targets and int((manifest.get("negative_counts") or {}).get("agent", 0)) > 0:
        choices.append(("agent_negative", REVIEW_MODES["agent_negative"][0]))
    return choices


def open_review(run_id: str, target: str) -> None:
    """Select the exact existing queue without changing any review decision."""
    if target not in REVIEW_MODES:
        raise ValueError("unsupported_review_target")
    workspace = st.session_state["ws"]
    mode, selector_key = REVIEW_MODES[target]
    if target in {"agent", "agent_negative"}:
        # The Agent reviewer lives in task details, with its own verified store.
        st.session_state["workflow-open-run"] = {"workspace": workspace, "run_id": run_id}
        st.session_state[f"task-center-run:{workspace}"] = run_id
        st.session_state[f"task-center-filter:{workspace}"] = "全部"
        st.session_state[f"task-center-search:{workspace}"] = ""
        st.session_state[f"task-center-locate:{workspace}"] = run_id
        st.session_state[f"task-center-agent-review:{workspace}:{run_id}"] = True
        st.session_state[f"agent-review:{workspace}:{run_id}:kind"] = (
            "失败轨迹" if target == "agent_negative" else "训练候选")
        st.session_state[f"task-view:{workspace}"] = "数据工作流"
        st.session_state["nav"] = "任务管理"
    else:
        st.session_state[f"review-mode:{workspace}"] = mode
        st.session_state[selector_key] = run_id
        st.session_state["nav"] = "人工审核"


def open_verified_review(application, run_id: str, target: str, *, from_fragment: bool = False) -> None:
    """Recheck files at the action boundary before opening a persisted queue."""
    error_key = f"workflow-review-error:{run_id}"
    try:
        state = application.state(run_id)
        active = getattr(application, "is_active", None)
        if not has_deliverable_results(state) or (callable(active) and active(run_id)):
            raise ValueError("workflow_not_ready_for_review")
        inventory = application.package_inventory(run_id)
        if not delivery_manifest_matches(state, inventory["manifest"]):
            raise ValueError("artifact_integrity_error")
        choices = dict(review_choices(state, inventory["manifest"]))
        filename = "agent.negative.jsonl" if target == "agent_negative" else f"{target}.jsonl"
        if target not in choices or filename not in {row["name"] for row in inventory["files"]}:
            raise ValueError("review_artifact_unavailable")
    except (OSError, ValueError, TypeError, KeyError):
        st.session_state[error_key] = True
        return
    st.session_state.pop(error_key, None)
    open_review(run_id, target)
    if from_fragment:
        request_app_navigation(run_id)


def open_package(run_id: str, target: str | None = None, *, from_fragment: bool = False) -> None:
    """Open this run's delivery page; that page verifies the current files."""
    workspace = st.session_state["ws"]
    st.session_state[f"package-run:{workspace}"] = run_id
    if target is not None:
        # The package selector uses (target, filename) tuples. Consume this
        # scalar handoff before creating it, after the files are verified.
        st.session_state[f"package-target:{workspace}:{run_id}"] = target
    st.session_state["nav"] = "输出打包"
    if from_fragment:
        request_app_navigation(run_id)
