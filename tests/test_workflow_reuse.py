"""Reuse immutable run settings without losing edits or copying authorization."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from lib.bootstrap.creation_drafts import creation_draft_application
from lib.presentation.streamlit.workflow_reuse import recipe_to_draft


def recipe(**changes):
    return {"sources": [], "brief": "Generate maintenance examples", "targets": ["sft", "dpo"],
            "sample_count": 50000, "tasks": 50000, "max_units": 60000, "chunk_chars": 2400,
            "concurrency": 8, "batch_size": 250, "conversation_turns": 5,
            "sft_output_style": "drop", "node_models": {
                "sft": {"generation": {"backend": "local", "model": "judge",
                                       "context_window_tokens": 196608, "max_output_tokens": 49152}}},
            "web_research": {"provider": "brave", "query": "maintenance safety", "count": 4,
                             "more_queries": ["maintenance records", "inspection procedures"]},
            **changes}


def source(path, *, name=None):
    return {"name": name or path.name, "file": "0000" + path.suffix,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}


def test_recipe_maps_bounded_settings_and_keeps_secrets_out_of_draft():
    original = recipe(endpoint_pins={"sft": {"secret": "DO_NOT_COPY_SECRET"}},
                      api_key="DO_NOT_COPY_SECRET", endpoint="DO_NOT_COPY_ENDPOINT",
                      evaluation_references=[{"file": "held-out.jsonl"}])
    unchanged = deepcopy(original)
    copied = recipe_to_draft(original, "Original task", "default", [])
    values = copied["values"]
    assert values["workflow-name:default"] == "Original task"
    assert values["workflow-source-mode:default"] == "开放需求"
    assert values["workflow-targets:default:自动推荐"] == ["sft", "dpo"]
    assert values["workflow-count:default"] == 50000
    assert values["workflow-max-units:default"] == 60000
    assert values["workflow-concurrency:default"] == 8
    assert values["workflow-batch-size:default"] == 250
    assert values["workflow-chunk-chars:default"] == 2400
    assert values["workflow-turns:default"] == 5
    assert values["workflow-open-brief:default"] == original["brief"]
    assert values["workflow-web-research-query:default"] == "maintenance safety"
    assert values["workflow-web-research-count:default"] == 4
    assert values["workflow-web-research-more:default"] == "maintenance records\ninspection procedures"
    assert values["workflow-sft-output-style:default"] == "drop"
    assert copied["evaluation_references_omitted"] is True
    assert copied["node_bindings"]["sft"]["generation"]["model"] == "judge"
    assert values["workflow-production-enabled:default"] is False
    assert not any("node-model" in key or "consent" in key or "api-key" in key for key in values)
    assert "DO_NOT_COPY" not in json.dumps(copied)
    copied["node_bindings"]["sft"]["generation"]["model"] = "another"
    values["workflow-targets:default:自动推荐"].append("cpt")
    assert original == unchanged


@pytest.mark.parametrize("style,expected", [("drop", "drop"), ("separated", "separated"), ("inline", "separated")])
def test_older_recipe_uses_pinned_generation_style_and_task_count(style, expected):
    copied = recipe_to_draft(recipe(sample_count=None, tasks=1200, sft_output_style=None,
                                   generation_preferences={"values": {"cot_style": style}}), "Old task", "w", [])
    assert copied["values"]["workflow-count:w"] == 1200
    assert copied["values"]["workflow-sft-output-style:w"] == expected


@pytest.mark.parametrize("changes", [
    {"sample_count": 0}, {"sample_count": False}, {"concurrency": 17}, {"sft_output_style": "inline"},
    {"node_models": {"sft": {"generation": {"backend": "local", "model": "writer", "api_key": "secret"}}}},
])
def test_invalid_copied_fields_are_rejected(changes):
    with pytest.raises(ValueError):
        recipe_to_draft(recipe(**changes), "Task", "w", [])


def test_only_matching_existing_library_content_is_selected_not_snapshot_paths(tmp_path):
    good = tmp_path / "library" / "manual.txt"
    good.parent.mkdir()
    good.write_text("Original manual", encoding="utf-8")
    changed = tmp_path / "changed" / "manual.txt"
    changed.parent.mkdir()
    changed.write_text("Changed manual", encoding="utf-8")
    snapshot = tmp_path / "output" / "inputs" / "0000.txt"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(good.read_bytes())
    absent = tmp_path / "missing.txt"
    absent.write_text("Unavailable", encoding="utf-8")
    sources = [source(good), source(absent)]
    # Even an absolute snapshot path in recipe.file is never a source-picker value.
    sources[0]["file"] = str(snapshot)
    absent.unlink()
    original = recipe(sources=sources, web_research=None, brief="Extra requirements")
    copied = recipe_to_draft(original, "Document task", "w",
                             [{"path": str(changed)}, {"path": str(absent)}, {"path": str(good)}])
    assert copied["values"]["workflow-source-mode:w"] == "文档资料"
    assert copied["values"]["workflow-sources:w:文档资料"] == [str(good)]
    assert copied["values"]["workflow-source-brief:w:文档资料"] == "Extra requirements"
    assert copied["missing_sources"] == ["missing.txt"]
    assert str(snapshot) not in json.dumps(copied)


def test_agent_context_sources_keep_the_agent_picker_mode(tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"messages": []}\n', encoding="utf-8")
    copied = recipe_to_draft(recipe(sources=[source(trace)], web_research=None), "Agent task", "w",
                             [{"path": str(trace)}])
    assert copied["values"]["workflow-source-mode:w"] == "Agent 上下文"
    assert copied["values"]["workflow-sources:w:Agent 上下文"] == [str(trace)]
    assert copied["missing_sources"] == []


def test_copy_can_find_a_document_beyond_the_old_picker_window(tmp_path, monkeypatch):
    from lib.infrastructure.training_workflow import create_run
    from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver

    selected = tmp_path / "manual.txt"
    selected.write_text("Existing library document", encoding="utf-8")
    output = tmp_path / "output"
    run_id = create_run(output, sources=[selected], targets=["cpt"])
    inventory = [{"path": str(tmp_path / f"older-{i}.txt")} for i in range(600)]
    inventory.append({"path": str(selected)})
    limits = []

    def bounded_inventory(_workspace, _suffixes, limit):
        limits.append(limit)
        return inventory[:limit]

    monkeypatch.setattr(FilesystemWorkflowDriver, "source_files", staticmethod(bounded_inventory))
    def copy_screen(output, run_id):
        import streamlit as st
        from pathlib import Path
        from lib.bootstrap.workflows import workflow_application
        from lib.bootstrap.creation_drafts import creation_draft_application
        from lib.presentation.streamlit.workflow_reuse import reuse_run_as_draft
        st.button("Copy", key="copy", on_click=reuse_run_as_draft,
                  args=(workflow_application(Path(output).parent, Path(output)),
                        creation_draft_application(Path(output)), "default", run_id))

    ui = AppTest.from_function(copy_screen, args=(str(output), run_id)).run()
    ui.button(key="copy").click().run()
    assert not ui.exception
    assert limits == [5000]
    assert ui.session_state["workflow-form-draft:default"]["workflow-sources:default:文档资料"] == [str(selected)]
    assert ui.session_state["workflow-reuse-notice:default"]["missing_sources"] == []


def screen(output, run_id, inventory):
    import streamlit as st
    from pathlib import Path
    from unittest.mock import patch
    from lib.application.workflow_service import WorkflowApplication
    from lib.application.workflow_node_models_service import WorkflowNodeModelsApplication
    from lib.bootstrap.creation_drafts import creation_draft_application
    from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver
    from lib.presentation.streamlit.workflow_reuse import reuse_run_as_draft
    from lib.presentation.streamlit.work_drafts import render_work_drafts
    from lib.presentation.streamlit import workflow_page

    class Driver(FilesystemWorkflowDriver):
        def source_files(self, *args, **kwargs):
            return [{**row, "label": Path(row["path"]).name} for row in inventory]
        def task_runs(self):
            return []
        def default_sft_output_style(self):
            return "separated"

    class Inventory:
        def list_backends(self):
            return {"default_backend": "local", "default_model": "writer", "roles": {},
                    "backends": [{"name": "local", "models": ["writer", "judge"]}]}

    st.session_state.setdefault("ws", "default")
    application = WorkflowApplication(Driver(Path(output).parent, Path(output)))
    drafts = creation_draft_application(Path(output))
    st.button("Copy settings and edit", key="copy", on_click=reuse_run_as_draft,
              args=(application, drafts, "default", run_id))
    if st.session_state.get("nav") == "自动工作流":
        with patch.object(workflow_page, "render_canvas", lambda *args, **kwargs: None):
            workflow_page.render_workbench(application, lambda args: None,
                                           WorkflowNodeModelsApplication(Inventory()), draft_application=drafts)
    render_work_drafts(drafts, "default", lambda page: st.session_state.__setitem__("nav", page))


def failed_run(tmp_path):
    """Create a genuine frozen recipe without making any model/network call."""
    from lib.infrastructure.training_workflow import create_run

    output = tmp_path / "output"
    run_id = create_run(
        output, name="Failed search task", brief="Generate maintenance examples", targets=["sft"],
        sample_count=50000, max_units=60000, concurrency=8, batch_size=250,
        sft_output_style="drop", node_models=recipe()["node_models"],
        web_research=recipe()["web_research"],
    )
    folder = output / "workflows" / run_id
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    state.update(status="failed", error="web_search_no_safe_results")
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return output, run_id, folder


def test_copy_failed_run_preserves_edits_and_old_recipe_with_network_consent_off(tmp_path):
    output, run_id, folder = failed_run(tmp_path)
    originals = {name: (folder / name).read_bytes() for name in ("recipe.json", "state.json")}
    drafts = creation_draft_application(output)
    drafts.replace({"workflow-name:default": "Older autosave", "workflow-count:default": 1000})
    edited = {"workflow-name:default": "Unsaved current task", "workflow-count:default": 30000}
    ui = AppTest.from_function(screen, args=(str(output), run_id, [])).run()
    ui.session_state["workflow-form-draft:default"] = edited
    ui.session_state["workflow-name:default"] = "Stale widget"
    ui.session_state["workflow-web-research-enabled:default"] = True
    ui.session_state["workflow-web-research-session-consent:default"] = True
    ui.session_state["node-model:default:sft:generation:backend"] = "Old binding"
    ui.session_state["job:default:other-run"] = "Still running"
    ui.button(key="copy").click().run()
    assert not ui.exception
    assert ui.session_state["nav"] == "自动工作流"
    values = ui.session_state["workflow-form-draft:default"]
    assert values == creation_draft_application(output).load()
    assert values["workflow-name:default"] == "Failed search task"
    assert values["workflow-web-research-query:default"] == "maintenance safety"
    assert ui.session_state["workflow-web-research-enabled:default"] is False
    assert ui.session_state["workflow-web-research-session-consent:default"] is False
    assert ui.text_input(key="workflow-name:default").value == "Failed search task"
    assert ui.number_input(key="workflow-count:default").value == 50000
    assert ui.number_input(key="workflow-concurrency:default").value == 8
    assert ui.selectbox(key="workflow-sft-output-style:default").value == "drop"
    assert ui.selectbox(key="node-model:default:sft:generation:backend").value == "local"
    assert ui.selectbox(key="node-model:default:sft:generation:model:local").value == "judge"
    assert ui.number_input(key="node-model:default:sft:generation:context:local:judge").value == 196608
    assert ui.number_input(key="node-model:default:sft:generation:output:local:judge").value == 49152
    assert ui.session_state["workflow-node-bindings:default"]["sft"]["generation"]["model"] == "judge"
    assert ui.session_state["job:default:other-run"] == "Still running"
    assert any("已复制到新草稿" in item.value for item in ui.success)
    backup = ui.session_state["work-draft-switch-backup:default"]
    assert json.loads((output / ".creation-drafts" / (backup + ".json")).read_text(encoding="utf-8"))["values"] == edited
    ui.checkbox(key="workflow-web-research-enabled:default").check().run()
    assert not ui.exception
    assert ui.text_input(key="workflow-web-research-query:default").value == "maintenance safety"
    # Existing one-click return restores the edit made before copying this run.
    ui.button(key="work-draft-return:default").click().run()
    assert not ui.exception
    assert ui.text_input(key="workflow-name:default").value == "Unsaved current task"
    assert ui.number_input(key="workflow-count:default").value == 30000
    assert originals == {name: (folder / name).read_bytes() for name in originals}


def test_document_copy_warns_about_missing_sources_and_actual_form_consumes_remaining_files(tmp_path):
    from lib.infrastructure.training_workflow import create_run

    library = tmp_path / "library"
    library.mkdir()
    available = library / "manual.txt"
    available.write_text("Existing library document", encoding="utf-8")
    unavailable = library / "removed.txt"
    unavailable.write_text("Removed library document", encoding="utf-8")
    output = tmp_path / "output"
    run_id = create_run(output, sources=[available, unavailable], name="Document task",
                        brief="Focus on maintenance steps", targets=["sft"],
                        sample_count=1200, chunk_chars=2400, sft_output_style="drop")
    original = (output / "workflows" / run_id / "recipe.json").read_bytes()
    unavailable.unlink()
    ui = AppTest.from_function(screen, args=(str(output), run_id,
                                             [{"path": str(available)}, {"path": str(unavailable)}])).run()
    ui.button(key="copy").click().run()
    assert not ui.exception
    assert any("请重新选择或上传" in item.value for item in ui.warning)
    assert any("removed.txt" in item.value for item in ui.caption)
    assert ui.segmented_control(key="workflow-source-mode:default").value == "文档资料"
    assert ui.multiselect(key="workflow-sources:default:文档资料").value == [str(available)]
    assert ui.text_area(key="workflow-source-brief:default:文档资料").value == "Focus on maintenance steps"
    assert ui.number_input(key="workflow-chunk-chars:default").value == 2400
    assert (output / "workflows" / run_id / "recipe.json").read_bytes() == original


@pytest.mark.parametrize("failure", ["save_snapshot", "replace"])
def test_failed_copy_does_not_discard_edits_or_navigate(tmp_path, monkeypatch, failure):
    from lib.application.creation_draft_service import CreationDraftApplication

    output, run_id, folder = failed_run(tmp_path)
    drafts = creation_draft_application(output)
    on_disk = {"workflow-name:default": "Autosaved current task", "workflow-count:default": 1000}
    drafts.replace(on_disk)
    edited = {**on_disk, "workflow-count:default": 50000}
    original = (folder / "recipe.json").read_bytes()

    def fail(*args, **kwargs):
        raise OSError("Controlled storage failure")

    monkeypatch.setattr(CreationDraftApplication, failure, fail)
    ui = AppTest.from_function(screen, args=(str(output), run_id, [])).run()
    ui.session_state["workflow-form-draft:default"] = edited
    ui.session_state["workflow-web-research-session-consent:default"] = True
    ui.session_state["nav"] = "任务管理"
    ui.button(key="copy").click().run()
    assert not ui.exception
    assert ui.session_state["workflow-form-draft:default"] == edited
    assert ui.session_state["workflow-web-research-session-consent:default"] is True
    assert ui.session_state["nav"] == "任务管理"
    assert ui.session_state["workflow-reuse-error:default"]
    assert drafts.load() == on_disk
    assert (folder / "recipe.json").read_bytes() == original
    if failure == "replace":
        backups = drafts.snapshots()
        assert len(backups) == 1
        assert drafts.restore_snapshot(backups[0]["id"]) == edited
    else:
        assert drafts.snapshots() == []
