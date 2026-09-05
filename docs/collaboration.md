# 多人协作审核（分布式评审）

> 交付标准是商业级数据制作：**多人协作审核是标配**。中心机跑 Argilla 评审平台，
> 每一位协作者在自己主机上开自己的 agent（自己配置模型）完成评审，提交回中心可审计。
>
> 已真机验证：协作者 pull 5 条 → 本地 agent（deepseek-v4-pro）判 keep 1 / reject 4 →
> 提交回中心，中心可见两个身份共 15 条响应（keep 11 / reject 4），每条可追到人。

## 部署模型

```
            中心机（我们的主机，mode collab 全栈）
  ┌─────────────────────────────────────────────────┐
  │ Redis + ES(JVM 2GB) + Argilla(6900, 中文界面)     │  ← 平台，只存评审题与响应
  │ 预览页(18700) + 控制台(8501)                     │
  └─────────────────────────────────────────────────┘
        ▲ pull（拉我的待审）        · api_key 每人唯一
        │ submit（提交我的判定）     · 响应带身份+理由
  ┌──────────────────┐      ┌──────────────────┐
  │ 协作者 A 主机      │      │ 协作者 B 主机      │
  │ 自己的 agent/模型  │      │ 自己的 agent/模型  │
  └──────────────────┘      └──────────────────┘
```

- 协作者**不需要**中心机用户、不需要 Argilla 服务端——只需要它自己的模型端点
  （`LLM_BASE_URL`/`LLM_MODEL` 或 `configs/backends.local.yaml`，任何 OpenAI 兼容端点）；
- "谁开 agent 谁就是评审 agent"：auto 模式用**协作者自己的模型**判定，中心只收结果+理由。

## 一、中心机部署（管理员）

```bash
cd Super-LLM-distill-Gen

# 1. 启动完整协作栈（看门狗守护，自动重启下挂服务；建议注册为开机任务）
python -m lib.services mode collab    # collab 已是默认；显式声明更清楚
python -m lib.services start
python -m lib.services status         # 应全部 UP，内存提示 ~2.5GB+

# 2. 初始化 Argilla 中文平台（幂等：迁移/管理员账号可重复执行）
python scripts/setup_argilla_user.py          # admin / distill123456，api_key=distill.apikey

# 3. 为每位协作者发放账号（每人唯一 api_key，审计链条的锚点）
python scripts/setup_argilla_user.py --create-user collaborator_zhang --role admin
#   → 协作者 zhang: api_key=agent.xxxxxxxx   （发给协作者本人，勿外泄/勿提交）
```

- 平台地址 http://127.0.0.1:6900（全中文界面；协作者经内网/隧道可达该地址）；
- 评审数据集 `rollout_review`（字段：样本 ID / 任务指令 / 对话内容 / 元数据；
  问题：**保留/驳回** + **判定理由/模型**），由 `df review push` 自动创建（幂等）；
- v1 协作者统一 `--role admin`（角色细粒度权限留后续版本）。

## 二、协作者接入（在自己主机）

```bash
git clone <仓库> && cd Super-LLM-distill-Gen
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
git submodule update --init --depth 1        # 仅跑评审无需 submodule，保险起见拉全

# 配置：服务器地址 + 管理员发放的密钥 + 我自己的评审模型
cp configs/review_remote.example.yaml configs/review_remote.yaml
#   server: http://<中心机>:6900        ← 协作者能访问的中心机地址
#   api_key: agent.xxxxxxxx             ← 管理员发放的密钥
#   model: deepseek-v4-pro              ← 我自己的模型（不填=judge 槽位/LLM_MODEL）

# 我的模型端点（任意 OpenAI 兼容，如 DeepSeek/本地 vLLM/Ollama）
export LLM_BASE_URL=https://api.deepseek.com/v1
export LLM_MODEL=deepseek-v4-pro
export NO_PROXY=127.0.0.1,localhost      # 本机 localhost 调用不走系统代理
```

## 三、评审三步（任意协作者）

```bash
python -m lib.cli review-remote pull --batch 10   # 1) 拉我的待审（跳过已提交者）
python -m lib.cli review-remote auto --model my-model  # 2a) 我的 agent 自动判
#   或
python -m lib.cli review-remote human             # 2b) 我本地逐条过目（理由必填）
python -m lib.cli review-remote submit            # 3) 以我的身份提交回中心
```

- `pull` 把待审记录缓存到 `data/output/remote_inbox.jsonl`（再次 pull 会覆盖缓存）；
- `auto` 用 `--model` > `review_remote.yaml model` > judge 槽位 解析我的模型，
  判定走 judge.score 提示词，**只用我的 LLM 密钥**——中心不产生我的费用；
- `human` 逐条打印指令/对话，输入 keep/reject + 理由；
- `submit` 把每条判定写成 Attrilla Response（`keep_label` + `reason`），
  response 的 `user_id` = 我的账号 → 中心审计可直接按身份统计；
- 提交后不可重复提交（`pull` 会跳过已 submitted 的记录）。

## 四、中心汇总与放行

```bash
python -m lib.cli review pull        # 拉全部评审响应（按身份/决策可统计）
python -m lib.cli review summary     # 通过率 ≥90% 且 ≥10 条 → 建议放行 G3
python -m lib.cli gate approve G3    # 人工确认后放量导出
```

审计依据：Argilla 每条响应 = 身份（`user_id`）+ 决策（keep/reject）+ 理由（模型名/分数
或人工理由）。`df review summary` 只统计所有**已提交**身份的响应。

## 五、安全与运维约定

- `configs/review_remote.yaml` 在 `.gitignore` 中（含服务器地址与密钥），**绝不提交**；
  `review_remote.example.yaml` 为模板随仓库分发；
- `scripts/setup_argilla_user.py` 只在中心机执行；`api_key` 通过离线渠道发给协作者；
- 中心机看门狗每小时对单服务最多重启 3 次（防崩溃循环）；资源受限可降级
  `python -m lib.services mode light`（单进程，审核走控制台内置路径）；
- 协作者在局域网/公网访问中心机：防火墙放通 6900（仅评审 API 端口），
  建议只对评审参与者的 IP 开放，或经 SSH 隧道/内网穿透访问。
