"""Replay saved review calls offline; never instantiate a provider or regenerate a candidate."""
from __future__ import annotations
import argparse
from copy import deepcopy
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.contexts.workflows.domain.reasoning_evidence import ReasoningEvidence
from lib.contexts.workflows.domain.reasoning_workspace import ReasoningWorkspace
from lib.contexts.workflows.application.check_reasoning import CheckReasoning


def project_check(entry, workspace):
    """Explicit wire-contract projection, not a replayed/new model response.

Retain every original opinion and citation. Only inactive clauses are excluded
from submission, with their originals still present in the report. A composite
rule uses the strongest original gap; nothing changes a negative into a pass.
"""
    wrapped = entry.get("check", entry)
    check = {k: deepcopy(wrapped[k]) for k in ("rule_id", "status", "evidence", "reason") if k in wrapped}
    check["evidence"] = [{"source": e.get("field", e.get("source")), "quote": e["quote"]} for e in check.get("evidence", [])]
    observations = entry.get("observations", [])
    mapped = []
    priority = {"violated": 3, "unknown": 2, "satisfied": 1, "not_applicable": 0}
    for requirement in workspace.requirements:
        if not requirement["active"] or requirement["rule_id"] != check.get("rule_id"):
            continue
        selected = [o for o in observations if (requirement["requirement"] in o.get("requirement_quote", "")
                    or o.get("requirement_quote", "") in requirement["requirement"])]
        if requirement["id"].endswith(":body"):
            selected = observations  # new protocol references the same frozen rule by ID
        if not selected:
            continue  # missing evidence remains missing; never synthesize a vote
        mapped.append({"requirement_id": requirement["id"],
            "status": max((o["status"] for o in selected), key=lambda status: priority[status]),
            "reason": "\n".join(o["reason"] for o in selected),
            "evidence": [{"source": e.get("field", e.get("source")), "quote": e["quote"]} for o in selected for e in o["evidence"]]})
    return {**check, "observations": mapped}


def project_call(name, arguments, workspace):
    if name == "submit_review" and isinstance(arguments, dict):
        return {"checks": [project_check(c, workspace) for c in arguments.get("checks", [])],
                "claims": [project_call("record_claim", c, workspace) for c in arguments.get("claims", [])]}
    if name == "record_check":
        return project_check(arguments, workspace)
    if name == "record_claim" and "claim" in arguments:
        return {**deepcopy(arguments["claim"]), "interpretation": deepcopy(arguments["interpretation"])}
    return deepcopy(arguments)


def leaves(receipt):
    return [e["result"] for e in receipt.get("entries", [])] if "entries" in receipt else [receipt]


def replay(fixture):
    results = []
    totals = {"old_errors": Counter(), "new_errors": Counter(), "old_accepted_entries": 0, "new_accepted_entries": 0,
              "candidate_bytes_unchanged": True, "api_calls": 0}
    for index, session in enumerate(fixture["sessions"]):
        inputs = session["inputs"]
        context = {**inputs.get("task", {})}
        # Preserve the recorded source IDs/text exactly. The historical adapter
        # had already resolved these passages; do not rediscover source material.
        old = ReasoningEvidence(inputs["rules"], context, inputs["candidate"])
        new = ReasoningWorkspace(inputs["rules"], context, inputs["candidate"], phase=inputs["phase"])
        passages = deepcopy(inputs.get("provided_sources", []))
        old.passages = deepcopy(passages)
        new.passages = deepcopy(passages)
        steps = []
        original = deepcopy(inputs["candidate"])
        for trace in session["trace"]:
            function = trace["call"]["function"]
            try:
                arguments = json.loads(function["arguments"])
            except json.JSONDecodeError:
                steps.append({"tool": function["name"], "raw": function["arguments"], "both": "invalid_json_not_executed"})
                continue
            before = old.execute(function["name"], arguments)
            projected = project_call(function["name"], arguments, new)
            after = new.execute(function["name"], projected)
            for prefix, receipt in (("old", before), ("new", after)):
                for row in leaves(receipt):
                    if row.get("ok"):
                        totals[prefix + "_accepted_entries"] += 1
                    else:
                        totals[prefix + "_errors"][row.get("issue", "unknown")] += 1
            steps.append({"tool": function["name"], "original_arguments": arguments, "projected_arguments": projected,
                          "old": before, "new": after})
        totals["candidate_bytes_unchanged"] &= old.candidate == new.candidate == original
        results.append({"case_id": session["case_id"], "session_index": index, "key": session["key"],
                        "phase": inputs["phase"], "candidate": original, "task": inputs.get("task", {}),
                        "steps": steps, "old": old.snapshot(), "new": new.snapshot()})
    return {"method": "Offline recorded-call replay with explicitly projected v5 wire fields. No model response generated; no model accuracy claim.",
            "source_sha256": fixture["sha256"], "model_of_original_records": fixture["model"],
            "totals": totals, "sessions": results, "minimal_pairs": comparison_probes(), "scheduler_probe": scheduler_probe()}


