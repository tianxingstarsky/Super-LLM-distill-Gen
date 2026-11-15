"""Local HTML evidence report; generation completion is not semantic correctness."""
from datetime import datetime, timezone
import hashlib
from html import escape
import json
from pathlib import Path


def _read(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _pre(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    return "<pre>" + escape(text) + "</pre>"


def _assessments(output, preflight):
    path = output / "semantic-assessment.json"
    if not path.exists():
        return {}, None
    assessment = _read(path)
    if assessment["experiment_identity"] != preflight.get("identity"):
        raise ValueError("training_report_assessment_identity_changed")
    records = {}
    for record in assessment["cases"]:
        source = output / record["artifact"]
        if source.parent.parent != output or source.parent.name not in {"baseline", "sft_eval", "cpt_sft_eval"}:
            raise ValueError("training_report_assessment_source_invalid")
        if hashlib.sha256(source.read_bytes()).hexdigest() != record["output_sha256"]:
            raise ValueError("training_report_assessed_output_changed")
        actual = _read(source)
        if actual["case_id"] != record["case_id"]:
            raise ValueError("training_report_assessment_case_mismatch")
        records[(source.parent.name, record["case_id"])] = record
    return records, assessment


def write_training_report(output, *, status, issue=None):
    output = Path(output)
    preflight = _read(output / "preflight.json", {})
    receipts = {name: _read(output / (name + ".json"), {}).get("result", {})
                for name in ("baseline", "sft_train", "sft_eval", "cpt_sft_train", "cpt_sft_eval")}
    arms = (("baseline", "原模型"), ("sft_eval", "SFT"), ("cpt_sft_eval", "CPT → SFT"))
    rows = {arm: receipt.get("cases", []) for arm, receipt in receipts.items()}
    assessments, assessment_document = _assessments(output, preflight)
    summary = []
    for arm, label in arms:
        cases = rows[arm]
        judged = [assessments[(arm, row["case_id"])] for row in cases if (arm, row["case_id"]) in assessments]
        values = (
            label, len(cases), sum(bool(r.get("answer")) for r in cases),
            sum(bool(r.get("thinking_closed")) for r in cases), sum(bool(r.get("truncated")) for r in cases),
            round(sum(r.get("seconds", 0) for r in cases), 2), sum(r.get("output_tokens", 0) for r in cases),
            f"{sum(j['semantic_score'] == 2 for j in judged)}/{len(judged)}" if judged else "未审阅",
            f"{sum(j.get('combined_usable') is True for j in judged)}/{len(judged)}" if judged else "未审阅",
            sum(j.get("combined_usable") is None for j in judged) if judged else "—",
            receipts[arm].get("validation_metrics", {}).get("eval_loss", "—") if receipts[arm].get("validation_metrics") else "—")
        summary.append("<tr>" + "".join("<td>" + escape(str(value)) + "</td>" for value in values) + "</tr>")
    training = []
    for arm in ("sft_train", "cpt_sft_train"):
        receipt = receipts[arm]
        training.append(f"<h3>{escape(arm)}</h3>" + _pre({key: receipt.get(key) for key in (
            "status", "actual_epochs", "global_steps", "example_count", "input_tokens", "supervised_tokens",
            "trainable_parameters", "seconds", "peak_allocated_bytes")}) +
            "<details><summary>完整 Trainer 指标与 loss 记录</summary>" + _pre({key: receipt.get(key)
                for key in ("metrics", "loss_history", "adapter_files")}) + "</details>")
    case_ids = list(dict.fromkeys(r["case_id"] for arm, _ in arms for r in rows[arm]))
    details = []
    for case_id in case_ids:
        columns = []
        question = ""
        for arm, label in arms:
            row = next((r for r in rows[arm] if r["case_id"] == case_id), None)
            if row is None:
                columns.append(f"<article><h3>{label}</h3><p>尚未执行</p></article>")
                continue
            question = next((m["content"] for m in reversed(row["messages"]) if m["role"] == "user"), "")
            judgment = assessments.get((arm, case_id))
            judgment_html = ("<details open><summary>逐题证据审阅</summary>" + _pre(judgment) + "</details>"
                if judgment else "<p class='note'>尚无语义审阅，不计为质量通过。</p>")
            columns.append(f"<article><h3>{label}</h3><small>"
                f"{row['output_tokens']} tokens · {row['seconds']:.2f}s · {escape(row['finish_reason'])}</small>"
                "<h4>正式回答</h4>" + _pre(row["answer"] or "【没有完整正式回答】") +
                "<details><summary>完整原生推理（CoT）</summary>" + _pre(row["reasoning"]) + "</details>" + judgment_html +
                "<details><summary>实际提示与完整原始输出</summary>" + _pre(row["prompt"]) + _pre(row["raw_output"]) +
                "</details><details><summary>参考要求（未提供给模型）</summary>" + _pre(row.get("rubric")) +
                _pre(row.get("expected_answer")) + "</details></article>")
        details.append(f"<section id='{escape(case_id)}'><h2>{escape(case_id)}</h2>" + _pre(question) +
                       "<div class='cases'>" + "".join(columns) + "</div></section>")
    report = {"schema_version": 1, "status": status, "issue": issue, "preflight": preflight,
              "semantic_status": "assistant_offline_audit" if assessment_document else "not_assessed",
              "updated_at": datetime.now(timezone.utc).isoformat(),
              "report": "report.html"}
    (output / "status.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    html = """<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Qwen3.5-4B · 论文训练验证</title><style>
*{box-sizing:border-box}body{margin:0;background:#edf3f8;color:#182b43;font:15px/1.7 'Segoe UI','Microsoft YaHei',sans-serif}
header{background:#132940;color:#fff;padding:44px max(32px,5vw);border-bottom:4px solid #63aef1}h1{font-size:36px;margin:8px 0}
header small{letter-spacing:3px;color:#93bfdc}main{max-width:1840px;padding:28px;margin:auto}section{background:#fff;border:1px solid #dbe5ee;border-radius:14px;padding:26px;margin-bottom:22px;box-shadow:0 8px 24px #243b5710}
nav a{margin-right:24px;color:#a9dbff}h2{font-size:23px;margin-top:0}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:12px;border-bottom:1px solid #e5ebf1}th{background:#f3f7fb}pre{font:14px/1.7 Consolas,'Microsoft YaHei',monospace;white-space:pre-wrap;overflow-wrap:anywhere;margin:10px 0}.cases{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}article{background:#f7fafd;border:1px solid #dee8f0;border-radius:12px;padding:19px;min-width:0}article h3{margin:0}article small{color:#687e93}summary{cursor:pointer;color:#1766a6;padding:10px 0}details{border-top:1px solid #dce7ef;margin-top:15px}.note{padding:14px 18px;background:#fff7e9;border-left:3px solid #d2a255}a{color:#1f71ad}</style>
<header><small>SHUJIAN CUBE / TRAINING VALIDATION</small><h1>同一份论文，三组真实对照。</h1>
<p>Qwen3.5-4B 原始后训练模型 · SFT · CPT → SFT · 三组统一 NF4 量化</p><nav><a href="#comparison">结果</a><a href="#preflight">模板与监督</a><a href="#training">训练记录</a><a href="#samples">完整问答</a></nav></header><main>"""
    html += f"<section><h2>状态：{escape(status)}</h2><p>{escape(issue or '')}</p><p class='note'>"
    html += "格式完成率不是事实正确率；训练 loss 不是开放生成质量。原资料及推理不改写，训练每个分支只遍历一次。"
    html += "CPT→SFT 按联合数据的有序一轮执行；参考答案不进入生成提示。本实验是同论文留出任务，不能证明未知领域泛化。</p></section>"
    html += "<section id='comparison'><h2>真实对照结果</h2><table><tr><th>组别</th><th>已评测</th><th>有正式答案</th><th>思考已闭合</th><th>达到上限</th><th>生成耗时 s</th><th>输出 tokens</th><th>答案满分</th><th>综合可用</th><th>待复核</th><th>验证 loss</th></tr>" + "".join(summary) + "</table>"
    html += "<p>答案满分仅指最终回答；综合可用还要求完整结束、未发现推理实质矛盾。未审阅与证据不足不算通过，也不冒充已确认错误。</p>"
    if assessment_document:
        html += "<p class='note'>语义分数来自对保存输出的助手离线审阅，不是经过校准的人类基准。量尺在生成前固定；每项判断保留原文证据。16 道论文机制题与 4 道额外 ML 评估题分别统计。</p>"
        html += _pre(assessment_document.get("summary")) + "<p><a href='semantic-assessment.json'>完整评分证据与来源摘要</a></p>"
    html += "</section>"
    html += "<section id='preflight'><h2>真实模板、角色与监督预检</h2>" + _pre({key: preflight.get(key) for key in (
        "train_records", "validation_records", "test_cases", "cpt_training_blocks", "cpt_tokens",
        "maximum_sft_tokens", "supervised_sft_tokens", "sft_turn_eos", "cpt_document_eos",
        "system_preserved", "full_reasoning_preserved", "silent_truncation")})
    audits = _read(output / "supervision-audit.json", [])
    added = sum(row.get("system_origin") == "experiment_added" for row in audits)
    html += f"<p>本批 {added} 个目标轮的来源未带 system，读取视图统一补充中性 system 并注明来源；原文件不改。教师解释完整保留，但不冒称教师原生思考。system／user 只作上下文，助手推理、答案与结束标记受监督。</p>"
    html += "<details><summary>完整预检与编码配置</summary>" + _pre(preflight) + "</details><p><a href='supervision-audit.json'>逐条完整渲染与监督 token 审计</a> · <a href='experiment.json'>固定实验清单</a></p></section>"
    html += "<section id='training'><h2>训练与资源记录</h2><p>CPT→SFT 是一次有序课程遍历；梯度累积跨越课程边界时可包含两类样本，不声称两个完全分开的优化阶段。</p>" + "".join(training) + "</section>"
    html += "<section><h2>完整样本索引</h2><p>点击每组中的「完整原生推理」展开全部 CoT；正式回答直接展示。</p>" + " · ".join(
        f"<a href='#{escape(case_id)}'>{escape(case_id)}</a>" for case_id in case_ids) + "</section>"
    html += "<div id='samples'>" + "".join(details) + "</div></main></html>"
    (output / "report.html").write_text(html, encoding="utf-8")
    return report
