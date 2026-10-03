"""Public-query web research is explicit, bounded, and pinned to a run."""
from __future__ import annotations

import json

import pytest

from lib.application.workflow_service import WorkflowApplication
from lib.domain.web_research import validate_web_research
from lib.infrastructure import brave_web_research, training_workflow
from lib.infrastructure.training_workflow import Workflow, create_run, read_json, run_path, verify_artifacts
from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver


CONFIG = {"provider": "brave", "query": "设备维护安全规范", "count": 3}
MULTI_CONFIG = {**CONFIG, "more_queries": ["设备检修风险", "维护记录质量规范"]}
RESULTS = [{"title": "公开维护指南", "url": "https://example.org/guide",
            "snippet": "检修设备前检查电源与故障状态。"}]


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = json.dumps(payload if payload is not None else {
            "web": {"results": [
                {"title": "Public &amp; guide", "description": "A short maintenance guide.",
                 "url": "https://example.org/guide?edition=1#part"},
                {"title": "Dup", "description": "Same address", "url": "https://example.org/guide?edition=1"},
                {"title": "Local", "description": "Must be dropped", "url": "https://127.0.0.1/admin"},
                {"title": "Insecure", "description": "Must be dropped", "url": "http://example.org/guide"},
                {"title": "Private", "description": "person@example.org", "url": "https://example.org/private"},
            ]}}).encode("utf-8")

    def read(self, size):
        assert size == brave_web_research.MAX_RESPONSE_BYTES + 1
        return self.payload[:size]


class FakeConnection:
    calls = []
    response = FakeResponse()

    def __init__(self, host, timeout):
        self.calls.append({"host": host, "timeout": timeout})

    def request(self, method, path, headers):
        self.calls[-1].update(method=method, path=path, headers=headers)

    def getresponse(self):
        return self.response

    def close(self):
        self.calls[-1]["closed"] = True


@pytest.mark.parametrize("config,brief,sources,targets,code", [
    (True, "开放需求", (), ["sft"], "web_research_invalid_config"),
    ({"provider": "brave", "query": "x", "count": True}, "开放需求", (), ["sft"], "web_research_invalid_config"),
    (CONFIG, "", (), ["sft"], "web_research_requires_open_brief"),
    (CONFIG, "开放需求", ["local.pdf"], ["sft"], "web_research_requires_open_brief"),
    (CONFIG, "开放需求", (), ["gsm8k"], "web_research_requires_planning_target"),
    ({**CONFIG, "query": "person@example.org"}, "开放需求", (), ["sft"], "web_research_query_private_or_invalid"),
    ({**CONFIG, "query": "sk-" + "x" * 20}, "开放需求", (), ["sft"], "web_research_query_private_or_invalid"),
    ({**CONFIG, "query": "https://private.example/data"}, "开放需求", (), ["sft"], "web_research_query_private_or_invalid"),
    ({**CONFIG, "query": "C:\\Users\\Private\\plan"}, "开放需求", (), ["sft"], "web_research_query_private_or_invalid"),
    ({**CONFIG, "query": "x" * 161}, "开放需求", (), ["sft"], "web_research_query_private_or_invalid"),
])
def test_invalid_search_cannot_create_a_run(tmp_path, config, brief, sources, targets, code):
    app = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, tmp_path / "out"))
    with pytest.raises(ValueError, match=f"^{code}$"):
        app.create_run(sources=sources, brief=brief, targets=targets, web_research=config)
    assert not (tmp_path / "out").exists()


def test_public_query_is_normalized_without_using_private_brief():
    config = validate_web_research({**CONFIG, "query": "  ＡＩ   training\n data  "},
                                   brief="Internal project roadmap", targets=["sft"])
    assert config == {"provider": "brave", "query": "AI training data", "count": 3}


