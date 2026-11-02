"""Persist a proposal already returned by the review call; never induce again."""
from copy import deepcopy
from hashlib import sha256
import json

from filelock import FileLock

from lib.io_utils import atomic_json
from ..domain.experience_evolution import FORMAT_VERSION, digest, validate_experience_text
from ..domain.reasoning_bank import cumulative_review_base_text, memory_context_assignments


def record_review_memory_proposal(repository, bank, proposal, *, source_receipt, base_text, episode):
    identity = {"proposal": proposal, "source_receipt": source_receipt, "base_text": base_text,
        "review_memory_cumulative_version": 1}
    identifier = digest(identity)
    directory = repository.root / "review-evolution"
    directory.mkdir(exist_ok=True)
    path = directory / (identifier + ".json")
    if directory.is_symlink() or path.is_symlink():
        raise ValueError("experience_evolution_unsafe_path")
    with FileLock(str(path) + ".lock"):
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("sha256") != digest({key: value for key, value in saved.items() if key != "sha256"}):
                raise ValueError("review_memory_proposal_receipt_changed")
            if saved.get("source_sha256") != identifier:
                raise ValueError("review_memory_proposal_receipt_changed")
            if saved.get("edition_id"):
                edition = repository.get_version(saved["edition_id"])
                if edition["sha256"] != saved["edition_sha256"]:
                    raise ValueError("review_memory_proposal_receipt_changed")
                cumulative_review_base_text(edition)
            return saved
        receipt = {"version": 1, "origin": "same_review_call", "status": proposal["status"],
            "source_sha256": identifier, "proposal_sha256": digest(proposal), "new_model_calls": 0, "adopted": False}
        if proposal["status"] == "proposed":
            items = list(dict.fromkeys(proposal["memory_items"]))
            text = validate_experience_text(base_text + ("\n\n" if base_text else "") + "\n\n".join(items))
            edition = repository.save({"format_version": FORMAT_VERSION, "scope": "reasoning",
                "status": "candidate", "label": "同次评审 · 通用经验增量", "parent_id": None,
                "base_text": base_text, "text": text, "summary": "同一评审提出的候选增量，未全局采用，未独立验证。",
                "review_memory_cumulative_version": 1,
                "base_text_sha256": sha256(base_text.encode("utf-8")).hexdigest(),
                "rules": [], "episodes": [deepcopy(episode)], "source_receipt": deepcopy(source_receipt),
                "instruction": "", "quality": "model_proposed_not_verified", "upstream_memory_items": items,
                "dialogue_context_assignment_version": 1, "dialogue_context_assignments": memory_context_assignments(items),
                "memory_proposal_origin": "same_review_call", "upstream": [],
                "model_request_receipt": [deepcopy(source_receipt.get("review_checkpoint", {}))]})
            receipt.update(edition_id=edition["id"], edition_sha256=edition["sha256"], index={"status": "pending"})
            # The immutable edition is durable before local indexing. Missing
            # embedding dependencies cannot make a replay create a new edition.
            receipt["sha256"] = digest({key: value for key, value in receipt.items() if key != "sha256"})
            atomic_json(path, receipt)
            receipt["index"] = bank.index_edition(edition["id"])
            receipt.pop("sha256")
        receipt["sha256"] = digest(receipt)
        atomic_json(path, receipt)
        return receipt
