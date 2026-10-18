"""Compact, read-only visibility into the director's public planning memory."""
from __future__ import annotations

from collections.abc import Mapping

import streamlit as st


def _text(language: str, zh: str, en: str) -> str:
    return en if language == "en" else zh


def knowledge_memory_context_help(language: str) -> str:
    return _text(language,
        "知识集合和节点配置匹配时续接会话。新任务保留近期完整往返，较早公开计划在本机归档；独立批次不继承会话。",
        "Continue when the knowledge and node settings match. New tasks keep recent complete exchanges and archive earlier public plans locally. Independent batches do not inherit the conversation.")


def knowledge_memory_tools_help(language: str) -> str:
    return _text(language,
        "支持工具时，可按需检索清洗资料与公开计划。私有推理和工具正文不进入历史索引；关闭工具仍会提供归档摘要。",
        "When tools are supported, retrieve clean sources and public plans as needed. Private reasoning and tool bodies are not indexed as history. Archive previews remain available with tools disabled.")


def knowledge_memory_retention_caption(language: str) -> str:
    return _text(language,
        "新任务保留近期完整往返，早期规划可检索；关闭工具仍提供摘要。历史只用于避重与调度，不作事实依据。",
        "New tasks retain recent complete exchanges and can retrieve earlier plans. Previews remain available without tools. History helps avoid repetition and plan work; it is not factual evidence.")


def _count(value) -> int | None:
    # Missing or malformed telemetry must not turn into an invented zero.
    return value if type(value) is int and value >= 0 else None


def knowledge_memory_summary(state: Mapping, language: str = "zh") -> tuple[str, ...]:
    """Format emitted archive metrics only, never archive bodies or estimates."""
    if not isinstance(state, Mapping):
        return ()
    director = state.get("qa_director")
    if not isinstance(director, Mapping):
        return ()
    context = director.get("knowledge_context")
    if not isinstance(context, Mapping) or type(context.get("archive_version")) is not int or context["archive_version"] != 1:
        return ()

    pieces = [_text(language, "**指导员记忆**", "**Director memory**")]
    retained = _count(context.get("retained_messages"))
    if retained is not None:
        pieces.append(_text(language, f"近期 {retained:,} 条消息", f"{retained:,} recent messages"))
    used = _count(context.get("history_bytes"))
    maximum = _count(context.get("history_window_max_bytes"))
    if used is not None:
        size = f"{used / 1024:,.1f}"
        if maximum is not None and maximum > 0:
            size += f" / {maximum / 1024:,.1f}"
        pieces.append(_text(language, f"历史 {size} KiB", f"History {size} KiB"))
    archived = _count(context.get("archived_operations"))
    if archived is not None:
        pieces.append(_text(language, f"归档 {archived:,} 次操作", f"{archived:,} archived operations"))
    previews = _count(context.get("archive_previews"))
    if previews is not None:
        pieces.append(_text(language, f"摘要 {previews:,} 条", f"{previews:,} previews"))
    # An archive version alone does not prove that an operation has completed.
    if len(pieces) == 1:
        return ()

    tools_available = context.get("archive_tools_available")
    if tools_available is True:
        mode = _text(language, "公开计划可按需检索。", "Public plans can be retrieved as needed. ")
    elif tools_available is False:
        mode = _text(language, "当前使用归档摘要。", "Archive previews are used in the current context. ")
    else:
        mode = ""
    notice = _text(language,
        "历史用于避重与调度，不作事实依据。KiB 表示历史大小，不是模型计费 token 数。",
        "History helps avoid repetition and plan work; it is not factual evidence. KiB measures history size, not billed model tokens.")
    return (" · ".join(pieces), mode + notice)


def render_knowledge_memory_summary(state: Mapping, language: str = "zh") -> None:
    """Use two short captions beside the existing live director coverage."""
    for line in knowledge_memory_summary(state, language):
        st.caption(line)
