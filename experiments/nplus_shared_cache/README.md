# Five latent heads with one shared cache

This is an isolated research experiment. The primary goal is continuous hidden
vectors with text used only as an observation window. Complementary reasoning
directions and useful learned fusion have **not** been established. Two full-text
variants each completed an independent one-epoch run and failed readable free
generation. Successful optimization or EOS is not quality approval.

## Primary experiment: hidden feedback, text observation only

The default runner and observer retain this information path:

```text
complete conversation -> one prefill -> shared h
    -> five continuous residual head vectors
    -> masked feature CAT -> dense fusion -> RMS alignment
    -> one continuous input position -> the SAME backbone and cache

head vectors -> observation map / LM head -> visible text and ending detection
```

The vocabulary distribution and displayed token embeddings do not feed the
primary feedback path. Text is an observation, not a replacement for the hidden
vectors. An ending signal may deactivate a route; its later CAT slots are zero.
Keep `--soft-embeddings` absent for this experiment. The current direct-hidden
checkpoints still produce poor free text; readable text and direction diversity
remain unresolved. A readable result from a different feedback space cannot be
reported as completion of this goal.

The legacy `runner.py` loop defaults to three positions. Full-attention
keys/values and GDN convolution/recurrent states remain in one Transformers
`DynamicCache`. There are no private branch caches. Ordinary causal attention
can still read the conversation prefix. This does not imply lossless,
unlimited memory. The variable-length observer is documented below.

## Recurrent pure-hidden experiment

The v4 research variant adds private continuous route state and a shared native
PyTorch GRU cell. Each route retains its previous width-D vector and a 512-wide
memory; this is not five backbone caches. Known direction descriptions are
encoded by the unchanged local Qwen without answers. They condition each route
along with its learned route code and the last committed shared state.
The current initialization also recomputes each route's last hidden state from
the original question/dialogue plus its direction instruction, then adds the
learned initialization residual. These frozen, uncached forwards retain no
branch cache. They do not read a cached teacher hidden state or any path token.

```text
own previous vector + own GRU memory + known direction + shared h
    -> five new continuous vectors -> masked CAT -> fusion/RMS
    -> commit 8 positions into one Qwen cache -> next shared h

predicted vectors -> frozen native LM head -> observation / ending signal
```

The last committed shared state stays constant within a block of eight route
updates. Two committed blocks retain one gradient graph before backward and
history detachment. This gives the second block's token loss a real path through
Qwen to the preceding fusion. It changes the global feedback cadence from the
older per-position experiment; it is not claimed equivalent to that experiment.
The cache retains all history values after gradient truncation.

```powershell
python -m experiments.nplus_shared_cache.recurrent_runtime --action prepare --model path/to/local-model --prepared path/to/pre-target-teacher-cache --initial-auxiliary path/to/v2/auxiliary.safetensors --output path/to/new-recurrent-run
python -m experiments.nplus_shared_cache.recurrent_runtime --action capacity --model path/to/local-model --prepared path/to/pre-target-teacher-cache --initial-auxiliary path/to/v2/auxiliary.safetensors --output path/to/new-recurrent-run
python -m experiments.nplus_shared_cache.recurrent_runtime --action train --model path/to/local-model --prepared path/to/pre-target-teacher-cache --initial-auxiliary path/to/v2/auxiliary.safetensors --output path/to/new-recurrent-run
python -m experiments.nplus_shared_cache.recurrent_observer --model path/to/local-model --checkpoint path/to/new-recurrent-run/training --cases experiments/nplus_shared_cache/context_pair.example.json --output path/to/new-observation --execute
```

Preparation authenticates the complete existing pre-target cache, source
records, tokenizer, base shards and warm-start fusion. Training makes one
ordered pass, at most one epoch. It freezes the pretrained backbone and native
LM head; only the new recurrence/initialization and existing fusion/gain train.
It warms only fusion/gain from the v2 auxiliary, not failed text-decoder weights.
Teacher vectors never become recurrent or backbone inputs. They provide native
hidden-state alignment targets, text labels and training lifetime masks.