def test_multiple_public_topics_are_explicit_bounded_and_unique():
    config = validate_web_research({**MULTI_CONFIG, "more_queries": [" 设备检修风险 ",
                                                                   "维护记录质量规范"]},
                                   brief="Internal roadmap", targets=["sft"])
    assert config == MULTI_CONFIG
    for extra in (["主题"] * 5, ["设备维护安全规范"], ["person@example.org"], [42]):
        with pytest.raises(ValueError):
            validate_web_research({**CONFIG, "more_queries": extra},
                                  brief="Internal roadmap", targets=["sft"])
    with pytest.raises(ValueError, match="^web_research_invalid_config$"):
        validate_web_research({**CONFIG, "more_queries": [" 设备维护安全规范 "]},
                              brief="Internal roadmap", targets=["sft"])


def test_brave_adapter_uses_fixed_https_endpoint_and_never_follows_results(monkeypatch):
    monkeypatch.setenv(brave_web_research.KEY_ENV, "test-api-key")
    FakeConnection.calls = []
    FakeConnection.response = FakeResponse()
    monkeypatch.setattr(brave_web_research.http.client, "HTTPSConnection", FakeConnection)
    results = brave_web_research.search(CONFIG)
    assert results == [{"title": "Public & guide", "url": "https://example.org/guide?edition=1",
                        "snippet": "A short maintenance guide."}]
    assert len(FakeConnection.calls) == 1
    call = FakeConnection.calls[0]
    assert call["host"] == "api.search.brave.com" and call["timeout"] == 8
    assert call["method"] == "GET" and call["path"].startswith("/res/v1/web/search?")
    assert "count=3" in call["path"] and "safesearch=strict" in call["path"]
    assert "设备维护安全规范" not in call["path"]  # URL-encoded public query only
    assert call["headers"]["X-Subscription-Token"] == "test-api-key"
    assert call["closed"]


def test_connection_check_is_opt_in_and_uses_only_a_fixed_public_query(monkeypatch):
    monkeypatch.delenv(brave_web_research.KEY_ENV, raising=False)
    FakeConnection.calls = []
    assert brave_web_research.check_connection() == "not_configured"
    assert FakeConnection.calls == []

    monkeypatch.setenv(brave_web_research.KEY_ENV, "private-test-key")
    FakeConnection.response = FakeResponse(payload={"web": {"results": []}})
    monkeypatch.setattr(brave_web_research.http.client, "HTTPSConnection", FakeConnection)
    app = WorkflowApplication(FilesystemWorkflowDriver(".", "out"))
    assert app.check_web_research_connection() == "ready"
    assert len(FakeConnection.calls) == 1
    call = FakeConnection.calls[0]
    assert call["host"] == brave_web_research.HOST
    assert "q=Brave+Search" in call["path"] and "count=1" in call["path"]
    assert CONFIG["query"] not in call["path"]
    assert "private-test-key" not in call["path"]
    assert call["closed"]


def test_connection_check_does_not_return_provider_error_or_key(monkeypatch):
    monkeypatch.setenv(brave_web_research.KEY_ENV, "private-test-key")

    class LeakingConnection(FakeConnection):
        def request(self, method, path, headers):
            raise RuntimeError("private-test-key; provider rejected query")

    FakeConnection.calls = []
    monkeypatch.setattr(brave_web_research.http.client, "HTTPSConnection", LeakingConnection)
    assert brave_web_research.check_connection() == "unavailable"


def test_multiple_explicit_queries_have_bounded_requests_and_deduped_leads(monkeypatch):
    monkeypatch.setenv(brave_web_research.KEY_ENV, "test-api-key")
    FakeConnection.calls = []
    FakeConnection.response = FakeResponse()
    monkeypatch.setattr(brave_web_research.http.client, "HTTPSConnection", FakeConnection)
    assert brave_web_research.search(MULTI_CONFIG) == [
        {"query": MULTI_CONFIG["query"], "title": "Public & guide",
         "url": "https://example.org/guide?edition=1",
         "snippet": "A short maintenance guide."}]
    assert len(FakeConnection.calls) == 3
    assert all(call["host"] == brave_web_research.HOST and call["closed"]
               for call in FakeConnection.calls)
    assert len({call["path"] for call in FakeConnection.calls}) == 3


