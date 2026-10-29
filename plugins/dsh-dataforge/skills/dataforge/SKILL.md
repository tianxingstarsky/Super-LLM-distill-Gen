---
name: dataforge
description: Orchestrate persistent training-data workflows with the external DataForge control tools.
---

# DataForge orchestration

## Hard constraints

- NEVER read, echo, copy, or edit model credentials.
- NEVER bypass core artifact, source, review, or reasoning validation.
- NEVER claim that a model score proves correctness.
- NEVER silently lower thresholds, discard original reasoning/evidence, or relabel failed samples to inflate the pass rate. The operator may explicitly choose a different quality policy for a new recipe; record that policy and keep its actual outcomes visible. Do not edit a frozen recipe or hot-change a running task.
- NEVER combine superseded rework parents and corrected children as independent examples.
- Do not install, start, or patch the harness from inside DataForge.
- Use only configured project ids. Deployment settings control write access and resource ceilings.

## Procedure

Read capabilities, inspect existing runs and shared model admission, then decide whether to reuse a frozen run or create a new recipe. Model bindings, prompts, source selection, source cleaning, question design, answer generation, reasoning style, review, and repair stay on their corresponding workflow nodes.

A source-dependent SFT run needs sufficient parsed and cleaned source material. Reuse the source cache rather than reparsing it. A prerequisite relation between two jobs does not itself pass data between them; source references must be explicit in the downstream recipe.

Treat requested sample volume as a ceiling or expectation, not a reason to keep repetitive or unsupported samples. Choose a plan with independent branches only where dependencies permit it. Dispatch through dataforge_dispatch, using priorities and dependencies; do not hold the model conversation waiting for a synchronous CLI process.

Use native job_output/job_list/job_kill for owned background work. Use runs.events cursors for durable progress, runs.streams and runs.stream_delta to inspect original model token output, and bounded previews for finalized samples. A harness restart loses local job handles; resume the core's persisted run ids.

## First bounded local recipe

The following is a minimal native CPT recipe for an operator-supplied local Markdown source. It needs no generation model. Replace the absolute source path and stable request id with real values, and create or execute it only within the user's requested scope:

```json
{
  "project": "local",
  "action": "runs.create",
  "request_id": "operator-chosen-stable-cpt-request",
  "params": {
    "recipe": {
      "targets": ["cpt"],
      "sources": ["/absolute/path/to/operator-supplied-source.md"],
      "max_units": 2,
      "sample_count": 1
    }
  }
}
```

Creation freezes the recipe and returns its real `run_id`; it does not execute it. When execution is requested, pass that returned id to `dataforge_dispatch`, for example `{"runs":[{"id":"local-cpt","project":"local","run_id":"<returned run_id>"}]}`. Do not copy placeholder ids into a live plan. SFT, multi-turn generation, model review, and repair require their own complete node/source/model settings; do not assume this CPT example configures those paths.

## Quality checks

Inspect whole multi-turn histories, including every assistant turn and each retained explanation. Check source conditions, omitted premises, answer/explanation consistency, invalid certainty, prompt or knowledge-wrapper leakage, and whether the learner-visible question is self-contained. Document-reading tasks may refer to visible material. Knowledge-application tasks must not pretend hidden material was supplied by the user.

Inspect question lengths, language, ambiguity, roles, dialogue acts, repetition, source coverage, and revision lineage across batches. Exact duplicates, semantic near-duplicates, and repeated corrected versions are different issues. Do not automatically reject a valid short question, ambiguous request, refusal, code symbol, or rare task shape.

## Reporting

Report real run ids, completed and quarantined counts, original reasoning, unresolved issues, review coverage and method, resource waits, and artifact hashes. Distinguish measured facts from model judgments and inspection hypotheses. Show incomplete work as incomplete.
