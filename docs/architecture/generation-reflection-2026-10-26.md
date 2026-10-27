# Generation-time reflection

The console now creates styled CoT tasks with guidance protocol 7 and recipe
35 (previously 33/34). The author considers applicable rules while drafting and produces the
complete authored rationale and answer. There is no mandatory independent
checklist, prefix assessment, style score or contract model call in this mode.
Custom guidance remains editable in the CoT node and personal prompt library.
It is not a fixed sequence of reasoning steps or a self-awarded scorecard.

Recipe 34 adds a versioned stage contract after the unmodified learner history.
See [stage and speaker boundaries](authored-stage-contract-2026-10-26.md).
Frozen recipe 33 requests and checkpoints remain unchanged.

Recipe 35 moves the selected editable guidance once after the actual dialogue;
see [context placement and role ownership](reflection-context-placement-2026-10-26.md).
Recipe 34 stage behavior and all earlier frozen jobs remain unchanged.

New default guidance also anchors the authored rationale in the responding
assistant's perspective. It asks for real, consequential user wording and
explains how that wording changes interpretation, choices and the proposed
response. First-person references stay attached to their speakers, including
quoted users, source authors, role-play characters and bilingual paraphrases.
No quote is invented for an input without text. There is no prescribed opening,
pronoun quota, extra review call or automatic semantic pass. The answer's form
still follows the learner's request. Frozen and personal guidance is unchanged;
users can choose the current defaults through the existing reset control.

The role guidance explicitly targets `reasoning`, not just the answer or trace.
It also separates the assistant's present explanation from actions that the
user may later perform. First-person prose must not imply access to the user's
environment, personal experiments, or unperformed actions.

### Targeted role-guidance check

Two historical cases were selected: English joint authoring (02) and Chinese
multiturn native-prefix authoring (07). Two iterations used five V4.1-Flash
generation calls in total and no judge calls. The first attempt did not meet
the role requirement: 02 remained impersonal and 07 misattributed the user's
planned evaluation to the assistant. After clarifying the field and action
ownership, 02 linked exact user wording to first-person interpretation and a
concrete schema recommendation. Case 07 then returned non-JSON text, so no
complete new candidate or role-quality conclusion was available for it. All
raw responses, original reasoning and first-attempt failures were retained.

These are assistant-inspected observations, not independent certification or
an aggregate acceptance claim. The historical baseline predates both the
user-task and role-guidance additions. Local reports are under
`.dataforge/software-ops-2026-10-24/role-anchor-live/` and `role-anchor-live-v2/`.
The related offline suite passed 99 tests; the final wording also passed 19
reflection and node-control tests (overlapping coverage, not 118 distinct tests).

The subsequent recipe-34 boundary repair and targeted live result are documented
in [stage and speaker boundaries](authored-stage-contract-2026-10-26.md).
Both complete outputs are now available, but the remaining semantic findings
are not counted as resolved by delivery or role-ownership success.

## Request and artifact boundaries

- Joint authoring makes one generation request on a successful path. Invalid
  JSON stops after that response; it cannot trigger a hidden re-authoring loop.
- Native prefix mode makes one authoring request and one answer continuation.
  Empty visible output is recorded as a failure and retains the full rationale.
  Neither rationale nor continuation is regenerated automatically. Shared rules
  shape the rationale; only final-only guidance is added to the answer request.
- Existing response-schema, authored-trace and learner-surface checks remain.
  A rejected draft and its reasoning are retained for explicit correction.
- The record carries `reasoning_reflection` with exact rule/body hashes and
  `independent_review: false`. It does not carry a fabricated passed verdict.
  Reports expose this receipt as provenance, not independent verification.
- An explicitly enabled review node still runs and may reject the candidate;
  it does not launch the generation guidance as another checklist agent.
- Trainer projection remains `question`, `reasoning`, `answer` for CoT.
  Source memory, policy receipts and provider details stay outside this payload.
- Frozen protocols 1–6 retain their original requests and checkpoints. Copying
  an old task keeps its mode until the user selects generation-time reflection.
  API callers select it through `node_generation.cot.self_check.version=7`.

## DeepSeek continuation contract

The [official thinking-mode guide](https://api-docs.deepseek.com/guides/thinking_mode/)
requires complete historical `reasoning_content` when the request includes
tools. Without tools, ordinary historical reasoning is ignored. Passing a
shortened history is therefore not a general mechanism for restoring thought.

The [Beta prefix interface](https://api-docs.deepseek.com/guides/chat_prefix_completion/)
uses a final assistant message with `prefix: true` at `/beta`.
Its [request schema](https://api-docs.deepseek.com/api/create-chat-completion/)
also accepts a reasoning prefix. The existing adapter preserves every authored
prefix byte and verifies its hash. Its visible newline selects answer
continuation; it does not promise further reasoning or restoration of an
interrupted provider execution. This change does not truncate native reasoning
or silently use ordinary user messages as a substitute for native prefill.

## Verification limits

Offline tests prove request counts, preserved bodies, unchanged export schemas,
resume behavior and isolation from independent review. They do not prove that
the author corrects semantic defects, or provide a new real-model acceptance
rate or cost per usable sample. These offline suites make no paid API calls;
the separately recorded live checks above do.

On 2026-10-26, the related business, request, export/resume and DDD/onion
regressions passed: 456 tests. The five node-guidance UI interaction tests also
passed. These use injected responses and Streamlit AppTest, not live model
evaluation or screenshot-based visual acceptance.
