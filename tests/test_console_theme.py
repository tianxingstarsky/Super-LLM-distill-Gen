"""主题统一契约测试：共享浅色产品令牌、主题 CSS、消息作用域 CSS、独立预览页兼容。

断言基于 CSS/令牌语义（选择器、变量、对比度），不依赖源码空白/格式。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_TOKENS = ("bg", "layer1", "layer2", "bubble", "text", "text2", "text3", "border", "border2", "brand")
# (前景, 背景) 组合；文本色在任一实际承载表面上都必须 >= 4.5:1
CONTRAST_PAIRS = (
    ("text", "bg"), ("text", "layer1"), ("text", "layer2"), ("text", "bubble"),
    ("text2", "bg"), ("text2", "layer1"), ("text2", "layer2"), ("text2", "bubble"),
    ("text3", "bg"), ("text3", "layer1"), ("text3", "layer2"), ("text3", "bubble"),
    ("danger", "bg"), ("danger", "layer1"), ("danger", "layer2"),
)
# 控制台 CSS 允许的选择器根：应用外壳 / 浮层 / BaseWeb 控件 / df-* 自定义类
_SCOPED_CONSOLE = re.compile(
    r'^(?:\[data-testid="st[A-Za-z]+"\]|\[role="[a-z]+"\]|\[data-baseweb="[a-z-]+"\]|\.df-[a-z0-9-]+)'
)


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _selectors(css: str):
    for selector, _declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", _strip_comments(css), flags=re.S):
        for part in selector.split(","):
            if part.strip():
                yield part.strip()


def _channel(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    digits = color.lstrip("#")
    r, g, b = (int(digits[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _contrast(fg: str, bg: str) -> float:
    low, high = sorted((_luminance(fg), _luminance(bg)))
    return (high + 0.05) / (low + 0.05)


def test_light_tokens_required_keys():
    from lib.console_theme import LIGHT_TOKENS

    for key in REQUIRED_TOKENS:
        assert key in LIGHT_TOKENS and LIGHT_TOKENS[key], f"缺少令牌 {key}"


def test_light_token_text_contrast_at_least_4_5():
    from lib.console_theme import LIGHT_TOKENS

    for fg, bg in CONTRAST_PAIRS:
        ratio = _contrast(LIGHT_TOKENS[fg], LIGHT_TOKENS[bg])
        assert ratio >= 4.5, f"{fg} 在 {bg} 上对比度 {ratio:.2f} < 4.5"


def test_console_css_forced_light_without_os_preference():
    from lib.console_theme import CSS

    body = _strip_comments(CSS)
    assert "prefers-color-scheme" not in body          # 与系统/浏览器偏好解耦
    assert "color-scheme: light" in body                # 原生控件/滚动条走浅色
    assert "color-scheme: dark" not in body
    assert ":root" not in body                         # 不写全局根选择器
    assert re.search(r"[{,]\s*(body|html|\*)\s*[,{]", body) is None   # 无 body/html/通配符规则
    assert not re.search(r"\*[^{}]*!important", body)  # 无全局 !important 通配


def test_console_css_sets_native_streamlit_vars():
    from lib.console_theme import CSS, LIGHT_TOKENS

    body = _strip_comments(CSS)
    mapping = {"--text-color": "text", "--background-color": "bg",
               "--secondary-background-color": "layer1", "--primary-color": "brand"}
    for var, key in mapping.items():
        pattern = re.escape(var) + r"\s*:\s*" + re.escape(LIGHT_TOKENS[key])
        assert re.search(pattern, body), f"缺少原生主题变量 {var}"


def test_console_css_selectors_are_scoped():
    from lib.console_theme import CSS

    bad = [selector for selector in _selectors(CSS) if not _SCOPED_CONSOLE.match(selector)]
    assert not bad, f"存在未作用域选择器: {bad[:5]}"


def test_console_css_uses_shared_tokens():
    from lib.console_theme import CSS, THEME_TOKENS

    for key, value in THEME_TOKENS.items():
        pattern = rf"--df-{re.escape(key)}\s*:\s*{re.escape(value)}"
        assert re.search(pattern, CSS), f"令牌 {key} 未体现在控制台 CSS 中"


def test_message_css_scoped_to_bubbles_only():
    from lib.render import MESSAGE_CSS

    body = _strip_comments(MESSAGE_CSS)
    assert "prefers-color-scheme" not in body          # 嵌入消息区不随系统偏好切换
    assert ":root" not in body
    bad = [selector for selector in _selectors(MESSAGE_CSS) if not selector.startswith(".bubbles")]
    assert not bad, f"MESSAGE_CSS 存在非 .bubbles 作用域选择器: {bad[:5]}"
    for key in REQUIRED_TOKENS:
        assert f"--df-{key}" in body, f"MESSAGE_CSS 缺少令牌 {key}"


def test_message_css_reuses_product_tokens():
    from lib.console_theme import THEME_TOKENS
    from lib.render import MESSAGE_CSS

    for value in THEME_TOKENS.values():
        assert value in MESSAGE_CSS, f"MESSAGE_CSS 未使用共享色板值 {value}"


def test_standalone_preview_css_preserved():
    from lib.console_theme import DARK_TOKENS
    from lib.render import _CSS

    assert "prefers-color-scheme: dark" in _CSS        # 独立页仍跟随系统偏好
    assert "color-scheme: light dark" in _CSS
    assert "body {" in _CSS                            # 独立页保留自己的 body/标题排版
    for selector in (".bubbles", ".bub-user", ".bub-assistant", ".bub-tool", ".bub-system", ".bub-think", ".tool-call"):
        assert selector in _CSS
    for value in DARK_TOKENS.values():                 # 暗色块与共享令牌零漂移
        assert value in _CSS


def test_preview_html_still_renders_with_standalone_css(tmp_path):
    from lib.render import render_preview_html

    out = render_preview_html(
        [{"id": "s1", "model": "m", "finish_reason": "stop",
          "messages": [{"role": "user", "content": "**q**"}, {"role": "assistant", "content": "a"}]}],
        out_path=tmp_path / "preview.html")
    text = out.read_text(encoding="utf-8")
    assert "<style>" in text and "bub-user" in text and "prefers-color-scheme: dark" in text
    assert "<strong>q</strong>" in text


def test_streamlit_theme_config_matches_light_tokens():
    import tomllib
    from lib.console_theme import LIGHT_TOKENS

    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    theme = config["theme"]
    assert theme["base"] == "light"
    assert theme["backgroundColor"] == LIGHT_TOKENS["bg"]
    assert theme["secondaryBackgroundColor"] == LIGHT_TOKENS["layer1"]
    assert theme["textColor"] == LIGHT_TOKENS["text"]
    assert theme["primaryColor"] == LIGHT_TOKENS["brand"]
