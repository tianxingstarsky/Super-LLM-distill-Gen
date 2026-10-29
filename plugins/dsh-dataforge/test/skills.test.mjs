import test from 'node:test'
import assert from 'node:assert/strict'
import {Context} from '@deepseek-ai/cordis'
import SkillRegistry from '@deepseek-ai/dsh-skill'
import {registerBundledSkills} from '../skills.mjs'

test('installed package skills load through the official registry and respect local overrides', async () => {
  const context = new Context()
  try {
    new SkillRegistry(context, {})
    const dispose = registerBundledSkills(context.skills)
    const catalog = await context.skills.list()
    assert.deepEqual(catalog.map(skill => skill.name), ['dataforge', 'review-team'])
    const skill = await context.skills.get('dataforge')
    assert.equal(skill.provider, 'dataforge-bundled')
    assert.equal(skill.source, 'bundled')
    assert.ok(skill.content.includes('dataforge_dispatch'))
    assert.ok(!skill.content.startsWith('---'))
    assert.equal(skill.resourceBase.kind, 'directory')
    const override = context.skills.register({name: 'dataforge', description: 'Local operator override',
      source: 'runtime', content: 'Operator instructions'})
    assert.equal((await context.skills.get('dataforge')).content, 'Operator instructions')
    await override()
    assert.equal((await context.skills.get('dataforge')).provider, 'dataforge-bundled')
    await dispose()
    assert.deepEqual(await context.skills.list(), [])
  } finally { await context.fiber.dispose() }
})
