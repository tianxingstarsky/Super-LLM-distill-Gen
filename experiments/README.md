# Isolated model experiments

This directory holds small research runners. Product code must not depend on it.
Each experiment documents its inputs, reused components, evidence, and limits.
Keep weights, conversations, caches, and generated results outside this directory.

| Goal | Entry | Scope |
| --- | --- | --- |
| Create reproducible weights and observe continuous soft-input text | [readable observation path](nplus_shared_cache/README.md#current-readable-observation-path) | Soft Thinking input-space adapter, five-route CAT and same-cache formal answer; direction specialization remains unverified |
| Load a shared-loop checkpoint and inspect five simultaneous latent heads | [nplus_shared_cache](nplus_shared_cache/README.md) | Shared-cache inference, saved vectors, and local fusion interventions; no training |
| Observe each route continuously until its own ending | [parallel text observer](nplus_shared_cache/README.md#five-continuous-text-observations) | Independent EOS, zero-padded CAT slots and full text traces; legacy three-position checkpoints require an explicit initialization probe |
| Supervise complete route text with continuous teacher feedback | [full-token continuous training](nplus_shared_cache/README.md#full-token-continuous-text-training) | Separate one-epoch experiment with complete route/EOS and answer targets; free-rollout quality requires actual observation |
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