def test_stop_is_checked_before_each_public_search_request(monkeypatch):
    monkeypatch.setenv(brave_web_research.KEY_ENV, "test-api-key")
    FakeConnection.calls = []
    FakeConnection.response = FakeResponse()
    monkeypatch.setattr(brave_web_research.http.client, "HTTPSConnection", FakeConnection)
    attempts = []

    def check_stop():
        attempts.append(len(attempts))
        if len(attempts) == 2:
            raise RuntimeError("stopped")

    with pytest.raises(RuntimeError, match="^stopped$"):
        brave_web_research.search(MULTI_CONFIG, before_query=check_stop)
    assert len(attempts) == 2
    assert len(FakeConnection.calls) == 1


@pytest.mark.parametrize("response", [FakeResponse(status=302),
                                      FakeResponse(payload={"web": []}),
                                      FakeResponse(payload={"web": {"results": []}})])
def test_redirect_malformed_or_empty_response_fails_closed(monkeypatch, response):
    monkeypatch.setenv(brave_web_research.KEY_ENV, "test-api-key")
    FakeConnection.calls = []
    FakeConnection.response = response
    monkeypatch.setattr(brave_web_research.http.client, "HTTPSConnection", FakeConnection)
    code = "web_search_no_safe_results" if response.payload == b'{"web": {"results": []}}' else "web_search_provider_error"
    with pytest.raises(ValueError, match=f"^{code}$"):
        brave_web_research.search(CONFIG)
    assert len(FakeConnection.calls) == 1 and FakeConnection.calls[0]["closed"]


def test_provider_response_bytes_are_bounded_and_error_hides_token(monkeypatch):
    monkeypatch.setenv(brave_web_research.KEY_ENV, "secret-api-token")
    FakeConnection.calls = []
    FakeConnection.response = FakeResponse(payload={"web": {"results": []}})
    FakeConnection.response.payload = b"x" * (brave_web_research.MAX_RESPONSE_BYTES + 1)
    monkeypatch.setattr(brave_web_research.http.client, "HTTPSConnection", FakeConnection)
    with pytest.raises(ValueError, match="^web_search_provider_error$") as error:
        brave_web_research.search(CONFIG)
    assert "secret-api-token" not in str(error.value)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://example.org/a",
                                  "https://user:pass@example.org/a", "https://localhost/a",
                                  "https://10.0.0.2/private", "https://metadata.google.internal/a",
                                  "https://example.org/\nheader", "https://example.org:8443/a",
                                  "https://2130706433/admin", "https://127.1/admin",
                                  "https://printer.lan/admin", "https://intranet.corp/admin"])
def test_source_url_rejects_unsafe_or_non_public_targets(url):
    assert brave_web_research._public_url(url) is None


def test_network_exception_does_not_surface_query_or_api_key(monkeypatch):
    monkeypatch.setenv(brave_web_research.KEY_ENV, "sensitive-key")

    class ErrorConnection(FakeConnection):
        def request(self, method, path, headers):
            raise RuntimeError("sensitive-key; 设备维护安全规范")

    monkeypatch.setattr(brave_web_research.http.client, "HTTPSConnection", ErrorConnection)
    with pytest.raises(ValueError, match="^web_search_provider_error$") as error:
        brave_web_research.search(CONFIG)
    assert "sensitive-key" not in str(error.value)
    assert CONFIG["query"] not in str(error.value)


