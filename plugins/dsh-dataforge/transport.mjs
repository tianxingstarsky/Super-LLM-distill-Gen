/** Only the stable stdin/stdout protocol crosses into the Python product. */
import {randomUUID} from 'node:crypto'

export const PROTOCOL = 'dataforge.control'
export const VERSION = 1
export const MUTATIONS = new Set(['runs.create', 'runs.execute', 'runs.cancel', 'models.limit'])

export function startRequest(subprocess, project, action, params, {signal, allowMutations = false, requestId = randomUUID()} = {}) {
  if (MUTATIONS.has(action) && !allowMutations) throw new Error('adapter_mutations_disabled')
  if (signal?.aborted) throw new Error('adapter_request_cancelled')
  const request = {protocol: PROTOCOL, version: VERSION, request_id: requestId, action, params}
  const input = JSON.stringify(request)
  if (Buffer.byteLength(input) > 1024 * 1024) throw new Error('adapter_request_too_large')
  const argv = [project.python, '-m', 'lib.control', '--root', project.root, '--workspace', project.workspace ?? 'default']
  if (allowMutations) argv.push('--allow-mutations')
  const process = subprocess.spawn({
    argv, cwd: project.root, graceMs: 5000, signal,
    env: {...Object.fromEntries((project.forwardEnv ?? []).map(name => [name, globalThis.process.env[name]])),
      PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1'},
    stdio: {stdin: {data: input}, stdout: {maxBytes: 2 * 1024 * 1024}, stderr: {maxBytes: 8192}},
  })
  const done = (async () => {
    try {
      const outcome = await process.done
      await process.waitForExit()
      const output = process.collected.stdout.readFrom(0)
      if (output.lossy) throw new Error('adapter_response_truncated')
      let reply
      try { reply = JSON.parse(output.text) } catch { throw new Error('adapter_invalid_response') }
      if (reply.protocol !== PROTOCOL || reply.version !== VERSION || reply.request_id !== requestId
          || typeof reply.ok !== 'boolean') throw new Error('adapter_protocol_mismatch')
      if (!reply.ok) {
        const code = reply.error?.code
        throw new Error(typeof code === 'string' && /^[a-z][a-z0-9_]{2,90}$/.test(code) ? code : 'adapter_operation_rejected')
      }
      if (outcome.exitCode !== 0) throw new Error('adapter_process_failed')
      return reply.data
    } finally {
      // A malformed response or failed child still has to release its entire
      // managed process range. The official provider owns Windows/POSIX details.
      try {
        process.terminate()
      } finally {
        if (!await process.waitForExit()) throw new Error('adapter_process_cleanup_unconfirmed')
      }
    }
  })()
  return {done, terminate: () => process.terminate()}
}

export async function request(subprocess, project, action, params, options) {
  return startRequest(subprocess, project, action, params, options).done
}
