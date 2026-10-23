#!/usr/bin/env node
/** Read-only DSH session import. Untrusted records are never executed. */
import {createHash} from 'node:crypto'
import {readFile, writeFile, stat, unlink} from 'node:fs/promises'
import {resolve} from 'node:path'
import {pathToFileURL} from 'node:url'
import {zstdDecompressSync} from 'node:zlib'

export const IMPORTER = Object.freeze({id: 'deepseek-harness', version: '0.3.0'})
export const LIMITS = Object.freeze({inputBytes: 50 * 1024 * 1024, decodedBytes: 128 * 1024 * 1024,
  outputBytes: 50 * 1024 * 1024, lineBytes: 8 * 1024 * 1024, events: 100000, messages: 20000})
const UPSTREAM = '22ddee3792402c3e70ec5f86a63c3e3e895a2f60'
const SDK_VERSION = '0.2.1-rc.2'
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex')

const MESSAGES = {
  dsh_import_arguments_invalid: 'Choose one source session, a new JSONL destination, and a new report destination.',
  dsh_import_source_too_large: 'This session exceeds the local import size limit. Export a smaller complete session from DSH.',
  dsh_import_invalid_encoding: 'The session is not a complete UTF-8 or Zstandard session log. Export the original log again.',
  dsh_import_dependencies_missing: 'Install the independent DSH adapter dependencies with npm install --ignore-scripts in its package directory.',
  dsh_import_sdk_incompatible: 'This importer requires its pinned DSH SDK. Update the independent adapter for this DSH format.',
  dsh_search_index_not_conversation: 'This DSH SQLite file is a disposable search index, not a complete conversation. Select the original session.v4.jsonl.zstd or session JSONL log from DSH storage.',
  dsh_database_schema_unsupported: 'This database is not a supported DSH conversation store. Select an original DSH session JSONL or JSONL.ZSTD log.',
  dsh_session_format_unsupported: 'The DSH session format is newer than this adapter. Update the independent adapter; the product does not need a schema change.',
  dsh_session_invalid: 'The official DSH reader refused the session. Export a complete committed session log; no damaged rows were skipped.',
  dsh_external_assets_required: 'This session refers to image or file attachments outside its log. A self-contained attachment import adapter is required; no attachment was silently removed.',
  dsh_content_block_unsupported: 'The session contains an unsupported content block. Update the independent adapter before importing it.',
  dsh_tool_schema_conflict: 'The same tool changes its definition during this session. Export a complete task using one consistent tool schema.',
  dsh_tool_sequence_invalid: 'A tool result is missing, duplicated, or has no matching call in this conversation. Export a completed session.',
  dsh_session_incomplete: 'The session has no complete user and assistant conversation, or its final task is still open. Complete or export it in DSH first.',
  dsh_import_destination_exists: 'An import destination already exists. Choose new output and report files.',
  dsh_import_io_failed: 'The session could not be read or its new import files could not be written.',
}

export class ImportRefusal extends Error {
  constructor(code) { super(MESSAGES[code] ?? MESSAGES.dsh_session_invalid); this.code = code }
}
const refuse = code => { throw new ImportRefusal(code) }
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)

async function loadReader() {
  try {
    const [{sessionFormatCatalog, createSessionFormatCatalogWithChildren}, surface, {default: version}] = await Promise.all([
      import('@deepseek-ai/dsh-session-format-catalog'),
      import('@deepseek-ai/dsh-session/surface'),
      import('@deepseek-ai/dsh-session-format-catalog/package.json', {with: {type: 'json'}}),
    ])
    if (version.version !== SDK_VERSION || sessionFormatCatalog.currentVersion !== 4) refuse('dsh_import_sdk_incompatible')
    // Official isolated-transcript restoration uses explicit empty child facts.
    // A session import never infers a missing child from another conversation.
    return {catalog: createSessionFormatCatalogWithChildren([]), surface}
  } catch (error) {
    if (error instanceof ImportRefusal) throw error
    if (error.code === 'ERR_MODULE_NOT_FOUND') refuse('dsh_import_dependencies_missing')
    refuse('dsh_import_sdk_incompatible')
  }
}

