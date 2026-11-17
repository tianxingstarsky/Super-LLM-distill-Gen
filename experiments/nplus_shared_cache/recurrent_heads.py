"""Five continuous route states around one shared backbone state.

The module has no tokenizer, vocabulary readout, model calls, or cache. Route
goals are frozen hidden states supplied by the caller from known instructions,
never from an answer or a teacher state that already contains a target token.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import torch
from safetensors import safe_open


class RecurrentDirectionHeads(torch.nn.Module):
    """A shared pretrained-context input with private GRU memory for each route.

    Sharing the cell weights does not share the five memory values. The route
    codes and goal states condition those values independently. The caller
    decides when global feedback is committed and where TBPTT detaches state.
    Route names or distinct states alone do not establish semantic expertise.
    """

    route_count = 5

    def __init__(self, width: int, memory_width: int = 512, rank: int = 64):
        super().__init__()
        if min(width, memory_width, rank) < 1:
            raise ValueError("positive_state_dimensions_required")
        self.width, self.memory_width, self.rank = width, memory_width, rank
        self.previous_norm = torch.nn.LayerNorm(width, elementwise_affine=False)
        self.shared_norm = torch.nn.LayerNorm(width, elementwise_affine=False)
        self.goal_norm = torch.nn.LayerNorm(width, elementwise_affine=False)
        self.previous_projection = self._projection(width, rank, memory_width)
        self.shared_projection = self._projection(width, rank, memory_width)
        self.goal_projection = self._projection(width, rank, memory_width)
        # These are five route codes, not word-token embeddings.
        self.purpose_codes = torch.nn.Parameter(torch.empty(self.route_count, memory_width))
        torch.nn.init.normal_(self.purpose_codes, std=.02)
        self.input_norm = torch.nn.LayerNorm(memory_width)
        self.cell = torch.nn.GRUCell(memory_width, memory_width)
        self.delta_projection = self._projection(memory_width, rank, width)
        self.initial_projection = self._projection(memory_width, rank, width)
        self.step_gate = torch.nn.Linear(memory_width, 1)
        # Small initial residuals preserve the known backbone state. This is an
        # initialization, not a clamp or an asserted semantic direction prior.
        torch.nn.init.normal_(self.delta_projection[-1].weight, std=.002)
        torch.nn.init.normal_(self.initial_projection[-1].weight, std=.002)
        torch.nn.init.zeros_(self.step_gate.weight)
        torch.nn.init.zeros_(self.step_gate.bias)
        self.register_buffer("goal_states", torch.zeros(self.route_count, width, dtype=torch.float32))
        self.register_buffer("goal_states_ready", torch.tensor(False, dtype=torch.bool))
        self.fusion = torch.nn.Linear(self.route_count * width, width)
        self.gain = torch.nn.Parameter(torch.tensor(1.0))
        with torch.no_grad():
            self.fusion.weight.zero_()
            diagonal = torch.arange(width)
            for route in range(self.route_count):
                self.fusion.weight[diagonal, route * width + diagonal] = 1 / self.route_count
            self.fusion.bias.zero_()

    @staticmethod
    def _projection(input_width: int, rank: int, output_width: int):
        return torch.nn.Sequential(torch.nn.Linear(input_width, rank, bias=False),
            torch.nn.SiLU(), torch.nn.Linear(rank, output_width, bias=False))

    def set_goal_states(self, goal_states: torch.Tensor):
        """Install [5,D] known-instruction hidden states, detached and immutable."""
        if tuple(goal_states.shape) != (self.route_count, self.width) or not torch.isfinite(goal_states).all():
            raise ValueError("five_finite_goal_hidden_states_required")
        with torch.no_grad():
            self.goal_states.copy_(goal_states.detach().to(self.goal_states))
            self.goal_states_ready.fill_(True)
        return self

    def _goals(self):
        if not bool(self.goal_states_ready):
            raise ValueError("set_known_instruction_goal_states_before_route_initialization")
        return self.goal_projection(self.goal_norm(self.goal_states)) + self.purpose_codes

    def initialize(self, sharedh: torch.Tensor):
        """Return q [B,5,D] near sharedh and zero private memory [B,5,M]."""
        if sharedh.ndim != 2 or sharedh.shape[-1] != self.width:
            raise ValueError("shared_hidden_shape_must_be_batch_by_width")
        with torch.autocast(device_type=sharedh.device.type, enabled=False):
            goals = self._goals()
            residual = self.initial_projection(goals).unsqueeze(0)
            q = sharedh.float().unsqueeze(1) + residual
            memory = torch.zeros((sharedh.shape[0], self.route_count, self.memory_width),
                device=sharedh.device, dtype=torch.float32)
        return q, memory

    def initial_state(self, sharedh: torch.Tensor):
        """Compatible descriptive alias for initialize."""
        return self.initialize(sharedh)

    def forward_step(self, sharedh: torch.Tensor, previousq: torch.Tensor, memory: torch.Tensor):
        """Update all routes simultaneously; no detach, sampling, or clipping."""
        batch = sharedh.shape[0]
        if tuple(sharedh.shape) != (batch, self.width) or tuple(previousq.shape) != (batch, self.route_count, self.width) or tuple(memory.shape) != (batch, self.route_count, self.memory_width):
            raise ValueError("shared_hidden_route_vector_or_memory_shape_mismatch")
        with torch.autocast(device_type=sharedh.device.type, enabled=False):
            previous = previousq.float()
            inputs = self.previous_projection(self.previous_norm(previous))
            inputs = inputs + self.shared_projection(self.shared_norm(sharedh.float())).unsqueeze(1)
            inputs = self.input_norm(inputs + self._goals().unsqueeze(0))
            new_memory = self.cell(inputs.reshape(batch * self.route_count, self.memory_width),
                memory.float().reshape(batch * self.route_count, self.memory_width))
            new_memory = new_memory.reshape(batch, self.route_count, self.memory_width)
            delta = self.delta_projection(new_memory)
            gate = self.step_gate(new_memory).sigmoid()
            q = previous + gate * delta
        return q, new_memory

    def parameter_groups(self):
        """Keep recurrence, initial routing, fusion, and gain identifiable."""
        return {
            "route_recurrence": [self.purpose_codes, *self.previous_projection.parameters(),
                *self.shared_projection.parameters(), *self.goal_projection.parameters(),
                *self.input_norm.parameters(), *self.cell.parameters(),
                *self.delta_projection.parameters(), *self.step_gate.parameters()],
            "route_initialization": list(self.initial_projection.parameters()),
            "fusion": list(self.fusion.parameters()), "gain": [self.gain]}

    def parameter_counts(self):
        return {**{name: sum(parameter.numel() for parameter in parameters)
            for name, parameters in self.parameter_groups().items()},
            "total": sum(parameter.numel() for parameter in self.parameters())}


def warm_start_fusion(module: RecurrentDirectionHeads, auxiliary_path,
                      *, expected_sha256: str):
    """Copy only authenticated fusion/gain; recurrence has no pretrained claim."""
    path = Path(auxiliary_path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    actual = digest.hexdigest()
    if actual != expected_sha256.lower():
        raise ValueError("fusion_warm_start_auxiliary_sha256_mismatch")
    with safe_open(path, framework="pt", device="cpu") as handle:
        values = {name: handle.get_tensor(name) for name in ("fusion.weight", "fusion.bias", "gain")}
        source_metadata = handle.metadata()
    with torch.no_grad():
        for name, target in (("fusion.weight", module.fusion.weight),
                             ("fusion.bias", module.fusion.bias), ("gain", module.gain)):
            if values[name].shape != target.shape:
                raise ValueError("fusion_warm_start_shape_mismatch:" + name)
            target.copy_(values[name].to(target))
    return {"source_sha256": actual, "source_metadata": source_metadata,
        "copied_parameters": list(values), "route_recurrence_was_pretrained": False}