def scheduler_probe():
    class ReadOnlyPort:
        def __init__(self):
            self.calls = 0
        def check_reasoning_cancel(self):
            pass
        def prepare_reasoning_context(self, context):
            return deepcopy(context)
        def reasoning_progress(self, *args):
            pass
        def reasoning_tool_turn(self, key, role, messages, tools):
            self.calls += 1
            return {"role": "assistant", "content": "", "tool_calls": [{"id": f"call-{self.calls}", "type": "function",
                "function": {"name": "read_source", "arguments": json.dumps({"source_id": "source", "start": 0, "length": 5})}}]}
    rows = []
    for version in (4, 5):
        port = ReadOnlyPort()
        config = {"version": version, "rules": [{"id": "scope", "requirement": "Check the stated scope.", "allow_na": False}]}
        audit = CheckReasoning(port, config).execute(["offline-repeat"], {"source": "Known source."}, {"reasoning": "Bounded explanation."}, phase="prefix")
        rows.append({"version": version, "scripted_agent_turns": port.calls, "passed": audit["passed"], "issue": audit["issue"]})
    return {"method": "Scripted repeated read using real session loop; no model API, no prediction of future model behavior.", "runs": rows}


def comparison_probes():
    rows = []
    for text, expected in (("覆盖率为50.6%，不到一半。", False), ("覆盖率为50.6%，超过一半。", True),
                           ("Coverage is 49.4%, less than half.", True), ("Coverage is 49.4%, more than half.", False),
                           ("覆盖率为50%，至少一半。", True), ("覆盖率为50%，超过一半。", False)):
        body = {"reasoning": text}
        old, new = ReasoningEvidence([], {}, body), ReasoningWorkspace([], {}, body, phase="prefix")
        anchor = old.anchors[0]
        claim = {"anchor_id": anchor["id"], "status": "supported", "evidence": [{"source": "reasoning", "quote": text}],
                 "reason": "Controlled optimistic vote, not a real model response.", "counterexample": "Check the strict comparison.",
                 "calculations": [], "no_calculation_reason": "Reported percentage; verbal comparison is the target."}
        interpretation = {"stance": "asserted", "context_quote": text, "explanation": "Direct assertion."}
        old.execute("record_claim", {"claim": claim, "interpretation": interpretation})
        new.execute("record_claim", {**claim, "interpretation": interpretation})
        rows.append({"text": text, "expected_pass": expected, "old_pass": old.snapshot()["passed"], "new_pass": new.snapshot()["passed"],
                     "receipt": new.snapshot()["assertions"], "method": "Minimal-pair validator probe. No model generated or judged these votes."})
    return rows


