"""Authenticated continuation reviews use the frozen dialogue node, not repair stage."""
from copy import deepcopy
import json
from pathlib import Path
from shutil import copyfile

import pytest

from lib.contexts.workflows.infrastructure.dialogue_adapter import CheckpointedDialogueAdapter
from lib.domain.human_augmentation import HUMAN_CHECK_FIELDS, human_design
from lib.infrastructure import training_workflow as engine
from tests.test_human_augmentation import manual


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Every model is injected at the existing backend loader boundary."""
    yield


def test_review_stage_continuation_matches_normal_dialogue_node_requests(tmp_path, monkeypatch):
    settings = tmp_path / "configs"
    settings.mkdir()
    copyfile(Path(__file__).resolve().parents[1] / "configs/preferences.yaml", settings / "preferences.yaml")
    (settings / "backends.local.yaml").write_text(json.dumps({"backends": {
        "offline": {"base_url": "http://127.0.0.1:9/v1", "models": ["author", "dialogue-reviewer", "repair-reviewer"]}}}),
        encoding="utf-8")
    prompt_ids = ("workflow.jev_score", "workflow.multiturn_consistency", "workflow.style_check",
                  "workflow.multiturn_directed_check", "workflow.human_augmentation_check")
    templates = {prompt_id: "DIALOGUE_NODE " + prompt_id for prompt_id in prompt_ids}
    models = {"multiturn": {"generation": {"backend": "offline", "model": "author"},
                              "jev": {"backend": "offline", "model": "dialogue-reviewer"}},
              "review": {"jev": {"backend": "offline", "model": "repair-reviewer"}}}
    output = tmp_path / "output"
    run_id = engine.create_run(output, settings_root=tmp_path, targets=["multiturn"],
        sources=[], brief="Make a small demo.", node_models=models,
        node_generation={"multiturn": {"enabled": True, "answer_mode": "native_joint"}},
        qa_director={"enabled": True, "planning_mode": "adaptive", "human_augmentation": manual()},
        node_prompts={"multiturn": templates}, package_review={"enabled": False})

    class CapturedReviewer:
        model = "dialogue-reviewer"

        def __init__(self):
            self.calls, self.usage = [], {"calls": 0}

        def chat(self, messages, **options):
            self.calls.append(deepcopy(messages))
            self.usage["calls"] += 1
            system = messages[0]["content"]
            if "DIALOGUE_NODE workflow.human_augmentation_check" in system:
                return json.dumps({"keep": True, "reason": "Original design retained.",
                                   **{name: True for name in HUMAN_CHECK_FIELDS}})
            if any("DIALOGUE_NODE " + name in system for name in prompt_ids[:2]):
                return json.dumps({"keep": True, "grounded": True, "reasoning_valid": True,
                    "correctness": 5, "scores": {name: 5 for name in
                        ("correctness", "reasoning", "grounding", "instruction", "safety")},
                    "reason": "Actual messages checked."})
            return json.dumps({"keep": True, "adherence": 5, "reason": "Actual instructions checked."})

    reviewer, loaded = CapturedReviewer(), []

    def load_backend(root, **options):
        loaded.append(deepcopy(options))
        assert options["model"] == "dialogue-reviewer"
        return reviewer, {}

    monkeypatch.setattr(engine, "load_backend", load_backend)
    runtime = engine.Workflow(output, run_id, tmp_path)
    frozen = deepcopy(runtime.recipe)
    unit = {"kind": "brief", "text": "Make a small demo.", "qa_contract": {
        "question": "Can I make a demo?", "visible_context": "", "qa_type": "closed_book",
        "answer_policy": "answer", "human_design": human_design(manual())}}
    messages = [{"role": "user", "content": "Can I make a demo?"},
                {"role": "assistant", "content": "Try it locally first.", "reasoning_content": "I need to check the conditions."}]
    for scoped in (False, True):
        runtime.stage = "review" if scoped else "multiturn"
        before_stage = runtime.stage
        adapter = CheckpointedDialogueAdapter(runtime, model_stage="multiturn" if scoped else None)
        adapter.assess([scoped, "turn"], unit, messages[-1])
        adapter.assess([scoped, "whole"], unit, messages, prompt_id="workflow.multiturn_consistency")
        adapter.check_style([scoped, "style"], {"style": "concise"}, messages[-1]["reasoning_content"], messages[-1]["content"])
        adapter.check_contract([scoped, "contract"], unit, messages, prompt_id="workflow.multiturn_directed_check")
        assert runtime.stage == before_stage
    assert len(reviewer.calls) == 10
    assert reviewer.calls[:5] == reviewer.calls[5:]
    for prompt_id, request in zip(prompt_ids, reviewer.calls[5:]):
        assert templates[prompt_id] in request[0]["content"]
    assert len(loaded) == 1 and loaded[0]["role"] == "jev"
    assert "multiturn.jev" in runtime.state["models"]
    assert "review.jev" not in runtime.state["models"]
    assert runtime.recipe == frozen
    assert engine.read_json(runtime.path / "recipe.json") == frozen
