# Isolated model experiments

This directory holds small research runners. Product code must not depend on it.
Each experiment documents its inputs, reused components, evidence, and limits.
Keep weights, conversations, caches, and generated results outside this directory.

| Goal | Entry | Scope |
| --- | --- | --- |
| Load a shared-loop checkpoint and inspect five simultaneous latent heads | [nplus_shared_cache](nplus_shared_cache/README.md) | Shared-cache inference, saved vectors, and local fusion interventions; no training |
| Train the same shared-state loop for one ordered pass | [shared-state training](nplus_shared_cache/README.md#differentiable-shared-state-training) | Explicit local HF Trainer experiment; frozen backbone and trainable feedback |

These experiments use explicit local paths. They do not change original model
weights, production tasks, or dataset exports. Training writes new auxiliary
checkpoints only when explicitly selected. Run only the validation described in the
experiment README; a mechanism check is not a quality benchmark.

The shared-loop experiment now includes a completed one-epoch auxiliary
training run and authenticated loading of its checkpoint. Earlier prototype
evidence remains separate. Mathematical accuracy evaluation is stopped for
this phase; baseline and accuracy comparisons are deferred. Vector inspection
does not launch training or establish a quality improvement.
