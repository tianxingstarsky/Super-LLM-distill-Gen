import assert from 'node:assert/strict'
import { pathToFileURL } from 'node:url'

const root = process.env.DF_ROOT!
const plugin = await import(pathToFileURL(`${root}/plugins/dsh-dataforge/src/dataforge.ts`).href)
let registered: any
plugin.apply({ tools: { register(tool: any) { registered = tool } } })
assert.equal(registered.name, 'dataforge')
const output = await registered.execute({command: 'workspace', options: {ws: 'default', action: 'status'}}, {signal: new AbortController().signal})
assert.equal(output.exitCode, 0)
assert.equal(JSON.parse(output.stdout).workspace, 'default')
await assert.rejects(() => registered.execute({command: 'gate', options: {action: 'approve', gate_id: 'G3'}}, {signal: new AbortController().signal}), /human confirmation/)
console.log('PASS: real plugin dispatch, positional ordering, workspace and human gate')
