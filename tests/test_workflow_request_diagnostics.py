"""Only validated transport facts survive a run failure and later recovery."""
import json

import pytest

from lib.infrastructure import training_workflow as engine
from lib.model_request_reliability import ModelRequestError, filter_request_diagnostic


def _safe():
    return {"phase": "stream", "exception_types": ["ModelStreamError", "ReadError"],
        "headers_received": True, "elapsed_seconds": 602.1234567,
        "headers_elapsed_seconds": 1.2, "first_output_elapsed_seconds": None,
        "http_status": 200, "provider_request_id": "req_safe", "provider_error_code": "overloaded_error"}


def test_filter_ignores_private_fields_and_copies_valid_plain_json():
    value = {**_safe(), "message": "PRIVATE", "headers": {"Authorization": "PRIVATE"},
             "body": {"source": "PRIVATE"}, "url": "https://private.invalid?key=PRIVATE"}
    result = filter_request_diagnostic(value)
    assert result == {**_safe(), "elapsed_seconds": 602.123457}
    assert "PRIVATE" not in json.dumps(result)
    value["exception_types"].append("APIError")
    assert result["exception_types"] == ["ModelStreamError", "ReadError"]


@pytest.mark.parametrize("value", [None, [], "PRIVATE", {},
    {"phase": "PRIVATE", "exception_types": ["ReadError"]},
    {"phase": "stream", "exception_types": ["PRIVATE"]},
    {"phase": "stream", "exception_types": "ReadError"},
    {"phase": "stream", "exception_types": ["ReadError"] * 7},
    {"phase": "stream", "exception_types": [{"ReadError": "PRIVATE"}]}])
def test_filter_rejects_invalid_required_shape(value):
    assert filter_request_diagnostic(value) is None


def test_filter_drops_invalid_scalars_without_coercing_or_raising():
    value = {**_safe(), "headers_received": "PRIVATE", "elapsed_seconds": 10 ** 1000,
        "headers_elapsed_seconds": float("nan"), "first_output_elapsed_seconds": -1,
        "http_status": True, "provider_request_id": "https://private.invalid?key=PRIVATE",
        "provider_error_code": "PRIVATE"}
    assert filter_request_diagnostic(value) == {"phase": "stream",
        "exception_types": ["ModelStreamError", "ReadError"]}


def test_filter_does_not_call_custom_container_hooks():
    class Untrusted(dict):
        def get(self, *args):
            pytest.fail("Diagnostic objects are not trusted")
    assert filter_request_diagnostic(Untrusted()) is None


def test_run_failure_persists_filtered_diagnostic_then_resume_clears_only_current_state(tmp_path):
    output = tmp_path / "output"
    source = tmp_path / "source.txt"
    source.write_text("停止设备前先保存运行记录。", encoding="utf-8")
    run_id = engine.create_run(output, sources=[source], targets=["sft"],
                              production={"goals": {"sft": 1}})
    class LifecycleWorkflow(engine.Workflow):
        fail = True
        def _execute_production(self):
            self.stage = "sft"
            if self.fail:
                raise ModelRequestError("service_connection_failed", "transient", 1,
                    diagnostics={**_safe(), "Authorization": "PRIVATE", "body": {"text": "PRIVATE"}})
            self.state["status"] = "completed"
            self.event("run_finished")
            return self.state
    failed = LifecycleWorkflow(output, run_id, tmp_path).execute()
    assert failed["status"] == "failed"
    assert failed["request_failure"] == filter_request_diagnostic(_safe())
    path = engine.run_path(output, run_id)
    original_events = (path / "events.jsonl").read_text(encoding="utf-8")
    assert "PRIVATE" not in original_events + (path / "state.json").read_text(encoding="utf-8")
    failure_event = next(json.loads(line) for line in original_events.splitlines()
                         if json.loads(line)["kind"] == "run_failed")
    assert failure_event["request_failure"] == failed["request_failure"]
    resumed = LifecycleWorkflow(output, run_id, tmp_path)
    resumed.fail = False
    successful = resumed.execute(resume_run=True)
    assert successful["status"] == "completed"
    assert "request_failure" not in successful and "error" not in successful
    assert (path / "events.jsonl").read_text(encoding="utf-8").startswith(original_events)


def test_invalid_diagnostic_is_not_persisted_on_run_failure(tmp_path):
    output = tmp_path / "output"
    run_id = engine.create_run(output, brief="帮助用户解释设备使用方法", targets=["sft"],
                              production={"goals": {"sft": 1}})
    class FailedWorkflow(engine.Workflow):
        def _execute_production(self):
            raise ModelRequestError("service_timeout", "transient", 1,
                                    diagnostics={"message": "PRIVATE", "body": {"api_key": "PRIVATE"}})
    state = FailedWorkflow(output, run_id, tmp_path).execute()
    assert state["status"] == "failed" and "request_failure" not in state
    path = engine.run_path(output, run_id)
    event = next(json.loads(line) for line in (path / "events.jsonl").read_text(encoding="utf-8").splitlines()
                 if json.loads(line)["kind"] == "run_failed")
    assert "request_failure" not in event
    assert "PRIVATE" not in json.dumps(event)