def test_search_is_real_planning_input_cached_and_manifested(tmp_path, monkeypatch):
    output = tmp_path / "out"
    run_id = create_run(output, brief="Internal model training plan", targets=["cpt"],
                        max_units=1, tasks=1, web_research=CONFIG)
    path = run_path(output, run_id)
    assert read_json(path / "recipe.json")["web_research"] == CONFIG
    calls, prompts = [], []

    def fake_search(config, *, before_query=None):
        if before_query:
            before_query()
        calls.append(config)
        return RESULTS

    monkeypatch.setattr(training_workflow, "search_web", fake_search)
    run = Workflow(output, run_id, tmp_path)

    def answer(key, role, prompt_id, data):
        prompts.append((prompt_id, data))
        if prompt_id == "workflow.plan":
            return {"tasks": ["Explain safe equipment maintenance"]}
        if prompt_id == "workflow.corpus":
            return {"text": "Inspect the power connection before any equipment maintenance."}
        return {"keep": True, "grounded": True, "reasoning_valid": True,
                "correctness": 5, "scores": {k: 5 for k in
                ("correctness", "reasoning", "grounding", "instruction", "safety")},
                "reason": "The short statement follows the task."}

    run.ask = answer
    state = run.execute()
    assert state["status"] == "completed"
    assert calls == [CONFIG]
    plan_request = next(data for prompt_id, data in prompts if prompt_id == "workflow.plan")
    assert plan_request["web_research"]["leads"] == RESULTS
    assert "Internal model training plan" not in json.dumps(plan_request["web_research"])
    report = read_json(path / "artifacts" / "quality.json")
    assert report["web_research"]["status"] == "planning_leads_only"
    assert report["web_research"]["topics"] == 1
    assert read_json(path / "artifacts" / "web_research.json")["results"] == RESULTS
    assert "web_research.json" in verify_artifacts(path)["sha256"]
    assert "secret-api-token" not in (path / "recipe.json").read_text(encoding="utf-8")
    run.ask = lambda *args: pytest.fail("cached plan must not ask the model again")
    assert len(run.plan()) == 1
    assert calls == [CONFIG]


def test_multiple_topics_rotate_across_large_planning_batches(tmp_path, monkeypatch):
    output = tmp_path / "out"
    run_id = create_run(output, brief="Generate distinct maintenance exercises",
                        targets=["sft"], max_units=51, tasks=51, web_research=MULTI_CONFIG)
    calls, planning = [], []
    topics = [MULTI_CONFIG["query"], *MULTI_CONFIG["more_queries"]]
    leads = [{"query": topics[i], "title": f"Guide {i}",
              "url": f"https://example.org/guide-{i}",
              "snippet": f"Public maintenance topic {i}."} for i in range(3)]
    def fake_search(config, **kwargs):
        calls.append(config)
        return [{key: value for key, value in row.items() if key != "query"}
                for row in leads if row["query"] == config["query"]]

    monkeypatch.setattr(training_workflow, "search_web", fake_search)
    run = Workflow(output, run_id, tmp_path)

    def answer(key, role, prompt_id, data):
        assert prompt_id == "workflow.plan"
        planning.append(data)
        return {"tasks": [f"Exercise {data['offset'] + i}" for i in range(data["count"])]}

    run.ask = answer
    assert len(run.plan()) == 51
    assert calls == [{"provider": "brave", "query": query, "count": 3} for query in topics]
    assert read_json(run.path / "state.json")["web_research"]["topics"] == 3
    assert [request["web_research"]["leads"] for request in planning] == [[leads[0]], [leads[1]]]
    document = read_json(run.path / "checkpoints" / "ingest" /
                         f"{training_workflow.digest(['web_research', MULTI_CONFIG])}.json")["data"]
    assert document["queries"] == topics
    assert "Internal roadmap" not in json.dumps(document)
    receipt = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, output)).web_research_results(run_id)
    assert receipt["queries"] == topics and receipt["results"] == leads


