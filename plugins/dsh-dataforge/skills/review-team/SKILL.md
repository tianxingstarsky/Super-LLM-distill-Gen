---
name: review-team
description: 用 dsh 子智能体组织 AI 审核小队：Lead 读本技能后创建多个 teammate（质检员/安全员），每个 teammate 持独立评审账号与配置，用 dataforge 工具完成 拉取→本地模型判定→提交，中心审计可追到子智能体身份。
whenToUse: 需要批量 AI 审核样本（keep/reject + 理由），且希望按子智能体分工、审计到人。
---

# AI 审核小队（子智能体审核）

## 角色 ↔ 配置对照表（Lead 必须按此表给子智能体分发"它自己的配置"）

| 子智能体 | 职责 | 配置（config= 参数） | 提交身份 |
|---|---|---|---|
| `quality` | 质检员：内容正确性/冗余/格式评分（judge.score） | 见下方"配置路径"第 1 条 | judge_quality |
| `safety` | 安全员：敏感内容/幻觉/危险操作否决 | 见下方"配置路径"第 2 条 | judge_safety |

## 配置路径（工具调用 dataforge 的 options.config 值，勿改文件内容）

1. `F:/无项目工作文件夹/Super-LLM-distill-Gen/configs/review_remote.judge_quality.yaml`
2. `F:/无项目工作文件夹/Super-LLM-distill-Gen/configs/review_remote.judge_safety.yaml`

（两份配置各自绑定独立 api_key 与评审模型；**只读使用**，永久固定。）

## 多角色审同批（多维度视角）

每个角色是独立身份：quality 与 safety 会各自拉到**同一批**未审记录——
这是刻意的（质量 + 安全两个维度分别评审），中心按身份各自记录，
汇总时每一维度的判定都可独立审计；若某角色已提交过某记录，中心自动跳过（不重复）。

## 流程（每个 teammate 干两件事，然后 Lead 汇总）

1. Lead 用团队工具创建两个 teammate：`quality` 与 `safety`（小写名，永久）；
2. 每个 teammate 依次调用 dataforge 工具（命令 review-remote）：
   - `pull`：options `{"action": "pull", "config": "<自己的配置路径>", "batch": 3}`
     只拉自己账号的待审；3 条即可，不要多拉（预算纪律）；
   - `auto`：options `{"action": "auto", "config": "<自己的配置路径>"}`
     用自己的模型判定（中心不花一分钱）；判完只落本地缓存；
   - `submit`：options `{"action": "submit", "config": "<自己的配置路径>"}`
     以**自己的评审账号**提交（身份+理由可审计）；
3. teammate 向 Lead 回报 summary（pull 条数 / keep / reject）；
4. Lead 最后调用一次 dataforge `review`（action=summary）看中心通过率，
   用一句话向用户汇报：各子智能体审了几条、中心总计 keep/reject、是否达放行线。

## 铁律

- 每个 teammate 只允许使用**自己的配置**进入 pull/auto/submit，绝不混用别人配置；
- `auto` 只判定不提交（提交必须显式 submit，防止重复）；
- 单个子智能体单次任务最多 3 条（超出请分批）；
- 中心已有回应的记录会被自动跳过（pull 只给未审的）；
- 不改配置、不建新账号、不越过闸门（G0 预算用于 auto 的本机模型调用，属评审者自己的费用）。
