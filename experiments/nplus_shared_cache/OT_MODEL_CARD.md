# Qwen3.5-4B with parallel latent routes

This local research package contains two independently fine-tuned Qwen3.5-4B checkpoints and their matching route modules. Use `five` for five simultaneous continuous states or `single` for the matched one-state comparison. The package includes the tokenizer, inference code and a SHA-256 file manifest.

## Run locally

Extract the whole archive. Open a terminal in the extracted folder and use a Python environment with CUDA-enabled PyTorch, Transformers, Accelerate and safetensors. The experiment used PyTorch 2.8.0 and torchvision 0.23.0 with CUDA 12.8, Transformers 5.23.0, Accelerate 1.15.0 and safetensors 0.8.0. Inference uses the local weights on the first CUDA device.

```console
python run.py --training-prompt --question "What is the remainder when 17 is divided by 5?" --max-new-tokens 96
python run.py --single --training-prompt --question "What is the remainder when 17 is divided by 5?" --max-new-tokens 96
python run.py --observe --training-prompt --question "What is the remainder when 17 is divided by 5?" --max-new-tokens 96
```

Read `text` for the final answer. Read `per_route_stop` for the step when each route halted. A null value means the route reached its configured horizon without signaling a stop. With `--observe`, `observations` contains the word head's projection of each active route at each step. A projection can be whitespace or a fragment. It is an inspection aid, with no guarantee that it describes the computation.

Use `--training-prompt` to reproduce the numeric-answer system message used in evaluation. The CLI also accepts a custom `--system` message. To answer multiple questions without reloading weights, import the loader from the extracted folder.

```python
from experiments.nplus_shared_cache.ot_latent_model import generate, load_bundle

base, tokenizer, heads = load_bundle("five")
result = generate(base, tokenizer, heads, "What is the remainder when 17 is divided by 5?")
print(result["text"])
```

## What the checkpoint learns

Each route carries a small continuous recurrent state. The routes share their dynamics and have trainable identifiers. All live states see the same current backbone state; their updates are simultaneous. A learned linear map reads the concatenation of their low-rank states. It contributes to a single fused vector that advances the backbone and its shared cache. Each route has a stop decision. Its final active vector participates in that step; subsequent steps use zeros in its slot. In normal inference the answer starts as soon as all routes finish or the latent horizon is reached.

The two arms start from the same Qwen weights. Training includes the language backbone, tied embedding and output weights, vision encoder, visual merger and route modules. Four of the 32 training questions are presented as images of the question. Each variant has its own tokenizer files and `base` directory. Keep each route module with its own fine-tuned backbone. The optional upstream MTP speculative decoder is outside the Transformers Qwen3.5 model class used here.

The training objective combines numeric answer and EOS supervision, alignment to the original model's representation of a sampled OpenThoughts3 solution, and a learned halting cost for each route. The teacher reads up to 384 solution tokens per training question. Each route evaluates its own candidate states over four steps. Routes may reach the same solution and may stop at the same time. The optional word projection is off during normal inference.

## Measured results

Both variants trained for 32 updates on the same 32 questions, one pass each. All 736 registered parameter tensors in each variant received a nonzero gradient and changed their SHA-256 digest. The comparison used 24 separate math questions with independently checked numeric references. Prior exposure in the base model's pretraining is unknown.

| Metric | Single route | Five routes |
| --- | ---: | ---: |
| Correct final answers | 3/24 | 0/24 |
| Answers reaching EOS within 96 generation steps | 24/24 | 0/24 |
| Backbone calls per question, including padding | 101 | 101 |
| Training time | 456.44 s | 474.14 s |
| Mean evaluation time with padding | 4.73 s | 5.18 s |
| Peak CUDA allocation in evaluation | 8.59 GiB | 8.59 GiB |

Every route reached the four-step latent horizon on these questions. The single-route model answered 11 on half the examples. The five-route model often reopened an internal text boundary or continued reasoning in its answer. All 24 five-route responses reached the answer budget. These checkpoints support further experiments; this run shows no gain in mathematical accuracy from the extra routes. Each variant's `route_model.json` records the training binding, task prompt and checkpoint hashes.

The original weights and training data are available from [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) and [OpenThoughts3-1.2M](https://huggingface.co/datasets/open-thoughts/OpenThoughts3-1.2M). Each variant includes the upstream model license in its `base/LICENSE`.
