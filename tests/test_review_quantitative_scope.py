"""Review instructions and bound requests cover quantities and inference scope.

The semantic verdicts are adversarial offline fixtures, not evidence that an
LLM now detects every arithmetic or causal error. No extra review call is added.
"""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from lib.domain.turn_review import supervised_assistant_turns
from lib.domain.workflow_quality import accepted
from lib.infrastructure import training_workflow as engine
from lib.prompts import get, render
from lib.prompts.workflow_fact_review import REVIEW_SCORE_V1_8, REVIEW_SCORE_V1_9
from tests.test_turn_review_and_reasoning_repair import reviewed
from tests.test_workflow_learning_integration import prepare
from tests.test_workflow_styled_generation import model_task_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Injected Python response objects only; no external model request."""
    yield


def test_review_1_9_retains_the_literal_1_8_body_and_existing_output_contract():
    current = get("workflow.review_score", version="1.9.0")
    frozen = get("workflow.review_score", version="1.8.0")
    assert current.version == "1.9.0"
    assert sha256(frozen.template.encode()).hexdigest() == "6fb8e603e0ce86c6c2f57a0238a168608abb39eeca5ec910c918107de96e23e6"
    assert render(frozen) == REVIEW_SCORE_V1_8
    assert render(current) == REVIEW_SCORE_V1_9 and REVIEW_SCORE_V1_9.startswith(REVIEW_SCORE_V1_8)
    assert current.variables == frozen.variables == ()
    assert "输出仍严格使用既定六字段及按需turn_verdicts" in render(current)
    assert "不新增核验表、JSON字段、评分维度或额外模型请求" in render(current)


def test_new_rules_require_general_calculation_without_encoding_one_papers_answers():
    new_rules = REVIEW_SCORE_V1_9[len(REVIEW_SCORE_V1_8):]
    for required in ("在本次评审中独立重算", "两个百分数相减得到的是百分点差", "以变化量除以原基准",
                     "实例级比例静默改称指令级比例", "比例确实超过50%", "相对多数，不自动构成绝对多数",
                     "有限分类标签只限制该任务的类别选择", "不自动限制输入文本、表达方式、情境组合",
                     "一次实验观察到的产量", "因果贡献", "区分方法建议与既有实证"):
        assert required in new_rules
    assert all(value not in new_rules for value in ("33.1", "35,878", "82,439", "SELF-INSTRUCT"))


def test_rules_keep_valid_bounds_qualified_suggestions_and_rounding_context():
    new_rules = REVIEW_SCORE_V1_9[len(REVIEW_SCORE_V1_8):]
    assert "明确给定这些约束的可证明上限仍可正常使用" in new_rules
    assert "不要求每条正常的设计建议都冒充实证" in new_rules
    assert "近似数、有效数字及四舍五入误差可合理保留" in new_rules
    assert "不按数字或措辞关键词禁用" in new_rules
    assert "后续纠正、总体高分或平均分" in new_rules


CASES = [
    ("The benchmark reports a baseline score of 40% and a new score of 52% on the same scale.",
     "The score improved by 12% relative to the baseline.",
     "Subtracting 40 from 52 gives 12, so the relative gain is 12%.",
     "The score rose by 12 percentage points, which is a 30% relative increase.",
     "52 minus 40 is 12 percentage points; 12 divided by the baseline 40 is 0.30.",
     "assistant[1]把12个百分点写成12%相对提升；相对率应以差值除以基准。"),
    ("There are 50 instructions and 100 instances; 36 instances have a nonempty auxiliary input. Per-instruction counts were not measured.",
     "36% of the instructions have input, so a majority of instructions have input.",
     "36 of 100 records have input, which establishes that most instructions have it.",
     "36% of instances have a nonempty auxiliary input. This is below half and does not determine the instruction-level fraction.",
     "The denominator is 100 instances, not 50 instructions; the distribution across instructions is unavailable.",
     "assistant[1]混同实例和指令分母，且36%不足以支持过半。"),
    ("A classification task has two permitted output labels. The specification places no bound on valid input texts or contexts.",
     "There can be at most two distinct useful input-output examples because there are only two labels.",
     "Two label values mean that only two different training pairs can exist.",
     "Two labels bound the output categories, but many distinct inputs may map to either label; no example-count bound follows.",
     "No finite input space or per-label instance limit was specified, so category count is not an instance-capacity bound.",
     "assistant[1]从有限标签数量推出实例产量上限，缺少有限输入空间等必要前提。"),
    ("A single run produced 80 items after filtering. No exhaustion test, control comparison, or ablation was performed.",
     "Filtering caused the improvement, and 80 items is the generator's permanent capacity limit.",
     "The single observed output count establishes both the causal benefit and an upper bound.",
     "The run produced 80 items; causal benefit and a capacity ceiling remain untested. A control or ablation is a possible next experiment.",
     "An observed count does not establish a maximum or causal effect without the needed constraints and comparisons.",
     "assistant[1]将一次观测扩为因果结论和永久上限；后续实验建议不能冒充已验证结果。"),
]


@pytest.mark.parametrize("evidence,bad,bad_reason,correct,correct_reason,why", CASES,
                         ids=["percentage-points", "denominator-majority", "labels-vs-inputs", "observation-vs-cause-cap"])
def test_new_pinned_review_receives_full_evidence_and_both_turns_without_an_extra_node(
        tmp_path, monkeypatch, evidence, bad, bad_reason, correct, correct_reason, why):
    worker, client, _ = prepare(tmp_path, review=True)
    # Replay the named 1.9 release's aggregate-plus-turn contract. Current
    # condition/claim protocols are exercised by their separate workflow tests.
    worker.recipe.pop("claim_review_protocol_version", None)
    worker.recipe["turn_review_protocol_version"] = 1
    released = get("workflow.review_score", "1.9.0")
    worker.recipe["prompts"]["workflow.review_score"] = {"version": "1.9.0", "sha256": engine.digest(released.template)}
    worker.recipe["node_prompt_templates"]["review"]["workflow.review_score"] = REVIEW_SCORE_V1_9
    engine.atomic_json(worker.path / "recipe.json", worker.recipe)
    worker.state["recipe_hash"] = engine.digest(worker.recipe)
    engine.atomic_json(worker.path / "state.json", worker.state)
    worker.stage = "review"
    candidate = {"messages": [{"role": "user", "content": "Check the conclusion and keep the scope of the evidence clear."},
        {"role": "assistant", "content": bad, "reasoning_content": bad_reason},
        {"role": "user", "content": "Please correct any quantitative or inferential overstatement."},
        {"role": "assistant", "content": correct, "reasoning_content": correct_reason}]}
    row = {"id": "quantitative-sample", "source_id": "ledger", "status": "eligible", **candidate}
    source = {"teacher_evidence": evidence, "source_kind": "document", "source_id": "ledger"}
    frozen = deepcopy(row)
    calls = []

    def assess(messages, **kwargs):
        data = model_task_data(messages)
        calls.append(deepcopy(data))
        assert REVIEW_SCORE_V1_9 in messages[0]["content"]
        assert evidence in json.dumps(messages, ensure_ascii=False)
        assert data["candidate"] == candidate
        assert data["supervised_assistant_turns"] == supervised_assistant_turns(candidate)
        verdict = reviewed(candidate, first_bad=True)
        verdict["turn_verdicts"][0]["reason"] = why
        return json.dumps(verdict, ensure_ascii=False)

    monkeypatch.setattr(client, "chat", assess)
    result = worker._review_score("multiturn", row, source, ["quantitative-sample"], "")
    assert len(calls) == 1
    assert worker.recipe["prompts"]["workflow.review_score"]["version"] == "1.9.0"
    assert worker.recipe["node_prompt_templates"]["review"]["workflow.review_score"] == REVIEW_SCORE_V1_9
    assert not accepted(result) and not result["keep"]
    assert result["turn_verdicts"][0]["critical_error"] and result["turn_verdicts"][-1]["keep"]
    assert result["aggregate_original"]["correctness"] == 5
    assert row["messages"] == frozen["messages"]  # Review cannot silently edit the old answer or reasoning.
