"""Continue the authenticated native author through real tool feedback."""
from copy import deepcopy

from .authored_edit_adapter import author_identity
from .reasoning_agent_adapter import CheckpointedReasoningAgent


class NativeFeedbackAdapter:
    def __init__(self, runtime):
        self.runtime = runtime

    def check_reasoning_cancel(self):
        self.runtime.check_cancel()

    def continue_prefix(self, key, session, pending_messages, *, reasoning_prefix):
        """Continue the bound author's native reasoning without a tool round."""
        return self._prefix_response(key, session, pending_messages,
            reasoning_prefix=reasoning_prefix, mode="reasoning")

    def finalize_prefix(self, key, session, pending_messages, *, reasoning_prefix):
        """Finish an authenticated saved reasoning response at the answer boundary."""
        return self._prefix_response(key, session, pending_messages,
            reasoning_prefix=reasoning_prefix, mode="answer")

    def _prefix_response(self, key, session, pending_messages, *, reasoning_prefix, mode):
        if (session.get("version") != 1 or session.get("role") != "generation"
                or session.get("stage") not in {"sft", "multiturn", "cot"}):
            raise ValueError("native_feedback_original_session_invalid")
        original = session.get("messages")
        if not original or pending_messages[:len(original)] != original:
            raise ValueError("native_feedback_original_prefix_changed")
        options = session.get("request_options")
        if not isinstance(options, dict) or set(options) != {"max_tokens", "temperature"}:
            raise ValueError("native_feedback_request_options_invalid")
        runtime, stage = self.runtime, session["stage"]
        client = runtime.client("generation", stage=stage)
        binding = runtime.recipe.get("node_models", {}).get(stage, {}).get("generation", {})
        if author_identity(client, binding) != session.get("author"):
            raise ValueError("native_feedback_original_model_changed")
        from lib.model_reasoning_prefill import prepare_reasoning_prefill
        request_messages, _ = prepare_reasoning_prefill(pending_messages, reasoning_prefix,
            api_format=getattr(client, "api_format", None), base_url=getattr(client, "base_url", ""),
            reasoning=getattr(client, "reasoning", None), thinking=True, reasoning_prefix_mode=mode)
        response = CheckpointedReasoningAgent(runtime).prepared_prefix_turn(
            [*key, session["author"]], "generation", pending_messages,
            reasoning_prefix=reasoning_prefix, reasoning_prefix_mode=mode, model_stage=stage,
            request_options={**deepcopy(options), "retries": 1})
        return {**response, "request_messages": request_messages,
            "author_continuation": {"version": 1, "author": session["author"],
                "model_stage": stage, "message_prefix_preserved": True,
                "tools_preserved": session.get("tools") == [],
                "transport": "deepseek_chat_beta"}}

    def turn(self, key, session, messages, tools, *, final=False):
        """Transport one bounded turn; the application owns tool calls and findings."""
        if (not isinstance(session, dict) or type(session.get("version")) is not int
                or session["version"] != 1 or session.get("role") != "generation"
                or not isinstance(session.get("stage"), str)
                or session.get("stage") not in {"sft", "multiturn", "cot"}):
            raise ValueError("native_feedback_original_session_invalid")
        original_messages = session.get("messages")
        if (not isinstance(original_messages, list) or not original_messages
                or not isinstance(messages, list)
                or messages[:len(original_messages)] != original_messages):
            raise ValueError("native_feedback_original_prefix_changed")
        options = session.get("request_options")
        if not isinstance(options, dict) or set(options) != {"max_tokens", "temperature"}:
            raise ValueError("native_feedback_request_options_invalid")

        runtime, stage = self.runtime, session["stage"]
        client = runtime.client("generation", stage=stage)
        binding = runtime.recipe.get("node_models", {}).get(stage, {}).get("generation", {})
        if author_identity(client, binding) != session.get("author"):
            raise ValueError("native_feedback_original_model_changed")
        capabilities = getattr(client, "model_info", None) or {}
        if (getattr(client, "api_format", None) != "chat"
                or not callable(getattr(client, "tool_turn", None))
                or tools and capabilities.get("tools") is False
                or capabilities.get("reasoning") is False):
            raise ValueError("native_feedback_native_tools_unsupported")
        if (getattr(client, "reasoning", None) or {}).get("mode") != "enabled":
            raise ValueError("native_feedback_thinking_not_enabled")

        response = CheckpointedReasoningAgent(runtime).prepared_tool_turn(
            [*key, session["author"]], "generation", messages, tools,
            model_stage=stage, capture_details=True, strict=False, final=final,
            request_options={**deepcopy(options), "retries": 1})
        return {**response, "author_continuation": {
            "version": 1, "author": session["author"], "model_stage": stage,
            "message_prefix_preserved": True, "tools_preserved": tools == session.get("tools"),
        }}
