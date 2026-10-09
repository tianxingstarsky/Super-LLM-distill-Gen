"""A calm starting surface with clear source choices and real work summaries."""

HOME_STYLE = """<style>
.df-home-kpis { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:0; padding:17px 6px; border:1px solid #D6E3F2; border-radius:16px; background:linear-gradient(135deg,#FFF 18%,#F1F7FF 100%); box-shadow:inset 0 1px 0 #FFF,0 1px 2px #264A7010,0 10px 24px -16px #315C8940; }
.df-home-kpi { display:flex; align-items:center; justify-content:space-between; gap:12px; min-width:0; padding:0 20px; border-left:1px solid #E5ECF5; }
.df-home-kpi:first-child { border-left:0; }
.df-home-kpi small { color:#56677D; font-size:13px; font-weight:500; line-height:1.5; }
.df-home-kpi strong { color:#1B61C7; font-size:27px; font-weight:650; line-height:1.2; letter-spacing:-.8px; font-variant-numeric:tabular-nums; }
.df-home-kpi[data-tone="green"] strong { color:#11795B; }
.df-home-kpi[data-tone="amber"] strong { color:#A66C15; }
.st-key-home-main [data-testid="stColumn"] { min-width:0; }
[data-testid="stMainBlockContainer"] .st-key-home-source-panel,
[data-testid="stMainBlockContainer"] .st-key-home-recent-panel,
[data-testid="stMainBlockContainer"] .st-key-home-library-panel,
[data-testid="stMainBlockContainer"] .st-key-home-results-panel { padding:19px; border:1px solid #D6E2F0; border-radius:17px; background:var(--df-surface,linear-gradient(150deg,#FFF,#F8FBFF)); box-shadow:var(--df-shadow-panel,0 2px 3px #27466E06,0 12px 32px -14px #27466E30,inset 0 1px 0 #FFF); }
[data-testid="stMainBlockContainer"] .st-key-home-source-panel { background:radial-gradient(ellipse at 100% 0%,#E4F0FF99,transparent 55%),linear-gradient(150deg,#FFF 44%,#F5F9FF); }
[data-testid="stMainBlockContainer"] .st-key-home-recent-panel { background:linear-gradient(145deg,#FFF 58%,#F2F7FD); }
.st-key-home-source-panel > [data-testid="stVerticalBlock"],
.st-key-home-recent-panel > [data-testid="stVerticalBlock"],
.st-key-home-library-panel > [data-testid="stVerticalBlock"] { gap:12px; }
.st-key-home-manual-entry { padding:10px 12px; border:1px solid #DFE5F0; border-radius:12px; background:linear-gradient(135deg,#FFF,#F1F3FC); box-shadow:inset 0 1px 0 #FFF,0 2px 4px #33476A08; }
.df-home-manual { display:flex; align-items:center; gap:10px; min-width:0; }
.df-home-manual > b { display:grid; place-items:center; flex:0 0 34px; height:34px; border:1px solid #E0D7F6; border-radius:10px; background:linear-gradient(145deg,#FBF9FF,#E3D9F9); box-shadow:inset 0 1px 0 #FFF,0 3px 5px #7355A71A; color:#7147B2; font-size:19px; }
.df-home-manual span { min-width:0; }
.df-home-manual strong { display:block; color:#283F5B; font-size:13px; font-weight:650; line-height:1.5; }
.df-home-manual small { display:block; color:#596C83; font-size:12px; line-height:1.6; overflow-wrap:anywhere; }
.st-key-home-manual-entry .stButton button { min-height:36px; background:#FFF; }
.st-key-home-entry-documents, .st-key-home-entry-agent, .st-key-home-entry-brief { position:relative; gap:5px; min-width:0; padding:15px 13px; border:1px solid #C9DCF3; border-radius:13px; background:radial-gradient(ellipse at 15% 0%,#FFFFFFEF,transparent 68%),linear-gradient(145deg,#EEF6FF,#DDEBFC); color:#236BC7; box-shadow:inset 0 1px 0 #FFF,inset 0 -1px 0 #ADC8ED38,0 2px 3px #204B8012,0 9px 16px -12px #225A9852; transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease; }
.st-key-home-entry-agent { border-color:#CEE4DB; background:radial-gradient(ellipse at 15% 0%,#FFFFFFEF,transparent 68%),linear-gradient(145deg,#EDF9F3,#DFEFE9); color:#148368; }
.st-key-home-entry-brief { border-color:#DDD3EF; background:radial-gradient(ellipse at 15% 0%,#FFFFFFEF,transparent 68%),linear-gradient(145deg,#F6F1FF,#E8E2F5); color:#8055BC; }
.st-key-home-entry-documents:hover, .st-key-home-entry-agent:hover, .st-key-home-entry-brief:hover { transform:translateY(-2px); border-color:currentColor; box-shadow:var(--df-shadow-lift,0 5px 10px -5px #294F8830,0 18px 32px -18px #294F884D,inset 0 1px 0 #FFF); }
.st-key-home-entry-documents:has(button:active), .st-key-home-entry-agent:has(button:active), .st-key-home-entry-brief:has(button:active) { transform:translateY(1px); box-shadow:inset 0 2px 5px #315C8918,0 1px 2px #204B8012; }
.st-key-home-entry-documents:has(button:focus-visible), .st-key-home-entry-agent:has(button:focus-visible), .st-key-home-entry-brief:has(button:focus-visible) { outline:2px solid currentColor; outline-offset:3px; }
.df-home-entry-art { display:flex; align-items:flex-start; justify-content:space-between; gap:6px; height:68px; color:inherit; pointer-events:none; }
.df-home-entry-art img { display:block; width:68px; height:68px; object-fit:contain; filter:drop-shadow(0 5px 4px rgba(55,102,160,.16)); transition:transform .24s ease,filter .24s ease; }
.df-home-entry-art > span { display:grid; place-items:center; width:23px; height:23px; margin-top:1px; border:1px solid currentColor; border-radius:50%; font-size:14px; opacity:.52; transition:opacity .18s ease,transform .18s ease; }
.st-key-home-source-entries [class*="st-key-home-entry-"]:is(:hover,:focus-within) .df-home-entry-art > span { opacity:.9; transform:translate(1px,-1px); }
.st-key-home-source-entries [class*="st-key-home-entry-"]:is(:hover,:focus-within) .df-home-entry-art img { transform:translateY(-2px) scale(1.04); filter:drop-shadow(0 6px 5px rgba(55,102,160,.13)); }
.st-key-home-source-entries .stButton button { min-height:28px; padding:0; border:0; border-radius:4px; background:transparent; color:inherit; justify-content:flex-start; text-align:left; box-shadow:none; }
.st-key-home-source-entries .stButton button p { width:100%; font-size:15px; font-weight:650; line-height:1.5; text-align:left; }
.st-key-home-source-entries .stButton button > div { width:100%; text-align:left; }
.st-key-home-source-entries .stButton button [data-testid="stMarkdownContainer"] { width:100%; text-align:left; }
.st-key-home-source-entries .stButton button > div { justify-content:flex-start; }
.st-key-home-source-entries [data-testid="stElementContainer"]:has(> .stButton) { position:static; }
.st-key-home-source-entries .stButton button::after { content:""; position:absolute; inset:0; z-index:1; border-radius:13px; }
.st-key-home-source-entries .stButton button:hover { background:transparent; color:inherit; }
.st-key-home-source-entries .stButton button:focus-visible { outline:0; }
.st-key-home-source-entries [data-testid="stCaptionContainer"] { min-height:62px; color:#5A6D83; overflow-wrap:anywhere; font-size:12px; line-height:1.7; }
.st-key-home-source-entries [data-testid="stCaptionContainer"] p { margin:0; color:#5A6D83; font-size:12px; line-height:1.7; }
.st-key-home-strategy-options { padding-top:11px; border-top:1px solid #E6EDF5; gap:8px; }
.st-key-home-strategy-options [data-testid="stCaptionContainer"], .st-key-home-strategy-options [data-testid="stCaptionContainer"] p { color:#5C6F85; font-size:12px; line-height:1.6; }
.st-key-home-targets-primary .stButton button { min-height:39px; height:100%; padding:6px 9px; border-color:#CBDDF4; background:linear-gradient(180deg,#FBFDFF,#E7F0FC); color:#1F5CAF; box-shadow:inset 0 1px 0 #FFF,0 2px 3px #315C8912; }
.st-key-home-targets-primary .stButton button:hover:not(:disabled) { border-color:#91B7E8; background:linear-gradient(180deg,#FFF,#DCEAFF); color:#1756AA; box-shadow:inset 0 1px 0 #FFF,0 3px 7px #315C8921; }
.st-key-home-targets-primary .stButton button:active:not(:disabled), .st-key-home-targets-secondary .stButton button:active:not(:disabled) { box-shadow:inset 0 2px 4px #315C8924; }
.st-key-home-targets-primary .stButton button p { font-size:13px; font-weight:600; line-height:1.5; white-space:normal; }
.st-key-home-targets-secondary { gap:7px; }
.st-key-home-targets-secondary .stButton button { min-height:33px; height:100%; padding:5px 7px; border-color:#D9E3F0; background:linear-gradient(180deg,#FFF,#F2F6FC); color:#425975; box-shadow:inset 0 1px 0 #FFF,0 1px 2px #315C8910; }
.st-key-home-targets-secondary .stButton button:hover { border-color:#BDD0E8; background:#F5F9FF; color:#235DA4; }
.st-key-home-targets-secondary .stButton button p { font-size:12px; line-height:1.5; white-space:normal; }
/* Stretch each native button with its row's tallest label, without imposing a
   taller fixed height on short Chinese labels. Keep every wrapper in the flex
   chain; current Streamlit inserts a layout wrapper around each widget. */
.st-key-home-targets-primary [data-testid="stHorizontalBlock"],
.st-key-home-targets-secondary [data-testid="stHorizontalBlock"] { align-items:stretch; }
.st-key-home-targets-primary [data-testid="stColumn"],
.st-key-home-targets-secondary [data-testid="stColumn"] { display:flex; flex-direction:column; }
.st-key-home-targets-primary [data-testid="stColumn"] > div,
.st-key-home-targets-secondary [data-testid="stColumn"] > div,
.st-key-home-targets-primary [data-testid="stColumn"] [data-testid="stLayoutWrapper"],
.st-key-home-targets-secondary [data-testid="stColumn"] [data-testid="stLayoutWrapper"],
.st-key-home-targets-primary [data-testid="stColumn"] [data-testid="stVerticalBlock"],
.st-key-home-targets-secondary [data-testid="stColumn"] [data-testid="stVerticalBlock"],
.st-key-home-targets-primary [data-testid="stColumn"] [data-testid="stElementContainer"],
.st-key-home-targets-secondary [data-testid="stColumn"] [data-testid="stElementContainer"],
.st-key-home-targets-primary [data-testid="stColumn"] [data-testid="stButton"],
.st-key-home-targets-secondary [data-testid="stColumn"] [data-testid="stButton"] { display:flex; flex-direction:column; flex:1; }
.st-key-home-targets-primary .stButton button,
.st-key-home-targets-secondary .stButton button { flex:1; height:auto; }
[class*="st-key-home-task-row-"] { position:relative; padding:10px 0; border-bottom:1px solid #EAF0F6; border-radius:8px; transition:background .18s ease; }
[class*="st-key-home-task-row-"]:hover { background:#F5F9FF; }
[class*="st-key-home-task-row-"]:has(button:focus-visible) { outline:2px solid #1769E0; outline-offset:3px; }
[class*="st-key-home-task-row-"] :is([data-testid="stColumn"],[data-testid="stElementContainer"],[data-testid="stLayoutWrapper"]) { position:static; }
[class*="st-key-home-task-row-"] button::after { content:""; position:absolute; inset:0; border-radius:8px; }
.st-key-home-recent-panel [class*="st-key-home-task-row-"] .stButton button { min-height:33px; padding:5px 9px; border-color:#DFE7F1; border-radius:9px; background:#FFF; }
.st-key-home-recent-panel [class*="st-key-home-task-row-"] .stButton button p { font-size:12px; white-space:nowrap; }
.df-home-task { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:start; gap:6px 10px; padding:0; }
.df-home-task strong { color:#20364E; font-size:14px; font-weight:650; line-height:1.5; overflow-wrap:anywhere; }
.df-home-task small { grid-column:1 / -1; color:#61758A; font-size:12px; line-height:1.5; overflow-wrap:anywhere; }
.df-home-task-status { display:inline-flex; align-items:center; gap:5px; padding:2px 7px; border:1px solid #DEE8F7; border-radius:6px; background:#EDF4FF; color:#2362B8; font-size:11px; font-weight:500; line-height:1.5; white-space:nowrap; }
.df-home-task-status[data-status="completed"] { border-color:#CFEADD; background:#EDF9F2; color:#15744F; }
.df-home-task-status[data-status="needs_attention"] { border-color:#F0DFBF; background:#FFF7E8; color:#946417; }
.df-home-task-status[data-status="failed"] { border-color:#F2D9DF; background:#FFF0F3; color:#A72A42; }
.df-home-task-status[data-status="cancelled"] { border-color:#E3E8EE; background:#F4F6F9; color:#617286; }
.df-home-source-list { min-width:0; }
.df-home-source { display:grid; grid-template-columns:32px minmax(0,1fr) auto; align-items:center; gap:10px; padding:5px 0; border-bottom:1px solid #EBF0F6; }
.df-home-source:last-child { border-bottom:0; }
.df-home-source b { display:grid; place-items:center; width:30px; height:32px; border:1px solid #D2E2F5; border-radius:8px; background:linear-gradient(145deg,#FFF,#DDEBFC); box-shadow:inset 0 1px 0 #FFF,0 2px 3px #315C8915; color:#2564B6; font-size:9px; letter-spacing:.1px; }
.df-home-source strong { overflow:hidden; color:#2A405A; font-size:13px; font-weight:550; line-height:1.5; text-overflow:ellipsis; white-space:nowrap; }
.df-home-source small { color:#62768D; font-size:11px; white-space:nowrap; font-variant-numeric:tabular-nums; }
.st-key-home-library-panel .stButton button { min-height:36px; }
.st-key-home-results-panel .df-section-heading { padding-bottom:0; margin-bottom:0; border-bottom:0; }
.st-key-home-results-panel .df-section-heading small { max-width:420px; }
.st-key-home-results-panel .stButton button { min-height:40px; background:#F6F9FE; border-color:#D8E4F3; color:#2E527E; }
.st-key-home-results-panel .stButton button:hover { background:#EDF4FF; border-color:#A8C6EB; color:#1B5DB5; }
.st-key-home-results-panel [data-testid="stExpander"] { border:0; border-radius:0; background:transparent; box-shadow:none; }
.st-key-home-results-panel [data-testid="stExpander"] details { border:0; background:transparent; }
.st-key-home-results-panel [data-testid="stExpander"] summary { min-height:26px; padding:0; color:#61748B; }
.st-key-home-results-panel [data-testid="stExpander"] summary p { font-size:12px; }
.df-home-summary { display:inline-flex; align-items:center; gap:10px; padding:6px 11px; border:1px solid #E4EBF4; border-radius:8px; background:#F9FBFE; color:#5B7088; font-size:12px; line-height:1.5; }
.df-home-summary b { color:#264B79; font-size:13px; font-weight:650; font-variant-numeric:tabular-nums; }
.df-home-empty { padding:12px 0; }
.df-home-empty strong { display:block; color:#29425F; font-size:14px; }
.df-home-empty small { display:block; margin-top:6px; color:#5D728A; font-size:13px; line-height:1.7; overflow-wrap:anywhere; }
.df-home-empty-work { position:relative; overflow:hidden; padding:23px 18px; margin:3px 0 7px; border:1px solid #D8E5F5; border-radius:12px; background:radial-gradient(ellipse at 100% 0%,#DFEEFF,transparent 70%),linear-gradient(150deg,#FFF,#F2F7FE); box-shadow:inset 0 1px 0 #FFF,0 2px 5px #315C8910; }
.df-home-empty-work > span { display:grid; place-items:center; width:38px; height:38px; margin-bottom:12px; border:1px solid #CBDFF8; border-radius:11px; color:#3479C9; background:linear-gradient(135deg,#FFF,#D9E9FC); box-shadow:inset 0 1px 0 #FFF,0 4px 7px #315C891C; font-size:22px; }
.df-home-empty-work small { max-width:330px; }
@media(prefers-reduced-motion:reduce) {
  .st-key-home-entry-documents, .st-key-home-entry-agent, .st-key-home-entry-brief, .df-home-entry-art > span, .df-home-entry-art img, [class*="st-key-home-task-row-"] { transition:none; }
  .st-key-home-entry-documents:hover, .st-key-home-entry-agent:hover, .st-key-home-entry-brief:hover,
  .st-key-home-entry-documents:has(button:active), .st-key-home-entry-agent:has(button:active), .st-key-home-entry-brief:has(button:active) { transform:none; }
  .st-key-home-source-entries [class*="st-key-home-entry-"]:is(:hover,:focus-within) .df-home-entry-art img,
  .st-key-home-source-entries [class*="st-key-home-entry-"]:is(:hover,:focus-within) .df-home-entry-art > span { transform:none; }
}
@media(max-width:1050px) {
  .st-key-home-main > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] { display:grid !important; grid-template-columns:minmax(0,1fr); gap:16px; }
  .st-key-home-main > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { flex:none !important; width:100% !important; min-width:0 !important; max-width:100%; }
}
@media(max-width:600px) {
  .df-home-kpis { grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px 0; }
  .df-home-kpi { align-items:flex-start; flex-direction:column; gap:5px; padding:0 12px; }
  .df-home-kpi:nth-child(3) { border-left:0; }
  .df-home-kpi strong { font-size:25px; }
  [data-testid="stMainBlockContainer"] .st-key-home-source-panel, [data-testid="stMainBlockContainer"] .st-key-home-recent-panel, [data-testid="stMainBlockContainer"] .st-key-home-library-panel, [data-testid="stMainBlockContainer"] .st-key-home-results-panel { padding:15px; }
  .st-key-home-source-entries [data-testid="stCaptionContainer"] { min-height:0; }
  .st-key-home-source-entries > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] { display:grid !important; grid-template-columns:minmax(0,1fr); gap:9px; }
  .st-key-home-source-entries [data-testid="stColumn"] { width:100% !important; min-width:0 !important; max-width:100%; flex:none !important; }
  .st-key-home-source-entries [class*="st-key-home-entry-"] { display:grid; grid-template-columns:64px minmax(0,1fr); gap:2px 10px; padding:12px 36px 12px 12px; }
  .st-key-home-source-entries [class*="st-key-home-entry-"] > :is([data-testid="stElementContainer"],[data-testid="stLayoutWrapper"]):first-child { grid-column:1; grid-row:1 / 3; align-self:center; }
  .st-key-home-source-entries [class*="st-key-home-entry-"] > :is([data-testid="stElementContainer"],[data-testid="stLayoutWrapper"]):nth-child(2),
  .st-key-home-source-entries [class*="st-key-home-entry-"] > :is([data-testid="stElementContainer"],[data-testid="stLayoutWrapper"]):nth-child(3) { grid-column:2; }
  .st-key-home-source-entries [class*="st-key-home-entry-"] .df-home-entry-art,
  .st-key-home-source-entries [class*="st-key-home-entry-"] .df-home-entry-art img { width:64px; height:64px; }
  .st-key-home-source-entries [class*="st-key-home-entry-"] .df-home-entry-art > span { position:absolute; top:10px; right:10px; }
  /* Native columns retain percentage widths even when the row wraps. These
     keyed rows use a grid and reset only their direct column children. */
  .st-key-home-manual-entry [data-testid="stHorizontalBlock"],
  .st-key-home-results-panel > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] { display:grid !important; grid-template-columns:minmax(0,1fr); gap:12px; }
  .st-key-home-targets-primary [data-testid="stHorizontalBlock"],
  .st-key-home-targets-secondary [data-testid="stHorizontalBlock"] { display:grid !important; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .st-key-home-manual-entry [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
  .st-key-home-results-panel [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
  .st-key-home-targets-primary [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
  .st-key-home-targets-secondary [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { flex:none !important; width:100% !important; min-width:0 !important; max-width:100%; }
  .st-key-home-results-panel [data-testid="stColumn"] [data-testid="stHorizontalBlock"] { display:grid !important; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .st-key-home-targets-primary [data-testid="stColumn"]:last-child,
  .st-key-home-targets-secondary [data-testid="stColumn"]:last-child { grid-column:1 / -1; }
  .st-key-home-targets-primary .stButton button,
  .st-key-home-targets-secondary .stButton button { min-height:44px; padding:8px 10px; }
  [class*="st-key-home-task-row-"] [data-testid="stHorizontalBlock"] { display:grid !important; grid-template-columns:minmax(0,1fr) auto; gap:10px; }
  [class*="st-key-home-task-row-"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { flex:none !important; width:100% !important; min-width:0 !important; max-width:100%; }
  [class*="st-key-home-task-row-"] [data-testid="stColumn"]:last-child { width:auto !important; }
  .st-key-home-manual-entry .stButton button,
  .st-key-home-recent-panel [class*="st-key-home-task-row-"] .stButton button,
  .st-key-home-results-panel .stButton button { min-height:44px; }
}
</style>"""
