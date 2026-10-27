# Authored response transport and quality ownership

## Failure demonstrated by the real records

The frozen ten-case run made 142 provider requests. Full-text review considered
three cases usable. Case 10's repaired rationale contained 5,947 characters and
six `basis` arrays. The former validator reported `authored_reasoning_vacuous_step`
because the canonical contract required strings. This is a transport mismatch,
not evidence that the rationale is empty or logically invalid.

The run also has real content failures. Case 01 reverses a necessary condition;
cases 05 and 06 confuse ROUGE score points with percentage points; case 07 has a
defective historical assistant turn; case 08 makes a necessity claim without the
required comparison; case 09 misses the requested bilingual expression standard.
Normalizing case 10 does not resolve those failures or complete its missing answer.

## Program ownership

New evidence-policy-2 runs use recipe 31 and explicitly freeze
`authored_trace_transport_version=1`. Existing recipes are not migrated.

- Workflows domain owns the response Schema and the canonicalization receipt.
- Only explicit nonempty lists of basis strings can become multiline strings.
  Every byte of every atom and the original order are retained. The receipt
  records the original atoms, paths, raw digest and canonical digest.
- Missing evidence, mixed types, unknown fields and incomplete structures are
  not inferred. Such failures have a field path and stop as protocol errors.
- Existing nonvacuity, noncircularity and duplicate checks still run. Joining
  atoms cannot hide a vacuous, repeated or circular basis entry.
- Semantic status remains `not_assessed`. All independent quality checks run
  after structural validation. Canonicalization is never a release decision.
- Infrastructure retains raw checkpoints; the application works on a copy.
  Training field names do not change. Transport receipts are diagnostic metadata.

The models application catches explicit syntax exceptions only. Its bounded
retry includes the last visible malformed response and parser feedback while
retaining the original message roles. It does not expose provider reasoning,
accumulate all failed turns, or retry a receipt-publication failure. If identical
malformed bytes recur after feedback, it stops instead of purchasing another
identical attempt. Schema-invalid CoT payloads do not use the content rewrite loop.

The model-layer retry fix applies to future requests, including future requests
from unfinished older runs. It does not rewrite a stored request, completed
response or checkpoint. The recipe flag gates the new CoT Schema normalization
and failure routing, not the common JSON exception-classification bug fix.

## Local validation is not provider-enforced decoding

The current CoT transport still uses `json_object`. The new Schema is validated
locally and supplied as instruction guidance, not represented as a server-side
guarantee. DeepSeek documents two stronger provider contracts:

- [Responses JSON Schema output](https://api-docs.deepseek.com/api/create-response/)
  uses `text.format.type=json_schema` with a name and schema.
- [Strict tool calls](https://api-docs.deepseek.com/guides/tool_calls/)
  use the beta endpoint, `strict=true` on every function, required object fields
  and `additionalProperties=false`. Its documented subset excludes `minLength`,
  `maxLength`, `minItems` and `maxItems`; local checks must retain those constraints.

These docs were checked on 2026-10-25 without model API calls. This change does
not silently switch API formats, claim upstream strict-mode validation, or
change the verified native-reasoning-prefix protocol. A provider adapter must
advertise the actual enforced contract and validate its capability separately.

## Verification

`tests/test_authored_trace_transport.py` covers representation preservation,
field failures, repeated/vacuous/circular atoms, retained negative decisions and
the absence of whole-draft regeneration for protocol failures.
`tests/test_model_request_use_cases.py` covers bounded parser feedback, repeated
invalid output, publication errors and unexpected adapter errors.

The offline report under `.dataforge/software-ops-2026-10-24/agent-protocol-ownership/`
replays the actual case-10 response, reconstructs its exact original JSON from
the receipt, verifies all frozen experiment file digests, and records no model
calls. It preserves the historical 3/10 usable result. It is not an A/B accuracy
measurement or a claim that a corrected envelope is a qualified training sample.
