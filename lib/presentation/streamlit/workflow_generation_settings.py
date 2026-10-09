"""Node-local authored reasoning settings and optional reasoning cleanup."""
from __future__ import annotations

import html
from pathlib import Path

import streamlit as st

from lib.domain.workflow_generation import (
    GENERATION_STYLES, MAX_GENERATION_INSTRUCTION_CHARS, STYLE_PRESETS,
    validate_node_generation,
)
from lib.domain.reasoning_trim import (validate_reasoning_trim,
                                       MAX_TRIM_INSTRUCTION_CHARS, MAX_TRIM_PROMPT_CHARS)
from lib.presentation.streamlit.i18n import translate, translate_label


STYLE_LABELS = {"default": "遵循任务", "concise": "简洁工程", "structured": "分步教学",
                "skeptical": "审慎核验", "reflective": "反思校验", "mixed": "多风格混合",
                "custom": "自定义风格"}
TRIM_LABELS = {"leakage": "提示词与包装清理", "concise": "精简重复推理", "custom": "自定义修剪模板"}
TRIM_DESCRIPTIONS = {
    "leakage": "清理暴露的系统或开发者指令、检索包装与不必要的原文复述；保留任务所需事实、引用和关键推导。",
    "concise": "在清理提示词暴露的同时精简重复陈述与无关旁支；保留可核验的依据、推导和最终答案。",
}
_MAX_TEMPLATE_BYTES = 64 * 1024
_MAX_TEMPLATE_CHARS = MAX_TRIM_PROMPT_CHARS


def _field(workspace: str, key: str, default):
    draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    # Callbacks save edits before the next render. The form draft remains
    # authoritative when Streamlit resets a hidden widget to its old default.
    return draft.get(key, st.session_state.get(key, default))


def _restore_field(workspace: str, key: str, default):
    # This is called only before each widget is instantiated on this render.
    st.session_state[key] = _field(workspace, key, default)


def generation_snapshot(workspace: str, nodes) -> dict:
    """Read active nodes only, including settings hidden by node selection."""
    return {node: {
        "enabled": _field(workspace, f"workflow-generation-enabled:{workspace}:{node}", False),
        "style": _field(workspace, f"workflow-generation-style:{workspace}:{node}",
                        "structured" if node == "cot" else "default"),
        "instruction": _field(workspace, f"workflow-generation-instruction:{workspace}:{node}", ""),
    } for node in nodes if node in {"sft", "cot"}}


def generation_issues(configuration: dict) -> list[str]:
    invalid = []
    for node, settings in configuration.items():
        try:
            validate_node_generation({node: settings})
        except ValueError:
            invalid.append(node)
    return invalid


def render_generation_settings(node: str, workspace: str, *, save_field) -> None:
    """Keep generation style separate from training-file output formatting."""
    prefix = f"{workspace}:{node}"
    enabled_key = f"workflow-generation-enabled:{prefix}"
    style_key = f"workflow-generation-style:{prefix}"
    instruction_key = f"workflow-generation-instruction:{prefix}"
    _restore_field(workspace, enabled_key, False)
    _restore_field(workspace, style_key, "structured" if node == "cot" else "default")
    _restore_field(workspace, instruction_key, "")
    language = st.session_state.get("ui_language", "zh")
    with st.container(key=f"workbench-generation:{node}"):
        enabled = st.toggle("启用风格化生成", key=enabled_key,
                            on_change=save_field, args=(workspace, enabled_key),
                            help="开启后，教师按风格提示词撰写推理与答案；关闭后使用普通蒸馏。")
        style = st.selectbox("生成风格", GENERATION_STYLES, key=style_key, disabled=not enabled,
                             format_func=lambda value: translate_label(STYLE_LABELS[value], language),
                             on_change=save_field, args=(workspace, style_key))
        if enabled:
            st.file_uploader("导入风格提示词", type=["txt", "md"], max_upload_size=1,
                             key=f"workflow-generation-upload:{prefix}",
                             on_change=_import_generation_instruction, args=(workspace, node, save_field),
                             help="UTF-8 文本，最多 64 KiB / 4,000 字符；导入后切换为自定义风格，可继续编辑。")
            if error := st.session_state.get(f"workflow-generation-upload-error:{prefix}"):
                st.error(error)
        st.text_area("附加风格指令", key=instruction_key, height=95, disabled=not enabled,
                     max_chars=MAX_GENERATION_INSTRUCTION_CHARS,
                     placeholder="例如：先核对条件，再给出简短推导；使用自然中文，避免机械编号。",
                     help="预设上的补充要求；自定义风格开启时必须填写。",
                     on_change=save_field, args=(workspace, instruction_key))
        description = (STYLE_PRESETS.get(style) or (
            "按样本稳定轮换简洁、教学、核验和反思风格，重试时保持一致。" if style == "mixed"
            else "根据附加指令组织推理与答案。"))
        status = "提示词驱动" if enabled else "普通蒸馏"
        st.html('<div class="df-generation-preview' + ('' if enabled else ' is-disabled') + '">'
                '<div><i aria-hidden="true">◈</i><strong>' + html.escape(translate(status, language)) +
                '</strong><span>' + html.escape(translate(STYLE_LABELS[style], language)) + '</span></div>'
                '<p>' + html.escape(translate(description if enabled else
                    "可保留模型输出的推理；SFT 训练文件格式可单独设置。", language)) + '</p></div>')
        if enabled:
            st.caption("教师根据提示词生成显式推理与答案，正确性与风格分别检查。")


