"""Exercise the real Agent node controls while keeping model calls disabled."""
from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from lib.domain.creation_draft import validate_creation_draft
from lib.presentation.streamlit.workflow_reuse import recipe_to_draft
from tests.test_workflow_draft import SCRIPT


def test_pinned_agent_experience_shows_frozen_baseline_instead_of_unprojected_edition():
    ui = AppTest.from_string('''
from lib.presentation.streamlit.workflow_experience_controls import render_pinned_experience
render_pinned_experience({
    "agent_experience_version": 1,
    "agent_trajectory_optimization_version": 1,
    "agent_experience": {"scopes": {"reasoning": {"enabled": True, "text": "Unprojected edition"}}},
    "agent_reasoning_memory": {"enabled": True, "text": "Frozen baseline", "edition_ref": {"id": "fixed-edition"}},
}, "workflow.agent_trajectory_optimization", "preview")
''').run()
    assert not ui.exception
    assert ui.text_area(key="workflow-run-experience:preview:reasoning").value == "Frozen baseline"
    assert any("fixed-edition" in row.value for row in ui.caption)


def test_agent_editor_mode_keeps_model_prompt_experience_and_missing_policy_in_their_nodes():
    from lib.domain.workflow_creation import validate_creation
    from lib.domain.workflow_node_models import binding_snapshot
    from lib.domain.workflow_scale import node_roles

    script = SCRIPT.replace("def canvas(spec,selection_key,**kwargs):", """def canvas(spec,selection_key,**kwargs):
    from copy import deepcopy
    st.session_state['agent-ui-canvas'] = deepcopy(spec)""")
    ui = AppTest.from_string(script).run()
    ui.session_state["workflow-entry-target:fixture"] = "agent"
    ui.run()
    ui.button(key="fixture-node:agent").click().run()
    assert not ui.exception
    assert ui.radio(key="workflow-agent-processing-choice:fixture").value == "optimize"
    assert any(select.key == "node-model:fixture:agent:generation:backend" for select in ui.selectbox)
    assert not any(select.key == "agent-mode-choice:fixture" for select in ui.selectbox)
    graph = ui.session_state["agent-ui-canvas"]
    check = next(node for node in graph["nodes"] if node["id"] == "review")
    assert check["label"] == "程序一致性核对" and check["models"] == []
    assert "不调用独立评审模型" in check["description"]
    assert "feedback_branch" not in graph
    assert not any(button.key == "workflow-config-fix:fixture:model:review" for button in ui.button)
    bindings = deepcopy(ui.session_state["workflow-node-bindings:fixture"])
    snapshot = binding_snapshot([node["id"] for node in graph["nodes"]], "Agent 上下文", bindings, {"local": {}},
        review_repair={"mode": "auto"}, agent_trajectory_optimization=True, targets=["agent"])
    assert set(snapshot) == {"agent"}, "only the one author is frozen; no scoring model is required"
    _, frozen = validate_creation(targets=["agent"], sources=["trace.jsonl"], node_models=bindings,
                                  agent_trajectory_optimization=True)
    assert not {"review", "jev", "package"}.intersection(frozen)
    assert node_roles("review", "Agent 上下文", agent_trajectory_optimization=True, targets=["agent", "sft"]) == ("generation",)
    assert node_roles("review", "Agent 上下文", targets=["agent"]) == ("generation",)
    ui.button(key="fixture-node:review").click().run()
    assert not ui.exception
    assert not any(select.key.startswith("node-model:fixture:review:") for select in ui.selectbox)
    assert not any(select.key == "workflow-node-prompt-selection:fixture:review" for select in ui.selectbox)
    ui.button(key="fixture-node:agent").click().run()
    ui.session_state["workflow-node-tabs:fixture:agent"] = "提示词与风格"
    ui.run()
    assert ui.selectbox(key="workflow-node-prompt-selection:fixture:agent").value == "workflow.agent_trajectory_optimization"
    assert any(area.key == "workflow-agent-experience-text:fixture:reasoning" for area in ui.text_area)
    assert not any(area.key.endswith(":agent") and "experience-text" in area.key for area in ui.text_area)
    ui.button(key="fixture-node:ingest").click().run()
    ui.selectbox(key="workflow-conversation-reasoning:fixture").set_value("fill_missing").run()
    assert not any(select.key == "node-model:fixture:ingest:generation:backend" for select in ui.selectbox)
    ui.button(key="fixture-node:agent").click().run()
    ui.session_state["workflow-node-tabs:fixture:agent"] = "节点设置"
    ui.run()
    ui.radio(key="workflow-agent-processing-choice:fixture").set_value("replay").run()
    assert not ui.exception
    assert ui.selectbox(key="agent-mode-choice:fixture").value == "local"
    assert not any(select.key == "node-model:fixture:agent:generation:backend" for select in ui.selectbox)
    assert ui.session_state["workflow-form-draft:fixture"]["workflow-agent-trajectory-optimization:fixture"] is False
    assert next(node for node in ui.session_state["agent-ui-canvas"]["nodes"] if node["id"] == "review")["label"] == "一次建议评审"
    ui.radio(key="workflow-agent-processing-choice:fixture").set_value("optimize").run()
    assert not ui.exception
    assert ui.session_state["workflow-form-draft:fixture"]["workflow-agent-trajectory-optimization:fixture"] is True


