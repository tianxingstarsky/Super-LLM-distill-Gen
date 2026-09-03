"""真实 rollout 数据导入：统计 + 样本导出（全局 manifest 查重）+ rejected 归档。

运行：
  .venv/Scripts/python.exe scripts/import_rollout.py [--limit N] [--export-limit M] [--cot r1]

产物（data/output/，gitignored）：
  rollout_stats.json        各文件统计（记录数/ok/error/模型/finishReason/错误步骤）
  rollout_samples.jsonl     SFT 样本（含思考+工具调用的闭环多轮，DeepSeek/Qwen 训练格式）
  rollout_rejected.jsonl    错误记录归档（DPO 负样本候选）
  manifest_rollout.txt      全局 sha256 清单（增量导入零重复）
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.adapters.rollout_import import (  # noqa: E402
    ManifestDedup,
    iter_records,
    record_status,
    record_to_sample,
)
from lib.pipeline_config import load_pipeline_config  # noqa: E402

_rcfg = load_pipeline_config("rollout", ROOT)["rollout"]
# 数据源可配：configs/pipelines/rollout.yaml 或环境变量 ROLLOUT_DIR
ROLLOUT_DIR = pathlib.Path(os.environ.get("ROLLOUT_DIR", _rcfg["dir"]))
ROLLOUT_PATTERN = _rcfg["pattern"]
OUT_DIR = ROOT / "data" / "output"
COT_STYLE = "separated"


def run(limit: int = 0, export_limit: int = 200, cot: str = COT_STYLE) -> None:
    """核心导入流程（CLI 与脚本共用）。"""
    files = sorted(ROLLOUT_DIR.glob(ROLLOUT_PATTERN))
    manifest = ManifestDedup(OUT_DIR / "manifest_rollout.txt")
    stats_all = {}
    samples_written = 0
    rejected_written = 0

    with open(OUT_DIR / "rollout_samples.jsonl", "w", encoding="utf-8") as fs, \
         open(OUT_DIR / "rollout_rejected.jsonl", "w", encoding="utf-8") as fr:
        for path in files:
            fin = Counter()
            models = Counter()
            err_tool_steps = 0
            ok, err = 0, 0
            for i, rec in enumerate(iter_records(path)):
                if limit and i >= limit:
                    break
                status = record_status(rec)
                if status == "ok":
                    ok += 1
                    fin[(rec.get("response") or {}).get("finishReason") or "?"] += 1
                    models[(rec.get("model") or {}).get("modelId", "?")] += 1
                    sample = record_to_sample(rec, cot)
                    err_tool_steps += sample["error_tool_steps"]
                    if samples_written < export_limit and not manifest.seen(sample["id"]):
                        fs.write(json.dumps(sample, ensure_ascii=False) + "\n")
                        samples_written += 1
                    manifest.add(sample["id"])
                else:
                    err += 1
                    if rejected_written < 100:
                        fr.write(json.dumps({
                            "requestId": rec.get("requestId"),
                            "sessionId": rec.get("sessionId"),
                            "model": (rec.get("model") or {}).get("modelId", "?"),
                            "reason": str((rec.get("error") or {}).get("message", "empty/partial"))[:300],
                        }, ensure_ascii=False) + "\n")
                        rejected_written += 1
            stats_all[path.name] = {
                "ok": ok, "error": err, "finish_reasons": dict(fin),
                "models": dict(models), "error_tool_steps": err_tool_steps,
            }
            print(f"{path.name}: ok={ok} error={err} models={dict(models)} fin={dict(fin)} toolErr={err_tool_steps}")

    manifest.save()
    (OUT_DIR / "rollout_stats.json").write_text(
        json.dumps(stats_all, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"\n导出样本 {samples_written} 条；manifest 新增 {manifest.new}、命中重复 {manifest.hits}")
    print(f"统计/样本/拒绝归档/清单 → {OUT_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="每文件最多处理的记录数（0=全部）")
    parser.add_argument("--export-limit", type=int, default=200, help="最多导出的样本数")
    parser.add_argument("--cot", default=COT_STYLE, choices=["separated", "tags", "plain", "drop"])
    args = parser.parse_args()
    run(args.limit, args.export_limit, args.cot)


if __name__ == "__main__":
    main()
