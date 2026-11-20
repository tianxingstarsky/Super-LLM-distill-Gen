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

If learning returns an `edition_id`, export it to a fresh path for inspection.
Supply `--base BASE.json` to carry forward other settings from existing
experience. A run with no new edition needs no adoption.

```sh
python -m lib.guided_teacher_feedback adopt --library LIB --edition ID --output EXPERIENCE.json
```

Read the `disclosure_teacher` text in that file against its originating
events. A useful change transfers across tasks, preserves student progress
and points to a grounded next move. Set aside rules that encode a particular
answer, overstate causation, or give away the solution as the next hint.
Use the chosen experience in the next independently authorized guided run.
Keep its source, sampling, teacher, student, and other settings comparable.
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

For rollback, adopt the earlier edition (or `initial`) to another new file
and use it in subsequent runs. Stop on insufficient evidence, an unavailable
local model, inconsistent receipts, or a new run that carries the wrong
edition. Preserve the failing identifiers for inspection. Never repeat live
training-data generation simply to fill the feedback library.