def _import_generation_instruction(workspace: str, node: str, save_field) -> None:
    """Import plain UTF-8 instructions before the node widgets are rebuilt."""
    prefix = f"{workspace}:{node}"
    upload = st.session_state.get(f"workflow-generation-upload:{prefix}")
    error_key = f"workflow-generation-upload-error:{prefix}"
    st.session_state.pop(error_key, None)
    if upload is None:
        return
    try:
        if Path(upload.name).suffix.lower() not in {".txt", ".md"}:
            raise ValueError("请选择 TXT 或 Markdown 提示词文件。")
        raw = upload.getvalue()
        if len(raw) > _MAX_TEMPLATE_BYTES:
            raise ValueError("提示词文件最多 64 KiB，请精简后重新上传。")
        content = raw.decode("utf-8-sig")
        if not content.strip() or "\x00" in content:
            raise ValueError("提示词文件为空或包含无效字符，请使用 UTF-8 文本。")
        if len(content) > MAX_GENERATION_INSTRUCTION_CHARS:
            raise ValueError("风格提示词最多 4,000 字符，请精简后重新上传。")
    except UnicodeDecodeError:
        st.session_state[error_key] = "提示词文件需要 UTF-8 编码，请转换后重新上传。"
        return
    except (OSError, ValueError) as error:
        st.session_state[error_key] = str(error)
        return
    instruction_key = f"workflow-generation-instruction:{prefix}"
    style_key = f"workflow-generation-style:{prefix}"
    st.session_state[instruction_key] = content
    st.session_state[style_key] = "custom"
    save_field(workspace, instruction_key)
    save_field(workspace, style_key)


def trim_snapshot(workspace: str, *, eligible: bool = True) -> dict:
    return {
        "enabled": bool(eligible and _field(workspace, f"workflow-trim-enabled:{workspace}", False)),
        "template": _field(workspace, f"workflow-trim-template:{workspace}", "leakage"),
        "instruction": _field(workspace, f"workflow-trim-instruction:{workspace}", ""),
        "custom_prompt": _field(workspace, f"workflow-trim-prompt:{workspace}", ""),
    }


def render_trim_toggle(workspace: str, targets, *, save_field) -> dict:
    eligible = bool(set(targets).intersection({"sft", "cot"}))
    key = f"workflow-trim-enabled:{workspace}"
    _restore_field(workspace, key, False)
    enabled = st.toggle("加入推理链修剪", key=key, disabled=not eligible,
                        on_change=save_field, args=(workspace, key),
                        help="在打包前增加独立节点，只处理 SFT / CoT 推理内容，保留最终答案。")
    if eligible and enabled:
        st.caption("已加入修剪节点：清理提示词暴露、包装与重复表述，保留必要知识和答案。")
    elif not eligible:
        st.caption("选择 SFT 或 CoT 目标后，可加入推理链修剪。")
    return trim_snapshot(workspace, eligible=eligible)


