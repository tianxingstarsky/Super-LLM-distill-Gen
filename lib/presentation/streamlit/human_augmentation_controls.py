"""Persistent question and answer designs for the human working window."""
from __future__ import annotations

from copy import deepcopy

import streamlit as st

from lib.domain.human_augmentation import MAX_HUMAN_DESIGN_CHARS, MAX_HUMAN_SEEDS, parse_human_seeds
from lib.presentation.streamlit.i18n import UntranslatedText


def field(workspace, name, default):
    key = f"workflow-human-{name}:{workspace}"
    return st.session_state.get(f"workflow-form-draft:{workspace}", {}).get(
        key, st.session_state.get(key, default))


def human_snapshot(workspace):
    if not field(workspace, "enabled", False):
        return {"enabled": False}
    return {"enabled": True, "seeds": deepcopy(field(workspace, "seeds", [])),
            "question_requirements": field(workspace, "question-requirements", ""),
            "answer_requirements": field(workspace, "answer-requirements", "")}


def _save_seeds(workspace, seeds, save_field):
    key = f"workflow-human-seeds:{workspace}"
    st.session_state[key] = seeds
    save_field(workspace, key)


def _edit_seed(workspace, index, save_field):
    seeds = deepcopy(field(workspace, "seeds", []))
    seeds[index] = {name: st.session_state[f"human-seed:{workspace}:{index}:{name}"]
                    for name in ("question", "answer", "question_requirements", "answer_requirements")}
    _save_seeds(workspace, seeds, save_field)


def _change_enabled(workspace, save_field):
    key = f"workflow-human-enabled:{workspace}"
    save_field(workspace, key)
    mode_key = f"workflow-creation-mode:{workspace}"
    st.session_state[mode_key] = "人工问答增强" if st.session_state[key] else "自动生成"
    save_field(workspace, mode_key)


def _add_seed(workspace, save_field):
    seeds = deepcopy(field(workspace, "seeds", []))
    if len(seeds) < MAX_HUMAN_SEEDS:
        seeds.append({"question": "", "answer": "", "question_requirements": "", "answer_requirements": ""})
        _save_seeds(workspace, seeds, save_field)
        st.session_state[f"human-seed-selection:{workspace}"] = len(seeds) - 1


def _remove_seed(workspace, index, save_field):
    seeds = deepcopy(field(workspace, "seeds", []))
    if len(seeds) > 1:
        seeds.pop(index)
        _save_seeds(workspace, seeds, save_field)
        st.session_state[f"human-seed-selection:{workspace}"] = min(index, len(seeds) - 1)


def _import_seeds(workspace, save_field):
    error_key = f"human-seed-import-error:{workspace}"
    try:
        upload = st.session_state.get(f"human-seed-upload:{workspace}")
        if upload is not None and upload.size > MAX_HUMAN_DESIGN_CHARS * 4:
            raise ValueError("human_augmentation_size_limit")
        raw = (upload.getvalue().decode("utf-8-sig") if upload is not None
               else st.session_state.get(f"human-seed-json:{workspace}", ""))
        imported = parse_human_seeds(raw)
        _save_seeds(workspace, [{key: value for key, value in seed.items() if key != "id"}
                               for seed in imported], save_field)
        st.session_state[f"human-seed-selection:{workspace}"] = 0
        st.session_state.pop(error_key, None)
    except (UnicodeError, ValueError):
        st.session_state[error_key] = True


def render_human_designs(workspace, *, save_field, show_toggle=False):
    key = f"workflow-human-enabled:{workspace}"
    st.session_state[key] = field(workspace, "enabled", False)
    enabled = (st.toggle("人工问答增强", key=key, on_change=_change_enabled, args=(workspace, save_field),
        help="人同时设计问题和参考答案。模型生成不同表达，再核对原意、事实和两者的设计要求。")
        if show_toggle else True)
    if not enabled:
        return
    seeds = deepcopy(field(workspace, "seeds", []))
    if not seeds:
        _add_seed(workspace, save_field)
        seeds = field(workspace, "seeds", [])
    selector, add, remove = st.columns([3, 1, 1], gap="small", vertical_alignment="bottom")
    selection_key = f"human-seed-selection:{workspace}"
    if st.session_state.get(selection_key, 0) not in range(len(seeds)):
        st.session_state[selection_key] = 0
    language = st.session_state.get("ui_language", "zh")
    with selector:
        index = st.selectbox("人工设计", range(len(seeds)), key=selection_key,
            format_func=lambda value: f"Design {value + 1} / {len(seeds)}" if language == "en" else f"设计 {value + 1} / {len(seeds)}",
            help="保留问题意图和答案事实，生成不同话术；达到质量或多样性边界即可停止，不强行凑数。")
    with add:
        st.button("添加", key=f"human-seed-add:{workspace}", on_click=_add_seed,
                  args=(workspace, save_field), disabled=len(seeds) >= MAX_HUMAN_SEEDS, width="stretch")
    with remove:
        st.button("移除", key=f"human-seed-remove:{workspace}", on_click=_remove_seed,
                  args=(workspace, index, save_field), disabled=len(seeds) < 2, width="stretch")
    for row in (
        (("question", "人工设计的问题", 12000, 110),
         ("answer", "人工设计的参考答案", 12000, 110)),
        (("question_requirements", "问题的设计要求", 6000, 90),
         ("answer_requirements", "答案的设计要求", 6000, 90)),
    ):
        for column, (name, label, maximum, height) in zip(st.columns(2, gap="small"), row):
            widget_key = f"human-seed:{workspace}:{index}:{name}"
            st.session_state[widget_key] = seeds[index].get(name, "")
            with column:
                help_text = ("人工参考答案仍需核对；只用人工设计时会标记为用户提供，不宣称已经得到独立事实验证。" if name == "answer" else
                    "设计要求可约束语气、难度、表达方式和允许变化的范围；不会作为训练答案直接导出。" if name.endswith("requirements") else None)
                st.text_area(label, key=widget_key, height=height, max_chars=maximum,
                             on_change=_edit_seed, args=(workspace, index, save_field), help=help_text)
    if len(seeds) > 1 or field(workspace, "question-requirements", "") or field(workspace, "answer-requirements", ""):
        with st.expander("共用设计要求"):
            for name, label in (("question-requirements", "共用问题要求"), ("answer-requirements", "共用答案要求")):
                key = f"workflow-human-{name}:{workspace}"
                st.session_state[key] = field(workspace, name, "")
                st.text_area(label, key=key, max_chars=6000, on_change=save_field, args=(workspace, key))
    with st.popover("导入人工设计", width="stretch"):
        st.caption("支持 JSON、JSONL，每项包含 question 与 answer，也可附带两者的设计要求。导入会替换当前设计。")
        st.file_uploader("人工设计文件", type=["json", "jsonl"], key=f"human-seed-upload:{workspace}")
        st.text_area("或粘贴 JSON / JSONL", key=f"human-seed-json:{workspace}", max_chars=MAX_HUMAN_DESIGN_CHARS)
        st.button("应用导入", key=f"human-seed-import:{workspace}", width="stretch",
                  on_click=_import_seeds, args=(workspace, save_field))
        if st.session_state.get(f"human-seed-import-error:{workspace}"):
            st.error("导入失败，请检查问答字段、重复项和大小限制；当前设计已保留。")
