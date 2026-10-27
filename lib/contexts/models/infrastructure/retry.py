"""Tenacity is the sole request retry engine; the use case owns retry eligibility."""
from tenacity import Retrying, retry_if_result

from ..application.retry_port import RetryDirective


class TenacityRetry:
    def run(self, action, *, attempts, wait):
        if type(attempts) is not int or attempts < 1:
            raise ValueError("invalid_model_request_attempts")
        counted = 0

        def invoke():
            nonlocal counted
            result = action(counted)
            if isinstance(result, RetryDirective):
                counted += int(result.counted)
            return result

        def exhausted(state):
            raise state.outcome.result().error from None

        controller = Retrying(
            retry=retry_if_result(lambda result: isinstance(result, RetryDirective)),
            stop=lambda state: (not state.outcome.result().retryable or counted >= attempts
                                or state.attempt_number >= attempts + 1),
            wait=lambda state: state.outcome.result().delay,
            # Waiting is cancellable and happens after admission/settlement.
            sleep=wait, retry_error_callback=exhausted, reraise=True,
        )
        return controller(invoke)
