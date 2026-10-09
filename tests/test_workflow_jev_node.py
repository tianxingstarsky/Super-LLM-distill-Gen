"""Independent JEV execution, durable scoring, and historical compatibility."""
from __future__ import annotations

import json
from types import SimpleNamespace as Obj

import pytest

from lib.domain.workflow_package_review import package_review_stage, validate_package_review
from lib.domain.workflow_node_prompts import active_node_prompt_ids, snapshot_node_prompts
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_stream_journal import read_streams
from lib.llm_client import ChatClient
from tests.test_workflow_package_review_engine import make_run, Reviewer, good_verdict, report


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Model clients and transports are stubbed; this module opens no server."""
    yield


def configuration(enabled=True, **settings):
    return {"enabled": enabled, "node": "jev", "mode": "all", **settings}


def test_disabled_mode_keeps_new_route_marker_without_changing_legacy_shape():
    assert validate_package_review(None) == {"enabled": False}
    assert validate_package_review({"enabled": False}) == {"enabled": False}
    assert validate_package_review(configuration(False)) == {"enabled": False, "node": "jev"}
    assert package_review_stage(None) == "package"
    assert package_review_stage(configuration(False)) == "jev"


@pytest.mark.parametrize("node", ["package", "wrong", "", None, False, [], {}])
def test_invalid_node_marker_is_rejected(node):
    with pytest.raises(ValueError, match="invalid_package_review"):
        validate_package_review({"enabled": True, "node": node})


def test_prompt_catalog_and_visibility_keep_legacy_scope():
    assert "jev" not in snapshot_node_prompts(None, recipe_version=15)
    assert "jev" in snapshot_node_prompts(None, recipe_version=16)
    assert active_node_prompt_ids("jev", "文档", package_review=configuration()) == ("workflow.package_review",)
    assert active_node_prompt_ids("package", "文档", package_review=configuration()) == ()
    assert active_node_prompt_ids("jev", "文档", package_review=configuration(False)) == ()
    assert active_node_prompt_ids("package", "文档", package_review={"enabled": True}) == ("workflow.package_review",)


def test_disabled_jev_keeps_native_cpt_offline_and_reports_no_final_score(tmp_path):
    output, rid, path, _ = make_run(tmp_path, count=1, package_review=configuration(False))
    assert engine.read_json(path / "recipe.json")["version"] == 16
    reviewer = Reviewer()
    state = engine.Workflow(output, rid, tmp_path, jev=reviewer).execute()
    assert state["status"] == "completed", state.get("error")
    assert reviewer.calls == [] and state["usage"] == {}
    assert "jev" not in state["stages"]
    assert report(path)["package_review"] == {"enabled": False, "node": "jev", "status": "disabled"}


def test_jev_owns_scoring_progress_events_models_and_prompt(tmp_path, monkeypatch):
    custom = 'FINAL_JEV_RULE: return the fixed scoring schema; keep {literal} text.'
    output, rid, path, _ = make_run(tmp_path, count=3, package_review=configuration(),
        node_prompts={"jev": {"workflow.package_review": custom}},
        node_models={"jev": {"jev": {"backend": "local-review", "model": "judge-model",
            "context_window_tokens": 131072, "max_output_tokens": 32768}}})
    observations = []
    def during_review():
        current = engine.read_json(path / "state.json")
        observations.append((current["stages"]["jev"]["status"], current["stages"]["package"]["status"]))
    original_write = engine._write_quality_report
    def during_write(*args, **kwargs):
        current = engine.read_json(path / "state.json")
        assert current["stages"]["jev"]["status"] == "completed"
        assert current["stages"]["package"]["status"] == "running"
        assert current["stages"]["package"]["phase"] == "writing_artifacts"
        return original_write(*args, **kwargs)
    monkeypatch.setattr(engine, "_write_quality_report", during_write)
    reviewer = Reviewer(hook=during_review)
    state = engine.Workflow(output, rid, tmp_path, jev=reviewer).execute()
    assert state["status"] == "completed", state.get("error")
    assert observations == [("running", "pending")] * 3
    assert state["stages"]["jev"]["done"] == state["stages"]["jev"]["total"] == 3
    assert state["stages"]["package"]["done"] == state["stages"]["package"]["total"] == 1
    assert "jev.jev" in state["models"] and "package.jev" not in state["models"]
    assert all(call["messages"][0]["content"].endswith(custom) for call in reviewer.package_calls)
    assert all(call["kwargs"]["max_tokens"] == 32768 for call in reviewer.package_calls)
    assert {event["stage"] for event in state["events"] if event["kind"] == "model_started"} == {"jev"}
    assert any(event["stage"] == "jev" and event["kind"] == "stage_completed" for event in state["events"])
    assert (path / "stage-results/jev.jsonl").is_file()
    assert list((path / "checkpoints/jev").glob("*.json"))
    assert not (path / "checkpoints/package").exists()
    assert report(path)["package_review"]["targets"]["cpt"]["reviewed"] == 3


def test_real_chat_client_stream_is_recorded_under_jev_without_network(tmp_path):
    output, rid, path, _ = make_run(tmp_path, count=1, package_review=configuration())
    client = ChatClient("http://127.0.0.1:1/v1", "offline", "stub-reviewer")
    verdict_text = json.dumps(good_verdict())
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        assert kwargs.get("stream") is True
        return iter([
            Obj(choices=[Obj(index=0, delta=Obj(content=verdict_text[:40]), finish_reason=None)], usage=None),
            Obj(choices=[Obj(index=0, delta=Obj(content=verdict_text[40:]), finish_reason=None)], usage=None),
            Obj(choices=[Obj(index=0, delta=Obj(), finish_reason="stop")],
                usage=Obj(prompt_tokens=21, completion_tokens=13)),
        ])
    client.client.chat.completions.create = create
    try:
        state = engine.Workflow(output, rid, tmp_path, jev=client).execute()
        assert state["status"] == "completed", state.get("error")
        rows = read_streams(path, active=False, run_attempt=1, stage="jev")
        assert len(rows) == 1 and rows[0]["stage"] == "jev"
        assert rows[0]["status"] == "completed" and rows[0]["text"] == verdict_text
        assert read_streams(path, active=False, run_attempt=1, stage="package") == []
        assert len(requests) == 1
    finally:
        client.client.close()


def test_failed_jev_resume_reuses_completed_scores_and_keeps_package_pending(tmp_path):
    output, rid, path, _ = make_run(tmp_path, count=3, batch_size=1, package_review=configuration())
    reviewer = Reviewer(fail_at=2)
    state = engine.Workflow(output, rid, tmp_path, jev=reviewer).execute()
    assert state["status"] == "failed"
    assert state["stages"]["jev"]["status"] == "failed" and state["stages"]["jev"]["done"] == 1
    assert state["stages"]["package"]["status"] == "pending"
    first = reviewer.package_calls[0]["data"]["answer"]
    reviewer.fail_at = None
    state = engine.resume(output, rid, tmp_path, jev=reviewer)
    assert state["status"] == "completed", state.get("error")
    assert len(reviewer.package_calls) == 4
    assert sum(call["data"]["answer"] == first for call in reviewer.package_calls) == 1
    assert state["stages"]["jev"]["cached"] == 1
    assert engine.verify_artifacts(path)["counts"] == {"cpt": 3}


def test_zero_eligible_candidates_complete_jev_without_a_model_call(tmp_path):
    output, rid, path, _ = make_run(tmp_path, count=1, package_review=configuration())
    reviewer = Reviewer()
    workflow = engine.Workflow(output, rid, tmp_path, jev=reviewer)
    (path / "input_records.json").write_text("[]", encoding="utf-8")
    workflow.package({"cpt": [{"id": "bad", "source_id": "source", "status": "quarantined",
                                "reason": "potential_secret"}]})
    assert reviewer.calls == []
    assert workflow.state["stages"]["jev"]["status"] == "completed"
    assert workflow.state["stages"]["jev"]["done"] == workflow.state["stages"]["jev"]["total"] == 0
    assert report(path)["package_review"]["targets"]["cpt"]["status"] == "no_candidates"


def test_legacy_enabled_review_stays_inside_package(tmp_path):
    output, rid, path, _ = make_run(tmp_path, count=2, package_review={"enabled": True, "mode": "all"})
    assert engine.read_json(path / "recipe.json")["version"] < 16
    reviewer = Reviewer()
    state = engine.Workflow(output, rid, tmp_path, jev=reviewer).execute()
    assert state["status"] == "completed", state.get("error")
    assert "jev" not in state["stages"]
    assert state["stages"]["package"]["done"] == 2
    assert {event["stage"] for event in state["events"] if event["kind"] == "model_started"} == {"package"}
    assert list((path / "checkpoints/package").glob("*.json"))


def test_production_escalation_scores_each_candidate_once_in_jev(tmp_path):
    output, rid, path, _ = make_run(tmp_path, count=4, batch_size=1,
        package_review=configuration(mode="sample", sample_percent=25, max_samples_per_target=1),
        production={"goals": {"cpt": 4}, "round_size": 4, "max_attempts": 4, "min_acceptance_rate": 0})
    reviewer = Reviewer()
    reviewer.reject = lambda payload: len(reviewer.package_calls) == 1
    state = engine.Workflow(output, rid, tmp_path, jev=reviewer).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert len(reviewer.package_calls) == 4
    assert len({json.dumps(call["data"]["answer"], sort_keys=True) for call in reviewer.package_calls}) == 4
    assert state["stages"]["jev"]["status"] == "completed"
    assert engine.verify_artifacts(path)["counts"] == {"cpt": 3}
    summary = report(path)["package_review"]["targets"]["cpt"]
    assert summary["reviewed"] == 4 and summary["rejected"] == 1 and summary["unreviewed"] == 0
    assert summary["escalation"]["additional_reviews"] == 3
    assert any(event["kind"] == "package_review_expanded" and event["stage"] == "jev" for event in state["events"])


def test_invalid_production_jev_score_is_isolated_with_review_payload_identity(tmp_path):
    output, rid, path, _ = make_run(tmp_path, count=1, package_review=configuration(),
        production={"goals": {"cpt": 1}, "round_size": 1, "max_attempts": 1,
                    "min_acceptance_rate": 0, "item_retries": 0})
    reviewer = Reviewer(invalid_at=1)
    state = engine.Workflow(output, rid, tmp_path, jev=reviewer).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert len(reviewer.package_calls) == 1
    assert engine.verify_artifacts(path)["counts"] == {"cpt": 0}
    rejected = engine.read_json(path / "artifacts/cpt.records.json")[0]
    assert rejected["package_review"]["status"] == "rejected"
    assert rejected["package_review"]["candidate_sha256"]
    assert rejected["package_review"]["error"]
