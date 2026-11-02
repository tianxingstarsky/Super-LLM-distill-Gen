"""Compose the original Google algorithms with personal storage and model ports."""
from pathlib import Path
import getpass
from hashlib import sha256
from lib.bootstrap.experience_evolution import experience_evolution_application
from lib.bootstrap.reasoning_bank import reasoning_bank_application
from lib.contexts.workflows.infrastructure.experience_evolution import ExperienceEvolutionRepository
from lib.contexts.workflows.infrastructure.reasoning_bank_induction import OfficialReasoningBankInductor
from lib.contexts.workflows.infrastructure.google_reasoning_bank import GoogleReasoningBank


def google_reasoning_bank_application(binding=None, *, root=None, user_key=None, client_factory=None,
                                      memory_policy_version="1.1.0"):
    storage = Path.home() / ".shujian-cube" if root is None else Path(root)
    user = getpass.getuser() if user_key is None else user_key
    namespace = sha256(user.encode()).hexdigest()
    repository = ExperienceEvolutionRepository(storage / "experience-evolution" / namespace)
    if client_factory is None:
        def client_factory(selected):
            from lib.llm_client import load_backend
            return load_backend(Path(__file__).resolve().parents[2], backend=selected["backend"],
                model=selected["model"], role="generation", allow_global_endpoint_override=False,
                context_window_tokens=selected.get("context_window_tokens"),
                **({"reasoning": selected["reasoning"]} if "reasoning" in selected else {}))
    return GoogleReasoningBank(repository, reasoning_bank_application(root=storage,user_key=user),
        experience_evolution_application(root=storage,user_key=user),
        OfficialReasoningBankInductor(binding or {},client_factory,repository.root,
            memory_policy_version=memory_policy_version),binding)
