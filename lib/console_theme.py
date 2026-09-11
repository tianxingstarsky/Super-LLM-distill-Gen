"""统一暗色主题：共享设计令牌 + 强制暗色控制台样式（与浏览器/系统偏好解耦）。

约定：
  - DARK_TOKENS 是唯一色板事实源：控制台 chrome、消息气泡（lib/render.MESSAGE_CSS）、
    独立预览页的暗色块都由它生成；.streamlit/config.toml 的 [theme] 与之对齐。
  - CSS 只作用于 Streamlit 应用外壳与浮层（stApp / Sidebar / Dialog / BaseWeb 下拉），
    不写 body/:root/通配符；显式设置 color-scheme: dark 并覆盖原生主题变量
    （--text-color/--background-color/--secondary-background-color/--primary-color），
    因此浏览器或 Streamlit 偏好为 light 时仍呈现暗色。
"""
from __future__ import annotations

from string import Template

# 暗色设计令牌（键名稳定，供 CSS 生成与测试引用）。
# text/text2/text3/danger 相对 bg/layer1/layer2/bubble 的对比度均 >= 4.5：
# tests/test_console_theme.py 会做独立校验，改颜色时必须同步通过该测试。
DARK_TOKENS = {
    "bg": "#151517",
    "layer1": "#232324",
    "layer2": "#2C2C2E",
    "bubble": "#2C2C2E",
    "text": "#F9FAFB",
    "text2": "#CFD3D6",
    "text3": "#ADB2B8",
    "border": "rgba(255,255,255,.06)",
    "border2": "rgba(255,255,255,.12)",
    "brand": "#5686FE",
    "danger": "#F87171",
}


def dark_var_block(indent: str = "  ") -> str:
    """把 DARK_TOKENS 渲染为 --df-* 变量声明块，供任意作用域嵌入。"""
    return "\n".join(f"{indent}--df-{key}: {value};" for key, value in DARK_TOKENS.items())


_TEMPLATE = Template("""
/* 强制暗色控制台：与浏览器 prefers-color-scheme / Streamlit light 偏好解耦。
   --df-* 是共享令牌；--text-color 等是 Streamlit 原生主题变量，显式覆盖防止 light 兜底。 */
[data-testid="stApp"], [data-testid="stSidebar"], [data-testid="stDialog"], [role="dialog"], [data-baseweb="popover"], [data-baseweb="menu"], [data-baseweb="tooltip"] {
$dark_vars
  color-scheme: dark;
  --text-color: $text;
  --background-color: $bg;
  --secondary-background-color: $layer1;
  --primary-color: $brand;
}
[data-testid="stApp"] { background: var(--df-bg); color: var(--df-text); }
[data-testid="stApp"] [data-testid="stHeader"] { background: transparent; }
[data-testid="stApp"] [data-testid="stToolbar"] { visibility: hidden; }
[data-testid="stApp"] [data-testid="stMainBlockContainer"] { max-width: 1700px; padding: 1.5rem 1.5rem 1rem; }
[data-testid="stSidebar"] { background: var(--df-layer1); border-right: 1px solid var(--df-border2); }
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] { padding: 1.6rem 1.1rem; }
[data-testid="stSidebar"] label { color: var(--df-text2); padding: 7px 9px; border-radius: 8px; }
[data-testid="stSidebar"] [role="radiogroup"] { gap: 5px; }
[data-testid="stApp"] h1 { font-size: 1.7rem !important; font-weight: 600 !important; letter-spacing: -.03em; }
[data-testid="stApp"] h2 { font-size: 1.1rem !important; font-weight: 600 !important; }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color: var(--df-text3); }
[data-testid="stButton"] button, [data-testid="stFormSubmitButton"] button {
  border-radius: 9px; min-height: 38px; border-color: var(--df-border2); color: var(--df-text);
}
[data-testid="stButton"] button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primary"] {
  background: #365db5; border-color: var(--df-brand); color: #fff;
}
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] {
  background: var(--df-layer2); border-color: var(--df-border2); border-radius: 9px; color: var(--df-text);
}
[data-baseweb="input"] input, [data-baseweb="textarea"] textarea, [data-baseweb="select"] div { color: var(--df-text); }
[data-baseweb="input"] input::placeholder, [data-baseweb="textarea"] textarea::placeholder { color: var(--df-text3); }
[data-baseweb="popover"], [data-baseweb="menu"] { background: var(--df-layer1); color: var(--df-text); }
[data-baseweb="menu"] [role="option"] { color: var(--df-text); }
[data-baseweb="menu"] [role="option"]:hover, [data-baseweb="menu"] [aria-selected="true"] { background: var(--df-layer2); }
[role="dialog"], [data-testid="stDialog"] { background: var(--df-layer1); color: var(--df-text); }
[data-testid="stMetric"] { background: var(--df-layer1); border: 1px solid var(--df-border2); border-radius: 12px; padding: 16px; }
[data-testid="stMetricValue"] { font-size: 1.55rem; }
[data-testid="stForm"] { border: 1px solid var(--df-border2); border-radius: 12px; padding: 18px; }
.df-brand { font-size: 21px; font-weight: 650; letter-spacing: -.04em; margin-bottom: 6px; color: var(--df-text); }
.df-kicker { font-size: 11px; letter-spacing: .14em; color: var(--df-text3); margin: 0 0 25px; }
.df-empty { border: 1px dashed var(--df-border2); background: var(--df-layer1); border-radius: 14px; padding: 32px; margin: 18px 0; }
.df-empty h2 { font-size: 20px !important; margin: 0 0 12px; }
.df-empty p { color: var(--df-text2); line-height: 1.8; }
""")

CSS = _TEMPLATE.substitute(dark_vars=dark_var_block(), **DARK_TOKENS)
