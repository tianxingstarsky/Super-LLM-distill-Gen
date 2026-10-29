import test from 'node:test'
import assert from 'node:assert/strict'
import {apply, configuration} from '../index.mjs'

const local = {id: 'local', root: process.cwd(), python: process.execPath}
const config = {projects: [local], allowMutations: true, maxActiveWorkflows: 1, monitorIntervalMs: 500}
const exec = () => ({signal: new AbortController().signal, agent: {id: 'owner-1'}, callId: 'call-1', rootCallId: 'call-1'})
const tick = () => new Promise(resolve => setImmediate(resolve))

function harness({failed = new Set(), delayed = false, rejectJobAt = 0} = {}) {
  const tools = new Map(), jobs = [], calls = [], releases = new Map(), kills = []
  let active = 0, peak = 0
  const ctx = {skills: {registerProvider() {return () => {}}}, tools: {register(tool) {tools.set(tool.name, tool)}},
    subprocess: {spawn(spec) {
      const input = JSON.parse(spec.stdio.stdin.data), {action, params} = input
      calls.push(input)
      let done
      const data = action === 'runs.events' ? {items: [{kind: 'progress'}], next_offset: 1}
        : action === 'runs.inspect' ? {state: {status: 'running'}} : {state: {status: failed.has(params.run_id) ? 'needs_attention' : 'completed'}}
      if (action === 'runs.execute') {
        active++; peak = Math.max(peak, active)
        done = delayed ? new Promise(resolve => releases.set(params.run_id, resolve)) : Promise.resolve()
        done = done.then(() => {active--; return {exitCode: 0}})
      } else done = Promise.resolve({exitCode: 0})
      const value = JSON.stringify({protocol: input.protocol, version: input.version, request_id: input.request_id, ok: true, data})
      return {done, collected: {stdout: {readFrom: () => ({text: value, lossy: false})}},
        terminate() {if (action === 'runs.execute') releases.get(params.run_id)?.()}, async waitForExit() {return true}}
    }}, jobs: {start(spec) {
      if (rejectJobAt && jobs.length + 1 === rejectJobAt) throw new Error('controller admission rejected')
      const id = `dataforge-${jobs.length}`, progress = [], output = []
      const hooks = spec.run({id, append: text => output.push(text), updateProgress: text => progress.push(text)})
      jobs.push({id, spec, hooks, progress, output})
      return id
    }, kill(id, caller, reason) {
      kills.push({id, caller, reason})
      const job = jobs.find(x => x.id === id)
      assert.equal(caller, job.spec.owner)
      job.hooks.cancel()
    }}}
  apply(ctx, config)
  return {tools, jobs, calls, releases, kills, peak: () => peak}
}

test('current official defineTool registers validated tools without old CLI/gate coupling', async () => {
  const h = harness()
  assert.deepEqual([...h.tools.keys()], ['dataforge', 'dataforge_dispatch'])
  const tool = h.tools.get('dataforge')
  await assert.rejects(tool.execute({project: 'local', action: 'gate.approve'}, exec()))
  await assert.rejects(tool.execute({project: 'local', action: 'runs.create'}, exec()), /stable_request_id/)
  await assert.rejects(tool.execute({project: 'local', action: 'models.limit', params: {max_concurrency: 99}}, exec()), /limit_changes_disabled/)
  assert.equal(h.calls.length, 0)
  assert.throws(() => configuration({...config, projects: [local, local]}))
})

test('published jobs own their lifecycle, dependencies and shared queue are real, progress is observed', async () => {
  const h = harness({delayed: true})
  const outerAbort = new AbortController()
  const invocation = {...exec(), signal: outerAbort.signal}
  const result = await h.tools.get('dataforge_dispatch').execute({runs: [
    {id: 'dependent', project: 'local', run_id: 'b'.repeat(32), depends_on: ['first']},
    {id: 'first', project: 'local', run_id: 'a'.repeat(32)},
  ]}, invocation)
  outerAbort.abort() // an outer call ending cannot kill published jobs
  await tick()
  assert.equal(result.kind, 'background')
  assert.equal(h.calls.filter(x => x.action === 'runs.execute').length, 1)
  assert.ok(h.calls.some(x => x.action === 'runs.events'))
  assert.equal(h.jobs[0].spec.owner, 'owner-1')
  h.releases.get('a'.repeat(32))()
  await tick(); await tick()
  h.releases.get('b'.repeat(32))()
  const outcomes = await Promise.all(h.jobs.map(x => x.hooks.done))
  assert.ok(outcomes.every(x => x.status === 'completed'))
  assert.equal(h.peak(), 1)
  assert.ok(h.jobs.some(x => x.output.length))
})

test('failed dependencies never execute and malformed DAG starts no jobs', async () => {
  const h = harness({failed: new Set(['a'.repeat(32)])}), tool = h.tools.get('dataforge_dispatch')
  const a = {id: 'a', project: 'local', run_id: 'a'.repeat(32)}
  const b = {id: 'b', project: 'local', run_id: 'b'.repeat(32), depends_on: ['a']}
  await assert.rejects(tool.execute({runs: [{...a, depends_on: ['b']}, b]}, exec()), /dependencies/)
  assert.equal(h.jobs.length, 0)
  await tool.execute({runs: [b, a]}, exec())
  const outcomes = await Promise.all(h.jobs.map(x => x.hooks.done))
  assert.equal(outcomes[0].detail, 'dispatch_dependency_failed')
  assert.deepEqual(h.calls.filter(x => x.action === 'runs.execute').map(x => x.params.run_id), [a.run_id])
})

test('partial job-registration failure cancels previously published owned jobs', async () => {
  const h = harness({rejectJobAt: 2, delayed: true})
  await assert.rejects(h.tools.get('dataforge_dispatch').execute({runs: [
    {id: 'a', project: 'local', run_id: 'a'.repeat(32)},
    {id: 'b', project: 'local', run_id: 'b'.repeat(32)},
  ]}, exec()), /admission rejected/)
  assert.equal(h.kills.length, 1)
  assert.equal(h.kills[0].caller, 'owner-1')
  const outcome = await h.jobs[0].hooks.done
  assert.equal(outcome.status, 'killed')
})

test('cancelling queued work does not start it or stop its independent sibling', async () => {
  const h = harness({delayed: true})
  await h.tools.get('dataforge_dispatch').execute({runs: [
    {id: 'a', project: 'local', run_id: 'a'.repeat(32)},
    {id: 'b', project: 'local', run_id: 'b'.repeat(32)},
  ]}, exec())
  await tick()
  h.jobs[1].hooks.cancel()
  assert.equal((await h.jobs[1].hooks.done).status, 'killed')
  assert.equal(h.calls.filter(x => x.action === 'runs.execute').length, 1)
  h.releases.get('a'.repeat(32))()
  assert.equal((await h.jobs[0].hooks.done).status, 'completed')
})

test('pre-aborted invocations publish no jobs', async () => {
  const h = harness()
  await assert.rejects(h.tools.get('dataforge_dispatch').execute({runs: [
    {id: 'a', project: 'local', run_id: 'a'.repeat(32)},
  ]}, {...exec(), signal: AbortSignal.abort()}), /cancelled/)
  assert.equal(h.jobs.length, 0)
})
