from lib.infrastructure import workflow_task_inventory as inventory
from lib.infrastructure.training_workflow import atomic_json, create_run, read_json, run_path


def test_inventory_reuses_unchanged_summary_and_refreshes_atomic_state(tmp_path, monkeypatch):
    source = tmp_path / "guide.txt"
    source.write_text("Inspect the source.", encoding="utf-8")
    output = tmp_path / "out"
    rid = create_run(output, sources=[source], targets=["cpt"])
    path = run_path(output, rid) / "state.json"
    state = read_json(path)
    state["events"] = [{"kind": f"event-{index}"} for index in range(100)]
    state["quality"] = {"large": "x" * 100000}
    atomic_json(path, state)
    calls = []
    def counted(path):
        calls.append(path)
        return read_json(path)
    monkeypatch.setattr(inventory, "read_json", counted)
    first = inventory.task_runs(output)
    assert len(calls) == 1
    assert "quality" not in first[0]
    assert [event["kind"] for event in first[0]["events"]] == ["event-97", "event-98", "event-99"]
    first[0]["name"] = "caller mutation"
    second = inventory.task_runs(output)
    assert len(calls) == 1 and second[0]["name"] == state["name"]
    state["status"] = "running"
    atomic_json(path, state)
    assert inventory.task_runs(output)[0]["status"] == "running"
    assert len(calls) == 2


def test_inventory_retries_state_replacement_and_ignores_broken_entries(tmp_path, monkeypatch):
    source = tmp_path / "guide.txt"
    source.write_text("Inspect the source.", encoding="utf-8")
    output = tmp_path / "out"
    rid = create_run(output, sources=[source], targets=["cpt"])
    path = run_path(output, rid) / "state.json"
    reads = []
    def replaced(path):
        state = read_json(path)
        reads.append(path)
        if len(reads) == 1:
            atomic_json(path, {**state, "status": "completed"})
        return state
    monkeypatch.setattr(inventory, "read_json", replaced)
    assert inventory.task_runs(output)[0]["status"] == "completed"
    assert len(reads) == 2
    broken = output / "workflows" / ("f" * 32) / "state.json"
    broken.parent.mkdir()
    broken.write_text("{", encoding="utf-8")
    assert [row["id"] for row in inventory.task_runs(output)] == [rid]
