#!/usr/bin/env node
/** Operator entry: explicit profile installation and bounded, model-free diagnostics. */
import {spawnSync} from 'node:child_process'
import {createHash, randomUUID} from 'node:crypto'
import {existsSync, readFileSync, writeFileSync, mkdirSync, mkdtempSync, copyFileSync,
  unlinkSync, rmdirSync, renameSync, statSync, constants} from 'node:fs'
import {createRequire} from 'node:module'
import {dirname, isAbsolute, join, resolve} from 'node:path'
import {tmpdir} from 'node:os'
import {fileURLToPath, pathToFileURL} from 'node:url'
import {parseArgs} from 'node:util'
import YAML from 'yaml'

const packageRoot = dirname(fileURLToPath(import.meta.url))
const manifest = JSON.parse(readFileSync(join(packageRoot, 'package.json'), 'utf8'))
const DSH_VERSION = manifest.engines.dsh
const ROW_ID = 'shujian-dataforge'
const HELP = `DataForge DSH adapter ${manifest.version}
  node setup.mjs install --home <absolute DSH home> --profile <name> --root <core root> --python <python.exe> [--allow-mutations]
  node setup.mjs doctor --root <core root> --python <python.exe> [--home <home> --profile <name>]
  node setup.mjs config --root <core root> --python <python.exe> [--output <new patch.yml>]

Options: --workspace default, --project local, --template web, --dsh <CLI .js or executable>,
         --npm-cli <npm-cli.js>, --archive <existing .tgz>, --allow-mutations.
Install uses the official fixed @deepseek-ai/dsh@${DSH_VERSION} through npm exec when --dsh is omitted.
It changes only the explicit profile and preserves unrelated patch rows. Conflicting adapter rows are refused.
Profiles require pnpm nodeLinker=isolated. An installed hoisted profile is refused; install into a new profile to migrate safely.
Doctor never boots a host, creates a workflow, calls a model, or changes model limits.
`

