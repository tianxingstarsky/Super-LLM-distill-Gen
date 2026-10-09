"""Delivery quantity controls stay consistent with durable drafts."""
from streamlit.testing.v1 import AppTest

from lib.bootstrap.creation_drafts import creation_draft_application
from tests.test_workflow_draft import SCRIPT


def production_screen(output):
    from pathlib import Path
    import streamlit as st
    from lib.bootstrap.creation_drafts import creation_draft_application
    from lib.presentation.streamlit.workflow_page import TARGET_LABELS, _draft_number, _save_draft_value
    from lib.presentation.streamlit.workflow_production_settings import render_delivery_goal

    st.session_state["ws"] = "fixture"
    drafts = creation_draft_application(Path(output))
    st.session_state["workflow-draft-application:fixture"] = drafts
    if "workflow-form-draft:fixture" not in st.session_state:
        st.session_state["workflow-form-draft:fixture"] = drafts.load()
    targets = st.multiselect("Targets", ["sft", "dpo", "cot", "cpt", "agent"],
                             default=["sft", "dpo"], key="fixture-targets")
    count, production = render_delivery_goal("fixture", targets, "文档资料", TARGET_LABELS,
                                             _draft_number, _save_draft_value)
    st.session_state["fixture-production"] = production
    st.session_state["fixture-candidates"] = count


def test_fresh_workbench_defaults_to_one_sft_goal():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception
    assert ui.pills(key="workflow-targets:fixture:自动推荐").value == ["sft"]
    assert ui.toggle(key="workflow-production-enabled:fixture").value is True
    assert ui.number_input(key="workflow-count:fixture").label == "目标合格数量"
    assert ui.number_input(key="workflow-max-units:fixture").max == 1000000


def test_delivery_progress_does_not_duplicate_internal_request_error_codes():
    def screen():
        from lib.presentation.streamlit.workflow_production_settings import render_delivery_progress
        render_delivery_progress({"status": "failed", "production": {
            "stop_reason": "service_authentication_failed", "goals": {
                "sft": {"goal": 100, "eligible": 10, "stop_reason": "service_authentication_failed"}}}})
    ui = AppTest.from_function(screen).run()
    assert not ui.exception
    assert not ui.warning
    assert "service_authentication_failed" not in ui.get("progress")[0].proto.text
    assert "10 / 100" in ui.get("progress")[0].proto.text


def test_candidate_mode_clamps_saved_production_source_scope():
    ui = AppTest.from_string(SCRIPT).run()
    ui.number_input(key="workflow-max-units:fixture").set_value(800000).run()
    ui.toggle(key="workflow-production-enabled:fixture").set_value(False).run()
    assert not ui.exception
    assert ui.number_input(key="workflow-max-units:fixture").value == 100000


def test_million_default_applies_to_all_targets_until_explicitly_overridden(tmp_path):
    ui = AppTest.from_function(production_screen, args=(str(tmp_path),)).run()
    ui.number_input(key="workflow-count:fixture").set_value(1000000).run()
    assert not ui.exception
    assert ui.session_state["fixture-production"]["goals"] == {"sft": 1000000, "dpo": 1000000}
    assert ui.session_state["fixture-candidates"] <= 100000
    ui.number_input(key="production-goal-editor:fixture:sft").set_value(400000).run()
    ui.number_input(key="workflow-count:fixture").set_value(700000).run()
    assert ui.session_state["fixture-production"]["goals"] == {"sft": 400000, "dpo": 700000}


def test_quantity_overrides_budget_and_mode_survive_a_fresh_session(tmp_path):
    ui = AppTest.from_function(production_screen, args=(str(tmp_path),)).run()
    ui.number_input(key="workflow-count:fixture").set_value(1000000).run()
    ui.number_input(key="production-goal-editor:fixture:sft").set_value(300000).run()
    ui.number_input(key="workflow-production-budget:fixture").set_value(1250.5).run()
    expected = ui.session_state["fixture-production"]
    fresh = AppTest.from_function(production_screen, args=(str(tmp_path),)).run()
    assert not fresh.exception
    assert fresh.number_input(key="workflow-count:fixture").value == 1000000
    assert fresh.number_input(key="workflow-production-budget:fixture").value == 1250.5
    assert fresh.session_state["fixture-production"] == expected
    fresh.toggle(key="workflow-production-enabled:fixture").set_value(False).run()
    candidate = AppTest.from_function(production_screen, args=(str(tmp_path),)).run()
    assert not candidate.exception
    assert candidate.toggle(key="workflow-production-enabled:fixture").value is False
    assert candidate.session_state["fixture-production"] is None
    assert candidate.number_input(key="workflow-count:fixture").value <= 100000
    assert any("不自动补齐" in item.value for item in candidate.caption)


