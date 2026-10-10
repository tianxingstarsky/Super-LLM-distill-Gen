"""Human question and answer designs survive drafts, copies and restarts."""
from copy import deepcopy
import hashlib
import json

import pytest
from streamlit.testing.v1 import AppTest

from lib.bootstrap.creation_drafts import creation_draft_application
from lib.domain.creation_draft import validate_creation_draft
from lib.domain.human_augmentation import validate_human_augmentation
from lib.presentation.streamlit.workflow_reuse import recipe_to_draft


def human_config():
    return validate_human_augmentation({
        "enabled": True,
        "seeds": [{"question": "When should the pump be stopped?",
                   "answer": "Stop it if the seal leaks. Keep the power off until inspection.",
                   "question_requirements": "Ask as an operator who has noticed a leak.",
                   "answer_requirements": "Keep both stop and inspection conditions."}],
        "question_requirements": "Use different natural wording with the same intent.",
        "answer_requirements": "Preserve facts, negation and operating conditions.",
    })


def draft_values():
    return {
        "workflow-name:w": "Unfinished operator examples",
        "workflow-creation-mode:w": "人工问答增强",
        "workflow-human-enabled:w": True,
        "workflow-human-seeds:w": [{"question": "", "answer": "  ",
                                    "question_requirements": "Keep the question practical.",
                                    "answer_requirements": ""}],
        "workflow-human-question-requirements:w": "",
        "workflow-human-answer-requirements:w": "Preserve important conditions.",
    }


def recipe(*, sources=None):
    return {
        "sources": sources or [{"kind": "human_design", "name": "人工设计问答",
                                "file": "human-design.json", "sha256": "a" * 64}],
        "brief": "", "targets": ["sft", "multiturn"],
        "qa_director": {"enabled": True, "planning_mode": "adaptive", "human_augmentation": human_config()},
    }


def test_incomplete_question_and_answer_draft_survives_restart_and_named_restore(tmp_path):
    supplied = draft_values()
    application = creation_draft_application(tmp_path)
    application.replace(supplied)
    identifier = application.save_snapshot()
    supplied["workflow-human-seeds:w"][0]["answer"] = "Edited later"
    restored = creation_draft_application(tmp_path).load()
    assert restored["workflow-human-seeds:w"][0]["answer"] == "  "
    application.replace({"workflow-name:w": "Another unfinished task"})
    assert creation_draft_application(tmp_path).restore_snapshot(identifier) == restored


@pytest.mark.parametrize("mode", ["自动生成", "人工问答增强", "人工制作图文"])
def test_all_creation_modes_are_persistable(mode):
    assert validate_creation_draft({"workflow-creation-mode:w": mode}) == {"workflow-creation-mode:w": mode}


@pytest.mark.parametrize("key,value", [
    ("workflow-creation-mode:w", "unknown"),
    ("workflow-creation-mode:w:extra", "人工问答增强"),
    ("workflow-human-enabled:w", 1),
    ("workflow-human-enabled:w:extra", True),
    ("workflow-human-seeds:w:extra", []),
    ("workflow-human-seeds:w", {}),
    ("workflow-human-seeds:w", ["Question"]),
    ("workflow-human-seeds:w", [{"question": "Missing answer"}]),
    ("workflow-human-seeds:w", [{"question": "", "answer": "", "api_key": "Never persist"}]),
    ("workflow-human-seeds:w", [{"question": 1, "answer": ""}]),
    ("workflow-human-seeds:w", [{"question": "", "answer": None}]),
    ("workflow-human-seeds:w", [{"question": "", "answer": "", "answer_requirements": True}]),
    ("workflow-human-seeds:w", [{"question": "x" * 12_001, "answer": ""}]),
    ("workflow-human-seeds:w", [{"question": "", "answer": "x" * 12_001}]),
    ("workflow-human-seeds:w", [{"question": "", "answer": "", "question_requirements": "x" * 6001}]),
    ("workflow-human-seeds:w", [{"question": "", "answer": "", "id": "a" * 64}]),
    ("workflow-human-question-requirements:w", "x" * 6001),
    ("workflow-human-answer-requirements:w", 12),
    ("workflow-human-answer-requirements:w:extra", "Valid text"),
])
def test_invalid_human_draft_fields_are_rejected(key, value):
    with pytest.raises(ValueError, match="invalid_creation_draft"):
        validate_creation_draft({key: value})


@pytest.mark.parametrize("secret", ["sk-" + "x" * 24, "api_key=abcdefghijklmnop",
                                   "-----BEGIN PRIVATE KEY-----", "broken\x00text", "broken\ufffdtext"])
def test_secret_or_broken_design_never_replaces_saved_draft(tmp_path, secret):
    application = creation_draft_application(tmp_path)
    original = draft_values()
    application.replace(original)
    before = (tmp_path / ".creation-draft.json").read_bytes()
    for values in (
        {"workflow-human-question-requirements:w": secret},
        {"workflow-human-seeds:w": [{"question": "", "answer": secret}]},
        {"workflow-human-seeds:w": [{"question": "", "answer": "", "answer_requirements": secret}]},
    ):
        with pytest.raises(ValueError, match="invalid_creation_draft"):
            application.update(values)
        assert (tmp_path / ".creation-draft.json").read_bytes() == before
        assert application.load() == original


