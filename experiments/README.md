# Isolated model experiments

This directory holds small research runners. Product code must not depend on it.
Each experiment documents its inputs, reused components, evidence, and limits.
Keep weights, conversations, caches, and generated results outside this directory.

| Goal | Entry | Scope |
| --- | --- | --- |
| Inspect five simultaneous latent heads with one shared Qwen cache | [nplus_shared_cache](nplus_shared_cache/README.md) | Inference mechanism and cached/full-prefix comparison; no training |

These experiments use explicit local paths. They do not change model weights,
production tasks, or dataset exports. Run only the validation described in the
experiment README; a mechanism check is not a quality benchmark.
