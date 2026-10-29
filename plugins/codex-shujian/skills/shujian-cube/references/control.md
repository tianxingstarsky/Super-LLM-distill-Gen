# Local control reference

All actions use `dataforge.control` version 1. The helper emits the complete core
response and preserves a core refusal's nonzero exit code. An `ok: true` response
confirms that operation, not the correctness of generated content.

## Calling the helper

```text
<product-python> <skill-dir>/scripts/control.py --root <product-root> --action runs.list
<product-python> <skill-dir>/scripts/control.py --root <product-root> --action runs.inspect --params-file inspect.json
```

`inspect.json` contains `{"run_id":"<32-character run ID>"}`. A parameter file
holds the action's **params object**, not a complete protocol envelope. It is read
as UTF-8; a Windows UTF-8 BOM is accepted. An inline `--params` JSON object is also
supported. The helper serializes the envelope, runs the core from its own root,
and checks the response identity. `--python` can select an explicit product
interpreter. Otherwise the product's `.venv` is used. No shell activation is needed.

## Actions

| Action | Params | Use |
| --- | --- | --- |
| `capabilities` | `{}` | Discover the installed protocol and action list. |
| `runs.list` | Optional `offset`, `limit` | Find persistent tasks. |
| `runs.inspect` | `run_id` | Read state, active execution, node bindings and usage. |
| `runs.create` | `recipe` | Freeze a new recipe without executing it. |
| `runs.execute` | `run_id` | Execute or resume in the foreground. |
| `runs.cancel` | `run_id` | Record cooperative cancellation intent. |
| `runs.events` | `run_id`, optional `offset`, `limit` | Read durable events and `next_offset`. |
| `runs.streams` | `run_id`, optional `stage` | Find original model streams. |
| `runs.stream_delta` | `run_id`, `request_id`, optional `stage`, `offset`, `limit_bytes` | Read a bounded original stream segment. |
| `runs.preview` | `run_id`, `target`, optional `offset`, `limit` | Inspect finalized training records, including reasoning. |
| `runs.quality` | `run_id` | Read verified package inventory and content report. |
| `models.list` | `{}` | List configured/cached model identities without probing an API. |
| `models.status` | `backend`, `model` | Inspect the shared model queue and limit. |
| `models.limit` | `backend`, `model`, `max_concurrency`, optional `request_queue_timeout_seconds` | Change a shared limit within authorized work. |

`limit` is at most 100. Events use UTF-8 byte offsets at complete JSONL boundaries,
not row counts. Use returned offsets; incomplete trailing rows remain unread.
Stream segments are at most 65,536 bytes. Use the exact target name reported by
the run, such as `sft`, `cot`, or `multiturn`.

## Creation and execution

Creation accepts the product's public recipe fields: `sources`, `brief`, `targets`,
`name`, `backend`, `model`, `judge_backend`, `judge_model`, `jev_backend`, `jev_model`,
`max_units`, `chunk_chars`, `tasks`, `sample_count`, `concurrency`, `batch_size`,
`node_models`, `conversation_turns`, `document_parser`, `cpt_processing`,
`node_generation`, `reasoning_trim`, `node_prompts`, `qa_director`, `production`,
`evaluation_sources`, `web_research`, `package_review`, `review_repair`,
`agent_experience`, `reasoning_required`, `agent_replay_mode`, `repair_inputs`,
`knowledge_retrieval`, `sft_output_style`, `source_names`, `evaluation_source_names`.
The product validates their values and supported target/model combinations.

For an existing configured task, prefer reuse over inventing a different recipe.
Inspect existing node bindings and the model list for valid identities. A recipe
with new requirements must be created as a separate run; do not change an old
task's saved recipe. Source paths must be absolute and accessible to the product.

### No-model source-only smoke test (v0.0.1)

For a local installation check with an existing Markdown source, save this
params object. Replace the source path with the actual absolute file path:

```json
{
  "recipe": {
    "targets": ["cpt"],
    "sources": ["C:/work/source.md"],
    "max_units": 2,
    "sample_count": 1
  }
}
```

This verified v0.0.1 recipe uses native source passthrough without a model. It
checks local creation, execution, and exported source records; it does not
validate AI cleaning or semantic quality. Inspect the new run's node bindings
before execution. If model-backed work would run, stop and resolve that mismatch
with the no-model task rather than starting it.

Save the params object in a file, then call:

```text
<product-python> <skill-dir>/scripts/control.py --root <product-root> --action runs.create --params-file create.json --request-id <stable-task-key> --allow-mutations
```

The response provides `data.run_id`. Save it before execution. An identical
creation retry uses the same key and file. `control_request_id_conflict` means the
same key was reused for different content. `control_create_outcome_unconfirmed`
means an interrupted creation cannot be safely declared complete: inspect the
reserved run and report the uncertainty rather than creating duplicates.

Execute by passing `{"run_id":"..."}` with `runs.execute --allow-mutations`.
This command remains running until execution settles; use the agent host's normal
managed terminal facility to retain a live session. A second request can monitor
events while the session runs. Check final persisted state after the process
exits. A lost terminal handle does not erase the run or its checkpoints.

This interface does not bypass source or export validation, mutate a frozen
recipe, approve review findings, or manage an agent host. If a requested operation
is absent from `capabilities`, report that boundary instead of editing internal
files. Generation prompts and quality policy remain user-controlled in nodes.
