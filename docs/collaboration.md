# 多人协作审核（分布式评审，单进程融合版）

> 交付标准是商业级数据制作：**多人协作审核是标配**。中心机一个进程（控制台或
> `df review-server`）提供 SQLite 审核中心 + 协作 HTTP API；
> 每一位协作者在自己主机上开自己的 agent（自己配置模型）完成评审，提交回中心可审计。
>
> 已真机验证：三人协作——admin 10 条 + collaborator_zhang 5 条 + collaborator_wang 5 条，
> 中心共 20 条响应（keep 12 / reject 8，未达 90% 放行线），每条可追到人。

## 部署模型

```
            中心机（一个进程：df console 或 df review-server）
  ┌─────────────────────────────────────────────────┐
  │ 控制台 UI(8501) + SQLite 审核中心 + HTTP API(6900) │  ← 无 Redis/ES/Argilla
  │ /files/ 静态预览                                │
  └─────────────────────────────────────────────────┘
        ▲ pull（拉我的待审）        · api_key 每人唯一
        │ submit（提交我的判定）     · 响应带身份+理由
  ┌──────────────────┐      ┌──────────────────┐
  │ 协作者 A 主机      │      │ 协作者 B 主机      │
  │ 自己的 agent/模型  │      │ 自己的 agent/模型  │
  └──────────────────┘      └──────────────────┘
```

- 协作者**不需要**中心机账号的 UI，只需要：中心机可达的 `http://<中心>:6900`、
  管理员发放的 api_key、以及自己的模型端点（`LLM_BASE_URL`/`LLM_MODEL` 或
  `configs/backends.local.yaml`，任何 OpenAI 兼容端点）；
- "谁开 agent 谁就是评审 agent"：auto 模式用**协作者自己的模型**判定，中心只收结果+理由。

## 一、中心机部署（管理员）

```bash
cd Super-LLM-distill-Gen

# 1. 一键启动（控制台 UI + 审核中心 API 共一个进程；无看门狗/无后台服务）
bash scripts/start_all.bat            # 或 python -m lib.cli console
#    仅要 API 不要 UI 的部署：python -m lib.cli review-server --port 6900

# 2. 管理员账号首次使用自动创建（api_key=distill.apikey；环境变量 REVIEW_ADMIN_KEY 可换）

# 3. 为每位协作者发放账号（每人唯一 api_key，审计链条的锚点）
python -m lib.cli user create collaborator_zhang --role admin
#   → 协作者 zhang: api_key=agent.xxxxxxxx   （离线发给协作者本人，勿外泄/勿提交）
```

- 审核中心 API `http://127.0.0.1:6900`（`/health`、`/api/me`、`/api/pending`、
  `/api/submit`、`/api/responses`、`/files/<path>`）；
- 数据集 `rollout_review`（按工作区自动变 `rollout_review_<工作区>`），
  由 `df review push` 自动建（幂等）；
- v1 协作者统一 `--role admin`（角色细粒度权限留后续版本）；
- 备份=拷贝 `data/review_center.db` 一个文件。

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
python -m lib.cli review-remote pull --batch 10    # 1) 拉我的待审（跳过已提交者）
python -m lib.cli review-remote auto --model my-model  # 2a) 我的 agent 自动判（判定完不提交）
python -m lib.cli review-remote human              # 2b) 我本地逐条过目（理由必填）
python -m lib.cli review-remote submit             # 3) 确认后以我的身份提交回中心
```

- `pull` 把待审记录缓存到 `data/output/remote_inbox.jsonl`（再次 pull 覆盖缓存；
  加 `--ws <工作区>` 时按工作区分流）；
- `auto` 用 `--model` > `review_remote.yaml model` > judge 槽位解析我的模型，
  **只用我的 LLM 密钥**——中心不产生我的费用；判定完成只落本地，`submit` 才提交；
- `submit` 以我的账号提交（决策+理由），中心唯一约束防重复提交；
- 提交后 `pull` 会跳过已 submitted 记录——不可重复，判定即终稿。

## 四、中心汇总与放行

```bash
python -m lib.cli review pull        # 拉全部评审响应（按身份/决策/理由统计）
python -m lib.cli review summary     # 通过率 ≥90% 且 ≥10 条 → 建议放行 G3
python -m lib.cli gate approve G3    # 人工确认后放量导出
```

审计依据：SQLite 中每条响应 = 身份（username）+ 决策（keep/reject）+ 理由
（模型名/分数或人工理由），`responses` 表带时间戳。

## 五、安全与运维约定

- `configs/review_remote.yaml` 在 `.gitignore` 中（含服务器地址与密钥），**绝不提交**；
  `review_remote.example.yaml` 为模板随仓库分发；
- `df user create` 只在中心机执行；`api_key` 通过离线渠道发给协作者；
- 协作者经内网/公网访问中心机：防火墙放通 6900（仅评审 API 端口），
  建议只对评审参与者的 IP 开放，或经 SSH 隧道/内网穿透访问；
- 中心机单点=控制台进程：重启即恢复（SQLite 落盘无损）；`start_all.bat` 双击即起。
