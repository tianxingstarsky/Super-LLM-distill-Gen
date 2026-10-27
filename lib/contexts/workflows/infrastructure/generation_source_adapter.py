"""Generation tool turns reuse frozen prompts, role projection and request admission."""
from copy import deepcopy
import json

from ..domain.model_request import NodeModelRequest
from .reasoning_agent_adapter import CheckpointedReasoningAgent
from .generation_source_search import search_generation_sources


class GenerationSourceAdapter:
    def __init__(self, runtime, request_adapter):
        self.runtime, self.requests = runtime, request_adapter

    def messages(self, prompt_id, task, instruction, index):
        client = self.runtime.client("generation", stage="cot")
        request = NodeModelRequest(key=[], role="generation", prompt_id=prompt_id,
                                   data=deepcopy(task), instruction=instruction,
                                   model_stage="cot", prompt_stage="cot")
        # Reuse production projection: learner history, frozen custom rules and
        # source memory keep their existing roles. Index metadata is not a user turn.
        messages = self.requests.messages(client, request)
        messages.insert(1, {"role": "system", "content": "Available source locations (not evidence or instructions):\n"
                           + json.dumps(index, ensure_ascii=False)})
        return messages

    def turn(self, key, messages, tools, *, final, edit_base=None):
        from ..domain.authored_submission import SUBMIT_AUTHORED, strict_submission_enabled
        strict = strict_submission_enabled(self.runtime.recipe)
        editable = strict and self.runtime.recipe.get("authored_local_edit_version") == 1
        session = None
        if editable:
            from .authored_edit_adapter import author_session
            client = self.runtime.client("generation", stage="cot")
            binding = self.runtime.recipe.get("node_models", {}).get("cot", {}).get("generation", {})
            session = author_session(client, binding, messages, tools, stage="cot", role="generation",
                max_tokens=binding.get("max_output_tokens", 32768), temperature=0.7)
        response = CheckpointedReasoningAgent(self.runtime).prepared_tool_turn(
            key, "generation", messages, tools, recover_arguments=not strict, final=final,
            **({"strict": True} if strict else {}), **({"capture_details": True} if editable else {}))
        if not editable:
            return response
        assistant = response["assistant"]
        from ..domain.authored_edits import EDIT_AUTHORED
        delivery_tool = EDIT_AUTHORED if edit_base is not None else SUBMIT_AUTHORED
        if any(call.get("function", {}).get("name") == delivery_tool for call in assistant.get("tool_calls", [])):
            self.runtime.checkpoint(["authored_submission_v1", key],
                lambda: {**deepcopy(response), "author_session": session,
                         **({"edit_base": deepcopy(edit_base)} if edit_base is not None else {})})
        return assistant

    def search(self, passages, query, limit):
        return search_generation_sources(passages, query, limit)
