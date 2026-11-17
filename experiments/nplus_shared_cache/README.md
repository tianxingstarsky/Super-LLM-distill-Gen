# Five latent heads with one shared cache

`runner.py` checks a concrete information path:

```text
complete conversation -> one prefill -> shared h
    -> five residual direction heads, all reading that same h
    -> CAT (5 * hidden width) -> learned dense projection
    -> RMS alignment to input embeddings -> one new continuous position
    -> the SAME backbone and cache -> next shared h
```

The loop defaults to three positions. Full-attention keys/values and GDN
convolution/recurrent states remain in one Transformers `DynamicCache`. There
are no private branch caches. Ordinary causal attention can still read the
conversation prefix. This does not imply lossless, unlimited memory.

## Run

Use an existing environment with PyTorch, Transformers, and safetensors. The
runner was written for the official Qwen3.5 implementation in Transformers
5.23. It installs and downloads nothing.

```powershell
python experiments/nplus_shared_cache/runner.py --model path/to/local-model --auxiliary path/to/auxiliary.safetensors --messages path/to/messages.json --output path/to/private-output
```

This prepares exact prompt bytes and IDs without loading model weights or
using a GPU. `messages.json` is a standard message array or `{ "messages": [...] }`.
For several cases, use `--cases cases.json` instead. Its top-level `cases` array
contains objects with `case_id` and `messages`. Other case fields are archived
but never inserted into the prompt. Text history and supported tool messages
are preserved; media input is rejected rather than silently omitted.

[context_pair.example.json](context_pair.example.json) contains the two synthetic
conversation probes. Only the original code changes between the cases. The
date correction, contact, and question stay the same. Expected observations are
evaluation metadata and never enter the model messages. Use it with
`--cases experiments/nplus_shared_cache/context_pair.example.json`.

Add `--execute --verify-replay` to load the local model once and run the shared
loop. Each case starts with a fresh cache. Replay uses the exact realized latent
inputs, not newly predicted replacements. It reports raw maximum absolute,
relative L2, cosine, and top-token differences without an arbitrary pass cutoff.

`--decode` also hands the complete realized input sequence and unconsumed
`\n</think>\n\n` boundary to native Hugging Face `generate`. It uses the existing
cache and saves the boundary IDs. The default sampling settings are temperature
0.7, top-p 0.8, top-k 20, repetition penalty 1.0, and a 32,768-token output cap.
There is no custom sampler or presence-penalty implementation in this runner.
For a controlled comparison, call `decode_answer(..., logits_processor=...)`
with the same already-audited processor for both arms. A `STOP` file in the
case output folder explicitly interrupts decoding; partial output is preserved.
There is no implicit time budget.

To load an already-trained auxiliary checkpoint, generate its answer, and
inspect the saved vectors in the same run:

```powershell
python experiments/nplus_shared_cache/runner.py --model path/to/local-model --auxiliary path/to/training/auxiliary.safetensors --cases experiments/nplus_shared_cache/context_pair.example.json --output path/to/new-private-output --execute --decode --inspect-vectors
```

Keep `auxiliary-config.json` and, when available, `training-receipt.json` beside
the auxiliary file. `--inspect-vectors` performs local forward comparisons; it
does not train, call a model API, or generate additional answers for each
intervention. Add `--verify-replay` separately to measure cached/full-prefix
numerical differences.

## What the weights mean

`runner.py --auxiliary` is required. The five low-rank residual modules, dense
fusion, and gain load the existing keys `adapters.*`, `fusion.*`, and `gain`
strictly. All parameters are frozen for inference. The separate `training.py`
entry now trains this shared-state loop and saves auxiliary checkpoints that
the runner can load. Inference itself never performs training.

Checkpoint provenance distinguishes these cases:

- A legacy file without shared-loop architecture metadata has unverified
  training origin. Earlier independent-branch weights were used only as an
  initialization in the prototype; that evidence is not shared-loop training.
- A declared shared-loop checkpoint must match its adjacent auxiliary config:
  identity, architecture, width, rank, and requested latent-step count.
- An adjacent completed training receipt is authenticated against the weight
  metadata identity and actual auxiliary-file SHA256. Without that receipt,
  loading a checkpoint is not evidence that training completed.