def document(report):
    esc = lambda value: html.escape(str(value))
    dump = lambda value: esc(json.dumps(value, ensure_ascii=False, indent=2))
    totals = report["totals"]
    errors = sorted(set(totals["old_errors"]) | set(totals["new_errors"]))
    rows = ''.join(f'<tr><td>{esc(k)}</td><td>{totals["old_errors"].get(k,0)}</td><td>{totals["new_errors"].get(k,0)}</td></tr>' for k in errors)
    probes = ''.join(f'<tr><td>{esc(p["text"])}</td><td>{"成立" if p["expected_pass"] else "错误"}</td><td>{"通过" if p["old_pass"] else "拦截"}</td><td>{"通过" if p["new_pass"] else "拦截"}</td></tr>' for p in report['minimal_pairs'])
    scheduling = report['scheduler_probe']['runs']
    validation = report.get('validation', {})
    validation_rows = ''.join(f'<tr><td>{esc(r["name"])}</td><td>{r["tests"]}</td><td>{r["failures"]}</td><td>{r["skipped"]}</td></tr>' for r in validation.get('runs', []))
    cases = []
    for s in report["sessions"]:
        steps = ''.join(f'<details><summary>{i+1}. {esc(t["tool"])}</summary><pre>{dump(t)}</pre></details>' for i,t in enumerate(s['steps']))
        cases.append(f'''<section id="session-{s['session_index']}"><h2>样本 {esc(s['case_id'])} · {esc(s['key'][-1])}</h2>
        <p>原完整稿件原样保留 · 阶段 {esc(s['phase'])}</p>
        <div class="states"><span>旧协议：{esc(state(s['old']))}</span><span>新协议：{esc(state(s['new']))}</span></div>
        <h3>完整用户任务</h3><pre>{dump(s['task'].get('prompt', s['task']))}</pre>
        <h3>完整推理</h3><pre class="prose">{esc(s['candidate'].get('reasoning',''))}</pre>
        <h3>完整回答</h3><pre class="prose">{esc(s['candidate'].get('answer','（前缀阶段无答案）'))}</pre>
        <details><summary>适用条款与确定性核查</summary><pre>{dump({'requirements':s['new']['requirements'],'assertions':s['new']['assertions'],'pending':s['new']['pending']})}</pre></details>
        <h3>工具调用前后对照 · {len(s['steps'])} 项</h3>{steps}</section>''')
    return f'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>Agent 环境协议 · 离线对比</title>
    <style>*{{box-sizing:border-box}}body{{margin:0;background:#eef3fa;color:#192c44;font:16px/1.65 "Segoe UI","Microsoft YaHei",sans-serif}}main{{max-width:1180px;margin:40px auto;padding:0 26px}}header{{padding:34px 38px;background:linear-gradient(125deg,#183f73,#296fa4);color:white;border-radius:22px;box-shadow:0 15px 40px #244b7826}}h1{{margin:8px 0;font-size:34px}}h2{{font-size:23px;margin:0 0 12px}}h3{{font-size:16px}}section,.panel{{padding:28px 32px;margin:24px 0;background:white;border:1px solid #dce5f0;border-radius:16px;box-shadow:0 6px 18px #203b5710}}.stats,.states{{display:flex;gap:18px;flex-wrap:wrap}}.stats b{{font-size:30px;display:block}}.stats>div{{flex:1;padding:20px;background:#f0f6fc;border-radius:12px}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{text-align:left;padding:10px;border-bottom:1px solid #e3eaf3}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.7 Consolas,monospace;background:#f5f8fc;padding:18px;border-radius:9px;max-height:640px;overflow:auto}}pre.prose{{font:15px/1.9 "Segoe UI","Microsoft YaHei",sans-serif;max-height:none}}details{{border:1px solid #e2e9f2;border-radius:9px;padding:12px 16px;margin:10px 0}}summary{{cursor:pointer;color:#2469a5;font-weight:600}}a{{color:#2469a5}}.note{{color:#546b83}}.states span{{background:#edf5ff;border-radius:8px;padding:8px 14px}}</style>
    <main><header><small>数简立方 / OFFLINE VERIFICATION</small><h1>让 Agent 知道自己正在检查什么</h1><p>相同稿件、相同已保存意见，对比环境协议与验证器。全程零模型 API。</p></header>
    <div class="panel"><div class="stats"><div><b>{len(report['sessions'])}</b>真实历史核查会话</div><div><b>{sum(totals['old_errors'].values())} → {sum(totals['new_errors'].values())}</b>工具提交错误</div><div><b>0</b>新增 API 调用</div></div>
    <p>推理与答案字节保持：<strong>{'全部一致' if totals['candidate_bytes_unchanged'] else '失败'}</strong></p>
    <p class="note">这是离线协议回放。新字段由可审查的转换器投影，未伪造新模型响应；有效提交数不是样本正确率。完整缺项仍阻止通过，待复核不等于已证明错误。无法据此推算新模型调用量或成本。</p>
    <p><a href="comparison.json">下载全部对照 JSON</a> · <a href="regression.json">离线回归记录</a></p>
    <table><tr><th>错误类别</th><th>旧版</th><th>新版</th></tr>{rows}</table></div>
    <div class="panel"><h2>最小反例 · 检验漏检和误杀</h2><p class="note">以下是受控程序探针，由已知比较关系标注真值，统一注入乐观评语来测试验证器。它们不是新生成的真实模型样本。</p><table><tr><th>原句</th><th>已知真值</th><th>旧验证器</th><th>新验证器</th></tr>{probes}</table></div>
    <div class="panel"><h2>重复调用 · 会话循环探针</h2><p>脚本化重复读取同一已提供片段，终止前回合数：<strong>{scheduling[0]['scripted_agent_turns']} → {scheduling[1]['scripted_agent_turns']}</strong>。两版均保持未完成，未把停止当作通过。正文不变时使用稳定核查身份；正文、来源、任务或规则变化使旧身份失效。</p><p class="note">这是实际调度循环搭配固定离线响应的验证，不是实际模型调用量或费用预测。</p></div>
    <div class="panel"><h2>离线回归</h2><table><tr><th>执行范围</th><th>测试数</th><th>失败</th><th>跳过</th></tr>{validation_rows}</table><p class="note">各轮有重叠，不相加。全仓探索性测试已停止，未完成，不计为全仓通过；其中发现的核查 Agent 角色映射遗漏已修复并补测。</p></div>
    <div class="panel"><h2>此次修复与边界</h2><p>阶段由程序选择适用条款；规则按 ID 引用，统一来源引用格式；工具参数错误只修参数。明确的百分比与“一半”比较由 Decimal 检验；跨句指代和片段不能支持的全文否定保留待复核。正文不变时复用核查身份，重复无进展有界停止。</p><p>通用语言蕴含、跨句指代和真正的生成质量仍需后续验证。本报告不把待复核包装为已通过，不把离线调用替身算成付费实验。</p></div>{''.join(cases)}</main></html>'''


def state(snapshot):
    return '存在已证缺陷' if snapshot['failed'] else '证据未决' if snapshot['blocked'] else '核查提交完整' if snapshot['passed'] else '核查未完成'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # An accidental future network addition must fail before any request.
    def deny_network(event, _):
        if event in {'socket.connect', 'socket.getaddrinfo'}:
            raise RuntimeError('offline_replay_network_forbidden')
    sys.addaudithook(deny_network)
    fixture = json.loads((ROOT/'tests/fixtures/reasoning_agent/environment_sessions.json').read_text(encoding='utf8'))
    report = replay(fixture)
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output/'regression.json').exists():
        report['validation'] = json.loads((args.output/'regression.json').read_text(encoding='utf8'))
    (args.output/'comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    (args.output/'index.html').write_text(document(report), encoding='utf8')
    print(json.dumps(report['totals'], ensure_ascii=False))


if __name__ == '__main__':
    main()
