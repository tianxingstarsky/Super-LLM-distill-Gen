"""Local Qwen3.5 loading and export, including precision-critical Gated DeltaNet weights."""

import json
from pathlib import Path


def weight_map(directory):
    from safetensors import safe_open

    directory = Path(directory)
    index = directory / "model.safetensors.index.json"
    if index.exists():
        mapping = json.loads(index.read_text(encoding="utf-8"))["weight_map"]
    else:
        with safe_open(
            directory / "model.safetensors", framework="pt", device="cpu"
        ) as handle:
            mapping = dict.fromkeys(handle.keys(), "model.safetensors")
    if not mapping or any(
        Path(s).name != s or not s.endswith(".safetensors") for s in mapping.values()
    ):
        raise ValueError("invalid_local_weight_index")
    return mapping


def load(model_dir=None, device="cuda:0"):
    """Load a standard HF model/tokenizer and restore the checkpoint FP32 GDN tensors."""
    import torch
    from safetensors import safe_open
    from transformers import AutoModelForImageTextToText, AutoTokenizer

    path = Path(model_dir) if model_dir is not None else Path(__file__).resolve().parent
    config = json.loads((path / "config.json").read_text(encoding="utf-8"))
    if config.get("model_type") != "qwen3_5":
        raise ValueError("full_language_training_requires_native_qwen35")
    model = AutoModelForImageTextToText.from_pretrained(
        path,
        local_files_only=True,
        trust_remote_code=False,
        dtype=torch.bfloat16,
        device_map={"": device},
        attn_implementation="sdpa",
    )
    mapping = weight_map(path)
    restore = [
        key
        for key in mapping
        if key.endswith((".linear_attn.norm.weight", ".linear_attn.A_log"))
    ]
    if len(restore) != 2 * model.config.text_config.layer_types.count(
        "linear_attention"
    ):
        raise ValueError("qwen35_gated_delta_weight_inventory_changed")
    with torch.no_grad():
        for shard in {mapping[key] for key in restore}:
            with safe_open(path / shard, framework="pt", device="cpu") as handle:
                for key in restore:
                    if mapping[key] == shard:
                        weight = handle.get_tensor(key)
                        if weight.dtype != torch.float32:
                            raise ValueError(
                                "expected_checkpoint_fp32_gdn_weight:" + key
                            )
                        param = model.get_parameter(key)
                        param.data = weight.to(device=param.device)
    tokenizer = AutoTokenizer.from_pretrained(
        path, local_files_only=True, trust_remote_code=False
    )
    return model.eval(), tokenizer
