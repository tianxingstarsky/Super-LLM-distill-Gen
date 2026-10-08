"""Library handoff adds inputs without discarding the user's existing work."""
from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from lib.bootstrap.creation_drafts import creation_draft_application
from lib.presentation.streamlit import source_handoff


def test_handoff_merges_and_persists_sources_without_changing_targets_or_models(tmp_path, monkeypatch):
    application = creation_draft_application(tmp_path)
    saved = {"workflow-preset:w": "自选目标", "workflow-targets:w:自选目标": ["sft", "dpo"],
             "workflow-count:w": 50000, "workflow-source-mode:w": "开放需求",
             "workflow-open-brief:w": "Keep this brief", "workflow-sources:w:文档资料": ["first.txt"],
             "workflow-sources:w:Agent 上下文": ["history.jsonl"]}
    application.replace(saved)
    models = {"sft": {"generation": {"backend": "local", "model": "writer"}}}
    state = {"workflow-node-bindings:w": deepcopy(models), "nav": "数据管理",
             "workflow-creation-mode:w": "人工制作图文"}
    monkeypatch.setattr(source_handoff.st, "session_state", state)
    item = {"path": "second.md", "source_mode": "文档资料"}
    source_handoff.use_library_source(item, "w", application)
    source_handoff.use_library_source(item, "w", application)
    expected = {**saved, "workflow-source-mode:w": "文档资料",
                "workflow-sources:w:文档资料": ["first.txt", "second.md"]}
    assert creation_draft_application(tmp_path).load() == expected
    assert state["workflow-form-draft:w"] == expected
    assert state["workflow-node-bindings:w"] == models
    assert state["workflow-creation-mode:w"] == "自动生成"
    assert state["workflow-setup-node:w"] == "ingest"
    assert state["nav"] == "自动工作流"


def test_handoff_preserves_unsaved_session_edits_and_uses_current_source_selection(tmp_path, monkeypatch):
    application = creation_draft_application(tmp_path)
    application.update({"workflow-count:w": 1000, "workflow-sources:w:Agent 上下文": ["old.jsonl"]})
    state = {"workflow-form-draft:w": {"workflow-count:w": 50000},
             "workflow-sources:w:Agent 上下文": ["current.jsonl"]}
    monkeypatch.setattr(source_handoff.st, "session_state", state)
    source_handoff.use_library_source({"path": "new.json", "source_mode": "Agent 上下文"}, "w", application)
    assert application.load() == {"workflow-count:w": 50000, "workflow-source-mode:w": "Agent 上下文",
                                  "workflow-sources:w:Agent 上下文": ["current.jsonl", "new.json"]}


@pytest.mark.parametrize("suffix", [".PNG", ".jpg", ".JPEG", ".webp"])
def test_image_handoff_preserves_targets_models_and_explicit_parser_choice(tmp_path, monkeypatch, suffix):
    application = creation_draft_application(tmp_path)
    saved = {"workflow-preset:w": "自选目标", "workflow-targets:w:自选目标": ["cpt", "sft"],
             "workflow-sources:w:文档资料": ["guide.md"], "workflow-count:w": 50000}
    application.replace(saved)
    parser_state = {"workflow-document-parse-mode:w": "native",
                    "workflow-document-parse-mode-draft:w": "native"}
    models = {"sft": {"generation": {"backend": "local", "model": "writer"}}}
    state = {**parser_state, "workflow-node-bindings:w": deepcopy(models), "nav": "数据管理"}
    monkeypatch.setattr(source_handoff.st, "session_state", state)

    source_handoff.use_library_source({"path": "scan" + suffix, "source_mode": "文档资料"}, "w", application)

    expected = {**saved, "workflow-source-mode:w": "文档资料",
                "workflow-sources:w:文档资料": ["guide.md", "scan" + suffix]}
    assert application.load() == expected
    assert state["workflow-form-draft:w"] == expected
    assert state["workflow-node-bindings:w"] == models
    assert {key: state[key] for key in parser_state} == parser_state
    assert state["workflow-setup-node:w"] == "ingest"
    assert state["nav"] == "自动工作流"


@pytest.mark.parametrize("item", [
    {"path": "file.md", "source_mode": "Agent 上下文"},
    {"path": "file.jsonl", "source_mode": "文档资料"},
    {"path": "file.png", "source_mode": "Agent 上下文"},
    {"path": "file.gif", "source_mode": "文档资料"},
    {"path": "file.csv", "source_mode": "文档资料"},
    {"path": "file.md", "source_mode": "文档资料", "unknown": True},
    {"path": None, "source_mode": "文档资料"},
])
def test_invalid_handoff_never_changes_draft_or_navigation(tmp_path, monkeypatch, item):
    application = creation_draft_application(tmp_path)
    values = {"workflow-count:w": 50000}
    application.replace(values)
    state = {"workflow-form-draft:w": values, "nav": "数据管理"}
    before = deepcopy(state)
    monkeypatch.setattr(source_handoff.st, "session_state", state)
    with pytest.raises(ValueError, match="invalid_generation_source"):
        source_handoff.use_library_source(item, "w", application)
    assert state == before
    assert application.load() == values


