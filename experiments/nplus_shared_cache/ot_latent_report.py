"""Render complete answers from a finished paired OpenThoughts evaluation."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from html import escape
import json
from pathlib import Path
from statistics import mean


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def html(value):
    return escape(str(value))


def gib(size):
    return f"{size / (1024 ** 3):.2f} GiB"


def extended_comparison(folder):
    path = folder / "extended-case-01.json"
    if not path.is_file():
        return ""
    probe = read(path)
    if "matched_total_calls_per_arm" not in probe:
        return ""
    panels = []
    for n, a in sorted(probe["arms"].items()):
        panels.append(f"<section><h4>{n} 路</h4><pre>{html(a['text'])}</pre>"
                      f"<p>停止原因 {html(a['stop'])} · 生成 {a['answer_active_calls']} 步"
                      f" · 出答案 {a['seconds_to_stop']:.2f} 秒 · 配平 {a['seconds']:.2f} 秒</p></section>")
    return (f"<h2 id='extended'>首题长预算复核</h2><p>单独复载，最多 {probe['binding']['ceiling']} 个答案 token，"
            f"两边各实际调用主干 {probe['matched_total_calls_per_arm']} 次。与上表独立计分。</p>"
            f"<article><h3>原题 <small>参照 {html(probe['reference'])}</small></h3><pre class='question'>"
            f"{html(probe['question'])}</pre><div class='pair'>{''.join(panels)}</div>"
            "<a href='extended-case-01.json'>长预算完整测量</a></article>")


def render(folder):
    result = read(folder / "eval-results.json")
    cases = result["cases"]
    if not cases:
        raise ValueError("evaluation has no cases")
    if any(c["arms"]["1"]["total_calls"] != c["arms"]["5"]["total_calls"] for c in cases):
        raise ValueError("paired backbone budgets differ")
    rows, cards, coverage = [], [], []
    audit_file = folder / "eval-reference-audit.jsonl"
    audit = {}
    if audit_file.is_file():
        for line in audit_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                audit[item["id"]] = item
    residency = []
    if (folder / "wddm.csv").is_file():
        with (folder / "wddm.csv").open(encoding="utf-8-sig", newline="") as stream:
            residency = list(csv.DictReader(stream))
    residency_lines = []
    for phase in sorted({row["arm_phase"] for row in residency}):
        current = [r for r in residency if r["arm_phase"] == phase]
        dedicated = max(int(r["dedicated_bytes"]) for r in current)
        shared = max(int(r["shared_bytes"]) for r in current)
        residency_lines.append(f"<li>{html(phase)}：专用 {gib(dedicated)}，共享 {gib(shared)}，{len(current)} 次采样。</li>")
    extras = []
    if audit:
        extras.append('<a href="eval-reference-audit.jsonl">参照核验</a>')
    if residency:
        extras.append('<a href="wddm.csv">显存采样</a>')
    extra_links = ''.join(' · ' + link for link in extras)
    novelty = Counter()
    for c in cases:
        novelty[(bool(c["arms"]["1"]["correct"]), bool(c["arms"]["5"]["correct"]))] += 1
    for n in ("1", "5"):
        receipt = read(folder / f"training-routes-{n}.json")
        arms = [c["arms"][n] for c in cases]
        hits = sum(bool(a["correct"]) for a in arms)
        source_hits = sum(a["stop"] == "eos" and a["parsed"] == str(c.get("source_reference", c["reference"]))
                          for c, a in zip(cases, arms))
        reached = sum(a["stop"] == "eos" for a in arms)
        halts = [s for a in arms for s in a["per_route_stop"]]
        signaled = sum(stop is not None for stop in halts)
        most_frequent, frequency = Counter(a["text"].strip() for a in arms).most_common(1)[0]
        early_stops = sum(t is not None and t < a["latent_calls"] for a in arms for t in a["per_route_stop"])
        staggered = sum(len({a["latent_calls"] if t is None else t for t in a["per_route_stop"]}) > 1
                        for a in arms)
        coverage.append(f"<li>{n} 路：{receipt['trainable_parameters']:,} 个参数；"
                        f"{len(receipt['nonzero_gradient_tensors'])} 个张量有非零梯度，"
                        f"{len(receipt['changed_tensors'])} 个张量在训练后摘要发生变化。"
                        f"最常输出 <code>{html(most_frequent)}</code>，出现 {frequency}/{len(cases)} 次。"
                        f"达到上限前自行结束 {early_stops}/{len(halts)} 路，有 {staggered} 题出现不同停步。"
                        f"以源教师原答案为准时命中 {source_hits}/{len(cases)}。</li>")
        rows.append(f"""<tr><th>{n} 路</th><td>{hits}/{len(cases)} · {hits/len(cases):.1%}</td>
