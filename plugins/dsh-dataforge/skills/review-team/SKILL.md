---
name: review-team
description: Review complete exported dialogues and reasoning while preserving exact evidence and unresolved findings.
---

# Evidence-based review coordination

Use the configured external DataForge tools and the harness's normal session and job ownership. Do not expose credentials or invent reviewer identities.

## Hard constraints

- NEVER turn a missing premise into an unstated assumption to rescue an answer.
- NEVER accept a self-asserted justification as independent evidence.
- NEVER discard reasoning or future-turn errors to raise the pass rate.
- NEVER overwrite frozen parent artifacts.
- A rejected, incomplete, contradictory, or context-limited assessment remains unresolved.
- Sharing a model between generation and review is allowed, but correlated model errors must be disclosed.

## Review method

Read the exact learner-visible history up to each supervised assistant turn. Inspect the answer and its complete retained explanation against actual source conditions and observed tool results. Keep facts, conditional advice, proposed experiments, and measured results distinct. Later user information must not justify an earlier unsupported answer.

Use the core's configured challenge and adjudication contracts. Preserve claim coverage, contradiction vetoes, source ranges, raw receipts, and content hashes. A high aggregate score cannot override a material refutation. Check that a requested revision actually appears in the new candidate and that unchanged turns remain valid.

The review and correction models are configured in the core's review node. Do not create a second hidden review pipeline, force fixed reasoning steps, or change the generation model without the operator's instruction.

## Outcome

Return accepted items, rejected items, unresolved disagreements, context-limit errors, revision lineage, and exact evidence. A timeout is an operational failure, not a sample-quality rejection. A model-only review is not an empirical accuracy benchmark.
