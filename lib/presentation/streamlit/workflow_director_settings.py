"""Configure QA dispatch and its editable rules beside the director node."""
from __future__ import annotations

import streamlit as st

from lib.domain.workflow_qa_director import (
    DEFAULT_TYPE_WEIGHTS, DEFAULT_QUESTION_RULES, DEFAULT_ANSWER_RULES, validate_qa_director,
)
from lib.presentation.streamlit.i18n import translate_label
from lib.presentation.streamlit.prompt_library_controls import render_prompt_library


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
    mode = _field(workspace, f"workflow-director-mode:{workspace}", "adaptive")
    return {
        "enabled": True,
        "planning_mode": mode,
        "batch_size": _field(workspace, f"workflow-director-batch:{workspace}", 20),
        "history_limit": _field(workspace, f"workflow-director-history:{workspace}", 10),
        "type_weights": ({name: _field(workspace, f"workflow-director-weight:{workspace}:{name}", default)
                          for name, default in DEFAULT_TYPE_WEIGHTS.items()}
                         if mode == "balanced" else dict(DEFAULT_TYPE_WEIGHTS)),
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
    st.toggle("加入对话指导员", key=key, disabled=not eligible,
              on_change=save_field, args=(workspace, key),
              help="结合语言、上下文与资料设计互动，连续多轮按实际回应调整。模型与提示词在节点内配置。")
    return director_snapshot(workspace, eligible=eligible)


def render_director_settings(workspace: str, *, save_field) -> None:
    key = f"workflow-director-mode:{workspace}"
    st.session_state[key] = _field(workspace, key, "adaptive")
    language = st.session_state.get("ui_language", "zh")
    mode_labels = {"adaptive": translate_label("语言专家自适应", language),
                   "balanced": translate_label("按线索比例安排", language)}
    mode = st.selectbox("指导方式", tuple(mode_labels), key=key, format_func=mode_labels.__getitem__,
                        on_change=save_field, args=(workspace, key))
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
    st.caption("历史样本用于发现重复和设计新的互动，不作为事实依据。")
    if mode == "adaptive":
        st.info("指导员结合任务目的、用户表达和上下文自主设计；可以澄清、协商、修改或共同解决问题，不要求问答形式或固定行为配比。")
        st.caption("多轮根据真实回应推进，达到目的即可结束；资料不足时允许跳过，不为数量或轮数凑内容。")
        return
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


def render_director_rules(workspace: str, *, save_field, prompt_library=None) -> None:
    rule_fields = (
        ("question-rules", "对话设计指令", DEFAULT_QUESTION_RULES, "例如：结合用户已有表达，设计共同分析、方案协商和有意义的后续互动。"),
        ("answer-rules", "回应与任务推进指令", DEFAULT_ANSWER_RULES, "例如：记住前文约束；只在必要时澄清，依据新反馈调整方案。"),
    )
    for name, _, default, _ in rule_fields:
        key = f"workflow-director-{name}:{workspace}"
        st.session_state[key] = _field(workspace, key, default)
    render_prompt_library(prompt_library, workspace, "director.rules", save_field=save_field,
                          fields={"question_rules": f"workflow-director-question-rules:{workspace}",
                                  "answer_rules": f"workflow-director-answer-rules:{workspace}"},
                          defaults={"question_rules": DEFAULT_QUESTION_RULES, "answer_rules": DEFAULT_ANSWER_RULES},
                          label="我的规则模板", save_label="保存规则")
    for name, label, _, placeholder in rule_fields:
        key = f"workflow-director-{name}:{workspace}"
        st.text_area(label, key=key, height=100, max_chars=12000, placeholder=placeholder,
                     on_change=save_field, args=(workspace, key))
    st.caption("指令随任务固定；规划、生成与评审共享对话目标和实际进展。可恢复默认，也可保存个人模板。")
