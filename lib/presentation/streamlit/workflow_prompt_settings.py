"""Edit and inspect prompts in the workflow node that actually uses them."""
from __future__ import annotations

import html
from pathlib import Path

import streamlit as st

from lib.domain.workflow_node_prompts import (
    MAX_NODE_PROMPT_CHARS, active_node_prompt_ids, builtin_node_prompt,
)
from lib.presentation.streamlit.i18n import translate, translate_label
from lib.presentation.streamlit.prompt_library_controls import (
    render_prompt_library, clear_prompt_template_selection,
)


PROMPT_LABELS = {
    "workflow.plan": "任务规划",
    "workflow.document_vision": "文档与图片识别",
    "workflow.corpus": "语料生成",
    "workflow.sft": "问答生成",
    "workflow.sft_styled": "风格化问答生成",
    "workflow.jev_score": "质量评审",
    "workflow.cot_generate": "风格化推理生成",
    "workflow.rationale_check": "推理正确性核验",
    "workflow.style_check": "风格符合度核验",
    "workflow.multiturn_user": "下一轮提问",
    "workflow.multiturn_assistant": "本轮回答",
    "workflow.multiturn_consistency": "整段一致性核验",
    "workflow.alternative": "独立偏好候选",
    "workflow.judge": "偏好一致性复核",
    "workflow.trim": "推理修剪执行",
    "workflow.trim_check": "语义保留核验",
    "workflow.trim_rules_check": "修剪规则核验",
    "workflow.package_review": "打包前 AI 评审",
    "workflow.qa_director": "问答任务调度",
    "workflow.sft_directed": "按指导任务生成问答",
    "workflow.sft_directed_check": "问答规则与线索核验",
    "workflow.multiturn_directed_check": "整段问答规则核验",
    "workflow.preference_directed_check": "偏好优选契约检查",
    "workflow.cot_directed_check": "CoT 问答契约检查",
    "workflow.trim_directed_check": "修剪后问答契约检查",
}
_MAX_UPLOAD_BYTES = 128 * 1024


def _prompt_key(workspace: str, node: str, prompt_id: str) -> str:
    return f"workflow-node-prompt:{workspace}:{node}:{prompt_id}"


def _field(workspace: str, key: str, default):
    draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    return draft.get(key, st.session_state.get(key, default))


def node_prompt_snapshot(workspace: str, nodes, source_mode: str, *, node_generation=None, package_review=None,
                         qa_director=None) -> dict:
    """Submit only active overrides; inactive edits stay in the local draft."""
    result = {}
    for node in nodes:
        for prompt_id in active_node_prompt_ids(node, source_mode, node_generation=node_generation,
                                                package_review=package_review, qa_director=qa_director):
            default = builtin_node_prompt(prompt_id)
            body = _field(workspace, _prompt_key(workspace, node, prompt_id), default)
            if body != default:
                result.setdefault(node, {})[prompt_id] = body
    return result


def node_prompt_has_issue(configuration: dict) -> bool:
    return any(not isinstance(body, str) or not body.strip() or "\x00" in body
               or len(body) > MAX_NODE_PROMPT_CHARS
               for prompts in configuration.values() for body in prompts.values())


def _restore_prompt(workspace: str, node: str, prompt_id: str, save_field) -> None:
    key = _prompt_key(workspace, node, prompt_id)
    st.session_state[key] = builtin_node_prompt(prompt_id)
    clear_prompt_template_selection(workspace, f"node:{node}:{prompt_id}")
    st.session_state.pop(f"workflow-node-prompt-upload-error:{workspace}:{node}:{prompt_id}", None)
    save_field(workspace, key)


def _import_prompt(workspace: str, node: str, prompt_id: str, save_field) -> None:
    prefix = f"{workspace}:{node}:{prompt_id}"
    error_key = f"workflow-node-prompt-upload-error:{prefix}"
    st.session_state.pop(error_key, None)
    uploaded = st.session_state.get(f"workflow-node-prompt-upload:{prefix}")
    if uploaded is None:
        return
    try:
        if Path(uploaded.name).suffix.casefold() not in {".txt", ".md"}:
            raise ValueError("请选择 TXT 或 Markdown 提示词文件。")
        raw = uploaded.getvalue()
        if len(raw) > _MAX_UPLOAD_BYTES:
            raise ValueError("节点提示词文件最多 128 KiB，请精简后重新上传。")
        body = raw.decode("utf-8-sig")
        if not body.strip() or "\x00" in body:
            raise ValueError("提示词文件为空或包含无效字符，请使用 UTF-8 文本。")
        if len(body) > MAX_NODE_PROMPT_CHARS:
            raise ValueError("节点提示词最多 32,768 字符，请精简后重新上传。")
    except UnicodeDecodeError:
        st.session_state[error_key] = "提示词文件需要 UTF-8 编码，请转换后重新上传。"
        return
    except (OSError, ValueError) as error:
        st.session_state[error_key] = str(error)
        return
    key = _prompt_key(workspace, node, prompt_id)
    st.session_state[key] = body
    save_field(workspace, key)


