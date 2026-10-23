import test from 'node:test'
import assert from 'node:assert/strict'
import {request} from '../transport.mjs'

const project = {root: process.cwd(), python: '/explicit/python', workspace: 'default'}
function provider(change = x => x, outcome = 0, lossy = false) {
  const calls = [], cleanup = []
  return {calls, cleanup, spawn(spec) {
    calls.push(spec)
    const input = JSON.parse(spec.stdio.stdin.data)
    const value = change({protocol: input.protocol, version: input.version, request_id: input.request_id, ok: true, data: {hello: '中文'}})
    return {done: Promise.resolve({exitCode: outcome, signal: null}),
      collected: {stdout: {readFrom: () => ({text: JSON.stringify(value), lossy})}},
      terminate() {cleanup.push('terminate')}, async waitForExit() {cleanup.push('wait'); return true}}
  }}
}

test('protocol uses stdin and argv, scrubs raw stderr, and awaits managed-range cleanup', async () => {
  const process = provider()
  assert.deepEqual(await request(process, project, 'capabilities', {}), {hello: '中文'})
  assert.deepEqual(process.calls[0].argv.slice(0, 3), ['/explicit/python', '-m', 'lib.control'])
  assert.equal(process.calls[0].cwd, project.root)
  assert.equal(process.calls[0].env.PYTHONUTF8, '1')
  assert.equal(process.calls[0].stdio.stdout.maxBytes, 2097152)
  assert.deepEqual(process.cleanup, ['wait', 'terminate', 'wait'])
})

test('wrong version, wrong request identity, truncation and nonzero exits fail closed', async () => {
  for (const process of [provider(x => ({...x, version: 2})), provider(x => ({...x, request_id: 'foreign'})),
    provider(x => x, 0, true), provider(x => x, 2), provider(x => ({...x, ok: false, error: {code: 'secret-key private data'}}))]) {
    await assert.rejects(request(process, project, 'capabilities', {}), error => !error.message.includes('secret-key'))
    assert.ok(process.cleanup.includes('terminate'))
    assert.equal(process.cleanup.at(-1), 'wait')
  }
})

test('read-only and pre-aborted requests do not launch a mutation process', async () => {
  const process = provider(), abort = new AbortController()
  await assert.rejects(request(process, project, 'runs.execute', {}), /mutations_disabled/)
  abort.abort()
  await assert.rejects(request(process, project, 'capabilities', {}, {signal: abort.signal}), /cancelled/)
  assert.equal(process.calls.length, 0)
})

test('only operator-named environment credentials are deliberately forwarded and never rendered', async () => {
  globalThis.process.env.ADAPTER_TEST_PROVIDER_KEY = 'test-only-canary'
  const process = provider()
  try {
    const value = await request(process, {...project, forwardEnv: ['ADAPTER_TEST_PROVIDER_KEY']}, 'capabilities', {})
    assert.equal(process.calls[0].env.ADAPTER_TEST_PROVIDER_KEY, 'test-only-canary')
    assert.equal(process.calls[0].env.PATH, undefined)
    assert.ok(!JSON.stringify(value).includes('canary'))
    assert.ok(!process.calls[0].stdio.stdin.data.includes('canary'))
  } finally {
    delete globalThis.process.env.ADAPTER_TEST_PROVIDER_KEY
  }
})