This authenticates the auxiliary artifact and loop configuration. It does not
bind a digest of every base-model weight. The training manifest records config
and template identities, but complete base-weight content binding remains a
limitation. A successful same-width load alone cannot certify the same base
model, and authenticated training does not establish improved accuracy.

Hidden states and input embeddings have the same width but different roles.
The learned projection and RMS alignment are an experimental bridge, not proof
that those spaces are interchangeable. The raw CAT tensor splits back into
the five heads; its dimension-reducing projection is generally not invertible.
LM-head top tokens are diagnostic readouts, not a textual transcript of latent
thought. This is not a claim about native CoT or the base model's best ability.

## Evidence and reuse

Each case saves source messages, exact prefix bytes/IDs, each step's actual
`h`, five head vectors, CAT, projected vector, injected embedding, and resulting
`h` in safetensors. Cache records include object identity, per-layer lengths,
shapes, norms, and hashes of each full-attention layer's original prefix K/V.
GDN states are summarized, not cloned. Positions, masks, readouts, timings,
and CUDA peak memory are recorded. Original files are not rewritten.

`--inspect-vectors` reports residual and centered-head geometry as well as raw
cosines. On the last actual latent step it holds the complete prefix, earlier
latent inputs, and shared `h` fixed. Five interventions each replace one
`h + A_i(h)` with `h`, removing the learned residual while retaining the common
state. One fixed cyclic permutation rotates the five CAT slots. The original,
an identical repeat, and all interventions use the same full-prefix **no-cache**
decoder path, including the same closing boundary.

The inspection saves actual tensors and raw changes in fused vectors, RMS
alignment, BF16 injection, decoder hidden states, and first-answer logits,
including KL and top-token readouts. A measurable change can show local
influence on readout. It cannot establish that a head learned its named
semantic role, that the heads are complementary, or that the model reasons
better. No arbitrary acceptance threshold converts these measurements into a
quality score.

The loader follows the existing local Qwen loader's FP32 restoration for GDN
`A_log` and normalization weights, using their original safetensors values.
The backbone and decoder are the upstream Transformers implementation.

Public functions are `prepare_cases`, `load_frozen_model`, `load_auxiliary`,
`run_shared_case`, and `decode_answer`. Runtime objects returned by the loop
retain the single cache and realized embeddings for a thin external runner.
Do not share these live objects across cases.

Validation for changes here is Python compilation and a tokenizer-only prepare
with real local inputs. The actual GPU run and cached/full replay are explicit
experiments. No full project regression, training, or model API call is needed.

## Earlier prototype evidence and numerical limits

Two controlled conversation cases completed a real inference run with one
shared cache using the earlier independently trained auxiliary initialization.
The requested contextual facts were answered correctly, including
an updated date and an unknown fee. This is a mechanism demonstration, not an
unbiased accuracy estimate. Streamed IDs matched the returned IDs in both cases.

Cached and full-prefix computation did not match exactly. An ordinary newline
continuation showed a hidden-state relative L2 difference of about 2.7%; the
latent steps reached about 8.8%, and the closing boundary was about 1.56%.
The compared top-1 token stayed the same. The numerical difference remains
unattributed; neither equal top-1 tokens nor correct example answers establishes
strict computational equivalence. No acceptance threshold has been added.

That GPU run used runner SHA256
`5dc40d787fcaed0bd727ff7832bb3f332bd84d9ecc01ea6eb47291b8207896f4`.
Subsequent source-archive and streamed-ID guards do not change the model
calculation path. They were checked on CPU; the GPU run was not repeated.
These measurements belong to that earlier prototype run, not a validation of
the later shared-loop-trained checkpoint.

## Differentiable shared-state training

`training.py` is a separate, explicit training entry. It uses the same
`SharedDirectionHeads` and `project_shared_hidden` in `latent_feedback.py` as
the inference runner. `runner.load_auxiliary` remains a compatible entry.

