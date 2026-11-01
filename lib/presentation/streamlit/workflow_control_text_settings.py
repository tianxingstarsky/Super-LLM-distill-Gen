"""Edit node-local training text adapters without changing native reasoning.

Edits stay in a node-local working copy until explicitly committed. The saved
draft and its previous version are persisted together; task snapshots never
read incomplete widget state.
"""
from __future__ import annotations

from copy import deepcopy
import html

import streamlit as st

from lib.contexts.datasets.api import (
    default_control_text_config,
    preview_control_text,
    validate_control_text_config,
)
from .i18n import UntranslatedText


SUPPORTED_NODES = {"sft", "multiturn", "cot"}


def _text(zh: str, en: str) -> UntranslatedText:
    return UntranslatedText(en if st.session_state.get("ui_language", "zh") == "en" else zh)


def _config_key(workspace: str, node: str) -> str:
    return f"workflow-control-text:{workspace}:{node}"


def _configuration(workspace: str, node: str) -> dict:
    key = _config_key(workspace, node)
    saved = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    if key in saved:
        return deepcopy(saved[key])
    if key in st.session_state:
        return deepcopy(st.session_state[key])
    configuration = default_control_text_config()
    configuration["enabled"] = False
    return configuration


def _editor_key(workspace, node):
    return f"_control-text-editor:{workspace}:{node}"


def _previous_key(workspace, node):
    return f"workflow-control-text-previous:{workspace}:{node}"


def _begin_edit(workspace, node, source="saved"):
    saved = _configuration(workspace, node)
    if source == "previous":
        configuration = deepcopy(st.session_state.get(f"workflow-form-draft:{workspace}", {})[
            _previous_key(workspace, node)])
    elif source == "defaults":
        configuration = default_control_text_config()
        configuration["enabled"] = False
    else:
        configuration = deepcopy(saved)
        # Only the editable copy upgrades. Frozen tasks and cancel stay exact.
        configuration["schema"] = default_control_text_config()["schema"]
    st.session_state[_editor_key(workspace, node)] = {"base": saved, "configuration": configuration}
    st.session_state[f"_control-text-selected:{workspace}:{node}"] = 0
    for key in list(st.session_state):
        if key.startswith(f"_control-text-target:{workspace}:{node}:"):
            del st.session_state[key]


def _cancel_edit(workspace, node):
    st.session_state.pop(_editor_key(workspace, node), None)


def _validate_edit(configuration):
    # An off switch is not permission to save a broken enabled rule.
    validate_control_text_config({**configuration, "enabled": True})
    return validate_control_text_config(configuration)


def _commit_edit(workspace, node, save_fields):
    editor = st.session_state[_editor_key(workspace, node)]
    if _configuration(workspace, node) != editor["base"]:
        st.session_state[f"_control-text-notice:{workspace}:{node}"] = "conflict"
        return
    try:
        configuration = _validate_edit(editor["configuration"])
    except ValueError:
        return
    if configuration == editor["base"]:
        _cancel_edit(workspace, node)
        return
    changes = {_config_key(workspace, node): configuration,
               _previous_key(workspace, node): deepcopy(editor["base"])}
    if save_fields(workspace, changes):
        _cancel_edit(workspace, node)
        st.session_state[f"_control-text-notice:{workspace}:{node}"] = "saved"
    else:
        st.session_state[f"_control-text-notice:{workspace}:{node}"] = "failed"


def _change_field(workspace, node, index, field, widget_key):
    configuration = st.session_state[_editor_key(workspace, node)]["configuration"]
    value = st.session_state[widget_key]
    if index is None:
        configuration[field] = value
    else:
        rule = configuration["rules"][index]
        if field == "matches":
            value = [line for line in value.splitlines() if line.strip()]
            if value != rule.get("matches", []):
                # An edited literal no longer proves the template's old observation.
                # Receipts still retain the rule ID and actual matched source text.
                rule.pop("field", None)
                rule.pop("observed_value", None)
        elif field == "mode":
            target_key = f"_control-text-target:{workspace}:{node}:{index}"
            if value == "mark":
                st.session_state[target_key] = rule.get("replacement") or ""
                value = None
            else:
                value = st.session_state.get(target_key, rule.get("replacement") or "")
            field = "replacement"
        rule[field] = value
    # Widget edits intentionally never touch the committed workflow draft.


