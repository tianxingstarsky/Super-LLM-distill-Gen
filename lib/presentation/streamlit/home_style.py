"""Homepage layout: one summary strip and compact, content-sized panels."""

HOME_STYLE = """<style>
.df-home-kpis { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:0; padding:12px 4px; border:1px solid #DAE5F2; border-radius:10px; background:#FFF; }
.df-home-kpi { display:flex; align-items:center; justify-content:space-between; gap:10px; min-width:0; padding:0 18px; border-left:1px solid #E8EEF6; }
.df-home-kpi:first-child { border-left:0; }
.df-home-kpi small { color:#65768C; font-size:13px; line-height:1.5; }
.df-home-kpi strong { color:#1A63C2; font-size:23px; line-height:1.3; }
.df-home-kpi[data-tone="green"] strong { color:#16845F; }
.df-home-kpi[data-tone="amber"] strong { color:#A56D13; }
.st-key-home-main [data-testid="stColumn"], .st-key-home-resources [data-testid="stColumn"] { min-width:0; }
.st-key-home-source-entries button { min-height:42px; border-color:#C8DCF3; background:#F4F8FF; color:#175BAC; }
.st-key-home-source-entries [data-testid="stColumn"]:nth-child(2) button { border-color:#CAE9DE; background:#F1FAF6; color:#167452; }
.st-key-home-source-entries [data-testid="stColumn"]:nth-child(3) button { border-color:#DFD5F5; background:#F7F4FD; color:#7250B1; }
.st-key-home-source-entries [data-testid="stCaptionContainer"] { overflow-wrap:anywhere; line-height:1.5; }
.st-key-home-strategy-options { padding-top:10px; border-top:1px solid #E8EEF6; }
.st-key-home-strategy-options button { min-height:34px; }
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
  .st-key-home-main > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"],
  .st-key-home-resources > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] { flex-wrap:wrap; }
  .st-key-home-main > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
  .st-key-home-resources > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { flex:1 1 100%; width:100%; min-width:0; }
}
@media(max-width:600px) {
  .df-home-kpis { grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px 0; }
  .df-home-kpi { padding:0 12px; }
  .df-home-kpi:nth-child(3) { border-left:0; }
}
</style>"""