async function decode(bytes) {
  const compressed = bytes.length >= 4 && bytes.readUInt32LE(0) === 0xfd2fb528
  if (!compressed) return bytes
  const chunks = []
  let size = 0, offset = 0
  try {
    // Node's public decoder stops after one frame. DSH appends independent
    // checksummed frames, so consume each one using the decoder's actual input
    // byte count. Never scan raw magic bytes or guess frame boundaries.
    while (offset < bytes.length) {
      if (bytes.length - offset < 4 || bytes.readUInt32LE(offset) !== 0xfd2fb528) refuse('dsh_import_invalid_encoding')
      const remaining = LIMITS.decodedBytes - size
      if (remaining <= 0) refuse('dsh_import_source_too_large')
      const {buffer, engine} = zstdDecompressSync(bytes.subarray(offset), {info: true, maxOutputLength: remaining})
      const consumed = engine.bytesWritten
      if (!Number.isSafeInteger(consumed) || consumed <= 0 || consumed > bytes.length - offset) refuse('dsh_import_invalid_encoding')
      offset += consumed; size += buffer.length
      chunks.push(buffer)
    }
    return Buffer.concat(chunks, size)
  } catch (error) {
    if (error instanceof ImportRefusal) throw error
    if (error.code === 'ERR_BUFFER_TOO_LARGE') refuse('dsh_import_source_too_large')
    refuse('dsh_import_invalid_encoding')
  }
}

async function refuseDatabase(input) {
  let db
  try {
    const {DatabaseSync} = await import('node:sqlite')
    db = new DatabaseSync(input, {readOnly: true, allowExtension: false})
    db.exec('PRAGMA trusted_schema=OFF; PRAGMA query_only=ON;')
    const {application_id: id} = db.prepare('PRAGMA application_id').get()
    if (id === 0x44534851) refuse('dsh_search_index_not_conversation')
  } catch (error) {
    if (error instanceof ImportRefusal) throw error
  } finally { db?.close() }
  refuse('dsh_database_schema_unsupported')
}

function restoreLog(text, catalog) {
  let restore, sourceVersion, rows = 0
  try {
    for (const line of text.split(/\r?\n/)) {
      if (!line.trim()) continue
      if (Buffer.byteLength(line) > LIMITS.lineBytes) refuse('dsh_import_source_too_large')
      const record = JSON.parse(line)
      if (!restore) {
        if (!object(record) || record.type !== 'session' || !Number.isSafeInteger(record.version)) refuse('dsh_session_invalid')
        sourceVersion = record.version
        if (sourceVersion > catalog.currentVersion) refuse('dsh_session_format_unsupported')
        restore = catalog.createRestore(record, {recovery: 'strict', validation: 'current'})
      } else {
        if (++rows > LIMITS.events) refuse('dsh_import_source_too_large')
        restore.decodeRow(record)
      }
    }
    if (!restore) refuse('dsh_session_invalid')
    const artifact = restore.finish()
    if (artifact.events.length > LIMITS.events) refuse('dsh_import_source_too_large')
    return {artifact, sourceVersion}
  } catch (error) {
    if (error instanceof ImportRefusal) throw error
    refuse('dsh_session_invalid')
  }
}

function convertMessage(message) {
  if (!object(message) || !['system', 'developer', 'user', 'assistant', 'tool'].includes(message.role)
      || !Array.isArray(message.content)) refuse('dsh_session_invalid')
  const texts = [], reasoning = [], calls = [], updates = []
  for (const block of message.content) {
    if (!object(block)) refuse('dsh_session_invalid')
    switch (block.type) {
      case 'text':
        if (typeof block.text !== 'string') refuse('dsh_session_invalid')
        texts.push(block.text)
        break
      case 'reasoning':
        if (message.role !== 'assistant' || typeof block.text !== 'string') refuse('dsh_content_block_unsupported')
        reasoning.push(block.text)
        break
      case 'tool-call':
        if (message.role !== 'assistant' || typeof block.id !== 'string' || !block.id
            || typeof block.name !== 'string' || !block.name || typeof block.arguments !== 'string') refuse('dsh_session_invalid')
        calls.push({id: block.id, type: 'function', function: {name: block.name, arguments: block.arguments}})
        break
      case 'tool-addition':
      case 'tool-removal':
        if (message.role !== 'developer') refuse('dsh_session_invalid')
        updates.push(block)
        break
      case 'image': case 'file': refuse('dsh_external_assets_required'); break
      default: refuse('dsh_content_block_unsupported')
    }
  }
  // Tool changes stay a developer instruction, never a fabricated user utterance.
  if (updates.length) texts.push(JSON.stringify({tool_updates: updates}))
  const converted = {role: message.role, content: texts.join('')}
  if (reasoning.length) converted.reasoning_content = reasoning.join('')
  if (calls.length) converted.tool_calls = calls
  if (message.role === 'tool') {
    converted.tool_call_id = message.toolCallId
    if (message.isError !== undefined) converted.is_error = message.isError
  }
  return converted
}