Each training unit uses its actual predicted route vectors and live cache. The
full final answer and EOS remain supervised through that cache. Native PyTorch
`save_on_cpu` offloads the long answer's saved autograd tensors without deleting
answer positions. The native SDPA math backend is selected for training and
observation after a real CUDA fused-attention mask-stride backward failure.
No Qwen attention or GDN equations are reimplemented. The small cache subclass
only protects saved convolution buffers and publishes recurrent states without
in-place modification of earlier graphs. This adapter currently requires the
reviewed Transformers 5.23 Torch fallback; optimized kernels are not verified.

`capacity` performs zero optimizer steps on the longest answer's first two
predicted blocks plus its complete answer. It verifies resource capacity and
gradient paths, not a complete trajectory or answer quality. `validate` does
the corresponding check on the first record. Training histories, source
snapshots, actual parameter changes and one-epoch completion are archived.
Repeated execution of a started run is rejected, including after failure;
there is no silent restart or second epoch.

An explicit recovery can select a completed auxiliary checkpoint and the
interrupted run's failure receipt with `--continue-from path/to/checkpoint`
and `--continuation-failure path/to/training/failure.json`, using a new output.
The input contract authenticates the parent sources, progress and checkpoint.
It skips every already-seen record, including the remaining part of an
interrupted record, to stay below one pass. This is not exact resume: optimizer
state resets and updates newer than the checkpoint are lost and reported.
Latest auxiliary weights are now saved after each completed record. Partial
records and lost updates cannot be counted as a fully retained training pass.

During inference, every route ends on its own observed EOS or thought-end.
Its ending-round vector is committed; subsequent CAT slots are exactly zero.
No all-padding round is added. Text IDs and vocabulary distributions never
enter recurrent or backbone feedback. The formal answer starts from the same
cache only once all routes have ended. This pure-hidden representation has
not consumed the displayed ending's token embedding, so the native thought
boundary is explicitly inserted before answer generation.

Training uses gold lifetime masks whereas free observation uses autonomous
endings. Online truncated gradients also leave cache values created by earlier
parameter versions. These are explicit limitations, not full-trajectory BPTT
or a fixed-weight rollout during optimization. Distinct vectors or route names
do not establish meaningful direction specialization. Readability, natural
completion and actual direction content require the saved free texts.

`recurrent_observer --contextual-initialization` is an optional inference-only
intervention. It recomputes five initial hidden vectors from the complete
original messages plus each known direction, then discards the temporary
uncached forwards. It never reads teacher paths or observed text. The ordinary
shared cache, recurrent weights and stopping rules stay the same. This replaces
the learned initialization with one not used during optimization; any result
must be labelled an initialization intervention, not learned specialization.

Current checkpoints declare
`route_initialization=native_question_and_direction_hidden_plus_learned_residual`;
their observer automatically follows the trained initialization instead. The
earlier v4d/v4e checkpoints used only the common shared state plus a learned
goal residual. Switching the initialization on unseen remaining training
records is recorded as a curriculum change, not a uniform-protocol full pass.
There is no replay of previously used records to train this change.

The scoped recurrent checks use a real tiny HF hybrid backbone for cached
block/single-step values and gradients, route memory isolation and EOS padding.
A Windows check also holds a real file handle while exercising bounded atomic
replacement retries:

```powershell
python -m unittest experiments.nplus_shared_cache.test_recurrent_feedback
```

### Actual recurrent result and unresolved quality

The first recurrent campaign consumed less than one pass: 0.993115 record-epoch
equivalents, including work before two interruptions. Already-seen records were
not replayed. The final continuation completed 91 unseen records, taking about
18.6 minutes with a CUDA allocated peak of 10.46 GiB. Earlier interrupted work
and zero-update diagnostics are separate. Rollbacks lost 119 and then 5 updates;
this is not a fully retained, uniform-protocol pass over all 128 records.

