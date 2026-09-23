/**
 * dataforge 工具插件：把 Super-LLM-distill-Gen 的 df-* CLI 暴露为 harness 工具。
 *
 * 设计（不造轮子）：本插件只做"调度"——按 command/options 参数调用
 * `python -m lib.cli <command> --k v…`，所有数据智能都在 Python 侧成熟管线中。
 * 环境变量：
 *   DF_ROOT    必填：Super-LLM-distill-Gen 项目根目录（绝对路径）
 *   DF_PYTHON  可选：python 解释器路径（缺省 PATH 中的 python）
 *
 * 注意（效率守卫）：命令列表与描述写死在本文件，与 lib/cli.py 的子命令保持一致，
 * 由 tests/test_dsh_plugin.py 交叉校验，防止两边漂移。
 */
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'

const execFileAsync = promisify(execFile)

export const name = 'dataforge'
export const inject = ['tools']

const COMMAND_HELP: Record<string, string> = {
  workflow: '自动 CPT/SFT/DPO/RLAIF/GSM8K/CoT/ORPO 工作流（--action start/resume/list；JEV 专用评分模型）',
  import: '导入 rollout 真实会话数据 → 多轮 SFT 样本（需 G1 数据源闸门已过）',
  stats: '查看导入统计（各会话文件 ok/error/模型分布）',
  preview: '预览样本（--html 生成美化渲染页供人工过目）',
  export: '导出训练格式（--format chat|llamafactory|minimind|all；--bulk 放量需 G3 闸门）',
  distill: '蒸馏质检：分类+DPO 负样本提取+可选 judge 打分（--llm-check N 需 G0 闸门）',
  review: '人工审核（app=本地轻量应用；push/pull 对接 Argilla）',
  'review-remote': '分布式人工审核（协作者在自己主机 pull/auto/human/submit，提交带身份与理由可审计）',
  'prompt-eval': '提示词真机评测（G0 闸门；--ids 按前缀过滤）',
  translate: '中英互译+回译校验（G0 闸门）',
  'identity-gen': '身份问答零参考训练集（G0 闸门；--max-answer-tokens 上限守卫）',
  doc2corpus: '文档→CPT 语料（知识注入层，零 LLM 成本）',
  doc2data: '文档→问答 SFT（--mode single|cross；cross=跨块综合分析自然长数据；G0 闸门）',
  'cot-style': 'CoT 风格偏好调教（G0 闸门）',
  vision: '多模态图文数据管线（图片目录→问答/多轮对话；G0 闸门）',
  'dpo-enhance': 'DPO 偏好对增强（--mode candidates|refine|hallucinate；G0 闸门）',
  'dpo-merge': '统一汇集各来源 DPO 对并去重',
  'agent-gen': 'Agent 工具使用零参考数据（--scenario web|code|indirect_web；G0 闸门）',
  'gui-cot': 'GUI 轨迹 CoT 蒸馏（上游 OpenCUA 成品；需截图轨迹 JSONL）',
  'style-correct': '语言风格强矫正（多轮去 AI 味；用户注入规则/示例；G0 闸门）',
  monitor: '运行监控摘要（本地审计）',
  models: '列出可用模型（models 网关自动获取）',
  gate: '闸门管理（action=status|approve|reject|propose，gate_id 可选）',
  workspace: '工作区=用户准备的数据文件夹（action=list|status|use|add + 名称/路径；add 注册外部文件夹）',
  user: '协作者账号管理（action=create+用户名 或 list；审核中心内置，key 发给协作者离线配置）',
  'review-server': '审核中心 HTTP 服务（独立模式；控制台进程内置同款 API）',
  doctor: '只读环境自检，不运行测试、不修改配置',
  'quality-report': '检查当前输入的结构、重复、审核覆盖与分歧（options.input 可指定）',
  backend: '模型后端与密钥管理（list 只读；add/test 由操作人员自行为之，agent 不写密钥）',
}

// 位置参数命令：options 中的这些键按"值"顺序拼接，不加 --前缀（与 lib/cli.py argparse 对齐）
const POSITIONAL_OPS: Record<string, string[]> = {
  gate: ['action', 'gate_id'],
  backend: ['action'],
  review: ['action'],
  'review-remote': ['action'],
  workspace: ['action', 'name'],
  user: ['action', 'name'],
}

export function apply(ctx: Context) {
  ctx.tools.register(defineTool({
    name: 'dataforge',
    description:
      '训练数据工厂统一入口（Super-LLM-distill-Gen）。按 command 调度对应的数据管线；'
      + '执行前务必用 gate 子命令确认所需闸门（G0 预算/G1 数据源/G3 放量）已通过。'
      + '子命令说明：'
      + Object.entries(COMMAND_HELP).map(([c, h]) => `${c}(${h})`).join('；'),
    parameters: {
      command: {
        type: 'string',
        required: true,
        description: `子命令名，取值：${Object.keys(COMMAND_HELP).join(' | ')}`,
      },
      options: {
        type: 'object',
        additionalProperties: true,
        description: '命令参数键值对（如 {"format": "chat", "bulk": true}；布尔值 true 表示只传 --key）',
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: true,
      },
      render: (_args, value) => [{
        type: 'text',
        text: typeof value === 'object' && value !== null && typeof value.stdout === 'string'
          ? value.stdout
          : JSON.stringify(value),
      }],
    },
    async execute(args, exec) {
      const root = process.env.DF_ROOT
      if (!root) {
        throw new Error('DF_ROOT 环境变量未设置（应为 Super-LLM-distill-Gen 项目根绝对路径）')
      }
      if (!Object.prototype.hasOwnProperty.call(COMMAND_HELP, args.command)) {
        throw new Error(`未知子命令 ${args.command}；可用：${Object.keys(COMMAND_HELP).join(', ')}`)
      }
      const python = process.env.DF_PYTHON || 'python'
      const argv = ['-m', 'lib.cli', args.command]
      const positional = POSITIONAL_OPS[args.command] ?? []
      const options = args.options ?? {}
      for (const key of positional) {
        if (options[key] !== undefined && options[key] !== null) argv.push(String(options[key]))
      }
      for (const [key, value] of Object.entries(options)) {
        if (positional.includes(key) || value === false || value === null || value === undefined) continue
        if (!/^[a-z][a-z0-9_-]*$/.test(key)) throw new Error(`Invalid option: ${key}`)
        argv.push(`--${key.replaceAll('_', '-')}`)
        if (value !== true) argv.push(String(value))
      }
      if (args.command === 'gate' && ['approve', 'reject'].includes(String(options.action))) {
        throw new Error('Gate decisions require explicit human confirmation in the console or CLI')
      }
      if (args.command === 'user' || args.command === 'review-server') {
        throw new Error('Administrative/lifecycle commands must be run by the operator, not by the agent')
      }
      const { stdout, stderr } = await execFileAsync(python, argv, {
        cwd: root,
        signal: exec.signal,
        maxBuffer: 8 * 1024 * 1024,
        windowsHide: true,
      })
      return { exitCode: 0, stdout, stderr }
    },
  }))
}
