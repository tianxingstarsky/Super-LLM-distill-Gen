"""Construct both saved protocol-1 jobs and new protocol-2 jobs offline."""
import pytest

from lib.infrastructure import training_workflow as engine
from lib.io_utils import atomic_json


@pytest.fixture(autouse=True, params=[1, 2])
def memory_protocol_versions(request, monkeypatch):
    """Pin a test recipe before it runs; never alter a result or a grade.

    Protocol 1 simulates an immutable historical job created by the previous
    version. Protocol 2 exercises the new normal default. The real creation
    validator and source snapshots run in both cases.
    """
    create = engine.create_run
    def versioned_create(*args, **kwargs):
        run_id = create(*args, **kwargs)
        output = args[0] if args else kwargs["output"]
        path = engine.run_path(output, run_id)
        recipe = engine.read_json(path / "recipe.json")
        state = engine.read_json(path / "state.json")
        assert state["status"] == "queued" and not state["usage"] and state["attempt"] == 0
        assert all(stage["status"] == "pending" for stage in state["stages"].values())
        recipe["source_memory_protocol_version"] = request.param
        state["recipe_hash"] = engine.digest(recipe)
        atomic_json(path / "recipe.json", recipe)
        atomic_json(path / "state.json", state)
        return run_id
    monkeypatch.setattr(engine, "create_run", versioned_create)
    return request.param
