"""Render an offline, standalone report of the measured latent-route swap arms."""
from __future__ import annotations

import argparse
from collections import Counter
from html import escape
import json
from pathlib import Path
from statistics import mean


ARMS = {
    "base": "原始轨迹", "self": "同源自交换", "zero": "全部槽位置零", "all": "全部槽交换",
    "forward": "正向", "backward": "反向", "alternatives": "替代方案",
    "constraints": "约束", "counterexamples": "反例",
}
HORIZONS = {"first_block": "首个共享块", "natural": "按原轨迹长度重放"}
STYLE = """
:root{font:16px/1.55 system-ui,sans-serif;color:#172f39;background:#edf3f4}
body{max-width:1120px;margin:32px auto;padding:0 30px}
header,section{background:white;border:1px solid #d6e2e4;border-radius:12px;padding:20px 26px;margin:14px 0}
h1,h2,h3{line-height:1.25}h1{font-size:2.1rem}h2{font-size:1.4rem}h3{font-size:1.08rem}
header{border-top:5px solid #286663}small,caption{color:#405c67}summary{cursor:pointer;font-weight:600}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:.91rem;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid #d6e2e4;text-align:right;padding:6px 9px}th:first-child,td:first-child{text-align:left}
th{background:#f1f6f6}pre{overflow:auto;white-space:pre-wrap;word-break:break-word}
a{color:#155d6d}code{overflow-wrap:anywhere}details{margin:8px 0}
"""


def html(value):
    return escape(str(value), quote=True)


def number(value):
    return "—" if value is None else f"{value:+.4f}"


def table(headings, records):
    heading = "".join(f"<th>{html(cell)}</th>" for cell in headings)
    body = "".join("<tr>" + "".join(f"<td>{html(cell)}</td>" for cell in row)
                   + "</tr>" for row in records)
    return f"<table><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table>"


def summary_table(rows):
    arms = sorted({row["arm"] for row in rows}, key=lambda arm: list(ARMS).index(arm) if arm in ARMS else len(ARMS))
    records = []
    for arm in arms:
        group = [row for row in rows if row["arm"] == arm]
        scored = [row for row in group if row.get("date_delta") is not None and row.get("contact_delta") is not None]
        records.append([ARMS.get(arm, arm), f"{len(scored)}/{len(group)}",
            number(mean(row["date_delta"] for row in scored)) if scored else "—",
            number(mean(abs(row["contact_delta"]) for row in scored)) if scored else "—",
            sum(row["date_delta"] > 0 for row in scored)])
    return table(["干预", "可评分 / 记录", "平均日期 Δ", "平均 |联系人 Δ|", "日期朝供体移动数"], records)


def late_takeaway(rows):
    late = [row for row in rows if row["horizon"] == "natural"]
    baselines = {(row["pair"], row["recipient"]): row for row in late if row["arm"] == "base"}
    singles = [row for row in late if row["arm"] not in {"base", "self", "all", "zero"}]
    comparable = [(row, baselines[(row["pair"], row["recipient"])]) for row in singles
                  if (row["pair"], row["recipient"]) in baselines]
    comparable = [(row, base) for row, base in comparable if all(
        entry.get(field) is not None for entry in (row, base) for field in ("day_margin", "contact_margin"))
        and row.get("date_delta") is not None]
    if not comparable:
        return "<section><h2>这次看到什么</h2><p>完整长度时点暂无可与原始轨迹配对评分的单路交换，不能判断。</p></section>"
    covered = {(row["pair"], row["recipient"]): base for row, base in comparable}
    count = len(comparable)
    donor_preferred = sum(row["day_margin"] > 0 for row, _ in comparable)
    flipped_to_donor = sum(base["day_margin"] < 0 < row["day_margin"] for row, base in comparable)
    baseline_donor = sum(base["day_margin"] > 0 for base in covered.values())
    moved = sum(row["date_delta"] > 0 for row, _ in comparable)
    contact_stable = sum(row["contact_margin"] > 0 and base["contact_margin"] > 0 for row, base in comparable)
    details = []
    for arm in dict.fromkeys(row["arm"] for row, _ in comparable):
        deltas = {(row["pair"], row["recipient"]): row["date_delta"] for row, _ in comparable if row["arm"] == arm}
        pairs = [pair for pair in dict.fromkeys(pair for pair, _ in deltas) if (pair, 0) in deltas and (pair, 1) in deltas]
        positive = sum(min(deltas[pair, 0], deltas[pair, 1]) > 0 for pair in pairs)
        details.append(f"{ARMS.get(arm, arm)} {positive}/{len(pairs)}" if pairs else f"{ARMS.get(arm, arm)} 无成对记录")
    return ("<section><h2>这次看到什么</h2><p>按原轨迹长度重放后，"
        f"{count} 个可对照的单路交换中，{moved} 个日期分差向供体移动（Δ &gt; 0）；"
        f"最终在强制二选一时偏向供体日期的是 {donor_preferred}/{count}，"
        f"配对的原始轨迹是 {baseline_donor}/{len(covered)}。实际从接收方翻转为供体的是 {flipped_to_donor}/{count}。</p>"
        f"<p>在联系人二选一中，基线与交换后都偏向正确姓氏的是 {contact_stable}/{count}。"
        "相对移动不等于翻转；选项不变也不代表分差不变。"
        f"两个交换方向均向供体移动的完整对话对：{html('；'.join(details))}。</p>"
        "<small>分母仅含有相应配对分数的观测。不同路线共享题目和基线。这里没有衡量自由输出，也不能单凭这些计数认定方向分工。</small></section>")


