"""Exact workflow handoffs for existing review queues and verified delivery."""
from __future__ import annotations

import streamlit as st
from filelock import Timeout
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


def remember_task_return(destination: str, run_id: str, target: str | None = None, *, widgets=None) -> None:
    """Keep reading context outside widget keys while a different page is open."""
    widgets = st if widgets is None else widgets
    workspace = widgets.session_state.get("ws", "default")
    origin_key = f"task-origin:{workspace}:{run_id}"
    origin = widgets.session_state.get(origin_key, {})
    view = widgets.session_state.get(f"workflow-result-view:{run_id}", origin.get("view", "运行过程"))
    context = {
        "run_id": run_id, "view": view if view in {"运行过程", "样本报告"} else "运行过程",
        "target": target or widgets.session_state.get(f"workflow-report-target:{run_id}", origin.get("target")),
        "limit": widgets.session_state.get(f"workflow-report-limit:{run_id}", origin.get("limit", 6)),
        "page": widgets.session_state.get(f"workflow-report-page:{run_id}", origin.get("page", 1)),
    }
    widgets.session_state[origin_key] = context
    widgets.session_state[f"task-return:{workspace}:{destination}"] = dict(context)


def return_to_task(run_id: str, *, destination: str | None = None, navigate=None) -> None:
    """Return to the exact run without changing its recipe or worker state."""
    workspace = st.session_state.get("ws", "default")
    saved = st.session_state.get(f"task-return:{workspace}:{destination}", {}) if destination else {}
    if navigate is not None:
        navigate("任务管理")
    else:
        st.session_state["nav"] = "任务管理"
        st.query_params.pop("human", None)
        st.session_state.pop("human-open-session", None)
        st.query_params["page"] = "任务管理"
    if saved.get("run_id") == run_id:
        st.session_state[f"workflow-result-view:{run_id}"] = saved.get("view", "运行过程")
        if saved.get("view") == "样本报告":
            st.session_state[f"task-center-focus:{workspace}"] = True
        if saved.get("target"):
            st.session_state[f"workflow-report-target:{run_id}"] = saved["target"]
        limit = saved.get("limit", 6)
        if limit in {6, 12, 24}:
            st.session_state[f"workflow-report-limit:{run_id}"] = limit
            st.session_state[f"workflow-report-page:{run_id}"] = max(1, int(saved.get("page", 1)))
            st.session_state[f"workflow-report-page:{run_id}:selection"] = (saved.get("target"), limit)
    st.session_state["workflow-open-run"] = {"workspace": workspace, "run_id": run_id}


def render_review_task_return(run_id: str, target: str) -> None:
    """Show a return action only for this queue's recorded task handoff."""
    workspace = st.session_state.get("ws", "default")
    destination = "review:" + run_id + ":" + target
    saved = st.session_state.get(f"task-return:{workspace}:{destination}", {})
    if saved.get("run_id") != run_id or saved.get("target") != target:
        return
    st.button("返回当前任务", icon=":material/arrow_back:",
              key=f"review-return-task:{workspace}:{run_id}:{target}",
              on_click=return_to_task, args=(run_id,), kwargs={"destination": destination},
              help="返回此任务，保留报告或运行视图。")


def open_verified_correction(application, human_application, run_id: str, target: str,
                             *, from_fragment: bool = False) -> None:
    """Open a preserved candidate collection only after rechecking this run."""
    error_key = f"workflow-review-error:{run_id}"
    try:
        state = application.state(run_id)
        active = getattr(application, "is_active", None)
        if (not has_deliverable_results(state) or state.get("status") not in {"completed", "needs_attention"}
                or target not in state.get("targets", []) or (callable(active) and active(run_id))):
            raise ValueError("workflow_not_ready_for_review")
        inventory = application.package_inventory(run_id)
        if not delivery_manifest_matches(state, inventory["manifest"]):
            raise ValueError("artifact_integrity_error")
        session_id = human_application.review_session(run_id)
        session = human_application.session(session_id)
        parent = next((row for row in session.get("rounds", []) if row.get("run_id") == run_id), None)
        if parent is None:
            raise ValueError("human_session_round_not_found")
        context = f"human:{st.session_state['ws']}:{session_id}"
        st.session_state[f"human-round-pending:{context}"] = parent["id"]
        st.session_state[f"human-target:{context}:{parent['id']}"] = target
        st.session_state[f"human-result-view-pending:{context}"] = "results"
    except (OSError, ValueError, TypeError, KeyError, Timeout):
        st.session_state[error_key] = True
        return
    st.session_state.pop(error_key, None)
    remember_task_return("human:" + session_id, run_id, target)
    st.session_state["human-open-session"] = {"workspace": st.session_state["ws"], "session_id": session_id}
    if from_fragment:
        request_app_navigation(run_id)


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
    remember_task_return("review:" + run_id + ":" + target, run_id, target)
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
    remember_task_return("package:" + run_id, run_id, target)
    st.session_state[f"package-run:{workspace}"] = run_id
    if target is not None:
        # The package selector uses (target, filename) tuples. Consume this
        # scalar handoff before creating it, after the files are verified.
        st.session_state[f"package-target:{workspace}:{run_id}"] = target
    st.session_state["nav"] = "输出打包"
    if from_fragment:
        request_app_navigation(run_id)
