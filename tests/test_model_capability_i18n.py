"""Keep node capability controls readable in both supported languages."""
import ast
from pathlib import Path
import re

from lib.presentation.streamlit.i18n import translate, translate_label, translate_markup


def test_model_capability_controls_translate_without_changing_model_names():
    source = Path(__file__).resolve().parents[1] / "lib/presentation/streamlit/workflow_model_capabilities.py"
    phrases = [node.value for node in ast.walk(ast.parse(source.read_text(encoding="utf-8")))
               if isinstance(node, ast.Constant) and isinstance(node.value, str)
               and re.search(r"[\u4e00-\u9fff]", node.value)]
    for phrase in phrases:
        assert not re.search(r"[\u4e00-\u9fff]", translate_label(phrase, "en")), phrase
        assert translate_label(phrase, "zh") == phrase
    assert translate("my-private-model-2026") == "my-private-model-2026"
    assert translate("我的自定义模型") == "我的自定义模型"


def test_model_capability_badges_translate_inside_markup():
    for feature in ("图片", "原生 PDF", "工具调用"):
        for status in ("支持", "不支持", "未知"):
            for source in ("手动设置", "服务返回", "公开元数据", "调用测试", "未知"):
                badge = f"<span>{feature} · {status} · {source}</span>"
                english = translate_markup(badge, "en")
                assert not re.search(r"[\u4e00-\u9fff]", english)
                assert translate_markup(badge, "zh") == badge
    assert translate_label("上下文容量 131,072 · 服务返回；最大输出容量 32,768 · 手动设置", "en") == (
        "Context capacity 131,072 · Service information；Output capacity 32,768 · Manual setting")
    assert translate_label("调用测试：文本 · 通过；图片 · 失败；原生 PDF · 已跳过", "en") == (
        "Call tests: Text · Passed; Images · Failed; Native PDF · Skipped")