def render_node_prompts(node: str, source_mode: str, workspace: str, *, save_field,
                        node_generation=None, package_review=None, qa_director=None,
                        prompt_library=None) -> None:
    """Show the active processing prompt beside this node's model settings."""
    prompts = active_node_prompt_ids(node, source_mode, node_generation=node_generation,
                                    package_review=package_review, qa_director=qa_director)
    if not prompts:
        return
    language = st.session_state.get("ui_language", "zh")
    selection_key = f"workflow-node-prompt-selection:{workspace}:{node}"
    if st.session_state.get(selection_key) not in prompts:
        st.session_state[selection_key] = prompts[0]
    with st.container(key=f"workbench-node-prompts:{node}"):
        st.html('<p class="df-node-prompt-heading"><strong>'
                + html.escape(translate("节点提示词", language)) + '</strong></p>')
        st.caption("完整模板决定处理步骤与输出字段；风格指令只补充表达要求，指导员规则只约束问答任务。开始运行后全部固定。")
        step, reset = st.columns([1.65, 1], gap="small", vertical_alignment="bottom")
        with step:
            prompt_id = st.selectbox("处理步骤", prompts, key=selection_key,
                                    format_func=lambda value: translate_label(PROMPT_LABELS.get(value, value), language))
        body_key = _prompt_key(workspace, node, prompt_id)
        default = builtin_node_prompt(prompt_id)
        st.session_state[body_key] = _field(workspace, body_key, default)
        custom = st.session_state[body_key] != default
        with reset:
            st.button("恢复默认模板", key=f"workflow-node-prompt-reset:{workspace}:{node}:{prompt_id}",
                      disabled=not custom, width="stretch", on_click=_restore_prompt,
                      args=(workspace, node, prompt_id, save_field),
                      help="恢复当前步骤的内置模板，保留已保存的个人模板。")
        render_prompt_library(prompt_library, workspace, f"node:{node}:{prompt_id}",
                              fields={"text": body_key}, save_field=save_field,
                              label="我的提示词模板", save_label="保存提示词")
        wide = st.session_state.get(f"canvas-wide:setup-canvas:{workspace}", False)
        st.text_area("提示词正文", key=body_key, height=320 if wide else 164, max_chars=MAX_NODE_PROMPT_CHARS,
                     on_change=save_field, args=(workspace, body_key),
                     help="可直接编辑或导入完整指令；输入资料会自动附加。请保留本步骤要求的输出字段。")
        with st.popover("导入 TXT / Markdown", width="stretch"):
            st.file_uploader("导入当前步骤提示词", type=["txt", "md"], max_upload_size=1,
                             key=f"workflow-node-prompt-upload:{workspace}:{node}:{prompt_id}",
                             on_change=_import_prompt, args=(workspace, node, prompt_id, save_field),
                             help="UTF-8 文本，最多 128 KiB / 32,768 字符；导入后可继续编辑。")
        if error := st.session_state.get(f"workflow-node-prompt-upload-error:{workspace}:{node}:{prompt_id}"):
            st.error(error)
        st.caption("已自定义 · 仅当前步骤" if custom else "使用内置提示词 · 可直接编辑")


def render_run_node_prompts(node: str, recipe: dict, run_id: str) -> None:
    """Display the immutable prompt bodies that a started run actually uses."""
    pinned = (recipe.get("node_prompt_templates") or {}).get(node, {})
    if not isinstance(pinned, dict) or not pinned:
        return
    mode = ("多模态文档" if (recipe.get("document_parser") or {}).get("mode") == "vision"
            else "文档资料" if recipe.get("sources") else "开放需求")
    active = active_node_prompt_ids(node, mode, node_generation=recipe.get("node_generation"),
                                   package_review=recipe.get("package_review"), qa_director=recipe.get("qa_director"))
    prompts = {key: pinned[key] for key in active if key in pinned}
    if not prompts:
        return
    language = st.session_state.get("ui_language", "zh")
    st.html('<p class="df-node-prompt-heading"><strong>'
            + html.escape(translate("节点提示词", language)) + '</strong></p>')
    st.caption("本次任务已固定的提示词；复制为新草稿后可以修改。")
    prompt_id = st.selectbox("处理步骤", list(prompts),
                            key=f"workflow-run-prompt-selection:{run_id}:{node}",
                            format_func=lambda value: translate_label(PROMPT_LABELS.get(value, value), language))
    st.text_area("提示词正文", value=prompts[prompt_id], height=164, disabled=True,
                 key=f"workflow-run-prompt-body:{run_id}:{node}:{prompt_id}")
