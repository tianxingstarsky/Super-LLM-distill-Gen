import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdtemp, writeFile, readFile, stat, rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {createHash} from 'node:crypto'
import {zstdCompressSync} from 'node:zlib'
import {DatabaseSync} from 'node:sqlite'
import {importConversation} from '../import-conversations.mjs'

const time = 1792771200000
const hash = bytes => createHash('sha256').update(bytes).digest('hex')
const text = value => ({type: 'text', text: value})
const model = {kind: 'model', provider: 'offline', model: 'fixture'}

// Logical v4 event shapes from the official persistence contract. The test
// exercises the actual released reader rather than mocking decoder behavior.
export function nativeLog({reasoning = false, tool = false, seeded = false, replacement = false, developer = false,
  corruptTool = false, incomplete = false, version = 4} = {}) {
  const header = {type: 'session', version, id: 'fixture-child', createdAt: time,
    isSeeded: seeded, delegationDepth: 0, ...(seeded ? {parentSession: 'fixture-parent'} : {})}
  const events = []
  const add = (type, data, extra = {}) => events.push({type, seq: events.length, time, data, ...extra})
  if (seeded) {
    add('turn/start', {turn: 0})
    add('step/start', {turn: 0, step: 0})
    add('system/message', {turn: 0, step: 0, message: {id: 'parent-system', role: 'system',
      source: {kind: 'system-prompt'}, content: [text('Remember the conversation.')]}}, {surfaceOp: 'append'})
    add('user/message', {id: 'parent-user', role: 'user', source: {kind: 'user'}, content: [text('Remember the number two.')]}, {surfaceOp: 'append'})
    add('assistant/message', {turn: 0, step: 0, stream: [], message: {id: 'parent-assistant', role: 'assistant',
      source: model, content: [text('The number is two.')]}}, {surfaceOp: 'append'})
    add('step/end', {turn: 0, step: 0})
    add('turn/end', {turn: 0, reason: {kind: 'completed'}})
    add('session/end-seed', {inherited: true})
  }
  add('turn/start', {turn: 1})
  add('step/start', {turn: 1, step: 1})
  add('system/message', {turn: 1, step: 1, message: {id: 'sys', role: 'system', source: {kind: 'system-prompt'},
    content: [text('Answer the actual user request.')] }}, seeded
      ? {surfaceOp: {op: 'replace', startSeq: 2, endSeq: 2}, sourceEventSeqs: [2]} : {surfaceOp: 'append'})
  add('user/message', {id: 'u1', role: 'user', source: {kind: 'user'},
    content: [text('What is two plus two?')]}, {surfaceOp: 'append'})
  if (developer) add('developer/message', {turn: 1, step: 1, message: {id: 'd1', role: 'developer',
    source: {kind: 'fixture'}, content: [text('Explain arithmetic clearly.')]}}, {surfaceOp: 'append'})
  if (tool) {
    add('request/header', {reason: 'initial', header: {config: {provider: 'offline', model: 'fixture'},
      tools: [{name: 'calculate', description: 'Add two numbers.', parameters: {type: 'object', properties: {a: {type: 'number'}, b: {type: 'number'}}}}]}})
    add('assistant/message', {turn: 1, step: 1, stream: [], message: {id: 'a-tool', role: 'assistant', source: model,
      content: [{type: 'tool-call', id: 'call-1', name: 'calculate', arguments: '{"a":2,"b":2}'}]}}, {surfaceOp: 'append'})
    add('tool/call', {turn: 1, step: 1, callId: 'call-1', name: 'calculate', arguments: '{"a":2,"b":2}'})
    add('tool/result', {turn: 1, step: 1, message: {id: 't1', role: 'tool', source: {kind: 'tool', callId: 'call-1'},
      toolCallId: corruptTool ? 'foreign' : 'call-1', content: [text('4')], isError: false}},
      {surfaceOp: 'append', sourceEventSeqs: [events.length - 1]})
    add('step/end', {turn: 1, step: 1})
    add('step/start', {turn: 1, step: 2})
  }
  add('assistant/message', {turn: 1, step: tool ? 2 : 1, stream: [], message: {id: 'a1', role: 'assistant', source: model,
    content: [...reasoning ? [{type: 'reasoning', text: '  Two added to two equals four.\n'}] : [], text('Four.')] }}, {surfaceOp: 'append'})
  if (replacement) {
    const systemSeq = seeded ? 10 : 2
    add('system/message', {turn: 1, step: tool ? 2 : 1, message: {id: 'sys2', role: 'system', source: {kind: 'system-prompt'},
      content: [text('Updated system context.')] }}, {surfaceOp: {op: 'replace', startSeq: systemSeq, endSeq: systemSeq}, sourceEventSeqs: [systemSeq]})
  }
  add('step/end', {turn: 1, step: tool ? 2 : 1})
  if (!incomplete) add('turn/end', {turn: 1, reason: {kind: 'completed'}})
  if (seeded) {
    for (const event of events) {
      if (Object.hasOwn(event.data, 'turn')) event.data.turn++
      if (event.data.step === 0) event.data.step = 1
    }
  }
  return [header, ...events].map(row => JSON.stringify(row)).join('\n') + '\n'
}

async function run(t, bytes) {
  const directory = await mkdtemp(join(tmpdir(), 'dataforge-dsh-import-'))
  t.after(() => rm(directory, {recursive: true, force: true}))
  const paths = {input: join(directory, 'source.jsonl'), output: join(directory, 'out.jsonl'), report: join(directory, 'report.json')}
  await writeFile(paths.input, bytes)
  const before = await readFile(paths.input)
  const report = await importConversation(paths)
  assert.deepEqual(await readFile(paths.input), before)
  assert.deepEqual(JSON.parse(await readFile(paths.report, 'utf8')), report)
  const record = report.status === 'ready' ? JSON.parse(await readFile(paths.output, 'utf8')) : null
  if (!record) await assert.rejects(stat(paths.output), {code: 'ENOENT'})
  return {report, record, paths}
}

