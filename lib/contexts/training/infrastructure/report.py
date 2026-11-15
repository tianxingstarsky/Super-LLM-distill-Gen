"""Local HTML evidence report; generation completion is not semantic correctness."""
from datetime import datetime, timezone
import hashlib
from html import escape
import json
from pathlib import Path
from ..domain.validation import complete_reasoned_generation, assessment_usability


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
        score, contradiction = record.get("semantic_score"), record.get("reasoning_material_contradiction")
        usable = assessment_usability(actual, score, contradiction)
        if record.get("combined_usable") is not usable:
            raise ValueError("training_report_assessment_decision_inconsistent")
        key = (source.parent.name, record["case_id"])
        if key in records or record.get("arm", key[0]) != key[0]:
            raise ValueError("training_report_assessment_duplicate_or_wrong_arm")
        records[key] = record
    return records, assessment


def write_training_report(output, *, status, issue=None):
    output = Path(output)
    preflight = _read(output / "preflight.json", {})
    receipts = {name: _read(output / (name + ".json"), {}).get("result", {})
                for name in ("baseline", "sft_train", "sft_eval", "cpt_sft_train", "cpt_sft_eval")}
    arms = (("baseline", "原模型"), ("sft_eval", "SFT"), ("cpt_sft_eval", "CPT → SFT"))
    rows = {arm: receipts[arm].get("cases") or [_read(file) for file in sorted((output / arm).glob("case-*.json"))]
            for arm, _ in arms}
    assessments, assessment_document = _assessments(output, preflight)
    summary = []
    for arm, label in arms:
        cases = rows[arm]
        judged = [assessments[(arm, row["case_id"])] for row in cases if (arm, row["case_id"]) in assessments]
        seconds = sum(row.get("seconds", 0) for row in cases)
        tokens = sum(row.get("output_tokens", 0) for row in cases)
        loss = receipts[arm].get("validation_metrics", {}).get("eval_loss") if receipts[arm].get("validation_metrics") else None
        values = (
            label, len(cases), sum(complete_reasoned_generation(row) for row in cases),
            f"{sum(j['semantic_score'] for j in judged)}/{2 * len(judged)}" if judged else "未审阅",
            f"{sum(j['semantic_score'] == 2 for j in judged)}/{len(judged)}" if judged else "未审阅",
            f"{sum(j.get('combined_usable') is True for j in judged)}/{len(judged)}" if judged else "未审阅",
            sum(j.get("combined_usable") is None for j in judged) if judged else "—",
            round(loss, 4) if loss is not None else "—", round(seconds, 2), tokens,
            round(tokens / seconds, 2) if seconds else "—")
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
            if judgment:
                decision = "本次标准可用" if judgment["combined_usable"] is True else "待复核" if judgment["combined_usable"] is None else "未满足全部条件"
                judgment_html = ("<details open><summary>逐题证据审阅</summary><p><strong>" +
                    f"{judgment['semantic_score']}/2 · {decision}" + "</strong></p><p>" +
                    escape(judgment.get("comment", "")) + "</p>" +
                    "".join(_pre(quote) for quote in judgment.get("evidence", [])) +
                    "<details><summary>身份摘要与完整审阅记录</summary>" + _pre(judgment) + "</details></details>")
            else:
                judgment_html = "<p class='note'>尚无语义审阅，不计为质量通过。</p>"
            columns.append(f"<article><h3>{label}</h3><small>"
                f"{row['output_tokens']} tokens · {row['seconds']:.2f}s · {escape(row['finish_reason'])}</small>"
                "<h4>正式回答</h4>" + _pre(row["answer"] or "【没有完整正式回答】") +
                "<details data-reasoning><summary>完整原生推理（CoT）</summary>" + _pre(row["reasoning"]) + "</details>" + judgment_html +
                "<details><summary>实际输入、采样配置与完整原始输出</summary>" + _pre(row["messages"]) +
                _pre({"generation_config": row["generation_config"], "sampling_support": row.get("sampling_support")}) +
                _pre(row["prompt"]) + _pre(row["raw_output"]) +
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
nav a{margin-right:24px;color:#a9dbff}h2{font-size:23px;margin-top:0}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:12px;border-bottom:1px solid #e5ebf1}th{background:#f3f7fb}pre{font:14px/1.7 Consolas,'Microsoft YaHei',monospace;white-space:pre-wrap;overflow-wrap:anywhere;margin:10px 0}.cases{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;align-items:start}article{background:#f7fafd;border:1px solid #dee8f0;border-radius:12px;padding:19px;min-width:0}article h3{margin:0}article small{color:#687e93}summary{cursor:pointer;color:#1766a6;padding:10px 0}details{border-top:1px solid #dce7ef;margin-top:15px}.note{padding:14px 18px;background:#fff7e9;border-left:3px solid #d2a255}a{color:#1f71ad}button{background:#1766a6;color:white;border:0;border-radius:7px;padding:10px 17px;cursor:pointer;margin:0 0 15px}button:hover{background:#0c518a}button:focus-visible{outline:3px solid #80c4ff;outline-offset:3px}</style>
<header><small>SHUJIAN CUBE / TRAINING VALIDATION</small><h1>同一份论文，三组真实对照。</h1>
<p>Qwen3.5-4B 原始后训练模型 · SFT · CPT → SFT · 三组统一 NF4 量化</p><nav><a href="#comparison">结果</a><a href="#preflight">模板与监督</a><a href="#training">训练记录</a><a href="#samples">完整问答</a></nav></header><main>"""
    html += f"<section><h2>状态：{escape(status)}</h2><p>{escape(issue or '')}</p><p class='note'>"
    html += "格式完成率不是事实正确率；训练 loss 不是开放生成质量。原资料及推理不改写，训练每个分支只遍历一次。"
    html += "CPT→SFT 按联合数据的有序一轮执行；参考答案不进入生成提示。本实验是同论文留出任务，不能证明未知领域泛化。</p></section>"
    html += "<section id='comparison'><h2>真实对照结果</h2><table><tr><th>组别</th><th>已保存</th><th>完整输出</th><th>得分</th><th>答案满分</th><th>综合可用</th><th>待复核</th><th>验证 loss</th><th>生成耗时 s</th><th>输出 tokens</th><th>tokens/s</th></tr>" + "".join(summary) + "</table>"
    html += "<p>答案满分仅指最终回答；综合可用还要求完整结束、未发现推理实质矛盾。未审阅与证据不足不算通过，也不冒充已确认错误。</p>"
    html += "<p>运行中显示已原子保存的完整题目作为阶段预览；整组完成以最终阶段收据为准。</p>"
    all_cases = [row for arm, _ in arms for row in rows[arm]]
    html += f"<p>结构检查：{len(all_cases)} 份已保存输出，{sum(bool(r.get('answer')) for r in all_cases)} 份正式回答，{sum(bool(r.get('thinking_closed')) for r in all_cases)} 份闭合思考，{sum(bool(r.get('ended_with_eos')) for r in all_cases)} 份 EOS 终止，{sum(bool(r.get('truncated')) for r in all_cases)} 份达到上限。未合并 PEFT 适配器的生成速率不等于部署优化后的速度。</p>"
    if assessment_document:
        html += "<p class='note'>语义分数来自对保存输出的助手离线审阅，不是经过校准的人类基准。量尺在生成前固定；每项判断保留原文证据。16 道论文机制题与 4 道额外 ML 评估题分别统计。</p>"
        groups = []
        for arm, label in arms:
            for scope in assessment_document.get("summary", {}).get(arm, {}).get("scope_groups", []):
                scope_name = {"paper_mechanism": "论文机制", "general_ml_evaluation_extension": "ML 评估扩展"}.get(scope["scope"], scope["scope"])
                groups.append("<tr>" + "".join("<td>" + escape(str(value)) + "</td>" for value in (
                    label, scope_name, scope["assessed"], f"{scope['semantic_score_sum']}/{scope['max_points_for_assessed']}",
                    scope["semantic_full_credit_count"], scope["combined_usable_true"])) + "</tr>")
        if groups:
            html += "<h3>分组审阅</h3><table><tr><th>组别</th><th>范围</th><th>已审阅</th><th>得分</th><th>答案满分数</th><th>综合可用数</th></tr>" + "".join(groups) + "</table>"
        html += "<details><summary>完整审阅统计</summary>" + _pre(assessment_document.get("summary")) + "</details><p><a href='semantic-assessment.json'>完整评分证据与来源摘要</a> · <a href='scoring-notes.json'>生成前冻结的评分解释</a></p>"
    html += "</section>"
    calibration = _read(output / "precision-reference.json")
    if calibration:
        precision_rows = []
        for name, condition in calibration.get("conditions", {}).items():
            precision_rows.append("<tr><td>" + escape(name) + "</td><td>" +
                str(round(condition["validation_metrics"]["eval_loss"], 6)) + "</td><td>" +
                escape(str(condition.get("loaded_precision"))) + "</td></tr>")
        html += "<section><h2>只读加载精度校准</h2><p>相同 16 条验证记录，0 次训练更新，0 条新增测试生成。这里比较监督 loss，不把它当作三个精度模式的生成正确率。</p><table><tr><th>加载条件</th><th>验证 loss</th><th>GDN 参数实际精度</th></tr>" + "".join(precision_rows) + "</table><p>BF16 checkpoint FP32 条件从原始 safetensors 恢复 24 个 A_log 与 24 个 GDN norm 参数的原值和 FP32 精度。单凭这些 loss 差异不能为每道错题确定原因。</p><a href='precision-reference.json'>完整校准记录</a></section>"
    html += "<section id='preflight'><h2>真实模板、角色与监督预检</h2>" + _pre({key: preflight.get(key) for key in (
        "train_records", "validation_records", "test_cases", "cpt_training_blocks", "cpt_tokens",
        "maximum_sft_tokens", "supervised_sft_tokens", "sft_turn_eos", "cpt_document_eos",
        "system_preserved", "full_reasoning_preserved", "silent_truncation")})
    audits = _read(output / "supervision-audit.json", [])
    added = sum(row.get("system_origin") == "experiment_added" for row in audits)
    html += f"<p>本批 {added} 个目标轮的来源未带 system，读取视图统一补充中性 system 并注明来源；原文件不改。教师解释完整保留，但不冒称教师原生思考。system／user 只作上下文，助手推理、答案与结束标记受监督。</p>"
    html += "<details><summary>完整预检与编码配置</summary>" + _pre(preflight) + "</details><p><a href='supervision-audit.json'>逐条完整渲染与监督 token 审计</a> · <a href='experiment.json'>固定实验清单</a></p></section>"
    html += "<section id='training'><h2>训练与资源记录</h2><p>CPT→SFT 是一次有序课程遍历；梯度累积跨越课程边界时可包含两类样本，不声称两个完全分开的优化阶段。</p>" + "".join(training) + "</section>"
    html += "<section><h2>完整样本索引</h2><p>点击每组中的「完整原生推理」查看全部 CoT；正式回答直接展示。</p><button type='button' id='toggle-reasoning'>展开全部原生推理</button><p>" + " · ".join(
        f"<a href='#{escape(case_id)}'>{escape(case_id)}</a>" for case_id in case_ids) + "</p></section>"
    html += "<div id='samples'>" + "".join(details) + "</div></main><script>const thoughts=[...document.querySelectorAll('details[data-reasoning]')];const toggle=document.getElementById('toggle-reasoning');function reveal(open){thoughts.forEach(item=>item.open=open);toggle.textContent=open?'收起全部原生推理':'展开全部原生推理';toggle.setAttribute('aria-expanded',String(open));}reveal(new URLSearchParams(location.search).get('reasoning')==='open');toggle.addEventListener('click',()=>reveal(!thoughts.every(item=>item.open)));</script></html>"
    (output / "report.html").write_text(html, encoding="utf-8")
    return report
