"""Shared light product theme for the Streamlit console and review surfaces.

The static HTML preview keeps its own light/dark switch. Console and embedded
review components use THEME_TOKENS so one semantic palette controls the app.
"""
from __future__ import annotations

from string import Template


LIGHT_TOKENS = {
    "bg": "#F1F5FB",
    "layer1": "#FFFFFF",
    "layer2": "#F6F9FE",
    "bubble": "#EAF2FF",
    "text": "#152238",
    "text2": "#3F4D60",
    "text3": "#596A80",
    "border": "rgba(27,55,100,.08)",
    "border2": "rgba(27,55,100,.14)",
    "brand": "#1769E0",
    "danger": "#B42335",
}

# Used only by the standalone HTML preview's dark media query. Keeping this
# palette separate prevents the console redesign from silently changing exports.
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

THEME_TOKENS = LIGHT_TOKENS


def _token_block(tokens: dict[str, str], indent: str = "  ") -> str:
    return "\n".join(f"{indent}--df-{key}: {value};" for key, value in tokens.items())


def theme_var_block(indent: str = "  ") -> str:
    """Render the current product palette as scoped CSS custom properties."""
    return _token_block(THEME_TOKENS, indent)


def dark_var_block(indent: str = "  ") -> str:
    """Render dark colors for the standalone preview's dark-mode media query."""
    return _token_block(DARK_TOKENS, indent)


