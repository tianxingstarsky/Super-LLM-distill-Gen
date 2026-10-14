"""CoT answer mode is a durable node setting, not an unregistered UI field."""
import pytest
from streamlit.testing.v1 import AppTest

from lib.bootstrap.creation_drafts import creation_draft_application
from lib.domain.creation_draft import validate_creation_draft
from lib.domain.workflow_generation import validate_node_generation
from lib.presentation.streamlit.workflow_generation_settings import generation_snapshot
from lib.presentation.streamlit.workflow_prompt_settings import PROMPT_LABELS
from tests.test_creation_draft_storage import persistent_script


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """These checks use local file storage and Streamlit doubles only."""
    yield


@pytest.mark.parametrize("mode", ["joint", "native_prefill"])
def test_cot_answer_mode_survives_restart_and_named_restore(tmp_path, mode):
    values = {"workflow-generation-enabled:default:cot": True,
              "workflow-generation-style:default:cot": "reflective",
              "workflow-generation-instruction:default:cot": "Keep factual conditions.",
              "workflow-generation-answer-mode:default:cot": mode}
    app = creation_draft_application(tmp_path)
    app.replace(values)
    snapshot = app.save_snapshot("CoT answer mode")
    assert creation_draft_application(tmp_path).load() == values
    app.update({"workflow-generation-answer-mode:default:cot": "joint"})
    assert creation_draft_application(tmp_path).restore_snapshot(snapshot) == values


@pytest.mark.parametrize("mode", ["joint", "native_prefill"])
def test_reusing_a_recipe_persists_the_original_answer_mode(tmp_path, mode):
    from copy import deepcopy
    from lib.presentation.streamlit.workflow_reuse import recipe_to_draft
    recipe = {"sources": [], "targets": ["cot"], "brief": "Create useful examples.",
              "node_generation": {"cot": {"enabled": True, "style": "reflective",
                                          "instruction": "Keep the conditions.", "answer_mode": mode}}}
    original = deepcopy(recipe)
    values = recipe_to_draft(recipe, "Copied task", "default", [])["values"]
    assert values["workflow-generation-answer-mode:default:cot"] == mode
    creation_draft_application(tmp_path).replace(values)
    assert creation_draft_application(tmp_path).load() == values
    assert recipe == original


@pytest.mark.parametrize("key, value", [
    ("workflow-generation-answer-mode:w:sft", "joint"),
    ("workflow-generation-answer-mode:w:trim", "native_prefill"),
    ("workflow-generation-answer-mode:w", "joint"),
    ("workflow-generation-answer-mode:w:cot:extra", "joint"),
    ("workflow-generation-answer-mode::cot", "joint"),
    ("workflow-generation-answer-mode:w:cot", "native"),
    ("workflow-generation-answer-mode:w:cot", True),
    ("workflow-generation-answer-mode:w:cot", ["native_prefill"]),
])
def test_mode_scope_and_values_are_checked_before_storage(tmp_path, key, value):
    output = tmp_path / "not-created"
    with pytest.raises(ValueError, match="invalid_creation_draft"):
        creation_draft_application(output).update({key: value})
    assert not output.exists()


def test_switching_to_native_does_not_drop_other_draft_values_on_autosave(tmp_path):
    app = creation_draft_application(tmp_path)
    original = {"workflow-name:default": "Current work",
                "workflow-generation-answer-mode:default:cot": "joint",
                "workflow-node-prompt:default:cot:workflow.cot_rationale": "My reasoning rules."}
    app.replace(original)
    app.update({"workflow-generation-answer-mode:default:cot": "native_prefill"})
    expected = {**original, "workflow-generation-answer-mode:default:cot": "native_prefill"}
    assert creation_draft_application(tmp_path).load() == expected
    before = (tmp_path / ".creation-draft.json").read_bytes()
    with pytest.raises(ValueError, match="invalid_creation_draft"):
        app.update({"workflow-generation-answer-mode:default:sft": "native_prefill"})
    assert (tmp_path / ".creation-draft.json").read_bytes() == before


def test_inactive_native_mode_remains_a_draft_but_is_not_submitted(monkeypatch):
    from types import SimpleNamespace
    from lib.presentation.streamlit import workflow_generation_settings as settings
    values = {"workflow-generation-enabled:w:cot": False,
              "workflow-generation-answer-mode:w:cot": "native_prefill"}
    assert validate_creation_draft(values) == values
    monkeypatch.setattr(settings, "st", SimpleNamespace(session_state={"workflow-form-draft:w": values}))
    snapshot = generation_snapshot("w", ["cot"])
    assert "answer_mode" not in snapshot["cot"]
    assert validate_node_generation(snapshot)["cot"]["enabled"] is False


def test_real_widget_autosave_restores_native_selection_and_prompt_labels(tmp_path):
    script = persistent_script(tmp_path)
    app = creation_draft_application(tmp_path)
    ui = AppTest.from_string(script).run()
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value(["cot"]).run()
    ui.button(key="fixture-node:cot").click().run()
    ui.toggle(key="workflow-generation-enabled:fixture:cot").set_value(True).run()
    key = "workflow-generation-answer-mode:fixture:cot"
    ui.selectbox(key=key).set_value("native_prefill").run()
    assert not ui.exception
    assert not any("草稿未能" in warning.value for warning in ui.warning)
    assert app.load()[key] == "native_prefill"
    options = ui.selectbox(key="workflow-node-prompt-selection:fixture:cot").options
    assert PROMPT_LABELS["workflow.cot_rationale"] in options
    assert PROMPT_LABELS["workflow.cot_native_answer"] in options
    assert PROMPT_LABELS["workflow.cot_prefix_check"] in options
    assert not any(option.startswith("workflow.") for option in options)
    fresh = AppTest.from_string(script).run()
    fresh.button(key="fixture-node:cot").click().run()
    assert not fresh.exception
    assert fresh.selectbox(key=key).value == "native_prefill"
    assert not any("草稿未能" in warning.value for warning in fresh.warning)
