"""Soft Thinking concept embeddings using native HF filters and PyTorch.

Algorithm reference: UCSB-AI/Soft-Thinking, NeurIPS 2025, revision
b97407c60d5e971da4a4fe76f405d6e236d0ddfa. Its sampler and weighted_forward
construct probability-weighted input embeddings. This small implementation
uses existing HF operators; it does not import or reproduce their SGLang fork.
https://github.com/UCSB-AI/Soft-Thinking
"""
import torch
from transformers import (LogitsProcessorList, MinPLogitsWarper,
    TemperatureLogitsWarper, TopKLogitsWarper, TopPLogitsWarper)


class SoftEmbeddingReadout:
    """Return a continuous input-space vector plus its visible top-token probe."""

    def __init__(self, embedding, *, max_topk=10, temperature=.6,
                 top_k=30, top_p=.95, min_p=.001):
        if max_topk < 1:
            raise ValueError("positive_concept_support_required")
        self.embedding, self.max_topk = embedding, max_topk
        self.filters = LogitsProcessorList([
            TemperatureLogitsWarper(temperature), TopKLogitsWarper(top_k),
            TopPLogitsWarper(top_p), MinPLogitsWarper(min_p)])

    def __call__(self, logits):
        scores = self.filters(torch.empty(logits.shape[0], 0,
            dtype=torch.long, device=logits.device), logits.float())
        probabilities = scores.softmax(-1)
        weights, ids = probabilities.topk(min(self.max_topk, probabilities.shape[-1]), dim=-1)
        weights = weights / weights.sum(-1, keepdim=True)
        candidates = self.embedding(ids)
        concepts = torch.einsum("bk,bkd->bd", weights, candidates.float()).to(candidates.dtype)
        return concepts, ids[:, 0], {"ids": ids, "weights": weights,
            "nonzero_support": (weights > 0).sum(-1)}
