# DataForge adapter for DeepSeek Harness

This is an **independently installed and versioned package**. DataForge does not install, launch, build, or import DeepSeek Harness. Its Python dependencies do not include a harness SDK.

The adapter talks only to the provider-neutral `dataforge.control` protocol, version 1, over bounded JSON on stdin/stdout. Other external controllers can use the same protocol.

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
dsh plugin --profile web add /absolute/path/to/dsh-shujian-dataforge-0.2.0.tgz --config.auto-install-peers=true
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

## Validation

Run this package's tests after installing its declared peer dependencies in a separate development directory:

```sh
npm install --ignore-scripts
npm test
```

The tests exercise the actual declared `defineTool` SDK plus controlled jobs and subprocess contracts. The main Python suite separately exercises the public protocol, native CPT execution, idempotent creation, cursor framing, error isolation, and shared scheduler delegation. Neither suite needs a paid model API.
