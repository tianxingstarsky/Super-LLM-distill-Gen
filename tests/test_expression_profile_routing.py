"""Actual profile transport, natural stopping and immutable old context rules."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from lib.domain.dialogue_expression import carry_expression_design, natural_expression_guide
from lib.domain.workflow_quality import canonical
from lib.infrastructure.dialogue_role_messages import question_role_messages
from lib.prompts import base


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


PROFILE = {"participant_voice": "直接与助手交流，不替第三人称出题。", "register": "简洁日常口语",
           "syntax": "允许省略主语与自我修正", "noise": "可不使用噪声",
           "form": "简短请求", "clarity": "有未知条件", "verbosity": "很短"}


def test_numeric_prompt_latest_does_not_rewrite_an_exact_version_pin(monkeypatch):
    old = base.PromptSpec("test.prompt", "1.9.0", "old", "test", "old immutable")
    new = base.PromptSpec("test.prompt", "1.10.0", "new", "test", "new immutable")
    monkeypatch.setattr(base, "registry_versions", lambda: {old.id: {old.version: old, new.version: new}})
    assert base.get(old.id) is new and base.registry()[old.id] is new
    assert base.get(old.id, "1.9.0") is old


def test_legacy_guide_and_new_source_free_guide_are_version_pinned():
    assert sha256(canonical(natural_expression_guide()).encode()).hexdigest() == (
        "ffdfa8666177e78b6a331fe6b94b76a0f551658e7a5e31f55b2a33c5ebc28216")
    brief = {"topic": "普通求助", "communication_goal": "表达当前困惑", "expression_design": PROFILE}
    data = {"question_brief": brief, "natural_expression_guidance": natural_expression_guide(2)}
    with pytest.raises(ValueError, match="private_context_in_question_request"):
        question_role_messages("system", data)
    actual = question_role_messages("system", data, expression_guide_version=2)
    assert "participant_voice" in actual[0]["content"]
    assert not any(message.get("name") == "source_memory" for message in actual)
    mutated = {**data, "natural_expression_guidance": {**data["natural_expression_guidance"], "source_fact": "hidden"}}
    with pytest.raises(ValueError, match="private_context_in_question_request"):
        question_role_messages("system", mutated, expression_guide_version=2)


def test_only_compatible_speaker_habits_carry_to_another_turn():
    copied = deepcopy(PROFILE)
    next_turn = carry_expression_design(PROFILE, {"form": "修改请求", "verbosity": "说明新增条件"})
    assert next_turn["register"] == PROFILE["register"]
    assert next_turn["form"] == "修改请求" and next_turn["verbosity"] == "说明新增条件"
    assert "clarity" not in next_turn and PROFILE == copied


def test_top_level_design_reaches_the_real_blind_actor_without_private_evidence(tmp_path):
    from tests.test_directed_answer_contracts import fit_workflow, question_dispatch
    workflow, aware, actor, _, unit = fit_workflow(tmp_path, source=True)
    unit["qa_contract"]["expression_design"] = deepcopy(PROFILE)
    before = deepcopy(unit)
    result = workflow._closed_book_question(unit, unit["qa_contract"], key=["expression-profile"])
    assert question_dispatch(actor.calls[0])["question_brief"]["expression_design"] == PROFILE
    assert result["expression_design"] == PROFILE and unit == before
    assert len(actor.calls) == len(aware.fit_requests) == 1
    assert "UNSELECTED_PRIVATE_V2" not in json.dumps(actor.calls, ensure_ascii=False)


def test_multiturn_keeps_speaker_habits_and_transmits_the_current_turn_design(tmp_path):
    from tests.test_separated_dialogue_generation import DirectorResponder, make_run, records, tool_payload
    class ProfileDirector(DirectorResponder):
        def chat(self, messages, **options):
            reply = json.loads(super().chat(messages, **options))
            if "tasks" in reply:
                for task in reply["tasks"]:
                    task["expression_design"] = deepcopy(PROFILE)
            elif reply.get("continue") is True:
                reply["dialogue_state"]["expression_design"] = {
                    "form": "回应已有结果的修改请求", "verbosity": "说明需要修改的部分"}
            return json.dumps(reply, ensure_ascii=False)
    director = ProfileDirector()
    worker, _, actor, _ = make_run(tmp_path, writer=director, targets=["multiturn"], conversation_turns=4)
    state = worker.execute()
    assert state["status"] == "completed", state.get("error")
    initial = tool_payload(actor.calls[0], "dialogue_dispatch")["question_brief"]["expression_design"]
    followup = tool_payload(actor.calls[1], "dialogue_dispatch")["question_brief"]["expression_design"]
    assert initial == PROFILE
    assert followup["register"] == PROFILE["register"]
    assert followup["form"] == "回应已有结果的修改请求"
    assert followup["verbosity"] == "说明需要修改的部分" and "clarity" not in followup
    assert len(actor.calls) == 2
    row, = records(worker.path, "multiturn")
    trainer = (worker.path / "artifacts/trl_multiturn.jsonl").read_text(encoding="utf-8")
    assert "expression_design" not in trainer and PROFILE["syntax"] not in trainer
    assert row["dialogue_steps"][1]["dialogue_state"]["expression_design"] == followup


def test_persistent_expression_window_is_bounded_without_erasing_ledger_entries(tmp_path):
    from tests.test_workflow_knowledge_agent import Worker, Client
    from lib.domain.dialogue_expression import EXPRESSION_FIELDS_V2
    worker = Worker(tmp_path, Client([]))
    worker.recipe["qa_expression_version"] = 2
    for index in range(15):
        worker.knowledge_store().claim("knowledge", str(index), run_id="saved", sample_id=str(index),
            question="问题" + str(index), qa_type="closed_book",
            expression_design={field: field + " " + str(index) + "x" * 210 for field in EXPRESSION_FIELDS_V2})
        worker.knowledge_store().outcome("knowledge", str(index), run_id="saved", sample_id=str(index),
            stage="sft", status="final_eligible")
    bounded = worker._knowledge_memory("knowledge")
    assert len(canonical(bounded).encode()) <= 8192 and bounded["expression_coverage_limited"]
    full = worker.knowledge_store().coverage("knowledge", include_expression=True)
    assert full["counts"] == [{"qa_type": "closed_book", "status": "final_eligible", "count": 15}]
    assert sum(map(len, full["accepted_dimensions"].values())) > sum(map(len, bounded["accepted_dimensions"].values()))
