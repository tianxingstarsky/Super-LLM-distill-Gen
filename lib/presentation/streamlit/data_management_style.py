"""Scoped visual language for the workspace data library."""

DATA_MANAGEMENT_STYLE = """<style>
.df-data-stats { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:0; padding:17px 0; margin:0; border:1px solid #E1E8F2; border-radius:16px; background:#FFF; box-shadow:0 4px 20px #1A3E6D06; }
.st-key-asset-directory, .st-key-asset-details { min-width:0; padding:20px; border-radius:16px; border-color:#E1E8F2; box-shadow:0 4px 20px #1A3E6D05; }
.st-key-asset-directory { background:#FFF; }
.st-key-asset-details { background:#FCFDFF; }
.st-key-asset-directory [data-testid="stDataFrame"] { border:1px solid #DBE6F4; border-radius:9px; overflow:hidden; }
.df-data-stat { display:grid; grid-template-columns:30px minmax(0,1fr) auto; align-items:center; gap:10px; min-width:0; padding:0 18px; border-left:1px solid #E8EEF6; }
.df-data-stat:first-child { border-left:0; }
.df-data-stat > b { display:grid; place-items:center; width:30px; height:30px; border-radius:10px; background:#EAF2FF; color:#1769D2; font-size:16px; line-height:1; }
.df-data-stat:nth-child(2) > b { background:#E9F7F0; color:#16805C; }
.df-data-stat:nth-child(3) > b { background:#F1ECFC; color:#7350C8; }
.df-data-stat:nth-child(4) > b { background:#FFF2E7; color:#B66B27; }
.df-data-stat span { color:#52657B; font-size:13px; line-height:1.5; overflow-wrap:anywhere; }
.df-data-stat strong { color:#1D304B; font-size:24px; line-height:1.3; font-variant-numeric:tabular-nums; letter-spacing:-.025em; }
.df-data-list { max-height:610px; overflow:auto; border:1px solid #E1EAF5; border-radius:9px; background:#fff; }
.df-data-list-head, .df-data-list-row { display:grid; grid-template-columns:minmax(0,1.7fr) 70px minmax(0,.9fr) 76px; align-items:center; gap:10px; padding:11px 13px; }
.df-data-list-head { position:sticky; top:0; z-index:1; background:#F6F8FC; color:#5C6F86; font-size:12px; font-weight:600; }
.df-data-list-row { min-height:48px; border-top:1px solid #ECF1F7; color:#52657B; font-size:12px; }
.df-data-list-row[data-selected="true"] { background:#EEF6FF; box-shadow:inset 3px 0 #2780E5; }
.df-data-file { display:flex; align-items:center; min-width:0; gap:10px; color:#263B54; font-size:13px; font-weight:600; }
.df-data-file span:last-child { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.df-data-file b { display:grid; place-items:center; width:32px; height:34px; flex:0 0 auto; border-radius:8px; background:#EAF2FF; color:#1769D2; font-size:10px; }
.df-data-file b[data-ext="JSONL"] { background:#E7F1FF; }
.df-data-file b[data-ext="PDF"], .df-data-file b[data-ext="DOCX"] { background:#FDEDEE; color:#C94554; }
.df-data-file b[data-ext="MD"], .df-data-file b[data-ext="TXT"] { background:#F0ECFC; color:#7450BF; }
.df-data-file b[data-ext="CSV"], .df-data-file b[data-ext="XLSX"] { background:#E7F7EF; color:#16805C; }
.df-data-list-row > span { overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
.df-data-detail { display:grid; gap:0; border:0; border-radius:12px; background:#F4F7FC; overflow:hidden; }
.df-data-detail > div { display:grid; grid-template-columns:82px minmax(0,1fr); gap:10px; padding:11px 13px; border-top:1px solid #E4EBF4; }
.df-data-detail > div:first-child { border-top:0; }
.df-data-detail span { color:#5C6F86; font-size:12px; }
.df-data-detail strong { overflow-wrap:anywhere; color:#263B54; font-size:13px; font-weight:600; }
.df-data-origin { display:inline-block; width:fit-content; padding:4px 8px; border-radius:6px; background:#E8F7F0; color:#16805C !important; font-size:11px !important; }
.df-data-origin[data-origin="source"] { background:#EAF2FF; color:#1769D2 !important; }
.df-data-note { margin:9px 0 0; color:#5C6F86; font-size:12px; line-height:1.65; }
.df-data-sample-head { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:9px; margin:1px 0 13px; padding:13px 15px; border:0; border-radius:12px; background:#EFF5FD; }
.df-data-sample-head strong { color:#213650; font-size:15px; }
.df-data-sample-head span { color:#526B8B; font-size:12px; }
.df-data-sample-facts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin:0 0 12px; }
.df-data-sample-facts > div { display:grid; gap:5px; padding:12px; border:0; border-radius:10px; background:#F5F8FC; }
.df-data-sample-facts span { color:#5C6F86; font-size:12px; }
.df-data-sample-facts b { overflow-wrap:anywhere; color:#253B55; font-size:14px; }
@media(max-width:1000px) { .df-data-stats { grid-template-columns:repeat(2,minmax(0,1fr)); row-gap:16px; } .df-data-stat:nth-child(3) { border-left:0; } }
@media(max-width:550px) { .st-key-asset-directory, .st-key-asset-details { padding:14px; } .df-data-stat { padding:0 12px; gap:7px; } .df-data-stat strong { font-size:21px; } }
@media(max-width:720px) { .df-data-stats { grid-template-columns:1fr 1fr; } .df-data-list-head,.df-data-list-row { grid-template-columns:minmax(0,1fr) 58px 60px; } .df-data-list-head span:nth-child(3),.df-data-list-row > span:nth-child(3) { display:none; } }
</style>"""