def _change_selection(workspace, node, widget_key):
    st.session_state[f"_control-text-selected:{workspace}:{node}"] = st.session_state[widget_key]


def _change_rules(workspace, node, action, index):
    configuration = st.session_state[_editor_key(workspace, node)]["configuration"]
    rules = configuration["rules"]
    if action == "restore":
        configuration.update(default_control_text_config())
        configuration["enabled"] = False
        index = 0
    elif action == "add":
        number = len(rules) + 1
        while any(rule.get("id") == f"custom-rule-{number}" for rule in rules):
            number += 1
        rules.append({"id": f"custom-rule-{number}", "enabled": True, "matches": [],
                      "marker": f"[[CUSTOM_CONTROL_{number}]]", "replacement": None})
        index = len(rules) - 1
    else:
        rules.pop(index)
        index = max(0, index - 1)
    for key in list(st.session_state):
        if key.startswith(f"_control-text-target:{workspace}:{node}:"):
            del st.session_state[key]
    st.session_state[f"_control-text-selected:{workspace}:{node}"] = index


def _error_message(error: ValueError) -> UntranslatedText:
    messages = {
        "control_text_rule_conflict": (
            "规则有冲突：请让已启用规则的名称、匹配文字和标记各不相同，标记不要包含另一条匹配文字。也可先关闭冲突规则。",
            "Rules conflict. Give enabled rules distinct names, matches and markers. A marker must not contain another match. You can disable a conflicting rule."),
        "control_text_replacement_cascade": (
            "目标文字包含匹配文字或标记，可能再次触发规则。请换用普通文字，或选择「仅标记」。",
            "Target text contains a match or marker. Use ordinary text, or choose Mark only."),
        "invalid_control_text_rule": (
            "请填写规则名称和标记；「替换文字」模式还需填写目标文字。输入已保留，也可以关闭这条规则后再完善。",
            "Enter a rule name and marker. Replace text also needs nonempty target text. Unsaved edits do not affect the saved rules."),
    }
    return _text(*messages.get(str(error), (
        "配置暂不能使用。请检查规则字段，或恢复默认规则；当前输入已保留。",
        "This configuration cannot be used yet. Check the rule fields or restore defaults. Your edits are retained.")))


def control_text_snapshot(workspace: str, nodes) -> dict:
    """Freeze explicitly saved node choices, rejecting active invalid rules."""
    saved = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    snapshot = {}
    for node in nodes:
        key = _config_key(workspace, node)
        if node in SUPPORTED_NODES and key in saved:
            try:
                snapshot[node] = validate_control_text_config(saved[key])
            except ValueError as error:
                raise ValueError(f"{node}: {_error_message(error)}") from error
    return snapshot


def _rule_label(rule: dict, index: int) -> UntranslatedText:
    names = {
        "deepseek-output-verbosity-5": ("DeepSeek · 详尽程度 5", "DeepSeek · verbosity 5"),
        "reasoning-effort-reference": ("思考强度标记", "Thinking intensity marker"),
    }
    label = (_text(*names[rule["id"]]) if rule.get("id") in names else
             rule.get("id") or _text("未命名规则", "Unnamed rule"))
    status = "" if rule.get("enabled", True) else _text(" · 已停用", " · disabled")
    return UntranslatedText(f"{index + 1}. {label}{status}")


