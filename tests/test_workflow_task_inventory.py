from lib.infrastructure import workflow_task_inventory as inventory
from lib.infrastructure.training_workflow import atomic_json, create_run, read_json, run_path
import pytest


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
    assert len(calls) == 2
    assert "quality" not in first[0]
    assert [event["kind"] for event in first[0]["events"]] == ["event-97", "event-98", "event-99"]
    first[0]["name"] = "caller mutation"
    second = inventory.task_runs(output)
    assert len(calls) == 2 and second[0]["name"] == state["name"]
    state["status"] = "running"
    atomic_json(path, state)
    assert inventory.task_runs(output)[0]["status"] == "running"
    assert len(calls) == 4


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
    assert len([path for path in reads if path.name == "state.json"]) == 2
    broken = output / "workflows" / ("f" * 32) / "state.json"
    broken.parent.mkdir()
    broken.write_text("{", encoding="utf-8")
    assert [row["id"] for row in inventory.task_runs(output)] == [rid]


def test_recipe_changes_refresh_source_summary_without_hiding_history(tmp_path):
    source = tmp_path / "original.txt"
    source.write_text("Persistent input.", encoding="utf-8")
    output = tmp_path / "out"
    rid = create_run(output, sources=[source], targets=["cpt"])
    recipe_path = run_path(output, rid) / "recipe.json"
    state_bytes = recipe_path.with_name("state.json").read_bytes()
    assert inventory.task_runs(output)[0]["source_names"] == ["original.txt"]

    recipe = read_json(recipe_path)
    recipe["sources"][0]["name"] = "updated.jsonl"
    recipe["sources"][0]["file"] = "0000.jsonl"
    atomic_json(recipe_path, recipe)
    changed = inventory.task_runs(output)[0]
    assert changed["source_names"] == ["updated.jsonl"]
    assert changed["source_mode"] == "agent"
    assert recipe_path.with_name("state.json").read_bytes() == state_bytes

    recipe_path.write_text("{", encoding="utf-8")
    broken = inventory.task_runs(output)
    assert [row["id"] for row in broken] == [rid]
    assert broken[0]["source_mode"] == "unknown" and broken[0]["source_names"] == []
    recipe_path.unlink()
    assert inventory.task_runs(output)[0]["id"] == rid

    atomic_json(recipe_path, {"sources": [], "brief": "Find a new maintenance workflow."})
    repaired = inventory.task_runs(output)[0]
    assert repaired["source_mode"] == "brief"
    assert repaired["source_brief"] == "Find a new maintenance workflow."


def test_inventory_retries_recipe_replacement_instead_of_caching_old_names(tmp_path, monkeypatch):
    source = tmp_path / "original.txt"
    source.write_text("Persistent input.", encoding="utf-8")
    output = tmp_path / "out"
    rid = create_run(output, sources=[source], targets=["cpt"])
    recipe_path = run_path(output, rid) / "recipe.json"
    replaced = []

    def replace_after_read(path):
        value = read_json(path)
        if path == recipe_path and not replaced:
            replaced.append(True)
            atomic_json(path, {**value, "sources": [{"name": "replacement.txt"}]})
        return value

    monkeypatch.setattr(inventory, "read_json", replace_after_read)
    summary = inventory.task_runs(output)[0]
    assert replaced == [True]
    assert summary["id"] == rid and summary["source_names"] == ["replacement.txt"]
    assert inventory.task_runs(output)[0]["source_names"] == ["replacement.txt"]


def test_saved_source_mode_overrides_legacy_extension_inference(tmp_path):
    source = tmp_path / "conversation.jsonl"
    source.write_text('{"messages": []}\n', encoding="utf-8")
    output = tmp_path / "out"
    rid = create_run(output, sources=[source], targets=["cpt"])
    recipe_path = run_path(output, rid) / "recipe.json"
    recipe = read_json(recipe_path)
    atomic_json(recipe_path, {**recipe, "source_mode": "document"})
    assert inventory.task_runs(output)[0]["source_mode"] == "document"


@pytest.mark.parametrize("recipe", [[], {}, {"sources": "invalid", "targets": ["cpt"]},
                                   {"sources": [1], "targets": ["cpt"]},
                                   {"sources": [], "brief": 1, "targets": ["cpt"]}])
def test_malformed_recipe_keeps_task_and_disables_unreadable_detail(tmp_path, recipe):
    source = tmp_path / "guide.txt"
    source.write_text("Keep this history.", encoding="utf-8")
    output = tmp_path / "out"
    rid = create_run(output, sources=[source], targets=["cpt"])
    atomic_json(run_path(output, rid) / "recipe.json", recipe)
    rows = inventory.task_runs(output)
    assert [row["id"] for row in rows] == [rid]
    assert rows[0]["source_mode"] == "unknown"
    assert rows[0]["recipe_readable"] is False
