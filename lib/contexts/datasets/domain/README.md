# Dataset boundary

The existing `Qwen35TrainingEncoder` in infrastructure resolves the exact
`[[SJ_REASONING_EFFORT]]` reference while encoding the supervised assistant
turn. The target encoder owns its wording; this domain adds no replacement
service. Source records and quality status remain unchanged. Controls are not
inferred from natural-language descriptions.

New canonical generation receipts opt into `training_control` metadata at the
training projection. The versioned control uses either one `effort` or
`by_message` with assistant-message indices. Older rows without canonical
receipts keep their exact prior export shape; native provider effort is not
retrospectively guessed. The loader rejects unlabelled turns unless the caller
explicitly supplies a default. Existing quality and eligibility checks remain.

`reasoning_import.py` adapts explicit NeMo and OpenThoughts result envelopes.
It decodes complete markers without inventing reasoning or trusting upstream
scores. The source-library adapter owns file reads and recipe-version gating.
The pure contract is exposed by `datasets/contracts.py`.
Verification: `tests/test_cot_style_transfer.py`.

A dataset version consists of native training rows, their evidence, and a
manifest. File verification and publication must refer to the same version.
An unfinished execution is not a dataset version. Explicit partial exports
must agree in both run state and manifest.

`assets.py` classifies library sources, final files, packages and execution
records. The default library excludes checkpoint/stream/stage directories
before traversal. Runtime files never become training-file counts just because
their filename contains SFT or CPT. Task identity comes from a validated path;
file readiness still requires the existing delivery checks.

`delivery.py` owns readiness rules. `trainer_formats.py` owns export schema
rules. `distribution.py` owns native-record counting rules. File I/O, ZIP,
hashing and worker jobs belong to infrastructure. No model is called here.

`training_projection.py` projects reviewed records into training payloads. It
preserves the configured reasoning representation and keeps evidence separate
from learner-visible messages. A new format must use the same projection rules,
not copy them into a download handler.

`training_control.py` owns the explicit learner-control contract
`shujian.training-control/v1`, with effort `low`, `medium`, `high`, `xhigh` or
`max`. `with_training_control` makes a deep copy and rejects conflicting labels.
Teacher request effort, response length and legacy explanation depth never imply
a learner label. Missing controls fail unless a caller explicitly chooses a
default for a read-only legacy view; malformed existing controls still fail.
Legacy rows retain their existing projections, hashes and artifacts. Only rows
with explicit versioned canonical controls opt into the new metadata.
Verify control validation, immutable source records and legacy-default behavior
alongside the lazy training-loader regression and DDD boundary checks.

Cross-context callers use `datasets/contracts.py`. The SQLite corpus index lives
in `datasets/infrastructure/corpus_index.py`; its storage policy must not change
the pure near-duplicate matching rules.
