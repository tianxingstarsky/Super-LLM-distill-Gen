"""Page motion follows navigation rather than ordinary state updates."""
from lib.presentation.streamlit.page_motion import page_surface_key


def test_motion_changes_only_when_rendered_page_changes():
    state = {}
    home = page_surface_key(state, "page_overview")
    state.update(ui_language="en", record="new-record", **{"node-model": "edited"})
    assert page_surface_key(state, "page_overview") == home
    workflow = page_surface_key(state, "page_workflow")
    assert workflow != home
    assert page_surface_key(state, "page_workflow") == workflow
    assert page_surface_key(state, "page_overview") == home


def test_invalid_motion_state_recovers_without_touching_page_configuration():
    state = {"page-motion-phase": "obsolete", "node-model": "model-a"}
    assert page_surface_key(state, "page_workflow") == "page-surface-a"
    assert state["node-model"] == "model-a"
