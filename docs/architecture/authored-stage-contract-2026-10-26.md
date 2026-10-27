# Authored stage and speaker boundaries

## Observed failure

The historical Chinese multiturn case 07 in `role-anchor-live-v2` sent
`response_format: json_object` and `max_tokens: 32768`. The provider returned
856 leading spaces followed by an ordinary prose conclusion, with `stop`,
`stream_complete: true`, and 2,563 completion tokens. The response was not a
complete JSON rationale. The existing parser correctly rejected it. Its raw
content and provider reasoning remain in the experiment directory.

The wire placed the stage rules before approximately 96,000 source characters
and five ordinary dialogue messages. This is a plausible boundary ambiguity;
it is not proof of the model's internal cause. Increasing the token limit or
reinterpreting private provider reasoning as the missing authored rationale
would not repair the observed delivery failure.

## Change and ownership

New protocol-7 runs freeze recipe 34 and `authored_stage_contract_version=1`.
The workflow domain defines a concise stage contract. Its infrastructure
adapter appends that system message after the complete actual dialogue.
Rationale/joint stages specify the existing JSON fields and types; answer
continuation specifies plain text. The complete native prefix is still sent
through the existing Beta adapter. No new review or regeneration loop is added.

User quotations retain the user's identity. The assistant owns its present
interpretation, derivation and advice. Future experiments belong to the user.
First-person report text drafted for the user is allowed when identified as a
draft. There is no fixed opening, reasoning order, step count or pronoun quota.

`domain/authored_stage_contract.py` has no I/O. Request assembly remains in
`infrastructure/model_request_adapter.py`. The legacy factory only records the
new version marker. Recipe 33 and older tasks, custom frozen guidance, caches,
actual learner history, and training export fields are unchanged.

## Verification

`tests/test_authored_stage_contract.py` exercises complete messages, unchanged
old prefixes/history, checkpoint reuse, the real SDK native-prefix payload,
and continued rejection of the historical prose-only failure. Architecture
and related request/source/generation regressions must pass before live calls.

The isolated live experiment is
`.dataforge/software-ops-2026-10-24/role-anchor-repair/`.
It freezes the same cases 02 and 07, full source, prior turns, prompt templates,
model settings and routes. Only V4.1-Flash is permitted. The planned successful
path is one joint request and two native-prefix requests, with zero judge calls.
Every request, response, failure, usage receipt and baseline hash is retained.
Semantic inspection must distinguish delivery success from response correctness
and from errors already present in frozen historical assistant messages.

The targeted run completed on 2026-10-26: 256 related offline tests passed;
both live cases returned complete candidates using three V4.1-Flash generation
calls and no judge calls. Case 02 used one call (57.5 seconds, estimated CNY
0.0661928). Case 07 used two calls (42.8 seconds, estimated CNY 0.05421732).
Estimates use actual cache-hit/miss and output tokens at the verified off-peak
rate, not an invoice. The full native prefix and original learner history
matched exactly, and the historical baseline hashes remained unchanged.

This does **not** establish that both samples are ready for training. The Chinese
last turn separates assistant advice from user action, but repeats an overly
restrictive deterministic-decoding requirement, refers to private source
material as “资料里”, and leaves metric conditions unspecified in its proposed
report template. Earlier history retains its known unit/causal-language issues.
The English rationale also remains source-oriented in its wording. The report
marks these findings explicitly and preserves every body; no failure was hidden
or candidate text removed to achieve a pass.

## Provider contracts

- [JSON mode](https://api-docs.deepseek.com/guides/json_mode/) requires JSON
  instructions and recommends an example. JSON mode does not justify accepting
  an absent authored body.
- [Tool and message guidance](https://api-docs.deepseek.com/guides/tool_calls/)
  permits system messages within Chat Completions conversation history.
- [Thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/) describes
  native reasoning behavior; it is distinct from the deliberately authored
  training rationale and from a guarantee of semantic correctness.
