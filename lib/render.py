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
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # Streamlit 自带依赖；缺失时降级为纯文本（换行保留）
    from markdown_it import MarkdownIt

    # html=False 关键：样本内容是不可信数据，Markdown 渲染必须转义原始 HTML
    _MD = MarkdownIt("commonmark", {"breaks": True, "html": False, "linkify": False}).enable("table")

    def render_md(text: str) -> str:
        return _MD.render(str(text))

except Exception:  # noqa: BLE001
    def render_md(text: str) -> str:
        return f"<p>{html.escape(str(text)).replace(chr(10), '<br>')}</p>"

_CSS = """
:root { color-scheme: light dark; }
body { font-family: "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
       max-width: 980px; margin: 0 auto; padding: 24px; background: #f6f7f9; color: #1c1e21;
       font-size: 16px; line-height: 1.7; }
@media (prefers-color-scheme: dark) { body { background: #14161a; color: #e6e6e6; } }
h1 { font-size: 26px; } h1 small { color: #8a8f98; font-weight: normal; font-size: 16px; }
.stats { display: flex; gap: 12px; flex-wrap: wrap; margin: 12px 0 20px; }
.chip { background: #e8ecf1; color: #1c1e21; border-radius: 999px; padding: 6px 14px; font-size: 14px; }
@media (prefers-color-scheme: dark) { .chip { background: #2a2f38; color: #e6e6e6; } }
.card { background: #fff; color: #1c1e21; border-radius: 14px; padding: 18px; margin-bottom: 18px;
        box-shadow: 0 1px 3px rgba(0,0,0,.08); }
@media (prefers-color-scheme: dark) { .card { background: #1d2025; color: #e6e6e6; } }
.meta { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; align-items: center; }
.badge { font-size: 13px; padding: 3px 12px; border-radius: 999px; color: #1c1e21; }
.b-model { background: #dbeafe; } .b-finish { background: #dcfce7; } .b-err { background: #fee2e2; }
.b-score { background: #fef9c3; font-weight: 600; }
@media (prefers-color-scheme: dark) {
  .badge { color: #f5f5f5; }
  .b-model { background: #1e3a5f; } .b-finish { background: #14532d; }
  .b-err { background: #7f1d1d; } .b-score { background: #713f12; }
}
/* ── 聊天气泡 ─────────────────────────────────────────────────────────── */
.bubbles { display: flex; flex-direction: column; gap: 10px; }
.bubble { max-width: 88%; border-radius: 16px; padding: 10px 14px;
          box-shadow: 0 1px 2px rgba(0,0,0,.06); word-break: break-word; }
.bubble .role-tag { font-size: 12.5px; text-transform: uppercase; letter-spacing: .06em;
                    color: #6b7280; margin-bottom: 4px; }
.bub-user { align-self: flex-end; background: #e7f0ff; color: #1c1e21;
            border-bottom-right-radius: 5px; }
.bub-assistant { align-self: flex-start; background: #f0fdf4; color: #1c1e21;
                 border-bottom-left-radius: 5px; }
.bub-tool { align-self: flex-start; background: #f5f3ff; color: #1c1e21; font-size: 15px; }
.bub-tool.err { background: #fef2f2; border: 1px solid #fecaca; }
.bub-system { align-self: center; background: #f1f2f4; color: #6b7280; font-size: 14.5px;
              max-width: 96%; text-align: center; }
.bub-think { align-self: flex-start; background: #fffbeb; border-left: 4px solid #f59e0b;
             max-width: 88%; }
@media (prefers-color-scheme: dark) {
  .bub-user { background: #172554; color: #dbeafe; }
  .bub-assistant { background: #052e16; color: #bbf7d0; }
  .bub-tool { background: #2e1065; color: #ddd6fe; }
  .bub-tool.err { background: #450a0a; border-color: #7f1d1d; color: #fecaca; }
  .bub-system { background: #1f2937; color: #9ca3af; }
  .bub-think { background: #2a2008; }
  .bubble .role-tag { color: #9ca3af; }
}
/* Markdown 内容排版（气泡内） */
.bubble .md > :first-child { margin-top: 0; }
.bubble .md > :last-child { margin-bottom: 0; }
.bubble .md p { margin: 6px 0; }
.bubble .md ul, .bubble .md ol { padding-left: 22px; margin: 6px 0; }
.bubble .md li { margin: 2px 0; }
.bubble .md h1, .bubble .md h2, .bubble .md h3, .bubble .md h4 { margin: 10px 0 6px; line-height: 1.35; }
.bubble .md pre { background: rgba(0,0,0,.06); border-radius: 10px; padding: 10px 12px;
                  overflow-x: auto; margin: 8px 0; }
.bubble .md code { background: rgba(0,0,0,.07); border-radius: 6px; padding: 1px 5px;
                   font-size: .92em; font-family: ui-monospace, Consolas, monospace; }
.bubble .md pre code { background: none; padding: 0; }
.bubble .md blockquote { border-left: 3px solid #cbd5e1; margin: 8px 0; padding: 2px 12px;
                         color: #475569; }
.bubble .md table { border-collapse: collapse; margin: 8px 0; display: block; overflow-x: auto; }
.bubble .md th, .bubble .md td { border: 1px solid #d8dee6; padding: 4px 10px; }
.bubble .md a { color: #2563eb; }
.bubble .md hr { border: none; border-top: 1px solid #e2e8f0; margin: 10px 0; }
@media (prefers-color-scheme: dark) {
  .bubble .md pre, .bubble .md code { background: rgba(255,255,255,.08); }
  .bubble .md blockquote { border-left-color: #475569; color: #94a3b8; }
  .bubble .md th, .bubble .md td { border-color: #374151; }
  .bubble .md a { color: #60a5fa; }
  .bubble .md hr { border-top-color: #374151; }
}
details.think summary { cursor: pointer; color: #b45309; font-size: 15px; }
details.think .md { margin-top: 8px; }
@media (prefers-color-scheme: dark) { details.think summary { color: #fbbf24; } }
.tool-call { margin: 4px 0; font-size: 15px; line-height: 1.6; }
.tc-name { font-weight: 700; }
.tc-key { color: #7c3aed; }
.tc-val { color: #1c1e21; word-break: break-word; }
@media (prefers-color-scheme: dark) {
  .tc-key { color: #c4b5fd; }
  .tc-val { color: #e6e6e6; }
}
.err-note { color: #dc2626; font-size: 13px; }
@media (prefers-color-scheme: dark) { .err-note { color: #f87171; } }
"""


