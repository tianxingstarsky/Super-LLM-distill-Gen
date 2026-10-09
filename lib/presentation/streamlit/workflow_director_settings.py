"""Configure QA dispatch and its editable rules beside the director node."""
from __future__ import annotations

import streamlit as st

from lib.domain.workflow_qa_director import (
    DEFAULT_TYPE_WEIGHTS, DEFAULT_QUESTION_RULES, DEFAULT_ANSWER_RULES, validate_qa_director,
)
from lib.presentation.streamlit.i18n import translate_label


TYPE_LABELS = {
    "closed_book": "无线索问答", "grounded": "有线索问答", "partial": "部分线索问答",
    "multi_source": "多线索整合", "distractor": "干扰线索问答",
}
DIRECTED_TARGETS = frozenset({"sft", "multiturn", "dpo", "orpo", "rlaif", "cot"})


def _field(workspace, key, default):
    return st.session_state.get(f"workflow-form-draft:{workspace}", {}).get(
        key, st.session_state.get(key, default))


def director_snapshot(workspace: str, *, eligible=True) -> dict:
    if not eligible or not _field(workspace, f"workflow-director-enabled:{workspace}", False):
        return {"enabled": False}
    return {
        "enabled": True,
        "batch_size": _field(workspace, f"workflow-director-batch:{workspace}", 20),
        "history_limit": _field(workspace, f"workflow-director-history:{workspace}", 10),
        "type_weights": {name: _field(workspace, f"workflow-director-weight:{workspace}:{name}", default)
                         for name, default in DEFAULT_TYPE_WEIGHTS.items()},
        "question_rules": _field(workspace, f"workflow-director-question-rules:{workspace}", DEFAULT_QUESTION_RULES),
        "answer_rules": _field(workspace, f"workflow-director-answer-rules:{workspace}", DEFAULT_ANSWER_RULES),
    }


def director_has_issue(config: dict) -> bool:
    try:
        validate_qa_director(config)
    except (ValueError, TypeError):
        return True
    return False


def render_director_toggle(workspace: str, targets, *, save_field) -> dict:
    eligible = bool(DIRECTED_TARGETS.intersection(targets))
    key = f"workflow-director-enabled:{workspace}"
    st.session_state[key] = _field(workspace, key, False)
    st.toggle("加入问答指导员", key=key, disabled=not eligible,
              on_change=save_field, args=(workspace, key),
              help="按批次安排问答类型、线索与回答规则。点击指导员节点配置模型和提示词。")
    return director_snapshot(workspace, eligible=eligible)


def render_director_settings(workspace: str, *, save_field) -> None:
    for name, default in (("batch", 20), ("history", 10)):
        key = f"workflow-director-{name}:{workspace}"
        st.session_state[key] = _field(workspace, key, default)
    batch, history = st.columns(2, gap="small")
    with batch:
        key = f"workflow-director-batch:{workspace}"
        st.number_input("每批指导任务数", min_value=1, max_value=50, key=key,
                        on_change=save_field, args=(workspace, key))
    with history:
        key = f"workflow-director-history:{workspace}"
        st.number_input("相似历史参考数", min_value=0, max_value=20, key=key,
                        on_change=save_field, args=(workspace, key))
    st.caption("指导员按批次调度；历史问答只用于避重，不能作为新答案的事实依据。")
    st.markdown("**问答类型配比**")
    language = st.session_state.get("ui_language", "zh")
    columns = st.columns(3, gap="small")
    for index, (name, default) in enumerate(DEFAULT_TYPE_WEIGHTS.items()):
        key = f"workflow-director-weight:{workspace}:{name}"
        st.session_state[key] = _field(workspace, key, default)
        with columns[index % 3]:
            st.number_input(translate_label(TYPE_LABELS[name], language), min_value=0,
                            max_value=100, step=5, key=key,
                            on_change=save_field, args=(workspace, key))
    st.caption("数值表示相对配比，设为 0 即关闭该类型；资料不足时隔离任务，不虚构线索。")
    st.caption("无线索样本不附资料；有线索样本只按可见资料回答。部分线索不足时澄清，干扰内容不改变事实依据。")
    if director_has_issue(director_snapshot(workspace)):
        st.warning("请至少保留一种问答类型，并检查指导员规则。")


def render_director_rules(workspace: str, *, save_field) -> None:
    for name, label, default, placeholder in (
        ("question-rules", "问答调度指令", DEFAULT_QUESTION_RULES, "例如：优先覆盖故障诊断和边界条件，避免反复询问定义。"),
        ("answer-rules", "回答规则", DEFAULT_ANSWER_RULES, "例如：证据不足时追问缺失条件；有错误前提时先纠正，再给出回答。"),
    ):
        key = f"workflow-director-{name}:{workspace}"
        st.session_state[key] = _field(workspace, key, default)
        st.text_area(label, key=key, height=100, max_chars=12000, placeholder=placeholder,
                     on_change=save_field, args=(workspace, key))
    st.caption("规则随任务固定保存；生成与评审都会收到本题规则。下方可编辑或导入完整的指导员提示词。")
