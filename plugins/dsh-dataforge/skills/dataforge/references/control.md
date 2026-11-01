# DataForge tool parameters

Use the native `dataforge` tool with `project`, `action`, and `params`.
For `runs.create`, also supply one stable top-level `request_id`; retry identical
content with the same identity. `project` must be an installed adapter project ID.

Example inspection (replace both IDs with actual returned/configured values):

```json
{"project":"local","action":"runs.inspect","params":{"run_id":"<returned-run-id>"}}
```

| Action | Params |
| --- | --- |
| `capabilities` | `{}` |
| `runs.list` | Optional `offset`, `limit` |
| `runs.inspect` | `run_id` |
| `runs.create` | `recipe` |
| `runs.cancel` | `run_id` |
| `runs.events` | `run_id`, optional `offset`, `limit` |
| `runs.streams` | `run_id`, optional `stage` |
| `runs.stream_delta` | `run_id`, stream `request_id`, optional `stage`, `offset`, `limit_bytes` |
| `runs.preview` | `run_id`, `target`, optional `offset`, `limit` |
| `runs.quality` | `run_id` |
| `models.list` | `{}` |
| `models.status` | `backend`, `model` |
| `models.limit` | `backend`, `model`, `max_concurrency`, optional `request_queue_timeout_seconds` |

Use the exact target name, such as `cpt`, `sft` or `multiturn`, for a preview.
Run IDs are 32-character IDs returned by creation, not host job IDs. A missing
`run_id` is an invalid parameter, not a model failure. `limit` is at most 100;
event offsets are returned UTF-8 byte cursors, not row numbers. Stream reads are
bounded to 65,536 bytes. Inspect before retrying an interrupted operation.

Use `dataforge_dispatch` for native execution:
`{"runs":[{"id":"a-local-job-name","project":"local","run_id":"<returned-run-id>"}]}`.
Optional per-run `priority` and `depends_on` express scheduling dependencies;
they do not transfer source material. Keep the returned job handle and run ID.
Use the native jobs tools for background work; use persisted core events after a restart.

`runs.create` accepts the public recipe, including node bindings, node prompts,
source handling, generation, review and `node_control_text_configs`.
The [operations guide](operations.md) includes the versioned control-text example
and its required `schema`. A newly frozen configuration appears in
`runs.inspect` under `node_control_text_configs`; legacy recipes without it omit it.
Creation does not execute a model. Preserve existing frozen tasks.

## Model-free local verification

Installation, `setup.mjs doctor`, SDK registration and skill discovery can be
checked without a model. A native Agent conversation and native tool dispatch
require a configured model; without it, stop short of claiming those were tested.
Do not build another scheduler or infer usage from the adapter's internal exports.

For a local business check without a model, the core also documents a subprocess
entry. From an isolated product root described in operations.md, run the product
Python with `-X utf8 -m lib.control --root <absolute-root> --workspace default`,
adding `--allow-mutations` only for authorized writes. Send one UTF-8 JSON object
on stdin and close stdin:

```json
{"protocol":"dataforge.control","version":1,"request_id":"unique-read-id","action":"capabilities","params":{}}
```

Use a JSON serializer rather than shell string interpolation. Apply the table's
params to this envelope. A local CPT recipe in SKILL.md needs no model; its
execution action is `runs.execute` with `run_id`. Read the JSON response and exit
code; `ok:false` preserves a refusal, not success. This verifies core protocol
business, **not** native host tool dispatch. Keep that distinction in the report.
