# DataForge adapter for DeepSeek Harness

This is an **independently installed and versioned package**. DataForge does not install, launch, build, or import DeepSeek Harness. Its Python dependencies do not include a harness SDK.

The controller uses the provider-neutral `dataforge.control` protocol, version 1, over bounded JSON on stdin/stdout. Other external controllers can use the same protocol. The separate conversation importer emits the versioned `dataforge.conversation_import` contract.

## Compatibility

- Tested upstream: DeepSeek Harness `0.2.1-rc.2`, commit `687f282e8625c6b8d11ff58211a90524be2e4317`.
- Node: 24 or later.
- Compatibility is exact for this release. Unknown future harness versions are refused by the bundle engine declaration.
- Update and test this package when harness interfaces change. The Python product does not need an update while protocol v1 remains compatible.
- The old source launcher, private dependency junction setup, command-name mirror, and team patch generator have been retired.

The official contracts are [tool authoring](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/docs/cookbook/adding-a-tool.md), [bundle publishing](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/docs/user/develop/basic/publish.md), [jobs](https://github.com/deepseek-ai/deepseek-harness/tree/687f282e8625c6b8d11ff58211a90524be2e4317/packages/jobs/jobs), and [managed subprocesses](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/docs/subsystems/subprocess.md).

## Install separately

Use the independent setup entry when asking an agent to install the integration. It installs the package with the official DSH CLI, registers both bundled skills automatically, writes this adapter's row and the required isolated package layout in the explicit profile, and runs a model-free doctor. It never installs the SDK into Python or edits the core's configuration.

From the DataForge repository root in PowerShell:

```powershell
$coreRoot = (Get-Location).Path
$pythonPath = Join-Path $coreRoot '.venv/Scripts/python.exe'
$harnessHome = Join-Path $coreRoot '.dataforge/dsh-adapter-setup/home'
npm --prefix plugins/dsh-dataforge install --ignore-scripts
node plugins/dsh-dataforge/setup.mjs install --home $harnessHome --profile dataforge --root $coreRoot --python $pythonPath --allow-mutations
```

Use the actual existing Python environment. On other systems the same entry accepts absolute POSIX paths. `--home` and `--profile` are required: no default user profile or home-level patch is changed. Omit `--allow-mutations` for an inspection-only deployment. The package also exposes `dataforge-dsh` as an npm executable.

If `dsh` is absent from PATH, the installer uses `npm exec --yes --package=@deepseek-ai/dsh@0.2.1-rc.2 -- dsh ...`. To use an existing installation, supply `--dsh /absolute/path/to/dsh/lib/bin.js` or an executable; on Windows use the JavaScript entry rather than a `.cmd`/`.ps1` shell shim. `--npm-cli /absolute/path/to/npm-cli.js` is available when npm is not beside Node. Existing Harness installations are never upgraded.

A new profile starts from the official `web` template; `--template` can select a different shipped composition. The installer persists `nodeLinker: isolated` in that profile's `pnpm-workspace.yaml` and passes the same pnpm flag during installation. This keeps SDK peers and the optional conversation reader inside the package's private dependency tree. A hoisted SDK at the profile root can shadow Harness services and split the private scope identity; the observed symptom is `agent-preset/invalid` with duplicate `deployment:persona-prefix`, commands, or tools when creating an empty session. Same-version native controls passed after using the isolated layout; no SDK patch or version upgrade is needed.

An existing isolated profile is retained. Installed hoisted layouts, unknown layouts, or public SDK shadows return an explicit error and require a new profile; the installer does not delete or rebuild an operator's existing dependencies. The installer uses `yaml` to preserve unrelated package settings, patch rows, comments, and inert `!!js` values. An existing adapter row with different settings returns `adapter_profile_configuration_conflict` without overwriting it; duplicate rows, unsupported YAML, and concurrent edits also fail visibly. Installation is not transactional: a failed final check can leave the package installed, and the error never claims successful host startup. Packed archives remain under the explicit home's `dataforge-packages/<sha256>/` so profile dependencies do not point to deleted temporary files. `--archive /absolute/package.tgz` installs an already packed release.

The successful JSON report includes a `start` object with the exact environment and argv. Apply its `DSH_HOME`, then start that profile with the same pinned CLI; for the example above:

```powershell
$env:DSH_HOME = $harnessHome
npm exec --yes --package=@deepseek-ai/dsh@0.2.1-rc.2 -- dsh --profile dataforge --no-open
```

Starting a host does not start a workflow. Model/provider setup remains in that external Harness installation. Do not copy core model credentials into the patch.

Recheck the **installed** package and profile, using the source entry as a forwarder:

```powershell
node plugins/dsh-dataforge/setup.mjs doctor --home $harnessHome --profile dataforge --root $coreRoot --python $pythonPath
```

Doctor checks exact SDK versions, persisted and installed isolated layout, absence of public SDK shadows, profile membership, and the requested project. It imports the installed package, registers tools and skills against the real official `ToolRuntime` and `SkillRegistry`, and sends only a read-only `capabilities` request to Python. It creates no Agent, sends no model prompt, and dispatches no job. Its report deliberately separates `official_sdk_registration`, `capabilities_verified`, and `host_boot_verified: false`: SDK registration is not proof of native host/session tool availability or live job ownership. Confirm the two tools and `/dataforge` / `/review-team` in a real host session before claiming end-to-end readiness. Native host startup errors remain blockers even when doctor passes.

### Migrate a 0.3.0 / 0.3.1 hoisted profile

Use the new package's installer with a **new profile name**, while keeping the same explicit home, core root, Python, and workspace. For the PowerShell variables above:

```powershell
node plugins/dsh-dataforge/setup.mjs install --home $harnessHome --profile dataforge-032 --root $coreRoot --python $pythonPath --allow-mutations
node plugins/dsh-dataforge/setup.mjs doctor --home $harnessHome --profile dataforge-032 --root $coreRoot --python $pythonPath
```

The old profile, installed dependencies, patch, and external session evidence remain intact. Core run ids, source caches, checkpoints, and artifacts remain under the same core root/workspace and can be inspected from the new profile. Stop the old host when its work can safely settle, start the new profile, then inspect persisted runs before resuming them. Old external job handles do not transfer. Simply editing a hoisted profile's YAML does not remove its already hoisted SDKs, so both installer and doctor reject that incomplete migration. If you prefer the original profile name, first preserve its configuration and evidence and rebuild its dependencies through normal package-management procedures; the automated safe path is the new profile above.

For a reviewable patch without installation, run `node setup.mjs config --root <absolute core> --python <absolute python> --output <new patch.yml>` from this package. The output must be a new file; `--allow-mutations` controls its write permission.

Manual official installation is also supported in a **new** profile. Initialize it from the shipped Web template, then set only `nodeLinker: isolated` in `$DSH_HOME/profiles/dataforge-manual/pnpm-workspace.yaml` before installing. Do not reuse an installed hoisted dependency tree. From this package directory:

```sh
npm pack
dsh --profile dataforge-manual --from-default-profile web --dump-config
# Set nodeLinker: isolated in this new profile's pnpm-workspace.yaml.
dsh plugin --profile dataforge-manual add /absolute/path/to/dsh-shujian-dataforge-0.3.2.tgz --config.node-linker=isolated --config.auto-install-peers=true
```

The explicit pnpm flag installs the declared SDK peers into this external profile even if the machine has disabled automatic peers. Installation alone and --dump-config do not prove that an entry module can load; the release is also checked by importing the installed profile package.

This uses the operator's existing harness installation. It does not vendor or replace it. The package can be copied or published separately from the DataForge repository. No parent-repository files are needed at runtime.

Configure the inserted row in the profile's `cordis.patch.yml`:

```yaml
- id: shujian-dataforge
  config:
    projects:
      - id: local
        root: /absolute/path/to/Super-LLM-distill-Gen
        python: /absolute/path/to/venv/bin/python
        workspace: default
    allowMutations: true
    allowModelLimitChanges: false
    maxActiveWorkflows: 4
    modelConcurrencyCeiling: 4
    monitorIntervalMs: 2000
```

On Windows use absolute Windows paths for both root and python, including the existing virtual environment's `Scripts/python.exe`. Do not copy model credentials into the patch. Each project points at its own configured DataForge installation.

The official subprocess provider scrubs ambient credentials. If a core installation uses environment-based keys, explicitly add an operator-owned `forwardEnv: [DEEPSEEK_API_KEY]` list to that project. Only those named variables are passed to the Python child; their values never enter tool arguments or output. Local backend credential files stay in the core installation.

`allowMutations` defaults to false. The separate model-limit setting defaults to false too. When enabled by the operator, a new model limit cannot exceed `modelConcurrencyCeiling`. Normal generation always uses the core's existing shared model admission pool.

The deployment must provide the official `skills` registry and `dsh-tool-skill` consumer, `jobs` service, a job controller such as `dsh-tool-jobs`, and the managed `subprocess` provider. Missing services or ownership fail before work starts. Built-in harness job ids belong to their creating session.

## Tools

`dataforge` accepts a project, action, and JSON params. Start with `capabilities`.

- `runs.list`, `runs.inspect`: workflow history, live state, and node model bindings.
- `runs.create`: freeze a complete recipe without executing it. Supply an explicit stable `request_id`; retries with the same content return the same run. Reusing the id for different content is rejected.
- `runs.events`: incremental durable events, using the returned UTF-8 byte cursor.
- `runs.streams`, `runs.stream_delta`: original live model output and per-request deltas.
- `runs.preview`: bounded, verified final samples, with original reasoning fields.
- `runs.quality`: verified inventory and content report. Scores are model assessments, not scientific proof.
- `runs.cancel`: request cooperative cancellation; the execution process checks it.
- `models.list`: configured and previously discovered model ids, without fetching a model catalog or exposing credentials.
- `models.status`: shared active/queued counts, concurrency limit, queue timeout, and cooldown.
- `models.limit`: operator-enabled shared limit adjustment; no provider credential access.

Use `dataforge_dispatch` for long runs. For example:

```json
{
  "runs": [
    {"id":"clean","project":"local","run_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","priority":10},
    {"id":"sft","project":"local","run_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","depends_on":["clean"]}
  ]
}
```

Replace the example ids with real frozen run ids. Dependencies wait for successful completion. They do not automatically change a child's inputs or bind the parent's artifacts: configure source reuse in the child's recipe before dispatch.

One official background job is returned for each workflow. Use the harness's native `job_list`, `job_output`, and `job_kill`. The adapter publishes progress and new core journal events into the job output ring. Original model deltas remain available through the explicit stream tools.

Scheduling supports up to 32 workflows in a plan, dependency validation, priorities, FIFO ties, a bounded waiting queue, and a global admission limit shared across callers of this adapter instance. The core independently enforces model limits across nodes, roles, and workflow processes within each core installation. Separate installations or machines do not share a model semaphore; this release does not claim a distributed model quota. Failed or cancelled dependencies block their children.

## Cancellation and recovery

Once a job is published, cancelling the outer tool call does not cancel it. Job cancellation or owner disposal requests core cancellation, then uses the official managed-process termination procedure if the worker does not exit during the grace period. Job settlement waits for cleanup.

Harness background handles are process-local. They do not survive a harness restart. DataForge run ids, checkpoints, events, source caches, and final artifacts remain on disk. Inspect them and submit the same run ids to resume.

Create requests reserve an identity before creating a workflow. A completed creation can be recovered after an interrupted acknowledgement. An incomplete directory is reported as `control_create_outcome_unconfirmed`; it is never silently recreated under a second identity.

## Agent instructions

The skills under `skills/` describe orchestration and review. Loading the adapter automatically registers the `dataforge-bundled` provider with the official `ctx.skills.registerProvider` API at `BUNDLED_SKILL_RANK` (600). The provider resolves resources beside the installed package, so it works outside this repository and needs no user skill-directory changes. Project, custom, and user skills keep their official higher priority. With `dsh-tool-skill` mounted, models can load `dataforge` and `review-team`, and users can invoke `/dataforge` and `/review-team`. They are adapter assets, not DataForge application settings. Generation and evaluation prompts remain configurable on the core's workflow nodes.

Agent startup procedure:

1. Run the installed doctor and report its verification boundaries. Confirm real session tools/skills before execution.
2. Load `dataforge`, call `dataforge` with `action: capabilities`, then inspect `runs.list`, `runs.inspect`, and `models.status` for the configured project.
3. Reuse a frozen run or call `runs.create` with a stable `request_id` and an explicit complete recipe. Source references must be bound in each dependent recipe.
4. Submit actual frozen run ids to `dataforge_dispatch`; monitor owned jobs with native job tools and durable core event cursors. Preserve run ids across host restarts.
5. Load `review-team` for evidence review; inspect full retained reasoning and verified previews before reporting quality. Report unresolved samples and operational failures separately.

The pinned official contracts are [bundled skill providers](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/packages/skill/skill/README.md), [skill loading](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/packages/skill/tool-skill/README.md), and [profile/CLI behavior](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/apps/cli/reference/README.md).

Never change a frozen recipe, remove reasoning to make a sample pass, infer correctness from a high score, or combine historical rework parents with their corrected children as independent samples.

## Import DSH conversation storage

The independent `import-conversations.mjs` entry reads one original DSH session.
It does not start DSH, run tools, call a model, or modify the source.

```sh
node import-conversations.mjs --input /path/session.v4.jsonl.zstd --output /new/conversation.jsonl --report /new/import-report.json
```

Both destinations must be new files. Exit code `0` means a complete import is ready
for preview. Exit code `2` means refusal. The JSON report uses
`dataforge.conversation_import`, version `1`; it includes the status, importer
identity, source and output hashes, byte size, message counts, and warnings.
The output contains one record with `id`, `messages`, `tools`, and `import_metadata`.
Import success is a format check, not a quality score or training approval.

DSH's native store uses `.jsonl` or `.jsonl.zstd` event logs. Its SQLite
`session-query` database is a disposable search index. It does not contain a
complete conversation. This importer recognizes that database without changing
it and asks for the original session log. It never invents tool results from
search snippets. Other Agent databases are outside this release.

The adapter uses the official pinned session-format catalog and strict reader.
Released formats `0` through `4` use its migration chain. Isolated transcript
restoration supplies an explicit empty child set, as the official catalog
requires. Newer or unrecognized formats fail visibly. Update this package to
adapt a future format; no private DSH schema belongs in the Python product.

The normalized conversation preserves system, developer, user, assistant, and
tool roles. Tool calls retain their IDs and exact argument strings. Only actual
reasoning blocks produce a `reasoning_content` field. Empty or missing reasoning
is never synthesized. Fork parent identity and inherited prefix length remain in
the record. The complete inherited prefix stays in its branch; it is not joined
with another session. The product's preview exposes any remaining training
eligibility constraints separately.

The human transcript uses the official append-origin surface projection.
Compaction replacements and empty settlement events stay in audit metadata;
they do not erase earlier user-visible turns or become duplicate training turns.
Original block order, message provenance, and historical tool definitions remain
available in `import_metadata`. Replacement-bearing records require review
before training because the transcript differs from the model's compacted view.
Unresolved calls, open final turns, changed tool definitions, missing attachments,
and unsupported blocks fail visibly. External attachment paths are never read.

Bounds are 50 MiB compressed input, 128 MiB decoded input, 50 MiB normalized
output, 8 MiB per physical row, 100,000 events, and 20,000 messages. The official
reader materializes one bounded session. This is not a constant-memory importer
for an entire session archive. Original bytes and all old generation/export
artifacts remain unchanged.

Install this package's dependencies separately with `npm install --ignore-scripts`.
The optional session reader dependencies do not enter the product environment.
If they are absent, the importer returns `dsh_import_dependencies_missing` with
installation instructions. No automatic fallback guesses an unknown schema.

Storage contracts were checked against official master commit
[`22ddee3`](https://github.com/deepseek-ai/deepseek-harness/tree/22ddee3792402c3e70ec5f86a63c3e3e895a2f60)
on 2026-10-24: [session persistence](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/packages/session/session-persistence/README.md),
[format catalog](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/packages/session/session-format-catalog/README.md),
[surface projection](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/packages/core/session/src/surface.ts),
and [SQLite index schema](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/packages/session-query/session-query-sqlite/src/schema.ts).
The controller SDK remains pinned to the tested `0.2.1-rc.2` release.

## Validation

Run this package's tests after installing its declared dependencies in this independent package directory or a separate installed copy:

```sh
npm install --ignore-scripts
npm test
```

The tests exercise the actual declared `defineTool` SDK plus controlled jobs and subprocess contracts. The main Python suite separately exercises the public protocol, native CPT execution, idempotent creation, cursor framing, error isolation, and shared scheduler delegation. Neither suite needs a paid model API.
