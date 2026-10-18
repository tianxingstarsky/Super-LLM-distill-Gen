"""Depth mismatches use the actual workflow error and localization path."""
import re

import pytest

from lib.presentation.streamlit.i18n import translate_label
from lib.presentation.streamlit.workflow_page import _workflow_error


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Human-readable error copy requires no model endpoint or mock server."""
    yield


@pytest.mark.parametrize("code", [
    "native_regeneration_reasoning_depth_control_mismatch",
    "reasoning_depth_input_control_mismatch",
])
@pytest.mark.parametrize("error_type", [str, ValueError])
def test_depth_mismatch_has_an_actionable_bilingual_error(code, error_type):
    chinese = _workflow_error(error_type(code))
    assert "已封存样本的训练输入不一致" in chinese
    assert "不会修改用户或系统输入" in chinese
    assert "恢复原档位" in chinese and "新生成创建样本" in chinese
    assert code not in chinese
    assert not any(word in chinese for word in ("更换模型", "充值", "自动修改"))
    english = translate_label(chinese, "en")
    assert not re.search(r"[\u4e00-\u9fff]", english)
    assert "sealed sample's training input" in english
    assert "user and system inputs unchanged" in english
    assert "Restore the original depth" in english
    assert "generate a new sample" in english
    assert translate_label(chinese, "zh") == chinese


@pytest.mark.parametrize("error_type", [str, ValueError])
def test_history_depth_error_preserves_the_earlier_assistant_replies(error_type):
    code = "reasoning_depth_history_control_mismatch"
    chinese = _workflow_error(error_type(code))
    assert "历史对话已包含助手回复" in chinese
    assert "不能通过修改开头的深度指令重新标注旧回复" in chinese
    assert "沿用历史原档位" in chinese and "新建对话后重新生成" in chinese
    assert not any(word in chinese for word in (code, "思考强度", "更换模型", "充值", "自动修改"))
    english = translate_label(chinese, "en")
    assert not re.search(r"[\u4e00-\u9fff]", english)
    assert "assistant replies" in english
    assert "cannot relabel those replies" in english
    assert "Keep the original depth" in english
    assert "start a new conversation" in english
    assert translate_label(chinese, "zh") == chinese


def test_authored_reasoning_caption_requires_inferences_in_the_explanation():
    from streamlit.testing.v1 import AppTest

    phrase = "正文按任务自然组织，不套固定步骤；必要推导须写进正文，审计记录不能代替解释。推理规范可在节点提示词编辑。"
    ui = AppTest.from_string('''
import streamlit as st
from lib.presentation.streamlit.workflow_generation_settings import render_generation_settings
st.session_state['workflow-generation-enabled:fixture:cot'] = True
render_generation_settings('cot', 'fixture', save_field=lambda *args: None)
''').run()
    assert phrase in [item.value for item in ui.caption]
    assert not ui.exception
    english = translate_label(phrase, "en")
    assert "necessary inferences in the explanation" in english
    assert "Audit records cannot replace it" in english
    assert not re.search(r"[\u4e00-\u9fff]", english)
