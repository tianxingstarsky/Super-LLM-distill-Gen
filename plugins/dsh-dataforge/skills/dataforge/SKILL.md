---
name: dataforge
description: Orchestrate persistent training-data workflows with the external DataForge control tools.
---

# DataForge orchestration

## Hard constraints

- NEVER read, echo, copy, or edit model credentials.
- NEVER bypass core artifact, source, review, or reasoning validation.
- NEVER claim that a model score proves correctness.
- NEVER remove reasoning fields, shorten evidence, or lower thresholds to turn a failed sample into a pass.
- NEVER combine superseded rework parents and corrected children as independent examples.
- Do not install, start, or patch the harness from inside DataForge.
- Use only configured project ids. Deployment settings control write access and resource ceilings.

## Procedure

Read capabilities, inspect existing runs and shared model admission, then decide whether to reuse a frozen run or create a new recipe. Model bindings, prompts, source selection, source cleaning, question design, answer generation, reasoning style, review, and repair stay on their corresponding workflow nodes.

A source-dependent SFT run needs sufficient parsed and cleaned source material. Reuse the source cache rather than reparsing it. A prerequisite relation between two jobs does not itself pass data between them; source references must be explicit in the downstream recipe.

Treat requested sample volume as a ceiling or expectation, not a reason to keep repetitive or unsupported samples. Choose a plan with independent branches only where dependencies permit it. Dispatch through dataforge_dispatch, using priorities and dependencies; do not hold the model conversation waiting for a synchronous CLI process.

Use native job_output/job_list/job_kill for owned background work. Use runs.events cursors for durable progress, runs.streams and runs.stream_delta to inspect original model token output, and bounded previews for finalized samples. A harness restart loses local job handles; resume the core's persisted run ids.

## Quality checks

Inspect whole multi-turn histories, including every assistant turn and each retained explanation. Check source conditions, omitted premises, answer/explanation consistency, invalid certainty, prompt or knowledge-wrapper leakage, and whether the learner-visible question is self-contained. Document-reading tasks may refer to visible material. Knowledge-application tasks must not pretend hidden material was supplied by the user.

Inspect question lengths, language, ambiguity, roles, dialogue acts, repetition, source coverage, and revision lineage across batches. Exact duplicates, semantic near-duplicates, and repeated corrected versions are different issues. Do not automatically reject a valid short question, ambiguous request, refusal, code symbol, or rare task shape.

## Reporting

Report real run ids, completed and quarantined counts, original reasoning, unresolved issues, review coverage and method, resource waits, and artifact hashes. Distinguish measured facts from model judgments and inspection hypotheses. Show incomplete work as incomplete.
