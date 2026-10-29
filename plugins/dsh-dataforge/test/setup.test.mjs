import test from 'node:test'
import assert from 'node:assert/strict'
import {spawnSync} from 'node:child_process'
import {mkdtempSync, readFileSync, rmSync, mkdirSync, writeFileSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {fileURLToPath} from 'node:url'
import YAML from 'yaml'

const setup = fileURLToPath(new URL('../setup.mjs', import.meta.url))
const run = args => spawnSync(process.execPath, [setup, ...args], {encoding: 'utf8', windowsHide: true})

test('operator config uses explicit paths and new-file semantics without installing or starting a host', () => {
  const directory = mkdtempSync(join(tmpdir(), 'dataforge-setup-test-'))
  try {
    const output = join(directory, 'adapter.patch.yml')
    const args = ['config', '--root', directory, '--python', process.execPath, '--output', output]
    assert.equal(run(args).status, 0)
    const [row] = YAML.parse(readFileSync(output, 'utf8'))
    assert.equal(row.id, 'shujian-dataforge')
    assert.equal(row.config.allowMutations, false)
    assert.equal(row.config.projects[0].python, process.execPath)
    assert.equal(run(args).status, 2)
    assert.equal(run(['config', '--root', 'relative', '--python', process.execPath]).status, 2)
    const install = run(['install', '--root', directory, '--python', process.execPath, '--profile', 'test'])
    assert.equal(install.status, 2)
    assert.equal(JSON.parse(install.stderr).code, 'explicit_absolute_dsh_home_required')
  } finally { rmSync(directory, {recursive: true, force: true}) }
})

test('old hoisted profiles fail before invoking DSH or changing existing configuration', () => {
  const directory = mkdtempSync(join(tmpdir(), 'dataforge-hoisted-profile-test-'))
  try {
    const profile = join(directory, 'profiles', 'old')
    mkdirSync(join(profile, 'node_modules'), {recursive: true})
    writeFileSync(join(profile, 'package.json'), '{}\n')
    writeFileSync(join(profile, 'node_modules', '.modules.yaml'), 'nodeLinker: hoisted\n')
    const policy = '# Keep operator policy\npackages:\n  - .\nnodeLinker: hoisted\n'
    writeFileSync(join(profile, 'pnpm-workspace.yaml'), policy)
    writeFileSync(join(profile, 'cordis.patch.yml'), '# Keep existing patch\n[]\n')
    const args = ['--home', directory, '--profile', 'old', '--root', directory, '--python', process.execPath]
    for (const command of ['install', 'doctor']) {
      const result = run([command, ...args])
      assert.equal(result.status, 2)
      assert.equal(JSON.parse(result.stderr).code, 'existing_hoisted_profile_requires_new_profile')
    }
    assert.equal(readFileSync(join(profile, 'pnpm-workspace.yaml'), 'utf8'), policy)
    assert.equal(readFileSync(join(profile, 'cordis.patch.yml'), 'utf8'), '# Keep existing patch\n[]\n')
  } finally { rmSync(directory, {recursive: true, force: true}) }
})
