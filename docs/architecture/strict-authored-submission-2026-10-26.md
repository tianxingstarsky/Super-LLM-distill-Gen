# Strict authored submission

## Failure and boundary

The prior case 09 completed its provider stream with `stop`, with JSON mode enabled,
but its content mixed string and array syntax. This was a second, application-level
JSON document inside an otherwise valid API response. A JSON Schema written into
the prompt was not an upstream grammar constraint.

New recipe 36 uses the official DeepSeek Chat strict tool protocol for integrated
CoT authoring. It is selected for new runs with a pinned official DeepSeek Chat
generation endpoint. Other providers retain recipe 35; existing frozen recipes,
cached results, native answer continuation and training export fields are unchanged.
There is no silent strict-to-text downgrade.

## Ownership

- workflows/domain/authored_submission.py owns the closed submission schema and
  deterministic projection into the existing reasoning/trace/answer record.
- workflows/application retains every existing quality and reasoning validation.
  The optional source lookup session can submit through the same tool; its lookup
  count, source scope and no-progress limit remain intact.
- models/application/ExecuteToolTurn retains shared admission, cancellation,
  usage accounting and bounded transport attempts.
- models/infrastructure/strict_tool_stream.py scopes the official beta URL to one
  request and uses the OpenAI SDK stream accumulator. Reasoning and tool argument
  deltas are journaled as received; no custom JSON repair or partial parsing
  promotes text into a candidate.
- workflows/infrastructure checkpoints the complete provider tool submission
  before local projection. Local validation failure cannot buy another full
  rationale when the same checkpoint is resumed.

DeepSeek thinking mode allows strict tools but rejects forced `required` or named
tool choice. The request keeps `auto` and thinking enabled. Runtime admission still
requires exactly one submission, no unassigned prose, a complete response and the
full local schema. No tool call, extra calls, malformed arguments or extra fields
are failures, not successful fallback text.

Upstream does not support `minLength` or `minItems`. Those grammar keywords are
omitted only in its schema projection; the unchanged full schema and semantic
checks still run locally. Empty reasoning, missing checks, vacuous/circular steps
and negative review outcomes are not exempted. Strict syntax is not a quality
verdict. No fixed thought order is imposed.

## Verification and actual run

`tests/test_strict_authored_submission.py` covers real SDK SSE fragmentation,
long escaped bilingual text, native reasoning, endpoint scoping, usage, incomplete
and length termination, observer failures, ambiguous/prose submissions, duplicate
fields, empty local-required lists, unchanged semantic checks, checkpoint reuse,
source tools and new-recipe selection. Existing tool, CoT, native prefix and onion
architecture regressions remain mandatory.

Real evidence lives under `.dataforge/software-ops-2026-10-24/strict-submission-repair`.
Only the previously failed case is rerun; no sibling regeneration or independent
paid judge is added. The report must distinguish syntax completion, existing
program validation and a separately documented reading of semantic quality.

Official sources checked on 2026-10-26:

- https://api-docs.deepseek.com/guides/tool_calls/#strict-mode-beta
- https://api-docs.deepseek.com/api/create-chat-completion/
