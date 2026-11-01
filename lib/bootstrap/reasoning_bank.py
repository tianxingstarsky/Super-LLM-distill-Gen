"""Compose a personal ReasoningBank with authenticated editions and local vectors."""
import getpass
from hashlib import sha256
from pathlib import Path

from lib.contexts.workflows.api import ReasoningBank
from lib.contexts.workflows.infrastructure.experience_evolution import ExperienceEvolutionRepository
from lib.contexts.workflows.infrastructure.reasoning_bank import (
    FastEmbedReasoningEmbedder, ReasoningBankRepository,
)


def reasoning_bank_application(*, root=None, user_key=None, embedder=None, embedding_cache=None, upstream_root=None):
    user_identifier = getpass.getuser() if user_key is None else user_key
    if (not isinstance(user_identifier, str) or not user_identifier or len(user_identifier) > 1024
            or "\x00" in user_identifier):
        raise ValueError("reasoning_bank_invalid_user")
    namespace = sha256(user_identifier.encode("utf-8")).hexdigest()
    storage_root = Path.home() / ".shujian-cube" if root is None else Path(root)
    edition_root = storage_root / "experience-evolution" / namespace
    cache_root = storage_root / "cache" / "reasoning-bank" if embedding_cache is None else Path(embedding_cache)
    embedding = embedder if embedder is not None else FastEmbedReasoningEmbedder(cache_root)
    repository = ReasoningBankRepository(edition_root, ExperienceEvolutionRepository(edition_root), embedding,
                                        upstream_root=upstream_root)
    return ReasoningBank(repository)
