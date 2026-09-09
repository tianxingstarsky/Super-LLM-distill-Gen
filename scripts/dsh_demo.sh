#!/usr/bin/env bash
# dsh 正式接入演示（可复现）：一条完整链，费用封顶 2 条样本评审。
#   bash scripts/dsh_demo.sh [tag] [config]
#     tag    默认 dsh-demo-1（导出版本目录名）
#     config 默认 configs/review_remote.demo.yaml（独立演示数据集+账号；
#            也可用 configs/review_remote.judge_quality.yaml 走真实审核队列）
# 链路：读技能 → gate status → quality-report → 以配置内账号 pull2/auto/submit
#       → 草稿导出 chat（不加 --bulk）→ 按技能回报格式总结
set -e
DF_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${1:-dsh-demo-1}"
CONFIG="${2:-configs/review_remote.demo.yaml}"
LOG="$DF_ROOT/data/output/dsh_demo_$(date +%Y%m%dT%H%M%S).log"

TASK="你是 DataForge 操作员，这是 dsh 正式接入演示。先读取 dataforge 技能并遵守其硬约束。
全程 ws=default；只允许对 2 条样本做评审（费用封顶）；不得代批闸门、不得读写密钥、不得使用 --bulk。
步骤（每一步都调用 dataforge 工具，并报告真实输出）：
1) gate action=status，报告 G0/G1/G3 状态；
2) quality-report，报告 samples / issue_count / review_coverage / ready_for_bulk；
3) review-remote：config=${CONFIG}，依次
   action=pull batch=2  →  action=auto policy=quality  →  action=submit；
4) export：format=chat tag=${TAG}（不要 --bulk）；
5) 最后按技能第五节的回报格式输出：结论/产物/依据/闸门/未完成项。"

echo "=== dsh 正式接入演示（tag=$TAG, config=$CONFIG）==="
bash "$DF_ROOT/scripts/dsh_smoke.sh" "$TASK" 2>&1 | tee "$LOG"

echo
echo "=== 演示后验证 ==="
ls -la "$DF_ROOT/data/output/export/$TAG" 2>/dev/null || echo "（export 版本目录未生成）"
"$DF_ROOT/.venv/Scripts/python.exe" - "$DF_ROOT" "$CONFIG" <<'PY'
import sys, yaml
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from lib import review_center as rc
cfg = yaml.safe_load(Path(sys.argv[1], sys.argv[2]).read_text(encoding="utf-8"))
dataset = cfg.get("dataset", "rollout_review")
rows = rc.responses(dataset)
print(f"数据集 {dataset} 响应数: {len(rows)}")
for r in rows[-2:]:
    print(f"  [{r['username']}] {r['sample_id']} {r['decision']} | {r['reason'][:90]}")
PY
echo "日志: $LOG"

