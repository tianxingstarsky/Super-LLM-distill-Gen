"""Explicitly replay the pre-turn-review contract in historical engine tests.

These fixtures exercise request reuse, provenance retirement and v1 projection.
New protocol behavior is covered independently with exact per-turn receipts.
"""
from functools import wraps

import pytest

from lib.infrastructure import training_workflow as engine
from lib.io_utils import atomic_json
from lib.prompts import get, render


@pytest.fixture(autouse=True)
def legacy_review_runs(monkeypatch):
    create = engine.create_run

    @wraps(create)
    def historical(output, *args, **kwargs):
        run_id = create(output, *args, **kwargs)
        path = engine.run_path(output, run_id)
        recipe = engine.read_json(path / "recipe.json")
        recipe.update(review_context_protocol_version=1, training_surface_protocol_version=1)
        for field in ("turn_review_protocol_version", "reasoning_revision_protocol_version", "reasoning_required",
                      "claim_review_protocol_version"):
            recipe.pop(field, None)
        for prompt_id, version in (("workflow.review_score", "1.6.0"), ("workflow.review_repair", "1.7.0")):
            spec = get(prompt_id, version)
            recipe["prompts"][prompt_id] = {"version": version, "sha256": engine.digest(spec.template)}
            for templates in recipe.get("node_prompt_templates", {}).values():
                if prompt_id in templates:
                    templates[prompt_id] = render(spec)
        atomic_json(path / "recipe.json", recipe)
        state = engine.read_json(path / "state.json")
        state["recipe_hash"] = engine.digest(recipe)
        atomic_json(path / "state.json", state)
        return run_id

    monkeypatch.setattr(engine, "create_run", historical)
