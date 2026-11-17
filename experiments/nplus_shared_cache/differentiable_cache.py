"""Graph-preserving state ownership for the official HF 5.23 Qwen cache.

The DynamicCache/Cache API remains upstream:
https://huggingface.co/docs/transformers/internal/generation_utils#transformers.DynamicCache

Only convolution-buffer ownership and recurrent-state publication change.
Attention KV updates and Qwen/GDN equations remain upstream. Use no_grad, not
inference_mode, for a frozen prefix later read by differentiable forwards.
Disable activation checkpointing, compilation, offloading and speculative cache
rollback while a chunk's graph is live. Finish backward before detach_history.
"""
from __future__ import annotations

import inspect

from transformers import __version__ as transformers_version
from transformers.cache_utils import DynamicCache, LinearAttentionCacheLayerMixin


def _fallback_contract():
    """Reject unreviewed kernels rather than assume their state/autograd protocol."""
    from transformers.models.qwen3_5 import modeling_qwen3_5 as qwen

    if transformers_version.split(".")[:2] != ["5", "23"]:
        raise RuntimeError("differentiable_cache_requires_reviewed_transformers_5_23")
    names = ("causal_conv1d_update", "causal_conv1d_fn",
             "torch_recurrent_gated_delta_rule", "torch_chunk_gated_delta_rule")
    implementations = {}
    for name in names:
        function = getattr(qwen, name)
        marker = inspect.getclosurevars(function).nonlocals
        if marker.get("is_new_implementation") is not False or \
                marker.get("implementation") is not marker.get("torch_function"):
            raise RuntimeError("unreviewed_qwen_training_kernel:" + name)
        implementation = marker["implementation"]
        implementations[name] = implementation.__module__ + "." + implementation.__name__
    return {"transformers": transformers_version, "function_backends": implementations,
            "scope": "official_Torch_fallback_functions_not_optimized_kernel_validation"}


class DifferentiableQwen35Cache(DynamicCache):
    """Keep shared history; publish new GDN states without modifying old graphs.

    For both one-position recurrent and multi-position chunk forwards, Qwen
    retrieves its convolution history before using the upstream update. That
    update receives a private graph-connected buffer instead of a past step's
    saved tensor. The returned recurrent state is assigned without copy_.
    """

    def __init__(self, config):
        self.backend_contract = _fallback_contract()
        super().__init__(config=config, offloading=False)

    @property
    def is_compileable(self):
        return False

    def get_conv_state(self, layer_idx: int, state_idx: int = 0):
        previous = super().get_conv_state(layer_idx, state_idx)
        if previous is None:
            return None
        working = previous.clone()  # Preserve the within-chunk gradient edge.
        self.layers[layer_idx].conv_states[state_idx] = working
        return working

    def update_recurrent_state(self, recurrent_states, layer_idx: int,
                               state_idx: int = 0, **kwargs):
        layer = self.layers[layer_idx]
        if not isinstance(layer, LinearAttentionCacheLayerMixin):
            raise ValueError("recurrent_state_requires_a_linear_attention_layer")
        if not layer.is_recurrent_states_initialized[state_idx]:
            layer.lazy_initialization(recurrent_states=recurrent_states, state_idx=state_idx)
        layer.recurrent_states[state_idx] = recurrent_states
        return recurrent_states

    def detach_history(self):
        """Truncate gradients after backward; retain every history value/position.

        The caller must also detach its carried hidden/head state and release
        old graph references. No slicing, resetting or cache reconstruction is
        performed. This method does not define a short context window.
        """
        before = self.get_seq_length()
        count, byte_count = 0, 0
        for layer in self.layers:
            for name in ("keys", "values"):
                tensor = getattr(layer, name, None)
                if tensor is not None:
                    setattr(layer, name, tensor.detach())
                    count += 1
                    byte_count += tensor.numel() * tensor.element_size()
            for name in ("conv_states", "recurrent_states"):
                states = getattr(layer, name, None)
                if states is not None:
                    for index, tensor in states.items():
                        if tensor is not None:
                            states[index] = tensor.detach()
                            count += 1
                            byte_count += tensor.numel() * tensor.element_size()
        after = self.get_seq_length()
        if before != after:
            raise RuntimeError("gradient_truncation_changed_cache_length")
        return {"detached_tensors": count, "tensor_bytes": byte_count,
                "sequence_length_before": before, "sequence_length_after": after}
