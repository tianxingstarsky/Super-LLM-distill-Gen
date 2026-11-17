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

## What the weights mean

`--auxiliary` is required. The five low-rank residual modules, dense fusion, and
gain load the existing keys `adapters.*`, `fusion.*`, and `gain` strictly. All
parameters are frozen. An auxiliary checkpoint trained with independent
branches is only an **initialization** for this shared-state loop. This runner
does not train this new architecture or establish an improvement in accuracy.

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

## Current mechanism evidence and numerical limits

Two controlled conversation cases completed a real inference run with one
shared cache. The requested contextual facts were answered correctly, including
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

## Upstream references

- [Transformers cache interface](https://huggingface.co/docs/transformers/main/en/cache_explanation)
  supplies the actual cache and generation machinery used here.
- [Coconut](https://github.com/facebookresearch/coconut/blob/main/coconut.py)
  provides a continuous-hidden-feedback reference. Its older tuple-cache code
  is not imported or treated as a drop-in Qwen hybrid-cache implementation.
