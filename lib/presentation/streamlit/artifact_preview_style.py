"""Styles scoped to a rendered training-record preview."""

ARTIFACT_PREVIEW_STYLE = """<style>
.df-artifact-preview .df-artifact-flow { display:grid; gap:0; min-width:0; }
.df-artifact-preview .df-artifact-flow-head { display:flex; align-items:center; justify-content:space-between; gap:10px; margin:1px 0 12px; }
.df-artifact-preview .df-artifact-flow-head strong { color:#183251; font-size:12px; }
.df-artifact-preview .df-artifact-flow-head span { color:#7B8DA4; font-size:10px; }
.df-artifact-preview .df-artifact-turn { min-width:0; margin:0 0 10px; border:1px solid #DFEAF7; border-radius:11px; background:#fff; overflow:hidden; box-shadow:0 2px 8px rgba(39,83,139,.035); }
.df-artifact-preview .df-artifact-turn[data-kind="context"] { background:#F7F9FD; border-color:#E2E8F1; }
.df-artifact-preview .df-artifact-turn-head { display:flex; align-items:center; gap:8px; min-height:38px; padding:7px 11px; border-bottom:1px solid #ECF1F8; background:#F8FBFF; }
.df-artifact-preview .df-artifact-turn[data-kind="context"] .df-artifact-turn-head { background:#F4F7FB; }
.df-artifact-preview .df-artifact-turn-number { display:grid; place-items:center; flex:0 0 auto; min-width:25px; height:24px; padding:0 5px; border-radius:7px; background:#E8F1FF; color:#1B67BD; font-size:10px; font-weight:800; }
.df-artifact-preview .df-artifact-turn-head strong { color:#203752; font-size:11px; }
.df-artifact-preview .df-artifact-turn-head small { margin-left:auto; color:#8291A5; font-size:10px; }
.df-artifact-preview .df-artifact-turn-body { padding:12px; }
.df-artifact-preview .df-artifact-turn-body .bubbles { gap:11px; }
.df-artifact-preview .df-artifact-more { margin:1px 0 10px; padding:8px 12px; border:1px dashed #BBD0EC; border-radius:9px; background:#F7FBFF; }
.df-artifact-preview .df-artifact-more summary { color:#1A64AF; cursor:pointer; font-size:11px; font-weight:700; }
.df-artifact-preview .df-artifact-more > div { display:grid; gap:10px; padding-top:11px; }
.df-artifact-preview .df-artifact-trace { display:grid; gap:0; min-width:0; padding-left:3px; }
.df-artifact-preview .df-artifact-trace-item { position:relative; min-width:0; margin-left:13px; padding:0 0 14px 19px; border-left:1px solid #C6DBF5; }
.df-artifact-preview .df-artifact-trace-item:last-child { border-left-color:transparent; padding-bottom:0; }
.df-artifact-preview .df-artifact-trace-marker { position:absolute; top:2px; left:-11px; display:grid; place-items:center; width:21px; height:21px; border:2px solid #fff; border-radius:50%; background:#2878D8; color:#fff; font-size:10px; font-weight:800; box-shadow:0 0 0 1px #BAD4F3; }
.df-artifact-preview .df-artifact-trace-item[data-kind="tool"] .df-artifact-trace-marker { background:#7C57C7; box-shadow:0 0 0 1px #DDCFFA; }
.df-artifact-preview .df-artifact-trace-item[data-kind="answer"] .df-artifact-trace-marker { background:#159A73; box-shadow:0 0 0 1px #BCE7D6; }
.df-artifact-preview .df-artifact-trace-item[data-failed="true"] .df-artifact-trace-marker { background:#D74C60; box-shadow:0 0 0 1px #F4C6CE; }
.df-artifact-preview .df-artifact-trace-card { min-width:0; overflow:hidden; border:1px solid #DFE9F6; border-radius:10px; background:#fff; }
.df-artifact-preview .df-artifact-trace-item[data-kind="tool"] .df-artifact-trace-card { border-color:#E6DDF4; background:#FCFAFF; }
.df-artifact-preview .df-artifact-trace-item[data-failed="true"] .df-artifact-trace-card { border-color:#F0C8D0; background:#FFF8F9; }
.df-artifact-preview .df-artifact-trace-head { display:flex; align-items:center; gap:8px; min-width:0; padding:8px 11px; border-bottom:1px solid #EDF1F7; background:rgba(244,248,254,.7); }
.df-artifact-preview .df-artifact-trace-head b { color:#1D4F87; font-size:10px; white-space:nowrap; }
.df-artifact-preview .df-artifact-trace-head strong { min-width:0; overflow:hidden; color:#263B56; font-size:11px; text-overflow:ellipsis; white-space:nowrap; }
.df-artifact-preview .df-artifact-trace-head small { margin-left:auto; flex:0 0 auto; color:#8191A5; font-size:10px; }
.df-artifact-preview .df-artifact-trace-item[data-failed="true"] .df-artifact-trace-head small { color:#B7354D; font-weight:700; }
.df-artifact-preview .df-artifact-trace-body { padding:10px 12px; }
.df-artifact-preview .df-artifact-trace-body .bubbles { gap:9px; }
.df-artifact-preview .df-artifact-verification { display:flex; flex-wrap:wrap; gap:7px; padding:9px 10px; border:1px solid #BDE3D4; border-radius:8px; background:#F2FBF7; color:#187458; font-size:10px; }
.df-artifact-preview .df-artifact-verification span + span { padding-left:8px; border-left:1px solid #C8E8DB; }
.df-artifact-preview .df-artifact-failure strong { display:block; margin-bottom:3px; font-size:11px; }
.df-artifact-preview .df-artifact-failure small { display:block; color:#A5485B; font-size:10px; }
@media(max-width:720px) {
  .df-artifact-preview .df-artifact-turn-body, .df-artifact-preview .df-artifact-trace-body { padding:9px; }
  .df-artifact-preview .df-artifact-trace-head { flex-wrap:wrap; }
  .df-artifact-preview .df-artifact-trace-head small { margin-left:0; width:100%; }
}
/* The artifact panel has its own width: the right column can be narrow even on a wide screen. */
.df-artifact-preview { box-sizing:border-box; min-width:0; width:100%; container-type:inline-size; gap:15px; padding:16px; border-color:#D8E6F6; border-radius:14px; background:linear-gradient(180deg,#FBFDFF,#F7FAFF); }
.df-artifact-preview *, .df-artifact-preview *::before, .df-artifact-preview *::after { box-sizing:border-box; }
.df-artifact-preview .df-artifact-heading { min-width:0; padding-bottom:13px; }
.df-artifact-preview .df-artifact-heading strong { font-size:15px; font-weight:750; }
.df-artifact-preview .df-artifact-badge { height:29px; min-width:46px; padding:0 9px; font-size:10px; }
.df-artifact-preview .df-artifact-meta { gap:6px; }
.df-artifact-preview .df-artifact-meta span { padding:5px 9px; border-color:#DFE9F5; border-radius:7px; background:#fff; color:#5B708B; font-size:11px; line-height:1.35; }
.df-artifact-preview .df-artifact-flow-head { margin:0 0 12px; flex-wrap:wrap; }
.df-artifact-preview .df-artifact-flow-head strong { font-size:14px; }
.df-artifact-preview .df-artifact-flow-head span { font-size:11px; }
.df-artifact-preview .bubbles { display:flex; flex-direction:column; gap:10px; min-width:0; }
.df-artifact-preview .bubble { min-width:0; max-width:100%; line-height:1.65; overflow-wrap:anywhere; word-break:normal; }
.df-artifact-preview .bubbles .bubble .role-tag { display:block; margin:0 0 5px; color:#627A99; font-family:inherit; font-size:10px; font-weight:750; letter-spacing:.02em; }
.df-artifact-preview .bubble .md, .df-artifact-preview .bubble .tool-call { min-width:0; overflow-wrap:anywhere; }
.df-artifact-preview .bubble .md > :first-child { margin-top:0; }
.df-artifact-preview .bubble .md > :last-child { margin-bottom:0; }
.df-artifact-preview .bubble .md pre { max-width:100%; overflow-x:auto; white-space:pre; }
.df-artifact-preview .bubble .md table { display:block; max-width:100%; overflow-x:auto; }
.df-artifact-preview .bubble .md a, .df-artifact-preview .bubble .tc-val,
.df-artifact-preview .bubble .tc-id { overflow-wrap:anywhere; word-break:break-word; }
.df-artifact-preview .bubble.bub-user { max-width:min(100%,640px); align-self:flex-end; padding:10px 13px; border:1px solid #D4E6FC; border-radius:12px 5px 12px 12px; background:#EDF5FF; color:#183553; }
.df-artifact-preview .bubble.bub-assistant { max-width:100%; align-self:stretch; padding:11px 13px; border:1px solid #D8ECE6; border-radius:5px 12px 12px; background:#F4FCF9; color:#173C35; }
.df-artifact-preview .bubble.bub-call { max-width:100%; align-self:stretch; padding:10px 12px; border:1px solid #E2D7F6; border-radius:10px; background:#F8F5FF; }
.df-artifact-preview .bubble.bub-call .role-tag { color:#7352A8; }
.df-artifact-preview .bubble.bub-tool { max-width:100%; align-self:stretch; padding:10px 12px; border:1px solid #DDE7F4; border-radius:10px; background:#fff; color:#344B64; font-family:inherit; font-size:13px; line-height:1.62; }
.df-artifact-preview .bubble.bub-tool .role-tag::before { content:"工具返回 · "; color:#506E98; }
.df-artifact-preview .bubble.bub-tool.err { border-color:#F0B8C2; background:#FFF7F8; color:#9A2F45; }
.df-artifact-preview .bubble.bub-system { max-width:100%; align-self:stretch; padding:9px 12px; border:1px solid #E2E9F2; border-radius:9px; background:#F6F8FC; color:#576B85; font-size:12px; }
.df-artifact-preview .bubble.bub-think { align-self:stretch; }
.df-artifact-preview details.think { padding:9px 11px; border:1px solid #F2DFB5; border-radius:9px; background:#FFFBF1; color:#7B5C22; }
.df-artifact-preview details.think summary { cursor:pointer; font-size:11px; font-weight:700; }
.df-artifact-preview details.think .md { padding-top:9px; }
.df-artifact-preview .tool-call { font-size:12px; line-height:1.6; }
.df-artifact-preview .tool-call .tc-name { color:#654399; font-weight:750; }
.df-artifact-preview .tool-call .tc-id { color:#7D84A2; font-family:ui-monospace,Consolas,monospace; font-size:10px; }
.df-artifact-preview .tool-call .tc-key { color:#516DA2; font-family:ui-monospace,Consolas,monospace; font-size:11px; }
.df-artifact-preview .tool-call .tc-val { color:#344B64; }
.df-artifact-preview .df-artifact-turn { margin-bottom:11px; border-radius:12px; box-shadow:0 4px 14px rgba(31,86,149,.04); }
.df-artifact-preview .df-artifact-turn-head { min-height:44px; padding:9px 12px; background:#F7FAFF; }
.df-artifact-preview .df-artifact-turn-head strong { font-size:12px; }
.df-artifact-preview .df-artifact-turn-head small { font-size:11px; }
.df-artifact-preview .df-artifact-turn-body { padding:12px; }
.df-artifact-preview .df-artifact-turn-columns { display:grid; grid-template-columns:minmax(0,1fr); gap:10px; min-width:0; }
.df-artifact-preview .df-artifact-turn-panel { min-width:0; padding:11px; border:1px solid #E4ECF6; border-radius:9px; background:#FBFDFF; }
.df-artifact-preview .df-artifact-turn-panel[data-role="input"] { border-color:#DCE9F8; background:#F5F9FF; }
.df-artifact-preview .df-artifact-turn-panel[data-role="output"] { border-color:#DDEFE8; background:#F8FCFA; }
.df-artifact-preview .df-artifact-panel-label { display:flex; align-items:center; gap:7px; margin-bottom:9px; color:#365776; font-size:11px; font-weight:750; }
.df-artifact-preview .df-artifact-panel-label b { display:grid; place-items:center; width:21px; height:21px; border-radius:6px; background:#DCEBFD; color:#145FBB; font-size:11px; }
.df-artifact-preview .df-artifact-turn-panel[data-role="output"] .df-artifact-panel-label b { background:#DCF3E9; color:#15815A; }
.df-artifact-preview .df-artifact-turn-panel .bubble.bub-user,
.df-artifact-preview .df-artifact-turn-panel .bubble.bub-assistant { width:100%; max-width:100%; align-self:stretch; background:#fff; }
.df-artifact-preview .df-artifact-turn-panel .bubble.bub-user { border-color:#D8E7FB; }
.df-artifact-preview .df-artifact-turn-panel .bubble.bub-assistant { border-color:#D6EADD; }
.df-artifact-preview .df-artifact-more { padding:10px 12px; }
.df-artifact-preview .df-artifact-more summary { font-size:12px; }
.df-artifact-preview .df-artifact-trace { padding-left:2px; }
.df-artifact-preview .df-artifact-trace-item { margin-left:14px; padding:0 0 15px 20px; border-left:2px solid #C8DBF2; }
.df-artifact-preview .df-artifact-trace-marker { top:7px; left:-13px; width:24px; height:24px; font-size:11px; }
.df-artifact-preview .df-artifact-trace-card { border-radius:11px; box-shadow:0 3px 12px rgba(41,86,135,.04); }
.df-artifact-preview .df-artifact-trace-head { flex-wrap:wrap; gap:5px 9px; min-height:43px; padding:9px 12px; }
.df-artifact-preview .df-artifact-trace-head b { color:#165DAD; font-size:11px; }
.df-artifact-preview .df-artifact-trace-head strong { flex:1 1 100px; min-width:0; overflow:visible; text-overflow:clip; white-space:normal; overflow-wrap:anywhere; font-size:12px; }
.df-artifact-preview .df-artifact-trace-head small { margin-left:auto; color:#73869E; font-size:10px; line-height:1.4; white-space:normal; text-align:right; }
.df-artifact-preview .df-artifact-trace-item[data-verified="true"] .df-artifact-trace-head small { color:#147753; font-weight:750; }
.df-artifact-preview .df-artifact-trace-item[data-kind="tool"] .df-artifact-trace-head b { color:#7552AD; }
.df-artifact-preview .df-artifact-trace-item[data-failed="true"] .df-artifact-trace-head b { color:#B43850; }
.df-artifact-preview .df-artifact-trace-body { padding:12px; }
.df-artifact-preview .df-artifact-trace-body .bubbles { gap:10px; }
.df-artifact-preview .df-artifact-verification { align-items:center; gap:7px; padding:10px 11px; border-radius:9px; font-size:11px; line-height:1.45; }
.df-artifact-preview .df-artifact-verification b { margin-right:2px; color:#116B50; font-size:11px; white-space:nowrap; }
.df-artifact-preview .df-artifact-verification span + span { padding-left:8px; }
.df-artifact-preview .df-artifact-failure { padding:11px 12px; border-radius:9px; font-size:11px; }
.df-artifact-preview .df-artifact-failure strong { font-size:12px; }
.df-artifact-preview .df-artifact-failure small { margin-top:3px; line-height:1.5; overflow-wrap:anywhere; }
@container (min-width:730px) {
  .df-artifact-preview .df-artifact-turn-columns { grid-template-columns:repeat(2,minmax(0,1fr)); }
}
@container (max-width:480px) {
  .df-artifact-preview { padding:11px; }
  .df-artifact-preview .df-artifact-turn-body, .df-artifact-preview .df-artifact-trace-body { padding:9px; }
  .df-artifact-preview .df-artifact-trace-head small { width:100%; text-align:left; }
}
</style>"""
