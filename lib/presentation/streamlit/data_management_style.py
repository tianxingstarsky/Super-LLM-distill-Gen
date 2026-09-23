"""Scoped visual language for the workspace data library."""

DATA_MANAGEMENT_STYLE = """<style>
.df-data-stats { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:11px; margin:2px 0 13px; }
.df-data-stat { display:grid; grid-template-columns:35px minmax(0,1fr); align-content:center; gap:2px 10px; min-height:87px; padding:13px 14px; border:1px solid #DFE8F5; border-radius:11px; background:linear-gradient(145deg,#fff,#F8FBFF); box-shadow:0 2px 9px rgba(30,70,120,.035); }
.df-data-stat > b { grid-row:span 2; display:grid; place-items:center; width:35px; height:35px; border-radius:9px; background:#EAF2FF; color:#1769D2; font-size:15px; }
.df-data-stat:nth-child(2) > b { background:#E9F7F0; color:#16805C; }
.df-data-stat:nth-child(3) > b { background:#F1ECFC; color:#7350C8; }
.df-data-stat:nth-child(4) > b { background:#FFF2E7; color:#B66B27; }
.df-data-stat span { color:#73859B; font-size:11px; }
.df-data-stat strong { color:#1D304B; font-size:22px; line-height:1.08; }
.df-data-list { max-height:610px; overflow:auto; border:1px solid #E1EAF5; border-radius:9px; background:#fff; }
.df-data-list-head, .df-data-list-row { display:grid; grid-template-columns:minmax(0,1.7fr) 66px minmax(0,.9fr) 72px; align-items:center; gap:9px; padding:9px 11px; }
.df-data-list-head { position:sticky; top:0; z-index:1; background:#F4F7FC; color:#8292A6; font-size:10px; font-weight:700; }
.df-data-list-row { min-height:43px; border-top:1px solid #ECF1F7; color:#61738B; font-size:10px; }
.df-data-list-row[data-selected="true"] { background:#EEF6FF; box-shadow:inset 3px 0 #2780E5; }
.df-data-file { display:flex; align-items:center; min-width:0; gap:8px; color:#263B54; font-size:11px; font-weight:650; }
.df-data-file span:last-child { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.df-data-file b { display:grid; place-items:center; width:28px; height:28px; flex:0 0 auto; border-radius:7px; background:#EAF2FF; color:#1769D2; font-size:8px; }
.df-data-file b[data-ext="JSONL"] { background:#E7F1FF; }
.df-data-file b[data-ext="PDF"], .df-data-file b[data-ext="DOCX"] { background:#FDEDEE; color:#C94554; }
.df-data-file b[data-ext="MD"], .df-data-file b[data-ext="TXT"] { background:#F0ECFC; color:#7450BF; }
.df-data-file b[data-ext="CSV"], .df-data-file b[data-ext="XLSX"] { background:#E7F7EF; color:#16805C; }
.df-data-list-row > span { overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
.df-data-detail { display:grid; gap:0; border:1px solid #E2EAF5; border-radius:9px; background:#FBFDFF; }
.df-data-detail > div { display:grid; grid-template-columns:68px minmax(0,1fr); gap:8px; padding:9px 11px; border-top:1px solid #E9EFF6; }
.df-data-detail > div:first-child { border-top:0; }
.df-data-detail span { color:#8191A4; font-size:10px; }
.df-data-detail strong { overflow-wrap:anywhere; color:#263B54; font-size:11px; font-weight:600; }
.df-data-origin { display:inline-block; width:fit-content; padding:3px 7px; border-radius:999px; background:#E8F7F0; color:#16805C !important; font-size:9px !important; }
.df-data-origin[data-origin="source"] { background:#EAF2FF; color:#1769D2 !important; }
.df-data-note { margin:7px 0 0; color:#8191A4; font-size:10px; line-height:1.55; }
.df-data-sample-head { display:flex; align-items:center; justify-content:space-between; gap:9px; margin:1px 0 13px; padding:11px 13px; border:1px solid #DFEAF7; border-radius:9px; background:#F8FBFF; }
.df-data-sample-head strong { color:#213650; font-size:12px; }
.df-data-sample-head span { color:#72849B; font-size:10px; }
.df-data-sample-facts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin:0 0 12px; }
.df-data-sample-facts > div { display:grid; gap:3px; padding:10px; border:1px solid #E2EAF5; border-radius:9px; background:#FBFDFF; }
.df-data-sample-facts span { color:#8292A6; font-size:10px; }
.df-data-sample-facts b { overflow-wrap:anywhere; color:#253B55; font-size:12px; }
@media(max-width:1080px) { .df-data-stats { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media(max-width:720px) { .df-data-stats { grid-template-columns:1fr 1fr; } .df-data-list-head,.df-data-list-row { grid-template-columns:minmax(0,1fr) 58px 60px; } .df-data-list-head span:nth-child(3),.df-data-list-row > span:nth-child(3) { display:none; } }
</style>"""