Two real observations reached autonomous endings on all routes. The training
reconstruction ended at rounds 329/471/773/237/945; the known conversation probe
ended at 90/111/83/85/87. Recorded inactive CAT slots were zero and each formal
answer used the same live cache. The final arithmetic value and conversation
facts were correct. These are mechanism checks, not an accuracy benchmark.

**Readable complementary reasoning failed.** The reconstruction emitted space
token 220 in 2,726 of 2,755 observed tokens (98.95%), versus 4,601 of 124,078
teacher target tokens (3.71%). Its five windows contain almost only whitespace
and a few repeated words. The conversation windows also collapsed. Independent
endings and different vectors do not turn these into five meaningful directions.

A separate, frozen post-hoc decoder probe replayed the full hidden trajectory.
Its six archived tensor checkpoints matched exactly. Four single-route views
produced generic solution text, one returned only EOS, and a same-length zero-CAT
control also answered 105. These texts are not the original observed CoT, and
they do not establish faithful decoding or direction specialization. Nor does
that control prove that the vectors contain no information. Raw failures remain
preserved. No further optimization or paid model API calls were used for it.

`--diagnostic-answer-on-partial` is an explicit observer-only option for a future
unfinished run. It preserves all unfinished route flags and labels the answer
diagnostic, never a completed workflow. Normal observation requires all route
endings before the answer. Both actual observations above ended naturally;
neither needed the partial-run option.

### Matched route-swap probe

`route_swap_probe.py` tests the trained v4f checkpoint without optimization.
Three synthetic pickup dialogues each differ by one weekday token; both swap
directions retain the recipient's full prompt and unchanged contact. Only the
first eight continuous writes at the fusion input are replaced. Private states
are not transplanted. Subsequent hidden states and shared-cache values are
recomputed. Interventions reuse the recipient's baseline lifetime masks to
keep the number of positions and active slots identical in each comparison.

```powershell
python -m experiments.nplus_shared_cache.route_swap_probe --model path/to/model --checkpoint path/to/training --fixtures experiments/nplus_shared_cache/route_swap_cases.json --output path/to/new-probe
python -m experiments.nplus_shared_cache.route_swap_report path/to/new-probe
```

The two independent answer cues score complete weekday and surname tokens.
The HTML uses pre-rounding two-token log-odds; the raw receipt also includes
native BF16 output margins. All controls use the same number of positions:
self-transplant must match exactly, all-slot transplant measures the combined
channel, and zero CAT retains the fusion bias. A positive change toward the
donor does not by itself reverse the answer. Effects under these prompts test
use of redundant information, not whether every route encodes the date.

In the three-pair run, all six unchanged inputs preferred their original
weekday. At matched recipient endings, single-slot transplantation reversed
none of 30 binary weekday choices; all 30 still preferred the correct surname.
The largest absolute single-slot weekday margin change was 0.0553 (rounded up),
versus a minimum baseline weekday margin of 4.916. All five slots transplanted
together also reversed none of six choices. Self-transplants were exact at both
horizons. This first-block intervention did not establish weekday-specific
route specialization. Later writes and tasks that need derived information
remain untested.

### Frozen decoder: one or five positions per step

`handoff_probe.py` separates trajectory generation from answer readout. The
source is the existing trained CAT loop throughout. It reuses an archived full
arithmetic trace and collects the six matched pickup traces once; each decoder
arm then sees exactly the same source vectors, endings and question. No arm
feeds its answers back into the trajectories.

```powershell
python -m experiments.nplus_shared_cache.handoff_probe --model path/to/model --checkpoint path/to/training --replay path/to/frozen-readout-probe/replay --fixtures experiments/nplus_shared_cache/route_swap_cases.json --output path/to/new-handoff
```

The original input at each step is a projection of the concatenated slots.
The probe splits that trained projection into five contributions, sharing the
bias among active slots and preserving the common normalization. Before BF16
rounding, their sum must reconstruct the original vector. The decoder reads
these contributions as five consecutive positions. A length-matched control
divides the same fused vector among the same active slots; other controls
reverse active contributions, repeat the fused vector, insert zeros, or omit
the latent prefix. Ended slots stay zero, but an explicit zero position still
advances the decoder cache. Five positions cost five times as many latent
sequence positions per step.

