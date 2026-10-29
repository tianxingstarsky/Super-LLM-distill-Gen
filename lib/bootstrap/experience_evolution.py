"""Compose an OS-user-scoped edition library and optional node-model proposer."""
import getpass
import hashlib
from pathlib import Path

from lib.contexts.workflows.application.experience_evolution import EvolveExperience
from lib.contexts.workflows.infrastructure.experience_evolution import ExperienceEvolutionRepository
from lib.contexts.workflows.infrastructure.experience_evolution_model import ExperienceEvolutionModelPort


def experience_evolution_application(binding=None, *, root=None, user_key=None) -> EvolveExperience:
    """Build a user-owned style library with an optional proposal model binding.

    ``root`` is the library's storage base, not the model-settings directory.
    Omitting ``binding`` still permits saved-edition access. A client is loaded
    only when the model port requests a proposal; no running recipe is changed.
    """
    user_identifier = getpass.getuser() if user_key is None else user_key
    if (not isinstance(user_identifier, str) or not user_identifier.strip()
            or len(user_identifier) > 1024 or "\x00" in user_identifier):
        raise ValueError("experience_evolution_invalid_user")
    user_namespace = hashlib.sha256(user_identifier.encode("utf-8")).hexdigest()
    storage_root = Path.home() / ".shujian-cube" if root is None else Path(root)
    edition_repository = ExperienceEvolutionRepository(storage_root / "experience-evolution" / user_namespace)
    settings_root = Path(__file__).resolve().parents[2]

    def client_factory(selected_binding):
        from lib.llm_client import load_backend

        return load_backend(settings_root, backend=selected_binding["backend"], model=selected_binding["model"],
            role="generation", allow_global_endpoint_override=False,
            context_window_tokens=selected_binding.get("context_window_tokens"),
            **({"reasoning": selected_binding["reasoning"]} if "reasoning" in selected_binding else {}))

    return EvolveExperience(ExperienceEvolutionModelPort(edition_repository, settings_root, binding,
        client_factory=client_factory))
