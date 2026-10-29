---
name: shujian-cube
description: Inspect, create, resume, and monitor saved ShuJian Cube training-data workflows through its local control interface. Use for data-generation supervision and scheduling, not general coding tasks.
---

# ShuJian Cube orchestration

Use the user's installed ShuJian Cube and its existing model connections. This
skill is an external controller. It does not implement generation, scoring, or a
second scheduling system.

## Connect

Locate the product installation supplied by the user. Its root contains
`lib/control.py` and a ready `.venv`. From this skill's directory, run:

```text
<product-python> scripts/control.py --root <product-root> --action capabilities
```

Use `.venv/Scripts/python.exe` on Windows or `.venv/bin/python` elsewhere.
The helper can run from any working directory when its script path is absolute.
It creates UTF-8 JSON itself. It does not call a model for connection checks.
Read [the control reference](references/control.md) when selecting parameters or
creating a recipe. Do not guess unavailable actions from another agent system.

## Choose actions from current evidence

Start by inspecting the relevant saved runs and configured models. Follow the
user's objective and actual state: reuse a frozen run, resume its checkpoints,
inspect a failure, create a new recipe, or summarize results. A monitoring-only
request needs no new run. A completed request is not a reason to regenerate.

Configure prompts, models, source handling, reasoning style, and review in the
corresponding recipe nodes. Reuse prepared sources. Requested sample counts are
ceilings, not permission to manufacture repetitive examples.
Preserve the user's specified model and protocol when diagnosing a failure.

Run independent workflows in separate managed terminal sessions only when their
data dependencies permit it. Keep each process handle and persistent run ID.
Use the core's shared model admission; inspect `models.status` when a model is
waiting. A dependency between jobs does not transfer source data. Put explicit
source references in a downstream recipe, and wait for successful prerequisites.

For long work, the caller owns the live terminal session. Read durable events or
model-stream deltas from another control request. Reuse returned byte cursors;
do not rescan entire histories every time. After a controller restart, inspect
the persistent run ID before resuming. Cancellation requests are not proof that
a worker has exited.

Use `--allow-mutations` for an action that changes state only when it is within
the user's authorized task. Creating or running data may use a paid model.
For `runs.create`, choose and retain one stable `--request-id`; retries of the
same recipe reuse that ID. Changed requirements need a new recipe and identity.
Do not edit frozen files to reroute a live run.

## Preserve outcomes and limits

Act on tool failures without silently treating them as completed work. Distinguish
invalid configuration, unavailable dependencies, model waits, cancellation,
incomplete output, and quality findings. Do not repeatedly retry the same failing
action without new evidence or a relevant change.

Inspect the full learner-visible conversation, answers, retained reasoning, and
source conditions when judging samples. A tool receipt or high model score is
not semantic proof. Keep unresolved findings visible. Do not remove reasoning,
invent missing premises, relax quality settings without authorization or
disclosure, or count a superseded parent and its correction as separate accepted
examples. Apply the user's explicitly chosen quality rules to a new recipe.

Report the actual run IDs, state, useful outputs, inspection coverage, usage,
unresolved issues, and next action. Do not copy credentials into commands or
reports. Availability of a tool does not authorize unrelated work.
