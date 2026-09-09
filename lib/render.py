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
/* 视觉规范取自 dsh（DeepSeek Harness）Web UI 的设计令牌，亮/暗两套对齐：
   用户气泡 --dsw-specific-bubble（亮 #EDF3FE / 暗 #2C2C2E）、22px 圆角、10×16 padding、
   14px/22px 行高、max-width min(477px,82%)；助手为纯文本（无气泡）；层次色 --dsw-alias-bg-layer-1/2。 */
:root { color-scheme: light dark;
  --df-bg: #FFFFFF; --df-layer1: #FFFFFF; --df-layer2: #FFFFFF; --df-bubble: #EDF3FE;
  --df-text: #0F1115; --df-text2: #61666B; --df-text3: #81858C;
  --df-border: rgba(0,0,0,.04); --df-border2: rgba(0,0,0,.1); --df-brand: #4176E6;
  --df-font: "Segoe UI", "Microsoft YaHei", system-ui, -apple-system, sans-serif;
  --df-mono: ui-monospace, "Cascadia Code", Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root { --df-bg: #151517; --df-layer1: #232324; --df-layer2: #2C2C2E; --df-bubble: #2C2C2E;
          --df-text: #F9FAFB; --df-text2: #CFD3D6; --df-text3: #ADB2B8;
          --df-border: rgba(255,255,255,.06); --df-border2: rgba(255,255,255,.12); --df-brand: #5686FE; }
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
.tc-key { color: var(--df-brand); }
.tc-val { color: var(--df-text2); word-break: break-word; }
.err-note { color: #ef4444; font-size: 12px; }
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
