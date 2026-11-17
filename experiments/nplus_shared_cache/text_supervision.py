"""Complete five-route token targets with independently masked ending positions."""
from __future__ import annotations

from .latent_feedback import PURPOSES


def encode_five_paths(tokenizer, paths: dict[str, str], *, ending_token_id: int):
    """Encode every original token; supervise one ending and ignore only later slots.

    Returned lists are round-major [round, route]. Padding IDs must never be
    confused with zero vectors: consumers apply ``active`` after embedding.
    """
    if set(paths) != set(PURPOSES):
        raise ValueError("five_named_paths_required")
    if ending_token_id < 0 or tokenizer.pad_token_id is None:
        raise ValueError("valid_ending_and_padding_ids_required")
    encoded = []
    for purpose in PURPOSES:
        text = paths[purpose]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("complete_nonempty_teacher_path_required:" + purpose)
        tokens = tokenizer.encode(text, add_special_tokens=False)
        if ending_token_id in tokens:
            raise ValueError("teacher_path_already_contains_ending_token:" + purpose)
        encoded.append(tokens + [ending_token_id])
    rounds = max(map(len, encoded))
    token_ids, labels, active = [], [], []
    for step in range(rounds):
        mask = [step < len(tokens) for tokens in encoded]
        token_ids.append([tokens[step] if keep else tokenizer.pad_token_id
            for tokens, keep in zip(encoded, mask)])
        labels.append([token if keep else -100 for token, keep in zip(token_ids[-1], mask)])
        active.append(mask)
    return {"token_ids": token_ids, "labels": labels, "active": active,
        "route_lengths_with_ending": [len(tokens) for tokens in encoded],
        "rounds": rounds, "ending_token_id": ending_token_id,
        "padding_token_id": tokenizer.pad_token_id, "purposes": list(PURPOSES),
        "ending_round_is_active": True, "complete_source_text_preserved": True}
