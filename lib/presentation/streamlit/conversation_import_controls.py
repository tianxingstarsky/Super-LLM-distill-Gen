"""Preview DSH conversation intake before selecting durable workflow sources."""
from __future__ import annotations

import hashlib
import streamlit as st
from lib.presentation.streamlit.i18n import UntranslatedText, translate


def _clear_import_preview(prefix):
    for field in ("identity", "preview", "confirmed", "error"):
        st.session_state.pop(prefix + ":" + field, None)


def conversation_import_warnings(warnings):
    """Map adapter codes to safe UI text; never echo arbitrary plugin output."""
    labels = {
        "source_has_no_reasoning": "原始会话没有推理字段，导入时不会补造。",
        "surface_replacements_preserved_in_audit": "保留原始消息；压缩或替换后的视图另存为审计记录。",
        "inherited_prefix_preserved": "已保留分支会话继承的前文，请确认这些消息属于本次训练资料。",
        "developer_role_preserved": "已保留 developer 角色，当前训练流程不支持的会话不会选中。",
        "recorded_tool_errors_require_review": "会话包含工具错误，需按目标流程审核。",
        "source_tool_schemas_absent": "部分工具调用缺少原始工具定义，导入时不会猜测补全。",
        "empty_settlement_events_preserved_in_audit": "空消息事件已保留在审计记录中，不会伪造消息正文。",
    }
    unknown = "导入器报告了未识别的提醒，请在确认前检查原始会话。"
    if not isinstance(warnings, (list, tuple)):
        return [unknown] if warnings else []
    return list(dict.fromkeys(labels.get(code, unknown) if isinstance(code, str) else unknown
                              for code in warnings))


