"""Compose an OS-user-scoped edition library and optional node-model proposer."""
import getpass
import hashlib
from pathlib import Path

from lib.contexts.workflows.application.experience_evolution import EvolveExperience
from lib.contexts.workflows.infrastructure.experience_evolution import ExperienceEvolutionRepository
from lib.contexts.workflows.infrastructure.experience_evolution_model import ExperienceEvolutionModelPort


def experience_evolution_application(binding=None, *, root=None, user_key=None):
    user = getpass.getuser() if user_key is None else user_key
    if not isinstance(user, str) or not user.strip() or len(user) > 1024 or "\x00" in user:
        raise ValueError("experience_evolution_invalid_user")
    namespace = hashlib.sha256(user.encode("utf-8")).hexdigest()
    storage_root = Path.home()/".shujian-cube" if root is None else Path(root)
    repository = ExperienceEvolutionRepository(storage_root/"experience-evolution"/namespace)
    settings_root = Path(__file__).resolve().parents[2]
    def client_factory(selected):
        from lib.llm_client import load_backend
        return load_backend(settings_root, backend=selected["backend"], model=selected["model"],
            role="generation", allow_global_endpoint_override=False,
            context_window_tokens=selected.get("context_window_tokens"),
            **({"reasoning": selected["reasoning"]} if "reasoning" in selected else {}))
    return EvolveExperience(ExperienceEvolutionModelPort(repository, settings_root, binding,
        client_factory=client_factory))