def _esc(text: Any) -> str:
    return html.escape(str(text))


def _human_value(v: Any, depth: int = 0) -> str:
    """把 JSON 值渲染为无引号/无花括号噪声的人类可读形式（嵌套递归，超长截断）。"""
    if depth > 2:
        return "…"
    if isinstance(v, str):
        return v[:400] + ("…" if len(v) > 400 else "")
    if isinstance(v, dict):
        inner = ", ".join(f"{k}: {_human_value(val, depth + 1)}" for k, val in list(v.items())[:8])
        if len(v) > 8:
            inner += ", …"
        return f"[{inner}]"
    if isinstance(v, list):
        inner = ", ".join(_human_value(x, depth + 1) for x in v[:8])
        if len(v) > 8:
            inner += ", …"
        return f"({inner})"
    return str(v)


def _render_tool_call(tc: Dict[str, Any]) -> str:
    """工具调用渲染为可读行：🔧 名称 + 参数（无 JSON 符号）。"""
    lines = [f'<span class="tc-name">🔧 {_esc(tc.get("name", "call"))}</span>']
    args = tc.get("input") or {}
    if isinstance(args, dict):
        for k, v in list(args.items())[:10]:
            lines.append(f'<span class="tc-key">{_esc(str(k))}</span> = <span class="tc-val">{_esc(_human_value(v))}</span>')
    elif args:
        lines.append(_esc(_human_value(args)))
    return f'<div class="tool-call">{"<br>".join(lines)}</div>'


def _render_message(m: Dict[str, Any]) -> str:
    role = m.get("role", "")
    parts: List[str] = []

    if role == "assistant":
        reasoning = m.get("reasoning_content")
        content = m.get("content", "")
        if reasoning:
            parts.append(
                f'<div class="bubble bub-think"><details class="think" open>'
                f"<summary>🧠 思考（{len(reasoning)} 字）</summary>"
                f'<div class="md">{render_md(reasoning)}</div></details></div>'
            )
        if content:
            parts.append(
                f'<div class="bubble bub-assistant"><div class="role-tag">assistant</div>'
                f'<div class="md">{render_md(content)}</div></div>'
            )
        for tc in m.get("toolCalls", []):
            parts.append(
                f'<div class="bubble bub-assistant"><div class="role-tag">tool call</div>'
                f"{_render_tool_call(tc)}</div>"
            )
    elif role == "tool":
        cls = "bub-tool err" if m.get("isError") else "bub-tool"
        note = ' <span class="err-note">⚠ 执行失败</span>' if m.get("isError") else ""
        content = str(m.get("content", ""))
        if len(content) > 1200:
            content = content[:1200] + "…[截断]"
        parts.append(
            f'<div class="bubble {cls}"><div class="role-tag">tool{m.get("toolCallId", "")[:12]}{note}</div>'
            f'<div class="md">{render_md(content)}</div></div>'
        )
    elif role == "system":
        parts.append(
            f'<div class="bubble bub-system"><div class="role-tag">system</div>'
            f'<div class="md">{render_md(m.get("content", ""))}</div></div>'
        )
    else:  # user
        parts.append(
            f'<div class="bubble bub-user"><div class="role-tag">user</div>'
            f'<div class="md">{render_md(m.get("content", ""))}</div></div>'
        )
    return "\n".join(parts)


def _render_sample(sample: Dict[str, Any], score: Optional[str] = None) -> str:
    badges = [
        f'<span class="badge b-model">{_esc(sample.get("model", "?"))}</span>',
        f'<span class="badge b-finish">finish: {_esc(sample.get("finish_reason", "?"))}</span>',
        f'<span class="badge">msgs: {len(sample.get("messages", []))}</span>',
    ]
    errs = int(sample.get("error_tool_steps", 0))
    if errs:
        badges.append(f'<span class="badge b-err">错误步骤: {errs}</span>')
    if score:
        badges.append(f'<span class="badge b-score">评分: {_esc(score[:80])}</span>')

    msgs = sample.get("messages", [])
    if len(msgs) > 60:
        # 尾轮永远保留（思考/正文/工具调用都在尾轮）；其余只展示最近 59 条
        msgs = msgs[-59:] + [msgs[-1]]
    msgs_html = "\n".join(_render_message(m) for m in msgs)
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
        f'<span class="chip">{_esc(k)}: {v}</span>'
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