def test_handoff_does_not_drop_existing_sources_when_limit_is_reached(tmp_path, monkeypatch):
    application = creation_draft_application(tmp_path)
    values = {"workflow-sources:w:文档资料": [f"file-{index}.md" for index in range(200)]}
    application.replace(values)
    state = {"workflow-form-draft:w": values, "nav": "数据管理"}
    before = deepcopy(state)
    monkeypatch.setattr(source_handoff.st, "session_state", state)
    with pytest.raises(ValueError, match="generation_source_limit_exceeded"):
        source_handoff.use_library_source({"path": "next.md", "source_mode": "文档资料"}, "w", application)
    assert state == before
    assert application.load() == values


def test_failed_persistence_leaves_the_current_session_untouched(monkeypatch):
    class UnwritableDraft:
        def load(self):
            return {"workflow-count:w": 50000}

        def replace(self, values):
            raise OSError("private storage path")

    state = {"nav": "数据管理", "workflow-node-bindings:w": {"kept": True}}
    before = deepcopy(state)
    monkeypatch.setattr(source_handoff.st, "session_state", state)
    with pytest.raises(OSError):
        source_handoff.use_library_source({"path": "saved.txt", "source_mode": "文档资料"}, "w", UnwritableDraft())
    assert state == before


_CATALOG_SCREEN = '''
import streamlit as st
from lib.application.asset_catalog_service import AssetCatalogApplication
from lib.domain.dataset_assets import Asset, AssetInventory
from lib.presentation.streamlit.asset_catalog_page import render_asset_catalog
class Driver:
    def inventory(self, workspace):
        return AssetInventory(() if st.session_state.get('empty') else (
            Asset('source/guide.md', 'source', 'guide.md', '来源文件', 10, 1),
            Asset('output/sft.jsonl', 'output', 'sft.jsonl', '样本', 10, 1)))
    def excerpt(self, *args): return ('preview', 'excerpt')
    def download(self, *args): return b'preview'
    def source_path_for_generation(self, *args):
        if st.session_state.get('changed'):
            raise ValueError('asset_changed_since_listing PRIVATE_PATH')
        return 'saved/guide.md'
def use(source): st.session_state['handoff'] = source
def import_sources(): st.session_state['imported'] = True
render_asset_catalog(AssetCatalogApplication(Driver()), 'fixture', show_title=False,
                     on_use_source=use, on_import_sources=import_sources)
'''


def test_catalog_action_hands_off_sources_but_not_outputs_and_imports_empty_library():
    ui = AppTest.from_string(_CATALOG_SCREEN).run()
    assert not ui.exception
    ui.button(key="asset-use:fixture:source/guide.md").click().run()
    assert ui.session_state["handoff"] == {"path": "saved/guide.md", "source_mode": "文档资料"}
    ui.selectbox(key="asset-category:fixture").set_value("样本").run()
    assert not any(button.label == "用于生成" for button in ui.button)
    empty = AppTest.from_string(_CATALOG_SCREEN)
    empty.session_state["empty"] = True
    empty.run().button(key="asset-import:fixture").click().run()
    assert not empty.exception
    assert empty.session_state["imported"] is True


def test_catalog_stale_source_keeps_user_in_library_and_shows_safe_error():
    ui = AppTest.from_string(_CATALOG_SCREEN).run()
    ui.session_state["changed"] = True
    ui.button(key="asset-use:fixture:source/guide.md").click().run()
    assert not ui.exception
    assert "handoff" not in ui.session_state
    assert any("资料未能加入生成草稿" in message.value for message in ui.error)
    assert not any("PRIVATE_PATH" in message.value for message in ui.error)


@pytest.mark.parametrize("suffix", [".PNG", ".jpg", ".JPEG", ".webp"])
def test_catalog_image_details_offer_generation_handoff(suffix):
    screen = _CATALOG_SCREEN.replace("guide.md", "scan" + suffix)
    ui = AppTest.from_string(screen).run()
    ui.button(key="asset-use:fixture:source/scan" + suffix).click().run()
    assert not ui.exception
    assert ui.session_state["handoff"] == {"path": "saved/scan" + suffix, "source_mode": "文档资料"}
