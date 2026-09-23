"""训练数据美化渲染：静态 HTML 预览页 + 控制台气泡（Markdown 渲染，无网络依赖）。

渲染约定：
  - 每个样本一张卡片：元数据徽章（模型/结束类型/消息数/错误步骤/质量分）
  - 消息按角色分气泡：user 右对齐蓝、assistant 左对齐绿、tool 紫（isError 红边）、
    system 居中灰；思考为琥珀色可折叠块（默认折叠）
  - 正文走 Markdown 渲染（markdown-it；样本内容是**不可信数据**：HTML 一律转义）
  - toolCalls 渲染为可读行（无 JSON 符号）
用途：G3 小批量放量闸的人工过目材料（df preview --html）与控制台审核/预览页。
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional

from lib.console_theme import DARK_TOKENS, dark_var_block, theme_var_block

try:  # Streamlit 自带依赖；缺失时降级为纯文本（换行保留）
    from markdown_it import MarkdownIt

    # html=False 关键：样本内容是不可信数据，Markdown 渲染必须转义原始 HTML
    _MD = MarkdownIt("commonmark", {"breaks": True, "html": False, "linkify": False}).enable("table")

    def _render_image_placeholder(tokens, index, options, env):
        # Avoid loading remote image URLs while previewing user supplied samples.
        alt = html.escape(tokens[index].content or "无描述")
        return f'<span class="md-image-placeholder">［图片：{alt}］</span>'

    _MD.renderer.rules["image"] = _render_image_placeholder

    def render_md(text: str) -> str:
        return _MD.render(str(text))

except Exception:  # noqa: BLE001
    def render_md(text: str) -> str:
        return f"<p>{html.escape(str(text)).replace(chr(10), '<br>')}</p>"

_FONT = '"Segoe UI", "Microsoft YaHei", system-ui, -apple-system, sans-serif'
_MONO = 'ui-monospace, "Cascadia Code", Consolas, monospace'

# 独立预览页（df preview --html）样式：保留系统亮/暗两套（向后兼容）。
# 暗色块由 console_theme.DARK_TOKENS 生成，与控制台/消息色板保持同一事实源。
_CSS_TEMPLATE = Template("""
/* 视觉规范取自 dsh（DeepSeek Harness）Web UI 的设计令牌，亮/暗两套对齐：
   用户气泡 --dsw-specific-bubble（亮 #EDF3FE / 暗 #2C2C2E）、22px 圆角、10×16 padding、
   14px/22px 行高、max-width min(477px,82%)；助手为纯文本（无气泡）；层次色 --dsw-alias-bg-layer-1/2。 */
:root { color-scheme: light dark;
  --df-bg: #FFFFFF; --df-layer1: #FFFFFF; --df-layer2: #FFFFFF; --df-bubble: #EDF3FE;
  --df-text: #0F1115; --df-text2: #61666B; --df-text3: #81858C;
  --df-border: rgba(0,0,0,.04); --df-border2: rgba(0,0,0,.1); --df-brand: #4176E6;
  --df-call-bg: #f7f4ff; --df-call-border: #e9ddff;
  --df-font: $font;
  --df-mono: $mono;
}
@media (prefers-color-scheme: dark) {
  :root {
$dark_vars
    --df-call-bg: #262239; --df-call-border: #534879;
  }
}
body { font-family: var(--df-font); max-width: 980px; margin: 0 auto; padding: 24px;
       background: var(--df-bg); color: var(--df-text); font-size: 14px; line-height: 22px; }
h1 { font-size: 22px; font-weight: 600; } h1 small { color: var(--df-text3); font-weight: normal; font-size: 14px; }
.stats { display: flex; gap: 10px; flex-wrap: wrap; margin: 12px 0 24px; }
.chip { background: var(--df-layer2); color: var(--df-text2); border-radius: 999px; padding: 4px 12px; font-size: 13px; }
.card { background: transparent; padding: 0; margin-bottom: 28px; }
.meta { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 6px; align-items: center; }
.badge { font-size: 12px; padding: 2px 10px; border-radius: 999px; background: var(--df-layer2);
         color: var(--df-text2); }
