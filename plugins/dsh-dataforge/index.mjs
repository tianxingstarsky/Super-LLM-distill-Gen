/** External dsh bundle. The Python product imports none of this package. */
import {isAbsolute} from 'node:path'
import {createRequire} from 'node:module'
import {defineTool} from '@deepseek-ai/dsh-tools'
import {Dispatcher, validatePlan} from './dispatcher.mjs'
import {MUTATIONS, request, startRequest} from './transport.mjs'
import {registerBundledSkills} from './skills.mjs'

export const name = 'shujian-dataforge'
export const inject = ['tools', 'jobs', 'subprocess', 'skills']
const ACTIONS = ['capabilities', 'runs.list', 'runs.inspect', 'runs.create', 'runs.cancel', 'runs.events',
  'runs.streams', 'runs.stream_delta', 'runs.preview', 'runs.quality', 'models.list', 'models.status', 'models.limit']
const output = {schema: {type: 'object', additionalProperties: true},
  render: (_args, value) => [{type: 'text', text: JSON.stringify(value)}]}

async function dependency(promise, signal) {
  if (signal.aborted) return {status: 'killed'}
  let listener
  try {
    return await Promise.race([promise, new Promise(resolve => {
      listener = () => resolve({status: 'killed'})
      signal.addEventListener('abort', listener, {once: true})
    })])
  } finally {
    signal.removeEventListener('abort', listener)
  }
}

export function configuration(value = {}) {
  const config = {allowMutations: false, allowModelLimitChanges: false, maxActiveWorkflows: 4, modelConcurrencyCeiling: 4, monitorIntervalMs: 2000, ...value}
  if (!Array.isArray(config.projects) || config.projects.length > 100 || typeof config.allowMutations !== 'boolean'
      || typeof config.allowModelLimitChanges !== 'boolean'
      || !Number.isInteger(config.maxActiveWorkflows) || config.maxActiveWorkflows < 1 || config.maxActiveWorkflows > 32
      || !Number.isInteger(config.modelConcurrencyCeiling) || config.modelConcurrencyCeiling < 1 || config.modelConcurrencyCeiling > 256
      || !Number.isInteger(config.monitorIntervalMs) || config.monitorIntervalMs < 500 || config.monitorIntervalMs > 60000) throw new Error('invalid_adapter_configuration')
  const projects = new Map()
  for (const project of config.projects) {
    if (!project || typeof project.id !== 'string' || !/^[a-zA-Z0-9_-]{1,80}$/.test(project.id)
        || projects.has(project.id) || !isAbsolute(project.root) || !isAbsolute(project.python)
        || typeof (project.workspace ?? 'default') !== 'string'
        || !Array.isArray(project.forwardEnv ?? [])
        || (project.forwardEnv ?? []).some(name => typeof name !== 'string' || !/^[A-Z][A-Z0-9_]{0,127}$/.test(name)
          || name.startsWith('DSH_'))) throw new Error('invalid_adapter_project')
    projects.set(project.id, Object.freeze({...project}))
  }
  return {...config, projects}
}