def test_draft_seed_count_is_bounded_but_does_not_require_completed_designs():
    rows = [{"question": "", "answer": ""} for _ in range(200)]
    assert validate_creation_draft({"workflow-human-seeds:w": rows})["workflow-human-seeds:w"] == rows
    with pytest.raises(ValueError, match="invalid_creation_draft"):
        validate_creation_draft({"workflow-human-seeds:w": rows + [{"question": "", "answer": ""}]})


def test_all_human_design_text_shares_one_total_size_bound():
    rows = [{"question": "q" * 12_000, "answer": "a" * 12_000} for _ in range(8)]
    assert validate_creation_draft({"workflow-human-seeds:w": rows})
    with pytest.raises(ValueError, match="invalid_creation_draft"):
        validate_creation_draft({"workflow-human-seeds:w": rows,
                                 "workflow-human-question-requirements:w": "q" * 6000,
                                 "workflow-human-answer-requirements:w": "a" * 6000})


def test_normalized_seed_id_is_allowed_until_its_design_is_edited():
    seed = human_config()["seeds"][0]
    assert validate_creation_draft({"workflow-human-seeds:w": [seed]})
    edited = {**seed, "answer": "A different answer"}
    with pytest.raises(ValueError, match="invalid_creation_draft"):
        validate_creation_draft({"workflow-human-seeds:w": [edited]})


def test_copy_human_only_recipe_restores_designs_without_a_missing_file_warning():
    original = recipe()
    before = deepcopy(original)
    copied = recipe_to_draft(original, "Human-designed variants", "w", [])
    values = copied["values"]
    assert values["workflow-creation-mode:w"] == "人工问答增强"
    assert values["workflow-source-mode:w"] == "开放需求"
    assert values["workflow-human-enabled:w"] is True
    assert copied["missing_sources"] == []
    assert not any(key.startswith("workflow-sources:") for key in values)
    human = original["qa_director"]["human_augmentation"]
    assert values["workflow-human-seeds:w"] == [{key: value for key, value in seed.items() if key != "id"}
                                                for seed in human["seeds"]]
    assert values["workflow-human-question-requirements:w"] == human["question_requirements"]
    assert values["workflow-human-answer-requirements:w"] == human["answer_requirements"]
    values["workflow-human-seeds:w"][0]["answer"] = "Editable copy"
    assert original == before


def test_copy_mixed_human_and_document_sources_keeps_only_matching_real_sources(tmp_path):
    document = tmp_path / "maintenance.md"
    document.write_text("Stop the pump if the seal leaks.", encoding="utf-8")
    file_source = {"name": document.name, "file": "0000.md",
                   "sha256": hashlib.sha256(document.read_bytes()).hexdigest()}
    original = recipe(sources=[recipe()["sources"][0], file_source,
                               {"name": "missing.md", "file": "0001.md", "sha256": "b" * 64}])
    copied = recipe_to_draft(original, "Grounded human designs", "w", [{"path": str(document)}])
    assert copied["values"]["workflow-source-mode:w"] == "文档资料"
    assert copied["values"]["workflow-sources:w:文档资料"] == [str(document)]
    assert copied["missing_sources"] == ["missing.md"]
    assert copied["values"]["workflow-creation-mode:w"] == "人工问答增强"


def test_ordinary_recipe_copy_clears_an_old_human_mode():
    ordinary = {"sources": [], "brief": "Generate operator conversations", "targets": ["sft"]}
    values = recipe_to_draft(ordinary, "Automatic task", "w", [])["values"]
    assert values["workflow-creation-mode:w"] == "自动生成"
    assert values["workflow-human-enabled:w"] is False
    assert not any(key.startswith("workflow-human-seeds:") for key in values)


def copy_screen(output, supplied_recipe):
    import streamlit as st
    from pathlib import Path
    from lib.bootstrap.creation_drafts import creation_draft_application
    from lib.presentation.streamlit.workflow_reuse import reuse_run_as_draft

    class Application:
        def recipe(self, run_id): return supplied_recipe
        def state(self, run_id): return {"name": "Human source task"}
        def source_files(self, *args, **kwargs):
            raise AssertionError("Human design snapshots must not trigger file lookup")

    st.button("Copy", key="copy", on_click=reuse_run_as_draft,
              args=(Application(), creation_draft_application(Path(output)), "w", "test-run"))


def test_copy_callback_preserves_prior_incomplete_draft_and_skips_human_snapshot_lookup(tmp_path):
    drafts = creation_draft_application(tmp_path)
    prior = draft_values()
    drafts.replace(prior)
    original = recipe()
    before = json.dumps(original, sort_keys=True)
    ui = AppTest.from_function(copy_screen, args=(str(tmp_path), original)).run()
    ui.button(key="copy").click().run()
    assert not ui.exception
    assert ui.session_state["nav"] == "自动工作流"
    current = drafts.load()
    assert current == ui.session_state["workflow-form-draft:w"]
    assert current["workflow-creation-mode:w"] == "人工问答增强"
    assert ui.session_state["workflow-reuse-notice:w"]["missing_sources"] == []
    backup = ui.session_state["work-draft-switch-backup:w"]
    assert creation_draft_application(tmp_path).restore_snapshot(backup) == prior
    assert json.dumps(original, sort_keys=True) == before