export function projectArtifact(artifact, sourceVersion, surface, sourceHash) {
  const messages = [], provenance = [], replacements = [], toolHeaders = [], emptyEvents = [], tools = new Map()
  const pending = new Set(), seen = new Set()
  let calls = 0, results = 0, turnOpen = false
  for (const event of artifact.events) {
    if (event.type === 'turn/start') turnOpen = true
    if (event.type === 'turn/end') turnOpen = false
    if (event.type === 'request/header') {
      const definitions = event.data.header.tools ?? []
      toolHeaders.push({event_seq: event.seq, tools: definitions})
      for (const definition of definitions) {
        if (!object(definition) || typeof definition.name !== 'string') refuse('dsh_session_invalid')
        const existing = tools.get(definition.name)
        if (existing && JSON.stringify(existing) !== JSON.stringify(definition)) refuse('dsh_tool_schema_conflict')
        tools.set(definition.name, definition)
      }
    }
    if (surface.isReplacementSurfaceEvent(event)) {
      // The durable human transcript is append-origin, as specified by DSH.
      // Keep replacements in audit metadata, never erase earlier visible turns.
      replacements.push({event_seq: event.seq, surface_op: event.surfaceOp,
        source_event_seqs: event.sourceEventSeqs, message: surface.deriveEventMessage(event)})
      continue
    }
    if (!surface.isAppendSurfaceEvent(event)) continue
    const original = surface.deriveEventMessage(event)
    if (!original) {
      // The official projection returns null for empty system/developer/assistant
      // settlement messages. Retain that event fact instead of inventing text.
      emptyEvents.push({event_seq: event.seq, type: event.type, data: event.data})
      continue
    }
    const message = convertMessage(original)
    if (message.role === 'tool') {
      if (!pending.delete(message.tool_call_id)) refuse('dsh_tool_sequence_invalid')
      results++
    } else if (pending.size) refuse('dsh_tool_sequence_invalid')
    for (const call of message.tool_calls ?? []) {
      if (seen.has(call.id)) refuse('dsh_tool_sequence_invalid')
      seen.add(call.id); pending.add(call.id); calls++
    }
    messages.push(message)
    provenance.push({event_seq: event.seq, message_id: original.id, source: original.source,
      content_blocks: original.content})
    if (messages.length > LIMITS.messages) refuse('dsh_import_source_too_large')
  }
  if (pending.size) refuse('dsh_tool_sequence_invalid')
  if (turnOpen || !messages.some(message => message.role === 'user')
      || messages.at(-1)?.role !== 'assistant' || !messages.at(-1).content.trim()) refuse('dsh_session_incomplete')
  const header = artifact.header
  const record = {id: `dsh-${sha256(`${header.id}\n${sourceHash}`).slice(0, 24)}`, messages,
    tools: [...tools.values()].map(tool => ({type: 'function', function: {
      name: tool.name, description: tool.description, parameters: tool.parameters,
    }})),
    import_metadata: {source: 'deepseek-harness', session_id: header.id,
      format_version: sourceVersion, restored_format_version: header.version,
      is_seeded: header.isSeeded, inherited_event_count: artifact.inheritedEventCount,
      ...(header.parentSession !== undefined ? {parent_session: header.parentSession} : {}),
      ...(header.origin !== undefined ? {origin: header.origin} : {}),
      delegation_depth: header.delegationDepth ?? 0, source_sha256: sourceHash,
      projection: 'append_origin_transcript', message_provenance: provenance,
      surface_replacements: replacements, empty_message_events: emptyEvents, tool_headers: toolHeaders}}
  const counts = {records: 1, messages: messages.length,
    assistant_messages: messages.filter(message => message.role === 'assistant').length,
    reasoning_messages: messages.filter(message => Boolean(message.reasoning_content)).length,
    tool_calls: calls, tool_results: results}
  const warnings = []
  if (counts.reasoning_messages === 0) warnings.push('source_has_no_reasoning')
  if (replacements.length) warnings.push('surface_replacements_preserved_in_audit')
  if (header.isSeeded) warnings.push('inherited_prefix_preserved')
  if (messages.some(message => message.role === 'developer')) warnings.push('developer_role_preserved')
  if (messages.some(message => message.is_error === true)) warnings.push('recorded_tool_errors_require_review')
  if (messages.some(message => (message.tool_calls ?? []).some(call => !tools.has(call.function.name)))) warnings.push('source_tool_schemas_absent')
  if (emptyEvents.length) warnings.push('empty_settlement_events_preserved_in_audit')
  return {record, counts, warnings}
}