export function apply(ctx, value) {
  const require = createRequire(import.meta.url)
  for (const module of ['dsh-tools', 'dsh-jobs', 'dsh-subprocess', 'dsh-skill']) {
    if (require(`@deepseek-ai/${module}/package.json`).version !== '0.2.1-rc.2') throw new Error('unsupported_harness_adapter_version')
  }
  const config = configuration(value)
  registerBundledSkills(ctx.skills)
  const dispatcher = new Dispatcher(config.maxActiveWorkflows)
  const call = (project, action, params, options = {}) => request(ctx.subprocess, project, action, params,
    {allowMutations: config.allowMutations, ...options})
  ctx.tools.register(defineTool({
    name: 'dataforge',
    description: 'Control and monitor configured DataForge installations through protocol v1. Read capabilities first. Create freezes a workflow without running it. Use dataforge_dispatch for long execution. Review quality and original reasoning before release; a model score is not proof. Never read credentials or bypass core validation. Events and stream deltas use byte cursors; previews are bounded and include reasoning.',
    parameters: {
      project: {type: 'string', required: true, description: `Configured installation: ${[...config.projects.keys()].join(', ')}`},
      action: {type: 'string', enum: ACTIONS, required: true},
      params: {type: 'object', additionalProperties: true},
      request_id: {type: 'string', description: 'Stable idempotency key for a repeated runs.create request; reuse with identical recipe.'},
    }, output,
    isConcurrencySafe: args => !MUTATIONS.has(args.action),
    async execute(args, exec) {
      const project = config.projects.get(args.project)
      if (!project) throw new Error('unknown_adapter_project')
      const params = args.params ?? {}
      if (args.action === 'models.limit' && !config.allowModelLimitChanges) throw new Error('operator_model_limit_changes_disabled')
      if (args.action === 'models.limit' && params.max_concurrency > config.modelConcurrencyCeiling) throw new Error('operator_model_limit_exceeded')
      if (args.action === 'runs.create' && !args.request_id) throw new Error('create_requires_stable_request_id')
      return call(project, args.action, params, {signal: exec.signal, ...(args.request_id ? {requestId: args.request_id} : {})})
    },
  }))
  ctx.tools.register(defineTool({
    name: 'dataforge_dispatch',
    description: 'Start an owned, monitored background job per frozen workflow. A plan can contain up to 32 runs across configured installations, priorities (-100..100), and dependency ids. Failed or cancelled dependencies block their children. Global workflow admission and core shared model limits both apply. Job handles support native job_output/job_list/job_kill. Core checkpoints and run ids remain inspectable after a harness restart; in-process job handles do not survive it.',
    parameters: {runs: {type: 'array', required: true, items: {type: 'object', additionalProperties: false, properties: {
      id: {type: 'string', required: true}, project: {type: 'string', required: true}, run_id: {type: 'string', required: true},
      priority: {type: 'integer'}, depends_on: {type: 'array', items: {type: 'string'}},
    }}}}, output,
    async execute(args, exec) {
      if (!config.allowMutations) throw new Error('adapter_mutations_disabled')
      if (exec.signal.aborted) throw new Error('dispatch_cancelled')
      if (!exec.agent) throw new Error('dispatch_requires_session_owner')
      const runs = validatePlan(args.runs, config.projects)
      const gates = new Map(runs.map(item => {
        let resolve
        const promise = new Promise(done => {resolve = done})
        return [item.id, {promise, resolve}]
      }))
      const handles = []
      try {
        for (const item of runs) {
          const project = config.projects.get(item.project)
          const jobId = ctx.jobs.start({kind: 'dataforge', label: `${item.project} / ${item.run_id}`,
            owner: exec.agent.id, toolCall: {callId: exec.callId, rootCallId: exec.rootCallId}, outputLimitBytes: 32768,
            run(job) {
              const abort = new AbortController()
              const monitorAbort = new AbortController()
              let running = false, stopTimer, monitorTimer, monitorWork, cancelWork, process
              let cancelled = false, cursor = 0
              const cancel = () => {
                if (cancelled) return
                cancelled = true
                abort.abort()
                if (running) {
                  cancelWork = call(project, 'runs.cancel', {run_id: item.run_id}, {signal: AbortSignal.timeout(5000)}).catch(() => {})
                  stopTimer = setTimeout(() => process?.terminate(), 5000)
                  stopTimer.unref?.()
                }
              }
              const done = (async () => {
                try {
                  job.updateProgress('waiting for dependencies')
                  for (const parent of item.depends_on ?? []) {
                    const result = await dependency(gates.get(parent).promise, abort.signal)
                    if (cancelled) throw new Error('dispatch_cancelled')
                    if (result.status !== 'completed') throw new Error('dispatch_dependency_failed')
                  }
                  job.updateProgress('queued for workflow admission')
                  return await dispatcher.schedule(`${item.project}:${item.run_id}`, item.priority ?? 0, abort.signal, async () => {
                    if (cancelled) throw new Error('dispatch_cancelled')
                    // The published task owns its lifetime, not the outer tool signal.
                    running = true
                    process = startRequest(ctx.subprocess, project, 'runs.execute', {run_id: item.run_id}, {allowMutations: true})
                    const monitor = async () => {
                      if (!running) return
                      try {
                        const events = await call(project, 'runs.events', {run_id: item.run_id, offset: cursor, limit: 50}, {signal: monitorAbort.signal})
                        cursor = events.next_offset
                        for (const event of events.items) job.append(JSON.stringify(event) + '\n', {channel: 'stdout'})
                        const state = await call(project, 'runs.inspect', {run_id: item.run_id}, {signal: monitorAbort.signal})
                        const stage = Object.entries(state.state.stages ?? {}).find(([, value]) => value.status === 'running')
                        const progress = stage ? ` · ${stage[0]} ${stage[1].done ?? 0}/${stage[1].total ?? '?'}` : ''
                        job.updateProgress(`${state.state.status}${progress} · ${item.project} / ${item.run_id}`)
                      } catch {
                        job.append('monitor temporarily unavailable; durable core journals remain authoritative\n', {channel: 'log'})
                      } finally {
                        if (running) monitorTimer = setTimeout(() => {monitorWork = monitor()}, config.monitorIntervalMs)
                      }
                    }
                    monitorWork = monitor()
                    const result = await process.done
                    const status = result.state.status === 'completed' ? 'completed'
                      : result.state.status === 'cancelled' ? 'killed' : 'failed'
                    return {status, detail: result.state.status, result: JSON.stringify(result)}
                  })
                } catch (error) {
                  return {status: cancelled ? 'killed' : 'failed', detail: cancelled ? 'cancelled' :
                    (['dispatch_dependency_failed', 'workflow_already_scheduled'].includes(error.message) ? error.message : 'workflow_execution_failed')}
                } finally {
                  running = false
                  clearTimeout(monitorTimer)
                  clearTimeout(stopTimer)
                  monitorAbort.abort()
                  await monitorWork
                  await cancelWork
                }
              })().then(result => {gates.get(item.id).resolve(result); return result})
              return {cancel, done}
            }})
          handles.push({id: item.id, project: item.project, run_id: item.run_id, job_id: jobId})
        }
      } catch (error) {
        // Failed registration must not leave earlier published work orphaned.
        for (const handle of handles) ctx.jobs.kill(handle.job_id, exec.agent.id, 'dispatch registration failed')
        for (const gate of gates.values()) gate.resolve({status: 'failed'})
        throw error
      }
      return {kind: 'background', jobs: handles, max_active_workflows: config.maxActiveWorkflows}
    },
  }))
}