def render_conversation_import(application, workspace, on_confirm, *, targets=(), selected_paths=None):
    """Keep format handling outside the page and preserve the original messages."""
    prefix = f"conversation-import:{workspace}"
    uploaded = st.file_uploader(
        "导入 DSH 原始会话", type=["jsonl", "zstd", "db", "sqlite", "sqlite3"],
        max_upload_size=50, key=prefix + ":file",
        help="选择 DSH 保存的 .jsonl.zstd 或 .jsonl 会话文件。搜索索引数据库不包含完整对话。")
    st.caption("仅支持 DSH 会话库。保留角色、工具调用和已有推理；没有推理的记录保持原样。")
    if uploaded is None:
        _clear_import_preview(prefix)
        return
    content = uploaded.getvalue()
    identity = (uploaded.name, hashlib.sha256(content).hexdigest())
    if st.session_state.get(prefix + ":identity") != identity:
        _clear_import_preview(prefix)
        st.session_state[prefix + ":identity"] = identity
        try:
            with st.spinner("正在还原 DSH 会话…"):
                st.session_state[prefix + ":preview"] = application.preview(uploaded.name, content)
        except (ValueError, OSError, RuntimeError) as error:
            st.session_state[prefix + ":error"] = str(error)
    error = st.session_state.get(prefix + ":error")
    if error:
        st.error(conversation_import_error(error))
        if st.button("重试导入", key=prefix + ":retry"):
            _clear_import_preview(prefix)
            st.rerun()
        return
    preview = st.session_state.get(prefix + ":preview")
    if not preview:
        return
    st.markdown("**会话预览**")
    counts = preview.get("counts", {})
    english = st.session_state.get("ui_language") == "en"
    summary = (f"{counts.get('records', 0):,} conversations · {counts.get('messages', 0):,} messages · "
               f"{counts.get('reasoning_messages', 0):,} with reasoning · {counts.get('tool_calls', 0):,} tool calls"
               if english else
               f"{counts.get('records', 0):,} 段对话 · {counts.get('messages', 0):,} 条消息 · "
               f"{counts.get('reasoning_messages', 0):,} 条含推理 · {counts.get('tool_calls', 0):,} 次工具调用")
    st.caption(UntranslatedText(summary))
    warning_labels = conversation_import_warnings(preview.get("report", {}).get("warnings", []))
    if warning_labels:
        st.info(UntranslatedText("\n".join(
            "- " + translate(label, "en" if english else "zh") for label in warning_labels)))
    target = "agent" if set(targets) == {"agent"} else "sft"
    eligibility = preview.get("eligibility", {}).get(target, {})
    blocked = eligibility.get("blocked_records", 0)
    eligible = eligibility.get("eligible_records", counts.get("records", 0))
    if blocked:
        st.warning(UntranslatedText(
            f"{eligible:,} conversations can enter this workflow; {blocked:,} need preparation in DSH and will stay unselected."
            if english else f"{eligible:,} 段可进入当前流程；{blocked:,} 段需先在 DSH 整理，本次不会选中。"))
        st.caption("请在 DSH 中整理不支持的角色、不完整消息或过长会话，再导入新的原始会话。这里不会改写历史对话。")
        for reason, count in eligibility.get("reasons", {}).items():
            labels = {"context_exceeds_auto_limit": "会话过长，需按完整任务拆分",
                      "invalid_role": "含当前训练流程不支持的角色",
                      "potential_secret": "含疑似密钥，需先脱敏",
                      "potential_personal_data": "含疑似个人信息，需先脱敏",
                      "unresolved_tool_error": "工具错误尚未解决",
                      "multimodal_requires_dedicated_pipeline": "附件需要专用多模态导入"}
            label = labels.get(reason, "对话结构需在 DSH 中整理")
            st.caption(UntranslatedText(f"{translate(label, 'en' if english else 'zh')} · {count:,}"))
    conversations = preview.get("conversations", [])
    if conversations:
        selected = 0
        if len(conversations) > 1:
            selected = st.selectbox("查看会话", range(len(conversations)),
                format_func=lambda index: UntranslatedText(conversations[index].get("title") or
                    (f"Session {index + 1}" if english else f"会话 {index + 1}")),
                key=prefix + ":sample:" + identity[1][:12])
        sample = conversations[selected]
        messages = sample.get("messages", [])
        with st.container(height=320, border=True):
            for message in messages[:30]:
                role = message.get("role", "unknown")
                labels = {"system": "系统", "developer": "开发者", "user": "用户", "assistant": "助手", "tool": "工具"}
                st.caption(labels.get(role, role))
                text = message.get("content")
                if isinstance(text, str) and text:
                    st.text(UntranslatedText(text[:6000]))
                elif text:
                    st.json(text, expanded=False)
                reasoning = message.get("reasoning_content") or message.get("reasoning")
                if reasoning:
                    with st.expander("已有推理"):
                        st.text(UntranslatedText(str(reasoning)[:12000]))
                if message.get("tool_calls"):
                    st.json(message["tool_calls"], expanded=False)
        st.caption("预览仅展示部分会话与消息，保存时保留完整记录。导入后仍按工作流配置进行审核。")
    receipt = st.session_state.get(prefix + ":confirmed")
    confirmed = isinstance(receipt, dict) and receipt.get("target") == target
    if confirmed:
        imported_paths = {row["path"] for row in receipt.get("rows", [])}
        if imported_paths and (selected_paths is None or imported_paths <= set(selected_paths)):
            st.success("已加入本次资料，可继续配置或启动工作流。")
            return
        st.caption("会话已保存在资料缓存中，目前未全部选中。可以重新加入本次任务，无需重新上传。")
    if eligibility.get("eligible_ids_truncated", False):
        st.caption("记录过多，请分批整理后导入。")
    if st.button("重新选入本次资料" if confirmed else "确认导入并选中",
                 key=prefix + ":confirm", type="primary", width="stretch",
                 disabled=not eligible or eligibility.get("eligible_ids_truncated", False)):
        try:
            rows = application.confirm(preview["import_id"],
                selected_ids=eligibility.get("eligible_ids") if blocked else None, target=target)
            on_confirm(rows)
            st.session_state[prefix + ":confirmed"] = {"target": target, "rows": rows}
            st.rerun()
        except (ValueError, OSError, RuntimeError) as error:
            st.error(conversation_import_error(str(error)))


def conversation_import_error(code):
    """Never display arbitrary plugin output or a provider traceback."""
    if any(part in code for part in ("cache_invalid", "cache_mismatch", "receipt_mismatch")):
        return "暂存会话校验失败，暂时无法确认导入。请修复本地导入缓存后重试。"
    if "search_index" in code or "sqlite" in code:
        return "这是 DSH 搜索索引，不含完整对话。请改选原始 .jsonl.zstd 或 .jsonl 会话文件。"
    if any(part in code for part in ("unavailable", "not_installed", "node", "dependency", "dependencies")):
        return "DSH 会话导入组件尚未就绪。请按独立插件的安装说明配置后重试。"
    if "limit" in code or "large" in code:
        return "会话文件超过导入上限，请分批导入较小的原始会话文件。"
    if "empty" in code:
        return "文件中没有可导入的完整会话，请选择包含真实对话的 DSH 会话文件。"
    if "integrity" in code or "changed" in code:
        return "暂存会话已变化，请重新上传并核对预览。"
    return "无法识别或完整还原这份 DSH 会话，请检查原始文件与插件支持的版本。其他数据库暂不支持。"
