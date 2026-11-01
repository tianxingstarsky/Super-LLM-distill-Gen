"""Native generation stays node-local across navigation and explicit migration."""
from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from lib.bootstrap.creation_drafts import creation_draft_application
from tests.test_workflow_draft import SCRIPT


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """These UI cases neither start a server nor call any model."""
    yield


@pytest.mark.parametrize("node", ["sft", "multiturn"])
def test_native_mode_style_and_guidance_survive_node_switches_and_disk_restore(tmp_path, node):
    ui = AppTest.from_string(SCRIPT).run()
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value([node]).run()
    ui.button(key=f"fixture-node:{node}").click().run()
    mode_key = f"workflow-generation-answer-mode:fixture:{node}"
    assert ui.radio(key=mode_key).value == "native_joint"
    assert not any(button.key == "fixture-node:cot" for button in ui.button)
    instruction_key = f"workflow-generation-instruction:fixture:{node}"
    style_key = f"workflow-generation-style:fixture:{node}"
    checklist_key = f"workflow-{node}-checklist-text:fixture"
    ui.text_area(key=instruction_key).set_value("Keep uncertainty visible and avoid fixed steps.").run()
    ui.selectbox(key=style_key).set_value("custom").run()
    ui.toggle(key=f"workflow-{node}-checklist-enabled:fixture").set_value(True).run()
    ui.text_area(key=checklist_key).set_value("? Check missing context.\n[final] Preserve justified conditions.").run()
    ui.button(key="fixture-node:director").click().run()
    ui.button(key=f"fixture-node:{node}").click().run()
    assert not ui.exception
    assert ui.radio(key=mode_key).value == "native_joint"
    assert ui.text_area(key=checklist_key).value.startswith("? Check missing context.")
    ui.radio(key=mode_key).set_value("joint").run()
    ui.button(key="fixture-node:director").click().run()
    ui.button(key=f"fixture-node:{node}").click().run()
    assert ui.radio(key=mode_key).value == "joint"
    ui.radio(key=mode_key).set_value("native_joint").run()
    saved = dict(ui.session_state["workflow-form-draft:fixture"])
    creation_draft_application(tmp_path).replace(saved)
    restored = AppTest.from_string(SCRIPT)
    restored.session_state["workflow-form-draft:fixture"] = creation_draft_application(tmp_path).load()
    restored.run()
    restored.button(key=f"fixture-node:{node}").click().run()
    assert not restored.exception
    assert restored.radio(key=mode_key).value == "native_joint"
    assert restored.selectbox(key=style_key).value == "custom"
    assert restored.text_area(key=instruction_key).value == saved[instruction_key]
    assert restored.text_area(key=checklist_key).value == saved[checklist_key]


def test_explicit_legacy_cot_merge_preserves_guidance_and_removes_separate_node():
    draft = {"workflow-targets:fixture:自动推荐": ["sft", "multiturn", "cot"],
             "workflow-generation-enabled:fixture:sft": False,
             "workflow-generation-enabled:fixture:multiturn": False,
             "workflow-generation-enabled:fixture:cot": True,
             "workflow-generation-style:fixture:cot": "custom",
             "workflow-generation-instruction:fixture:cot": "Keep tentative interpretations.",
             "workflow-generation-reasoning-language:fixture:cot": "zh_en",
             "workflow-cot-checklist-enabled:fixture": True,
             "workflow-cot-checklist-version:fixture": 6,
             "workflow-cot-checklist-text:fixture": "? [prefix] Check the assumptions."}
    original = deepcopy(draft)
    ui = AppTest.from_string(SCRIPT)
    ui.session_state["workflow-form-draft:fixture"] = deepcopy(draft)
    ui.run()
    assert not ui.exception
    assert any(button.key == "fixture-node:cot" for button in ui.button)
    ui.radio(key="workflow-generation-answer-mode:fixture:sft").set_value("native_joint").run()
    assert not ui.exception
    assert any("不能被静默覆盖" in warning.value for warning in ui.warning)
    assert ui.button(key="workflow-create:fixture").disabled
    assert "cot" in ui.pills(key="workflow-targets:fixture:自动推荐").value
    assert any(button.key == "fixture-node:cot" for button in ui.button)
    assert ui.session_state["workflow-form-draft:fixture"]["workflow-generation-instruction:fixture:cot"] == original["workflow-generation-instruction:fixture:cot"]
    ui.button(key="workflow-integrate-cot:fixture").click().run()
    assert not ui.exception
    assert "cot" not in ui.pills(key="workflow-targets:fixture:自动推荐").value
    assert not any(button.key == "fixture-node:cot" for button in ui.button)
    saved = ui.session_state["workflow-form-draft:fixture"]
    for node in ("sft", "multiturn"):
        assert saved[f"workflow-generation-answer-mode:fixture:{node}"] == "native_joint"
        assert saved[f"workflow-generation-style:fixture:{node}"] == "custom"
        assert saved[f"workflow-generation-instruction:fixture:{node}"] == original["workflow-generation-instruction:fixture:cot"]
        assert saved[f"workflow-generation-reasoning-language:fixture:{node}"] == "zh_en"
        assert saved[f"workflow-{node}-checklist-text:fixture"] == original["workflow-cot-checklist-text:fixture"]
        assert saved[f"workflow-{node}-checklist-version:fixture"] == 7
    assert draft == original
