"""Execute one pinned node request through a durable checkpoint boundary."""
from .model_request_ports import NodeRequestPort
from ..domain.model_request import NodeModelRequest, request_role, validate_captured_response


class RequestNodeModel:
    def __init__(self, port: NodeRequestPort):
        self.port = port

    def execute(self, key, role, prompt_id, data, *, image=None, instruction=None,
                allow_reasoning_fallback=True, model_stage=None, prompt_stage=None,
                capture_response_details=False, reasoning_prefix=None, native_request_attempts=None,
                structured_response_attempts=None):
        recipe = self.port.recipe
        request = NodeModelRequest(
            key, request_role(recipe, model_stage or self.port.stage, role), prompt_id, data,
            image, instruction, allow_reasoning_fallback, model_stage, prompt_stage,
            capture_response_details and recipe.get("provider_reasoning_capture_version") == 1,
            reasoning_prefix, native_request_attempts, structured_response_attempts)
        request.validate()
        try:
            value = self.port.checkpoint(["call", key], lambda: self.port.invoke(request))
        except BaseException:
            self.port.finish_stream(interrupted=True)
            raise
        self.port.finish_stream(interrupted=False)
        if request.capture:
            validate_captured_response(value)
            return self.port.captured_result(value)
        return value
