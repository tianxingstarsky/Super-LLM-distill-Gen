"""Queue a bounded page of authenticated SFT plans with current experience."""
from __future__ import annotations

import streamlit as st
from filelock import Timeout

from lib.presentation.streamlit.review_navigation import request_app_navigation
from lib.presentation.streamlit.workflow_experience_controls import experience_configuration


def _queue_reanswer(application, run_id, candidate_ids, instruction_key=None):
    workspace = st.session_state.get("ws", "default")
    error_key = f"sft-reanswer-error:{run_id}"
    try:
        instructions = st.session_state.get(instruction_key, "") if instruction_key else ""
        options = {"agent_experience": experience_configuration(workspace)}
        if instructions.strip():
            options["task_instructions"] = {identifier: instructions for identifier in candidate_ids}
        child = application.reanswer_sft(run_id, candidate_ids=candidate_ids, **options)
    except (OSError, ValueError, TypeError, KeyError, Timeout):
        st.session_state[error_key] = True
        return
    st.session_state.pop(error_key, None)
    st.session_state[f"workflow-selected:{workspace}"] = child
    st.session_state["workflow-open-run"] = {"workspace": workspace, "run_id": child}
    request_app_navigation(run_id)


def render_sft_reanswer(application, run_id, target, page):
    if target != "sft" or not callable(getattr(application, "reanswer_sft", None)):
        return
    recipe = application.recipe(run_id)
    if recipe.get("node_generation", {}).get("sft", {}).get("answer_mode") != "native_joint":
        return
    selected = [row["id"] for row in page.get("rows", []) if row.get("id")]
    if not selected:
        return
    en = st.session_state.get("ui_language", "zh") == "en"
    instruction_key = f"sft-reanswer-instructions:{run_id}:{page.get('offset', 0)}"
    st.text_area("Requirements for this re-answer only (optional)" if en else "仅本次重答的补充要求（可选）",
        key=instruction_key, height=90,
        help=("Applies to this page as a generation system instruction. It does not change long-term experience "
              "or become something the user said. Facts still need support from the current sources."
              if en else "作为本页样本生成时的系统要求，不改长期经验，也不变成用户说过的话。事实判断仍须依据当前资料。"))
    st.button("Re-answer this page with current experience" if en else "用当前经验重答本页",
        key=f"sft-reanswer-page:{run_id}:{page.get('offset', 0)}", on_click=_queue_reanswer,
        args=(application, run_id, selected, instruction_key),
        help=("Keep the questions, source ranges and task contracts. Queue a new SFT run without planning again."
              if en else "保留本页问题、资料范围和任务合同，新建待运行的 SFT 重答任务，不再调用指导员。"))
    st.caption("Creates a queued task. Start it from the usual run controls; creating it makes no model calls."
               if en else "仅新建待运行任务；创建时不调用模型，按原有启动入口开始执行。")
    if st.session_state.get(f"sft-reanswer-error:{run_id}"):
        st.error("Could not verify this SFT selection. The original task is unchanged. Check its source and native SFT settings."
                 if en else "本页 SFT 暂时无法通过来源校验，原任务仍保留。请检查原生 SFT 配置与来源记录。")