def test_existing_single_target_override_stays_visible_when_other_targets_removed(tmp_path):
    ui = AppTest.from_function(production_screen, args=(str(tmp_path),)).run()
    ui.number_input(key="production-goal-editor:fixture:sft").set_value(500).run()
    ui.multiselect(key="fixture-targets").set_value(["sft"]).run()
    assert not ui.exception
    assert ui.session_state["fixture-production"]["goals"] == {"sft": 500}
    assert any(item.key == "production-goal-editor:fixture:sft" for item in ui.number_input)


def test_legacy_copied_candidate_draft_restarts_without_refill(tmp_path):
    from lib.presentation.streamlit.workflow_reuse import recipe_to_draft

    values = recipe_to_draft({"sources": [], "targets": ["sft"], "sample_count": 50000},
                             "Legacy candidate task", "fixture", [])["values"]
    creation_draft_application(tmp_path).replace(values)
    ui = AppTest.from_function(production_screen, args=(str(tmp_path),)).run()
    assert not ui.exception
    assert ui.toggle(key="workflow-production-enabled:fixture").value is False
    assert ui.session_state["fixture-production"] is None
    assert ui.number_input(key="workflow-count:fixture").value == 50000


def test_copied_production_goals_and_budget_restart_without_becoming_candidates(tmp_path):
    from lib.presentation.streamlit.workflow_reuse import recipe_to_draft

    values = recipe_to_draft({"sources": [], "targets": ["sft", "dpo"], "sample_count": 10000,
                              "production": {"goals": {"sft": 1000000, "dpo": 300000}, "budget_usd": 2500}},
                             "Delivery task", "fixture", [])["values"]
    creation_draft_application(tmp_path).replace(values)
    ui = AppTest.from_function(production_screen, args=(str(tmp_path),)).run()
    assert not ui.exception
    assert ui.session_state["fixture-production"]["goals"] == {"sft": 1000000, "dpo": 300000}
    assert ui.session_state["fixture-production"]["budget_usd"] == 2500


def coverage_screen():
    from lib.presentation.streamlit.workflow_production_settings import review_coverage_hint
    review_coverage_hint({"goals": {"sft": 10000, "dpo": 5000}, "round_size": 10000},
                         {"enabled": True, "mode": "sample", "sample_percent": 10,
                          "max_samples_per_target": 100})


def test_package_sampling_hint_uses_per_target_sampling_cap():
    ui = AppTest.from_function(coverage_screen).run()
    assert not ui.exception
    assert any("100 / 10,000" in item.value and "每轮每类" in item.value for item in ui.caption)


def test_candidate_draft_with_old_million_count_is_clamped_before_widget_creation(tmp_path):
    creation_draft_application(tmp_path).replace({"workflow-production-enabled:fixture": False,
                                                 "workflow-count:fixture": 1000000})
    ui = AppTest.from_function(production_screen, args=(str(tmp_path),)).run()
    assert not ui.exception
    assert ui.number_input(key="workflow-count:fixture").value == 100000


def test_reset_per_class_goals_restores_common_quantity_and_persists_it(tmp_path):
    ui = AppTest.from_function(production_screen, args=(str(tmp_path),)).run()
    ui.number_input(key="production-goal-editor:fixture:sft").set_value(500).run()
    ui.number_input(key="workflow-count:fixture").set_value(10000).run()
    ui.button(key="production-goals-reset:fixture").click().run()
    assert not ui.exception
    assert ui.session_state["fixture-production"]["goals"] == {"sft": 10000, "dpo": 10000}
    fresh = AppTest.from_function(production_screen, args=(str(tmp_path),)).run()
    assert not fresh.exception
    assert fresh.session_state["fixture-production"]["goals"] == {"sft": 10000, "dpo": 10000}