Results are conditional log probabilities of the specified alternatives,
given the original prompt and a fixed answer cue. They are not free-answer
accuracy. Only the one-position interface was trained with the recurrent
heads. This inference-only comparison cannot decide which interface would
learn better under matched training.

Actual readout on these seven inputs: every arm preferred the specified
weekday and surname in all six dialogues and 105 over 104 on the archived
arithmetic item, even without latent inputs. Relative to equally splitting
the fused vector across the active positions, giving the five projected
contributions separately reduced the weekday margin on six of six dialogues
(mean change -0.333) and increased the surname margin on five of six (mean
change +0.073). There was no consistent advantage. Maximum relative error
when summing the contributions in FP32 was below 0.000002. The successful
local run covered 56 case/arm combinations in 181 seconds with no updates.

An additional `blind_handoff_probe` control removes the source facts from the
answer decoder's text. It reuses the saved dialogue tensors with a common
query and the same first 64 all-active steps on both sides. Run it as above,
substituting `--handoff path/to/completed-handoff` for `--replay` and a fresh
output directory. The model favored the same weekday on both variants of each
pair for CAT, separate and matched split positions: 3/6 was obtained even
without any source signal. Restoring the original source text without vectors
gave 6/6. This tests direct transfer to a new decoder prompt; it cannot rule
out information elsewhere in the trace or learnability of a trained readout.

### Reference readout: official LOTUS natural-language weights

