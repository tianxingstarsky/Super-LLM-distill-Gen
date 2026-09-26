"""Large input previews stop reading once the requested window is full."""
import json

from lib.application.workflow_service import WorkflowApplication
from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver


def test_input_preview_stops_and_closes_reader(tmp_path, monkeypatch):
    run_id = "a" * 32
    path = tmp_path / "workflows" / run_id / "input_records.json"
    path.parent.mkdir(parents=True)
    path.write_text("[]", encoding="utf-8")
    closed = []

    def records(_path):
        try:
            yield {"id": "ready", "status": "ready"}
            yield {"id": "first", "status": "quarantined"}
            yield {"id": "second", "status": "quarantined"}
            raise AssertionError("Read past requested preview")
        finally:
            closed.append(True)

    monkeypatch.setattr("lib.infrastructure.workflow_driver.iter_json_records", records)
    app = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, tmp_path))
    assert [row["id"] for row in app.quarantined_inputs(run_id, limit=2)] == ["first", "second"]
    assert closed == [True]
    assert app.quarantined_inputs(run_id, limit=0) == []


def test_input_preview_caps_large_requests_and_reads_legacy_array(tmp_path):
    run_id = "b" * 32
    path = tmp_path / "workflows" / run_id / "input_records.json"
    path.parent.mkdir(parents=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump([{"id": i, "status": "quarantined"} for i in range(50000)], handle)
    app = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, tmp_path))
    assert len(app.quarantined_inputs(run_id, limit=50000)) == 100
    assert len(app.quarantined_inputs(run_id)) == 100