def _import_trim_template(workspace: str, save_field) -> None:
    """Uploader callback runs before the editable prompt widget is created."""
    upload = st.session_state.get(f"workflow-trim-upload:{workspace}")
    error_key = f"workflow-trim-upload-error:{workspace}"
    st.session_state.pop(error_key, None)
    if upload is None:
        return
    try:
        if Path(upload.name).suffix.lower() not in {".txt", ".md"}:
            raise ValueError("请选择 TXT 或 Markdown 提示词文件。")
        raw = upload.getvalue()
        if len(raw) > _MAX_TEMPLATE_BYTES:
            raise ValueError("提示词文件最多 64 KiB，请精简后重新上传。")
        content = raw.decode("utf-8-sig")
        if not content.strip() or "\x00" in content:
            raise ValueError("提示词文件为空或包含无效字符，请使用 UTF-8 文本。")
        if len(content) > _MAX_TEMPLATE_CHARS:
            raise ValueError("自定义修剪模板最多 16,000 字符，请精简后重新上传。")
    except UnicodeDecodeError:
        st.session_state[error_key] = "提示词文件需要 UTF-8 编码，请转换后重新上传。"
        return
    except (OSError, ValueError) as error:
        st.session_state[error_key] = str(error)
        return
    prompt_key = f"workflow-trim-prompt:{workspace}"
    template_key = f"workflow-trim-template:{workspace}"
    st.session_state[prompt_key] = content
    st.session_state[template_key] = "custom"
    save_field(workspace, prompt_key)
    save_field(workspace, template_key)


def render_trim_settings(workspace: str, *, save_field) -> None:
    template_key = f"workflow-trim-template:{workspace}"
    instruction_key = f"workflow-trim-instruction:{workspace}"
    prompt_key = f"workflow-trim-prompt:{workspace}"
    for key, default in ((template_key, "leakage"), (instruction_key, ""), (prompt_key, "")):
        _restore_field(workspace, key, default)
    language = st.session_state.get("ui_language", "zh")
    st.caption("仅修剪推理字段，最终答案保持原样；质检不通过的改写会隔离。")
    selected = st.selectbox("修剪模板", tuple(TRIM_LABELS), key=template_key,
                            format_func=lambda value: translate_label(TRIM_LABELS[value], language),
                            on_change=save_field, args=(workspace, template_key))
    st.text_area("附加修剪指令", key=instruction_key, height=85,
                 max_chars=MAX_TRIM_INSTRUCTION_CHARS,
                 placeholder="例如：保留引用与公式，去掉重复的资料包装说明。",
                 on_change=save_field, args=(workspace, instruction_key))
    if selected == "custom":
        st.file_uploader("导入修剪提示词", type=["txt", "md"], max_upload_size=1,
                         key=f"workflow-trim-upload:{workspace}",
                         on_change=_import_trim_template, args=(workspace, save_field),
                         help="UTF-8 文本，最多 64 KiB / 16,000 字符；上传后可直接编辑。")
        st.text_area("自定义修剪模板", key=prompt_key, height=180, max_chars=_MAX_TEMPLATE_CHARS,
                     on_change=save_field, args=(workspace, prompt_key),
                     help="明确哪些内容需要清理，并要求返回完整修剪后的推理；任务会另行提供推理与答案。")
    else:
        st.html('<div class="df-generation-preview"><div><i aria-hidden="true">✂</i><strong>'
                + html.escape(translate("模板要求", language)) + '</strong></div><p>'
                + html.escape(translate(TRIM_DESCRIPTIONS[selected], language)) + '</p></div>')
    if error := st.session_state.get(f"workflow-trim-upload-error:{workspace}"):
        st.error(error)


def trim_has_issue(settings: dict) -> bool:
    try:
        validate_reasoning_trim(settings)
    except ValueError:
        return True
    return False


def generation_summary(configuration: dict, language: str) -> str:
    parts = []
    for node, settings in configuration.items():
        label = translate(STYLE_LABELS.get(settings.get("style"), "遵循任务"), language)
        if settings.get("enabled"):
            detail = label + (" + " + translate("附加指令", language) if settings.get("instruction", "").strip() else "")
        else:
            detail = translate("普通蒸馏", language)
        parts.append(f"{node.upper()} {detail}")
    return " · ".join(parts)
