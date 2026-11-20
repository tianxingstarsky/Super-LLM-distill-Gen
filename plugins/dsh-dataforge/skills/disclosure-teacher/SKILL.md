---
name: disclosure-teacher
description: Improve the local guided-answer teacher's disclosure policy from real student attempts and retries. Use for near-real-time teacher feedback, evidence collection, adopting prompt experience, and verifying which teacher received it.
---

# Disclosure teacher

Use the product checkout and its Python environment. This loop updates the
teacher's **prompt experience** from learner outcomes. Model weights stay fixed.
Run commands at the product root. On Windows use its `.venv/Scripts/python.exe`;
on Unix use `.venv/bin/python`. Below, `python` means that interpreter.
These commands need a host terminal that can reach that checkout. When only
the two DataForge control tools are available, report the missing terminal
access. Their control protocol has a different set of actions.

## Read the evidence

Start with the library the operator wants to use. To inspect a new or existing
library and collect completed records from a guided run:

```sh
python -m lib.guided_teacher_feedback status --library LIB
python -m lib.guided_teacher_feedback observe --library LIB --run RUN --input ORIGINAL.jsonl
```

`RUN` is the original guided-answer run directory. `ORIGINAL.jsonl` is its
exact input shard, including rows outside the selected cases. Use repeated
`--case-id ID` to investigate particular cases. Observe again as new
attempts finish. Start from saved case files and request receipts. Keep
student drafts, teacher proposals, published disclosures, and subsequent
student responses attributed to their actual authors.
When several original shards are available, observe each run with its own
input file into the same library. Keep the evidence and gaps for each
kind of task visible.

If the run reused a student reply, supply the original parent run directory
with `observe --source-run PARENT`. Repeat the option for additional original
parents. The original receipt authenticates the shared student checkpoint.
Count the original model call once. A missing parent leaves that record
unconfirmed.

Read `awaiting_case` as input rows with no completed case archive yet.
`by_category` counts observed outcomes. `eligible_unconsumed` counts events
available to a new learning batch. Re-reading a receipt must not increase that count.
An `unverified_receipt` is evidence to investigate. Keep it out of learning
and continue with authenticated cases. Save the observation with `--output`
when the operator wants to examine individual events and their receipt links.

A helpful disclosure has a downstream consequence. Look for the student
correcting a wrong inference, adding a supported intermediate result, or
delivering a sound solution after the hint. Repeated mistakes, a wrong rule,
or loss of a previously correct step supply negative evidence. A teacher
proposing a hint, approving it, or writing a fluent review does not establish
its effect. If no student received the hint, the next reply is absent, or the
result is merely a matching number with a broken explanation, record the limit
of the evidence. A stronger student's answer has its own provenance. Keep the
denominator and the originally assigned student visible when comparing runs.

## Learn and apply

For an authorized local update, use an already configured **loopback** backend
and the model it actually serves. The local guide has no separate dispatcher.

```sh
python -m lib.guided_teacher_feedback learn --library LIB --backend BACKEND --model MODEL --output CANDIDATE.json
python -m lib.guided_teacher_feedback status --library LIB
```

The default learning batch waits for 8 new items and uses up to 16. Adjust
`--min-new` and `--batch-size` for an explicitly small pilot. Keep observing
if the library has too little new evidence. For an explicit local server,
replace `--backend BACKEND` with `--local-url URL` (including `/v1`) and
supply its served model ID. If another checkout owns the local slots, pass
`--runtime-root` pointing to that checkout. To extend an earlier teacher
edition, pass its ID as `--parent`. Keep the returned call receipts.

Read the learning receipt. `no_update` and `insufficient_new_evidence`
produce no edition to try. `candidate_source_overlap` means the learned text
carries expressions from a source task. It is blocked even for a trial.
Keep its receipt. `learn --reconsider ID` is available for a blocked older
edition only after the representation of observed behavior has changed.
Write the new learning receipt to a separate output path.

For a `candidate`, prepare a **trial** edition for inspection and an
independent comparison. Supply `--base BASE.json` to carry forward the
other role settings from existing experience.

```sh
python -m lib.guided_teacher_feedback adopt --trial --library LIB --edition ID --output EXPERIENCE.json
```

Read the `disclosure_teacher` text in that file against its originating
events. A useful change transfers across tasks, preserves student progress
and points to a grounded next move. Set aside rules that encode a particular
answer, overstate causation, or give away the solution as the next hint.
Use this file only for an authorized comparison. Choose held-out tasks
outside the evidence that produced the candidate. Use the same tasks,
student checkpoint before the hint, and run settings for both parent and trial.
For a direct first attempt, use the guided runner's `--reuse-first-from`
with the original control run. For a keyed worked solution, use
`--reuse-worked-first-from` with the same original input shard. Supply
the control as a source run when observing the trial.
Reuse the normal `python -m lib.guided_answers` invocation with a **new**
`--output NEW_RUN` and `--experience EXPERIENCE.json`. A frozen or resumed
run continues with its original edition. Preserve the old experience,
evidence library, and run receipts.

## Verify and recover

```sh
python -m lib.guided_teacher_feedback verify --run NEW_RUN --experience EXPERIENCE.json
```

Check the actual teacher system message and its completed model receipt.
`applied` names real teacher requests. `pending_teacher_request` means
none has been verified. For a specific case that is still running, pass
`--case-id ID`.
An exported experience file alone proves serialization. A verified request
proves delivery. Student improvement needs new student responses and a check
of their reasoning and final answers. Record all three observations separately.
If the task was answered before a teacher turn was needed, report that the
teacher has not been observed. No extra model call is needed to prove absence.

Observe both comparison runs into the same library. To adopt outside a
trial, use `adopt` again without `--trial`, writing a **new** file. The CLI
requires improved checked outcomes on at least two distinct held-out cases,
matching the task contract and first response, with no observed regressions.
Inspect the paired responses and complete explanations before claiming a
quality gain. Report the sizes and subject coverage of the comparisons.
If there are no comparable pairs, keep the prior edition in production.

For rollback, use a previously adopted frozen file or export `initial` to
another new file with `adopt`. Use that edition in subsequent runs. Stop on
insufficient evidence, an unavailable
local model, inconsistent receipts, or a new run that carries the wrong
edition. Preserve the failing identifiers for inspection. Never repeat live
training-data generation simply to fill the feedback library.
