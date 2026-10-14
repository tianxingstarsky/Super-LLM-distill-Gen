"""New phase contracts retain old prompt bodies and their JSON schemas."""
from dataclasses import replace
from hashlib import sha256
import json

import pytest

from lib.prompts.base import render
from lib.prompts import get
from lib.prompts.workflow import COT_RATIONALE_V1, COT_NATIVE_ANSWER_V1
from lib.prompts.workflow_prefill import (
    RATIONAL_ONLY_BODY, NATIVE_ANSWER_BODY, PREFIX_CHECK_BODY,
    RATIONAL_ONLY_BODY_V1_2, NATIVE_ANSWER_BODY_V1_2, PREFIX_CHECK_BODY_V1_2,
)


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


@pytest.mark.parametrize("body,expected_sha", [
    (RATIONAL_ONLY_BODY, "f38bc8f85533070949e514db7539a25cf31db5e4301ac060f96e35a091cbc995"),
    (NATIVE_ANSWER_BODY, "a0f272ebd493334bcb505de9632af77bc79c97eda6ccbf095b2cd08c1fd6817d"),
    (PREFIX_CHECK_BODY, "bf732db2b06f25da5dbbc149e0bc3908633d474824f1e938f437a05ec4027f25"),
])
def test_existing_phase_bodies_remain_exact(body, expected_sha):
    assert sha256(body.encode("utf-8")).hexdigest() == expected_sha


@pytest.mark.parametrize("old,body", [
    (COT_RATIONALE_V1, RATIONAL_ONLY_BODY_V1_2),
    (COT_NATIVE_ANSWER_V1, NATIVE_ANSWER_BODY_V1_2),
    (get("workflow.cot_prefix_check", version="1.0.0"), PREFIX_CHECK_BODY_V1_2),
])
def test_new_phase_contract_renders_as_own_version_without_mutating_old(old, body):
    original = old.template
    current = replace(old, version="99.0.0", template=body.replace("{", "{{").replace("}", "}}"))
    assert render(current) == body
    assert old.template == original
    assert current.template != original


def test_rationale_only_contract_contains_no_final_answer_field_and_keeps_audit():
    contract = json.loads(RATIONAL_ONLY_BODY_V1_2.rsplit("仅返回以下JSON，不增加answer：", 1)[1])
    assert set(contract) == {"reasoning", "reasoning_trace"}
    assert set(contract["reasoning_trace"]) == {"task", "premises", "steps", "checks", "limitations"}
    assert "长度不由问句字数或固定步骤数决定" in RATIONAL_ONLY_BODY_V1_2
    assert "参考旧答案写成已证明结论" not in RATIONAL_ONLY_BODY_V1_2


def test_prefix_checks_retain_factual_and_role_checks_without_metadata_keyword_rejection():
    assert "关键依赖失效时keep=false" in PREFIX_CHECK_BODY_V1_2
    assert "已有模型评分不能替代事实依据" in PREFIX_CHECK_BODY_V1_2
    assert "trace出现工具名或来源标识本身不是正文泄露" in PREFIX_CHECK_BODY_V1_2
    assert "正文泄露的判断须指出实际reasoning中的内容" in PREFIX_CHECK_BODY_V1_2
    assert "也不能凭参考答消除实际歧义" in PREFIX_CHECK_BODY_V1_2


def test_native_phase_does_not_claim_authored_prefill_is_spontaneous_internal_reasoning():
    assert "不是你自发产生的完整内部思考" in NATIVE_ANSWER_BODY_V1_2
    assert "纯文本回复" in NATIVE_ANSWER_BODY_V1_2
    assert "不强迫短答案" in NATIVE_ANSWER_BODY_V1_2
    assert "私有参考记录中的旧助手回答不是用户已见前文" in NATIVE_ANSWER_BODY_V1_2
