"""Create explicit, untrained, native-compatible five-head auxiliary weights."""
import argparse
import hashlib
import json
from pathlib import Path


def initialize(model, output, *, seed=42, rank=16):
    import torch
    from safetensors.torch import save_file
    from .latent_feedback import SharedDirectionHeads
    from .runner import file_sha, write_json

    model, output = Path(model), Path(output)
    config = json.loads((model / "config.json").read_text("utf-8"))
    width = config.get("text_config", config)["hidden_size"]
    if rank < 1 or output.exists():
        raise ValueError("positive_rank_and_new_output_directory_required")
    binding = {"schema": "native_soft_input_initialization/v1", "seed": seed,
        "width": width, "rank": rank, "model_config_sha256": file_sha(model / "config.json"),
        "adapter_input_std": .02, "adapter_output_std": .002,
        "fusion": "five_identity_blocks_divided_by_five_plus_dense_jitter",
        "fusion_jitter_std": 1e-4, "training_performed": False}
    identity = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    torch.manual_seed(seed)
    auxiliary = SharedDirectionHeads(width, rank)
    with torch.no_grad():
        for adapter in auxiliary.adapters:
            torch.nn.init.normal_(adapter[1].weight, std=.02)
            torch.nn.init.normal_(adapter[3].weight, std=.002)
        auxiliary.fusion.weight.zero_()
        diagonal = torch.arange(width)
        for index in range(5):
            auxiliary.fusion.weight[diagonal, index * width + diagonal] = .2
        auxiliary.fusion.weight.add_(torch.randn_like(auxiliary.fusion.weight) * 1e-4)
        auxiliary.fusion.bias.zero_()
    output.mkdir(parents=True)
    path = output / "auxiliary.safetensors"
    save_file({name: value.contiguous() for name, value in auxiliary.state_dict().items()},
        str(path), metadata={"format": "pt", "architecture": binding["schema"], "identity": identity})
    receipt = {"identity": identity, "binding": binding, "status": "untrained_initialization",
        "auxiliary_file_sha256": file_sha(path), "training_steps": 0, "paid_model_calls": 0,
        "direction_specialization_established": False, "learned_fusion_established": False}
    write_json(output / "initialization-receipt.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--rank", type=int, default=16)
    args = parser.parse_args()
    print(json.dumps(initialize(args.model, args.output, seed=args.seed, rank=args.rank)))


if __name__ == "__main__":
    main()
