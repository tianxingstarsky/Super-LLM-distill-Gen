"""Scoped visual language for the workspace data library."""

DATA_MANAGEMENT_STYLE = """<style>
.df-data-stats { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:0; padding:17px 0; margin:0; border:1px solid #D6E3F2; border-radius:16px; background:linear-gradient(135deg,#FFF 18%,#F1F7FF); box-shadow:inset 0 1px 0 #FFF,0 1px 2px #264A7010,0 10px 24px -16px #315C8940; }
.st-key-asset-directory, .st-key-asset-details { min-width:0; padding:20px; border-radius:16px; border-color:#D6E2F0; box-shadow:var(--df-shadow-panel,0 2px 3px #27466E06,0 12px 32px -14px #27466E30,inset 0 1px 0 #FFF); }
.st-key-asset-directory { background:linear-gradient(150deg,#FFF 55%,#F4F8FE); }
.st-key-asset-details { background:radial-gradient(ellipse at 100% 0%,#E4F0FF99,transparent 55%),linear-gradient(150deg,#FFF 40%,#F3F8FE); }
.st-key-asset-directory [data-testid="stDataFrame"] { border:1px solid #DBE6F4; border-radius:10px; overflow:hidden; transition:border-color .15s ease,box-shadow .15s ease; }
.st-key-asset-directory [data-testid="stDataFrame"]:hover { border-color:#B4CDEE; }
.st-key-asset-directory [data-testid="stDataFrame"]:focus-within { border-color:#649BE1; box-shadow:0 0 0 3px #2378D812; }
.st-key-asset-empty { padding:25px; border:1px solid #D2E1F3; border-radius:16px; background:radial-gradient(ellipse at 95% 0,#DFEDFF,transparent 55%),linear-gradient(145deg,#FFF,#F1F7FF); box-shadow:var(--df-shadow-panel,0 2px 3px #27466E06,0 12px 32px -14px #27466E30,inset 0 1px 0 #FFF); }
.df-data-empty { display:flex; align-items:center; gap:15px; min-width:0; padding:4px 0; }
.df-data-empty > span { display:grid; place-items:center; width:46px; height:48px; flex:0 0 auto; border:1px solid #C6DBF7; border-radius:12px; background:linear-gradient(145deg,#FFF,#D8E8FD); box-shadow:inset 0 1px 0 #FFF,0 5px 9px #315C8921; color:#2773CE; font-size:24px; }
.df-data-empty > div { display:grid; gap:6px; min-width:0; }
.df-data-empty strong { color:#20354F; font-size:18px; line-height:1.4; }
.df-data-empty p { margin:0; color:#60758F; font-size:14px; line-height:1.65; }
.st-key-asset-details [data-testid="stCode"] pre { max-height:360px; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; }
.st-key-asset-details [data-testid="stCode"] code { font-size:13px; line-height:1.7; }
.df-data-stat { display:grid; grid-template-columns:30px minmax(0,1fr) auto; align-items:center; gap:10px; min-width:0; padding:0 18px; border-left:1px solid #E8EEF6; }
.df-data-stat:first-child { border-left:0; }
.df-data-stat > b { display:grid; place-items:center; width:30px; height:30px; border:1px solid #D0E0F5; border-radius:10px; background:linear-gradient(145deg,#F8FCFF,#DCE9FB); box-shadow:inset 0 1px 0 #FFF,0 3px 4px #315C8917; color:#1769D2; font-size:16px; line-height:1; }
.df-data-stat:nth-child(2) > b { border-color:#D0E9DD; background:linear-gradient(145deg,#F8FFFA,#DDEFE6); color:#16805C; }
.df-data-stat:nth-child(3) > b { border-color:#E0D5F6; background:linear-gradient(145deg,#FDFBFF,#E7DDF8); color:#7350C8; }
.df-data-stat:nth-child(4) > b { border-color:#F0DDCA; background:linear-gradient(145deg,#FFFCF7,#F8E4D1); color:#A75E23; }
.df-data-stat span { color:#52657B; font-size:13px; line-height:1.5; overflow-wrap:anywhere; }
.df-data-stat strong { color:#1D304B; font-size:24px; line-height:1.3; font-variant-numeric:tabular-nums; letter-spacing:-.025em; }
.df-data-list { max-height:610px; overflow:auto; border:1px solid #D8E4F2; border-radius:9px; background:#FFF; box-shadow:inset 0 1px 3px #315C890C; }
.df-data-list-head, .df-data-list-row { display:grid; grid-template-columns:minmax(0,1.7fr) 70px minmax(0,.9fr) 76px; align-items:center; gap:10px; padding:11px 13px; }
.df-data-list-head { position:sticky; top:0; z-index:1; background:linear-gradient(180deg,#F9FCFF,#EDF3FB); box-shadow:inset 0 1px 0 #FFF,0 1px 2px #315C8910; color:#4D627E; font-size:12px; font-weight:600; }
.df-data-list-row { min-height:48px; border-top:1px solid #ECF1F7; color:#52657B; font-size:12px; }
.df-data-list-row[data-selected="true"] { background:linear-gradient(100deg,#E4F0FF,#F5F9FF); box-shadow:inset 3px 0 #2780E5,inset 0 1px 0 #FFF; }
.df-data-file { display:flex; align-items:center; min-width:0; gap:10px; color:#263B54; font-size:13px; font-weight:600; }
.df-data-file span:last-child { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.df-data-file b { display:grid; place-items:center; width:32px; height:34px; flex:0 0 auto; border:1px solid #FFFFFFC9; border-radius:8px; background:#EAF2FF; box-shadow:inset 0 1px 0 #FFF,0 2px 3px #315C8917; color:#1769D2; font-size:10px; }
.df-data-file b[data-ext="JSONL"] { background:#E7F1FF; }
.df-data-file b[data-ext="PDF"], .df-data-file b[data-ext="DOCX"] { background:#FDEDEE; color:#C94554; }
.df-data-file b[data-ext="MD"], .df-data-file b[data-ext="TXT"] { background:#F0ECFC; color:#7450BF; }
.df-data-file b[data-ext="CSV"], .df-data-file b[data-ext="XLSX"] { background:#E7F7EF; color:#16805C; }
.df-data-list-row > span { overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
.df-data-detail { display:grid; gap:0; border:1px solid #DCE6F3; border-radius:12px; background:linear-gradient(145deg,#F9FCFF,#ECF3FC); box-shadow:inset 0 1px 0 #FFF; overflow:hidden; }
.df-data-detail > div { display:grid; grid-template-columns:82px minmax(0,1fr); gap:10px; padding:11px 13px; border-top:1px solid #E4EBF4; }
.df-data-detail > div:first-child { border-top:0; }
.df-data-detail span { color:#5C6F86; font-size:12px; }
.df-data-detail strong { overflow-wrap:anywhere; color:#263B54; font-size:14px; font-weight:600; line-height:1.55; }
.df-data-origin { display:inline-block; width:fit-content; padding:4px 8px; border-radius:6px; background:#E8F7F0; color:#16805C !important; font-size:12px !important; }
.df-data-origin[data-origin="source"] { background:#EAF2FF; color:#1769D2 !important; }
.df-data-selected-head { display:flex; align-items:flex-start; gap:12px; min-width:0; padding:2px 0 14px; }
.df-data-selected-head > b { display:grid; place-items:center; flex:0 0 42px; height:46px; border:1px solid #C9DDF7; border-radius:12px; background:linear-gradient(145deg,#FFF,#D7E7FC); box-shadow:inset 0 1px 0 #FFF,0 4px 6px #315C891D; color:#1E6ACA; font-size:11px; }
.df-data-selected-head > b:is([data-ext="PDF"],[data-ext="DOCX"]) { background:linear-gradient(145deg,#FFF7F5,#FFE8E4); border-color:#F1D7D2; color:#B04D44; }
.df-data-selected-head > div { display:grid; gap:8px; min-width:0; }
.df-data-selected-head strong { color:#233A56; font-size:17px; font-weight:650; line-height:1.5; overflow-wrap:anywhere; }
.df-data-file-facts { display:grid; grid-template-columns:minmax(70px,.65fr) minmax(0,1.35fr); gap:10px; margin:0; padding:12px 13px; border:1px solid #DCE7F5; border-radius:10px; background:linear-gradient(145deg,#FFF,#EEF5FD); box-shadow:inset 0 1px 0 #FFF,0 2px 3px #315C890B; }
.df-data-file-facts > div { min-width:0; }
.df-data-file-facts dt { margin-bottom:5px; color:#60718A; font-size:12px; line-height:1.4; }
.df-data-file-facts dd { margin:0; color:#263D58; font-size:13px; font-weight:600; line-height:1.5; overflow-wrap:anywhere; font-variant-numeric:tabular-nums; }
.df-data-file-location { display:grid; gap:5px; min-width:0; margin:10px 0 14px; padding:0 2px; }
.df-data-file-location > span { color:#60718A; font-size:12px; }
.df-data-file-location strong { color:#536B89; font-size:12px; font-weight:500; line-height:1.65; overflow-wrap:anywhere; }
.df-data-note { margin:9px 0 0; color:#5C6F86; font-size:12px; line-height:1.65; }
.df-data-sample-head { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:9px; margin:1px 0 13px; padding:13px 15px; border:1px solid #D1E1F5; border-radius:12px; background:linear-gradient(125deg,#FAFDFF,#E4EFFC); box-shadow:inset 0 1px 0 #FFF,0 3px 6px #315C8912; }
.df-data-sample-head strong { flex:1 1 180px; min-width:0; overflow-wrap:anywhere; color:#213650; font-size:15px; line-height:1.5; }
.df-data-sample-head span { flex:0 0 auto; color:#526B8B; font-size:12px; }
.df-data-sample-facts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin:0 0 12px; }
.df-data-sample-facts > div { display:grid; align-content:start; gap:5px; padding:11px 12px; border:1px solid #E0E9F4; border-radius:10px; background:linear-gradient(145deg,#FCFEFF,#EEF4FC); box-shadow:inset 0 1px 0 #FFF,0 1px 2px #315C890A; }
.df-data-sample-facts span { color:#5C6F86; font-size:12px; }
.df-data-sample-facts b { overflow-wrap:anywhere; color:#253B55; font-size:14px; }
@media(max-width:1000px) { .df-data-stats { grid-template-columns:repeat(2,minmax(0,1fr)); row-gap:16px; } .df-data-stat:nth-child(3) { border-left:0; } }
@media(max-width:550px) { .st-key-asset-directory, .st-key-asset-details { padding:14px; } .df-data-stat { padding:0 12px; gap:7px; } .df-data-stat strong { font-size:21px; } }
@media(max-width:720px) { .df-data-stats { grid-template-columns:1fr 1fr; } .df-data-list-head,.df-data-list-row { grid-template-columns:minmax(0,1fr) 58px 60px; } .df-data-list-head span:nth-child(3),.df-data-list-row > span:nth-child(3) { display:none; } }
/* Stack only this page's native column groups; node inspectors keep their own layout. */
@media(max-width:700px) {
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] :is([class*="st-key-asset-category-"],.st-key-asset-directory,.df-data-sample-head,[class*="st-key-data-preview-run-"],[class*="st-key-preview-file-"])) {
    flex-direction:column !important; align-items:stretch !important; gap:14px !important;
  }
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] :is([class*="st-key-asset-category-"],.st-key-asset-directory,.df-data-sample-head,[class*="st-key-data-preview-run-"],[class*="st-key-preview-file-"])) > [data-testid="stColumn"] {
    flex:1 1 auto !important; width:100% !important; min-width:0 !important; max-width:100% !important;
  }
  [class*="st-key-asset-import-"] button { width:100%; }
  .st-key-asset-empty { padding:19px; }
}
@media(prefers-reduced-motion:reduce) { .st-key-asset-directory [data-testid="stDataFrame"] { transition:none; } }
</style>"""
