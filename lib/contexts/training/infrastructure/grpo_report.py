"""Offline, receipt-backed GRPO comparison; numeric rewards are not a CoT audit."""
from html import escape
import json
import math
from pathlib import Path
import re

from ..domain.validation import complete_reasoned_generation


_ARMS = (("baseline", "训练前 · 原模型"), ("grpo_eval", "训练后 · GRPO 适配器"))
_FAMILIES = {
    "rational_expression": "精确分数运算", "trip_average_speed": "全程平均速度",
    "set_counting": "集合计数", "divisibility_counting": "整除计数",
    "inventory_balance": "存量变化", "gsm8k": "GSM8K 应用题",
}
_PENDING = "待记录"
_SECRET_KEY = re.compile(r"(?:api[_-]?key|authorization|password|secret|access[_-]?token|hf[_-]?token|token)$", re.I)
_WINDOWS_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)[^\r\n\"'<>]*")
_UNIX_PATH = re.compile(r"(?<![\w:/])/(?:[A-Za-z_][\w.-]*/)+[^\s\"'<>]+")


def _safe(value):
    """Keep portable evidence while excluding credentials and machine locations."""
    if isinstance(value, dict):
        return {_safe(str(key)): "[已隐藏凭据]" if _SECRET_KEY.fullmatch(str(key)) else _safe(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        value = _WINDOWS_PATH.sub("[本机路径已隐藏]", value)
        value = _UNIX_PATH.sub("[本机路径已隐藏]", value)
        # Relative model IDs and artifact names remain usable in the report.
        if value.startswith("/") and "\n" not in value:
            value = "[本机路径已隐藏]"
        value = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{16,}|hf_[A-Za-z0-9]{16,})\b", "[已隐藏凭据]", value)
    return value


def _text(value):
    return escape(str(_safe(value)), quote=True)


def _pre(value):
    value = _safe(value)
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return "<pre>" + escape(value, quote=True) + "</pre>"


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _format(value, unit="", precision=2):
    if not _number(value):
        return _PENDING
    number = f"{value:,}" if type(value) is int else f"{value:,.{precision}f}"
    return number + unit


def _gib(value):
    return _format(value / (1024 ** 3), " GiB") if _number(value) else _PENDING


def _case_id(row):
    return str(row.get("case_id", row.get("task_id", "")))


def _correct(row):
    if row is None:
        return None
    # A token-capped answer never earns a pass, including stale self-reported rewards.
    if row.get("truncated") is True:
        return False
    verification = row.get("verification") or {}
    decision = verification.get("answer_correct", verification.get("exact_final_answer_correct"))
    if type(decision) is not bool:
        return None
    if decision is False:
        return False
    if row.get("ended_with_eos") is False:
        return False
    if row.get("truncated") is not False or row.get("ended_with_eos") is not True:
        return None
    return bool(isinstance(row.get("answer"), str) and row["answer"].strip())


def _complete(row):
    if row is None:
        return None
    if any(type(row.get(key)) is not bool for key in ("truncated", "ended_with_eos", "thinking_closed")):
        return None
    if any(not isinstance(row.get(key), str) for key in ("answer", "reasoning")):
        return None
    return complete_reasoned_generation(row)


def _summary(rows, expected):
    correctness = [_correct(row) for row in rows]
    completeness = [_complete(row) for row in rows]
    judged = sum(value is not None for value in correctness)
    complete_judged = sum(value is not None for value in completeness)
    correct = sum(value is True for value in correctness)
    complete = sum(value is True for value in completeness)
    token_values = [row.get("output_tokens") for row in rows]
    second_values = [row.get("seconds") for row in rows]
    tokens = sum(token_values) if rows and all(_number(item) for item in token_values) else None
    seconds = sum(second_values) if rows and all(_number(item) for item in second_values) else None
    return {"expected_cases": expected, "saved_cases": len(rows), "verified_cases": judged,
            "correct": correct if judged else None, "accuracy": correct / judged if judged else None,
            "pending_verification": len(rows) - judged, "missing_cases": max(0, expected - len(rows)),
            "complete_cases": complete if complete_judged else None, "completeness_checked": complete_judged,
            "complete_accuracy": complete / complete_judged if complete_judged else None,
            "correct_and_complete": sum(a is True and b is True for a, b in zip(correctness, completeness))
                if judged and complete_judged else None,
            "truncated_cases": sum(row.get("truncated") is True for row in rows) if rows else None,
            "output_tokens": tokens, "generation_seconds": seconds,
            "tokens_per_second": tokens / seconds if tokens is not None and seconds and seconds > 0 else None}


def _rate(count, denominator):
    return _PENDING if count is None or not denominator else f"{count} / {denominator}（{count / denominator:.1%}）"


def _change(before, after):
    if before is None or after is None:
        return "pending"
    return "improved" if not before and after else "regressed" if before and not after else "stable_correct" if after else "stable_incorrect"


def _table(headers, rows):
    return ("<div class='table-wrap'><table><thead><tr>" + "".join("<th scope='col'>" + _text(item) + "</th>" for item in headers)
            + "</tr></thead><tbody>" + "".join("<tr>" + "".join("<td>" + _text(item) + "</td>" for item in row) + "</tr>" for row in rows)
            + "</tbody></table></div>")


def _badge(value, *, complete=False):
    label = ("结构完整" if complete else "数值答案正确") if value is True else ("结构不完整" if complete else "数值答案未通过") if value is False else "待判定"
    return f"<span class='badge {'pass' if value is True else 'fail' if value is False else 'pending'}'>{label}</span>"


def _question(task, before, after):
    if task.get("prompt"):
        return task["prompt"]
    for row in (before, after):
        if row:
            for message in reversed(row.get("messages") or []):
                if message.get("role") == "user":
                    return message.get("content", "")
            if row.get("prompt"):
                return row["prompt"]
    return "题目尚未记录"


def _case_column(row, label):
    start = "<article class='case-arm'><h3>" + label + "</h3>"
    if row is None:
        return start + "<p class='empty'>本阶段尚无这道题的生成收据。</p></article>"
    finish = "EOS" if row.get("ended_with_eos") is True else "未确认 EOS"
    if row.get("truncated") is True:
        finish += " · 达到输出上限"
    return (start + "<div class='badges'>" + _badge(_correct(row)) + _badge(_complete(row), complete=True) + "</div>"
            + "<p class='meta'>" + _text(_format(row.get("output_tokens"), " tokens")) + " · "
            + _text(_format(row.get("seconds"), " s")) + " · " + _text(finish) + "</p>"
            + "<h4>正式回答</h4><div class='answer'>" + _pre(row.get("answer") or "【未生成正式回答】") + "</div>"
            + "<details data-reasoning><summary>完整原生推理（CoT）</summary>"
            + _pre(row.get("reasoning") or "【未记录推理】") + "</details>"
            + "<details><summary>实际输入、原始输出与逐题验证收据</summary>" + _pre(row) + "</details></article>")


_STYLE = """
:root{color-scheme:light;--ink:#18314c;--muted:#506880;--blue:#175eae;--line:#d9e5ef;--paper:#fff;--ground:#edf4fa}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:24px}body{margin:0;background:var(--ground);color:var(--ink);font:15px/1.7 'Segoe UI','Microsoft YaHei',sans-serif}::selection{background:#bedfff;color:#102a46}
a{color:var(--blue);text-underline-offset:4px}a:hover{color:#0b417a}a:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid #3989d9;outline-offset:4px}button,summary{touch-action:manipulation}
.sidebar{position:fixed;inset:0 auto 0 0;width:200px;padding:28px 18px;background:#f8fbff;border-right:1px solid var(--line)}.brand{font-size:19px;font-weight:700;padding:0 12px 22px}.sidebar nav{display:grid;gap:8px}.sidebar a{padding:9px 12px;border-radius:8px;text-decoration:none}.sidebar a:hover{background:#e4f0ff}.sidebar .hint{position:absolute;bottom:24px;left:30px;font-size:12px;line-height:1.7;color:var(--muted)}
.workspace{margin-left:200px;min-width:880px}header{padding:34px 38px;background:#e1effe;border-bottom:1px solid #c5dcf2}h1{font-size:32px;line-height:1.3;margin:0 0 14px;letter-spacing:-.02em}header p{max-width:88ch;margin:8px 0;color:#355675}.identity{font-size:12px;overflow-wrap:anywhere;font-family:Consolas,monospace}main{padding:26px 30px;max-width:1820px;margin:auto}section{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:25px 28px;margin-bottom:22px}h2{font-size:22px;line-height:1.4;margin:0 0 16px}h3{font-size:17px;margin:22px 0 10px}h4{font-size:14px;margin:18px 0 8px}.lead{font-size:17px;color:#294d72;max-width:85ch}.note{background:#f0f6fc;padding:14px 18px;border-radius:8px;color:#395770;max-width:110ch}.meta,.muted{color:var(--muted);font-size:13px}.empty{padding:24px 0;color:var(--muted)}
.stages{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px;margin-top:22px;padding:18px 0;border-top:1px solid var(--line)}.stage strong{display:block}.stage span{font-size:13px;color:var(--muted)}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}th,td{padding:12px 13px;border-bottom:1px solid #e5edf4;text-align:left;vertical-align:top}th{background:#f0f6fc;font-weight:600;white-space:nowrap}tbody tr:hover{background:#f7fbff}td:first-child{font-weight:600}.comparison td{white-space:nowrap}.cases{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px;align-items:start}.case-arm{min-width:0}.case-arm+article{padding-left:24px;border-left:1px solid var(--line)}.case-arm h3{margin:0 0 12px}.answer{background:#f6faff;border-radius:8px;padding:2px 15px}pre{font:14px/1.8 Consolas,'Microsoft YaHei',monospace;white-space:pre-wrap;overflow-wrap:anywhere;tab-size:2;margin:12px 0}.question{max-width:110ch}.expected{display:flex;gap:12px;align-items:baseline;background:#edf6ff;padding:12px 16px;border-radius:8px;margin:18px 0 24px}.expected strong{font-family:Consolas,monospace;overflow-wrap:anywhere}
details{border-top:1px solid var(--line);margin-top:16px}summary{cursor:pointer;color:var(--blue);padding:12px 0;font-weight:500}details[open]>summary{padding-bottom:4px}.badges{display:flex;flex-wrap:wrap;gap:8px}.badge{font-size:12px;padding:3px 9px;border-radius:6px}.pass{color:#176047;background:#e4f4eb}.fail{color:#8d3a32;background:#fcece8}.pending{color:#665224;background:#fbf3df}.controls{display:flex;gap:14px;align-items:center;margin:16px 0}button{border:0;border-radius:7px;background:var(--blue);color:#fff;font:inherit;padding:9px 16px;cursor:pointer}button:hover{background:#104d91}.case-index{display:flex;gap:8px;flex-wrap:wrap}.case-index a{font:12px/1.5 Consolas,monospace;padding:4px 8px;background:#edf5fd;border-radius:5px}.case-title{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}.case-title h2{margin-bottom:0}.case-title span{font-size:13px;color:var(--muted)}footer{padding:0 30px 24px;color:var(--muted);font-size:12px}
@media print{.sidebar,.controls{display:none}.workspace{margin:0;min-width:0}body{background:#fff}header,main{padding:18px}section{break-inside:auto}.cases{gap:16px}details[open]{break-inside:auto}button{display:none}a{color:inherit}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
"""


def render_grpo_report(manifest, stages, output_path):
    """Write a self-contained desktop report from already authenticated receipts.

    ``output_path`` accepts an experiment directory or the desired HTML filename.
    No model, network request, stage execution or input artifact is changed here.
    """
    tasks = manifest.get("tasks") or {}
    evaluation_tasks = tasks.get("evaluation") or []
    task_index = {str(task["task_id"]): task for task in evaluation_tasks}
    stage_rows = {arm: (stages.get(arm) or {}).get("cases") or [] for arm, _ in _ARMS}
    indexes = {arm: {_case_id(row): row for row in rows} for arm, rows in stage_rows.items()}
    case_ids = list(dict.fromkeys([*task_index, *indexes["baseline"], *indexes["grpo_eval"]]))
    expected_count = len(evaluation_tasks) or len(case_ids)
    summaries = {arm: _summary(rows, expected_count) for arm, rows in stage_rows.items()}
    paired = {name: 0 for name in ("improved", "regressed", "stable_correct", "stable_incorrect", "pending")}
    for case_id in case_ids:
        paired[_change(_correct(indexes["baseline"].get(case_id)), _correct(indexes["grpo_eval"].get(case_id)))] += 1
    families = {}
    for case_id in case_ids:
        task = task_index.get(case_id, {})
        row = indexes["baseline"].get(case_id) or indexes["grpo_eval"].get(case_id) or {}
        family = str(task.get("family") or row.get("group") or "未标注")
        families.setdefault(family, []).append(case_id)
    family_summaries = {family: {arm: _summary([indexes[arm][case_id] for case_id in ids if case_id in indexes[arm]], len(ids))
                                for arm, _ in _ARMS} for family, ids in families.items()}
    complete = all((stages.get(stage) or {}).get("status") == "completed" for stage in ("baseline", "grpo_train", "grpo_eval"))
    report = {"schema_version": 1, "report": "report.html", "status": "completed" if complete else "incomplete",
              "identity": manifest.get("identity"), "evaluation": summaries,
              "families": family_summaries, "paired_changes": paired,
              "reasoning_semantics": "not_independently_verified"}
    configuration = manifest.get("configuration") or {}
    model = manifest.get("model") or {}
    configured_model = configuration.get("model")
    model_basename = re.split(r"[\\/]", str(configured_model).rstrip("\\/"))[-1] if configured_model else None
    model_name = model.get("name") or model.get("model_id") or model_basename or model.get("model_name") or model.get("model_type") or "本地模型"
    task_source = manifest.get("task_source") or {}
    source_html = ""
    if task_source:
        source_html = ("<h3>题目来源与固定抽样</h3>" + _table(("公开数据集", "固定版本", "许可证"), [(
            task_source.get("id") or task_source.get("dataset_id") or task_source.get("dataset") or _PENDING,
            task_source.get("revision") or _PENDING, task_source.get("license") or _PENDING)])
            + "<p class='meta'>抽样方式</p>" + _pre(task_source.get("sampling") or _PENDING))
    if "gsm8k" in families:
        source_html += ("<p class='note'>GSM8K 是公开基准，原模型可能已在预训练或后训练中见过其中题目。"
                        "本次使用固定的官方训练／测试划分；留出题目不保证对原模型全新，不能把这份小样本结果当作完整 GSM8K 成绩。</p>")
    capacity_parent = manifest.get("capacity_parent") or {}
    if capacity_parent:
        source_html += ("<p class='note'>本容量分支从父版已保存的 step " + _text(_format(capacity_parent.get("global_step")))
                        + " / epoch " + _text(_format(capacity_parent.get("epoch"), precision=3))
                        + " 继续训练。父版与分支共用累计 " + _text(_format(configuration.get("epochs")))
                        + " epoch 预算，父版已完成的进度计入其中。输出容量由 "
                        + _text(_format(capacity_parent.get("previous_max_completion_length"), " tokens")) + " 调整为 "
                        + _text(_format(configuration.get("max_completion_length"), " tokens"))
                        + "，原冻结父版保持原样。评测仅复用已以 EOS 结束且未达到上限的输出；达到上限的输出按新容量重新生成，"
                        "复用不依据答案质量筛选。完整原始请求元数据保留。</p>")
    stage_labels = (("baseline", "原模型评测"), ("grpo_train", "GRPO 训练"), ("grpo_eval", "适配器评测"))
    stage_html = "".join("<div class='stage'><strong>" + label + "</strong><span>" +
                         _text((stages.get(key) or {}).get("status") or ("已有收据" if key in stages else "待执行")) +
                         "</span></div>" for key, label in stage_labels)
    html = ("<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<meta name='referrer' content='no-referrer'><title>GRPO · 本地训练对照报告</title><style>" + _STYLE + "</style></head><body>"
            "<aside class='sidebar'><div class='brand'>数简立方</div><nav aria-label='报告导航'>"
            "<a href='#overview'>实验概览</a><a href='#comparison'>训练前后</a><a href='#families'>题型与变化</a>"
            "<a href='#resources'>训练与资源</a><a href='#samples'>完整问答</a><a href='#receipts'>原始收据</a></nav>"
            "<p class='hint'>本地模型验证<br>静态报告 · 离线可读</p></aside><div class='workspace'><header>"
            "<h1>GRPO 本地训练对照</h1><p>" + _text(model_name) + " · 同一组留出题目，比较训练前后的完整生成。</p>"
            "<p class='identity'>实验身份：" + _text(manifest.get("identity") or _PENDING) + "</p></header><main>"
            "<section id='overview'><h2>这次实验回答什么</h2><p class='lead'>在可精确验证的数值任务上，检查一次小规模 GRPO 训练是否改变留出题目的答案表现。</p>"
            "<p class='note'>这是小规模本地试验，不能代表通用推理能力或正式能力基准。奖励仅验证最终数值答案；答案正确不能证明推理过程正确。"
            "本报告读取本地生成与训练收据，不调用付费 API。</p>" + _table(("训练题目", "留出评测题目", "随机种子", "训练上限"),
            [(_format(len(tasks["train"])) if "train" in tasks else _PENDING,
              _format(len(evaluation_tasks)) if "evaluation" in tasks else _PENDING,
              _format(configuration.get("seed")), _format(configuration.get("epochs"), " epoch"))])
            + source_html + "<div class='stages'>" + stage_html + "</div></section>")
    comparison_rows = []
    for arm, label in _ARMS:
        summary = summaries[arm]
        comparison_rows.append((label, f"{summary['saved_cases']} / {expected_count}" if arm in stages else "待执行",
            _rate(summary["correct"], summary["verified_cases"]),
            _rate(summary["complete_cases"], summary["completeness_checked"]),
            _format(summary["correct_and_complete"]), _format(summary["missing_cases"] + summary["pending_verification"])))
    html += ("<section id='comparison' class='comparison'><h2>训练前后的留出结果</h2>" + _table(
        ("组别", "已保存 / 计划题数", "固定输出容量内正确交付率", "推理与答案完整结束", "答案正确且结构完整", "待生成或判定"), comparison_rows)
        + "<p class='meta'>每题固定输出容量：" + _text(_format(configuration.get("max_completion_length"), " tokens"))
        + "。正确交付率按已保存且已判定的题目计算，未执行与未知不会补成 0。阶段未完成时，仅为已保存题目的预览。</p>"
        "<p>正确交付要求有非空正式答案、通过 TRL accuracy_reward／Math-Verify 数学等价判定、未截断且以 EOS 结束；"
        "这是固定输出容量下的交付指标，不是无限输出预算下的准确率或完整 GSM8K 基准成绩。结构完整另外要求非空推理、思考闭合与非空正式回答。"
        "结构完整只描述生成边界，推理语义未独立审核。</p></section>")
    family_rows = []
    for family, values in family_summaries.items():
        family_rows.append((_FAMILIES.get(family, family), len(families[family]), *[
            _rate(values[arm]["correct"], values[arm]["verified_cases"]) for arm, _ in _ARMS]))
    html += "<section id='families'><h2>题型与逐题变化</h2>"
    html += _table(("题型", "题数", "训练前数值正确", "训练后数值正确"), family_rows) if family_rows else "<p class='empty'>尚未记录评测题目。</p>"
    html += _table(("由错变对", "由对变错", "两次均正确", "两次均未通过", "尚不能配对判定"), [tuple(paired.values())])
    html += "<p class='meta'>逐题变化只比较同一道留出题；缺失或未知的一侧保留为待判定。样本量较小，不据此推断统计显著性。</p></section>"
    training = stages.get("grpo_train") or {}
    html += "<section id='resources'><h2>训练与资源记录</h2>" + _table(
        ("实际 epoch", "优化步数", "可训练参数", "训练生成次数", "训练生成 tokens", "平均奖励", "零奖励方差组"),
        [tuple(_format(training.get(key)) for key in ("actual_epochs", "global_steps", "trainable_parameters", "rollout_count", "rollout_output_tokens", "reward_mean", "zero_variance_groups"))])
    if capacity_parent or "new_optimizer_steps" in training:
        html += _table(("恢复起点 step", "本分支新增优化步数", "累计优化步数", "累计实际 epoch"), [tuple(
            _format(training.get(key)) for key in ("resumed_from_step", "new_optimizer_steps", "global_steps", "actual_epochs"))])
        html += "<p class='meta'>优化步数与实际 epoch 按同一条检查点训练序列累计；本分支新增步数单独列示。</p>"
    adapter_delta = training.get("adapter_delta_l2")
    html += _table(("有奖励差异的组", "全部 rollout 组", "适配器参数更新 L2"), [(
        _format(training.get("informative_groups")), _format(training.get("total_groups")),
        f"{adapter_delta:.6g}" if _number(adapter_delta) else _PENDING)])
    html += ("<p class='meta'>组内奖励存在差异时才有相对奖励信号；适配器更新 L2 衡量本次记录前后的参数变化，"
             "大于 0 只说明参数发生改变，效果仍由留出生成结果判断。</p>")
    resources = []
    for stage, label in stage_labels:
        receipt = stages.get(stage) or {}
        summary = summaries.get(stage, {})
        resources.append((label, _format(receipt.get("seconds"), " s"), _format(receipt.get("model_load_seconds"), " s"),
            _gib(receipt.get("peak_allocated_bytes") if stage == "grpo_train" else receipt.get("cuda_peak_allocated_bytes")),
            _gib(receipt.get("peak_reserved_bytes") if stage == "grpo_train" else receipt.get("cuda_peak_reserved_bytes")),
            _format(training.get("rollout_output_tokens")) if stage == "grpo_train" else _format(summary.get("output_tokens")),
            "见训练收据" if stage == "grpo_train" else _format(summary.get("generation_seconds"), " s"),
            _PENDING if stage == "grpo_train" else _format(summary.get("tokens_per_second"), " tokens/s")))
    html += _table(("阶段", "本次阶段执行耗时", "模型加载", "峰值分配显存", "峰值保留显存", "生成 tokens", "逐题生成耗时合计", "生成速率"), resources)
    html += ("<p class='meta'>1 GiB = 1,073,741,824 bytes。本次阶段执行耗时、模型加载和逐题生成耗时分别按收据原字段展示，不相加推算。"
             "断点前的执行时间不包含在本次进程计时中；逐题耗时保留已有完整输出，额外中断与诊断开销另记。"
             "训练生成 tokens 属于在线 rollout；评测生成 tokens 属于留出题生成，两者分开统计。训练总耗时不等于纯生成耗时，未据此推算训练生成速率。"
             "缺失指标标为待记录；未合并适配器的速率不代表部署性能。</p><details><summary>完整训练配置、Trainer 指标与 loss 记录</summary>"
             + _pre(training if training else "GRPO 训练收据尚未生成") + "</details></section>"
             "<section id='samples'><h2>每一道题，完整对照</h2><p>题目与参考数值独立展示，训练前后保留完整正式回答、原生推理及实际输入。"
             "评测参考答案是验证元数据，不作为生成提示输入。</p><div class='controls'><button type='button' id='toggle-reasoning' aria-expanded='false'>展开全部原生推理</button>"
             "<span class='meta'>也可以逐题展开</span></div><div class='case-index'>")
    html += "".join(f"<a href='#case-{index}'>{_text(case_id)}</a>" for index, case_id in enumerate(case_ids, 1)) + "</div></section>"
    change_names = {"improved": "由错变对", "regressed": "由对变错", "stable_correct": "两次均正确", "stable_incorrect": "两次均未通过", "pending": "待配对判定"}
    for index, case_id in enumerate(case_ids, 1):
        task = task_index.get(case_id, {})
        before, after = indexes["baseline"].get(case_id), indexes["grpo_eval"].get(case_id)
        family = task.get("family") or (before or after or {}).get("group") or "未标注"
        expected = task.get("expected_answer", (before or after or {}).get("expected_answer", _PENDING))
        html += (f"<section id='case-{index}'><div class='case-title'><h2>" + _text(_FAMILIES.get(family, family))
                 + "</h2><span>" + _text(case_id) + " · " + change_names[_change(_correct(before), _correct(after))] + "</span></div>"
                 "<div class='question'>" + _pre(_question(task, before, after)) + "</div><div class='expected'><span>参考数值</span><strong>"
                 + _text(expected) + "</strong><span class='meta'>仅用于精确校验</span></div><div class='cases'>"
                 + _case_column(before, _ARMS[0][1]) + _case_column(after, _ARMS[1][1]) + "</div></section>")
    html += ("<section id='receipts'><h2>原始收据与固定清单</h2><p class='meta'>以下为本次报告的输入证据；本机绝对路径与凭据字段已隐藏，原始文件保持原样。</p>"
             "<details><summary>固定实验清单、题目划分与配置</summary>" + _pre(manifest) + "</details>")
    for stage, label in stage_labels:
        html += "<details><summary>" + label + " · 完整阶段收据</summary>" + _pre(stages.get(stage) or "尚无阶段收据") + "</details>"
    html += ("</section></main><footer>本地 GRPO 小规模验证 · 推理语义未独立审核 · 本报告没有联网依赖</footer></div>"
             "<script>const thoughts=[...document.querySelectorAll('details[data-reasoning]')];"
             "const toggle=document.getElementById('toggle-reasoning');function reveal(open){thoughts.forEach(item=>item.open=open);"
             "toggle.textContent=open?'收起全部原生推理':'展开全部原生推理';toggle.setAttribute('aria-expanded',String(open));}"
             "reveal(new URLSearchParams(location.search).get('reasoning')==='open');"
             "toggle.addEventListener('click',()=>reveal(!thoughts.every(item=>item.open)));</script></body></html>")
    output = Path(output_path)
    if output.suffix.lower() not in (".html", ".htm"):
        output = output / "report.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    report["report"] = output.name
    return report
