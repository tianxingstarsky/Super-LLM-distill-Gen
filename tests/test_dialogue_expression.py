"""Natural expression coverage persists without fixed quotas or old replay drift."""
import json

import pytest

from lib.domain.dialogue_expression import compact_expression_guide, natural_expression_guide, validate_expression_design
from lib.domain.workflow_qa_director import validate_director_task, validate_question_brief
from lib.domain.workflow_quality import canonical
from lib.infrastructure.qa_agent_memory import MAX_MEMORY_BYTES, build_agent_memory
from lib.infrastructure.qa_history import QAHistory


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Everything below uses injected doubles or SQLite; no paid model calls."""
    yield


def expression(**changes):
    return {"form": "ultra_short", "clarity": "object_explicit", "addressing": "none", **changes}


def accepted(index, question="怎么用？", **changes):
    return {"id": str(index), "status": "eligible", "question": question, "answer": "先检查条件。",
            "qa_type": "closed_book", "answer_policy": "clarify", "visible_context": "",
            "expression_design": expression(), **changes}


def test_guide_can_cover_short_ambiguous_and_explicit_roleplay_without_quotas():
    guide = natural_expression_guide()
    assert guide["fixed_quotas"] is False and guide["labels_are_optional"] is True
    assert {item["id"] for item in guide["profiles"]} >= {
        "ultra_short", "ambiguous", "ordinary", "elliptical_followup", "explicit_roleplay"}
    assert "无需称呼" in guide["default_addressing"]
    assert all("weight" not in item and "quota" not in item for item in guide["profiles"])
    guide["profiles"][0]["example"] = "mutated"
    assert natural_expression_guide()["profiles"][0]["example"] != "mutated"


def test_optional_expression_examples_can_shrink_without_losing_modes_or_configuration():
    guide = natural_expression_guide()
    identifiers = [item["id"] for item in guide["profiles"]]
    before = len(canonical(guide).encode("utf-8"))
    while compact_expression_guide(guide):
        pass
    assert len(canonical(guide).encode("utf-8")) < before
    assert [item["id"] for item in guide["profiles"]] == identifiers
    assert all(item["use"] for item in guide["profiles"])
    assert guide["fixed_quotas"] is False
    assert guide["configuration"] == natural_expression_guide()["configuration"]
    assert "example" in natural_expression_guide()["profiles"][0]


def test_older_capacity_policy_ignores_new_expression_reserve(monkeypatch):
    import lib.infrastructure.workflow_source_context as contexts
    recipe = {"node_prompt_system": "old system", "targets": ["sft"],
              "node_prompt_templates": {"director": {"workflow.qa_director": "old pinned template"}},
              "qa_director": {"enabled": True, "question_rules": "old question", "answer_rules": "old answer"}}
    def forbidden(*args):
        raise AssertionError("legacy context capacity was migrated")
    monkeypatch.setattr(contexts, "_director_request_reserve", forbidden)
    expected = 131072 - 32768 - len(b"old system") - len(b"old pinned template")
    expected -= len(b"old question\nold answer\n") + 8192 + 1024
    assert contexts.context_capacity(recipe) == expected


def test_expression_labels_are_open_and_a_real_roleplay_address_is_not_rejected():
    custom = {"form": "手写断句式请求", "addressing": "老张",
              "roleplay_basis": "用户明确要求写一段两个老同学的对话。"}
    assert validate_expression_design(custom) == custom
    task = {"id": "a", "qa_type": "closed_book", "question": "老张，这个怎么改？",
            "visible_context": "", "answer_policy": "clarify", "guidance": "等待用户说明对象。",
            "evidence_quotes": ["已知事实。"], "expression_design": custom}
    assert validate_director_task(task, expected_type="closed_book")["expression_design"] == custom
    assert validate_director_task({**task, "question": "不行了"}, expected_type="closed_book")["question"] == "不行了"


def test_blind_questioner_receives_only_known_source_free_expression_guide():
    from lib.infrastructure.dialogue_role_messages import question_role_messages
    brief = {"topic": "设备", "communication_goal": "询问用法。", "expression_design": expression()}
    data = {"question_brief": brief, "question_rules": "保持日常简洁表达。",
            "natural_expression_guidance": natural_expression_guide()}
    messages = question_role_messages("Write only a user message.", data)
    assert "ultra_short" in messages[0]["content"]
    assert not any(message.get("name") == "source_memory" for message in messages)
    changed = {**data, "natural_expression_guidance": {**data["natural_expression_guidance"], "private_fact": "secret source"}}
    with pytest.raises(ValueError, match="private_context_in_question_request"):
        question_role_messages("system", changed)


@pytest.mark.parametrize("value", [{}, "short", {"unknown": "label"}, {"form": " "},
                                    {"form": 1}, {"form": "x" * 257}, {"form": "x\x00y"}])
def test_expression_transport_is_bounded_before_a_model_request(value):
    with pytest.raises(ValueError, match="invalid_expression_design"):
        validate_expression_design(value)
    with pytest.raises(ValueError, match="invalid_expression_design"):
        validate_question_brief({"topic": "设备", "communication_goal": "排查", "expression_design": value})


def test_accepted_expression_coverage_survives_restart_without_inflating_on_replay(tmp_path):
    path = tmp_path / "history.sqlite3"
    first = accepted(1)
    with QAHistory(path) as history:
        assert history.add(first, namespace="sft")
        assert history.add(first, namespace="sft")
        assert not history.add(accepted(2, status="quarantined"), namespace="sft")
        assert not history.add(accepted(3, expression_design=expression(form="different label")), namespace="sft")
        identity = history.duplicate(first["question"], answer_policy="clarify", namespace="sft")["identity"]
    with QAHistory(path) as history:
        counts = history.dimension_coverage(namespace="sft", include_expression=True)
        assert counts["expression_form"] == [{"value": "ultra_short", "accepted": 1}]
        assert history.recent(namespace="sft", include_expression=True)[0]["expression_design"] == expression()
        assert "expression_design" not in history.recent(namespace="sft")[0]
        assert set(history.dimension_coverage(namespace="sft")) == {"source_id", "topic", "skill"}
        assert identity == history.duplicate(first["question"], answer_policy="clarify", namespace="sft")["identity"]


def test_new_editorial_labels_do_not_change_exact_learner_task_identity(tmp_path):
    with QAHistory(tmp_path / "history.sqlite3") as history:
        assert history.add(accepted(1), learner_task_dedup=True)
        assert not history.add(accepted(2, expression_design=expression(form="ordinary")), learner_task_dedup=True)
        assert history.coverage() == {"closed_book": 1}


def test_legacy_database_migration_does_not_classify_or_rewrite_old_questions(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with QAHistory(path) as history:
        old = accepted(1)
        old.pop("expression_design")
        assert history.add(old)
        identity = history.duplicate(old["question"], answer_policy="clarify")["identity"]
        history.connection.execute("ALTER TABLE qa_history DROP COLUMN expression_design")
    with QAHistory(path) as history:
        assert history.recent()[0]["question"] == old["question"]
        assert history.duplicate(old["question"], answer_policy="clarify")["identity"] == identity
        assert history.dimension_coverage(include_expression=True)["expression_form"] == []


def test_bounded_memory_has_opt_in_coverage_and_preserves_full_ledger(tmp_path):
    with QAHistory(tmp_path / "local.sqlite3") as local, QAHistory(tmp_path / "published.sqlite3") as published:
        for index in range(70):
            assert local.add(accepted(index, "设备问题" + str(index), expression_design=expression(form="form " + str(index))),
                             namespace="sft")
        old = build_agent_memory(local, published, ["sft"], history=local.recent(namespace="sft"),
                                 feedback={}, candidates=[])
        assert set(old["ledgers"]["current_run"]["sft"]["accepted_dimensions"]) == {"source_id", "topic", "skill"}
        assert all("expression_design" not in item for item in old["recent_designs"])
        memory = build_agent_memory(local, published, ["sft"],
            history=local.recent(namespace="sft", include_expression=True), feedback={}, candidates=[], include_expression=True)
        assert len(canonical(memory).encode("utf-8")) <= MAX_MEMORY_BYTES
        assert len(memory["ledgers"]["current_run"]["sft"]["accepted_dimensions"]["expression_form"]) <= 8
        assert local.coverage(namespace="sft") == {"closed_book": 70}


def opt_in_run(tmp_path, *, writer=None, sample_count=3):
    from tests.test_workflow_qa_director_engine import make_run, Writer, Reviewer
    from lib.infrastructure import training_workflow as engine
    output, run_id, path = make_run(tmp_path, sample_count=sample_count)
    recipe = engine.read_json(path / "recipe.json")
    recipe["qa_expression_version"] = 1
    engine.atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    engine.atomic_json(path / "state.json", state)
    model = writer or Writer()
    final = engine.Workflow(output, run_id, tmp_path, generator=model, jev=Reviewer()).execute()
    return final, path, output, run_id, model


def test_new_planner_receives_persistent_expression_coverage(tmp_path):
    from tests.test_workflow_qa_director_engine import Writer, request_data
    class NaturalWriter(Writer):
        def chat(self, messages, **kwargs):
            result = json.loads(super().chat(messages, **kwargs))
            data = request_data(messages)
            if "candidates" in data:
                for task in result["tasks"]:
                    task["expression_design"] = expression(form="ordinary")
            return json.dumps(result, ensure_ascii=False)
    state, _, output, _, writer = opt_in_run(tmp_path, writer=NaturalWriter())
    assert state["status"] == "completed", state.get("error")
    plans = [request_data(call) for call in writer.calls if "candidates" in request_data(call)]
    assert plans[0]["natural_expression_guidance"]["fixed_quotas"] is False
    assert plans[2]["agent_memory"]["ledgers"]["current_run"]["sft"]["accepted_dimensions"]["expression_form"] == [
        {"value": "ordinary", "accepted": 2}]
    with QAHistory(output / "qa-history.sqlite3") as history:
        assert history.dimension_coverage(namespace="sft", include_expression=True)["expression_form"] == [
            {"value": "ordinary", "accepted": 3}]


def test_new_expression_snapshot_is_reused_after_external_history_changes(tmp_path):
    from tests.test_workflow_qa_director_engine import Writer, Reviewer, request_data
    from lib.infrastructure import training_workflow as engine
    writer = Writer(fail_batch=1)
    state, _, output, run_id, writer = opt_in_run(tmp_path, writer=writer)
    assert state["status"] == "failed"
    original = next(request_data(call) for call in writer.calls
                    if "candidates" in request_data(call) and request_data(call)["offset"] == 1)
    with QAHistory(output / "qa-history.sqlite3") as history:
        assert history.add(accepted("external", "外部新任务", expression_design=expression(form="ambiguous")), namespace="sft")
    writer.fail_batch = None
    resumed = engine.resume(output, run_id, tmp_path, generator=writer, jev=Reviewer())
    assert resumed["status"] == "completed", resumed.get("error")
    repeated = [request_data(call) for call in writer.calls
                if "candidates" in request_data(call) and request_data(call)["offset"] == 1]
    assert len(repeated) == 2
    assert repeated[1]["agent_memory"] == original["agent_memory"]
    assert repeated[1]["natural_expression_guidance"] == original["natural_expression_guidance"]