_TEMPLATE = Template("""
/* DataForge light workspace: scoped to Streamlit surfaces and DataForge classes. */
[data-testid="stApp"], [data-testid="stSidebar"], [data-testid="stDialog"], [role="dialog"], [data-baseweb="popover"], [data-baseweb="menu"], [data-baseweb="tooltip"] {
$theme_vars
  color-scheme: light;
  --text-color: $text;
  --background-color: $bg;
  --secondary-background-color: $layer1;
  --primary-color: $brand;
}
[data-testid="stApp"] { background: var(--df-bg); color: var(--df-text); }
[data-testid="stApp"] [data-testid="stHeader"] { background: transparent !important; border: 0; z-index: 1001; }
[data-testid="stApp"] [data-testid="stMainMenu"] { display: none !important; }
[data-testid="stAppDeployButton"] { display: none !important; }
[data-testid="stExpandSidebarButton"] { position: fixed !important; top: 6px !important; left: .5rem !important; z-index: 20000 !important; width: 44px !important; height: 44px !important; border: 1px solid #B8CFF0 !important; border-radius: 10px !important; background: #EAF2FF !important; color: #145DBF !important; box-shadow: 0 2px 8px rgba(30,60,100,.13) !important; }
[data-testid="stExpandSidebarButton"]:hover { border-color: #7EA9E8 !important; background: #DCEBFF !important; color: #064DAB !important; box-shadow: 0 3px 10px rgba(30,60,100,.2) !important; }
[data-testid="stSidebarCollapseButton"] { position: fixed !important; top: 6px !important; left: 14.25rem !important; z-index: 20000 !important; width: 44px !important; pointer-events: auto; }
[data-testid="stApp"]:has([data-testid="stSidebar"][aria-expanded="false"]) [data-testid="stSidebarCollapseButton"] { left: .5rem !important; }
[data-testid="stSidebarCollapseButton"] button { width: 44px; min-height: 44px; padding: 0; border: 1px solid #B8CFF0; border-radius: 10px; background: #EAF2FF; color: #145DBF; box-shadow: 0 2px 8px rgba(30,60,100,.13); }
[data-testid="stSidebarCollapseButton"] button:hover { border-color: #7EA9E8; background: #DCEBFF; color: #064DAB; box-shadow: 0 3px 10px rgba(30,60,100,.2); }
[data-testid="stApp"] [data-testid="stMainBlockContainer"] { max-width: 1680px; padding: 3.5rem 1.6rem 2.2rem; }
[data-testid="stApp"] [data-testid="stHeadingWithActionElements"]:has(> h1) { display: none !important; }
[data-testid="stSidebar"] { background: var(--df-layer1); border-right: 1px solid var(--df-border2); width: 14.25rem !important; min-width: 14.25rem !important; max-width: 14.25rem !important; }
[data-testid="stApp"]:has([data-testid="stSidebar"][aria-expanded="false"]) .df-topbar { left: 3.5rem; }
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] { padding: 1.25rem .9rem; }
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] { display: none; }
[data-testid="stSidebar"] .st-key-nav { display: none !important; }
[data-testid="stSidebar"] label { color: var(--df-text2); border-radius: 9px; }
[data-testid="stSidebar"] [data-testid="stButton"] button { justify-content: flex-start; min-height: 43px; padding: 0 12px; border-color: transparent; border-radius: 0 9px 9px 0; background: transparent; color: var(--df-text2); text-align: left; font-weight: 500; }
[data-testid="stSidebar"] [data-testid="stButton"] button:hover { border-color: transparent; background: #F0F5FD; color: var(--df-text); }
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"] { position: relative; border-color: transparent; background: #E5F0FF; color: #0757BF; font-weight: 650; }
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"]::before { content: ""; position: absolute; inset: 0 auto 0 -1px; width: 3px; border-radius: 0 2px 2px 0; background: var(--df-brand); }
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"]:hover { border-color: transparent; background: #D9EAFE; color: #0757BF; }
[data-testid="stSidebar"] [data-testid="stRadio"] label { position: relative; display: flex; align-items: center; min-height: 43px; margin-left: -.9rem; padding: 7px 10px 7px 12px; border-radius: 0 9px 9px 0; }
[data-testid="stSidebar"] [data-testid="stRadio"] label > :first-child { position: absolute !important; left: 0; top: 0; width: 1px !important; height: 1px !important; margin: 0 !important; opacity: 0 !important; overflow: hidden; }
[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) { background: #E5F0FF; color: #0757BF; font-weight: 600; }
[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked)::before { content: ""; position: absolute; inset: 0 auto 0 0; width: 3px; border-radius: 0 2px 2px 0; background: var(--df-brand); }
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover { background: #F0F5FD; }
[data-testid="stSidebar"] [role="radiogroup"] { gap: 4px; }
[data-testid="stSidebar"] [role="radio"] { position: relative; min-height: 42px; padding: 8px 10px; border-radius: 0 9px 9px 0; }
[data-testid="stSidebar"] [role="radio"] > :first-child { display: none; }
[data-testid="stSidebar"] input[type="radio"] { position: absolute; width: 1px; height: 1px; opacity: 0; }
[data-testid="stSidebar"] [role="radio"]:hover { background: #F0F5FD; }
[data-testid="stSidebar"] [role="radio"]:has(input:checked) { background: #E5F0FF; color: #0757BF; font-weight: 600; }
[data-testid="stSidebar"] [role="radio"]:has(input:checked)::before { content: ""; position: absolute; inset: 0 auto 0 0; width: 3px; border-radius: 0 2px 2px 0; background: var(--df-brand); }
[data-testid="stSidebar"] [data-baseweb="radio"] { position: relative; min-height: 43px; padding: 8px 10px; border-radius: 0 9px 9px 0; }
[data-testid="stSidebar"] [data-baseweb="radio"] > :first-child { margin-right: 0 !important; }
[data-testid="stSidebar"] [data-baseweb="radio"] input[type="radio"] { appearance: none; -webkit-appearance: none; width: 1px; height: 1px; opacity: 0; position: absolute; }
[data-testid="stSidebar"] [data-baseweb="radio"]:has(input:checked) { background: #E5F0FF; color: #0757BF; font-weight: 600; }
[data-testid="stSidebar"] [data-baseweb="radio"]:has(input:checked)::before { content: ""; position: absolute; inset: 0 auto 0 0; width: 3px; border-radius: 0 2px 2px 0; background: var(--df-brand); }
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: .45rem; }
[data-testid="stApp"] h1 { font-size: 1.8rem !important; font-weight: 700 !important; letter-spacing: -.035em; color: var(--df-text); }
[data-testid="stApp"] h2 { font-size: 1.13rem !important; font-weight: 650 !important; color: var(--df-text); }
[data-testid="stApp"] h3 { color: var(--df-text); }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color: var(--df-text3); }
[data-testid="stButton"] button, [data-testid="stFormSubmitButton"] button {
  border-radius: 9px; min-height: 38px; border-color: var(--df-border2); color: var(--df-text2); background: #fff;
}
[data-testid="stButton"] button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"] {
  background: var(--df-brand); border-color: var(--df-brand); color: #fff;
}
[data-testid="stButton"] button:hover, [data-testid="stFormSubmitButton"] button:hover { border-color: var(--df-brand); color: var(--df-brand); }
[data-testid="stButton"] button[kind="primary"]:hover, [data-testid="stFormSubmitButton"] button[kind="primary"]:hover, [data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"]:hover { color: #fff; background: #0C5BCB; }
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] {
  background: #fff; border-color: var(--df-border2); border-radius: 9px; color: var(--df-text);
}
[data-baseweb="input"] input, [data-baseweb="textarea"] textarea, [data-baseweb="select"] div { color: var(--df-text); }
[data-baseweb="input"] input::placeholder, [data-baseweb="textarea"] textarea::placeholder { color: var(--df-text3); }
/* Current Streamlit uses React Aria inputs; keep their outlines visible in light cards. */
[data-testid="stMainBlockContainer"] [data-testid="stTextInputRootElement"],
[data-testid="stMainBlockContainer"] [data-testid="stSelectbox"] .react-aria-ComboBox [role="group"],
[data-testid="stMainBlockContainer"] [data-testid="stMultiSelect"] .react-aria-ComboBox [role="group"],
[data-testid="stMainBlockContainer"] [data-testid="stNumberInput"] [role="group"],
[data-testid="stMainBlockContainer"] [data-testid="stNumberInputContainer"],
[data-testid="stMainBlockContainer"] [data-testid="stTextArea"] textarea {
  border: 1px solid #D7E2F0 !important; border-radius: 9px !important; background: #fff !important;
}
[data-testid="stMainBlockContainer"] [data-testid="stTextInputRootElement"]:focus-within,
[data-testid="stMainBlockContainer"] [data-testid="stSelectbox"] .react-aria-ComboBox [role="group"]:focus-within,
[data-testid="stMainBlockContainer"] [data-testid="stMultiSelect"] .react-aria-ComboBox [role="group"]:focus-within,
[data-testid="stMainBlockContainer"] [data-testid="stNumberInputContainer"]:focus-within,
[data-testid="stMainBlockContainer"] [data-testid="stTextArea"] textarea:focus {
  border-color: #6FA2EB !important; box-shadow: 0 0 0 3px rgba(23,105,224,.12) !important;
}
[data-baseweb="popover"], [data-baseweb="menu"] { background: #fff; color: var(--df-text); }
[data-baseweb="menu"] [role="option"] { color: var(--df-text); }
[data-baseweb="menu"] [role="option"]:hover, [data-baseweb="menu"] [aria-selected="true"] { background: #EAF2FF; }
[role="dialog"], [data-testid="stDialog"] { background: #fff; color: var(--df-text); }
[data-testid="stMetric"] { position: relative; overflow: hidden; background: #fff; border: 1px solid #D7E2F0; border-radius: 12px; padding: 17px 17px 15px; box-shadow: 0 4px 14px rgba(30,60,100,.055); }
[data-testid="stMetric"]::before { content: ""; position: absolute; inset: 0 0 auto; height: 3px; background: linear-gradient(90deg,#1769E0,#4DA9F5); }
[data-testid="stMetricLabel"] { color: var(--df-text3); }
[data-testid="stMetricValue"] { font-size: 1.65rem; font-weight: 700; color: var(--df-text); }
[data-testid="stForm"] { background: #fff; border: 1px solid var(--df-border2); border-radius: 12px; padding: 18px; }
[data-testid="stDataFrame"] { border: 1px solid var(--df-border); border-radius: 10px; overflow: hidden; }
[data-testid="stApp"] [data-testid="stSegmentedControl"] { padding: 4px; border-radius: 11px; background: #EAF0F7; }
[data-testid="stApp"] [data-testid="stSegmentedControl"] button { min-height: 34px; border: 0; border-radius: 8px; background: transparent; color: var(--df-text2); font-size: 12px; }
[data-testid="stApp"] [data-testid="stSegmentedControl"] button[aria-pressed="true"] { background: #fff; color: var(--df-brand); box-shadow: 0 1px 4px rgba(30,60,100,.12); font-weight: 650; }
[data-testid="stVerticalBlockBorderWrapper"] { border-color: #D8E3F0 !important; border-radius: 14px; background: #fff !important; box-shadow: 0 4px 16px rgba(30,60,100,.045); }
/* Recent Streamlit renders border=True on the block itself, without the old wrapper. */
[data-testid="stVerticalBlock"][data-test-wrap="false"] { border-color: #D8E3F0 !important; border-radius: 14px !important; background: #fff !important; box-shadow: 0 4px 16px rgba(30,60,100,.045); }
[data-testid="stExpander"] { background: #fff; border-color: var(--df-border2); border-radius: 11px; }
.df-brand { font-size: 21px; font-weight: 720; letter-spacing: -.045em; margin-bottom: 4px; color: var(--df-text); }
.df-kicker { font-size: 10px; letter-spacing: .13em; color: var(--df-text3); margin: 0 0 18px; }
.df-brand-line { display: flex; align-items: center; gap: 10px; margin: 0 0 18px; }
.df-brand-line { margin-top: -3.25rem; }
.df-brand-line .df-brand { display: block; font-size: 18px; line-height: 1.15; margin: 0; }
.df-brand-line .df-kicker { display: block; font-size: 10px; letter-spacing: .04em; margin: 4px 0 0; }
.df-brand-mark { display: block; flex: 0 0 36px; width: 36px; height: 36px; object-fit: contain; }
.df-topbar { position: fixed; inset: 0 0 auto 14.25rem; z-index: 1000; display: flex; align-items: center; justify-content: space-between; gap: 18px; height: 56px; margin: 0; padding: 0 1.5rem 0 3.6rem; border-bottom: 1px solid var(--df-border2); background: rgba(255,255,255,.98); color: var(--df-text2); font-size: 13px; box-shadow: 0 1px 4px rgba(30,60,100,.035); }
.df-topbar strong { color: var(--df-text); font-weight: 650; }
.df-topbar-meta { color: var(--df-text3); }
.df-hero { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; padding: 1.15rem 1.3rem; margin: 0 0 1.1rem; border: 1px solid #DCE8F8; border-radius: 15px; background: linear-gradient(105deg,#F7FAFF 0%,#EFF6FF 63%,#E6F1FF 100%); }
.df-hero-package { position: relative; min-height: 70px; align-items: center; overflow: hidden; padding: .45rem 1.3rem 1.1rem; margin-top: -.25rem; border: 0; border-radius: 0; background: linear-gradient(100deg,rgba(247,250,255,.15),rgba(235,244,255,.82)); }
.df-hero-package > div:first-child, .df-hero-package .df-hero-tag { position: relative; z-index: 2; }
.df-hero-eyebrow { display: block; margin-bottom: 5px; color: #3977C8; font-size: 9px; font-weight: 750; letter-spacing: .2em; }
.df-hero-package p { max-width: 650px; font-size: 13px; }
.df-hero-art { position: absolute; inset: 0 18% 0 auto; width: 230px; opacity: .75; }
.df-hero-art i { position: absolute; display: block; width: 52px; height: 52px; transform: rotate(30deg) skew(-10deg,-10deg); border: 1px solid rgba(69,132,220,.24); background: linear-gradient(145deg,rgba(255,255,255,.95),rgba(141,186,246,.45)); box-shadow: 16px 18px 28px rgba(62,124,218,.12); }
.df-hero-art i:nth-child(1) { top: 4px; left: 100px; width: 65px; height: 65px; }
.df-hero-art i:nth-child(2) { top: 27px; left: 45px; width: 38px; height: 38px; opacity: .7; }
.df-hero-art i:nth-child(3) { top: 28px; left: 174px; width: 42px; height: 42px; opacity: .55; }
.df-hero h1 { margin: 0 0 .35rem; font-size: 1.7rem !important; }
.df-hero p { margin: 0; color: var(--df-text2); }
.df-hero-tag { color: #0757BF; font-size: 12px; font-weight: 650; letter-spacing: .05em; white-space: nowrap; }
.df-node { display: flex; align-items: center; gap: 11px; min-height: 70px; padding: 12px 14px; border: 1px solid var(--df-border2); border-radius: 12px; background: #fff; box-shadow: 0 2px 10px rgba(30,60,100,.035); }
.df-node-icon { width: 34px; height: 34px; display: grid; place-items: center; flex: 0 0 auto; border-radius: 10px; background: #EAF2FF; color: var(--df-brand); font-weight: 700; }
.df-node-copy { min-width: 0; }
.df-node-title { color: var(--df-text); font-weight: 650; font-size: 13px; }
.df-node-sub { color: var(--df-text3); font-size: 11px; margin-top: 3px; }
.df-flow-arrow { text-align: center; color: #78A7E8; font-size: 18px; line-height: 1; padding: 4px 0; }
.df-overview-flow { display: flex; align-items: center; gap: 12px; overflow-x: auto; padding: 4px 0 16px; }
.df-overview-flow > span { color: #83A9DD; font-size: 19px; }
.df-overview-step { display: grid; grid-template-columns: 28px 1fr; align-items: center; column-gap: 10px; min-width: 205px; padding: 13px 15px; border: 1px solid var(--df-border2); border-radius: 11px; background: #fff; }
.df-overview-step b { grid-row: span 2; display: grid; place-items: center; width: 26px; height: 26px; border-radius: 50%; background: #EAF2FF; color: var(--df-brand); font-size: 11px; }
.df-overview-step strong { font-size: 13px; color: var(--df-text); }
.df-overview-step small { margin-top: 3px; color: var(--df-text3); font-size: 11px; }
.df-flow-scroll { overflow-x: auto; padding: 2px 0 12px; }
.df-flow-rail { display: flex; align-items: center; min-width: max-content; gap: 0; }
.df-stage-node { width: 152px; min-height: 103px; padding: 11px; border: 1px solid var(--df-border2); border-radius: 11px; background: #fff; box-shadow: 0 2px 9px rgba(30,60,100,.035); }
.df-stage-node[data-status="running"] { border-color: #4C91EB; box-shadow: 0 0 0 2px rgba(23,105,224,.1); }
.df-stage-node[data-status="completed"] { border-color: #B8E2D2; background: #FBFEFC; }
.df-stage-node[data-status="failed"], .df-stage-node[data-status="cancelled"] { border-color: #F0B6B9; background: #FFFAFA; }
.df-stage-head { display: flex; align-items: center; gap: 7px; color: var(--df-text); font-size: 12px; font-weight: 650; }
.df-stage-head span { display: grid; place-items: center; width: 22px; height: 22px; border-radius: 7px; background: #EAF2FF; color: var(--df-brand); font-size: 12px; }
.df-stage-meta { display: flex; justify-content: space-between; gap: 6px; margin-top: 9px; color: var(--df-text3); font-size: 10px; }
.df-stage-track { height: 4px; margin-top: 6px; border-radius: 4px; background: #E8EEF6; overflow: hidden; }
.df-stage-track i { display: block; height: 100%; border-radius: inherit; background: var(--df-brand); }
.df-stage-node[data-status="completed"] .df-stage-track i { background: #1B9A6C; }
.df-stage-node[data-status="failed"] .df-stage-track i { background: var(--df-danger); }
.df-stage-result { margin-top: 5px; color: var(--df-text3); font-size: 10px; }
.df-brand-card { background: #fff; border: 1px solid var(--df-border2); border-radius: 13px; padding: 17px; box-shadow: 0 3px 14px rgba(30,60,100,.035); }
.df-empty { border: 1px dashed var(--df-border2); background: #fff; border-radius: 14px; padding: 32px; margin: 18px 0; }
.df-empty h2 { font-size: 20px !important; margin: 0 0 12px; }
.df-empty p { color: var(--df-text2); line-height: 1.8; }
.df-sidebar-note { display: grid; gap: 5px; margin-top: 1rem; padding: 15px 13px; border: 1px solid #DCE8F8; border-radius: 12px; background: linear-gradient(145deg,#F7FAFF,#EAF3FF); color: var(--df-text); }
.df-sidebar-note .df-note-mark { display: grid; place-items: center; width: 27px; height: 27px; margin-bottom: 3px; border-radius: 8px; background: #DBEAFE; color: var(--df-brand); }
.df-sidebar-note strong { font-size: 12px; }
.df-sidebar-note small { color: var(--df-text3); font-size: 11px; }
.df-panel { background: #fff; border: 1px solid var(--df-border2); border-radius: 14px; padding: 18px; box-shadow: 0 3px 14px rgba(30,60,100,.035); }
.df-panel-head { display: flex; align-items: center; gap: 10px; margin: 0 0 14px; }
.df-panel-icon { display: grid; place-items: center; flex: 0 0 auto; width: 32px; height: 32px; border-radius: 9px; background: #EAF2FF; color: var(--df-brand); font-size: 15px; font-weight: 700; }
.df-panel-title { color: var(--df-text); font-size: 15px; font-weight: 700; }
.df-panel-subtitle { margin-top: 2px; color: var(--df-text3); font-size: 11px; }
.df-format-card { display: flex; align-items: center; gap: 12px; min-height: 88px; padding: 14px; border: 1px solid #BBD4F6; border-radius: 11px; background: #F2F7FF; }
.df-format-icon { display: grid; place-items: center; width: 38px; height: 42px; flex: 0 0 auto; border-radius: 8px; background: linear-gradient(145deg,#4B91EE,#1769E0); color: white; font-size: 13px; font-weight: 750; box-shadow: 0 3px 8px rgba(23,105,224,.18); }
.df-format-copy { min-width: 0; }
.df-format-copy strong { display: block; color: var(--df-text); font-size: 13px; }
.df-format-copy small { display: block; margin-top: 4px; color: var(--df-text3); font-size: 11px; }
.df-file-list { overflow: hidden; border: 1px solid var(--df-border2); border-radius: 11px; background: #fff; }
.df-file-head, .df-file-row { display: grid; grid-template-columns: minmax(190px,1.6fr) minmax(95px,.75fr) minmax(84px,.65fr) minmax(130px,1fr); align-items: center; gap: 12px; padding: 10px 14px; }
.df-file-head { background: #F1F5FA; color: var(--df-text3); font-size: 11px; font-weight: 650; }
.df-file-row { min-height: 44px; border-top: 1px solid #E9EEF5; color: var(--df-text2); font-size: 12px; }
.df-file-name { display: flex; align-items: center; min-width: 0; gap: 9px; color: var(--df-text); font-weight: 600; }
.df-file-name span:last-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.df-file-mark { display: grid; place-items: center; width: 25px; height: 27px; flex: 0 0 auto; border-radius: 6px; background: #EAF2FF; color: var(--df-brand); font-size: 9px; font-weight: 800; }
.df-file-hash { color: var(--df-text3); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 10px; }
.df-status-pill { display: inline-flex; align-items: center; gap: 6px; width: fit-content; padding: 5px 9px; border-radius: 999px; background: #E6F7F0; color: #137452; font-size: 11px; font-weight: 650; }
.df-status-pill i { display: grid; place-items: center; width: 15px; height: 15px; border-radius: 50%; background: currentColor; color: #fff; font-size: 9px; font-style: normal; }
.df-status-attention { background: #FFF4DE; color: #996900; }
.df-status-failed { background: #FDECEF; color: #A92335; }
.df-status-running { background: #EAF2FF; color: #1769E0; }
.df-status-muted { background: #EFF3F8; color: #596A80; }
.df-format-check { display: grid; place-items: center; width: 21px; height: 21px; margin-left: auto; border-radius: 6px; background: var(--df-brand); color: #fff; font-size: 12px; }
.df-empty-package { display: grid; justify-items: start; gap: 9px; min-height: 205px; align-content: center; padding: 18px 10px 20px; }
.df-empty-package-icon { display: grid; place-items: center; width: 48px; height: 48px; margin-bottom: 3px; border-radius: 13px; background: #EAF2FF; color: var(--df-brand); font-size: 21px; }
.df-empty-package strong { color: var(--df-text); font-size: 17px; }
.df-empty-package p { max-width: 640px; margin: 0; color: var(--df-text2); font-size: 12px; line-height: 1.7; }
.df-target-card { display: grid; grid-template-columns: 38px 1fr; align-items: center; column-gap: 10px; min-height: 88px; padding: 14px; border: 1px solid var(--df-border2); border-radius: 11px; background: #fff; }
.df-target-card > span { grid-row: span 2; display: grid; place-items: center; width: 36px; height: 36px; border-radius: 9px; background: #F1F6FE; font-size: 11px; font-weight: 800; }
.df-target-card strong { color: var(--df-text); font-size: 12px; }
.df-target-card small { align-self: start; margin-top: 3px; color: var(--df-text3); font-size: 10px; }
.df-wizard-steps { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 0 0 16px; padding: 14px 18px; border: 1px solid var(--df-border2); border-radius: 13px; background: #fff; box-shadow: 0 2px 9px rgba(30,60,100,.025); }
.df-wizard-step { display: flex; align-items: center; gap: 10px; min-width: 0; color: var(--df-text3); }
.df-wizard-step b { display: grid; place-items: center; width: 30px; height: 30px; flex: 0 0 auto; border-radius: 50%; background: #EEF2F7; color: #66768B; font-size: 12px; }
.df-wizard-step.active b { background: var(--df-brand); color: #fff; box-shadow: 0 3px 8px rgba(23,105,224,.18); }
.df-wizard-step strong { display: block; color: var(--df-text); font-size: 12px; }
.df-wizard-step small { display: block; margin-top: 3px; color: var(--df-text3); font-size: 12px; white-space: nowrap; }
.df-wizard-steps > i { width: 24px; height: 1px; flex: 0 0 auto; background: #D7E1EE; }
.df-workflow-targets { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 10px; margin: 9px 0 17px; }
.df-workflow-targets > div { display: grid; grid-template-columns: 36px 1fr; align-items: center; column-gap: 10px; min-height: 90px; padding: 12px; border: 1px solid #DEE7F3; border-radius: 11px; background: linear-gradient(145deg,#fff,#FAFCFF); }
.df-workflow-targets b { grid-row: span 2; display: grid; place-items: center; width: 35px; height: 35px; border-radius: 9px; background: #EAF2FF; color: var(--df-brand); font-size: 9px; }
.df-workflow-targets > div:nth-child(2) b { background: #E8F7F1; color: #14805D; }
.df-workflow-targets > div:nth-child(3) b { background: #F1ECFC; color: #7350C8; }
.df-workflow-targets > div:nth-child(4) b { background: #FFF2E6; color: #B45C13; }
.df-workflow-targets strong { color: var(--df-text); font-size: 13px; }
.df-workflow-targets small { align-self: start; margin-top: 2px; color: var(--df-text3); font-size: 11px; line-height: 1.45; }
.df-workflow-targets-compact { grid-template-columns: repeat(2,minmax(0,1fr)); gap: 7px; margin: 0 0 10px; }
.df-workflow-targets-compact > div { min-height: 82px; padding: 12px; background: linear-gradient(155deg,#FFF,#F7FAFF); }
.df-workflow-targets-compact > div b { width: 36px; height: 36px; font-size: 10px; }
.df-workflow-targets-compact strong { font-size: 12px; }
.df-workflow-targets-compact small { font-size: 10px; }
[data-testid="stApp"] .st-key-workflow-source-mode, [data-testid="stApp"] .st-key-workflow-source-mode [data-testid="stButtonGroup"] { width: 100%; }
[data-testid="stApp"] [role="radiogroup"][aria-label="选择来源类型"] { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); width: 100%; max-width: none; gap: 12px; padding: 0; background: transparent; }
[data-testid="stApp"] [role="radiogroup"][aria-label="选择来源类型"] button { display: grid; grid-template-columns: 45px minmax(0,1fr); grid-template-rows: auto auto; align-content: center; column-gap: 13px; row-gap: 5px; min-height: 110px; padding: 17px; border: 1px solid #DEE7F3; border-radius: 11px; background: linear-gradient(150deg,#FFF,#F8FBFF); color: var(--df-text); text-align: left; box-shadow: 0 2px 9px rgba(35,75,125,.025); }
[data-testid="stApp"] [role="radiogroup"][aria-label="选择来源类型"] button[data-selected="true"] { border-color: #75A9EC; background: #EFF6FF; color: #1263C9; box-shadow: 0 3px 10px rgba(30,105,205,.08); }
[data-testid="stApp"] [role="radiogroup"][aria-label="选择来源类型"] button::before { display: grid; grid-column: 1; grid-row: 1 / 3; place-items: center; width: 45px; height: 45px; border-radius: 10px; background: #EAF2FF; color: #1769E0; font-size: 22px; }
[data-testid="stApp"] [role="radiogroup"][aria-label="选择来源类型"] button > div { grid-column: 2; grid-row: 1; align-self: end; font-size: 15px; font-weight: 750; }
[data-testid="stApp"] [role="radiogroup"][aria-label="选择来源类型"] button::after { grid-column: 2; grid-row: 2; align-self: start; color: #718198; font-size: 11px; font-weight: 450; line-height: 1.45; white-space: normal; }
[data-testid="stApp"] [role="radiogroup"][aria-label="选择来源类型"] button:nth-child(1)::before { content: "▤"; }
[data-testid="stApp"] [role="radiogroup"][aria-label="选择来源类型"] button:nth-child(2)::before { content: "◈"; background: #E8F7F1; color: #16845E; }
[data-testid="stApp"] [role="radiogroup"][aria-label="选择来源类型"] button:nth-child(3)::before { content: "✦"; background: #F1ECFC; color: #7350C8; }
[data-testid="stApp"] [role="radiogroup"][aria-label="选择来源类型"] button:nth-child(1)::after { content: "PDF / DOCX / TXT · 保留原文来源"; }
[data-testid="stApp"] [role="radiogroup"][aria-label="选择来源类型"] button:nth-child(2)::after { content: "对话、工具调用与真实观测"; }
[data-testid="stApp"] [role="radiogroup"][aria-label="选择来源类型"] button:nth-child(3)::after { content: "描述任务领域和应用场景"; }
@media (max-width: 760px) { [data-testid="stApp"] [role="radiogroup"][aria-label="选择来源类型"] { grid-template-columns: 1fr; } }
.df-package-recent { border-bottom: 1px solid #E9EEF5; padding: 9px 0; }
.df-status-dot { width: 7px; height: 7px; border-radius: 50%; background: #20A478; }
.df-step-list { display: grid; gap: 0; margin: 14px 0 2px; }
.df-step { display: grid; grid-template-columns: 25px 1fr; gap: 10px; position: relative; min-height: 47px; color: var(--df-text2); font-size: 12px; }
.df-step:not(:last-child)::after { content: ""; position: absolute; top: 23px; left: 11px; height: 23px; border-left: 1px solid #D9E3F0; }
.df-step-icon { display: grid; place-items: center; width: 23px; height: 23px; border-radius: 50%; background: #E6F7F0; color: #137452; font-size: 11px; font-weight: 700; }
.df-step.pending .df-step-icon { background: #EFF3F8; color: #8492A6; }
.df-step strong { display: block; color: var(--df-text); font-size: 12px; }
.df-step small { display: block; margin-top: 2px; color: var(--df-text3); font-size: 10px; }
.df-page-hero { position: relative; display: flex; align-items: center; justify-content: space-between; gap: 22px; min-height: 112px; overflow: hidden; padding: 19px 25px; margin: 0 0 18px; border: 1px solid #CFE0F6; border-radius: 15px; background: linear-gradient(105deg,#F8FBFF 0%,#EFF6FF 57%,#E2EEFF 100%); box-shadow: 0 5px 18px rgba(36,91,159,.055), inset 0 1px 0 rgba(255,255,255,.95); }
.df-page-copy, .df-page-tag { position: relative; z-index: 2; }
.df-page-eyebrow { display: block; margin-bottom: 6px; color: #256AC2; font-size: 10px; font-weight: 750; letter-spacing: .18em; }
.df-page-hero h1 { margin: 0 !important; color: var(--df-text); font-size: 29px !important; line-height: 1.2; letter-spacing: -.035em; }
.df-page-hero p { max-width: 720px; margin: 6px 0 0; color: var(--df-text2); font-size: 15px; line-height: 1.55; }
.df-page-tag { padding: 7px 11px; border: 1px solid rgba(49,112,194,.15); border-radius: 999px; background: rgba(255,255,255,.62); color: #0757BF; font-size: 10px; font-weight: 750; letter-spacing: .07em; white-space: nowrap; box-shadow: 0 2px 8px rgba(39,91,154,.05); }
.df-page-hero .df-hero-art { inset: 0 14% 0 auto; width: 260px; opacity: .88; }
.df-page-hero .df-hero-art i { position: absolute; display: block; top: 5px; left: 80px; width: 92px; height: 106px; transform: none; border: 0; background: none; box-shadow: none; filter: drop-shadow(5px 9px 10px rgba(48,116,210,.18)); }
.df-page-hero .df-hero-art i:nth-child(2) { top: 43px; left: 22px; width: 59px; height: 68px; opacity: .56; }
.df-page-hero .df-hero-art i:nth-child(3) { top: 32px; left: 180px; width: 70px; height: 79px; opacity: .43; }
.df-page-hero .df-hero-art i > * { position: absolute; inset: 0; display: block; }
.df-page-hero .df-hero-art i > b { clip-path: polygon(0 25%,50% 50%,50% 100%,0 75%); background: linear-gradient(135deg,#E5F2FF,#5EA4F3); }
.df-page-hero .df-hero-art i > em { clip-path: polygon(50% 50%,100% 25%,100% 75%,50% 100%); background: linear-gradient(145deg,#99CAFB,#1769E0); }
.df-page-hero .df-hero-art i > span { clip-path: polygon(0 25%,50% 0,100% 25%,50% 50%); background: linear-gradient(135deg,#FFFFFF,#A8D0FF); }
.df-section-heading { display: flex; align-items: center; gap: 10px; margin: 0 0 14px; padding-bottom: 11px; border-bottom: 1px solid #E8EEF6; }
.df-section-heading strong { display: block; color: var(--df-text); font-size: 15px; font-weight: 700; }
.df-section-heading small { display: block; margin-top: 3px; color: var(--df-text3); font-size: 13px; }
.df-quick-card { min-height: 113px; padding: 15px; border: 1px solid var(--df-border2); border-radius: 12px; background: linear-gradient(145deg,#fff,#F8FAFD); }
.df-quick-card .df-node-icon { margin-bottom: 10px; }
.df-quick-card strong { display: block; color: var(--df-text); font-size: 13px; }
.df-quick-card p { margin: 4px 0 0; color: var(--df-text3); font-size: 13px; line-height: 1.5; }
.df-asset-list { overflow: hidden; border: 1px solid var(--df-border2); border-radius: 12px; background: #fff; }
.df-asset-head, .df-asset-row { display: grid; grid-template-columns: minmax(220px,2fr) minmax(80px,.6fr) minmax(100px,.8fr) minmax(90px,.6fr); align-items: center; gap: 12px; padding: 10px 14px; }
.df-asset-head { background: #F1F5FA; color: var(--df-text3); font-size: 11px; font-weight: 700; }
.df-asset-row { min-height: 46px; border-top: 1px solid #E9EEF5; color: var(--df-text2); font-size: 12px; transition: background .15s ease; }
.df-asset-row:hover { background: #F4F8FF; }
.df-asset-file { display: flex; align-items: center; min-width: 0; gap: 9px; color: var(--df-text); font-weight: 600; }
.df-asset-file span:last-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.df-asset-kind { display: grid; place-items: center; width: 28px; height: 30px; flex: 0 0 auto; border-radius: 7px; background: #EAF2FF; color: var(--df-brand); font-size: 8px; font-weight: 800; }
.df-asset-kind[data-ext="PDF"], .df-asset-kind[data-ext="DOCX"] { background: #FFF0F0; color: #D44A4A; }
.df-asset-kind[data-ext="CSV"], .df-asset-kind[data-ext="XLSX"] { background: #E8F7EE; color: #168454; }
.df-asset-kind[data-ext="TXT"], .df-asset-kind[data-ext="MD"] { background: #F1ECFC; color: #7350C8; }
.df-asset-path { overflow: hidden; color: var(--df-text3); text-overflow: ellipsis; white-space: nowrap; }
.df-empty-state { display: grid; justify-items: center; gap: 8px; padding: 28px 16px; border: 1px dashed #C9D7E8; border-radius: 13px; background: #FBFDFF; text-align: center; }
.df-empty-state-icon { display: grid; place-items: center; width: 42px; height: 42px; margin-bottom: 3px; border-radius: 12px; background: #EAF2FF; color: var(--df-brand); font-size: 19px; }
.df-empty-state strong { color: var(--df-text); font-size: 14px; }
.df-empty-state p { max-width: 560px; margin: 0; color: var(--df-text3); font-size: 11px; line-height: 1.65; }
.df-callout { padding: 12px 14px; border: 1px solid #D8E7FA; border-radius: 10px; background: #F2F7FE; color: #31577F; font-size: 12px; line-height: 1.6; }
.df-nav-list { display: grid; gap: 8px; margin: 4px 0 14px; }
.df-nav-list > div { display: grid; gap: 3px; padding: 11px 12px; border: 1px solid #E7EDF5; border-radius: 9px; background: #FBFCFE; }
.df-nav-list b { color: var(--df-text); font-size: 12px; }
.df-nav-list span { color: var(--df-text3); font-size: 10px; }
.df-stage-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 9px; margin-top: 6px; }
.df-stage-grid > div { display: flex; align-items: center; gap: 10px; min-height: 70px; padding: 10px; border: 1px solid #E5ECF5; border-radius: 10px; background: #FBFCFE; }
.df-stage-grid b { display: grid; place-items: center; width: 29px; height: 29px; flex: 0 0 auto; border-radius: 9px; background: #EAF2FF; color: var(--df-brand); font-size: 10px; }
.df-stage-grid strong { display: block; color: var(--df-text); font-size: 11px; }
.df-stage-grid small { display: block; margin-top: 3px; color: var(--df-text3); font-size: 9px; line-height: 1.4; }
.df-conversation-card { padding: 18px 20px; border: 1px solid var(--df-border2); border-radius: 13px; background: #fff; }
.df-conversation-title { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; padding-bottom: 12px; margin-bottom: 16px; border-bottom: 1px solid #E9EEF5; }
.df-conversation-title strong { color: var(--df-text); font-size: 14px; }
.df-conversation-title small { color: var(--df-text3); font-size: 10px; }
.df-conversation-card .bubbles { gap: 13px; }
.df-conversation-card .bubbles .bub-user { max-width: min(720px,86%); border-radius: 17px 6px 17px 17px; }
.df-conversation-card .bubbles .bub-assistant:not(:has(.tool-call)) { align-self: flex-start; max-width: min(900px,90%); padding: 11px 15px; border: 1px solid #E8EDF4; border-radius: 6px 16px 16px; background: #F8FAFD; }
.df-conversation-card .bubbles .bub-user .role-tag, .df-conversation-card .bubbles .bub-assistant .role-tag { display: block; margin-bottom: 4px; color: #63748B; font-family: inherit; font-size: 10px; font-weight: 700; letter-spacing: .02em; }
.df-conversation-card .bubbles .bub-assistant:has(.tool-call) { align-self: flex-start; max-width: min(900px,90%); padding: 10px 13px; border: 1px solid #E7DDF8; border-radius: 9px; background: #FBF9FF; }
.df-conversation-card .bubbles .bub-think { align-self: flex-start; max-width: min(900px,90%); }
.df-conversation-card .bubbles .bub-tool { max-width: min(900px,90%); font-size: 12px; }
.df-conversation-card .bubbles .bub-system { max-width: 90%; border-radius: 10px; }
.df-preview-meta { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }
.df-preview-meta span { padding: 5px 9px; border: 1px solid #DFE8F3; border-radius: 999px; background: #fff; color: var(--df-text2); font-size: 10px; }
.df-artifact-preview { display: grid; gap: 13px; padding: 15px; border: 1px solid #DCE6F3; border-radius: 12px; background: #FBFDFF; }
.df-artifact-heading { display: flex; align-items: center; gap: 9px; padding-bottom: 11px; border-bottom: 1px solid #E8EEF6; }
.df-artifact-heading strong { color: var(--df-text); font-size: 13px; }
.df-artifact-badge { display: inline-grid; place-items: center; min-width: 38px; height: 27px; padding: 0 7px; border-radius: 7px; background: #E7F0FF; color: #135DBD; font-size: 9px; font-weight: 800; letter-spacing: .04em; }
.df-artifact-badge[data-target="sft"], .df-artifact-badge[data-target="multiturn"], .df-artifact-badge[data-target="agent"] { background: #E5F8F1; color: #0E835A; }
.df-artifact-badge[data-target="agent_negative"] { background: #FDECEF; color: #A92335; }
.df-artifact-badge[data-target="dpo"], .df-artifact-badge[data-target="orpo"], .df-artifact-badge[data-target="rlaif"] { background: #F0E9FC; color: #7350C8; }
.df-artifact-badge[data-target="gsm8k"], .df-artifact-badge[data-target="cot"] { background: #FFF0E3; color: #A95713; }
.df-artifact-meta { display: flex; flex-wrap: wrap; gap: 7px; }
.df-artifact-meta span { padding: 4px 8px; border: 1px solid #DBE6F3; border-radius: 999px; background: #fff; color: var(--df-text3); font-size: 10px; }
.df-artifact-conversation { min-width: 0; padding: 12px; border: 1px solid #E2EAF4; border-radius: 10px; background: #fff; }
.df-artifact-subhead { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 10px; color: var(--df-text); font-size: 11px; font-weight: 700; }
.df-artifact-subhead span { color: var(--df-text3); font-size: 10px; font-weight: 500; }
.df-artifact-conversation .bubbles { gap: 12px; }
.df-artifact-conversation .bubbles .bub-user { max-width: 88%; border-radius: 14px 5px 14px 14px; }
.df-artifact-conversation .bubbles .bub-assistant:not(:has(.tool-call)) { align-self: flex-start; max-width: 94%; padding: 10px 12px; border: 1px solid #E6EDF5; border-radius: 5px 13px 13px; background: #F8FAFD; }
.df-artifact-compare { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 10px; }
.df-artifact-chosen, .df-artifact-rejected, .df-artifact-feedback { min-width: 0; padding: 7px; border: 1px solid #C7E8D9; border-radius: 10px; background: #F3FBF7; }
.df-artifact-rejected { border-color: #F0D5DD; background: #FFF7F8; }
.df-artifact-feedback { border-color: #DDD6F1; background: #FAF8FF; }
.df-artifact-text, .df-artifact-reasoning { min-width: 0; padding: 12px; border: 1px solid #E2EAF4; border-radius: 10px; background: #fff; }
.df-artifact-text .df-artifact-body { max-height: 270px; overflow: auto; color: var(--df-text2); font-size: 12px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
.df-artifact-answer { border-color: #CDE6D9; background: #F8FDFB; }
.df-artifact-step { display: flex; gap: 10px; padding: 8px 0; border-top: 1px solid #EDF1F6; color: var(--df-text2); font-size: 12px; line-height: 1.6; }
.df-artifact-step b { color: #C17725; font-size: 10px; }
.df-artifact-muted { color: var(--df-text3); font-size: 11px; }
.df-artifact-failure { padding: 8px 10px; border: 1px solid #F0D5DD; border-radius: 8px; background: #FFF7F8; color: #9E253E; font-size: 11px; }
.df-review-empty { display: grid; grid-template-columns: minmax(240px,.85fr) minmax(0,1.7fr); gap: 22px; align-items: stretch; padding: 23px; border: 1px solid #D9E6F6; border-radius: 14px; background: linear-gradient(115deg,#F8FBFF 0%,#F1F7FF 58%,#EAF3FF 100%); }
.df-review-empty-main { display: flex; flex-direction: column; align-items: flex-start; justify-content: center; padding: 4px 8px; }
.df-review-empty-icon { display: grid; place-items: center; width: 42px; height: 42px; margin-bottom: 11px; border-radius: 12px; background: linear-gradient(145deg,#E7F1FF,#D6E8FF); color: #1769E0; font-size: 20px; box-shadow: 0 4px 12px rgba(23,105,224,.09); }
.df-review-empty-kicker { margin-bottom: 6px; color: #3977C8; font-size: 9px; font-weight: 800; letter-spacing: .16em; }
.df-review-empty-main > strong { color: var(--df-text); font-size: 17px; }
.df-review-empty-main > p { max-width: 430px; margin: 6px 0 13px; color: var(--df-text2); font-size: 11px; line-height: 1.65; }
.df-review-empty-count { display: flex; align-items: baseline; gap: 8px; padding-top: 10px; border-top: 1px solid #DDE7F4; width: 100%; color: var(--df-text3); font-size: 10px; }
.df-review-empty-count b { color: #1769E0; font-size: 24px; line-height: 1; }
.df-review-route { display: flex; align-items: center; justify-content: space-between; gap: 11px; padding: 15px; border: 1px solid rgba(192,212,240,.9); border-radius: 12px; background: rgba(255,255,255,.74); }
.df-review-route-title { position: absolute; transform: translateY(-34px); color: var(--df-text2); font-size: 11px; font-weight: 700; }
.df-review-route > div { display: flex; align-items: center; gap: 9px; min-width: 0; }
.df-review-route > div > b { display: grid; place-items: center; width: 31px; height: 31px; flex: 0 0 auto; border-radius: 9px; background: #E8F1FF; color: #1769E0; font-size: 10px; }
.df-review-route > div:nth-of-type(2) > b { background: #E6F7EF; color: #14805D; }
.df-review-route > div:nth-of-type(3) > b { background: #F1ECFC; color: #7350C8; }
.df-review-route strong { display: block; color: var(--df-text); font-size: 11px; white-space: nowrap; }
.df-review-route small { display: block; margin-top: 3px; color: var(--df-text3); font-size: 9px; line-height: 1.4; }
.df-review-route > i { color: #8AA9D0; font-size: 19px; font-style: normal; }
.df-review-panel-title { display: flex; align-items: center; gap: 10px; min-height: 42px; padding-bottom: 12px; margin-bottom: 10px; border-bottom: 1px solid #E8EEF6; }
.df-review-panel-title > b { display: grid; place-items: center; width: 33px; height: 33px; flex: 0 0 auto; border-radius: 9px; background: #E9F2FF; color: #1769E0; font-size: 17px; }
.df-review-panel-title strong { display: block; color: var(--df-text); font-size: 14px; line-height: 1.25; }
.df-review-panel-title small { display: block; margin-top: 3px; color: var(--df-text3); font-size: 10px; line-height: 1.35; }
[data-testid="stApp"] [class*="st-key-df-review-queue-"] [data-testid="stRadio"] [role="radiogroup"] { display: grid; gap: 7px; max-height: 560px; overflow: auto; }
[data-testid="stApp"] [class*="st-key-df-review-queue-"] [data-testid="stRadio"] label { width: 100%; align-items: flex-start; padding: 10px 11px; border: 1px solid #E4ECF6; border-radius: 9px; background: #fff; color: var(--df-text2); font-size: 11px; line-height: 1.45; overflow-wrap: anywhere; transition: background .15s ease, border-color .15s ease; }
[data-testid="stApp"] [class*="st-key-df-review-queue-"] [data-testid="stRadio"] label:hover { border-color: #B8D3F5; background: #F7FAFF; }
[data-testid="stApp"] [class*="st-key-df-review-queue-"] [data-testid="stRadio"] label:has(input:checked) { border-color: #76A9EC; background: #EAF3FF; color: #0B59BB; font-weight: 650; box-shadow: 0 2px 8px rgba(32,104,198,.08); }
.df-review-record-meta { display: flex; flex-wrap: wrap; gap: 7px; margin: 0 0 12px; }
.df-review-record-meta span { padding: 4px 8px; border: 1px solid #DCE7F5; border-radius: 999px; background: #F8FBFF; color: var(--df-text3); font-size: 10px; }
.df-review-record-meta span:first-child { background: #E9F2FF; border-color: #D8E8FC; color: #125EBF; font-weight: 700; }
.df-review-sample { max-height: 650px; overflow: auto; scrollbar-gutter: stable; }
.df-review-release-ready { display:flex; gap:9px; align-items:center; padding:11px; margin:12px 0 8px; border:1px solid #BFE6D5; border-radius:9px; background:#F1FBF6; }
.df-review-release-ready > span { display:grid; place-items:center; width:28px; height:28px; border-radius:50%; background:#17A574; color:#fff; }
.df-review-release-ready strong { display:block; color:#166447; font-size:12px; }
.df-review-release-ready small { display:block; margin-top:3px; color:#56756A; font-size:10px; overflow-wrap:anywhere; }
@container (min-width:500px) {
  .df-review-sample .df-artifact-turn-columns { grid-template-columns: repeat(2,minmax(0,1fr)); }
}
.df-review-dialogue { max-height: 610px; overflow: auto; padding: 17px; border: 1px solid #E2EAF4; border-radius: 10px; background: #FCFDFF; }
.df-review-dialogue .bubbles { gap: 13px; }
.df-review-dialogue .bubbles .bub-user, .df-review-prompt .bubbles .bub-user { max-width: 88%; border-radius: 14px 5px 14px 14px; }
.df-review-dialogue .bubbles .bub-assistant, .df-review-prompt .bubbles .bub-assistant { align-self: flex-start; max-width: 96%; padding: 10px 12px; border: 1px solid #E4ECF6; border-radius: 5px 13px 13px; background: #fff; }
.df-review-dialogue .bubbles .role-tag, .df-review-prompt .bubbles .role-tag, .df-review-compare .bubbles .role-tag { display: block; margin-bottom: 5px; font-family: inherit; font-size: 10px; }
.df-review-facts { display: grid; gap: 0; }
.df-review-facts > div { display: grid; grid-template-columns: 85px minmax(0,1fr); gap: 10px; padding: 8px 0; border-bottom: 1px solid #EDF1F6; }
.df-review-facts span { color: var(--df-text3); font-size: 10px; }
.df-review-facts strong { overflow-wrap: anywhere; color: var(--df-text2); font-size: 11px; font-weight: 500; line-height: 1.5; }
.df-review-history { display: grid; gap: 4px; margin-bottom: 12px; padding: 11px 12px; border: 1px solid #E4ECF6; border-radius: 9px; background: #F8FAFE; }
.df-review-history b { color: var(--df-text); font-size: 11px; }
.df-review-history span { overflow-wrap: anywhere; color: var(--df-text3); font-size: 10px; }
.df-review-history p { margin: 2px 0 0; color: var(--df-text2); font-size: 10px; line-height: 1.5; overflow-wrap: anywhere; }
.df-review-progress-title { display: flex; justify-content: space-between; gap: 6px; margin: 18px 0 7px; color: var(--df-text); font-size: 12px; }
.df-review-progress-title span { color: var(--df-brand); font-weight: 700; }
.df-review-mini-stats { display: flex; justify-content: space-between; gap: 8px; padding-top: 9px; border-top: 1px solid #EDF1F6; color: var(--df-text3); font-size: 10px; }
.df-review-mini-stats b { margin-left: 4px; color: var(--df-text); font-size: 11px; }
.df-review-edit-head { display: grid; gap: 3px; margin: 16px 0 10px; padding-top: 13px; border-top: 1px solid #E6EDF5; }
.df-review-edit-head strong { color: var(--df-text); font-size: 13px; }
.df-review-edit-head small { color: var(--df-text3); font-size: 10px; }
.df-review-prompt { max-height: 235px; overflow: auto; margin-bottom: 13px; padding: 12px; border: 1px solid #DDE8F6; border-radius: 10px; background: #F8FBFF; }
.df-review-prompt-label { margin-bottom: 9px; color: #125EBF; font-size: 11px; font-weight: 700; }
.df-review-prompt .bubbles { gap: 9px; }
.df-review-compare { display: grid; grid-template-columns: repeat(auto-fit,minmax(min(100%,280px),1fr)); gap: 11px; }
.df-review-compare-card { min-width: 0; overflow: hidden; border: 1px solid #D5EADF; border-radius: 11px; background: #FBFEFC; }
.df-review-compare-card.rejected { border-color: #F0DBE0; background: #FFFBFC; }
.df-review-compare-card header { display: flex; gap: 8px; align-items: center; min-height: 53px; padding: 10px 12px; border-bottom: 1px solid #DFEEE5; }
.df-review-compare-card.rejected header { border-bottom-color: #F3E5E9; }
.df-review-compare-card header b { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 8px; background: #E4F7EC; color: #13805B; font-size: 12px; }
.df-review-compare-card.rejected header b { background: #FCEBED; color: #B02E45; }
.df-review-compare-card header strong { display: block; color: var(--df-text); font-size: 12px; }
.df-review-compare-card header small { display: block; margin-top: 2px; color: var(--df-text3); font-size: 9px; }
.df-review-compare-card > .bubbles { max-height: 405px; min-height: 108px; overflow: auto; padding: 13px; gap: 10px; }
.df-review-compare-card .bub-assistant { align-self: stretch; max-width: 100%; padding: 10px 11px; border: 1px solid #E5EDF6; border-radius: 8px; background: #fff; }
.df-review-corpus-card { overflow: hidden; margin: 0 0 12px; border: 1px solid #D5EADF; border-radius: 11px; background: #FBFEFC; }
.df-review-corpus-card.original { border-color: #DCE8F7; background: #F9FBFF; }
.df-review-corpus-card header { display: flex; justify-content: space-between; gap: 8px; align-items: center; padding: 11px 13px; border-bottom: 1px solid #E4ECF6; }
.df-review-corpus-card header b { color: var(--df-text); font-size: 12px; }
.df-review-corpus-card header span { color: var(--df-text3); font-size: 10px; }
.df-review-corpus-text { max-height: 430px; overflow: auto; padding: 16px; color: var(--df-text2); font-size: 12px; line-height: 1.75; white-space: pre-wrap; overflow-wrap: anywhere; }
.df-review-evidence-title { display: grid; gap: 3px; margin: 15px 0 6px; padding-top: 13px; border-top: 1px solid #E6EDF5; }
.df-review-evidence-title strong { color: var(--df-text); font-size: 12px; }
.df-review-evidence-title small { color: var(--df-text3); font-size: 10px; }
@media (max-width: 1250px) {
  .df-review-compare { grid-template-columns: 1fr; }
}
.df-overview-flow { gap: 8px; }
.df-overview-step { min-width: 0; flex: 1 1 0; }
.df-backend-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 12px; }
.df-backend-card { display: grid; gap: 9px; min-width: 0; padding: 15px; border: 1px solid #E0E9F5; border-radius: 11px; background: #fff; box-shadow: 0 2px 9px rgba(30,60,100,.03); }
.df-backend-head { display: flex; align-items: center; gap: 8px; min-width: 0; }
.df-backend-head strong { overflow: hidden; color: var(--df-text); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.df-backend-icon { display: grid; place-items: center; width: 31px; height: 31px; flex: 0 0 auto; border-radius: 9px; background: #EAF2FF; color: var(--df-brand); font-size: 14px; }
.df-backend-default { margin-left: auto; padding: 3px 7px; border-radius: 999px; background: #E7F6EF; color: #16805C; font-size: 9px; font-weight: 700; }
.df-backend-models { overflow: hidden; color: var(--df-text); font-size: 12px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.df-backend-address { overflow: hidden; color: var(--df-text3); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.df-backend-foot { display: flex; justify-content: space-between; gap: 10px; padding-top: 9px; border-top: 1px solid #EBF0F6; color: var(--df-text3); font-size: 12px; }
.df-backend-foot span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.df-backend-foot span:last-child { flex: 0 0 auto; color: #16805C; font-weight: 650; }
.df-role-heading { display: flex; align-items: center; gap: 9px; }
.df-role-heading > span { display: grid; place-items: center; width: 31px; height: 31px; border-radius: 9px; background: #EDEAFF; color: #6754C2; }
.df-role-heading strong { display: block; color: var(--df-text); font-size: 12px; }
.df-role-heading small { display: block; margin-top: 2px; color: var(--df-text3); font-size: 9px; }
.df-gate-head { display: flex; align-items: center; gap: 9px; min-height: 46px; padding-bottom: 12px; border-bottom: 1px solid #E9EFF6; }
.df-gate-icon { display: grid; place-items: center; width: 32px; height: 32px; flex: 0 0 auto; border-radius: 9px; background: #EAF2FF; color: var(--df-brand); font-size: 10px; font-weight: 800; }
.df-gate-head > div { min-width: 0; }
.df-gate-head strong { display: block; color: var(--df-text); font-size: 12px; }
.df-gate-head small { display: block; margin-top: 3px; color: var(--df-text3); font-size: 9px; line-height: 1.35; }
.df-gate-head b { flex: 0 0 auto; margin-left: auto; padding: 4px 6px; border-radius: 999px; background: #EAF2FF; color: var(--df-brand); font-size: 9px; white-space: nowrap; }
.df-gate-head b[data-status="approved"] { background: #E8F7EF; color: #13805B; }
.df-gate-head b[data-status="rejected"] { background: #FDECEF; color: #A8273E; }
.df-trainer-row { display: grid; grid-template-columns: 65px 110px 75px minmax(0,1fr); align-items: center; gap: 10px; padding: 9px 0; border-top: 1px solid #EBF0F6; }
.df-trainer-row strong { color: var(--df-text); font-size: 11px; }
.df-trainer-row > span:not(.df-artifact-badge) { color: var(--df-text2); font-size: 10px; }
.df-trainer-row small { overflow: hidden; color: var(--df-text3); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.df-pref-overview { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 11px; margin: 0 0 15px; }
.df-pref-overview > div { display: grid; gap: 5px; min-height: 91px; padding: 15px; border: 1px solid #E0E9F5; border-radius: 11px; background: linear-gradient(145deg,#fff,#F8FBFF); }
.df-pref-overview span { color: var(--df-text3); font-size: 10px; }
.df-pref-overview strong { color: var(--df-text); font-size: 22px; line-height: 1.1; }
.df-pref-overview small { color: var(--df-text3); font-size: 9px; }
.df-pref-overview > div:nth-child(3) strong { color: #16845E; font-size: 17px; }
.df-pref-overview > div:nth-child(4) strong { color: #7350C8; font-size: 17px; }
.df-pref-style-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 11px; }
.df-pref-style { display: grid; grid-template-columns: 32px 1fr auto; column-gap: 9px; align-items: center; padding: 15px; border: 1px solid #E0E9F5; border-radius: 11px; background: #FBFDFF; }
.df-pref-style-icon { display: grid; place-items: center; grid-row: span 2; width: 32px; height: 32px; border-radius: 9px; background: #F0ECFC; color: #7350C8; }
.df-pref-style strong { color: var(--df-text); font-size: 12px; }
.df-pref-style b { color: #7350C8; font-size: 12px; }
.df-pref-style p { grid-column: 2 / -1; margin: 4px 0 0; color: var(--df-text3); font-size: 10px; line-height: 1.55; }
.df-pref-rule-list { display: grid; gap: 8px; }
.df-pref-rule-list > div { display: flex; align-items: flex-start; gap: 9px; padding: 11px 12px; border: 1px solid #E3EBF5; border-radius: 9px; background: #FBFDFF; }
.df-pref-rule-list b { color: #1769E0; font-size: 10px; }
.df-pref-rule-list span { color: var(--df-text2); font-size: 11px; line-height: 1.55; }
[data-testid="stForm"] [data-testid="stVerticalBlock"] { gap: .7rem; }
[data-testid="stFileUploader"] section { border: 1px dashed #B8CBE1; border-radius: 11px; background: #FBFDFF; }
[data-testid="stFileUploader"] section:hover { border-color: var(--df-brand); background: #F5F9FF; }
[data-testid="stDataFrame"] { background: #fff; }
@media (max-width: 980px) {
  .df-page-hero { align-items: flex-start; flex-direction: column; gap: 7px; padding: 16px 18px; }
  .df-page-tag { white-space: normal; }
  .df-review-empty { grid-template-columns: 1fr; gap: 17px; }
  .df-review-route { flex-wrap: wrap; justify-content: flex-start; }
  .df-review-route-title { position: static; transform: none; flex-basis: 100%; }
  .df-review-compare { grid-template-columns: 1fr; }
  .df-workflow-targets { grid-template-columns: repeat(2,minmax(0,1fr)); }
  .df-backend-grid { grid-template-columns: 1fr; }
  .df-pref-overview { grid-template-columns: repeat(2,minmax(0,1fr)); }
  .df-pref-style-grid { grid-template-columns: 1fr; }
  .df-trainer-row { grid-template-columns: 65px 1fr 75px; }
  .df-trainer-row small { grid-column: 2 / -1; }
  .df-artifact-compare { grid-template-columns: 1fr; }
  [data-testid="stApp"] [role="radiogroup"][aria-label="选择来源类型"] { flex-direction: column; }
  .df-page-hero .df-hero-art { inset: 0 0 0 auto; opacity: .35; }
  .df-asset-head, .df-asset-row { grid-template-columns: minmax(150px,1.5fr) minmax(60px,.5fr) minmax(90px,1fr); }
  .df-asset-head span:nth-child(3), .df-asset-row > span:nth-child(3) { display: none; }
  .df-overview-flow { align-items: stretch; flex-direction: column; }
  .df-overview-flow > span { transform: rotate(90deg); align-self: center; }
}
""")

CSS = _TEMPLATE.substitute(theme_vars=_token_block(LIGHT_TOKENS), **LIGHT_TOKENS)
