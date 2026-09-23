"""Scoped visual language for the model-connection administration page."""

CSS = """
<style>
.df-model-summary { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:16px 0 18px; }
.df-model-summary > div { position:relative; min-width:0; min-height:108px; padding:17px 18px 14px; overflow:hidden; border:1px solid #DDE8F7; border-radius:12px; background:linear-gradient(145deg,#FFF 35%,#F5F9FF); box-shadow:0 4px 15px rgba(48,91,151,.045); }
.df-model-summary > div:before { position:absolute; top:0; left:0; width:100%; height:3px; background:#2A7CE4; content:""; }
.df-model-summary > div[data-tone="green"]:before { background:#24B78E; }
.df-model-summary > div[data-tone="purple"]:before { background:#8566DB; }
.df-model-summary > div[data-tone="amber"]:before { background:#E4A443; }
.df-model-summary small { display:block; color:#667B94; font-size:12px; }
.df-model-summary strong { display:block; overflow:hidden; margin:8px 0 5px; color:#192C48; font-size:25px; line-height:1.15; text-overflow:ellipsis; white-space:nowrap; }
.df-model-summary em { color:#788BA2; font-size:11px; font-style:normal; }
.df-model-summary > div[data-tone="green"] strong { color:#168564; }
.df-model-summary > div[data-tone="purple"] strong { color:#7357BE; }
.df-model-summary > div[data-tone="amber"] strong { color:#A96F20; }
.df-model-context { display:flex; flex-wrap:wrap; align-items:center; gap:7px 13px; margin:1px 0 16px; padding:12px 15px; border:1px solid #DFEAF7; border-radius:10px; background:#F6FAFF; color:#647A94; font-size:12px; }
.df-model-context b { color:#2168BA; }
.df-model-context i { color:#99ACC2; font-style:normal; }
.df-model-panel-title { display:flex; align-items:center; gap:10px; margin:0 0 14px; padding:0 0 12px; border-bottom:1px solid #E7EEF7; }
.df-model-panel-title > b { display:grid; place-items:center; width:36px; height:36px; border-radius:10px; background:#EAF2FF; color:#1868CB; font-size:15px; }
.df-model-panel-title strong { display:block; color:#1E304B; font-size:15px; }
.df-model-panel-title small { display:block; margin-top:3px; color:#71849A; font-size:12px; }
.df-model-panel-title > span { margin-left:auto; color:#6C82A0; font-size:12px; }
.df-model-endpoints { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:11px; }
.df-model-endpoint { min-width:0; padding:15px 16px 13px; border:1px solid #E0E9F5; border-radius:11px; background:linear-gradient(150deg,#FFF,#FBFDFF); }
.df-model-endpoint[data-default="true"] { border-color:#A7C9F4; background:linear-gradient(150deg,#FFF,#F2F8FF); box-shadow:inset 3px 0 #2375DB; }
.df-model-endpoint-head { display:flex; align-items:center; gap:8px; min-width:0; }
.df-model-endpoint-icon { display:grid; place-items:center; width:34px; height:34px; flex:0 0 auto; border-radius:9px; background:#EAF2FF; color:#2672CE; font-size:15px; }
.df-model-endpoint-head strong { overflow:hidden; color:#1E3049; font-size:14px; text-overflow:ellipsis; white-space:nowrap; }
.df-model-endpoint-head em { margin-left:auto; padding:4px 7px; border-radius:6px; background:#DFEEFF; color:#1D69BA; font-size:10px; font-style:normal; white-space:nowrap; }
.df-model-endpoint-models { display:flex; flex-wrap:wrap; gap:5px; min-height:26px; margin:13px 0 8px; }
.df-model-endpoint-models span { max-width:100%; overflow:hidden; padding:5px 8px; border-radius:5px; background:#EDF3FB; color:#315578; font-size:11px; text-overflow:ellipsis; white-space:nowrap; }
.df-model-endpoint-models i { color:#8395AA; font-size:12px; font-style:normal; }
.df-model-endpoint-address { overflow:hidden; margin:0 0 12px; color:#697F99; font-size:12px; text-overflow:ellipsis; white-space:nowrap; }
.df-model-endpoint-foot { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:6px; padding-top:10px; border-top:1px solid #E7EDF5; }
.df-model-endpoint-foot span { display:-webkit-box; min-width:0; overflow:hidden; color:#5D738D; font-size:11px; line-height:1.45; -webkit-box-orient:vertical; -webkit-line-clamp:2; }
.df-model-endpoint-foot b { flex:0 0 auto; padding:4px 7px; border-radius:5px; background:#E4F7EE; color:#16815D; font-size:11px; }
.df-model-endpoint-foot b[data-ready="false"] { background:#FFF0E2; color:#A46C27; }
.df-model-empty { padding:29px 18px; border:1px dashed #C8D7E9; border-radius:10px; background:#FAFCFF; color:#71859F; font-size:12px; text-align:center; }
.df-model-role-map { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:9px; }
.df-model-role { display:grid; grid-template-columns:33px minmax(0,1fr) auto; align-items:center; column-gap:9px; min-height:70px; padding:10px 12px; border:1px solid #E0EAF6; border-radius:10px; background:#FAFCFF; }
.df-model-role > b { display:grid; place-items:center; width:31px; height:31px; border-radius:8px; background:#EDE9FC; color:#7559C4; font-size:13px; }
.df-model-role strong { display:block; color:#263B57; font-size:12px; }
.df-model-role small { display:block; overflow:hidden; max-width:100%; margin-top:3px; color:#778BA2; font-size:11px; text-overflow:ellipsis; white-space:nowrap; }
.df-model-role em { padding:4px 6px; border-radius:5px; background:#E6F7EF; color:#15805B; font-size:10px; font-style:normal; }
.df-model-role em[data-ready="false"] { background:#F2F5F9; color:#8C9AAC; }
.df-model-budget { padding:18px; border:1px solid #DDE8F6; border-radius:11px; background:linear-gradient(145deg,#F9FCFF,#FFF); }
.df-model-budget-top { display:flex; align-items:flex-start; justify-content:space-between; gap:15px; }
.df-model-budget-top small { display:block; color:#72869F; font-size:11px; }
.df-model-budget-top strong { display:block; margin:5px 0 3px; color:#1A3456; font-size:26px; }
.df-model-budget-top em { color:#71869D; font-size:11px; font-style:normal; }
.df-model-budget-track { height:10px; overflow:hidden; margin:17px 0 9px; border-radius:8px; background:#E5EDF8; }
.df-model-budget-track i { display:block; height:100%; border-radius:8px; background:linear-gradient(90deg,#2C82E6,#27A5E3); }
.df-model-budget-notes { display:flex; justify-content:space-between; color:#6E829A; font-size:10px; }
[class*="st-key-model-budget-"] { border-color:#DDE8F6!important; box-shadow:0 4px 15px rgba(48,91,151,.045)!important; }
[class*="st-key-model-budget-"] .df-model-panel-title { margin-bottom:15px; }
[class*="st-key-model-budget-actions"] { background:linear-gradient(145deg,#FFF,#F7FAFF)!important; }
@media(min-width:1500px) { .df-model-endpoints { grid-template-columns:repeat(3,minmax(0,1fr)); } }
@media(max-width:1150px) { .df-model-summary { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media(max-width:850px) { .df-model-endpoints,.df-model-role-map { grid-template-columns:1fr; } }
@media(max-width:600px) { .df-model-summary { grid-template-columns:1fr; } }
</style>
"""