@pytest.mark.parametrize("optimized", [False, True])
def test_copy_preserves_agent_mode_and_custom_prompt_without_upgrading_frozen_recipe(optimized):
    recipe = {"sources": [], "targets": ["agent"], "sample_count": 1,
              "agent_replay_mode": "local"}
    if optimized:
        recipe.update(agent_trajectory_optimization_version=1,
                      node_prompt_templates={"agent": {"workflow.agent_trajectory_optimization": "Preserve the final answer."}})
    before = deepcopy(recipe)
    values = recipe_to_draft(recipe, "Imported trace", "demo", [])["values"]
    assert values["workflow-agent-trajectory-optimization:demo"] is optimized
    assert validate_creation_draft(values) == values
    if optimized:
        assert values["workflow-node-prompt:demo:agent:workflow.agent_trajectory_optimization"] == "Preserve the final answer."
    assert recipe == before
    ui = AppTest.from_string(SCRIPT)
    ui.session_state["workflow-form-draft:fixture"] = (
        {"workflow-agent-trajectory-optimization:fixture": True} if optimized else
        {"workflow-agent-mode:fixture": "local"})
    ui.run()
    ui.session_state["workflow-entry-target:fixture"] = "agent"
    ui.run()
    ui.button(key="fixture-node:agent").click().run()
    assert not ui.exception
    assert ui.radio(key="workflow-agent-processing-choice:fixture").value == ("optimize" if optimized else "replay")


def test_agent_canvas_shows_the_real_author_without_changing_execution_edges():
    from lib.presentation.streamlit.workflow_canvas import canvas_spec
    from lib.presentation.streamlit.workflow_page import GRAPH_LABELS, STAGE_GLYPHS

    legacy = canvas_spec(["agent"], {}, "agent", GRAPH_LABELS, STAGE_GLYPHS)
    view = canvas_spec(["agent"], {}, "agent", GRAPH_LABELS, STAGE_GLYPHS,
                       {"agent": {"generation": {"backend": "writer", "model": "trace-editor"}}},
                       language="en", agent_trajectory_optimization=True)
    assert view["edges"] == legacy["edges"]
    assert [node["id"] for node in view["nodes"]] == [node["id"] for node in legacy["nodes"]]
    editor = next(node for node in view["nodes"] if node["id"] == "agent")
    original = next(node for node in legacy["nodes"] if node["id"] == "agent")
    assert editor["label"] == "Agent optimization"
    assert editor["subtitle"] == "writer · trace-editor" and len(editor["models"]) == 1
    assert "final answer" in editor["description"] and "no local replay" in editor["description"]
    assert original["models"] == []
