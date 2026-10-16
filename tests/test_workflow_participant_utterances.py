"""Offline expression contracts, unchanged schemas, and real role routing.

These tests prove the instructions reach the intended production calls, not
that a fixed fake response establishes natural-language quality in a model.
"""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from lib.domain.workflow_qa_director import validate_director_task
from lib.infrastructure import training_workflow as engine
from lib.prompts import get, render
from lib.prompts.workflow_utterance import (
    PARTICIPANT_UTTERANCE_GUIDANCE, PARTICIPANT_UTTERANCE_REVIEW,
    QA_DIRECTOR_V1_10, QA_DIRECTOR_V1_11, QUESTION_USER_V1_3, MULTITURN_USER_V1_5, SFT_V1_7,
    QUESTION_FIT_V1_4, SFT_DIRECTED_CHECK_V1_3, MULTITURN_DIRECTED_CHECK_V1_3,
)
from tests.test_adaptive_dialogue_director import Expert, make_run
from tests.test_directed_answer_contracts import fit_workflow, prepare, question_dispatch
from tests.test_multiturn_natural_stop import create, Writer as OrdinaryWriter, Judge as OrdinaryJudge
from tests.test_workflow_qa_director_engine import request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


@pytest.mark.parametrize("name,version,body,shared", [
    ("qa_director", "1.10.0", QA_DIRECTOR_V1_10, PARTICIPANT_UTTERANCE_GUIDANCE),
    ("question_user", "1.3.0", QUESTION_USER_V1_3, PARTICIPANT_UTTERANCE_GUIDANCE),
    ("multiturn_user", "1.5.0", MULTITURN_USER_V1_5, PARTICIPANT_UTTERANCE_GUIDANCE),
    ("sft", "1.7.0", SFT_V1_7, PARTICIPANT_UTTERANCE_GUIDANCE),
    ("question_fit", "1.4.0", QUESTION_FIT_V1_4, PARTICIPANT_UTTERANCE_REVIEW),
    ("sft_directed_check", "1.3.0", SFT_DIRECTED_CHECK_V1_3, PARTICIPANT_UTTERANCE_REVIEW),
    ("multiturn_directed_check", "1.3.0", MULTITURN_DIRECTED_CHECK_V1_3, PARTICIPANT_UTTERANCE_REVIEW),
])
def test_versioned_complete_contract_has_shared_guidance_once(name, version, body, shared):
    prompt = get("workflow." + name, version=version)
    assert prompt.version == version
    assert render(prompt) == body and body.count(shared) == 1


def test_latest_director_retains_participant_contract_with_adaptive_multiturn_design():
    prompt = get("workflow.qa_director")
    assert prompt.version == "1.11.0"
    assert render(prompt) == QA_DIRECTOR_V1_11
    assert render(prompt).count(PARTICIPANT_UTTERANCE_GUIDANCE) == 1


@pytest.mark.parametrize("name,version,expected", [
    ("qa_director", "1.9.0", "588cfd72c3cb0c6b3edec2bd2f23431439d52a0274c9dce92178e82ec349cb55"),
    ("question_user", "1.2.0", "f4ae8482036fdad400a04d5bf7c1594304a3e8630e63c7d9a1321b2a7c432544"),
    ("multiturn_user", "1.4.0", "ca97c4d2cfcc7cde86a904a61710bb6605e0edbc49ce3b1a528de712ca28bab8"),
    ("sft", "1.6.0", "aa0ca3c3f5f40dfc81d57c53592d16d5e7f93110d889ead636888d4cc96957ce"),
    ("question_fit", "1.3.0", "447369505fdb2de50ce22da01d722f81696a8f6663744fb52d1fcdf4e31525ef"),
    ("sft_directed_check", "1.2.0", "135a0e9501b7a6348a3c2cc173c1780580b2efff0abf691f84479b18207b5c8e"),
    ("multiturn_directed_check", "1.2.0", "24505474ef4d1c3945a42fc2b7a4d86382447f1968c823020b822b5e3435c0aa"),
])
def test_previous_renderable_templates_remain_byte_identical(name, version, expected):
    prompt = get("workflow." + name, version=version)
    assert sha256(prompt.template.encode()).hexdigest() == expected
    assert PARTICIPANT_UTTERANCE_GUIDANCE not in render(prompt)
    assert PARTICIPANT_UTTERANCE_REVIEW not in render(prompt)


def test_generation_and_review_allow_real_variation_without_forcing_personal_claims_or_noise():
    for required in ("不强制出现“我”或“I”", "generated_interaction", "固定类别、随机轮换、每批配额",
                     "不强造噪声", "来源作者的实验行为嫁接给用户", "引用文章已有编号",
                     "participant_voice、register、verbosity、syntax、noise、interaction_style"):
        assert required in PARTICIPANT_UTTERANCE_GUIDANCE
    assert "固定用户原文不可为提高自然度改写" in PARTICIPANT_UTTERANCE_REVIEW
    assert "不要求口语、错字、含混或噪声" in PARTICIPANT_UTTERANCE_REVIEW
    assert "不能因未附教师资料就拒绝可回答的知识任务" in SFT_DIRECTED_CHECK_V1_3
    assert "source_line_scope=selected时不能借未选行或整包补证" in MULTITURN_DIRECTED_CHECK_V1_3
    assert "为了多轮结构，completed_turns<2时须提供有作用的第二轮" not in QA_DIRECTOR_V1_10
    assert "已有一轮也可停止" in QA_DIRECTOR_V1_10
    assert "expression_design的可选开放文本字段为form、clarity、context_dependency、addressing、roleplay_basis、participant_voice" in QA_DIRECTOR_V1_10
    assert "dialogue_state还可包含expression_design表达建议" in QA_DIRECTOR_V1_10


