/** SDK-independent admission shared by all callers of this plugin instance. */
export class Dispatcher {
  constructor(limit) {
    if (!Number.isInteger(limit) || limit < 1 || limit > 32) throw new Error('invalid_workflow_limit')
    this.limit = limit
    this.active = 0
    this.queue = []
    this.keys = new Set()
    this.sequence = 0
  }

  schedule(key, priority, signal, work) {
    if (signal.aborted) return Promise.reject(new Error('dispatch_cancelled'))
    if (this.keys.has(key)) return Promise.reject(new Error('workflow_already_scheduled'))
    if (this.queue.length >= 128) return Promise.reject(new Error('dispatch_queue_full'))
    this.keys.add(key)
    return new Promise((resolve, reject) => {
      const entry = {key, priority, sequence: this.sequence++, signal, work, resolve, reject}
      entry.abort = () => {
        const index = this.queue.indexOf(entry)
        if (index >= 0) {
          this.queue.splice(index, 1)
          this.keys.delete(key)
          reject(new Error('dispatch_cancelled'))
        }
      }
      signal.addEventListener('abort', entry.abort, {once: true})
      this.queue.push(entry)
      this.pump()
    })
  }

  pump() {
    this.queue.sort((a, b) => b.priority - a.priority || a.sequence - b.sequence)
    while (this.active < this.limit && this.queue.length) {
      const entry = this.queue.shift()
      entry.signal.removeEventListener('abort', entry.abort)
      this.active++
      Promise.resolve().then(() => {
        if (entry.signal.aborted) throw new Error('dispatch_cancelled')
        return entry.work()
      }).then(entry.resolve, entry.reject).finally(() => {
        this.active--
        this.keys.delete(entry.key)
        this.pump()
      })
    }
  }
}

export function validatePlan(runs, projects) {
  if (!Array.isArray(runs) || !runs.length || runs.length > 32) throw new Error('invalid_dispatch_plan')
  const ids = new Set()
  for (const item of runs) {
    if (!item || typeof item.id !== 'string' || !/^[a-zA-Z0-9_-]{1,80}$/.test(item.id)
        || ids.has(item.id) || !projects.has(item.project) || !/^[a-f0-9]{32}$/.test(item.run_id)
        || !Number.isInteger(item.priority ?? 0) || Math.abs(item.priority ?? 0) > 100
        || !Array.isArray(item.depends_on ?? [])) throw new Error('invalid_dispatch_plan')
    ids.add(item.id)
  }
  const workflowKeys = runs.map(x => `${x.project}:${x.run_id}`)
  if (new Set(workflowKeys).size !== runs.length) throw new Error('duplicate_dispatch_workflow')
  const visiting = new Set(), finished = new Set(), byId = new Map(runs.map(x => [x.id, x]))
  function visit(id) {
    if (!ids.has(id) || visiting.has(id)) throw new Error('invalid_dispatch_dependencies')
    if (finished.has(id)) return
    visiting.add(id)
    for (const parent of byId.get(id).depends_on ?? []) visit(parent)
    visiting.delete(id)
    finished.add(id)
  }
  runs.forEach(x => visit(x.id))
  return runs
}