def test_large_plan_rotates_topics_before_reusing_a_topic_lead(tmp_path, monkeypatch):
    output = tmp_path / "out"
    five_each = {**MULTI_CONFIG, "count": 5}
    run_id = create_run(output, brief="Generate distinct maintenance exercises",
                        targets=["sft"], max_units=301, tasks=301, web_research=five_each)
    topics = [MULTI_CONFIG["query"], *MULTI_CONFIG["more_queries"]]
    by_topic = {query: [{"title": f"Guide {topic_index}-{result_index}",
                         "url": f"https://example.org/guide-{topic_index}-{result_index}",
                         "snippet": f"Public topic {topic_index} result {result_index}."}
                        for result_index in range(5)]
                for topic_index, query in enumerate(topics)}
    monkeypatch.setattr(training_workflow, "search_web",
                        lambda config, **kwargs: by_topic[config["query"]])
    run = Workflow(output, run_id, tmp_path)
    planned_leads = []

    def answer(key, role, prompt_id, data):
        planned_leads.append(data["web_research"]["leads"][0])
        return {"tasks": [f"Exercise {data['offset'] + i}" for i in range(data["count"])]}

    run.ask = answer
    assert len(run.plan()) == 301
    assert [lead["query"] for lead in planned_leads] == [
        topics[0], topics[1], topics[2], topics[0], topics[1], topics[2], topics[0]]
    assert [lead["title"] for lead in planned_leads] == [
        "Guide 0-0", "Guide 1-0", "Guide 2-0", "Guide 0-1",
        "Guide 1-1", "Guide 2-1", "Guide 0-2"]


def test_late_topic_failure_reuses_checked_earlier_topics_on_resume(tmp_path, monkeypatch):
    output = tmp_path / "out"
    run_id = create_run(output, brief="Generate maintenance exercises",
                        targets=["sft"], web_research=MULTI_CONFIG)
    topics = [MULTI_CONFIG["query"], *MULTI_CONFIG["more_queries"]]
    calls = []
    fail_last = True

    def fake_search(config, **kwargs):
        nonlocal fail_last
        calls.append(config["query"])
        if config["query"] == topics[-1] and fail_last:
            fail_last = False
            raise ValueError("web_search_provider_error")
        index = topics.index(config["query"])
        return [{"title": f"Guide {index}", "url": f"https://example.org/guide-{index}",
                 "snippet": f"Public topic {index}."}]

    monkeypatch.setattr(training_workflow, "search_web", fake_search)
    with pytest.raises(ValueError, match="^web_search_provider_error$"):
        Workflow(output, run_id, tmp_path).research()
    assert calls == topics
    first_checkpoint = run_path(output, run_id) / "checkpoints" / "ingest" / (
        training_workflow.digest(["web_research_topic", MULTI_CONFIG, topics[0]]) + ".json")
    assert first_checkpoint.is_file()
    assert Workflow(output, run_id, tmp_path).research()["queries"] == topics
    assert calls == [*topics, topics[-1]]


def test_resumed_topic_checkpoint_is_validated_even_with_recomputed_hash(tmp_path, monkeypatch):
    output = tmp_path / "out"
    run_id = create_run(output, brief="Generate maintenance exercises",
                        targets=["sft"], web_research=MULTI_CONFIG)
    topics = [MULTI_CONFIG["query"], *MULTI_CONFIG["more_queries"]]
    calls = []

    def fake_search(config, **kwargs):
        calls.append(config["query"])
        if config["query"] == topics[-1]:
            raise ValueError("web_search_provider_error")
        return [{"title": "Guide", "url": "https://example.org/guide",
                 "snippet": "Public guide."}]

    monkeypatch.setattr(training_workflow, "search_web", fake_search)
    with pytest.raises(ValueError, match="^web_search_provider_error$"):
        Workflow(output, run_id, tmp_path).research()
    checkpoint = run_path(output, run_id) / "checkpoints" / "ingest" / (
        training_workflow.digest(["web_research_topic", MULTI_CONFIG, topics[0]]) + ".json")
    saved = read_json(checkpoint)
    saved["data"]["results"][0]["url"] = "https://127.0.0.1/private"
    saved["sha256"] = training_workflow.digest(saved["data"])
    training_workflow.atomic_json(checkpoint, saved)
    with pytest.raises(ValueError, match="^web_research_integrity_error$"):
        Workflow(output, run_id, tmp_path).research()
    assert calls == topics