def test_stop_limits_follow_shared_goal_and_custom_limits_survive_reuse(tmp_path):
    ui = AppTest.from_function(production_screen, args=(str(tmp_path),)).run()
    ui.number_input(key="workflow-count:fixture").set_value(1000000).run()
    assert ui.session_state["fixture-production"]["max_attempts"] == 3000000
    ui.number_input(key="production-limit-editor:fixture:max_attempts").set_value(1500000).run()
    ui.number_input(key="production-limit-editor:fixture:min_acceptance_rate").set_value(5.0).run()
    from lib.presentation.streamlit.workflow_reuse import recipe_to_draft
    copied = recipe_to_draft({"sources": [], "targets": ["sft", "dpo"],
                              "production": ui.session_state["fixture-production"]},
                             "Configured refill", "fixture", [])["values"]
    creation_draft_application(tmp_path).replace(copied)
    fresh = AppTest.from_function(production_screen, args=(str(tmp_path),)).run()
    assert not fresh.exception
    assert fresh.session_state["fixture-production"]["max_attempts"] == 1500000
    assert fresh.session_state["fixture-production"]["min_acceptance_rate"] == .05
    fresh.button(key="production-limits-reset:fixture").click().run()
    assert fresh.session_state["fixture-production"]["max_attempts"] == 3000000
    assert fresh.session_state["fixture-production"]["min_acceptance_rate"] == .01


def vision_workbench_screen(output, confirmed, with_services=False):
    from pathlib import Path
    from unittest.mock import patch
    import streamlit as st
    from lib.bootstrap.creation_drafts import creation_draft_application
    from lib.application.workflow_node_models_service import WorkflowNodeModelsApplication
    from lib.presentation.streamlit import workflow_page

    class Sources:
        def default_sft_output_style(self): return "separated"
        def source_files(self, *args, **kwargs): return [{"path": "fixture.png", "label": "Saved image"}]
        def task_runs(self): return []
        def agent_replay_capabilities(self): return {}
        def web_research_capabilities(self): return {"brave_configured": False}

    class Inventory:
        def list_backends(self):
            return {"backends": [{"name": "local", "models": ["writer", "judge"],
                                  "model_capabilities": {"writer": {"vision": confirmed}}}],
                    "roles": {}}

    class Services:
        def list_backends(self): return {"budget": {}}

    st.session_state["ws"] = "fixture"
    with patch.object(workflow_page, "render_canvas", lambda *args, **kwargs: None):
        workflow_page.render_workbench(Sources(), lambda args: None,
                                       WorkflowNodeModelsApplication(Inventory()),
                                       draft_application=creation_draft_application(Path(output)),
                                       backend_application=Services() if with_services else None)


def vision_draft():
    binding = {"backend": "local", "model": "writer", "context_window_tokens": 131072,
               "max_output_tokens": 32768}
    return {"workflow-preset:fixture": "自动推荐", "workflow-targets:fixture:自动推荐": ["sft"],
            "workflow-source-mode:fixture": "文档资料", "workflow-sources:fixture:文档资料": ["fixture.png"],
            "workflow-document-parse-mode:fixture": "vision", "workflow-node-bindings:fixture": {
                "ingest": {"vision": binding},
                "sft": {"generation": binding, "jev": {**binding, "model": "judge"}}}}


def test_fresh_workbench_restores_vision_binding_before_start_validation(tmp_path):
    creation_draft_application(tmp_path).replace(vision_draft())
    ui = AppTest.from_function(vision_workbench_screen, args=(str(tmp_path), True)).run()
    assert not ui.exception
    assert ui.session_state["workflow-setup-node:fixture"] == "sft"
    assert not ui.button(key="workflow-create:fixture").disabled


def test_restored_vision_reference_still_requires_current_model_capability(tmp_path):
    creation_draft_application(tmp_path).replace(vision_draft())
    ui = AppTest.from_function(vision_workbench_screen, args=(str(tmp_path), False)).run()
    assert not ui.exception
    assert ui.button(key="workflow-create:fixture").disabled


def test_saved_candidate_mode_does_not_activate_stale_production_budget_on_first_render(tmp_path):
    creation_draft_application(tmp_path).replace({**vision_draft(),
                                                 "workflow-production-enabled:fixture": False,
                                                 "workflow-production-budget:fixture": 1250.5})
    ui = AppTest.from_function(vision_workbench_screen, args=(str(tmp_path), True, True)).run()
    assert not ui.exception
    assert ui.toggle(key="workflow-production-enabled:fixture").value is False
    assert not ui.button(key="workflow-create:fixture").disabled