test('actual official reader imports native no-reasoning conversation without inventing a chain', async t => {
  const {report, record, paths} = await run(t, nativeLog())
  assert.equal(report.status, 'ready', JSON.stringify(report))
  assert.deepEqual(record.messages.map(message => message.role), ['system', 'user', 'assistant'])
  assert.equal(report.reasoning_messages, 0)
  assert.ok(record.messages.every(message => !Object.hasOwn(message, 'reasoning_content')))
  assert.equal(report.output_sha256, hash(await readFile(paths.output)))
  assert.ok(report.warnings.includes('source_has_no_reasoning'))
})

test('Zstandard source retains exact reasoning bytes and tool call identity', async t => {
  const {report, record} = await run(t, zstdCompressSync(nativeLog({tool: true, reasoning: true})))
  assert.equal(report.status, 'ready', JSON.stringify(report))
  assert.equal(report.tool_calls, 1); assert.equal(report.tool_results, 1)
  assert.equal(record.messages[2].tool_calls[0].id, record.messages[3].tool_call_id)
  assert.equal(record.messages[2].tool_calls[0].function.arguments, '{"a":2,"b":2}')
  assert.equal(record.messages.at(-1).reasoning_content, '  Two added to two equals four.\n')
  assert.equal(record.tools[0].function.name, 'calculate')
})

test('native concatenated Zstandard header and append frames decode completely', async t => {
  const rows = nativeLog({reasoning: true}).trimEnd().split('\n')
  const bytes = Buffer.concat([zstdCompressSync(rows[0] + '\n'),
    zstdCompressSync(rows.slice(1, 4).join('\n') + '\n'), zstdCompressSync(rows.slice(4).join('\n') + '\n')])
  const {report, record} = await run(t, bytes)
  assert.equal(report.status, 'ready', JSON.stringify(report))
  assert.equal(record.messages.at(-1).content, 'Four.')
  assert.equal(report.reasoning_messages, 1)
})

test('empty reasoning block stays empty and does not count as a reasoning chain', async t => {
  const log = nativeLog({reasoning: true}).replace('  Two added to two equals four.\\n', '')
  const {report, record} = await run(t, log)
  assert.equal(report.status, 'ready', JSON.stringify(report))
  assert.equal(record.messages.at(-1).reasoning_content, '')
  assert.equal(report.reasoning_messages, 0)
})

test('fork metadata and source prefix survive independently of parent lookup', async t => {
  const {report, record} = await run(t, nativeLog({seeded: true}))
  assert.equal(report.status, 'ready', JSON.stringify(report))
  assert.equal(record.import_metadata.parent_session, 'fixture-parent')
  assert.equal(record.import_metadata.inherited_event_count, 7)
  assert.equal(record.import_metadata.is_seeded, true)
  assert.equal(record.messages[1].content, 'Remember the number two.')
  assert.equal(record.messages[2].content, 'The number is two.')
})

test('developer and user roles remain distinct without fake reasoning', async t => {
  const {report, record} = await run(t, nativeLog({developer: true}))
  assert.equal(report.status, 'ready', JSON.stringify(report))
  assert.equal(record.messages[2].role, 'developer')
  assert.equal(record.messages[2].content, 'Explain arithmetic clearly.')
  assert.equal(report.reasoning_messages, 0)
  assert.ok(report.warnings.includes('developer_role_preserved'))
})

test('surface replacement is audited without deleting original human-visible context', async t => {
  const {report, record} = await run(t, nativeLog({replacement: true}))
  assert.equal(report.status, 'ready', JSON.stringify(report))
  assert.equal(record.messages[0].content, 'Answer the actual user request.')
  assert.equal(record.import_metadata.surface_replacements[0].message.content[0].text, 'Updated system context.')
})

test('unknown future format, mismatched tools and incomplete turns fail closed', async t => {
  for (const options of [{version: 99}, {corruptTool: true, tool: true}, {incomplete: true}]) {
    const {report} = await run(t, nativeLog(options))
    assert.equal(report.status, 'rejected')
  }
})

test('malformed JSON and unsupported image references are not silently dropped', async t => {
  const malformed = nativeLog() + '{"secret":"not printed"'
  assert.equal((await run(t, malformed)).report.status, 'rejected')
  const log = nativeLog().replace('"type":"text","text":"What is two plus two?"',
    '"type":"image","attachment":{"id":"missing-image"}')
  assert.equal((await run(t, log)).report.status, 'rejected')
})

test('DSH SQLite search index is recognized read-only and cannot masquerade as conversation', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'dataforge-dsh-index-'))
  t.after(() => rm(directory, {recursive: true, force: true}))
  const input = join(directory, 'session-search.sqlite')
  const db = new DatabaseSync(input)
  db.exec('PRAGMA application_id=1146308689; PRAGMA user_version=8; CREATE TABLE persisted_sessions(id TEXT);')
  db.close()
  const before = await readFile(input)
  const report = await importConversation({input, output: join(directory, 'out.jsonl'), report: join(directory, 'report.json')})
  assert.equal(report.error.code, 'dsh_search_index_not_conversation')
  assert.deepEqual(await readFile(input), before)
})

test('existing output is never overwritten', async t => {
  const {paths} = await run(t, nativeLog())
  const before = await readFile(paths.output)
  const result = await importConversation({...paths, report: paths.report + '.new'})
  assert.equal(result.error.code, 'dsh_import_destination_exists')
  assert.deepEqual(await readFile(paths.output), before)
})
