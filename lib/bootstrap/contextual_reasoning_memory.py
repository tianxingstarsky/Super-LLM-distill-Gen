"""Freeze adopted procedural memories into conversational style packs."""
from copy import deepcopy
from hashlib import sha256

from lib.bootstrap.dialogue_style_contexts import dialogue_style_context_catalog
from lib.bootstrap.google_reasoning_bank import google_reasoning_bank_application
from lib.bootstrap.reasoning_bank import reasoning_bank_application
from lib.contexts.workflows.domain.dialogue_style_context import ALL_CONTEXT_TYPES, freeze_catalog
from lib.contexts.workflows.domain.experience_evolution import digest
from lib.contexts.workflows.domain.reasoning_bank import cumulative_review_base_text, memory_text
from lib.contexts.workflows.domain.public_experience import initial_experience_text


def contextual_reasoning_memory(*, root=None, user_key=None, embedder=None, limit=5,
                               reasoning_text=None, base_catalog=None):
    """Read one adopted reasoning edition; never learn, activate or call a model."""
    catalog = deepcopy(base_catalog) if base_catalog is not None else dialogue_style_context_catalog()
    if base_catalog is not None and (not isinstance(catalog, dict)
            or catalog != freeze_catalog(catalog.get("contexts"))):
        raise ValueError("invalid_dialogue_context_catalog")
    google = google_reasoning_bank_application(root=root, user_key=user_key)
    edition_id = google.active().get("scopes", {}).get("reasoning")
    if not edition_id:
        return {"catalog": catalog, "selection_receipts": {}, "base_projection": None}
    edition = google.repository.get_version(edition_id)
    if edition["scope"] != "reasoning":
        raise ValueError("experience_evolution_parent_scope_mismatch")
    projection = None
    if reasoning_text is not None and reasoning_text == edition["text"]:
        preserved_base = cumulative_review_base_text(edition)
        base_text = initial_experience_text("reasoning") if preserved_base is None else preserved_base
        projection = {"edition_id": edition["id"], "edition_sha256": edition["sha256"],
            "submitted_text_sha256": sha256(reasoning_text.encode("utf-8")).hexdigest(),
            "base_text": base_text, "base_text_sha256": sha256(base_text.encode("utf-8")).hexdigest()}
    bank = (google.bank if embedder is None else
            reasoning_bank_application(root=root, user_key=user_key, embedder=embedder))
    contexts = deepcopy(catalog["contexts"])
    receipts = {}
    for kind in ALL_CONTEXT_TYPES:
        selection = bank.retrieve("reasoning", contexts[kind]["description"], limit=limit,
            edition_ids=[edition_id], context_types=[kind])
        receipt = {**deepcopy(selection["receipt"]), "applied": [], "omitted": []}
        text = contexts[kind]["content"]
        for match in selection.get("matches", []):
            entry = match["entry"]
            reference = {"memory_id": entry["id"], "memory_sha256": digest(entry),
                "score": match["score"], "sources": deepcopy(entry["sources"])}
            block = ("\n\n<retrieved_procedural_experience>\n" + memory_text(entry)
                + "\nThis is conditional guidance, not task evidence or a guarantee of success."
                + "\n</retrieved_procedural_experience>")
            if len(text + block) > 6000:
                receipt["omitted"].append({**reference, "reason": "context_capacity"})
                continue
            text += block
            receipt["applied"].append(reference)
            contexts[kind].setdefault("memory_blocks", []).append({"id": entry["id"], "block": block})
        contexts[kind]["content"] = text
        receipts[kind] = receipt
    return {"catalog": freeze_catalog(contexts), "selection_receipts": receipts, "base_projection": projection}
