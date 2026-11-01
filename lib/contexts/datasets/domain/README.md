# Dataset boundary

`preferred_training_record` keeps raw reasoning by default because review,
repair and candidate receipts also use it. Actual export writers opt into the
v3 annotation view. `without_control_text_metadata` removes only program-owned
v3 configuration fields from known candidate/message containers before model
requests; it does not strip prose, roles, tools or reasoning, and legacy-only
request shapes remain unchanged.

`control_text.py` owns the pure `ControlTextAdapter`: validates and freezes explicit
literal rules and returns an in-memory view with original-span receipts. It performs
no I/O or model calls. The public API exposes defaults, validation and local preview.
Infrastructure's compatibility file forwards to this single implementation.
New configurations use `shujian.control-text-adaptation/v3`. Mark-only mode
wraps exact original controls in `<reason-strength>...</reason-strength>`;
`annotation_tag` can select another simple tag name. The inner words and the
rest of the reasoning remain unchanged. Repeated reads do not nest tags.
Only the leading `prefix_char_limit` source characters are eligible (default 512,
configurable from 64 through 2048). Recognized wrapper characters do not consume
that source budget, so another read cannot push a near-boundary control out of
scope. A match or complete wrapped block crossing the boundary stays intact.
Later wording and tags are not interpreted; unknown wrapper examples count in
full toward the prefix rather than expanding it. Disabled adaptation is identity.
Complete unrecognized tag contents remain untouched; incomplete or nested matching tags
fail explicitly instead of being guessed or removed. The receipt retains original
Unicode spans and marker identities in `source_control_annotations`. Only an
explicit target replacement removes the wrapper and substitutes its content.
`v2` keeps its original text plus metadata-only annotations; `v1` keeps its
historical placeholder behavior and receipt shape. Stored tasks never migrate
silently. `SCHEMA`, `ANNOTATION_SCHEMA`, and `PAIRED_SCHEMA` identify those fixed
contracts; new defaults use `CURRENT_SCHEMA`. `mark(..., task_contents=...)`
shares `apply`'s task-reference protection without applying target replacements.
The v3 boolean `review_suggestions` is metadata for the review boundary, not a
matching instruction. New defaults enable it; a missing field remains absent
and means disabled for previously saved configurations. The reviewer cannot
alter this adapter's frozen rules through this metadata alone.
`Qwen35TrainingEncoder` applies the record's frozen node rules at training read time;
explicit caller configuration takes precedence. Source records and quality status
remain unchanged. Controls are not inferred from natural-language descriptions.

`control_text_projection.py` selects reasoning fields for the same adapter's
mark-only view. Explicit v3 rules reach native JSONL and TRL exports, including
each assistant turn and known-author preference responses. It never performs
target-model replacements or changes answers/evidence. Message-owned rules take
precedence over record defaults; unknown-author sides receive no guessed rules.
Task quotations are checked against that turn's prefix, not future messages.
An already marked export stays stable through another format conversion.
`with_source_control_text` carries only known source assistants' v3 rules into
both preference-generation routes. Rules follow the answer through ranking;
the independently generated alternative receives no inferred configuration.

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