@pytest.mark.parametrize("message,policy", [
    ("这咋办", "clarify"),
    ("维护前要断电吗？", "answer"),
    ("帮同事把这段提醒改短些。", "answer"),
    ("保留原文的第2条。\n要求：\n1. 不改数字\n2. 控制在两行内", "answer"),
    ('这个配置能保留吗：{"mode":"safe"}', "clarify"),
])
def test_actual_task_validator_preserves_short_ambiguous_third_party_lists_and_literal_json(message, policy):
    original = {"id": "candidate", "qa_type": "closed_book", "question": message,
        "visible_context": "", "answer_policy": policy, "guidance": "按实际沟通目标回应。", "evidence_quotes": []}
    before = deepcopy(original)
    checked = validate_director_task(original, expected_type=None, expected_id="candidate")
    assert checked["question"] == message and checked["answer_policy"] == policy
    assert original == before


class ObservedExpert(Expert):
    def chat(self, messages, **kwargs):
        assert PARTICIPANT_UTTERANCE_GUIDANCE in messages[0]["content"]
        return super().chat(messages, **kwargs)


def test_grounded_planning_and_next_turn_share_protocol_and_existing_review_call(tmp_path, monkeypatch):
    output, run_id, path, expert, worker, reviewer, _ = make_run(tmp_path, monkeypatch, ObservedExpert())
    recipe = engine.read_json(path / "recipe.json")
    assert recipe["prompts"]["workflow.qa_director"]["version"] == "1.11.0"
    before = deepcopy(recipe["node_prompt_templates"])
    state = engine.Workflow(output, run_id, tmp_path, jev=reviewer).execute()
    assert state["status"] == "completed", state.get("error")
    phases = [request_data(messages).get("request_phase") for messages in expert.calls]
    assert "batch_design" in phases and "next_turn" in phases
    checks = [messages for messages in reviewer.calls if PARTICIPANT_UTTERANCE_REVIEW in messages[0]["content"]]
    assert checks and all('"adherence"' in messages[0]["content"] for messages in checks)
    assert len(worker.calls) == 2
    assert engine.read_json(path / "recipe.json")["node_prompt_templates"] == before


def test_independent_question_and_existing_fit_keep_private_evidence_out_of_public_actor(tmp_path):
    workflow, aware, questioner, _, unit = fit_workflow(tmp_path, source=True)
    contract = workflow._closed_book_question(unit, unit["qa_contract"], key=["participant-utterance"])
    assert contract["question"] == "又不行了" and contract["answer_policy"] == "clarify"
    assert len(questioner.calls) == len(aware.fit_requests) == 1
    assert PARTICIPANT_UTTERANCE_GUIDANCE in questioner.calls[0][0]["content"]
    fit_calls = [call for call in aware.calls if PARTICIPANT_UTTERANCE_REVIEW in call[0]["content"]]
    assert len(fit_calls) == 1
    blind = json.dumps(questioner.calls, ensure_ascii=False)
    assert "UNSELECTED_PRIVATE_V2" not in blind and "source_line_ranges" not in blind
    assert "answer_policy" not in question_dispatch(questioner.calls[0])


def test_ordinary_multiturn_actor_receives_same_protocol_without_extra_generation(tmp_path):
    output, run_id, path = create(tmp_path, turns=6)
    writer, judge = OrdinaryWriter(), OrdinaryJudge()
    state = engine.Workflow(output, run_id, tmp_path, generator=writer, jev=judge).execute()
    assert state["status"] == "completed", state.get("error")
    calls = [call for call in writer.calls if "previous_messages" in request_data(call)]
    assert len(calls) == 3
    assert all(PARTICIPANT_UTTERANCE_GUIDANCE in call[0]["content"] for call in calls)
    assert len(writer.answer_calls) == 2


def test_sft_new_task_contract_is_pinned_while_fixed_input_remains_literal(tmp_path):
    output = tmp_path / "ordinary"
    run_id = engine.create_run(output, brief="设计一条有用的自然互动。", targets=["sft"])
    ordinary = engine.Workflow(output, run_id, tmp_path)
    assert ordinary.recipe["prompts"]["workflow.sft"]["version"] == "1.7.0"
    assert ordinary.prompt_text("workflow.sft", stage="sft") == SFT_V1_7
    workflow, writer, _, unit = prepare(tmp_path, [{"answer": "你说的是哪件事？", "quotes": []}],
                                         separated=True, policy="clarify")
    row, = workflow.directed_sft(unit)
    assert row["messages"][0] == {"role": "user", "content": "又不行了"}
    assert len(writer.calls) == 1
    assert "以下参与者表达协议仅用于本节点创建新用户任务" in SFT_V1_7
