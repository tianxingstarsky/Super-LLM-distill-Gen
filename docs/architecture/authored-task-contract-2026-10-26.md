# Authored task scope and delivery

Case 09 produced valid strict tool arguments but failed the learner task. Its
explicit scope was one supplied passage. The author and native continuation both
received the entire paper. The author used examples from tables outside that
passage. The English user then received a Chinese answer, with stronger claims
than the passage supported.

## Why reflection did not stop this

- Integrated reflection is guidance during generation, not an independently
  executed assessment. Its receipt binds rules and text; it does not contain a
  semantic verdict. Schema validity cannot certify the resulting claims.
- The reasoning and continuation stages had different instructions. The complete
  bilingual style rule was supplied to authoring, while the continuation only
  received final-only reflection rules. The actual answer language was not
  represented as a distinct delivery constraint.
- Existing `observable_surface_defects` checks ran on review paths, not on the
  integrated-generation admission path.
- The self-check examined definitions, example counts and missing thresholds but
  accepted its own inference that label-by-label generation guarantees coverage
  and that bias can only be repaired before generation. A terminal caveat did
  not retract these stronger assertions.

## Decision

New official DeepSeek strict-authoring runs freeze recipe 39 and
`authored_task_contract_version=3`. Old recipes and their stored artifacts remain
unchanged. Domain rules live in `authored_task_contract.py`.

The contract derives delivery language and explicit passage restrictions from
actual learner turns, excluding the article block from instruction parsing.
Recognized later user changes supersede earlier constraints. Ambiguous tasks
retain their context; the parser is not a general natural-language interpreter.

For an explicit excerpt-only task, the model request is a disposable projection
containing that visible evidence. Full-source references, cached source packs and
private quotations are not forwarded. Source tools use the same projection.
Original sources, complete learner history, caches and export fields remain
unchanged. Ordinary knowledge-application tasks retain available source context.

Both stages receive the resolved scope and answer language in the primary system
instruction. Recipe 37 placed this contract in a trailing system turn; its live
pilot still answered in Chinese despite receiving the language instruction.
That pilot remains quarantined and its bodies and costs are retained. Recipe 38
separates English answer delivery from bilingual reasoning explicitly and checks
frequency versus absence, described procedure versus impossibility of alternatives,
and working definitions versus necessary conditions. It does not modify the
native reasoning prefix or force an answer opening. Guidance distinguishes
procedure, intent, empirical outcome and guarantee, without prescribing reasoning
steps or phrases. A prefix is not a certified fact or a substitute for task
constraints. User-configured reasoning guidance remains frozen and editable at
the existing node.

Observable language mismatches and exact quotations identifiable in the original
source but outside the learner's allowed excerpt cause quarantine. An observable
prefix violation stops before purchasing the answer. No body is removed or
rewritten, no JSON validation is weakened, and no independent paid review is
added. The receipt explicitly leaves semantic correctness unassessed.

## Production patterns consulted

The following primary sources were checked on 2026-10-26. They provide useful
engineering patterns, not a guarantee that this sample or framework is correct.

- [LangChain context engineering](https://docs.langchain.com/oss/python/langchain/context-engineering)
  separates transient model context from persistent state. Its
  [ModelRequest.override implementation](https://github.com/langchain-ai/langchain/blob/master/libs/langchain_v1/langchain/agents/middleware/types.py)
  creates a new request instead of mutating the original. The application uses
  the same boundary through its existing DDD ports; no second orchestrator was
  installed.
- [PydanticAI output validators](https://pydantic.dev/docs/ai/core-concepts/output/#output-validators)
  separate structured shape from application output validation, and bound output
  retries. Its [output implementation](https://github.com/pydantic/pydantic-ai/blob/main/pydantic_ai_slim/pydantic_ai/_output.py)
  keeps validators distinct from output schemas. We reuse our existing surface
  validator and retain checkpoints rather than adopting an unlimited repair loop.
- [Anthropic context engineering](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents)
  emphasizes relevant context and clear, flexible guidance. Here the relevant
  intervention is excluding disallowed paper content from this call, rather than
  adding more full-document warnings or a fixed chain of thought.

## Verification

`tests/test_authored_task_contract.py` covers actual request projection across all
three authoring stages, source-tool scope, original-input immutability, explicit
language overrides, later scope expansion, retained rejected reasoning, early
prefix stopping, and the complete historical case 09 fixture. Existing native
prefix, schema, content, source-memory, review and DDD regressions still apply.

The isolated live experiment is in
`.dataforge/software-ops-2026-10-24/task-contract-repair-v2/`. It freezes the original
question, source, editable guidance, model and high thinking effort; changes only
call-local scope and delivery enforcement. Two generation calls maximum, no
separate paid judge. Full bodies and per-request usage must be published, including
any remaining failure. A single-case improvement is not a production-scale
quality estimate.

## Saved-turn repair

The second pilot encountered `authored_submission_unassigned_content`: the
provider emitted a preamble as well as a valid tool call. Strict parameter
schemas do not prohibit ordinary assistant content. The local rejection remains.
`RepairAuthoredSubmission`, composed by `authored_submission_repairer`, supports
one explicitly requested continuation with the exact earlier assistant/native
thinking and a bound tool error. Located objections must quote prior text. The
new submission must pass the same assembler; the use case never retries itself,
never deletes the old prose, and never declares semantic correctness. The normal
checkpointed port prevents repurchasing an unchanged recovery. No automatic
recovery is enabled for existing runs.

The isolated `task-contract-recovery` experiment records the assisted repair,
then continues the normal CoT and native-answer validators in a fresh run. It must
report the two earlier failed pilots and their costs, and distinguish assisted
repair from first-pass automation.

## Final-stage language placement

Recipe 39 places a brief delivery-language instruction after final-stage reflection
rules, as well as retaining the primary task contract. Bilingual reasoning is not
an answer translation request. Real source quotations, proper names and requested
other-language content remain allowed; uncertain language is not forced. The
complete reasoning prefix is untouched. This placement is versioned because the
live recipe-38 continuation sometimes repeated bilingual glosses even after the
primary instruction was corrected.

## Final evidence and remaining limits

The final report is in `task-contract-release` under the isolated experiment
folder. Nine V4.1-Flash model requests across all failed and assisted repair stages
cost an estimated CNY 0.33155608 in total (usage and timestamp-based pricing, not
an invoice). The last answer delivery was one request; its cost alone is not the
cost per accepted sample. Full bodies and trace are retained. The coding
assistant's full-text check found case 09 usable after assisted repair; this is
not blind evaluation or first-pass automatic success.

A final local quarantine was a false positive: `this prompt` was explicitly
attributed to the user-visible paper, not the private execution. The narrow
attribution fix retains first-person/private-field/execution-rule rejection.
Exact authenticated model checkpoints were replayed through the normal workflow,
without changing their text or making another model request. Final related
regression: 369 passed, no failures or skips. This is the relevant suite, not the
entire repository suite. Broader unattended semantic reliability remains open.
