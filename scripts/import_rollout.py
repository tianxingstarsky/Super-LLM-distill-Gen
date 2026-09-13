"""真实 rollout 数据导入：统计 + 样本导出（全局 manifest 查重）+ rejected 归档。

运行：
  .venv/Scripts/python.exe scripts/import_rollout.py [--limit N] [--export-limit M] [--cot r1]
                                                    [--rollout-dir DIR]

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
# 历史默认数据源（default 工作区回退用）：configs/pipelines/rollout.yaml 或环境变量 ROLLOUT_DIR
ROLLOUT_DIR = pathlib.Path(os.environ.get("ROLLOUT_DIR", _rcfg["dir"]))
ROLLOUT_PATTERN = _rcfg["pattern"]
OUT_DIR = ROOT / "data" / "output"
COT_STYLE = "separated"

# 生成产物与依赖目录不是源数据（.dataforge=本项目/所选文件夹的产物目录，防自我摄取）
EXCLUDE_DIRS = {".dataforge", ".git", ".venv", "node_modules", "__pycache__"}


def source_files(source: str | pathlib.Path | None = None) -> list[pathlib.Path]:
    """枚举 rollout 源文件。目录递归匹配 pattern，但跳过产物/依赖目录（不自我摄取）。

    source=None 保持历史行为：仅 ROLLOUT_DIR 顶层匹配 ROLLOUT_PATTERN（不递归）。
    显式目录（所选文件夹或 --rollout-dir）：递归匹配，排除 .dataforge 等目录。
    显式文件：直接作为唯一源。
    目录与文件都排除符号链接/junction（lib.workspace.is_linked，Python 3.11 无 is_junction）。
    """
    from lib.workspace import is_linked

    if source is None:
        return sorted(ROLLOUT_DIR.glob(ROLLOUT_PATTERN))
    path = pathlib.Path(source).expanduser()
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    found: list[pathlib.Path] = []
    for directory, dirs, names in os.walk(path, followlinks=False):
        dirs[:] = sorted(
            d for d in dirs
            if d not in EXCLUDE_DIRS and not is_linked(pathlib.Path(directory) / d)
        )
        for name in sorted(names):
            candidate = pathlib.Path(directory) / name
            if not is_linked(candidate) and candidate.match(ROLLOUT_PATTERN):
                found.append(candidate)
    return found


def source_label(source: str | pathlib.Path | None = None) -> pathlib.Path:
    """实际数据源路径（G1 提案/错误信息用；None=历史默认目录）。"""
    return ROLLOUT_DIR if source is None else pathlib.Path(source).expanduser()


def _stats_key(path: pathlib.Path, source) -> str:
    """源文件的统计键：显式目录用相对路径（不同子目录同名文件不再互相覆盖），
    历史默认（source=None，顶层 glob）保持 basename 不变。"""
    if source is None:
        return path.name
    try:
        return path.resolve().relative_to(source_label(source).resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


def run(limit: int = 0, export_limit: int = 200, cot: str = COT_STYLE,
        out_dir: pathlib.Path | None = None,
        source: str | pathlib.Path | None = None) -> None:
    """Incremental import; only records actually written enter the manifest.

    source=None 按历史默认（ROLLOUT_DIR/配置）；显式目录按所选文件夹递归枚举（排除 .dataforge）。"""
    from lib.workspace import output_at
    from filelock import FileLock

    destination = pathlib.Path(out_dir) if out_dir else output_at(ROOT)
    destination.mkdir(parents=True, exist_ok=True)
    with FileLock(str(destination / "import.lock"), timeout=1):
        _run_locked(destination, limit, export_limit, cot, source=source)


def _run_locked(OUT_DIR, limit, export_limit, cot, source=None):
    files = source_files(source)
    if not files:
        raise ValueError(f"No source files: {source_label(source)} (pattern {ROLLOUT_PATTERN})")
    manifest = ManifestDedup(OUT_DIR / "manifest_rollout.txt")
    samples_path = OUT_DIR / "rollout_samples.jsonl"
    existing = set()
    if samples_path.exists():
        # 容错解析：追加写的文件可能因中断留下截断行/缺 id 行；单行损坏不能让
        # 整个导入永久失败（旧实现 json.loads(line)["id"] 直接 KeyError/JSONDecodeError）。
        for line in samples_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(record, dict) and record.get("id"):
                existing.add(record["id"])
    stats_all = {}
    samples_written = 0
    rejected_written = 0
    manifest_skipped = 0

    with open(OUT_DIR / "rollout_samples.jsonl", "a", encoding="utf-8") as fs, \
         open(OUT_DIR / "rollout_rejected.jsonl", "a", encoding="utf-8") as fr:
        for path in files:
            key = _stats_key(path, source)
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
                    # manifest 是全局去重清单：即使样本文件被删/轮转，已导入过的 id 也不再重复写入
                    if samples_written < export_limit and sample["id"] not in existing:
                        if manifest.seen(sample["id"]):
                            manifest_skipped += 1
                        else:
                            fs.write(json.dumps(sample, ensure_ascii=False) + "\n")
                            samples_written += 1
                            existing.add(sample["id"])
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
            stats_all[key] = {
                "ok": ok, "error": err, "finish_reasons": dict(fin),
                "models": dict(models), "error_tool_steps": err_tool_steps,
            }
            print(f"{key}: ok={ok} error={err} models={dict(models)} fin={dict(fin)} toolErr={err_tool_steps}")

    manifest.save()
    (OUT_DIR / "rollout_stats.json").write_text(
        json.dumps(stats_all, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"\n导出样本 {samples_written} 条；manifest 新增 {manifest.new}、命中重复 {manifest.hits}"
          + (f"、历史清单跳过 {manifest_skipped}" if manifest_skipped else ""))
    print(f"统计/样本/拒绝归档/清单 → {OUT_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="每文件最多处理的记录数（0=全部）")
    parser.add_argument("--export-limit", type=int, default=200, help="最多导出的样本数")
    parser.add_argument("--cot", default=COT_STYLE, choices=["separated", "tags", "plain", "drop"])
    parser.add_argument("--rollout-dir", default=None, help="显式数据源目录（缺省=环境变量/配置的默认目录）")
    args = parser.parse_args()
    run(args.limit, args.export_limit, args.cot, source=args.rollout_dir)


if __name__ == "__main__":
    main()
