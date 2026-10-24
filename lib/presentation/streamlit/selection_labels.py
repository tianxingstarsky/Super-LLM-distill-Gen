"""Unique visible choices for widgets that serialize formatted labels."""
from collections import Counter
from collections.abc import Mapping


def unique_choice_labels(captions: Mapping[str, str]) -> dict[str, str]:
    """Disambiguate repeated names/previews without changing their record IDs.

    Streamlit select boxes deserialize the displayed string. A repeated caption
    can otherwise resolve to another record when any widget submits the form.
    """
    counts = Counter(captions.values())
    labels = {identity: (caption if counts[caption] == 1 else f"{caption} · #{identity[:8]}")
              for identity, caption in captions.items()}
    if len(set(labels.values())) != len(labels):
        labels = {identity: f"{caption} · #{identity}" for identity, caption in captions.items()}
    if len(set(labels.values())) != len(labels):
        labels = {identity: f"{index + 1} · {caption}" for index, (identity, caption) in enumerate(captions.items())}
    return labels