[LOTUS](https://github.com/yingfan-bot/lotus) is a separate Llama-based
experiment. Use its [natural-language stage-2 weights](https://huggingface.co/yingfanbot/gsm-lotus-llama3b-nl)
and its inference dependencies in an isolated environment; the Qwen experiment
uses a different Transformers version. The model card specifies six latent
blocks of 50 positions and six refinement iterations. Stage-1 CoT weights and
ordinary text generation do not exercise this trained latent interface.

```powershell
python -m experiments.nplus_shared_cache.lotus_readout_probe --upstream path/to/pinned-lotus --model path/to/local-nl-checkpoint --question "A shop has 4 boxes of 6 cups each. It sells 7 cups. How many cups are left?" --output path/to/new-readout.json
```

The probe checks both shard hashes and the upstream generation source revision,
uses the authors' batch-one generation path, and records the final loop's six
raw word-head readouts and the separately decoded answer. Every block has 50
positions. Text remaining after a coherent phrase may be unsupervised padding
positions: no gold step length is available at inference, so the receipt does
not trim the suffix or count it as a separate reasoning step. Blocks correspond
to supervised steps, not independently ending solution directions.

With the official NL checkpoint, changing four boxes of six cups to five
changed the first block's readable inventory calculation from 24 to 30.
After selling seven cups the separate greedy answers were 17 and 23, both
with EOS. Later readout positions repeated digits, words and punctuation;
they did not form six clean, complementary explanations. These two local
examples demonstrate a responsive intermediate readout, not benchmark
replication or a causal test of whether the answer needs that block.

## Readable soft-input comparison

This separate, opt-in comparison uses probability-weighted input embeddings from
[Soft Thinking](https://github.com/UCSB-AI/Soft-Thinking), pinned at
`b97407c60d5e971da4a4fe76f405d6e236d0ddfa`. The local adapter uses native HF
temperature/top-k/top-p/min-p operators and PyTorch embedding contraction.
It does not install their patched SGLang engine or claim its performance.

```powershell
python -m experiments.nplus_shared_cache.initialization --model path/to/local-model --output path/to/new-native-initialization
python experiments/nplus_shared_cache/parallel_observer.py --model path/to/local-model --auxiliary path/to/new-native-initialization/auxiliary.safetensors --cases experiments/nplus_shared_cache/context_pair.example.json --output path/to/new-private-output --execute --initialization-probe --soft-embeddings --decode-final-answer
```

Each route predicts a probability distribution. Its continuous input vector is
a weighted mixture of retained pretrained token embeddings. The top token is
displayed for observation; it is not re-embedded as the feedback input. The five
resulting vectors enter feature CAT and a dense fusion matrix before updating
the single cache. The vocabulary distribution therefore influences feedback.
Although continuous, this is a different mechanism from the primary hidden
feedback experiment; it is not a text-only observation of that experiment.

The initializer loads no backbone and performs no training. It creates small
separate residual heads and a dense trainable CAT matrix initialized with five
identity blocks divided by five plus small dense jitter. The demonstrated
fusion remains an **initialization**, not a trained multi-purpose fusion.
Hidden-space-trained fusion weights are not calibrated for this input space;
they produced degeneration in the local experiment.

Two fixed conversation probes produced 907 and 817 tokens per route, all ending
naturally. Their texts retained the updated date, code, contact and unknown fee.
Routes were highly similar and included system-instruction restatement and
small textual artifacts. These are readable observations, not approved training
records or evidence of five-way diversity. The upstream project also warns
about soft-thinking quality on small models.

`--decode-final-answer` uses the same live cache only after all routes end.
If a soft concept already consumed `</think>`, only its whitespace separator
is appended. Unfinished routes do not silently pass to answer generation.
The real handoff and complete raw answer are archived.

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

### Five continuous text observations

Without `--soft-embeddings`, `parallel_observer.py` observes a longer
shared-state trajectory. Each round reads all five actual head vectors through
the same pretrained LM head and appends each active route's selected token to
its own complete ID sequence. It does not concatenate five pieces of text into
the backbone input, create five private backbone caches, or feed the selected
discrete tokens back as text. The continuous vectors still enter CAT and the
single shared-cache update. This is continuous-state emission, not five
ordinary independently autoregressive language models.

```powershell
python experiments/nplus_shared_cache/parallel_observer.py --model path/to/local-model --auxiliary path/to/training/auxiliary.safetensors --cases experiments/nplus_shared_cache/context_pair.example.json --output path/to/new-private-output --execute --initialization-probe
```

Preparation without `--execute` uses the tokenizer only. Legacy three-position
checkpoints have no per-route token/EOS supervision, so execution requires the explicit
`--initialization-probe` flag. Their earlier shared-loop training remains real;
they are an initialization for this new observation mode, not trained readable
five-route CoT checkpoints. No extra training occurs in this command.

Each route ends on its own EOS or existing `</think>` token. A route emitting
its ending participates in that current round's CAT. Starting with the next
round, its entire vector slot is exactly zero and no more tokens are appended.
Token ID zero is not padding: it is an ordinary vocabulary token. If all routes
end in one round, that last active round completes its cache update and the loop
exits; no extra all-padding round is injected through the fusion bias.

The default ceiling is 32,768 shared rounds, potentially five emitted tokens per
round but only one new backbone position. This is a capacity limit, not a target
length. EOS completion, round-cap exhaustion, context exhaustion, and explicit
`STOP` are recorded separately. An unfinished route never receives an invented
EOS. `parallel-streams.json` and five purpose-named `.txt` files expose each
complete actual stream; the per-round JSONL records route masks, emitted IDs,
CAT slot norms, and cache lengths. Actual tensor samples cover the first and
last rounds and every route-ending transition, rather than claiming that all
intermediate tensors were archived.

The first local observation with a three-position checkpoint reached actual EOS
on all routes after 377 rounds. Its streams were highly similar and disfluent.
This verifies neither readable reasoning nor useful specialization. Increasing
the observation length alone does not add the missing language supervision.

The full-token training entry below gives this observation mode a separate
training contract. It does not silently reuse three proxy targets as complete
text supervision or start training from the observer command. A completed
full-token checkpoint can be observed without `--initialization-probe`;
the original legacy checkpoint still requires that flag. The observer follows
the trained checkpoint's ending-token ID in the full-token mode.

Run the observer's targeted EOS/padding protocol regression with:

```powershell
python -m unittest experiments.nplus_shared_cache.test_parallel_observer
```

Its scripted tiny model checks state transitions, not real model text quality.

### Full-token continuous text training

The full-text experiments retain continuous head-vector feedback. They do
**not** replace it with decoded-token embeddings.
It reuses the pretrained Qwen backbone, the existing auxiliary weights as an
explicit warm start, and the already-generated complete five teacher texts.

```powershell
python experiments/nplus_shared_cache/teacher_states.py --model path/to/local-model --records path/to/verified-train.jsonl --heldout path/to/heldout.jsonl --output path/to/new-teacher-cache --execute
python experiments/nplus_shared_cache/continuous_text_training.py --model path/to/local-model --initial-auxiliary path/to/previous/auxiliary.safetensors --initial-sha256 CHECKPOINT_SHA256 --prepared path/to/new-teacher-cache --output path/to/new-training-run --action train
```

This default is the v2 pre-target-state experiment. For the separate v3
post-target-state experiment, add `--state-position post_target` to the encoder
and use new cache and training directories. v3 includes each current token and
EOS in the encoder input, selects its post-consumption state, and trains a
shared observation map to recover the current token through the frozen LM head.
It adds a teacher-state decoding CE term. The native LM head predicts the
following token; shifting labels alone would not resolve this distinction.

Both commands prepare only by default; the model encoder requires `--execute`
and optimization requires `--action train`. The encoder never trains or calls
a remote model. Complete record identities, held-out questions, base weight
shards, template, source bytes, target digests and executed code are bound.
Completed teacher encodings are reused after authentication. Neither original
records nor completed older training runs are rewritten.

`teacher_states.py` encodes every original path token once with frozen local
Qwen. For a path of N tokens, it saves N+1 hidden states: the last prefix state
predicts token zero, subsequent states precede each next target, and the state
after the full body predicts the appended ending. These are local-Qwen teacher
states, not the remote teacher's private reasoning states. Each route ends
independently; later hidden slots are exactly zero and later labels are ignored.

Training uses teacher forcing in the **continuous hidden space**:

```text
gold teacher heads s[t, route] -> masked CAT -> fusion/RMS -> z[t]
one causal Qwen forward over prefix + all z + boundary + answer[:-1]
h[P - 1 + t] -> five heads -> each route's token/EOS loss
h[answer_start - 1 + k] -> full answer/EOS loss
```

`z[t]` is placed at P+t, after the state predicting target t. Reading that
target from the state after `z[t]` would leak the target information. The first
actual training forward checks that its first token cannot depend on any
current or future gold feedback. It separately records token-loss gradients
to all five heads and answer-loss gradients to fusion, gain and early feedback.
Under teacher forcing, the answer loss does not directly traverse the predicted
heads; it must not be reported as full free-rollout backpropagation.

All five complete token/EOS targets and the complete answer/EOS are supervised.
A small unit-normalized squared-L2 hidden alignment term supplements CE.
PyTorch non-reentrant checkpointing chunks the vocabulary losses to avoid
materializing the entire `[rounds, 5, vocabulary]` tensor. Native HF Trainer
owns the single ordered epoch, accumulation, scheduling and resume checkpoints.
The backbone remains frozen and differentiable with respect to the continuous
inputs. Only auxiliary parameters are updated and saved.

This is an experimental latent teacher-forcing method. At inference, its gold
continuous inputs are replaced by the student's predicted heads. That exposure
difference is real: low teacher-forced loss does not establish coherent free
generation. Inspect the five complete free-running texts, each real ending,
any unfinished routes and the actual resource receipts before making a quality
claim. The new entry does not extend or relabel the completed v1 epoch.

The v2 pre-target feedback does not uniquely contain the actual sampled prior
word; it is not standard token-embedding teacher forcing. The v3 post-state
targets remove that particular timing ambiguity, but its observation map and
student states still failed readable free rollout locally. Both results and
their full losses are retained. Repeating the same teacher-forced loss does
not establish that this failure has been solved.

### Auxiliary checkpoint identity

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
