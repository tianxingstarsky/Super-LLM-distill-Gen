"""Use the existing reviewer, scheduler and checkpoint store for each window."""
from copy import deepcopy

from lib.contexts.models.contracts import ModelOutputLimitError
from ..domain.claim_windows import validate_review_window_response


class CheckpointedClaimWindowAdapter:
    def __init__(self, runtime, *, role, audit, phase, instruction):
        self.runtime = runtime
        self.role, self.audit, self.phase, self.instruction = role, audit, phase, instruction

    def check_cancel(self):
        self.runtime.check_cancel()

    def review(self, request):
        marker = ["claim_window_output_limit", request["review_window"]["sha256"]]
        if self.runtime.checkpoint_has(marker):
            saved = self.runtime.checkpoint(marker, lambda: None)
            if (not isinstance(saved, dict) or set(saved) != {"code", "request", "schema_failures"}
                    or saved["code"] != "model_output_limit" or saved["request"] != request):
                raise ValueError("checkpoint_integrity_error")
            raise ModelOutputLimitError()
        prompt_id = "workflow.review_reference" if self.phase == "claim_challenge" else "workflow.review_score"
        return self.runtime._claim_v2_call(request, prompt_id=prompt_id,
            base_key=["claim_window", self.phase, request["review_window"]["sha256"]], role=self.role,
            validator=validate_review_window_response, audit=self.audit, phase=self.phase,
            instruction=self.instruction)

    def record_output_limit(self, request):
        from ..domain.claim_windows import request_digest
        marker = ["claim_window_output_limit", request["review_window"]["sha256"]]
        failures = [failure for failure in self.audit["schema_failures"]
                    if failure["checkpoint"]["review_request_sha256"] == request_digest(request)]
        saved = self.runtime.checkpoint(marker, lambda: {"code": "model_output_limit",
            "request": deepcopy(request), "schema_failures": deepcopy(failures)})
        self.audit["schema_failures"][:] = [failure for failure in self.audit["schema_failures"] if failure not in failures]
        self.audit["window_failures"].append(saved)
