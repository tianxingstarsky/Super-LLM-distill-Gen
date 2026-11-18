# Qwen3.5-4B with variable-duration continuous routes

The `single` and `five` folders each contain native Qwen3.5 weights, tokenizer, and a matching recurrent route module. The progress table at the end reports the training status and source cursor for the exact pair in this package. Each arm follows the same pinned order in OpenThoughts3-1.2M.

## Local inference

Extract the archive and open a terminal in the extracted directory. Use CUDA-enabled PyTorch, Transformers, Accelerate, torchvision and safetensors. The research runtime uses PyTorch 2.8 and Transformers 5.23. Inference loads local model files.

```console
python run.py --question "What is the remainder when 17 is divided by 5?"
python run.py --single --question "What is the remainder when 17 is divided by 5?"
python run.py --observe --latent-budget 128 --max-new-tokens 512 --question "What is the remainder when 17 is divided by 5?"
```

The command uses the system message and execution budgets recorded in the loaded checkpoint. Use `--system`, `--latent-budget`, or `--max-new-tokens` to override them. `per_route_stop` gives each route's learned stopping step. An unfinished route has a null stop value. If any route is still active at the budget or model context limit, `thinking_status` is `incomplete`, `status_label` is `思考未完成`, and the formal answer is empty. `--diagnostic-answer-on-partial` explicitly requests an answer attempt from that unfinished state. The record retains its incomplete status and `diagnostic_answer` flag.

The text projection enabled by `--observe` is an inspection aid. Native word IDs from the projected routes never enter the latent recurrence. The separately generated answer is in `text`; its stopping reason is in `stop`.

To use a native training snapshot in the source checkout, pass its immutable `model` directory to the module entry point. The same directory works with the Python loader.

```console
python -m experiments.nplus_shared_cache.ot_long_model --bundle path/to/snapshot/model --question "What is the remainder when 17 is divided by 5?"
```

```python
from experiments.nplus_shared_cache.ot_long_model import generate, load_bundle

model = load_bundle("five")
messages = [
    {"role": "system", "content": "Solve the task and give a complete answer."},
    {"role": "user", "content": "What is the remainder when 17 is divided by 5?"},
]
result = generate(*model, messages, latent_budget=1024, max_new_tokens=32768)
print(result["thinking_status"], result["text"])
```

## Training and inference

The source teacher reads available original reasoning in order. For each contiguous segment it averages its final-layer token vectors in float32. The training default is 128 tokens per segment, and the final segment retains the remaining tokens. The student sees the question and advances a single native model cache with one fused continuous vector per latent step. One route learns successive segments. Five routes learn groups of up to five successive segment targets. Each slot has its own recurrent state and a learned stopping decision. Its terminal vector participates in the current write and later writes zero its slot. A learned coefficient on the logarithm of elapsed steps provides the route clock.

Records containing answers receive supervision on the complete original answer and EOS. Source reasoning that ends before a closing marker supplies observed reasoning segments only. Rows containing only an answer provide ordinary language supervision. See the checkpoint's training progress for the number of records actually used.

The package verifies native weights, tokenizer and route weights against the training receipt. `SHA256.json` lists the files shipped. Each variant includes the upstream model license in `base/LICENSE`. Sources are [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) and [OpenThoughts3-1.2M](https://huggingface.co/datasets/open-thoughts/OpenThoughts3-1.2M).
