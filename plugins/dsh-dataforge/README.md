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

From this package directory, create an archive:

```sh
npm pack
dsh plugin --profile web add /absolute/path/to/dsh-shujian-dataforge-0.3.0.tgz --config.auto-install-peers=true
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

The deployment must provide the official `jobs` service, a job controller such as `dsh-tool-jobs`, and the managed `subprocess` provider. Missing services or ownership fail before work starts. Built-in harness job ids belong to their creating session.

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

The optional skills under `skills/` describe orchestration and review. Install them in the harness's configured skill directory if desired. They are adapter assets, not DataForge application settings. Generation and evaluation prompts remain configurable on the core's workflow nodes.

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
