# Dataset boundary

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

Cross-context callers use `datasets/contracts.py`. The SQLite corpus index lives
in `datasets/infrastructure/corpus_index.py`; its storage policy must not change
the pure near-duplicate matching rules.
