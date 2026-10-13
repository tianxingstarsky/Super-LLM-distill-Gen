"""Offline checks for complete ledgers and bounded planner working memory."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest

from lib.domain.workflow_quality import canonical
from lib.infrastructure.qa_agent_memory import MAX_MEMORY_BYTES, build_agent_memory, compact_agent_memory
from lib.infrastructure.qa_history import QAHistory, learner_task_identity


def test_plan_repair_feedback_exposes_only_known_validation_codes():
    from lib.infrastructure.workflow_qa_director import _plan_validation_code
    assert _plan_validation_code(ValueError("qa_director_evidence_not_in_source_line_ranges")) == \
        "qa_director_evidence_not_in_source_line_ranges"
    assert _plan_validation_code(ValueError("invalid_qa_director_task: private body sentinel")) == \
        "qa_director_contract_validation_failed"


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """These tests inject models and never use the shared TCP fixture."""
    yield


def row(index, question="设备检查前怎样处理供电？", **changes):
    return {"id": str(index), "status": "eligible", "question": question,
            "answer": "检查前先断开供电。", "visible_context": "", "qa_type": "closed_book",
            "answer_policy": "answer", "run_id": "run-" + str(index),
            "source_id": "equipment", "topic": "设备安全", "skill": "流程判断", **changes}


def test_hidden_goal_type_and_policy_cannot_bypass_same_learner_input(tmp_path):
    with QAHistory(tmp_path / "qa.sqlite3") as store:
        assert store.add(row(1, dialogue_design={"interaction_goal": "检查作业", "user_intent": "安全执行"}),
                         namespace="sft", learner_task_dedup=True)
        duplicate = store.duplicate(row(2)["question"], namespace="sft", qa_type="partial",
            answer_policy="clarify", dialogue_design={"interaction_goal": "换了目标", "user_intent": "换了标签"},
            learner_task_dedup=True)
        assert duplicate["id"] == "1" and duplicate["match_kind"] == "exact_learner_task"
        assert not store.add(row(2, qa_type="partial", answer_policy="clarify",
            dialogue_design={"interaction_goal": "另一个标签"}), namespace="sft", learner_task_dedup=True)
        assert store.coverage(namespace="sft") == {"closed_book": 1}


def test_exact_core_identity_preserves_full_article_and_literal_conditions(tmp_path):
    prefix = "完整文章的共同前文。" * 500
    question = "文章为什么选择该方案？"
    with QAHistory(tmp_path / "qa.sqlite3") as store:
        assert store.add(row(1, question, visible_context=prefix + "对象甲", qa_type="grounded"),
                         learner_task_dedup=True)
        assert store.add(row(2, question, visible_context=prefix + "对象乙", qa_type="grounded"),
                         learner_task_dedup=True)
        assert store.duplicate(question, visible_context=prefix + "对象甲", learner_task_dedup=True)["id"] == "1"
    assert learner_task_identity("What is x² when Foo=3?") != learner_task_identity("What is x2 when foo=3?")
    assert learner_task_identity("What does 'a  b' mean?") != learner_task_identity("What does 'a b' mean?")
    assert learner_task_identity(" cafe\u0301?\r\n ") == learner_task_identity("café?")


def test_core_acceptance_is_atomic_across_independent_connections(tmp_path):
    path = tmp_path / "qa.sqlite3"
    with QAHistory(path):
        pass
    def accept(index):
        with QAHistory(path) as store:
            return store.add(row(index, dialogue_design={"interaction_goal": "goal " + str(index)}),
                             namespace="sft", learner_task_dedup=True)
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(accept, range(24))).count(True) == 1
    with QAHistory(path) as store:
        assert store.coverage(namespace="sft") == {"closed_book": 1}


def test_old_goal_qualified_history_is_seen_by_new_core_check_and_replay_is_idempotent(tmp_path):
    with QAHistory(tmp_path / "qa.sqlite3") as store:
        original = row(1, dialogue_design={"interaction_goal": "旧版本任务"})
        assert store.add(original, namespace="sft")
        assert store.duplicate(original["question"], namespace="sft", learner_task_dedup=True)["id"] == "1"
        assert store.add(original, namespace="sft", learner_task_dedup=True)
        assert not store.add(row(2), namespace="sft", learner_task_dedup=True)


def test_memory_keeps_all_history_counts_while_optional_examples_are_compacted(tmp_path):
    with QAHistory(tmp_path / "local.sqlite3") as local, QAHistory(tmp_path / "published.sqlite3") as published:
        for index in range(70):
            assert local.add(row(index, "问题 " + str(index), topic="主题 " + str(index),
                dialogue_design={"interaction_goal": "有用的实际业务目标。" * 100}), namespace="sft")
        assert published.add(row("prior", "历史发布任务"), namespace="sft")
        candidates = [{"source_context_pack": {"id": "immutable-pack"}, "teacher_evidence": "完整原文。" * 500}]
        before = deepcopy(candidates)
        memory = build_agent_memory(local, published, ["sft"], history=local.recent(limit=20),
                                    feedback={"duplicate_qa_contract": 8}, candidates=candidates)
        assert candidates == before
        assert len(canonical(memory).encode("utf-8")) <= MAX_MEMORY_BYTES
        assert memory["ledgers"]["current_run"]["sft"]["accepted_by_type"] == {"closed_book": 70}
        assert memory["ledgers"]["published"]["sft"]["accepted_by_type"] == {"closed_book": 1}
        assert memory["generation_feedback"]["duplicate_qa_contract"] == 8
        assert memory["immutable_source_windows"] == ["immutable-pack"]
        assert local.duplicate("问题 0", namespace="sft", learner_task_dedup=True) is not None
        while compact_agent_memory(memory):
            pass
        assert memory["ledgers"]["current_run"]["sft"]["accepted_by_type"] == {"closed_book": 70}
        assert local.coverage(namespace="sft") == {"closed_book": 70}
        assert local.duplicate("问题 0", namespace="sft", learner_task_dedup=True) is not None


def test_new_workflow_memory_uses_complete_counts_and_is_checkpointed(tmp_path):
    from test_workflow_qa_director_engine import execute, request_data
    state, path, _, _, writer, _ = execute(tmp_path, sample_count=3)
    assert state["status"] == "completed", state.get("error")
    plans = [request_data(call) for call in writer.calls if "candidates" in request_data(call)]
    assert plans[0]["agent_memory"]["ledgers"]["current_run"]["sft"]["accepted_by_type"] == {}
    assert plans[2]["agent_memory"]["ledgers"]["current_run"]["sft"]["accepted_by_type"] == {"closed_book": 2}
    assert plans[2]["agent_memory"]["history_is_not_evidence"] is True
    snapshots = list((path / "checkpoints" / "director").glob("*.json"))
    assert any('"agent_memory"' in snapshot.read_text(encoding="utf-8") for snapshot in snapshots)


def test_new_core_scope_keeps_multiturn_opening_and_sft_cot_reuse_valid(tmp_path):
    from test_workflow_qa_director_engine import make_run, Writer, Reviewer
    from lib.infrastructure import training_workflow as engine
    output, run_id, _ = make_run(tmp_path)
    workflow = engine.Workflow(output, run_id, tmp_path, generator=Writer(), jev=Reviewer())
    assert workflow.qa_core_dedup("sft")
    assert not workflow.qa_core_dedup("multiturn")
    # SFT/CoT derive the same candidate and immutable contract; register once.
    with QAHistory(output / "qa-history.sqlite3") as store:
        original = row(run_id + ":same")
        assert store.add(original, namespace="sft", learner_task_dedup=True)
        assert store.add(original, namespace="sft", learner_task_dedup=True)
        assert store.coverage(namespace="sft") == {"closed_book": 1}


def test_changed_hidden_goal_is_rejected_across_real_workflow_runs_before_answer_call(tmp_path):
    from test_workflow_qa_director_engine import execute, config, Writer, request_data
    class DesignWriter(Writer):
        def __init__(self, goal):
            super().__init__()
            self.goal = goal
        def chat(self, messages, **kwargs):
            output = super().chat(messages, **kwargs)
            if "candidates" in request_data(messages):
                value = json.loads(output)
                for task in value["tasks"]:
                    task.update(qa_type="closed_book", visible_context="", dialogue_design={"interaction_goal": self.goal})
                return json.dumps(value, ensure_ascii=False)
            return output
    options = config(planning_mode="adaptive")
    first, *_ = execute(tmp_path, options=options, writer=DesignWriter("设计设备检查流程"))
    assert first["status"] == "completed", first.get("error")
    second, path, output, _, writer, _ = execute(tmp_path, options=options, writer=DesignWriter("仅更换任务目标标签"))
    assert second["status"] == "needs_attention", second.get("error")
    assert len(writer.calls) == 1
    result = json.loads((path / "stage-results/sft.jsonl").read_text(encoding="utf-8"))
    assert result["reason"] == "duplicate_qa_contract"
    assert request_data(writer.calls[0])["agent_memory"]["ledgers"]["published"]["sft"]["accepted_by_type"] == {"closed_book": 1}
    with QAHistory(output / "qa-history.sqlite3") as history:
        assert history.coverage(namespace="sft") == {"closed_book": 1}


def test_resume_retains_exact_memory_snapshot_despite_new_global_history(tmp_path):
    from test_workflow_qa_director_engine import execute, Writer, Reviewer, request_data
    from lib.infrastructure import training_workflow as engine
    writer = Writer(fail_batch=1)
    state, _, output, run_id, *_ = execute(tmp_path, writer=writer, sample_count=3)
    assert state["status"] == "failed"
    old = next(request_data(call) for call in writer.calls
               if "candidates" in request_data(call) and request_data(call)["offset"] == 1)
    with QAHistory(output / "qa-history.sqlite3") as history:
        assert history.add(row("external", "完全不同的新业务任务"), namespace="sft", learner_task_dedup=True)
    writer.fail_batch = None
    resumed = engine.resume(output, run_id, tmp_path, generator=writer, jev=Reviewer())
    assert resumed["status"] == "completed", resumed.get("error")
    requests = [request_data(call) for call in writer.calls
                if "candidates" in request_data(call) and request_data(call)["offset"] == 1]
    assert len(requests) == 2
    assert requests[1]["agent_memory"] == old["agent_memory"]


def test_actual_input_hash_matches_sft_cot_and_excludes_private_reasoning():
    from lib.infrastructure.workflow_qa_director import actual_qa_input_identity
    prompt = [{"role": "user", "content": "An actual task."},
              {"role": "assistant", "content": "An earlier public answer.", "reasoning_content": "Private reasoning."},
              {"role": "user", "content": "A follow-up constraint."}]
    sft = {"messages": [*deepcopy(prompt), {"role": "assistant", "content": "A final answer."}]}
    cot = {"question": deepcopy(prompt), "answer": "A final answer.", "reasoning": ["Authored explanation."]}
    identity = actual_qa_input_identity(sft, "sft")
    assert identity == actual_qa_input_identity(cot, "cot")
    cot["question"][1]["reasoning_content"] = "Different private reasoning."
    assert identity == actual_qa_input_identity(cot, "cot")
    cot["question"][1]["content"] = "A different public answer."
    assert identity != actual_qa_input_identity(cot, "cot")


def test_actual_input_hash_retains_complete_article_and_public_tool_calls():
    from lib.infrastructure.workflow_qa_director import actual_qa_input_identity
    base = {"prompt": [{"role": "user", "content": "An article. " * 600 + "version one"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "function": {"name": "read", "arguments": '{"page":1}'}}]},
        {"role": "tool", "tool_call_id": "a", "content": "An actual observation."}]}
    changed = deepcopy(base)
    changed["prompt"][0]["content"] = changed["prompt"][0]["content"].replace("version one", "version two")
    assert actual_qa_input_identity(base, "dpo") != actual_qa_input_identity(changed, "dpo")
    changed = deepcopy(base)
    changed["prompt"][1]["tool_calls"][0]["function"]["arguments"] = '{"page":2}'
    assert actual_qa_input_identity(base, "dpo") != actual_qa_input_identity(changed, "dpo")


def test_actual_input_index_blocks_changed_contract_metadata_and_keeps_idempotent_derivations(tmp_path):
    identity = "a" * 64
    original = row(1, actual_input_sha256=identity)
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        assert history.add(original, namespace="sft")
        assert history.add(original, namespace="sft")
        assert not history.add(row(2, "A different planned task?", actual_input_sha256=identity), namespace="sft")
        assert history.duplicate_actual(identity, namespace="sft")["id"] == "1"
        assert history.duplicate_actual(identity, namespace="sft", exclude_id="1") is None
        assert history.coverage(namespace="sft") == {"closed_book": 1}
        with pytest.raises(ValueError, match="qa_history_sample_input_conflict"):
            history.add({**original, "actual_input_sha256": "b" * 64}, namespace="sft")


@pytest.mark.parametrize("identity", ["", "a" * 63, "a" * 65, "g" * 64, 123])
def test_actual_input_hash_is_strict_before_any_write(tmp_path, identity):
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        with pytest.raises(ValueError, match="qa_history_actual_input_hash_invalid"):
            history.add(row(1, actual_input_sha256=identity))
        assert history.coverage() == {}


def test_actual_input_lookup_does_not_guess_legacy_inputs(tmp_path):
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        assert history.add(row(1))
        assert history.duplicate_actual("a" * 64) is None
        assert history.add(row(1, actual_input_sha256="a" * 64))
        assert history.duplicate_actual("a" * 64)["id"] == "1"


def test_actual_input_registration_is_atomic_across_connections(tmp_path):
    path = tmp_path / "qa.sqlite3"
    with QAHistory(path):
        pass
    def accept(index):
        with QAHistory(path) as history:
            return history.add(row(index, "Different planned label " + str(index), actual_input_sha256="a" * 64),
                               namespace="sft", learner_task_dedup=True)
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(accept, range(16))).count(True) == 1


def test_actual_human_revision_is_registered_independently_of_stale_planned_core(tmp_path):
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        assert history.add(row(1, actual_input_sha256="a" * 64), namespace="sft", learner_task_dedup=True)
        revised = row(2, actual_input_sha256="b" * 64, actual_input_revision=True,
                      actual_question="An explicitly revised delivered question?")
        assert history.add(revised, namespace="sft", learner_task_dedup=True)
        assert history.duplicate_actual("b" * 64, namespace="sft")["question"] == revised["actual_question"]
        assert not history.add(row(3, "Different hidden design", actual_input_sha256="b" * 64), namespace="sft")


def test_actual_alien_duplicate_cannot_hide_behind_selected_parent_contract(tmp_path):
    from lib.infrastructure.workflow_qa_director import WorkflowQADirector, actual_qa_input_identity
    class Workflow(WorkflowQADirector):
        recipe = {"qa_agent_memory_version": 1}
        state = {"id": "child"}
        def human_revision_duplicate_allowed(self, contract, duplicate):
            return duplicate["id"] == "parent"
    workflow = Workflow()
    candidate = {"id": "edited", "status": "eligible", "family_id": "family", "messages": [
        {"role": "user", "content": "An unrelated published input?"}, {"role": "assistant", "content": "An answer."}],
        "qa_contract": {"question": row(1)["question"], "visible_context": "", "qa_type": "closed_book", "answer_policy": "answer"}}
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        assert history.add(row("parent", run_id="ancestor"), namespace="sft")
        assert history.add(row("alien", "Some different planned metadata", run_id="alien-run",
            actual_input_sha256=actual_qa_input_identity(candidate, "sft")), namespace="sft")
        assert workflow.final_qa_duplicate(candidate, "sft", history)["id"] == "alien"
        projected = workflow.qa_history_row(candidate, "sft")
        assert projected["actual_input_revision"] is True
        assert projected["actual_question"] == candidate["messages"][0]["content"]


def test_long_actual_revision_summary_never_becomes_its_complete_input_identity(tmp_path):
    from lib.infrastructure.workflow_qa_director import WorkflowQADirector
    class Workflow(WorkflowQADirector):
        recipe = {"qa_agent_memory_version": 1}
        state = {"id": "human-run"}
    workflow = Workflow()
    contract = {"question": "The original planned task?", "visible_context": "",
                "qa_type": "closed_book", "answer_policy": "answer"}
    def projected(index, center):
        question = "A public article. " * 2200 + center + " End of public article." * 2200
        return workflow.qa_history_row({"id": str(index), "family_id": "family", "status": "eligible",
            "qa_contract": contract, "messages": [{"role": "user", "content": question},
            {"role": "assistant", "content": "An answer for the explicit revision."}]}, "sft")
    one, two = projected(1, "version one"), projected(2, "version two")
    assert one["actual_question"] == two["actual_question"]
    assert "History excerpt only" in one["actual_question"] and len(one["actual_question"]) <= 32768
    assert one["actual_input_sha256"] != two["actual_input_sha256"]
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        assert history.add(one, namespace="sft", learner_task_dedup=True)
        assert history.add(two, namespace="sft", learner_task_dedup=True)
        assert history.duplicate_actual(two["actual_input_sha256"], namespace="sft")["id"] == two["id"]


def test_actual_input_lookup_uses_exact_primary_key_index(tmp_path):
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        assert history.add(row(1, actual_input_sha256="a" * 64), namespace="sft")
        plan = history.connection.execute("EXPLAIN QUERY PLAN SELECT h.* FROM qa_history_actual a "
            "JOIN qa_history h ON h.row_id=a.row_id WHERE a.namespace=? AND a.actual_input_sha256=?",
            ("sft", "a" * 64)).fetchall()
        assert any("SEARCH a USING PRIMARY KEY" in step[3] for step in plan)


def test_multiturn_same_opening_and_goal_can_have_distinct_actual_followups(tmp_path):
    from lib.infrastructure.workflow_qa_director import WorkflowQADirector
    class Workflow(WorkflowQADirector):
        recipe = {"qa_agent_memory_version": 1, "qa_director": {"planning_mode": "adaptive"}}
        state = {"id": "run"}
    workflow = Workflow()
    contract = {"question": "Help plan the inspection.", "visible_context": "", "qa_type": "closed_book",
                "answer_policy": "answer", "dialogue_design": {"interaction_goal": "Complete the inspection plan"}}
    def delivered(index, followup):
        return {"id": str(index), "status": "eligible", "family_id": "family", "qa_contract": contract,
                "messages": [{"role": "user", "content": contract["question"]},
                             {"role": "assistant", "content": "First isolate power."},
                             {"role": "user", "content": followup},
                             {"role": "assistant", "content": "Follow the specified additional constraint."}]}
    with QAHistory(tmp_path / "qa.sqlite3") as history:
        first = workflow.qa_history_row(delivered(1, "The inlet is still open."), "multiturn")
        second = workflow.qa_history_row(delivered(2, "The outlet pressure is high."), "multiturn")
        assert first["actual_input_only_identity"] and second["actual_input_only_identity"]
        assert history.add(first, namespace="multiturn")
        assert history.add(second, namespace="multiturn")
        repeated = workflow.qa_history_row(delivered(3, "The inlet is still open."), "multiturn")
        assert not history.add(repeated, namespace="multiturn")
        assert history.add(first, namespace="multiturn")
        assert history.coverage(namespace="multiturn") == {"closed_book": 2}


def test_multiturn_dispatch_waits_for_complete_actual_dialogue_identity(tmp_path):
    from io import StringIO
    from lib.infrastructure.workflow_qa_director import WorkflowQADirector
    class Workflow(WorkflowQADirector):
        recipe = {"qa_agent_memory_version": 1, "concurrency": 2}
        state = {"id": "run", "stages": {"multiturn": {key: 0 for key in
                 ("done", "outputs", "eligible", "quarantined", "cached", "batches_done")}}}
        path = tmp_path
        def activate_directed_stage(self, stage): pass
        def check_cancel(self): pass
        def event(self, *args, **kwargs): pass
        def save(self): pass
        def checkpoint(self, key, operation): return operation()
        def multiturn(self, unit):
            return [{**unit, "status": "eligible", "messages": [
                {"role": "user", "content": unit["qa_contract"]["question"]},
                {"role": "assistant", "content": "An initial response."},
                {"role": "user", "content": "A distinct condition " + unit["id"]},
                {"role": "assistant", "content": "A final response."}]}]
    from threading import Event
    workflow = Workflow()
    workflow._abort = Event()
    workflow.state["qa_director"] = {"coverage": {"closed_book": {"accepted": 0, "rejected": 0}}, "feedback": {}}
    contract = {"question": "Help plan the inspection.", "visible_context": "", "qa_type": "closed_book", "answer_policy": "answer"}
    units = [{"id": str(index), "source_id": "source", "family_id": "family", "qa_contract": contract} for index in range(2)]
    with QAHistory(tmp_path / "local.sqlite3") as local, QAHistory(tmp_path / "published.sqlite3") as published:
        output = StringIO()
        workflow._directed_worker_batch("multiturn", units, 0, output, local, published)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        assert [item["status"] for item in records] == ["eligible", "eligible"]
        assert local.coverage(namespace="multiturn") == {"closed_book": 2}


def test_pending_publication_isolates_batch_collision_before_export_without_pretending_published(tmp_path):
    from lib.infrastructure.workflow_qa_director import WorkflowQADirector
    class Workflow(WorkflowQADirector):
        recipe = {"qa_agent_memory_version": 1, "qa_director": {"enabled": True}}
        state = {"id": "run"}
        path = tmp_path / "runs" / "run"
        def check_cancel(self): pass
    workflow = Workflow()
    workflow.path.mkdir(parents=True)
    def revised(index):
        return {"id": str(index), "family_id": "family", "status": "eligible", "qa_contract": {
            "question": "A different original task " + str(index), "visible_context": "", "qa_type": "closed_book", "answer_policy": "answer"},
            "messages": [{"role": "user", "content": "The same explicitly revised actual task."},
                         {"role": "assistant", "content": "Different answer " + str(index)}]}
    one, two = revised(1), revised(2)
    with workflow.qa_publication_guard() as published:
        assert workflow.final_qa_duplicate(one, "sft", published) is None
        workflow.register_final_qa_input(one, "sft")
        duplicate = workflow.final_qa_duplicate(two, "sft", published)
        assert duplicate["id"] == "run:1" and duplicate["duplicate_scope"] == "current_publication"
        assert published.coverage(namespace="sft") == {}
        workflow.register_final_qa_input(one, "sft")  # Same candidate/format replay is valid.
    assert not list((workflow.path / "stage-results").glob(".qa-publication-*.sqlite3"))


def test_failed_global_registration_cannot_leave_a_complete_manifest(tmp_path, monkeypatch):
    from test_workflow_qa_director_engine import execute
    from lib.infrastructure import training_workflow as engine
    def conflict(self, destination):
        raise ValueError("qa_publication_history_conflict")
    monkeypatch.setattr(engine.Workflow, "publish_qa_history", conflict)
    state, path, *_ = execute(tmp_path)
    assert state["status"] == "failed" and state["error"] == "qa_publication_history_conflict"
    assert not (path / "artifacts" / "manifest.json").exists()


@pytest.mark.parametrize("kind", ["brief", "human_provided"])
def test_long_unpacked_followup_retains_tail_fact_in_one_director_call(kind):
    from lib.infrastructure.workflow_qa_director import WorkflowQADirector
    text = "A complete known premise. " * 1200 + "TAIL_FACT: pressure must stay below 15 kPa."
    class Workflow(WorkflowQADirector):
        recipe = {"qa_agent_memory_version": 1, "qa_director": {"question_rules": "Design useful turns.",
                  "answer_rules": "Keep all applicable conditions."}, "conversation_turns": 5}
        calls = []
        def ask(self, key, role, prompt_id, data, **kwargs):
            self.calls.append(deepcopy(data))
            return {"continue": True, "user_message": "What operating limit applies?", "reason": "Confirm a remaining condition.",
                    "dialogue_state": {"progress": "Initial preparation was discussed."}}
    workflow = Workflow()
    workflow.calls = []
    unit = {"id": "manual", "kind": kind, "text": text, "qa_contract": {"qa_type": "closed_book"}}
    dialogue = [{"role": "user", "content": "Plan the procedure."}, {"role": "assistant", "content": "First prepare the equipment."}]
    step = workflow.next_dialogue_step(unit, dialogue, {})
    assert step["continue"] and len(workflow.calls) == 1
    assert workflow.calls[0]["teacher_evidence"] == text
    assert workflow.calls[0]["teacher_evidence"].endswith("pressure must stay below 15 kPa.")
    # Historical snapshots retain their pinned bounded transport behavior.
    workflow.recipe = {**workflow.recipe, "qa_agent_memory_version": 0}
    workflow.next_dialogue_step(unit, dialogue, {})
    assert workflow.calls[-1]["teacher_evidence"] == text[:20000]


def test_oversized_full_unpacked_source_fails_preflight_before_api_request():
    import httpx
    from openai import OpenAI
    from lib.llm_client import ChatClient
    calls = []
    def no_request(request):
        calls.append(request)
        raise AssertionError("An oversized source must never reach the model API.")
    client = ChatClient("https://offline.invalid/v1", "offline-test-key", "offline-writer", context_window_tokens=8192)
    client.client.close()
    client.client = OpenAI(base_url="https://offline.invalid/v1", api_key="offline-test-key",
                           http_client=httpx.Client(transport=httpx.MockTransport(no_request)))
    text = "An unchanged source fact. " * 1200 + "TAIL_FACT: pressure must stay below 15 kPa."
    try:
        with pytest.raises(ValueError, match="model_context_window_exceeded"):
            client.chat([{"role": "system", "content": "Use all the supplied premises."},
                         {"role": "user", "content": canonical({"teacher_evidence": text})}], max_tokens=1024)
        assert not calls and client.usage["calls"] == 0
    finally:
        client.client.close()
