"""Compact, source-backed AI package-review evidence shared by delivery views."""
from __future__ import annotations

import html
from typing import Any


PACKAGE_REVIEW_STYLE = """<style>
.df-ai-review{margin:12px 0;padding:14px 15px;border:1px solid #dce8f5;border-radius:10px;
 background:linear-gradient(130deg,#f6faff,#fff 65%);color:#294766}
.df-ai-review-head{display:flex;align-items:center;justify-content:space-between;gap:12px}
.df-ai-review-head strong{font-size:14px}.df-ai-review-mode{padding:3px 8px;border-radius:6px;
 background:#e8f2ff;color:#176ac2;font-size:11px;white-space:nowrap}
.df-ai-review-coverage{display:flex;align-items:baseline;gap:10px;margin:11px 0 7px;
 color:#6b8099;font-size:12px}.df-ai-review-coverage b{font-size:22px;color:#176ac2}
.df-ai-review-track{height:5px;border-radius:8px;background:#e5edf7;overflow:hidden}
.df-ai-review-track i{display:block;height:100%;background:linear-gradient(90deg,#42b7bd,#267bdf)}
.df-ai-review-scroll{overflow-x:auto;margin-top:9px}
.df-ai-review-table{width:100%;border-collapse:collapse;white-space:nowrap;font-size:11px}
.df-ai-review-table th{padding:7px 8px;color:#73869d;font-weight:500;text-align:right}
.df-ai-review-table td{padding:8px;border-top:1px solid #e8eef6;text-align:right;color:#294766}
.df-ai-review-table th:first-child,.df-ai-review-table td:first-child{text-align:left;padding-left:0}
.df-ai-review-table td[data-kind=accepted]{color:#159079}.df-ai-review-table td[data-kind=rejected]{color:#b5703f}
.df-ai-review-note{margin:9px 0 0;color:#6c8098;font-size:11px;line-height:1.6}
@media(max-width:600px){.df-ai-review{padding:12px}.df-ai-review-coverage{flex-wrap:wrap;gap:5px 10px}}
</style>"""


def _safe(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def package_review_report_html(quality: dict) -> str:
    """Show only recorded review evidence; old and disabled runs add no empty box."""
    report = quality.get("package_review")
    if not isinstance(report, dict) or not report.get("enabled"):
        return ""
    details = report.get("targets") or {}
    if not isinstance(details, dict):
        details = {}
    rows = [(str(target), row) for target, row in details.items() if isinstance(row, dict)]
    fields = ("candidates", "reviewed", "accepted", "rejected", "unreviewed")
    totals = {field: sum(_count(row.get(field)) for _, row in rows) for field in fields}
    coverage = 100 * totals["reviewed"] / totals["candidates"] if totals["candidates"] else 0
    mode = "抽样评审" if report.get("mode") == "sample" else "全量评审"
    table_rows = []
    for target, row in rows:
        counts = {field: _count(row.get(field)) for field in fields}
        ratio = 100 * counts["reviewed"] / counts["candidates"] if counts["candidates"] else 0
        table_rows.append(
            '<tr><td><strong>' + _safe(target.upper()) + '</strong></td>'
            + ''.join('<td data-kind="' + field + '">' + f"{counts[field]:,}" + '</td>'
                      for field in fields)
            + '<td>' + f"{ratio:.1f}%" + '</td></tr>'
        )
    headers = ("训练目标", "评审前样本", "已评审", "AI 通过", "AI 隔离", "未评审", "覆盖率")
    if totals["candidates"] == 0:
        note = "本次没有可供 AI 评审的样本。"
    elif not totals["unreviewed"]:
        note = "AI 评审已覆盖本次进入打包的全部样本。"
    elif report.get("mode") == "sample":
        note = "未抽中的样本沿用上游质检结果；抽样评审不代表所有样本均已通过 AI 评审。"
    elif totals["unreviewed"]:
        note = "全量评审尚未覆盖所有样本，请查看任务处理记录。"
    else:
        note = "AI 评审已覆盖本次进入打包的全部样本。"
    table = ('<div class="df-ai-review-scroll"><table class="df-ai-review-table"><thead><tr>'
             + ''.join('<th>' + label + '</th>' for label in headers)
             + '</tr></thead><tbody>' + ''.join(table_rows) + '</tbody></table></div>') if rows else ''
    escalated = any(isinstance(row.get("escalation"), dict) and row["escalation"].get("escalated")
                    for _, row in rows)
    escalation_note = ('<p class="df-ai-review-note">抽检发现问题后，部分轮次扩大为全量评审；范围与阈值保存在报告中。</p>'
                       if escalated else '')
    return (PACKAGE_REVIEW_STYLE + '<div class="df-ai-review"><div class="df-ai-review-head">'
            '<strong>AI 打包评审</strong><span class="df-ai-review-mode">' + mode + '</span></div>'
            '<div class="df-ai-review-coverage"><b>' + f"{coverage:.1f}%" + '</b><span>评审覆盖率</span>'
            '<span>已评审</span><strong>' + f'{totals["reviewed"]:,} / {totals["candidates"]:,}'
            + '</strong></div><div class="df-ai-review-track"><i style="width:'
            + f"{min(100, coverage):.1f}" + '%"></i></div>' + table
            + '<p class="df-ai-review-note">' + note + '</p>' + escalation_note
            + '<p class="df-ai-review-note">AI 隔离样本不进入训练文件；评审证据随数据包保存。</p></div>')