def render(directory):
    metadata = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (directory / "rows.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    sections = [
        '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8"><title>五路隐向量交换实验</title>',
        f"<style>{STYLE}</style><main><header><small>QWEN · 本地冻结权重实验</small>",
        "<h1>只换一路，日期判断会改变吗？</h1>",
        "<p>收件对话只改变星期，联系人不变。分别考察日期是否向供体移动、无关的联系人判断是否漂移。</p>",
        "<p>这里测量的是固定答案前缀下的候选词分数，不是自由回答正确率，也不是五路文字解读。</p>",
        "<p><a href='results.json'>运行收据</a> · <a href='rows.jsonl'>逐臂原始分数</a></p></header>",
        "<section><h2>运行记录</h2>",
        table(["状态", "记录数", "耗时", "峰值显存", "付费调用", "优化步"],
              [[("完成" if metadata.get("complete") else "未完成"), len(rows),
                f"{metadata['elapsed_seconds']:.1f} s", f"{metadata['peak_cuda_bytes'] / (1024**3):.2f} GiB",
                metadata["paid_api_calls"], metadata["optimizer_steps"]]]),
        f"<small>实验权重 SHA-256：<code>{html(metadata['checkpoint_sha256'])}</code></small></section>",
        "<section><h2>如何读数</h2><p>日期差 = 供体日期分数 − 接收方原日期分数；联系人差 = 正确姓氏分数 − 对照姓氏分数。"
        "Δ 表示相对同题、同一接收方、同一观测时点的原始轨迹变化。日期 Δ 为正表示向供体靠近，不代表答案已翻转。"
        "联系人 Δ 的绝对值越大，对无关事实的扰动越强。缺失的分数以「—」表示，不当作零。</p>"
        "<p>干预只作用于前八次写入融合层的连续向量；随后由实际模型继续计算各路状态和缓存。"
        "较长时点固定使用接收方原生轨迹的各路存活掩码，匹配计算长度，并非所有干预臂自行自然结束。"
        "候选差由冻结词表头中两个 BF16 行向量与末隐状态在 FP32 中计算，使用输出舍入前的分数。</p>"
        "<p>前八次全部槽位置零仍经过原融合层；同源自交换用于确认重放一致性。原对话仍可被模型读取，"
        "无明显变化不能证明向量不含日期。六个方向来自三对相关对话，不是独立大样本。</p></section>", late_takeaway(rows),
    ]
    for horizon, label in HORIZONS.items():
        group = [row for row in rows if row["horizon"] == horizon]
        sections.extend([f"<section><h2>{label}</h2>", summary_table(group)])
        stops = Counter(row.get("stop") for row in group)
        sections.append("<small>停止记录：" + " · ".join(f"{html(stop)} × {count}" for stop, count in stops.items()) + "</small></section>")
    for pair in metadata["pairs"]:
        pair_id = pair["id"]
        sections.append(f"<section><h2>{html(pair_id)}</h2>")
        for recipient, day in enumerate(pair["days"]):
            donor_day = pair["days"][1 - recipient]
            sections.append(f"<h3>接收方周{html(day)} ← 供体周{html(donor_day)}</h3>")
            messages = "\n".join(f"{message['role']}：{message['content'].replace('{day}', day)}" for message in pair["messages"])
            sections.append(f"<details><summary>完整接收方对话与候选</summary><pre>{html(messages)}</pre>"
                f"<p>日期前缀：{html(pair['date_prefix'])}；联系人前缀：{html(pair['contact_prefix'])}；"
                f"正确姓氏 / 对照：{html(' / '.join(pair['contacts']))}</p></details>")
            for horizon, label in HORIZONS.items():
                subset = [row for row in rows if row["pair"] == pair_id and row["recipient"] == recipient and row["horizon"] == horizon]
                sections.append(f"<details><summary>{label} · {len(subset)} 组</summary>")
                sections.append(table(["干预", "轮数", "停止", "日期差", "日期 Δ", "联系人差", "联系人 Δ"],
                    [[ARMS.get(row["arm"], row["arm"]), row["rounds"], row.get("stop"),
                      number(row.get("day_margin")), number(row.get("date_delta")), number(row.get("contact_margin")),
                      number(row.get("contact_delta"))] for row in subset]))
                sections.append("</details>")
        sections.append("</section>")
    sections.append("</main></html>")
    output = directory / "report.html"
    output.write_text("\n".join(sections), encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(render(parser.parse_args().directory))
