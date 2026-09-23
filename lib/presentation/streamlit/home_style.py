"""Homepage visual treatment, kept separate from the shared console theme."""

HOME_STYLE = """<style>
.df-home-entry { min-height:169px; padding:16px 14px 11px; border:1px solid #E0E9F5; border-radius:11px; background:linear-gradient(155deg,#FFF 25%,#F4F9FF); transition:border-color .18s,box-shadow .18s; }
.df-home-entry:hover { border-color:#9EC4EF; box-shadow:0 7px 20px rgba(28,96,184,.08); }
.df-home-entry[data-kind="agent"] { background:linear-gradient(155deg,#FFF 25%,#F0FBF7); }
.df-home-entry[data-kind="brief"] { background:linear-gradient(155deg,#FFF 25%,#F8F5FF); }
.df-home-entry-icon { position:relative; display:grid; place-items:center; width:46px; height:46px; margin-bottom:12px; border-radius:11px; background:linear-gradient(150deg,#EAF3FF,#DCEBFF); color:#1769E0; }
.df-home-entry-icon i { display:block; width:23px; height:28px; border-radius:3px; background:#2377DE; clip-path:polygon(0 0,69% 0,100% 25%,100% 100%,0 100%); box-shadow:0 4px 8px rgba(31,105,203,.18); }
.df-home-entry-icon b { position:absolute; top:24px; left:14px; color:#fff; font-size:7px; line-height:1; letter-spacing:.02em; }
.df-home-entry[data-kind="agent"] .df-home-entry-icon { background:#DCF6E9; color:#14805B; }
.df-home-entry[data-kind="agent"] .df-home-entry-icon i { width:25px; height:21px; border-radius:7px; background:#18A97D; clip-path:none; }
.df-home-entry[data-kind="agent"] .df-home-entry-icon i:before { position:absolute; top:8px; left:22px; width:3px; height:7px; border-radius:3px; background:#18A97D; content:""; }
.df-home-entry[data-kind="agent"] .df-home-entry-icon b { top:22px; left:17px; font-size:10px; letter-spacing:5px; }
.df-home-entry[data-kind="brief"] .df-home-entry-icon { background:#EDE5FF; color:#7652C7; }
.df-home-entry[data-kind="brief"] .df-home-entry-icon i { width:26px; height:27px; border-radius:0; background:linear-gradient(135deg,#9C7DE8,#6F4BC3); clip-path:polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%); }
.df-home-entry[data-kind="brief"] .df-home-entry-icon b { top:18px; left:18px; font-size:16px; }
.df-home-entry strong { display:block; color:#1B2D45; font-size:14px; line-height:1.35; }
.df-home-entry p { min-height:37px; margin:7px 0 0; color:#64758B; font-size:11px; line-height:1.5; }
.df-home-entry em { display:block; margin-top:7px; color:#1769E0; font-size:10px; font-style:normal; font-weight:700; }
.df-home-entry[data-kind="agent"] em { color:#14805B; }
.df-home-entry[data-kind="brief"] em { color:#7652C7; }
.st-key-home-source-entries [data-testid="stColumn"] { min-width:0; }
.st-key-home-source-entries button { min-height:32px; font-size:11px; }
.df-home-strategy-intro { margin:-1px 0 13px; padding:9px 11px; border-radius:8px; background:#F4F8FE; color:#657991; font-size:11px; line-height:1.5; }
.df-home-strategy-intro b { color:#2465B8; }
.df-home-strategy-card { min-height:64px; padding:9px 10px; border:1px solid #E1EAF5; border-radius:10px; background:linear-gradient(150deg,#FFF,#F5FAFF); }
.df-home-strategy-card[data-kind="sft"] { background:linear-gradient(150deg,#FFF,#F2FBF7); }
.df-home-strategy-card[data-kind="dpo"] { background:linear-gradient(150deg,#FFF,#FAF6FF); }
.df-home-strategy-card b { display:inline-grid; place-items:center; min-width:32px; height:24px; padding:0 5px; border-radius:6px; background:#E7F0FF; color:#1769E0; font-size:10px; }
.df-home-strategy-card[data-kind="sft"] b { background:#DCF5E9; color:#16845F; }
.df-home-strategy-card[data-kind="dpo"] b { background:#EEE6FC; color:#7652C7; }
.df-home-strategy-card strong { margin-left:7px; font-size:12px; }
.df-home-strategy-card small { display:block; margin-top:4px; color:#77869A; font-size:10px; line-height:1.4; }
.st-key-home-strategy-options [data-testid="stColumn"] { min-width:0; }
.st-key-home-strategy-options button { min-height:32px; font-size:11px; }
.df-home-strategy-note { margin:10px 0 0; color:#798AA0; font-size:10px; line-height:1.5; }
.df-home-kpis { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:9px; }
.df-home-kpi { position:relative; min-height:82px; padding:12px 14px; border:1px solid #E2EAF4; border-radius:10px; background:#FAFCFF; }
.df-home-kpi:before { position:absolute; top:0; left:0; width:100%; height:3px; border-radius:10px 10px 0 0; background:#2A79DC; content:""; }
.df-home-kpi[data-tone="green"]:before { background:#24B083; }
.df-home-kpi[data-tone="amber"]:before { background:#E5AA4A; }
.df-home-kpi[data-tone="red"]:before { background:#E46A75; }
.df-home-kpi[data-tone="green"] { background:#F5FCF9; border-color:#DCEFE6; }
.df-home-kpi[data-tone="amber"] { background:#FFFBF4; border-color:#F4E8D5; }
.df-home-kpi[data-tone="red"] { background:#FFF8F8; border-color:#F6E2E5; }
.df-home-kpi[data-tone="blue"] { background:#F4F8FF; border-color:#DCEAFF; }
.df-home-kpi small { display:block; color:#6A7C92; font-size:11px; }
.df-home-kpi strong { display:block; margin-top:7px; color:#1A2B42; font-size:25px; line-height:1; }
.df-home-kpi[data-tone="green"] strong { color:#16845F; }
.df-home-kpi[data-tone="amber"] strong { color:#B87919; }
.df-home-kpi[data-tone="red"] strong { color:#C54959; }
.df-home-kpi[data-tone="blue"] strong { color:#1769E0; }
.df-home-summary { display:flex; flex-wrap:wrap; gap:6px 12px; margin:12px 0 8px; color:#708197; font-size:11px; }
.df-home-summary b { color:#25466E; }
.df-home-flow { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:9px; margin:4px 0 1px; }
.df-home-flow-step { position:relative; min-width:0; min-height:92px; padding:13px 10px; border:1px solid #E0EAF5; border-radius:11px; background:linear-gradient(145deg,#FFF,#F8FBFF); }
.df-home-flow-step:first-child { background:#F2F7FF; }
.df-home-flow-step:last-child { background:#EBFAF6; }
.df-home-flow-step:not(:last-child):after { position:absolute; top:38px; right:-9px; z-index:1; width:9px; color:#4685D2; font-size:16px; content:'→'; }
.df-home-flow-step b { display:grid; place-items:center; width:26px; height:26px; margin-bottom:5px; border-radius:7px; background:#E7F1FF; color:#1C6ECC; font-size:11px; }
.df-home-flow-step:last-child b { background:#DDF5EB; color:#138660; }
.df-home-flow-step strong { display:block; overflow:hidden; color:#253A54; font-size:11px; text-overflow:ellipsis; white-space:nowrap; }
.df-home-flow-step small { display:block; overflow:hidden; margin-top:2px; color:#8190A3; font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
.df-home-review-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }
.df-home-review-card { min-height:106px; padding:13px; border:1px solid #E2EAF5; border-radius:10px; background:linear-gradient(150deg,#FFF,#F8FBFF); }
.df-home-review-card b { display:inline-grid; place-items:center; min-width:35px; height:25px; padding:0 5px; border-radius:7px; background:#E7F0FF; color:#1769E0; font-size:10px; }
.df-home-review-card[data-kind="dpo"] b { background:#F0E9FC; color:#7451C6; }
.df-home-review-card[data-kind="cpt"] b { background:#E6F7EF; color:#16845F; }
.df-home-review-card strong { display:block; margin-top:9px; color:#1C3049; font-size:12px; }
.df-home-review-card small { display:block; margin-top:4px; color:#728198; font-size:10px; line-height:1.45; }
.df-home-source-list { border:1px solid #E5ECF5; border-radius:10px; overflow:hidden; }
.df-home-source { display:grid; grid-template-columns:32px minmax(0,1fr) 70px; align-items:center; gap:10px; min-height:40px; padding:7px 11px; border-top:1px solid #EBF0F6; }
.df-home-source:first-child { border-top:0; }
.df-home-source b { display:grid; place-items:center; width:29px; height:29px; border-radius:7px; background:#EAF2FF; color:#1769E0; font-size:8px; }
.df-home-source strong { overflow:hidden; color:#263950; font-size:12px; text-overflow:ellipsis; white-space:nowrap; }
.df-home-source small { color:#8290A3; font-size:11px; text-align:right; }
.df-home-task { display:grid; grid-template-columns:29px minmax(0,1fr) auto; align-items:center; gap:5px 8px; padding:10px 0; border-top:1px solid #EAF0F6; }
.df-home-task-icon { display:grid; place-items:center; grid-row:span 2; width:29px; height:29px; border-radius:8px; background:#EAF2FF; color:#1970D2; font-size:12px; }
.df-home-task-icon[data-status="completed"] { background:#E3F7ED; color:#16815B; }
.df-home-task-icon[data-status="needs_attention"] { background:#FFF2DA; color:#976719; }
.df-home-task-icon[data-status="failed"] { background:#FDECEF; color:#AC293E; }
.df-home-task strong { display:block; overflow:hidden; color:#21344D; font-size:12px; text-overflow:ellipsis; white-space:nowrap; }
.df-home-task small { grid-column:2 / -1; color:#8492A5; font-size:10px; }
.df-home-task-status { padding:4px 7px; border-radius:6px; background:#EAF2FF; color:#1769E0; font-size:10px; font-weight:700; }
.df-home-task-status[data-status="completed"] { background:#E6F7EF; color:#16815B; }
.df-home-task-status[data-status="needs_attention"] { background:#FFF2DA; color:#976719; }
.df-home-task-status[data-status="failed"] { background:#FDECEF; color:#AC293E; }
.df-home-empty { padding:18px 14px; border:1px dashed #C8D9ED; border-radius:10px; background:linear-gradient(135deg,#FBFDFF,#F5F9FF); text-align:center; }
.df-home-empty b { display:grid; place-items:center; width:36px; height:36px; margin:0 auto 9px; border-radius:10px; background:#E7F1FF; color:#246FC8; font-size:17px; }
.df-home-empty strong { display:block; color:#29425F; font-size:12px; }
.df-home-empty small { display:block; margin-top:5px; color:#768AA3; font-size:10px; line-height:1.5; }
@media(max-width:1430px) {
  .st-key-home-top [data-testid="stHorizontalBlock"]:has(.st-key-home-source-panel):has(.st-key-home-strategy-panel) { flex-wrap:wrap; }
  .st-key-home-top [data-testid="stHorizontalBlock"]:has(.st-key-home-source-panel):has(.st-key-home-strategy-panel) > [data-testid="stColumn"] { flex:1 1 420px !important; width:auto !important; min-width:min(420px,100%); }
  .st-key-home-top [data-testid="stHorizontalBlock"]:has(.st-key-home-source-panel):has(.st-key-home-strategy-panel) > [data-testid="stColumn"]:has(.st-key-home-stats-panel) { flex-basis:100% !important; min-width:100%; }
  .st-key-home-source-entries [data-testid="stHorizontalBlock"] { flex-wrap:wrap; }
  .st-key-home-source-entries [data-testid="stColumn"] { flex:1 1 140px !important; width:auto !important; min-width:min(140px,100%); }
  .df-home-entry { padding:16px 12px 11px; }
  .df-home-entry p { font-size:12px; }
  .df-home-entry em { font-size:12px; }
  .st-key-home-source-entries button { min-height:36px; font-size:12px; }
  .df-home-strategy-card { padding:10px; }
  .df-home-strategy-card strong { font-size:14px; }
  .df-home-strategy-card small { font-size:12px; }
  .st-key-home-strategy-options button { min-height:36px; font-size:12px; }
  .df-home-strategy-intro, .df-home-strategy-note, .df-home-kpi small, .df-home-summary { font-size:12px; }
  .df-home-kpis { grid-template-columns:repeat(4,minmax(0,1fr)); }
}
@media(max-width:1150px) {
  .st-key-home-top [data-testid="stHorizontalBlock"]:has(.st-key-home-source-panel):has(.st-key-home-strategy-panel) > [data-testid="stColumn"] { flex-basis:100% !important; min-width:100%; }
}
@media(max-width:760px) {
  .st-key-home-source-entries [data-testid="stColumn"] { flex:1 1 160px !important; width:auto !important; min-width:min(160px,100%); }
  .df-home-kpis { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .df-home-flow { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .df-home-flow-step:not(:last-child):after { display:none; }
  .df-home-review-grid { grid-template-columns:1fr; }
  .df-home-task { grid-template-columns:27px minmax(0,1fr); }
  .df-home-task-status { grid-column:2; justify-self:start; }
  .df-home-task small { grid-column:2; }
}
</style>"""