export async function importConversation({input, output, report}) {
  const destinations = [input, output, report].map(value => typeof value === 'string' ? resolve(value) : '')
  if (destinations.some(value => !value) || new Set(destinations).size !== 3) refuse('dsh_import_arguments_invalid')
  let createdOutput = false, sourceHash
  const base = {protocol: 'dataforge.conversation_import', version: 1, importer: IMPORTER}
  try {
    for (const destination of [output, report]) {
      try { await stat(destination); refuse('dsh_import_destination_exists') }
      catch (error) { if (error.code !== 'ENOENT') throw error }
    }
    const info = await stat(input)
    if (!info.isFile()) refuse('dsh_import_arguments_invalid')
    if (info.size > LIMITS.inputBytes) refuse('dsh_import_source_too_large')
    const bytes = await readFile(input)
    if (bytes.length > LIMITS.inputBytes) refuse('dsh_import_source_too_large')
    sourceHash = sha256(bytes)
    if (bytes.subarray(0, 16).toString('ascii') === 'SQLite format 3\0') await refuseDatabase(input)
    const decoded = await decode(bytes)
    if (decoded.length > LIMITS.decodedBytes) refuse('dsh_import_source_too_large')
    let text
    try { text = new TextDecoder('utf-8', {fatal: true}).decode(decoded) }
    catch { refuse('dsh_import_invalid_encoding') }
    const {catalog, surface} = await loadReader()
    const {artifact, sourceVersion} = restoreLog(text, catalog)
    const {record, counts, warnings} = projectArtifact(artifact, sourceVersion, surface, sourceHash)
    const body = Buffer.from(JSON.stringify(record) + '\n')
    if (body.length > LIMITS.outputBytes) refuse('dsh_import_source_too_large')
    const receipt = {...base, status: 'ready', source_sha256: sourceHash, ...counts,
      output_sha256: sha256(body), output_bytes: body.length, warnings,
      source_format_version: sourceVersion, upstream_commit: UPSTREAM, sdk_version: SDK_VERSION}
    await writeFile(output, body, {flag: 'wx', mode: 0o600}); createdOutput = true
    await writeFile(report, JSON.stringify(receipt, null, 2) + '\n', {flag: 'wx', mode: 0o600})
    return receipt
  } catch (error) {
    if (createdOutput) await unlink(output).catch(() => {})
    const code = error instanceof ImportRefusal ? error.code : 'dsh_import_io_failed'
    const receipt = {...base, status: 'rejected', ...(sourceHash ? {source_sha256: sourceHash} : {}),
      records: 0, warnings: [], error: {code, message: MESSAGES[code]}}
    await writeFile(report, JSON.stringify(receipt, null, 2) + '\n', {flag: 'wx', mode: 0o600}).catch(() => {})
    return receipt
  }
}

async function main() {
  const args = process.argv.slice(2), options = {}
  if (args.length !== 6) refuse('dsh_import_arguments_invalid')
  for (let index = 0; index < args.length; index += 2) {
    const key = args[index].replace(/^--/, '')
    if (!['input', 'output', 'report'].includes(key) || Object.hasOwn(options, key)) refuse('dsh_import_arguments_invalid')
    options[key] = args[index + 1]
  }
  const report = await importConversation(options)
  process.stdout.write(JSON.stringify(report) + '\n')
  if (report.status !== 'ready') process.exitCode = 2
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(() => {
    process.stderr.write('dsh_import_arguments_invalid\n'); process.exitCode = 2
  })
}