<td>{receipt['n_rows']} 题 · {receipt['n_updates']} 次更新</td><td>{receipt['elapsed_seconds']:.1f} 秒</td>
<td>{gib(receipt['peak_cuda_bytes'])}</td><td>{mean(a['seconds_to_stop'] for a in arms):.2f} 秒</td>
<td>{mean(a['seconds'] for a in arms):.2f} 秒</td><td>{gib(max(a['peak_cuda_bytes'] for a in arms))}</td>
<td>{reached}/{len(arms)}</td><td>{signaled}/{len(halts)}</td></tr>""")
    for index, c in enumerate(cases, 1):
        outputs = []
        for n in ("1", "5"):
            a = c["arms"][n]
            verdict = "正确" if a["correct"] else "未命中"
            cls = "good" if a["correct"] else "miss"
            stops = " / ".join("到上限" if t is None else str(t) for t in a["per_route_stop"])
            outputs.append(f"""<section><h4>{n} 路 <span class="{cls}">{verdict}</span></h4>
<pre>{html(a['text'])}</pre><p>解析值 {html(a['parsed'])} · {html(a['stop'])} · 生成 {a['answer_active_calls']} 步
 · 共 {a['total_calls']} 次主干 · 出答案 {a['seconds_to_stop']:.2f} 秒 · 配平 {a['seconds']:.2f} 秒
 · 峰值 {gib(a['peak_cuda_bytes'])}</p><p>各路判停步 {html(stops)}</p></section>""")
        verified = audit.get(c["id"], {})
        proof = f"<p>参照依据：{html(verified['reason'])}　原数据答案：{html(verified['teacher'])}</p>" if verified.get("reason") else ""
        cards.append(f"""<article id="case-{index:02}"><h3>题 {index:02} <small>参照值 {html(c['reference'])}</small></h3>
<pre class="question">{html(c['question'])}</pre>{proof}<div class="pair">{''.join(outputs)}</div>
<small>问题指纹 {html(c['id'])}</small></article>""")
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Qwen3.5 · 五路与单路实测</title>
<style>body{{margin:0;background:#f6f7f5;color:#172927;font:16px/1.55 system-ui,sans-serif}}main{{max-width:1340px;margin:36px auto;padding:0 24px}}h1,h2,h3,h4{{line-height:1.3}}
h1{{font-size:36px}}h2{{margin-top:32px}}a{{color:#10665b}}table,article{{background:white;border:1px solid #d8dedb;border-radius:8px}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #e1e6e3}}article{{margin:12px 0;padding:16px 20px}}
h3,h4,p{{margin:8px 0}}small,section p{{color:#52635e;font-size:13px}}h3 small,h4 span{{margin-left:12px}}.pair{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
section{{min-width:0}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f5f3;padding:12px;font:15px/1.55 ui-monospace,monospace}}
.question{{background:transparent;padding:6px 0;font:inherit}}.good{{color:#0c7458}}.miss{{color:#a54830}}</style><main>
<h1>Qwen3.5-4B · 五路与单路实测</h1><p>OpenThoughts3 数学留出题，两个独立微调的完整模型。自由生成后按数值评分。每题配平实际主干调用。模型加载、校验文件与训练用时单独计算。</p>
<table><thead><tr><th>模型</th><th>命中</th><th>训练</th><th>训练用时</th><th>训练显存</th><th>出答案均时</th><th>配平均时</th><th>推理显存</th><th>答案自然结束</th><th>隐路自行结束</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<p>配对结果：两边都对 {novelty[(True,True)]}，五路独对 {novelty[(False,True)]}，单路独对 {novelty[(True,False)]}，两边都没命中 {novelty[(False,False)]}。</p>
<ul>{''.join(coverage)}</ul>
<p>正确率以独立核验的数值为准，每题均计入分母。任务是英语数学题，统一要求仅给最终数值，自由贪心解码，最多 {result['answer_budget']} 个答案 token。没有提供候选答案。四次潜在主干步允许每路提前结束，停止后的槽位补零。文字观察全程关闭。</p>
<p>显存为 PyTorch 进程峰值 allocated。出答案用时从提示预填计至答案结束或到达上限，包含隐步预算内的补齐调用。配平用时还包括答案结束后的占位主干前向；这些占位跳过了词表投影。因此主干次数相等，选词开销仍会不同。默认部署可提前停止。训练留出不能排除基座预训练曾见相同问题。</p>
<p>Windows 驱动可将部分分配放在共享系统内存。以下为独立采样的进程占用最大值，各列峰值可能发生在不同时间。</p><ul>{''.join(residency_lines)}</ul>
<p><a href="eval-results.json">逐题原始测量</a> · <a href="training-routes-1.json">单路训练收据</a> · <a href="training-routes-5.json">五路训练收据</a>{extra_links}</p>
{extended_comparison(folder)}<h2>全部 {len(cases)} 道题的原始输出</h2>{''.join(cards)}</main></html>"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    directory = parser.parse_args().run_directory
    (directory / "report.html").write_text(render(directory), encoding="utf-8")
