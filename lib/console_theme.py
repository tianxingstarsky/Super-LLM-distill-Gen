"""Scoped application chrome; conversation rendering keeps the DSH-inspired renderer."""
CSS = """
body {max-width:none;margin:0;padding:0;}
[data-testid="stApp"] {background:#111315;color:#e8eaed;}
[data-testid="stHeader"] {background:transparent;}
[data-testid="stToolbar"] {visibility:hidden;}
[data-testid="stSidebar"] {background:#191c20;border-right:1px solid #2a2e34;}
[data-testid="stMainBlockContainer"] {max-width:1520px;padding-top:2.2rem;padding-bottom:2rem;}
[data-testid="stSidebarUserContent"] {padding:1.6rem 1.1rem;}
h1 {font-size:1.7rem!important;font-weight:600!important;letter-spacing:-.03em;}
h2 {font-size:1.1rem!important;font-weight:600!important;}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {color:#b8c0cc;}
[data-testid="stButton"] button,[data-testid="stFormSubmitButton"] button {border-radius:9px;min-height:38px;border-color:#353b44;}
[data-testid="stButton"] button[kind="primary"],[data-testid="stFormSubmitButton"] button[kind="primary"] {background:#517be6;border-color:#517be6;}
[data-baseweb="input"],[data-baseweb="select"]>div,[data-baseweb="textarea"] {background:#1d2126;border-color:#353b44;border-radius:9px;}
[data-testid="stMetric"] {background:#191d22;border:1px solid #2b3038;border-radius:12px;padding:16px;}
[data-testid="stMetricValue"] {font-size:1.55rem;}
[data-testid="stForm"] {border:1px solid #2b3038;border-radius:12px;padding:18px;}
[data-testid="stSidebar"] [role="radiogroup"] {gap:5px;}
[data-testid="stSidebar"] label {padding:7px 9px;border-radius:8px;}
.df-brand {font-size:21px;font-weight:650;letter-spacing:-.04em;margin-bottom:6px;color:#f0f2f5;}
.df-kicker {font-size:11px;letter-spacing:.14em;color:#8f99a8;margin:0 0 25px;}
.df-empty {border:1px dashed #3b4350;background:#181c21;border-radius:14px;padding:32px;margin:18px 0;}
.df-empty h2 {font-size:20px!important;margin:0 0 12px;}
.df-empty p {color:#aeb7c5;line-height:1.8;}
"""
