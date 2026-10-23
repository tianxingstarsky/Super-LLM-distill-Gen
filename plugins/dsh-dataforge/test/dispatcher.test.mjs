import test from 'node:test'
import assert from 'node:assert/strict'
import {Dispatcher, validatePlan} from '../dispatcher.mjs'

const tick = () => new Promise(resolve => setImmediate(resolve))
const signal = () => new AbortController().signal

test('global admission is bounded, priorities precede FIFO ties, and duplicate active workflows fail', async () => {
  const dispatcher = new Dispatcher(1), order = []
  let release
  const first = dispatcher.schedule('first', 0, signal(), () => new Promise(done => {release = done}))
  await tick()
  await assert.rejects(dispatcher.schedule('first', 100, signal(), async () => {}), /already_scheduled/)
  const later = dispatcher.schedule('later', 0, signal(), async () => {order.push('later')})
  const higher = dispatcher.schedule('higher', 9, signal(), async () => {order.push('higher')})
  const tie = dispatcher.schedule('tie', 9, signal(), async () => {order.push('tie')})
  assert.equal(dispatcher.active, 1)
  release('done')
  await Promise.all([first, later, higher, tie])
  await tick()
  assert.deepEqual(order, ['higher', 'tie', 'later'])
  assert.equal(dispatcher.active, 0)
  assert.equal(dispatcher.keys.size, 0)
})

test('cancelled queued work never starts and failure releases admission', async () => {
  const dispatcher = new Dispatcher(1)
  let release, ran = false
  const first = dispatcher.schedule('first', 0, signal(), () => new Promise(done => {release = done}))
  await tick()
  const abort = new AbortController()
  const queued = dispatcher.schedule('cancelled', 0, abort.signal, async () => {ran = true})
  const rejected = assert.rejects(queued, /cancelled/)
  abort.abort()
  await rejected
  release()
  await first
  await assert.rejects(dispatcher.schedule('error', 0, signal(), async () => {throw new Error('failure')}))
  await dispatcher.schedule('after', 0, signal(), async () => {})
  assert.equal(ran, false)
})

test('complete dependency graph is validated before anything can run', () => {
  const projects = new Map([['local', {}]])
  const a = {id: 'a', project: 'local', run_id: 'a'.repeat(32)}
  const b = {id: 'b', project: 'local', run_id: 'b'.repeat(32), depends_on: ['a']}
  assert.equal(validatePlan([b, a], projects).length, 2)
  for (const plan of [[a, a], [{...a, project: 'unknown'}], [a, {...b, run_id: a.run_id}],
    [a, {...b, depends_on: ['missing']}], [{...a, depends_on: ['b']}, b], [{...a, priority: 1.1}]]) {
    assert.throws(() => validatePlan(plan, projects))
  }
})
