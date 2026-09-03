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
    return 0


def cmd_stats(args) -> int:
    stats = json.loads((OUT_DIR / "rollout_stats.json").read_text(encoding="utf-8"))
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


def cmd_preview(args) -> int:
    path = pathlib.Path(args.file)
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines[: args.n]:
        sample = json.loads(line)
        last = sample["messages"][-1]
        content = str(last.get("content", ""))[:200]
        reasoning = str(last.get("reasoning_content", ""))[:120]
        print(f"[{sample['id'][:20]}] model={sample['model']} fin={sample['finish_reason']} "
              f"msgs={len(sample['messages'])} toolErr={sample['error_tool_steps']}")
        if reasoning:
            print(f"    思考: {reasoning}…" if len(reasoning) == 120 else f"    思考: {reasoning}")
        print(f"    正文: {content}")
        print()
    print(f"（共 {len(lines)} 条，展示前 {min(args.n, len(lines))} 条）")
    return 0


def cmd_export(args) -> int:
    from lib.exporters import export_samples

    if args.bulk:
        _gates().require("G3")  # 放量前须过小批量预览闸
    samples = (json.loads(l) for l in pathlib.Path(args.input).read_text(encoding="utf-8").splitlines() if l.strip())
    counts = export_samples(samples, args.format, args.out)
    print(f"导出完成: {counts}（{args.format} → {args.out}）")
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

    p_preview = sub.add_parser("preview", help="预览样本")
    p_preview.add_argument("--n", type=int, default=10)
    p_preview.add_argument("--file", default=str(OUT_DIR / "rollout_samples.jsonl"))
    p_preview.set_defaults(func=cmd_preview)

    p_export = sub.add_parser("export", help="导出训练格式")
    p_export.add_argument("--format", default="chat", choices=["llamafactory", "chat"])
    p_export.add_argument("--input", default=str(OUT_DIR / "rollout_samples.jsonl"))
    p_export.add_argument("--out", default=str(OUT_DIR / "export" / "sft.jsonl"))
    p_export.add_argument("--bulk", action="store_true", help="放量导出（需 G3 闸门）")
    p_export.set_defaults(func=cmd_export)

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