.b-err { color: #ef4444; } .b-score { color: var(--df-text); font-weight: 600; }
.idline { color: var(--df-text3); font-size: 12px; margin-bottom: 12px; }
/* ── 会话气泡（对齐 dsh） ─────────────────────────────────────────────── */
.bubbles { display: flex; flex-direction: column; gap: 16px; }
.bubble { font-size: 14px; line-height: 22px; word-break: break-word; }
.bub-user { align-self: flex-end; max-width: min(477px, 82%); background: var(--df-bubble);
            color: var(--df-text); border-radius: 22px; padding: 10px 16px; }
.bub-assistant { align-self: stretch; max-width: 100%; background: transparent; padding: 0; }
.bub-call { align-self: stretch; max-width: 100%; background: var(--df-call-bg);
            border: 1px solid var(--df-call-border); border-radius: 16px; padding: 10px 14px; }
.bub-tool { align-self: stretch; background: var(--df-layer1); border: 1px solid var(--df-border2);
            border-radius: 16px; padding: 10px 14px; color: var(--df-text2);
            font-family: var(--df-mono); font-size: 13px; line-height: 20px; }
.bub-tool.err { border-color: rgba(239,68,68,.45); color: #ef4444; }
.bub-system { align-self: center; background: var(--df-layer1); border: 1px solid var(--df-border);
              border-radius: 999px; padding: 6px 14px; color: var(--df-text3); font-size: 13px; }
.bub-think { align-self: stretch; max-width: 100%; background: transparent; padding: 0; }
.bub-user .role-tag, .bub-assistant .role-tag { display: none; }  /* dsh 靠气泡/对齐区分角色 */
.role-tag { font-size: 11px; letter-spacing: .05em; color: var(--df-text3); margin-bottom: 4px;
            font-family: var(--df-mono); }
/* Markdown 排版 */
.md > :first-child { margin-top: 0; }
.md > :last-child { margin-bottom: 0; }
.md p { margin: 6px 0; }
.md ul, .md ol { padding-left: 22px; margin: 6px 0; }
.md li { margin: 2px 0; }
.md h1, .md h2, .md h3, .md h4 { margin: 10px 0 6px; line-height: 1.4; }
.md pre { background: var(--df-layer2); border-radius: 12px; padding: 10px 12px; overflow-x: auto; margin: 8px 0; }
.md code { background: var(--df-layer2); border-radius: 6px; padding: 1px 5px;
           font-size: .92em; font-family: var(--df-mono); }
.md pre code { background: none; padding: 0; }
.md blockquote { border-left: 3px solid var(--df-border2); margin: 8px 0; padding: 2px 12px;
                 color: var(--df-text2); }
.md table { border-collapse: collapse; margin: 8px 0; display: block; overflow-x: auto; }
.md th, .md td { border: 1px solid var(--df-border2); padding: 4px 10px; }
.md a { color: var(--df-brand); }
.md hr { border: none; border-top: 1px solid var(--df-border); margin: 10px 0; }
details.think { border: 1px solid var(--df-border2); border-radius: 16px; padding: 10px 14px;
                color: var(--df-text2); }
details.think summary { cursor: pointer; color: var(--df-text2); font-size: 13px; }
details.think .md { margin-top: 8px; }
.tool-call { margin: 2px 0; font-size: 13px; line-height: 20px; }
.tc-name { font-weight: 600; color: var(--df-text); }
.tc-id { margin-left: 8px; color: var(--df-text3); font-family: var(--df-mono); font-size: 11px; }
.tc-key { color: var(--df-brand); }
.tc-val { color: var(--df-text2); word-break: break-word; }
.md-image-placeholder { display: inline-block; padding: 3px 8px; border: 1px dashed var(--df-border2);
                        border-radius: 6px; color: var(--df-text3); font-size: 12px; }
.tool-overflow { margin-top: 8px; }
.tool-overflow summary { cursor: pointer; color: var(--df-brand); font-family: var(--df-font); }
.err-note { color: #ef4444; font-size: 12px; }
""")

_CSS = _CSS_TEMPLATE.substitute(font=_FONT, mono=_MONO, dark_vars=dark_var_block("    "))

# 嵌入 Streamlit 控制台用的消息区样式：仅 .bubbles 作用域，
# 不含 :root/body/裸 h1，避免影响宿主页面；与 console_theme.CSS 共用浅色产品令牌。
# 用法：<style>{MESSAGE_CSS}</style> + <div class="bubbles">{_render_message(...)}</div>
_MESSAGE_TEMPLATE = Template("""
/* 消息区（仅 .bubbles 内生效）：跟随 DataForge 产品主题。 */
.bubbles {
$dark_vars
  --df-call-bg: #f7f4ff;
  --df-call-border: #e9ddff;
  --df-font: $font;
  --df-mono: $mono;
  display: flex; flex-direction: column; gap: 16px;
  font-family: var(--df-font); font-size: 14px; line-height: 22px; color: var(--df-text);
}
.bubbles .bubble { font-size: 14px; line-height: 22px; word-break: break-word; }
.bubbles .bub-user { align-self: flex-end; max-width: min(477px, 82%); background: var(--df-bubble);
            color: var(--df-text); border-radius: 22px; padding: 10px 16px; }
.bubbles .bub-assistant { align-self: stretch; max-width: 100%; background: transparent; padding: 0; }
.bubbles .bub-call { align-self: stretch; max-width: 100%; background: var(--df-call-bg);
            border: 1px solid var(--df-call-border); border-radius: 16px; padding: 10px 14px; }
.bubbles .bub-tool { align-self: stretch; background: var(--df-layer1); border: 1px solid var(--df-border2);
            border-radius: 16px; padding: 10px 14px; color: var(--df-text2);
            font-family: var(--df-mono); font-size: 13px; line-height: 20px; }
.bubbles .bub-tool.err { border-color: rgba(248,113,113,.45); color: var(--df-danger); }
.bubbles .bub-system { align-self: center; background: var(--df-layer1); border: 1px solid var(--df-border);
              border-radius: 999px; padding: 6px 14px; color: var(--df-text3); font-size: 13px; }
.bubbles .bub-think { align-self: stretch; max-width: 100%; background: transparent; padding: 0; }
.bubbles .bub-user .role-tag, .bubbles .bub-assistant .role-tag { display: none; }  /* dsh 靠气泡/对齐区分角色 */
.bubbles .role-tag { font-size: 11px; letter-spacing: .05em; color: var(--df-text3); margin-bottom: 4px;
            font-family: var(--df-mono); }
/* Markdown 排版 */
.bubbles .md > :first-child { margin-top: 0; }
.bubbles .md > :last-child { margin-bottom: 0; }
.bubbles .md p { margin: 6px 0; }
.bubbles .md ul, .bubbles .md ol { padding-left: 22px; margin: 6px 0; }
.bubbles .md li { margin: 2px 0; }
.bubbles .md h1, .bubbles .md h2, .bubbles .md h3, .bubbles .md h4 { margin: 10px 0 6px; line-height: 1.4; }
.bubbles .md pre { background: var(--df-layer2); border-radius: 12px; padding: 10px 12px; overflow-x: auto; margin: 8px 0; }
.bubbles .md code { background: var(--df-layer2); border-radius: 6px; padding: 1px 5px;
           font-size: .92em; font-family: var(--df-mono); }
.bubbles .md pre code { background: none; padding: 0; }
.bubbles .md blockquote { border-left: 3px solid var(--df-border2); margin: 8px 0; padding: 2px 12px;
                 color: var(--df-text2); }
.bubbles .md table { border-collapse: collapse; margin: 8px 0; display: block; overflow-x: auto; }
.bubbles .md th, .bubbles .md td { border: 1px solid var(--df-border2); padding: 4px 10px; }
.bubbles .md a { color: var(--df-brand); }
.bubbles .md hr { border: none; border-top: 1px solid var(--df-border); margin: 10px 0; }
.bubbles .md img, .bubbles .md video { max-width: 100%; height: auto; border-radius: 8px; }  /* 多模态安全显示，不改变渲染逻辑 */
.bubbles .md-image-placeholder { display: inline-block; padding: 3px 8px; border: 1px dashed var(--df-border2); border-radius: 6px; color: var(--df-text3); font-size: 12px; }
.bubbles details.think { border: 1px solid var(--df-border2); border-radius: 16px; padding: 10px 14px;
                color: var(--df-text2); }
.bubbles details.think summary { cursor: pointer; color: var(--df-text2); font-size: 13px; }
.bubbles details.think .md { margin-top: 8px; }
.bubbles .tool-call { margin: 2px 0; font-size: 13px; line-height: 20px; }
.bubbles .tc-name { font-weight: 600; color: var(--df-text); }
.bubbles .tc-id { margin-left: 8px; color: var(--df-text3); font-family: var(--df-mono); font-size: 11px; }
.bubbles .tc-key { color: var(--df-brand); }
.bubbles .tc-val { color: var(--df-text2); word-break: break-word; }
.bubbles .tool-overflow { margin-top: 8px; }
.bubbles .tool-overflow summary { cursor: pointer; color: var(--df-brand); font-family: var(--df-font); }
.bubbles .err-note { color: var(--df-danger); font-size: 12px; }
""")

MESSAGE_CSS = _MESSAGE_TEMPLATE.substitute(font=_FONT, mono=_MONO, dark_vars=theme_var_block("  "))


def _esc(text: Any) -> str:
    return html.escape(str(text))


def _human_value(v: Any, depth: int = 0) -> str:
    """把 JSON 值渲染为无引号/无花括号噪声的人类可读形式（嵌套递归，超长截断）。"""
    if depth > 2:
        return "…"
    if isinstance(v, str):
        if v.startswith("data:"):
            return "［内嵌媒体数据］"
        return v[:400] + ("…" if len(v) > 400 else "")
    if isinstance(v, dict):
        inner = ", ".join(
            f"{k}: {'［媒体内容］' if str(k).lower() in {'image_url', 'audio_url', 'data', 'base64'} else _human_value(val, depth + 1)}"
            for k, val in list(v.items())[:8]
        )
        if len(v) > 8:
            inner += ", …"
        return f"[{inner}]"
    if isinstance(v, list):
        inner = ", ".join(_human_value(x, depth + 1) for x in v[:8])
        if len(v) > 8:
            inner += ", …"
        return f"({inner})"
    return str(v)


def _plain_text(value: Any) -> str:
    """Read text-shaped blocks without including media URLs or base64 payloads."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "value", "content"):
            if key in value:
                return _plain_text(value[key])
        return _human_value(value)
    if isinstance(value, list):
        return "\n".join(filter(None, (_plain_text(item) for item in value)))
    return "" if value is None else str(value)


def _content_blocks(value: Any, depth: int = 0) -> List[tuple[str, Any]]:
    """Keep multimodal and tool blocks in their original conversational order."""
    if depth > 5 or value is None:
        return []
    if isinstance(value, list):
        return [block for item in value for block in _content_blocks(item, depth + 1)]
    if not isinstance(value, dict):
        return [("text", str(value))] if str(value) else []

    kind = str(value.get("type") or "").lower()
    if kind in {"tool_use", "tool_call", "function_call"}:
        return [("tool_call", value)]
    if kind in {"tool_result", "function_result"}:
        return [("tool_result", value)]
    if kind in {"reasoning", "thinking"}:
        thought = next((value[key] for key in ("thinking", "text", "content", "summary") if key in value), "")
        return [("reasoning", _plain_text(thought))]
    mime_type = str(value.get("mime_type") or value.get("mimeType") or "").lower()
    if "inline_data" in value or "file_data" in value:
        media = value.get("inline_data") or value.get("file_data") or {}
        if isinstance(media, dict):
            mime_type = str(media.get("mime_type") or media.get("mimeType") or mime_type).lower()
    if mime_type.startswith("image/"):
        return [("text", "［图像内容］")]
    if mime_type.startswith("audio/"):
        return [("text", "［音频内容］")]
    if mime_type.startswith("video/"):
        return [("text", "［视频内容］")]
    if "image" in kind or "image_url" in value or "image" in value:
        return [("text", "［图像内容］")]
    if "audio" in kind or "audio_url" in value:
        return [("text", "［音频内容］")]
    if "video" in kind or "video_url" in value:
        return [("text", "［视频内容］")]
    if kind in {"file", "document", "input_file", "pdf"} or "file_data" in value or "inline_data" in value:
        return [("text", "［文件内容］")]
    if kind in {"text", "input_text", "output_text"}:
        return [("text", _plain_text(value.get("text", value.get("content", ""))))]
    for key in ("parts", "content_parts", "text", "content"):
        if key in value:
            return _content_blocks(value[key], depth + 1)
    return [("text", _human_value(value))] if value else []


def _message_parts(value: Any) -> tuple[str, str]:
    """Normalize content to visible text and separately collapsible reasoning."""
    blocks = _content_blocks(value)
    visible = [str(item) for kind, item in blocks if kind == "text" and item]
    reasoning = [str(item) for kind, item in blocks if kind == "reasoning" and item]
    return "\n".join(visible), "\n".join(reasoning)


def _render_tool_call(tc: Dict[str, Any]) -> str:
    """工具调用渲染为可读行：🔧 名称 + 参数（无 JSON 符号）。"""
    function = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    name = tc.get("name") or function.get("name") or tc.get("toolName") or "call"
    args = next((source[key] for source, key in ((tc, "input"), (tc, "arguments"), (function, "arguments"))
                 if key in source), {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (TypeError, ValueError):
            pass
    lines = [f'<span class="tc-name">🔧 {_esc(name)}</span>']
    call_id = tc.get("id") or tc.get("toolCallId") or tc.get("tool_call_id")
    if call_id:
        lines[0] += f'<span class="tc-id">{_esc(str(call_id)[:32])}</span>'
    if isinstance(args, dict):
        for k, v in list(args.items())[:10]:
            lines.append(f'<span class="tc-key">{_esc(str(k))}</span> = <span class="tc-val">{_esc(_human_value(v))}</span>')
    elif args:
        lines.append(_esc(_human_value(args)))
    return f'<div class="tool-call">{"<br>".join(lines)}</div>'


def _iter_tool_calls(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if isinstance(value, dict):
        value = [value]
    return [call for call in value if isinstance(call, dict)] if isinstance(value, list) else []


def _render_tool_result(m: Dict[str, Any]) -> str:
    content, _ = _message_parts(m.get("content", ""))
    failed = m.get("isError") or m.get("is_error")
    cls = "bub-tool err" if failed else "bub-tool"
    note = ' <span class="err-note">⚠ 执行失败</span>' if failed else ""
    tool_name = m.get("toolName") or m.get("name") or m.get("tool_name") or "工具结果"
    call_id = str(m.get("toolCallId") or m.get("tool_call_id") or m.get("tool_use_id") or "")[:32]
    if len(content) > 1200:
        preview = render_md(content[:1200] + "…")
        full = render_md(content[:20000] + ("…［后续内容省略］" if len(content) > 20000 else ""))
        body = (f'<div class="md">{preview}</div><details class="tool-overflow">'
                f'<summary>展开工具输出（{len(content)} 字）</summary><div class="md">{full}</div></details>')
    else:
        body = f'<div class="md">{render_md(content or "（空结果）")}</div>'
    return (
        f'<div class="bubble {cls}"><div class="role-tag">{_esc(tool_name)}'
        f'{(" · " + _esc(call_id)) if call_id else ""}{note}</div>{body}</div>'
    )


def _render_message(m: Dict[str, Any]) -> str:
    role = str(m.get("role") or "").lower()
    parts: List[str] = []
    raw_content = m.get("content", "")
    tagged_reasoning = ""
    if role == "assistant" and isinstance(raw_content, str):
        tagged = re.match(r"^\s*<think>(.*?)</think>\s*(.*)$", raw_content, re.DOTALL)
        if tagged:
            tagged_reasoning, raw_content = tagged.groups()
    blocks = _content_blocks(raw_content)

    if role == "assistant":
        explicit_reasoning = m.get("reasoning_content") or m.get("reasoning")
        explicit_blocks = _content_blocks(explicit_reasoning)
        reasoning = "\n".join(str(item) for kind, item in explicit_blocks
                              if kind in {"reasoning", "text"} and item)
        embedded_reasoning = "\n".join([tagged_reasoning] +
                                         [str(item) for kind, item in blocks if kind == "reasoning" and item]).strip()
        if reasoning and embedded_reasoning and reasoning != embedded_reasoning:
            reasoning += "\n" + embedded_reasoning
        elif not reasoning:
            reasoning = embedded_reasoning
        if reasoning:
            parts.append(
                f'<div class="bubble bub-think"><details class="think">'
                f"<summary>🧠 思考（{len(reasoning)} 字）</summary>"
                f'<div class="md">{render_md(reasoning)}</div></details></div>'
            )
        pending_text: List[str] = []

        def flush_text() -> None:
            if pending_text:
                text_content = "\n".join(pending_text)
                parts.append(
                    f'<div class="bubble bub-assistant"><div class="role-tag">助手</div>'
                    f'<div class="md">{render_md(text_content)}</div></div>'
                )
                pending_text.clear()

        for kind, item in blocks:
            if kind == "text" and item:
                pending_text.append(str(item))
            elif kind == "tool_call":
                flush_text()
                parts.append(f'<div class="bubble bub-call"><div class="role-tag">工具调用</div>'
                             f'{_render_tool_call(item)}</div>')
            elif kind == "tool_result":
                flush_text()
                parts.append(_render_tool_result({"content": item.get("content", ""),
                                                  "name": item.get("name"), "tool_use_id": item.get("tool_use_id"),
                                                  "is_error": item.get("is_error")}))
        flush_text()
        calls = _iter_tool_calls(m.get("toolCalls") or m.get("tool_calls") or [])
        for tc in calls:
            parts.append(f'<div class="bubble bub-call"><div class="role-tag">工具调用</div>'
                         f'{_render_tool_call(tc)}</div>')
    elif role == "tool":
        parts.append(_render_tool_result(m))
    elif role in {"system", "developer"}:
        content, _ = _message_parts(m.get("content", ""))
        label = "开发者指令" if role == "developer" else "系统指令"
        parts.append(
            f'<div class="bubble bub-system"><div class="role-tag">{label}</div>'
            f'<div class="md">{render_md(content)}</div></div>'
        )
    else:
        label = "用户" if role == "user" else f"其他消息 · {_esc(role or '未知角色')}"
        pending_text = []

        def flush_text() -> None:
            if pending_text:
                text_content = "\n".join(pending_text)
                parts.append(f'<div class="bubble bub-user"><div class="role-tag">{label}</div>'
                             f'<div class="md">{render_md(text_content)}</div></div>')
                pending_text.clear()

        for kind, item in blocks:
            if kind == "text" and item:
                pending_text.append(str(item))
            elif kind == "tool_result":
                flush_text()
                parts.append(_render_tool_result({"content": item.get("content", ""),
                                                  "name": item.get("name"), "tool_use_id": item.get("tool_use_id"),
                                                  "is_error": item.get("is_error")}))
            elif kind == "tool_call":
                flush_text()
                parts.append(f'<div class="bubble bub-call"><div class="role-tag">工具调用</div>'
                             f'{_render_tool_call(item)}</div>')
        flush_text()
    return "\n".join(parts)


def render_message_sequence(messages: List[Dict[str, Any]], *, omission_after: Optional[int] = None,
                            omission_count: int = 0) -> str:
    """Render a trajectory and label tool observations with their matching call names."""
    call_names: Dict[str, str] = {}
    rendered: List[str] = []
    for index, message in enumerate(messages):
        if omission_after is not None and index == omission_after and omission_count > 0:
            rendered.append(f'<div class="bubble bub-system">已省略中间 {omission_count} 条消息</div>')
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            inline = [item for kind, item in _content_blocks(message.get("content")) if kind == "tool_call"]
            for call in inline + _iter_tool_calls(message.get("toolCalls") or message.get("tool_calls") or []):
                call_id = call.get("id") or call.get("toolCallId") or call.get("tool_call_id")
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                name = call.get("name") or function.get("name") or call.get("toolName")
                if call_id and name:
                    call_names[str(call_id)] = str(name)

        current = message
        if message.get("role") == "tool" and not any(
            message.get(key) for key in ("toolName", "tool_name", "name")
        ):
            call_id = message.get("toolCallId") or message.get("tool_call_id")
            if call_id and str(call_id) in call_names:
                current = {**message, "toolName": call_names[str(call_id)]}
        elif message.get("role") == "user" and isinstance(message.get("content"), list):
            content = []
            changed = False
            for block in message["content"]:
                if isinstance(block, dict) and str(block.get("type") or "").lower() == "tool_result":
                    call_id = block.get("tool_use_id") or block.get("toolCallId")
                    if not block.get("name") and call_id and str(call_id) in call_names:
                        block = {**block, "name": call_names[str(call_id)]}
                        changed = True
                content.append(block)
            if changed:
                current = {**message, "content": content}
        rendered.append(_render_message(current))
    return "\n".join(rendered)


def _render_sample(sample: Dict[str, Any], score: Optional[str] = None) -> str:
    all_messages = sample.get("messages")
    if not isinstance(all_messages, list):
        all_messages = []
    badges = [
        f'<span class="badge b-model">{_esc(sample.get("model", "?"))}</span>',
        f'<span class="badge b-finish">finish: {_esc(sample.get("finish_reason", "?"))}</span>',
        f'<span class="badge">msgs: {len(all_messages)}</span>',
    ]
    try:
        errs = int(sample.get("error_tool_steps") or 0)
    except (TypeError, ValueError):
        errs = 0
    if errs:
        badges.append(f'<span class="badge b-err">错误步骤: {errs}</span>')
    if score:
        badges.append(f'<span class="badge b-score">评分: {_esc(str(score)[:80])}</span>')

    if len(all_messages) > 60:
        # Keep the initial task and the latest trajectory in order, without duplicating the final turn.
        visible = all_messages[:2] + all_messages[-58:]
        msgs_html = render_message_sequence(visible, omission_after=2,
                                            omission_count=len(all_messages) - 60)
    else:
        msgs_html = render_message_sequence(all_messages)
    return (
        f'<div class="card"><div class="meta">{"".join(badges)}</div>'
        f'<div class="role-tag">id: {_esc(sample.get("id", ""))}</div>'
        f'<div class="bubbles">{msgs_html}</div></div>'
    )


def render_preview_html(
    samples: List[Dict[str, Any]],
    report: Optional[Dict[str, Any]] = None,
    out_path: str | Path = "data/output/preview.html",
    max_samples: int = 20,
) -> Path:
    """把样本列表渲染为静态 HTML 预览页，返回文件路径。"""
    report = report or {}
    scores = {s.get("id"): s.get("score") for s in report.get("llm_scores", [])}

    stats_html = "".join(
        f'<span class="chip">{_esc(k)}: {_esc(v)}</span>'
        for k, v in {
            "样本数": len(samples),
            "分类": report.get("counts", {}),
            "DPO 对": report.get("n_dpo_pairs", "—"),
            "LLM 调用": report.get("llm_usage", {}).get("calls", "—"),
        }.items()
    )
    cards = "\n".join(_render_sample(s, scores.get(s.get("id"))) for s in samples[:max_samples])

    doc = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>训练数据预览 — Super-LLM-distill-Gen</title>
<style>{_CSS}</style></head>
<body>
<h1>训练数据预览 <small>Super-LLM-distill-Gen</small></h1>
<div class="stats">{stats_html}</div>
{cards}
</body></html>"""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")
    return path
