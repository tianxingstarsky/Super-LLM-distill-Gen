"""训练数据美化渲染：静态 HTML 预览页（人工过目用，纯 HTML+CSS，无依赖无网络）。

渲染约定：
  - 每个样本一张卡片：元数据徽章（模型/结束类型/消息数/错误步骤/质量分）
  - 消息按角色分色：user 蓝色左对齐、assistant 深绿、tool 紫色代码块、
    isError 工具结果红色边框、reasoning 琥珀色可折叠块（默认折叠）
  - toolCalls 渲染为 JSON 代码块
用途：G3 小批量放量闸的人工过目材料（df preview --html）。
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

_CSS = """
:root { color-scheme: light dark; }
body { font-family: "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
       max-width: 980px; margin: 0 auto; padding: 24px; background: #f6f7f9; color: #1c1e21; }
@media (prefers-color-scheme: dark) { body { background: #14161a; color: #e6e6e6; } }
h1 { font-size: 22px; } h1 small { color: #8a8f98; font-weight: normal; }
.stats { display: flex; gap: 12px; flex-wrap: wrap; margin: 12px 0 20px; }
.chip { background: #e8ecf1; color: #1c1e21; border-radius: 999px; padding: 4px 12px; font-size: 13px; }
@media (prefers-color-scheme: dark) { .chip { background: #2a2f38; color: #e6e6e6; } }
.card { background: #fff; color: #1c1e21; border-radius: 12px; padding: 16px; margin-bottom: 18px;
        box-shadow: 0 1px 3px rgba(0,0,0,.08); }
@media (prefers-color-scheme: dark) { .card { background: #1d2025; color: #e6e6e6; } }
.meta { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; align-items: center; }
.badge { font-size: 12px; padding: 2px 10px; border-radius: 999px; color: #1c1e21; }
.b-model { background: #dbeafe; } .b-finish { background: #dcfce7; } .b-err { background: #fee2e2; }
.b-score { background: #fef9c3; font-weight: 600; }
@media (prefers-color-scheme: dark) {
  .badge { color: #f5f5f5; }
  .b-model { background: #1e3a5f; } .b-finish { background: #14532d; }
  .b-err { background: #7f1d1d; } .b-score { background: #713f12; }
}
.msg { border-radius: 10px; padding: 8px 12px; margin: 8px 0; white-space: pre-wrap; word-break: break-word; }
.m-user { background: #eef4ff; color: #1c1e21; border-left: 4px solid #3b82f6; }
.m-assistant { background: #ecfdf3; color: #1c1e21; border-left: 4px solid #16a34a; }
.m-tool { background: #f5f3ff; color: #1c1e21; border-left: 4px solid #8b5cf6;
          font-family: Consolas, monospace; font-size: 13px; }
.m-tool-err { background: #fef2f2; color: #1c1e21; border-left: 4px solid #dc2626;
              font-family: Consolas, monospace; font-size: 13px; }
.m-system { background: #f1f2f4; color: #6b7280; border-left: 4px solid #9ca3af; font-size: 13px; }
.role-tag { font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
            color: #6b7280; margin-bottom: 2px; }
@media (prefers-color-scheme: dark) {
  .m-user { background: #172554; color: #dbeafe; border-left-color: #60a5fa; }
  .m-assistant { background: #052e16; color: #bbf7d0; border-left-color: #22c55e; }
  .m-tool { background: #2e1065; color: #ddd6fe; border-left-color: #a78bfa; }
  .m-tool-err { background: #450a0a; color: #fecaca; border-left-color: #ef4444; }
  .m-system { background: #1f2937; color: #9ca3af; border-left-color: #6b7280; }
  .role-tag { color: #9ca3af; }
}
details.think { background: #fffbeb; border-left: 4px solid #f59e0b; border-radius: 10px;
                padding: 6px 12px; margin: 8px 0; }
details.think summary { cursor: pointer; color: #b45309; font-size: 13px; }
details.think pre { white-space: pre-wrap; font-family: inherit; font-size: 14px; margin: 6px 0 2px; color: #1c1e21; }
@media (prefers-color-scheme: dark) {
  details.think { background: #2a2008; }
  details.think summary { color: #fbbf24; }
  details.think pre { color: #fde68a; }
}
code.json { background: #f8fafc; color: #1c1e21; border-radius: 8px; padding: 8px 10px; display: block;
            font-family: Consolas, monospace; font-size: 12.5px; overflow-x: auto;
            white-space: pre-wrap; word-break: break-word; }
@media (prefers-color-scheme: dark) { code.json { background: #11151a; color: #e5e7eb; } }
.err-note { color: #dc2626; font-size: 12px; }
@media (prefers-color-scheme: dark) { .err-note { color: #f87171; } }
"""


def _esc(text: Any) -> str:
    return html.escape(str(text))


def _render_message(m: Dict[str, Any]) -> str:
    role = m.get("role", "")
    parts: List[str] = []

    if role == "assistant":
        reasoning = m.get("reasoning_content")
        content = m.get("content", "")
        if reasoning:
            parts.append(
                f'<details class="think"><summary>🧠 思考（{len(reasoning)} 字）</summary>'
                f"<pre>{_esc(reasoning)}</pre></details>"
            )
        if content:
            parts.append(f'<div class="msg m-assistant"><div class="role-tag">assistant</div>{_esc(content)}</div>')
        for tc in m.get("toolCalls", []):
            payload = {"name": tc.get("name"), "arguments": tc.get("input", {})}
            parts.append(
                f'<div class="msg m-assistant"><div class="role-tag">tool call</div>'
                f'<code class="json">{_esc(json.dumps(payload, ensure_ascii=False, indent=1))}</code></div>'
            )
    elif role == "tool":
        cls = "m-tool m-tool-err" if m.get("isError") else "m-tool"
        note = ' <span class="err-note">⚠ isError</span>' if m.get("isError") else ""
        parts.append(
            f'<div class="msg {cls}"><div class="role-tag">tool {m.get("toolCallId", "")[:12]}{note}</div>'
            f"{_esc(m.get('content', ''))}</div>"
        )
    elif role == "system":
        parts.append(f'<div class="msg m-system"><div class="role-tag">system</div>{_esc(m.get("content", ""))}</div>')
    else:  # user
        parts.append(f'<div class="msg m-user"><div class="role-tag">user</div>{_esc(m.get("content", ""))}</div>')
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
        f"{msgs_html}</div>"
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
