# External control protocol v1

The Python product exposes a provider-neutral local interface. It does not depend on any agent SDK. External adapters are installed and released separately.

Run `python -m lib.control --root /absolute/installation --workspace default`. Send one UTF-8 JSON document on stdin, close stdin, and read one JSON response from stdout. This is a local subprocess interface, not a public unauthenticated network service.

```json
{"protocol":"dataforge.control","version":1,"request_id":"inspection-1","action":"capabilities","params":{}}
```

Every response carries the same protocol, version and request id. A success has `ok: true` and `data`. A rejection has `ok: false` and a bounded `error.code`, with process exit code 2. Unvalidated request identities are not echoed. Raw exceptions, provider diagnostics and credentials are not part of this channel.

Unknown protocol versions, actions, parameter names, malformed run ids and invalid page limits fail before dispatch. Requests are capped at 1 MiB; responses at 2 MiB. Oversized responses fail explicitly and are never silently truncated into valid-looking data.

## Actions

| Action | Purpose |
| --- | --- |
| `capabilities` | Supported actions, protocol and pagination semantics |
| `runs.list` | Compact persisted workflow history; offset and limit |
| `runs.inspect` | State, stages, usage, active execution and node model bindings |
| `runs.create` | Freeze a validated recipe without executing it |
| `runs.execute` | Resume a frozen run in the foreground; existing workflow locks and checkpoints apply |
| `runs.cancel` | Request cooperative cancellation without claiming the worker has already stopped |
| `runs.events` | Durable events by UTF-8 byte cursor |
| `runs.streams` | Bounded original model-stream summaries; optional stage |
| `runs.stream_delta` | Bounded original deltas for a request and byte offset |
| `runs.preview` | Verified final training records, including retained reasoning |
| `runs.quality` | Verified package inventory and content report |
| `models.list` | Configured and cached-discovered model ids, without keys or network discovery |
| `models.status` | Existing shared scheduler's active/queued counts, limit, timeout and cooldown |
| `models.limit` | Persist a validated shared model scheduling limit |

Mutation actions require the operator's `--allow-mutations` flag. This is separate from the adapter's own permission configuration. No action approves a gate, writes a credential, evaluates shell commands, edits a frozen recipe, or bypasses export validation.

## Creation and execution

`runs.create` takes `params.recipe`. It accepts the existing public recipe fields, including node model bindings, node prompts, source paths, parser and cleaning settings, QA director, generation style, reasoning, review and production settings. Internal settings roots and run ids cannot be supplied by a model request. The existing application and domain validators remain authoritative.

The request id is an idempotency key for creation. Identical retries return the reserved run id; a different request under the same id is rejected. The control receipt reserves a run identity before creation. A valid fully frozen run can be recovered after an interrupted acknowledgement. Incomplete creation is reported as `control_create_outcome_unconfirmed`, with no automatic duplicate run or deletion of partial evidence.

Execution is deliberately foreground and checkpointed. An external controller owns background registration, its queue and process lifecycle. The product does not start or supervise that controller. A controller restart can inspect and resume persistent run ids; ephemeral external job ids are not persistent run ids.

## Monitoring and cancellation

Event cursors are byte offsets at complete UTF-8 JSONL boundaries. Reads contain at most 100 records and 64 KiB. An incomplete last line is reported and left unread until it completes. Invalid cursors, corrupt events and oversized individual events are rejected without skipping ahead. Model stream deltas use the existing journal's cursor and retention semantics.

Cancellation records an intent. A running pipeline checks it and preserves committed checkpoints. External process termination, including escalation and descendant cleanup, belongs to the external runtime. A cancelled or failed prerequisite must not silently allow a dependent workflow to run.

The shared model scheduler is authoritative across nodes, roles and processes within one core installation. An external controller may also limit workflow admission. Separate installations or machines do not automatically share model quotas.

## Independent adapter validation

The [dsh package](../plugins/dsh-dataforge/README.md) owns its SDK dependencies, exact upstream compatibility declaration, tests and archive. The product's `tests/test_external_control.py` verifies the stable protocol without installing Node or a harness. Adapter tests live in its own package and use its declared SDK version. Adding a product CLI command does not require a harness adapter release.

Integrity, schema compatibility and automated review are separate quality signals. Never interpret a verified artifact hash or a model score as an independently measured semantic accuracy rate.

## Configured credential protection

Formal conversation exports, automatic workflow packages, reviewed archives, download boundaries, and machine-controller responses reject exact matches to configured model credentials. This includes retained reasoning, tool arguments, audit sidecars and compressed source-caption records. Source files and rejected records are not silently rewritten or removed. An interrupted or blocked publication is never marked complete.

The check resolves explicit backend literals and credential environment references locally. It compares credential values of at least 12 characters and never emits a matching value. It is a targeted guard for configured model credentials, not proof of general de-identification or detection of all unknown secrets. Old files remain available for private inspection; a model-only score or a historical complete manifest does not waive the current publication check.