def render_control_text_settings(node, workspace, *, save_fields):
    """Separate read-only configuration, provisional edits and explicit commit."""
    if node not in SUPPORTED_NODES:
        return
    saved = _configuration(workspace, node)
    editor = st.session_state.get(_editor_key(workspace, node))
    editing = editor is not None
    configuration = editor["configuration"] if editing else saved
    prefix = f"control-text-ui:{workspace}:{node}"

    def field(kind, label, name, value, *, index=None, **kwargs):
        key = f"{prefix}:{index if index is not None else 'all'}:{name}"
        st.session_state[key] = value
        return getattr(st, kind)(label, key=key, on_change=_change_field,
                                 args=(workspace, node, index, name, key), **kwargs)

    st.caption(_text(
        "只调整训练读取时的文字，完整原生推理与答案保持原样。不会关闭模型思考，也不会再次调用模型。",
        "Adapt text when training data is read. Full native reasoning and answers remain intact. This neither disables thinking nor calls a model."))
    if editing:
        st.info(_text("正在编辑草稿 · 预览无误后保存；取消可回到已保存配置。",
                      "Editing a draft. Preview before saving; cancel to keep the saved configuration."))
    else:
        status = _text("已启用", "Enabled") if saved["enabled"] else _text("已关闭", "Off")
        st.markdown(_text(f"**当前配置 · {status}**", f"**Saved configuration · {status}**"))
        buttons = st.columns(2)
        buttons[0].button(_text("编辑规则", "Edit rules"), key=prefix + ":edit", type="primary",
                          on_click=_begin_edit, args=(workspace, node), use_container_width=True)
        previous = st.session_state.get(f"workflow-form-draft:{workspace}", {}).get(_previous_key(workspace, node))
        buttons[1].button(_text("恢复上一版…", "Restore previous…"), key=prefix + ":previous",
                          disabled=previous is None, on_click=_begin_edit, args=(workspace, node, "previous"),
                          use_container_width=True,
                          help=_text("先加载上一版预览，确认保存后才生效。", "Load the previous version for preview, then save to apply."))
        st.caption(_text("当前为只读 · 规则修改必须确认保存", "Read-only · Rule changes require Save"))
    notice = st.session_state.pop(f"_control-text-notice:{workspace}:{node}", None)
    if notice == "saved":
        st.success(_text("已保存；上一版配置可随时恢复。", "Saved. The previous configuration is available to restore."))
    elif notice == "failed":
        st.error(_text("保存失败，原配置仍生效。编辑内容已保留，可重试。", "Save failed. The prior configuration remains active; your edits are available to retry."))
    elif notice == "conflict":
        st.error(_text("已保存配置发生变化。请取消编辑，再重新打开，避免覆盖新配置。", "The saved configuration changed. Cancel and reopen to avoid overwriting it."))
    legacy = configuration.get("schema") != default_control_text_config()["schema"]
    if legacy:
        st.warning(_text("这是旧版占位符配置，可能替换匹配原文。点击编辑会准备保留原文的新版标注，保存后生效。",
                         "This legacy configuration may replace matched text with placeholders. Edit prepares the new annotation behavior; Save applies it."))
    field("toggle", _text("启用输出文字适配", "Enable output text adaptation"), "enabled",
          configuration["enabled"], help=_text(
              "关闭后，已有标记与匹配文字都原样保留；规则仍可编辑。设置只用于本节点的新任务。",
              "When off, matches and existing markers are left unchanged. Changes apply to new tasks for this node."), disabled=not editing)

    rules = configuration.get("rules", [])
    selected_key = f"_control-text-selected:{workspace}:{node}"
    index = min(st.session_state.get(selected_key, 0), max(0, len(rules) - 1))
    if rules:
        selection_key = prefix + ":selection"
        st.session_state[selection_key] = index
        labels = {selected: _rule_label(rule, selected) for selected, rule in enumerate(rules)}
        st.selectbox(_text("当前规则", "Current rule"), range(len(rules)), key=selection_key,
                     format_func=labels.__getitem__,
                     on_change=_change_selection, args=(workspace, node, selection_key))
    buttons = st.columns(3)
    for column, action, label in zip(buttons, ("add", "remove", "restore"), (
            _text("新增规则", "Add rule"), _text("删除规则", "Remove"), _text("恢复默认", "Reset rules"))):
        column.button(label, key=f"{prefix}:{action}", use_container_width=True,
                      disabled=not editing or action == "remove" and not rules,
                      on_click=_change_rules, args=(workspace, node, action, index),
                      help=_text("把默认规则载入草稿并关闭总开关，保存后才生效。", "Load defaults into the draft with adaptation off. Save to apply.")
                      if action == "restore" else None)
    if rules:
        rule = rules[index]
        field("toggle", _text("启用这条规则", "Enable this rule"), "enabled", rule.get("enabled", True), index=index, disabled=not editing)
        field("text_input", _text("规则名称 / ID", "Rule name / ID"), "id", rule.get("id", ""), index=index, disabled=not editing,
              help=_text("用于识别命中记录，例如 deepseek-verbosity。已启用规则的名称不能重复。",
                         "Identifies matches in the receipt, for example deepseek-verbosity. Enabled rule names must be unique."))
        field("text_area", _text("匹配文字 · 每行一项", "Exact matches · one per line"), "matches",
              "\n".join(rule.get("matches", [])), index=index, height=90, disabled=not editing,
              help=_text("逐字匹配，区分大小写，不是正则表达式。例：Desired oververbosity 5。留空时只处理已有标记。修改后清除模板的旧观测值，仅保留实际匹配文字和规则名称用于追溯。",
                         "Case-sensitive literal matches, not regular expressions. Example: Desired oververbosity 5. Leave empty to adapt existing markers only. Editing clears the template's old observation; receipts still retain the actual match and rule name."))
        field("text_input", _text("标记名称", "Annotation label"), "marker", rule.get("marker", ""), index=index, disabled=not editing,
              help=_text("仅标记时记录命中位置及此名称，正文保留原词句；它不是替换结果。",
                         "Mark only records this label and the matched span separately. The original words remain in the text."))
        mode = "mark" if rule.get("replacement") is None else "replace"
        modes = {"mark": (_text("占位符（旧版）", "Placeholder (legacy)") if legacy else
                          _text("仅标记 · 保留原文", "Mark only · keep original")),
                 "replace": _text("替换文字", "Replace text")}
        field("radio", _text("处理方式", "Action"), "mode", mode, index=index,
              options=("mark", "replace"), horizontal=True,
              format_func=modes.__getitem__, disabled=not editing)
        if mode == "replace":
            field("text_area", _text("目标文字", "Target text"), "replacement", rule.get("replacement", ""),
                  index=index, height=90, disabled=not editing, help=_text(
                      "例：思考已开启。不能留空删除文字；这不会修改真实模型调用的思考强度。",
                      "Example: Thinking is enabled. Empty deletion is not allowed. This does not change the model request's actual thinking intensity."))
    else:
        st.caption(_text("尚无规则。新增一条，或恢复 DeepSeek 默认模板。", "No rules yet. Add a rule or restore the DeepSeek template."))
    valid = True
    try:
        (_validate_edit if editing else validate_control_text_config)(configuration)
    except ValueError as error:
        st.error(_error_message(error))
        valid = False
    if editing:
        actions = st.columns(2)
        actions[0].button(_text("保存配置", "Save configuration"), key=prefix + ":save", type="primary",
                          disabled=not valid, on_click=_commit_edit, args=(workspace, node, save_fields),
                          use_container_width=True)
        actions[1].button(_text("取消修改", "Cancel changes"), key=prefix + ":cancel",
                          on_click=_cancel_edit, args=(workspace, node), use_container_width=True)
    if not valid:
        return

    st.divider()
    st.markdown(_text("**本地预览**", "**Local preview**"))
    preview_key = prefix + ":preview"
    sample = "Desired oververbosity 5. I will check the evidence before answering."
    original_text = st.text_area(_text("待测试原文", "Text to test"), value=sample, height=90, key=preview_key,
                                 help=_text("粘贴一段原生推理，只在本地预览，不保存为训练样本。", "Paste native reasoning for a local preview. It is not saved as a training sample."))
    preview = preview_control_text(configuration, original_text)
    st.caption(_text(f"命中 {preview['match_count']} 处 · 此处仅预览，尚未保存的修改不参与任务",
                     f"{preview['match_count']} matches · Preview only; unsaved edits are excluded from tasks"))
    annotations = preview["receipt"].get("source_control_annotations", [])
    if annotations:
        segments, offset = [], 0
        for annotation in annotations:
            segments.extend((html.escape(original_text[offset:annotation["start"]]),
                             '<mark title="' + html.escape(annotation["marker"], quote=True) + '">',
                             html.escape(original_text[annotation["start"]:annotation["end"]]), '</mark>'))
            offset = annotation["end"]
        segments.append(html.escape(original_text[offset:]))
        st.markdown(_text("**标注位置 · 正文不增删**", "**Annotated spans · Original text preserved**"))
        st.html('<pre style="white-space:pre-wrap;overflow-wrap:anywhere;font:inherit">' + ''.join(segments) + '</pre>')
        for annotation in annotations:
            st.text(_text(f'标记：{annotation["marker"]}（匹配原文：{annotation["original"]}）',
                          f'Label: {annotation["marker"]} (matched text: {annotation["original"]})'))
    st.markdown(_text("**训练读取结果**", "**Training read view**"))
    st.code(preview["transformed_text"], language=None, wrap_lines=True)
    if preview["marked_text"] != preview["transformed_text"]:
        st.markdown(_text("**替换前的标记视图**", "**Marker view before replacement**"))
        st.code(preview["marked_text"], language=None, wrap_lines=True)
