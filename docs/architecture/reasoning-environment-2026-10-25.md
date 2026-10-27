# Agent environment protocol: offline validation

This change introduces checklist protocol 5 and recipe 27. It leaves protocols 1–4, historical prompts, task files, caches and training exports intact. No provider API was called for this validation. New protocol defaults apply to new configurations; copied configurations retain their explicit version.

## Responsibilities

- `reasoning_environment.py` binds the phase and active requirements. Only the exact shipped conditional scope rule is decomposed automatically. Custom text is never assigned guessed applicability; explicit `[prefix]` / `[final]` prefixes are supported by the version 5 text parser.
- `reasoning_environment_tools.py` supplies stable requirement IDs and a common citation shape. The legacy `field` alias becomes `source` only when the alias is unambiguous; text, source identity and votes remain unchanged and the repair is recorded.
- `reasoning_workspace.py` retains immutable evidence, distinguishes invalid tool inputs from candidate defects and unresolved evidence, and caches local operations within the frozen binding.
- `reasoning_assertions.py` adds limited comparison obligations. Explicit adjacent percentage/half comparisons have deterministic receipts. Different referents, cross-sentence references and source-wide absence claims without sufficient scope remain unresolved. These detectors are not general semantic proofs.
- `reasoning_session.py` exposes current state in initial input and tool feedback. A reviewer can choose tools in any order. Repeated unchanged work terminates without passing; stopping with missing work is an unresolved result, not a malformed-tool retry.
- `GenerateCot` gives identical full review inputs a stable checkpoint identity across generation attempts. Changed text, task or rules invalidate the identity. Version 5 failure records retain full reasoning and answers.

No reviewer can edit the candidate, grant itself a waiver, turn unknown evidence into approval or overwrite a committed negative. Blind independent review remains in place. A direct deterministic comparison failure does not need another model vote to confirm arithmetic. Positive admission still requires complete active-rule and claim coverage.

## Reproducible offline comparison

Run:

```powershell
.venv/Scripts/python.exe scripts/replay_reasoning_environment.py --output .dataforge/software-ops-2026-10-24/agent-environment-offline
```

The runner rejects network connections. It uses five saved V4.1-Flash review sessions from `tests/fixtures/reasoning_agent/environment_sessions.json`. The fixture records the original report SHA-256. Original candidate text and tool arguments are retained. Version 5 wire fields are explicitly projected; this is not a new model response and does not measure model accuracy or predict spending.

| Observation | Old protocol | New protocol |
| --- | --- | --- |
| Rejected tool entries across the saved calls | 20 | 10 |
| False rejection of the final-stage scope check as a forbidden waiver | 1 | 0 |
| Case 01 independent review, including an unresolved “less than half” assertion | Passed | Unresolved; cannot export as passed |
| Two complete case 02 review sessions | Both passed | Both still pass |
| Three erroneous explicit comparisons in controlled probes | All admitted by optimistic votes | All blocked |
| Three correct explicit comparisons in controlled probes | All admitted | All admitted |
| Scripted repeated identical read: turns until unresolved stop | 3 | 2 |
| Candidate reasoning and answer bytes | Original | Identical |
| New provider requests | — | 0 |

Tool-entry totals include rejected evidence mutations and incomplete citations. A valid tool submission is not a correctness verdict. The replay deliberately retains all recorded calls, including calls a newer scheduler could stop before issuing. The scheduler probe uses the production session loop with fixed offline responses; it is not a paid performance experiment.

The previously missing `workflow.cot_check_agent` experience-role mapping and recipe 26 runtime support were also repaired. Their absence was found while checking compatibility rather than by weakening tests.

## Validation and remaining scope

The affected-domain regression covers 943 tests, followed by final targeted regression after the last changes. Machine-readable results are in the local report directory. An exploratory full-repository run was stopped before completion; it is not reported as passing. Its early role-mapping failure was fixed and included in targeted verification.

The report presents complete candidate questions, reasoning, answers, original/projected tool calls, applicable clauses and verification receipts. It distinguishes genuine failure, unresolved evidence and incomplete coverage. Screenshot launch was blocked by automatic approval policy; no screenshot-based visual acceptance is claimed.

General entailment, long-range reference resolution, broader numerical language and actual model behavior remain empirical questions. This release does not claim they are solved by lexical detectors, longer prompts or repeated votes. Further live comparisons require separate authorization to resume API use.
