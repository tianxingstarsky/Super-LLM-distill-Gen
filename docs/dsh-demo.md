# dsh 正式接入演示（实测记录）

两种展示方式：
- **dsh 本体 Web UI（现场可见）**：`bash scripts/dsh_web.sh [端口]` 启动后按日志打印的
  带 token 地址打开（浏览器信任栅栏要求 `?token=…`）。实测在 dsh 对话里发
  "用 dataforge 工具执行 models 命令…"，界面出现 **1 次工具调用**，返回 3 个模型
  （deepseek-v4-flash / flash-vision-exp / pro，5 秒 / 75.3K tok），左侧会话列表可见
  历史 dataforge 会话。
- **headless 全链（可复现）**：`bash scripts/dsh_demo.sh [tag] [config]`（见下）。

`bash scripts/dsh_demo.sh [tag] [config]` —— 一条完整链，费用封顶 2 条样本评审，全程可复现。

## 演示链路（dsh 操作员按 dataforge 技能执行）

1. 读取 dataforge 技能（硬约束/注意要点/回报格式）；
2. `gate action=status` —— 只读查看 G0/G1/G3（**不得代批**）；
3. `quality-report` —— 真实质量快照（结构/重复/审核覆盖）；
4. `review-remote`（配置内独立账号）：`pull 2 → auto(quality) → submit` —— 中心审计到账号+理由；
5. `export format=chat tag=<tag>` —— 草稿导出（**不加 --bulk**，不触发放量条件）；
6. 按技能第五节回报格式输出：结论/产物/依据/闸门/未完成项。

## 实测结果（2026-09-10，本机）

### 第一跑：约束系统拦截（价值演示）
中心（6900）当时未运行。dsh 操作员：
- `pull` 报 `WinError 10061 连接被拒`；
- **没有伪造结果、没有自己启服务**（启动属操作人员专属）、没有跑 `auto`（付费）；
- 如实回报"部分完成"，并给出操作员应执行的 `scripts/start_all.vbs`；
- 仅完成 1/2/4 步（闸门/质量/草稿导出）。

### 第二跑：全链成功（tag=dsh-demo-2）
```
结论：完成
产物：data/output/export/dsh-demo-2/{sft.jsonl(250 条 chat, 28.5MB), manifest.json, quality.json}
依据：G0/G1/G3 approved；quality-report samples=250 issue_count=367 review_coverage=0.0
      ready_for_bulk=false；review-remote(judge_demo) pull2 → auto keep=2 未完成=0 → submit 2；
      export {'sft':250,'chat':250,'dpo':0}
闸门：只查看未代批；草稿导出未使用 --bulk
未完成项：评审覆盖远低于 90%、367 个结构问题；放量需补审+消问题+人工批准
```
中心审计（数据集 `rollout_review_demo`）：
```
[judge_demo] demo-1 keep | [quality] correctness=5: ...
[judge_demo] demo-2 keep | [quality] correctness=5: ...
```

## 复现步骤

```bash
# 0. 操作员：启动中心（单进程，8501+6900）
bash scripts/start_all.bat            # 或 python -m lib.cli console

# 1. 操作员：演示数据集与账号（一次性；独立于真实审核队列）
python -m lib.cli user create judge_demo
python -m lib.cli user grant judge_demo --ws default      # 数据集为 rollout_review_demo
#   并把 2 条小样本推入该数据集（见脚本注释/README），配置写入
#   configs/review_remote.demo.yaml（gitignored，含密钥）

# 2. 跑演示
bash scripts/dsh_demo.sh dsh-demo-3            # 默认用 demo 配置；也可传第二参数换配置
#   想跑真实审核队列：bash scripts/dsh_demo.sh dsh-demo-3 configs/review_remote.judge_quality.yaml
```

## 边界与成本

- 演示只对 2 条小样本做 LLM 评审（本次 keep=2），费用为美分级；其余步骤零成本；
- 草稿导出 250 条真实样本（含 367 个结构问题）**不可当成品放量**——`quality.json` 与
  `ready_for_bulk=false` 就是证据；
- dsh 侧模型开销（Lead 的推理）不计入 Python 预算守卫，演示任务文本已限制步数；
- 第一跑的"拒绝执行"是设计行为：技能硬约束 + 插件层拦截（`gate approve`/`user`/`review-server`
  对 agent 直接拒绝），不是故障。