```powershell
python experiments/nplus_shared_cache/training.py --model path/to/local-model --records path/to/verified-train.jsonl --heldout path/to/heldout.jsonl --initial-auxiliary path/to/original-initial/auxiliary.safetensors --initial-sha256 CHECKPOINT_SHA256 --teacher-targets path/to/existing-local-targets --output path/to/new-private-output
```

The default `prepare` action loads the tokenizer, checks the initial checkpoint
digest, freezes source identities, and writes complete token supervision. It
does not load the backbone or use a GPU. Add `--action train` to perform one
ordered epoch with native HF Trainer, microbatch one and gradient accumulation
four. The frozen Qwen backbone stays differentiable with respect to continuous
inputs. Non-reentrant activation checkpointing is enabled. No model API is used.

Training starts from the explicitly pinned **original initial** auxiliary file,
not an already-trained checkpoint. In the training forward, `use_cache=False`:

```text
h0 = Qwen(prefix)
z1 = shared_heads_and_alignment(h0)
h1 = Qwen(prefix, z1)
z2 = shared_heads_and_alignment(h1)
h2 = Qwen(prefix, z1, z2)
z3 = shared_heads_and_alignment(h2)
loss = answer+EOS CE from Qwen(prefix, z1, z2, z3, boundary, answer[:-1])
```

Every feedback vector and later hidden state retains its autograd graph. No
precomputed question features or detached latent states are used. The full
prefix is recomputed for the short feedback passes so GDN in-place caches do
not stand in for backpropagation through time. Only answer and EOS positions
are supervised by token CE; system, user, latent, and boundary positions are
masked. Capacity checks exclude complete records above 8,192 positions by
default; they do not truncate answers or teacher text. This training capacity
does not alter the separate 32,768-token generation cap.

Teacher records must retain `question`, `question_sha256`, `split=train`, the
five `paths`, `final_answer`, `structural_status=complete`, and an existing
`teacher_verification.score=1`. Exact duplicate records are merged; distinct
solutions remain. All questions in `--heldout` are excluded by identity and
normalized text, including alternate solutions of the same question.

Existing `targets.npz` files can be reused by `record_key`; their shape and
origin are checked and their bytes are pinned. Missing targets are encoded
locally before optimization. Each of the five complete teacher texts is encoded
by frozen Qwen. The mean of the final 16 states at three token quantiles forms
the `[3,5,hidden_width]` proxy target. These are **local Qwen representation
targets**, not DeepSeek hidden states. Per-head cosine and relative-direction
alignment supplement the final-answer CE with weight 0.2. Three proxy positions
do not establish faithful compression of a long CoT or readable thought tokens.

The first actual training forward separately records final-answer-CE-only
gradients to all three `z` inputs, `h1/h2`, five head parameter groups, and fusion.
A fixed-probe `z2 -> z1` VJP records the recursive dependency. These diagnostic
gradients add no optimizer step. Normal Trainer backward then applies the total
loss. First-step parameter deltas and a final auxiliary checksum provide
separate evidence of updates. A failed or absent gradient is not a successful
training result.

Trainer owns optimization, scheduling, accumulation, and checkpoints. Resume
requires the same source/code/config identity and an authenticated checkpoint.
Observed updates newer than the latest checkpoint are not silently replayed.
A completed training receipt prevents an accidental second epoch. This entry
does not run an accuracy evaluation; native CoT, initial shared-loop, and trained
shared-loop evaluation remain distinct experiments.

A fresh shared-loop training run has completed on 128 records from 126
questions: 32 optimizer steps and one actual epoch. It trained the auxiliary
heads and fusion while keeping the Qwen backbone frozen. This establishes
that the new feedback architecture was trained, rather than merely loading
the earlier prototype weights. It does not establish a benchmark improvement.
Mathematical accuracy evaluation is stopped for this phase, and baseline
comparisons are deferred. Current inspection commands add no training.

## Upstream references

- [Transformers cache interface](https://huggingface.co/docs/transformers/main/en/cache_explanation)
  supplies the actual cache and generation machinery used here.
- [Coconut](https://github.com/facebookresearch/coconut/blob/main/coconut.py)
  provides a continuous-hidden-feedback reference. Its older tuple-cache code
  is not imported or treated as a drop-in Qwen hybrid-cache implementation.
