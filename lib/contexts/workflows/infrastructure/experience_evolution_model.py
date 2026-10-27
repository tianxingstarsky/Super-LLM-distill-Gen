"""One strict proposal call through the existing provider gateway, with receipts."""
from copy import deepcopy
import json
import time

from lib.io_utils import atomic_json
from ..domain.experience_evolution import EXPERIENCE_PROPOSAL_SCHEMA, digest


class ExperienceEvolutionModelPort:
    def __init__(self, repository, settings_root, binding=None, *, client_factory=None):
        self.repository, self.settings_root = repository, settings_root
        self.binding = deepcopy(binding)
        self.client_factory = client_factory
        self._receipt = None

    def propose(self, payload):
        from lib.prompts import get, render
        if not self.binding or not self.binding.get("backend") or not self.binding.get("model"):
            raise ValueError("experience_evolution_model_required")
        binding = self.binding
        client, model = self.client_factory(binding)
        spec = get("workflow.evolve_experience", version="1.0.0")
        request = {"model": model, "endpoint": client.base_url, "api_format": client.api_format,
            "binding": binding, "prompt": {"id": spec.id, "version": spec.version, "text": render(spec)},
            "payload": payload, "submission_schema": EXPERIENCE_PROPOSAL_SCHEMA}
        directory = self.repository.root / "requests"
        directory.mkdir(exist_ok=True)
        identity = digest(request)
        path = directory / (identity + ".json")
        try:
            if path.exists():
                saved = json.loads(path.read_text(encoding="utf-8"))
                if saved.get("request") != request:
                    raise ValueError("experience_evolution_request_changed")
            else:
                messages = [{"role": "system", "content": render(spec)},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
                tool = {"type": "function", "function": {"name": "submit_experience_rules",
                    "strict": True,
                    "description": "Submit the consolidated style and evidence-bound lessons.",
                    "parameters": EXPERIENCE_PROPOSAL_SCHEMA}}
                details, started = {}, time.time()
                assistant = client.tool_turn(messages, [tool], strict=True,
                    max_tokens=binding.get("max_output_tokens") or 32768,
                    retries=1, response_details=details)
                saved = {"request": request, "assistant": assistant, "response_details": details,
                    "seconds": time.time()-started}
                # Save the real provider result before parsing or validating it.
                atomic_json(path, saved)
            self._receipt = {"request_id": identity, "model": model, "backend": binding["backend"],
                "prompt_version": spec.version, "seconds": saved["seconds"],
                "response_details": saved.get("response_details", {})}
            calls = saved["assistant"].get("tool_calls", [])
            if len(calls) != 1 or calls[0].get("function", {}).get("name") != "submit_experience_rules":
                raise ValueError("experience_evolution_submission_required")
            arguments = calls[0]["function"]["arguments"]
            return json.loads(arguments) if isinstance(arguments, str) else arguments
        finally:
            client.client.close()

    def save(self, snapshot):
        if snapshot.get("status") == "candidate" and self._receipt:
            snapshot = {**snapshot, "model_request_receipt": deepcopy(self._receipt)}
        return self.repository.save(snapshot)

    def get_version(self, identifier):
        return self.repository.get_version(identifier)

    def list_versions(self, scope):
        return self.repository.list_versions(scope)
