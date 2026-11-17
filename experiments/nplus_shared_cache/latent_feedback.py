"""Shared five-head projection and embedding alignment for train and inference."""
import torch
from safetensors.torch import load_file

PURPOSES = ("forward", "backward", "alternatives", "constraints", "counterexamples")


class SharedDirectionHeads(torch.nn.Module):
    """All heads read the same h; checkpoint names retain the original contract."""

    def __init__(self, width: int, rank: int):
        super().__init__()
        self.width, self.rank = width, rank
        self.adapters = torch.nn.ModuleList([torch.nn.Sequential(
            torch.nn.LayerNorm(width, elementwise_affine=False),
            torch.nn.Linear(width, rank, bias=False), torch.nn.SiLU(),
            torch.nn.Linear(rank, width, bias=False)) for _ in PURPOSES])
        self.fusion = torch.nn.Linear(5 * width, width)
        self.gain = torch.nn.Parameter(torch.tensor(1.0))

    def forward(self, hidden):
        heads = torch.stack([hidden + adapter(hidden) for adapter in self.adapters], dim=1)
        feature_cat = torch.cat(list(heads.unbind(1)), dim=-1)
        return heads, feature_cat, self.fusion(feature_cat)

    def parameter_groups(self):
        return {**{name: list(adapter.parameters()) for name, adapter in zip(PURPOSES, self.adapters)},
            "fusion": list(self.fusion.parameters()), "gain": [self.gain]}


def load_auxiliary(path, *, width: int, device: str, trainable: bool = False):
    state = load_file(str(path), device="cpu")
    module = SharedDirectionHeads(width, state["adapters.0.1.weight"].shape[0])
    module.load_state_dict(state, strict=True)
    module.to(device=device, dtype=torch.float32).requires_grad_(trainable)
    return module.train(trainable)


def project_shared_hidden(auxiliary, hidden, reference_rms, embedding_dtype):
    """Actual CAT projection and differentiable alignment, with no hidden detach."""
    with torch.autocast(device_type=hidden.device.type, enabled=False):
        heads, feature_cat, fused = auxiliary(hidden.float())
        injected = fused * reference_rms / fused.square().mean(-1, keepdim=True).sqrt().clamp_min(1e-8)
        injected = (injected * auxiliary.gain).to(embedding_dtype).unsqueeze(1)
    return heads, feature_cat, fused, injected
