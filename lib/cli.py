"""df-* 命令行（M1 最小闭环，纯 thin CLI，无界面代码）。

用法（在项目根，.venv 已激活）：
  python -m lib.cli import     [--limit N] [--export-limit M] [--cot separated]
  python -m lib.cli stats
  python -m lib.cli preview    [--n 10] [--file data/output/rollout_samples.jsonl]
  python -m lib.cli export     [--format llamafactory|chat] [--input ...] [--out ...]
  python -m lib.cli gate       [propose G0|G1|G3 | approve G0 | reject G0 | status]

闸门接入：import→G1；export --bulk→G3；df-run/df-distill→G0（M1 后续接入）。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 本地端点绕代理（spike 报告 F2）；云端 API 不受影响
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

from lib.gates import GateBlocked, GateKeeper  # noqa: E402

GATES_YAML = ROOT / "configs" / "gates.yaml"
GATES_STATE = ROOT / "data" / "output" / "gates_state.json"
OUT_DIR = ROOT / "data" / "output"
ROLLOUT_DIR = pathlib.Path(r"C:\Users\tianx\.zcode\cli\rollout")


def _gates() -> GateKeeper:
    return GateKeeper(GATES_YAML, GATES_STATE)


def cmd_import(args) -> int:
    gate = _gates()
    if gate.status("G1") != "approved":
        gate.propose("G1", {"rollout_dir": str(ROLLOUT_DIR)})
        raise GateBlocked("数据源闸 G1 待确认：df gate approve G1（确认导入私有会话数据与云端上传）")
    sys.path.insert(0, str(ROOT / "scripts"))
    from import_rollout import run  # type: ignore

    run(limit=args.limit, export_limit=args.export_limit, cot=args.cot)
    from lib.monitor import trace_run

    stats = json.loads((OUT_DIR / "rollout_stats.json").read_text(encoding="utf-8"))
    trace_run(ROOT, "import", {"cot": args.cot, "stats": stats})
    return 0


def cmd_stats(args) -> int:
    stats = json.loads((OUT_DIR / "rollout_stats.json").read_text(encoding="utf-8"))
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


def cmd_preview(args) -> int:
    path = pathlib.Path(args.file)
    lines = path.read_text(encoding="utf-8").splitlines()
    samples = [json.loads(l) for l in lines if l.strip()]

    if args.html:
        report = None
        report_path = OUT_DIR / "distill_report.json"
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        from lib.render import render_preview_html

        out = render_preview_html(samples, report, OUT_DIR / "preview.html", max_samples=args.n)
        print(f"HTML 预览已生成（{min(args.n, len(samples))} 条）: {out}")
        return 0

    for sample in samples[: args.n]:
        last = sample["messages"][-1]
        content = str(last.get("content", ""))[:200]
        reasoning = str(last.get("reasoning_content", ""))[:120]
        print(f"[{sample['id'][:20]}] model={sample['model']} fin={sample['finish_reason']} "
              f"msgs={len(sample['messages'])} toolErr={sample['error_tool_steps']}")
        if reasoning:
            print(f"    思考: {reasoning}…" if len(reasoning) == 120 else f"    思考: {reasoning}")
        print(f"    正文: {content}")
        print()
    print(f"（共 {len(samples)} 条，展示前 {min(args.n, len(samples))} 条）")
    return 0


def cmd_export(args) -> int:
    from lib.exporters import export_samples

    if args.bulk:
        _gates().require("G3")  # 放量前须过小批量预览闸
    samples = (json.loads(l) for l in pathlib.Path(args.input).read_text(encoding="utf-8").splitlines() if l.strip())
    counts = export_samples(samples, args.format, args.out)
    from lib.monitor import trace_run

    trace_run(ROOT, "export", {"format": args.format, "counts": counts, "bulk": args.bulk})
    print(f"导出完成: {counts}（{args.format} → {args.out}）")
    return 0


def cmd_distill(args) -> int:
    """蒸馏质检：分类（免费）→ DPO 负样本提取（免费）→ 可选 LLM 打分（需 G0 闸门）。"""
    import lib.adapters.distill as distill_mod
    from lib.adapters.distill_prompts import SUMMARIZER_TEXT_PROMPT

    samples = [
        json.loads(l)
        for l in pathlib.Path(OUT_DIR / "rollout_samples.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    report = distill_mod.classify_report(samples)
    report["n_samples"] = len(samples)

    pairs = []
    for f in sorted(ROLLOUT_DIR.glob("model-io-sess_*.jsonl")):
        pairs.extend(distill_mod.extract_dpo_pairs(str(f), "separated"))
    report["n_dpo_pairs"] = len(pairs)

    llm_scores = []
    if args.llm_check > 0:
        _gates().require("G0")  # 调用云端 API 前必须过预算/模型闸
        from lib.llm_client import load_backend

        client, model = load_backend(ROOT, judge=True)  # judge 角色：更稳的模型（默认 v4-pro）
        candidates = sorted(
            samples,
            key=lambda s: (distill_mod.classify_sample(s)["tag"] != "recovery", -s["error_tool_steps"]),
        )[: args.llm_check]
        print(f"LLM 打分 {len(candidates)} 条（模型 {model}）…")
        for s in candidates:
            last = s["messages"][-1]
            goal = next((m["content"] for m in reversed(s["messages"]) if m["role"] == "user"), "")
            try:
                out = client.chat(
                    [{"role": "user", "content": SUMMARIZER_TEXT_PROMPT.format(
                        goal=goal[:800],
                        thinking=str(last.get("reasoning_content", ""))[:1500],
                        final_answer=str(last.get("content", ""))[:1500],
                    )}],
                    max_tokens=None, temperature=0.2,  # 思考允许无限长度（用户确认）
                )
                llm_scores.append({"id": s["id"], "score": out})
            except Exception as e:  # noqa: BLE001
                llm_scores.append({"id": s["id"], "error": str(e)[:200]})
        report["llm_scores"] = llm_scores
        report["llm_usage"] = client.usage

    # 产物落盘
    (OUT_DIR / "distill_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    if pairs:
        (OUT_DIR / "dpo_pairs.jsonl").write_text(
            "\n".join(json.dumps(p, ensure_ascii=False) for p in pairs) + "\n", encoding="utf-8"
        )
    from lib.monitor import trace_run

    trace_run(ROOT, "distill", {"report": report})
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"报告/DPO 对 → {OUT_DIR}")
    return 0


def cmd_review(args) -> int:
    """Argilla 人工审核：push（推送+评分建议）/ pull（拉回标注+按通过率放行 G3）/ summary。"""
    import lib.review as review_mod

    gate = _gates()
    if args.action == "push":
        samples = [
            json.loads(l)
            for l in pathlib.Path(OUT_DIR / "rollout_samples.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()
        ][: args.n]
        report_path = OUT_DIR / "distill_report.json"
        scores = {}
        if report_path.exists():
            scores = {s.get("id"): s.get("score", "") for s in json.loads(report_path.read_text(encoding="utf-8")).get("llm_scores", [])}
        try:
            n = review_mod.push_samples(samples, scores)
            print(f"已推送 {n} 条到 Argilla（http://localhost:6900，admin/distill123456）")
            print("标注完 keep/reject 后运行: df review pull")
        except Exception as e:  # noqa: BLE001
            print(f"[Argilla 不可用] {str(e)[:200]}")
            print("启动服务: docker compose -f docker/argilla.yml up -d")
            return 4
        return 0

    if args.action == "pull":
        try:
            decisions = review_mod.pull_decisions()
        except Exception as e:  # noqa: BLE001
            print(f"[Argilla 不可用] {str(e)[:200]}")
            return 4
        review_mod.write_review_log(decisions, OUT_DIR / "review.jsonl")
        result = review_mod.decide_gate(decisions)
        print(json.dumps(result, ensure_ascii=False, indent=1))
        if result["release"]:
            gate.decide("G3", True, note=f"Argilla 审核通过率 {result['pass_rate']}")
            print("✔ G3 放量闸已自动放行")
        else:
            print(f"未达放行条件（需 ≥10 条且通过率 ≥0.9）；如需手动放行: df gate approve G3")
        return 0

    if args.action == "app":
        # 轻量本地审核+监控应用（无 Docker）：streamlit run lib/webapp.py
        import subprocess

        print("启动本地审核应用: http://localhost:8501（Ctrl+C 退出）")
        return subprocess.call(
            [sys.executable, "-m", "streamlit", "run", str(ROOT / "lib" / "webapp.py"),
             "--server.port", "8501", "--server.headless", "true"]
        )

    if args.action == "summary":
        review_path = OUT_DIR / "review.jsonl"
        if not review_path.exists():
            print("暂无审核记录（先 df review app 审核或 df review push/pull）")
            return 0
        decisions = [json.loads(l) for l in review_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(json.dumps(review_mod.decide_gate(decisions), ensure_ascii=False, indent=1))
        return 0
    return 1


def cmd_monitor(args) -> int:
    """监控摘要：本地 runs.jsonl（Langfuse 未配置时的兜底审计）。"""
    runs_path = OUT_DIR / "runs.jsonl"
    if not runs_path.exists():
        print("暂无运行记录（执行 df import / df distill / df export 后生成）")
        return 0
    runs = [json.loads(l) for l in runs_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"本地运行审计 {len(runs)} 条（最近 {min(len(runs), args.n)} 条）：")
    for r in runs[-args.n :]:
        usage = r.get("usage") or {}
        extra = ""
        if usage:
            extra = f" calls={usage.get('calls', '')} prompt={usage.get('prompt_tokens', '')} completion={usage.get('completion_tokens', '')}"
        print(f"  [{str(r.get('at', ''))[11:19]}] {r.get('kind', '')}{extra}")
    print("图形化查看: df review app → 侧边栏选「监控」")
    return 0


def cmd_gate(args) -> int:
    gate = _gates()
    if args.action == "status":
        for gid, g in gate.defs.items():
            print(f"{gid} [{gate.status(gid)}] {g.title} — {g.trigger}")
        return 0
    if args.action == "propose":
        gate.propose(args.gate_id)
        print(f"{args.gate_id} → awaiting（待确认）")
        return 0
    if args.action in ("approve", "reject"):
        gate.decide(args.gate_id, args.action == "approve")
        print(f"{args.gate_id} → {'approved' if args.action == 'approve' else 'rejected'}")
        return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="df", description="Super-LLM-distill-Gen CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_import = sub.add_parser("import", help="导入 rollout 数据（需 G1 闸门）")
    p_import.add_argument("--limit", type=int, default=0)
    p_import.add_argument("--export-limit", type=int, default=200)
    p_import.add_argument("--cot", default="separated", choices=["separated", "tags", "plain", "drop"])
    p_import.set_defaults(func=cmd_import)

    sub.add_parser("stats", help="查看导入统计").set_defaults(func=cmd_stats)

    p_preview = sub.add_parser("preview", help="预览样本（--html 生成美化渲染页）")
    p_preview.add_argument("--n", type=int, default=10)
    p_preview.add_argument("--file", default=str(OUT_DIR / "rollout_samples.jsonl"))
    p_preview.add_argument("--html", action="store_true", help="生成静态 HTML 预览页（人工过目）")
    p_preview.set_defaults(func=cmd_preview)

    p_export = sub.add_parser("export", help="导出训练格式")
    p_export.add_argument("--format", default="chat", choices=["llamafactory", "chat"])
    p_export.add_argument("--input", default=str(OUT_DIR / "rollout_samples.jsonl"))
    p_export.add_argument("--out", default=str(OUT_DIR / "export" / "sft.jsonl"))
    p_export.add_argument("--bulk", action="store_true", help="放量导出（需 G3 闸门）")
    p_export.set_defaults(func=cmd_export)

    p_distill = sub.add_parser("distill", help="蒸馏质检：分类+DPO负样本+可选LLM打分")
    p_distill.add_argument("--llm-check", type=int, default=0, help="LLM 打分条数（>0 需 G0 闸门）")
    p_distill.set_defaults(func=cmd_distill)

    p_review = sub.add_parser("review", help="人工审核（app=本地轻量应用，push/pull=Argilla 可选）")
    p_review.add_argument("action", choices=["app", "push", "pull", "summary"])
    p_review.add_argument("--n", type=int, default=20, help="push 条数")
    p_review.set_defaults(func=cmd_review)

    p_monitor = sub.add_parser("monitor", help="运行监控摘要（本地审计）")
    p_monitor.add_argument("--n", type=int, default=10)
    p_monitor.set_defaults(func=cmd_monitor)

    p_gate = sub.add_parser("gate", help="闸门管理")
    p_gate.add_argument("action", choices=["propose", "approve", "reject", "status"])
    p_gate.add_argument("gate_id", nargs="?", default="")
    p_gate.set_defaults(func=cmd_gate)

    args = parser.parse_args()
    try:
        return args.func(args)
    except GateBlocked as e:
        print(f"[闸门拦截] {e}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
