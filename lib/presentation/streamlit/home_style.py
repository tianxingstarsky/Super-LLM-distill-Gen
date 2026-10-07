"""Homepage layout with illustrated entry points and readable work summaries."""

HOME_STYLE = """<style>
.df-home-kpis { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:0; padding:14px 4px; border:1px solid #DAE5F2; border-radius:11px; background:linear-gradient(110deg,#FFF,#F8FBFF); box-shadow:0 3px 12px rgba(46,85,136,.025); }
.df-home-kpi { display:flex; align-items:center; justify-content:space-between; gap:10px; min-width:0; padding:0 18px; border-left:1px solid #E8EEF6; }
.df-home-kpi:first-child { border-left:0; }
.df-home-kpi small { color:#65768C; font-size:13px; line-height:1.5; }
.df-home-kpi strong { color:#1A63C2; font-size:23px; line-height:1.3; }
.df-home-kpi[data-tone="green"] strong { color:#16845F; }
.df-home-kpi[data-tone="amber"] strong { color:#A56D13; }
.st-key-home-main [data-testid="stColumn"] { min-width:0; }
[data-testid="stMainBlockContainer"] .st-key-home-source-panel,
[data-testid="stMainBlockContainer"] .st-key-home-recent-panel,
[data-testid="stMainBlockContainer"] .st-key-home-library-panel,
[data-testid="stMainBlockContainer"] .st-key-home-results-panel { padding:16px; border-radius:13px; box-shadow:0 3px 13px rgba(46,85,136,.035); }
.st-key-home-entry-documents, .st-key-home-entry-agent, .st-key-home-entry-brief { position:relative; gap:5px; min-width:0; padding:12px; border:1px solid #D8E5F5; border-radius:11px; background:linear-gradient(145deg,#F1F7FF,#FBFDFF); color:#236BC7; transition:border-color .16s ease,box-shadow .16s ease; }
.st-key-home-entry-agent { border-color:#D7EAE4; background:linear-gradient(145deg,#EDF9F4,#FBFEFC); color:#148368; }
.st-key-home-entry-brief { border-color:#E5DDF5; background:linear-gradient(145deg,#F5F0FD,#FDFBFF); color:#8254C3; }
.st-key-home-entry-documents:hover, .st-key-home-entry-agent:hover, .st-key-home-entry-brief:hover { border-color:currentColor; box-shadow:0 4px 12px rgba(46,85,136,.07); }
.df-home-entry-art { display:flex; align-items:flex-start; justify-content:space-between; gap:6px; height:57px; color:inherit; pointer-events:none; }
.df-home-entry-art img { display:block; width:51px; height:54px; object-fit:contain; filter:drop-shadow(0 3px 3px rgba(55,102,160,.08)); }
.df-home-entry-art > span { display:grid; place-items:center; width:22px; height:22px; border:1px solid currentColor; border-radius:50%; font-size:14px; opacity:.5; }
.st-key-home-source-entries .stButton button { min-height:29px; padding:0; border:0; border-radius:4px; background:transparent; color:inherit; justify-content:flex-start; text-align:left; box-shadow:none; }
.st-key-home-source-entries .stButton button p { font-size:15px; font-weight:650; line-height:1.5; }
.st-key-home-source-entries .stButton button > div { width:100%; text-align:left; }
.st-key-home-source-entries [data-testid="stElementContainer"]:has(> .stButton) { position:static; }
.st-key-home-source-entries .stButton button::after { content:""; position:absolute; inset:0; z-index:1; border-radius:11px; }
.st-key-home-source-entries .stButton button:hover { background:rgba(255,255,255,.65); color:inherit; }
.st-key-home-source-entries .stButton button:focus-visible { outline:2px solid currentColor; outline-offset:3px; }
.st-key-home-source-entries [data-testid="stCaptionContainer"] { color:#64768B; overflow-wrap:anywhere; font-size:12px; line-height:1.6; }
.st-key-home-strategy-options { padding-top:11px; border-top:1px solid #E8EEF6; }
.st-key-home-strategy-options button { min-height:36px; height:100%; padding:6px 8px; }
.st-key-home-strategy-options button p { white-space:normal; line-height:1.45; font-size:12px; }
.df-home-task { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:start; gap:4px 8px; padding:5px 0; }
.df-home-task strong { color:#21344D; font-size:14px; line-height:1.5; overflow-wrap:anywhere; }
.df-home-task small { grid-column:1 / -1; color:#77879B; font-size:12px; line-height:1.5; overflow-wrap:anywhere; }
.df-home-task-status { padding:3px 7px; border-radius:5px; background:#EAF2FF; color:#1769E0; font-size:12px; line-height:1.5; white-space:nowrap; }
.df-home-task-status[data-status="completed"] { background:#E6F7EF; color:#16815B; }
.df-home-task-status[data-status="needs_attention"] { background:#FFF2DA; color:#976719; }
.df-home-task-status[data-status="failed"] { background:#FDECEF; color:#AC293E; }
.df-home-source-list { min-width:0; }
.df-home-source { display:grid; grid-template-columns:32px minmax(0,1fr) auto; align-items:center; gap:9px; padding:8px 0; border-bottom:1px solid #EBF0F6; }
.df-home-source:last-child { border-bottom:0; }
.df-home-source b { display:grid; place-items:center; width:29px; height:29px; border-radius:6px; background:#EAF2FF; color:#1769E0; font-size:9px; }
.df-home-source strong { overflow:hidden; color:#263950; font-size:13px; line-height:1.5; text-overflow:ellipsis; white-space:nowrap; }
.df-home-source small { color:#78889D; font-size:12px; white-space:nowrap; }
.df-home-summary { display:flex; justify-content:space-between; gap:10px; padding:6px 0; color:#64768D; font-size:13px; line-height:1.5; }
.df-home-summary b { color:#25466E; }
.df-home-empty { padding:12px 0; }
.df-home-empty strong { display:block; color:#29425F; font-size:14px; }
.df-home-empty small { display:block; margin-top:5px; color:#76879D; font-size:13px; line-height:1.6; overflow-wrap:anywhere; }
@media(max-width:1050px) {
  .st-key-home-main > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] { flex-wrap:wrap; }
  .st-key-home-main > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { flex:1 1 100%; width:100%; min-width:0; }
}
@media(max-width:600px) {
  .df-home-kpis { grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px 0; }
  .df-home-kpi { padding:0 12px; }
  .df-home-kpi:nth-child(3) { border-left:0; }
}
</style>"""