function fail(code) { throw new Error(code) }
function absolute(value, code) {
  if (typeof value !== 'string' || !isAbsolute(value)) fail(code)
  return resolve(value)
}
function readBounded(path, max = 1024 * 1024) {
  if (statSync(path).size > max) fail('setup_file_too_large')
  return readFileSync(path, 'utf8')
}
function optionsFrom(argv) {
  const parsed = parseArgs({args: argv, allowPositionals: true, options: {
    help: {type: 'boolean'}, home: {type: 'string'}, profile: {type: 'string'}, root: {type: 'string'},
    python: {type: 'string'}, workspace: {type: 'string', default: 'default'},
    project: {type: 'string', default: 'local'}, template: {type: 'string', default: 'web'},
    dsh: {type: 'string'}, 'npm-cli': {type: 'string'}, archive: {type: 'string'},
    output: {type: 'string'}, 'allow-mutations': {type: 'boolean', default: false},
  }})
  if (parsed.values.help || parsed.positionals.length === 0) return {help: true}
  if (parsed.positionals.length !== 1 || !['install', 'doctor', 'config'].includes(parsed.positionals[0])) fail('invalid_setup_command')
  return {...parsed.values, command: parsed.positionals[0]}
}
async function adapterConfig(options) {
  const {configuration} = await import('./index.mjs')
  const config = configuration({projects: [{id: options.project,
    root: absolute(options.root, 'absolute_core_root_required'),
    python: absolute(options.python, 'absolute_python_required'), workspace: options.workspace}],
    allowMutations: options['allow-mutations']})
  return {...config, projects: [...config.projects.values()]}
}
function profileLocation(options) {
  const home = absolute(options.home, 'explicit_absolute_dsh_home_required')
  if (!/^[a-zA-Z0-9_-]{1,80}$/.test(options.profile ?? '')) fail('explicit_profile_name_required')
  return {home, directory: join(home, 'profiles', options.profile)}
}
function runner(command, argv, env, label, timeout = 300000) {
  const result = spawnSync(command, argv, {env, encoding: 'utf8', windowsHide: true,
    timeout, maxBuffer: 4 * 1024 * 1024})
  if (result.error || result.status !== 0) fail(`${label}_failed`)
  return result.stdout
}
function npmEntry(options) {
  const candidates = [options['npm-cli'], process.env.npm_execpath,
    join(dirname(process.execPath), 'node_modules', 'npm', 'bin', 'npm-cli.js'),
    resolve(dirname(process.execPath), '../lib/node_modules/npm/bin/npm-cli.js')]
  const found = candidates.find(path => path && isAbsolute(path) && existsSync(path))
  if (!found) fail('npm_cli_entry_required')
  return found
}
function dshCommand(options) {
  if (!options.dsh) return [process.execPath, npmEntry(options), 'exec', '--yes',
    `--package=@deepseek-ai/dsh@${DSH_VERSION}`, '--', 'dsh']
  const path = absolute(options.dsh, 'absolute_dsh_entry_required')
  if (/\.(?:cmd|bat|ps1)$/i.test(path)) fail('use_dsh_lib_bin_js_instead_of_shell_shim')
  if (!existsSync(path)) fail('dsh_entry_missing')
  return /\.[cm]?js$/i.test(path) ? [process.execPath, path] : [path]
}
function runDsh(command, argv, home, label) {
  return runner(command[0], [...command.slice(1), ...argv], {...process.env, DSH_HOME: home}, label)
}
function sameConfig(a, b) {
  const canonical = value => Array.isArray(value) ? value.map(canonical)
    : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value
  return JSON.stringify(canonical(a)) === JSON.stringify(canonical(b))
}
function profileDocument(text) {
  // Preserve comments, unrelated nodes and inert !!js scalars; never evaluate YAML.
  const document = YAML.parseDocument(text || '[]\n', {customTags: [{
    tag: 'tag:yaml.org,2002:js', resolve: value => value,
  }]})
  if (document.errors.length || document.warnings.length || !YAML.isSeq(document.contents)) fail('profile_patch_requires_manual_merge')
  return document
}
function profilePatch(text, config) {
  const document = profileDocument(text)
  const matches = document.contents.items.filter(row => YAML.isMap(row) && row.get('id') === ROW_ID)
  if (matches.length > 1) fail('duplicate_adapter_profile_rows')
  if (matches.length) {
    if (!sameConfig(matches[0].get('config')?.toJSON(), config)) fail('adapter_profile_configuration_conflict')
    return text
  }
  if (document.contents.items.length === 0) document.contents.flow = false
  document.contents.add(document.createNode({id: ROW_ID, config}))
  return document.toString()
}
function archiveFor(options, home) {
  let archive, temporary
  if (options.archive) {
    archive = absolute(options.archive, 'absolute_archive_required')
    if (!archive.endsWith('.tgz') || !statSync(archive).isFile()) fail('invalid_adapter_archive')
  } else {
    temporary = mkdtempSync(join(tmpdir(), 'dataforge-dsh-pack-'))
    const output = runner(process.execPath, [npmEntry(options), 'pack', '--ignore-scripts', '--json',
      '--pack-destination', temporary, packageRoot], process.env, 'adapter_pack')
    const packed = JSON.parse(output)
    if (packed.length !== 1 || !/^[a-zA-Z0-9_.-]+\.tgz$/.test(packed[0].filename)) fail('invalid_pack_result')
    archive = join(temporary, packed[0].filename)
  }
  const bytes = readFileSync(archive)
  const digest = createHash('sha256').update(bytes).digest('hex')
  const destination = join(home, 'dataforge-packages', digest)
  mkdirSync(destination, {recursive: true})
  const stored = join(destination, `dsh-shujian-dataforge-${manifest.version}.tgz`)
  try { copyFileSync(archive, stored, constants.COPYFILE_EXCL) } catch (error) {
    if (error.code !== 'EEXIST' || !readFileSync(stored).equals(bytes)) throw error
  }
  if (temporary) { unlinkSync(archive); rmdirSync(temporary) }
  return stored
}
function installedRoot(directory) {
  try { return dirname(createRequire(join(directory, 'package.json')).resolve(manifest.name)) }
  catch { fail('adapter_not_installed_in_profile') }
}
function isolatedProfile(directory, {verify = false} = {}) {
  const path = join(directory, 'pnpm-workspace.yaml')
  const original = existsSync(path) ? readBounded(path) : ''
  const document = YAML.parseDocument(original || 'packages:\n  - .\n')
  if (document.errors.length || document.warnings.length || !YAML.isMap(document.contents)) fail('profile_package_policy_requires_manual_merge')
  const linker = document.get('nodeLinker') ?? 'hoisted'
  if (!['hoisted', 'isolated'].includes(linker)) fail('unsupported_profile_node_linker')
  const modules = join(directory, 'node_modules')
  if (existsSync(modules)) {
    const metadata = join(modules, '.modules.yaml')
    if (!existsSync(metadata)) fail('existing_profile_layout_unconfirmed_use_new_profile')
    const layout = YAML.parse(readBounded(metadata)).nodeLinker
    if (layout !== 'isolated') fail('existing_hoisted_profile_requires_new_profile')
    for (const name of ['cordis', 'dsh-scope', 'dsh-agent', 'dsh-session', 'dsh-system-prompt',
      'dsh-tools', 'dsh-skill', 'dsh-jobs', 'dsh-subprocess']) {
      if (existsSync(join(modules, '@deepseek-ai', name, 'package.json'))) fail('profile_sdk_shadowing_requires_new_profile')
    }
  }
  if (verify && linker !== 'isolated') fail('profile_requires_isolated_node_linker')
  if (linker === 'isolated') return {path, original, updated: original}
  document.set('nodeLinker', 'isolated')
  return {path, original, updated: document.toString()}
}
function writeUnchangedFile(path, original, updated, code) {
  const current = existsSync(path) ? readBounded(path) : ''
  if (current !== original) fail(code)
  if (current === updated) return
  const temporary = `${path}.${randomUUID()}.tmp`
  writeFileSync(temporary, updated, {flag: 'wx', mode: 0o600})
  renameSync(temporary, path)
}
async function install(options, config) {
  const {home, directory} = profileLocation(options)
  if (!['web', 'headless', 'sdk', 'sdk-minimal', 'acp'].includes(options.template)) fail('invalid_profile_template')
  const patchPath = join(directory, 'cordis.patch.yml')
  let original = existsSync(patchPath) ? readBounded(patchPath) : ''
  let updated = profilePatch(original, config) // Refuse conflicts before invoking the package manager.
  // Existing managed dependencies must be safe before any package or policy change.
  if (existsSync(join(directory, 'package.json'))) isolatedProfile(directory)
  const command = dshCommand(options)
  const version = runDsh(command, ['--version'], home, 'dsh_version').trim().match(/\b\d+\.\d+\.\d+(?:-[a-zA-Z0-9.]+)?\b/)?.[0]
  if (version !== DSH_VERSION) fail('unsupported_harness_adapter_version')
  if (!existsSync(join(directory, 'package.json'))) {
    if (existsSync(directory)) fail('existing_profile_has_no_manifest')
    runDsh(command, ['--profile', options.profile, '--from-default-profile', options.template, '--dump-config'], home, 'profile_initialize')
    original = existsSync(patchPath) ? readBounded(patchPath) : ''
    updated = profilePatch(original, config)
  }
  const policy = isolatedProfile(directory)
  writeUnchangedFile(policy.path, policy.original, policy.updated, 'profile_package_policy_changed_during_install')
  const archive = archiveFor(options, home)
  runDsh(command, ['plugin', '--profile', options.profile, 'add', archive,
    '--config.node-linker=isolated', '--config.auto-install-peers=true'], home, 'adapter_install')
  const root = installedRoot(directory)
  if (JSON.parse(readBounded(join(root, 'package.json'))).version !== manifest.version) fail('installed_adapter_version_mismatch')
  // Package-manager changes are separate from user patch changes. Never overwrite a concurrent edit.
  const now = existsSync(patchPath) ? readBounded(patchPath) : ''
  if (now !== original && !(original === '' && (now.trim() === '' || now.trim() === '[]'))) fail('profile_patch_changed_during_install')
  if (updated !== now) {
    writeUnchangedFile(patchPath, now, updated, 'profile_patch_changed_during_install')
  }
  const report = runner(process.execPath, [join(root, 'setup.mjs'), 'doctor', '--root', config.projects[0].root,
    '--python', config.projects[0].python, '--workspace', options.workspace, '--project', options.project,
    '--home', home, '--profile', options.profile, ...(options['allow-mutations'] ? ['--allow-mutations'] : [])],
    process.env, 'installed_adapter_doctor', 60000)
  return {ok: true, adapter: manifest.name, version: manifest.version, profile: options.profile, home,
    archive, doctor: JSON.parse(report), start: {env: {DSH_HOME: home}, argv: [...command, '--profile', options.profile]}}
}
async function sdkProbe(config) {
  const [{Context}, {default: SystemPrompt}, {default: ToolRuntime}, {default: SkillRegistry}, adapter] = await Promise.all([
    import('@deepseek-ai/cordis'), import('@deepseek-ai/dsh-system-prompt'), import('@deepseek-ai/dsh-tools'),
    import('@deepseek-ai/dsh-skill'), import('./index.mjs'),
  ])
  const context = new Context()
  try {
    new SystemPrompt(context, {})
    new ToolRuntime(context)
    new SkillRegistry(context, {})
    // Only register against the real SDK registries. No execution provider or model is started.
    adapter.apply(context, config)
    const tools = context.tools.schemas().map(tool => tool.name)
    if (!['dataforge', 'dataforge_dispatch'].every(name => tools.includes(name))) fail('adapter_tools_not_registered')
    const skills = []
    for (const name of ['dataforge', 'review-team']) {
      const skill = await context.skills.get(name)
      if (!skill?.content || skill.provider !== 'dataforge-bundled' || skill.content.startsWith('---')) fail('bundled_skill_not_registered')
      skills.push({name, provider: skill.provider, path: skill.path})
    }
    return {kind: 'official_sdk_registration', tools, skills, host_boot_verified: false}
  } finally { await context.fiber.dispose() }
}
async function doctor(options, config) {
  if (Number(process.versions.node.split('.')[0]) < 24) fail('node_24_required')
  if (!existsSync(join(config.projects[0].root, 'lib', 'control.py')) || !statSync(config.projects[0].python).isFile()) fail('configured_core_or_python_missing')
  const require = createRequire(import.meta.url)
  const sdk = Object.fromEntries(['dsh-tools', 'dsh-jobs', 'dsh-subprocess', 'dsh-skill', 'dsh-system-prompt'].map(name => {
    const version = require(`@deepseek-ai/${name}/package.json`).version
    if (version !== DSH_VERSION) fail('unsupported_harness_adapter_version')
    return [name, version]
  }))
  let profile
  let registrationConfig = config
  if (options.home || options.profile) {
    const {home, directory} = profileLocation(options)
    isolatedProfile(directory, {verify: true})
    if (installedRoot(directory) !== packageRoot) fail('run_doctor_from_installed_package')
    const profileManifest = JSON.parse(readBounded(join(directory, 'package.json')))
    if (!profileManifest.dsh?.profile?.bundles?.includes(manifest.name)) fail('profile_bundle_layer_missing')
    const document = profileDocument(readBounded(join(directory, 'cordis.patch.yml')))
    const rows = document.contents.items.filter(row => YAML.isMap(row) && row.get('id') === ROW_ID)
    if (rows.length !== 1) fail('adapter_profile_configuration_missing')
    registrationConfig = rows[0].get('config')?.toJSON()
    const {configuration} = await import('./index.mjs')
    const actual = configuration(registrationConfig).projects.get(options.project)
    if (!actual || resolve(actual.root) !== config.projects[0].root || resolve(actual.python) !== config.projects[0].python
        || (actual.workspace ?? 'default') !== options.workspace) fail('adapter_profile_project_mismatch')
    profile = {home, name: options.profile, bundle: manifest.name, nodeLinker: 'isolated', publicSdkShadowing: false,
      allowMutations: registrationConfig.allowMutations ?? false,
      allowModelLimitChanges: registrationConfig.allowModelLimitChanges ?? false}
  }
  const registration = await sdkProbe(registrationConfig)
  const requestId = `doctor-${randomUUID()}`
  const environment = Object.fromEntries(['PATH', 'Path', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP']
    .filter(name => process.env[name] !== undefined).map(name => [name, process.env[name]]))
  const result = spawnSync(config.projects[0].python,
    ['-m', 'lib.control', '--root', config.projects[0].root, '--workspace', options.workspace],
    {cwd: config.projects[0].root, env: {...environment, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8'},
      input: JSON.stringify({protocol: 'dataforge.control', version: 1, request_id: requestId, action: 'capabilities', params: {}}),
      encoding: 'utf8', windowsHide: true, timeout: 30000, maxBuffer: 2 * 1024 * 1024})
  let reply
  try { reply = JSON.parse(result.stdout) } catch { fail('core_capabilities_unavailable') }
  if (result.error || result.status !== 0 || reply.protocol !== 'dataforge.control' || reply.version !== 1
      || reply.request_id !== requestId || reply.ok !== true) fail('core_capabilities_unavailable')
  return {ok: true, adapter: manifest.name, version: manifest.version, node: process.versions.node, sdk,
    ...(profile ? {profile} : {}), registration, core: {protocol: reply.protocol, version: reply.version,
      capabilities_verified: true}, host_boot_verified: false,
    note: 'SDK registration and read-only core protocol passed. Native host startup and live job ownership remain unverified.'}
}
export async function main(argv = process.argv.slice(2)) {
  const options = optionsFrom(argv)
  if (options.help) { process.stdout.write(HELP); return }
  if (options.command === 'doctor' && (options.home || options.profile)) {
    const {directory} = profileLocation(options)
    isolatedProfile(directory, {verify: true})
    const installed = installedRoot(directory)
    if (installed !== packageRoot) {
      process.stdout.write(runner(process.execPath, [join(installed, 'setup.mjs'), ...argv], process.env,
        'installed_adapter_doctor', 60000))
      return
    }
  }
  const config = await adapterConfig(options)
  if (options.command === 'config') {
    const text = YAML.stringify([{id: ROW_ID, config}])
    if (options.output) writeFileSync(absolute(options.output, 'absolute_output_required'), text, {flag: 'wx'})
    else process.stdout.write(text)
    return
  }
  const result = options.command === 'install' ? await install(options, config) : await doctor(options, config)
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`)
}
if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch(error => {
    const code = /^[a-z][a-z0-9_]{2,90}$/.test(error.message) ? error.message : 'adapter_setup_failed'
    process.stderr.write(`${JSON.stringify({ok: false, code})}\n`)
    process.exitCode = 2
  })
}
