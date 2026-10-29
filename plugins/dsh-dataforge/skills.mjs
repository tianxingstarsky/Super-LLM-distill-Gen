/** Package-owned skills use the official registry, without changing user skill roots. */
import {readFile} from 'node:fs/promises'
import {dirname} from 'node:path'
import {fileURLToPath} from 'node:url'
import {BUNDLED_SKILL_RANK} from '@deepseek-ai/dsh-skill'

const provider = 'dataforge-bundled'
const names = ['dataforge', 'review-team']

async function definition(name, signal) {
  const path = fileURLToPath(new URL(`./skills/${name}/SKILL.md`, import.meta.url))
  const text = await readFile(path, {encoding: 'utf8', signal})
  // These are trusted, released assets with a deliberately small metadata shape.
  const match = /^---\r?\nname: ([a-z0-9-]+)\r?\ndescription: ([^\r\n]+)\r?\n---\r?\n([\s\S]+)$/.exec(text)
  if (!match || match[1] !== name) throw new Error('invalid_bundled_skill')
  return {name, description: match[2], content: match[3].trim(), path,
    source: 'bundled', provider, invocation: {modelInvocable: true, userInvocable: true},
    resourceBase: {kind: 'directory', path: dirname(path)}}
}

export function registerBundledSkills(registry) {
  return registry.registerProvider(() => ({
    name: provider,
    async list({signal} = {}) {
      return Promise.all(names.map(async name => {
        const {content: _content, ...summary} = await definition(name, signal)
        return {...summary, rank: BUNDLED_SKILL_RANK, locator: name}
      }))
    },
    async get(candidate, {signal} = {}) {
      if (!names.includes(candidate.locator)) return undefined
      return definition(candidate.locator, signal)
    },
  }))
}