def test_all_empty_topics_are_retried_instead_of_cached_forever(tmp_path, monkeypatch):
    output = tmp_path / "out"
    run_id = create_run(output, brief="Generate maintenance exercises",
                        targets=["sft"], web_research=MULTI_CONFIG)
    topics = [MULTI_CONFIG["query"], *MULTI_CONFIG["more_queries"]]
    calls = []
    has_results = False

    def fake_search(config, **kwargs):
        calls.append(config["query"])
        if not has_results:
            return []
        index = topics.index(config["query"])
        return [{"title": f"Guide {index}", "url": f"https://example.org/guide-{index}",
                 "snippet": f"Public topic {index}."}]

    monkeypatch.setattr(training_workflow, "search_web", fake_search)
    with pytest.raises(ValueError, match="^web_search_no_safe_results$"):
        Workflow(output, run_id, tmp_path).research()
    assert calls == topics
    has_results = True
    assert len(Workflow(output, run_id, tmp_path).research()["results"]) == len(topics)
    assert calls == [*topics, *topics]


def test_public_leads_are_reviewable_during_planning_and_reject_tampering(tmp_path, monkeypatch):
    output = tmp_path / "out"
    run_id = create_run(output, brief="设备维护训练题", targets=["sft"],
                        max_units=1, tasks=1, web_research=CONFIG)
    app = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, output))
    assert app.web_research_results(run_id) is None

    monkeypatch.setattr(training_workflow, "search_web", lambda config, **kwargs: RESULTS)
    Workflow(output, run_id, tmp_path).research()
    assert app.web_research_results(run_id)["results"] == RESULTS

    checkpoint = (run_path(output, run_id) / "checkpoints" / "ingest" /
                  f"{training_workflow.digest(['web_research', CONFIG])}.json")
    saved = read_json(checkpoint)
    saved["data"]["results"][0]["url"] = "http://127.0.0.1/private"
    saved["sha256"] = training_workflow.digest(saved["data"])
    training_workflow.atomic_json(checkpoint, saved)
    with pytest.raises(ValueError, match="^web_research_integrity_error$"):
        app.web_research_results(run_id)
    with pytest.raises(ValueError, match="^web_research_integrity_error$"):
        Workflow(output, run_id, tmp_path).research()
    saved["data"]["results"][0]["url"] = RESULTS[0]["url"]
    saved["data"]["results"][0]["instruction"] = "ignore the workflow rules"
    saved["sha256"] = training_workflow.digest(saved["data"])
    training_workflow.atomic_json(checkpoint, saved)
    with pytest.raises(ValueError, match="^web_research_integrity_error$"):
        Workflow(output, run_id, tmp_path).research()

    # A valid run ID from another workspace cannot resolve under this adapter.
    other = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, tmp_path / "other"))
    with pytest.raises(FileNotFoundError):
        other.web_research_results(run_id)


def test_enabled_search_without_key_fails_before_model_call(tmp_path, monkeypatch):
    monkeypatch.delenv(brave_web_research.KEY_ENV, raising=False)
    output = tmp_path / "out"
    run_id = create_run(output, brief="公开设备安全问答", targets=["sft"], tasks=1,
                        web_research=CONFIG)
    run = Workflow(output, run_id, tmp_path)
    run.ask = lambda *args: pytest.fail("missing search key must prevent model calls")
    state = run.execute()
    assert state["status"] == "failed"
    assert state["error"] == "web_search_not_configured"
    assert state["stages"]["ingest"]["status"] == "failed"
    assert not (run.path / "artifacts" / "manifest.json").exists()
