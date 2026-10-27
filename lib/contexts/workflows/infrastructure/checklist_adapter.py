"""Pin follow-up audits to the original CoT node's models and prompt snapshot."""


class CheckpointedChecklistAdapter:
    def __init__(self, runtime):
        self.runtime = runtime

    def request(self, key, role, prompt_id, payload, **options):
        return self.runtime.ask(key, role, prompt_id, payload,
                                prompt_stage="cot", model_stage="cot", **options)

    def check_reasoning_cancel(self):
        self.runtime.check_cancel()

    def prepare_reasoning_context(self, context):
        from .reasoning_agent_adapter import CheckpointedReasoningAgent
        return CheckpointedReasoningAgent(self.runtime).prepare_reasoning_context(context)

    def reasoning_progress(self, *args):
        from .reasoning_agent_adapter import CheckpointedReasoningAgent
        return CheckpointedReasoningAgent(self.runtime).reasoning_progress(*args)

    def reasoning_tool_turn(self, *args):
        from .reasoning_agent_adapter import CheckpointedReasoningAgent
        return CheckpointedReasoningAgent(self.runtime).reasoning_tool_turn(*args)

    def reasoning_review_identity(self):
        from .reasoning_agent_adapter import CheckpointedReasoningAgent
        return CheckpointedReasoningAgent(self.runtime).reasoning_review_identity()

    def checkpoint_has(self, key):
        return self.runtime.checkpoint_has(key)

    def checkpoint(self, key, action):
        return self.runtime.checkpoint(key, action)
