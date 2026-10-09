"""Styles scoped to the interactive workflow-run inspector."""


def workflow_run_styles() -> str:
    return """
<style>
.df-run-meta {display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin:0 0 9px;
  color:#63758b;font-size:12px;line-height:1.5}
.df-run-meta b {color:#47617e;font-weight:650}.df-run-meta i {color:#b4c0ce;font-style:normal}
.df-run-summary {margin:0 0 3px}
.df-run-summary-title {display:flex;align-items:center;flex-wrap:wrap;gap:8px 12px;margin:0 0 5px}
.df-run-summary-title h3 {flex:1 1 auto;min-width:0;margin:0;padding:0;color:#172842;
  font-size:21px;line-height:1.4;font-weight:650;letter-spacing:-.3px;overflow-wrap:anywhere}
.df-run-status {display:inline-flex;align-items:center;gap:6px;border-radius:6px;padding:3px 8px;
  font-size:12px;font-weight:600;line-height:1.5;color:#376ca7;background:linear-gradient(180deg,#f2f8ff,#e8f2ff);
  border:1px solid #d8e7f6;box-shadow:inset 0 1px 0 #ffffffc4,0 1px 2px #305f890a}
.df-run-status::before {content:"";width:6px;height:6px;border-radius:50%;background:currentColor;flex:none}
.df-run-status[data-status="completed"] {color:#18805f;background:linear-gradient(180deg,#f1fbf6,#e6f6ee);border-color:#cfeadd}
.df-run-status[data-status="failed"],.df-run-status[data-status="cancelled"] {color:#b34856;background:linear-gradient(180deg,#fff8f9,#ffedf0);border-color:#efd3d9}
.df-run-stage-count {color:#63758b;font-size:12px;line-height:1.5;white-space:nowrap}
.df-run-stage-count b {color:#47617e;font-weight:650;font-variant-numeric:tabular-nums}
[data-testid="stMainBlockContainer"] :is([class*="st-key-workflow-run-layout-"],
  [class*="st-key-workflow-run-canvas-"],[class*="st-key-workflow-run-actions-"],
  [class*="st-key-workflow-run-input-statistics-"])[data-testid="stVerticalBlock"] {
  border:0;border-radius:0;padding:0;background:transparent;box-shadow:none;
}
[class*="st-key-workflow-run-canvas-toolbar-"] .df-run-section {margin:0}
[class*="st-key-workflow-run-canvas-toolbar-"] .df-run-section small {margin-top:3px}
:is([class*="st-key-workflow-run-canvas-toolbar-"],
    [class*="st-key-workflow-run-canvas-toolbar-"] > [data-testid="stLayoutWrapper"])
    > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child {
  flex:1 1 0!important;width:auto!important;min-width:0!important;
}
:is([class*="st-key-workflow-run-canvas-toolbar-"],
    [class*="st-key-workflow-run-canvas-toolbar-"] > [data-testid="stLayoutWrapper"])
    > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child {
  flex:0 0 180px!important;width:180px!important;max-width:180px;min-width:0!important;
}
[class*="st-key-workflow-run-actions-"] {max-width:210px;margin-left:auto!important;width:100%}
[class*="st-key-workflow-run-actions-"]:has(> [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"]) {max-width:422px}
.df-run-input-summary {display:flex;align-items:baseline;flex-wrap:wrap;gap:7px 20px;margin:0;padding:0}
.df-run-input-summary > span {display:inline-flex;align-items:baseline;gap:6px;white-space:nowrap;line-height:1.5}
.df-run-input-summary b {color:#345778;font-size:15px;font-weight:650;font-variant-numeric:tabular-nums}
.df-run-input-summary small {color:#63758b;font-size:12px}
.df-run-section {display:flex;align-items:flex-start;justify-content:space-between;gap:14px;margin:3px 0 12px}
.df-run-section strong {display:block;color:#172842;font-size:17px;letter-spacing:.01em}
.df-run-section small {display:block;color:#5c708a;font-size:13px;line-height:1.6;margin-top:5px}
.df-run-section .df-run-section-tag {font-size:12px;color:#2772d2;background:#edf5ff;padding:5px 9px;
  border-radius:7px;white-space:nowrap}
.df-run-lane {display:flex;align-items:center;justify-content:center;gap:8px;padding:9px 12px;margin:0 0 14px;
  border:1px solid #e7eff9;border-radius:10px;background:#f8fbff;color:#6c85a7;font-size:12px;font-weight:650}
.df-run-lane b {color:#246ac7;background:#e8f1ff;border-radius:6px;padding:4px 7px;font-weight:700}
.df-run-lane span {font-size:16px;color:#84a9dc}
.df-dag-wrap {margin:1px 0 15px;padding:15px 16px 11px;overflow:hidden;
  border:1px solid #dce9f9;border-radius:13px;
  background-color:#f8fbff;
  background-image:radial-gradient(#e6eef9 .8px,transparent .8px),
    radial-gradient(circle at 78% 15%,rgba(223,238,255,.58),transparent 46%);
  background-size:17px 17px,100% 100%;box-shadow:inset 0 1px 0 #fff}
.df-dag-canvas {position:relative;width:100%;max-width:760px;margin:0 auto;
  container-type:inline-size}
.df-dag-lane {position:absolute;top:3%;color:#7389a8;font-size:12px;font-weight:700;
  letter-spacing:.07em;white-space:nowrap}
.df-dag-edge {position:absolute;height:2px;transform-origin:0 50%;border-radius:2px;
  background:#a6bdd9;opacity:.85;z-index:0}
.df-dag-edge::after {content:"";position:absolute;right:-1px;top:-4px;width:0;height:0;
  border-top:5px solid transparent;border-bottom:5px solid transparent;border-left:8px solid #a6bdd9}
.df-dag-edge[data-status="completed"] {background:#45b89a}
.df-dag-edge[data-status="completed"]::after {border-left-color:#45b89a}
.df-dag-edge[data-status="running"] {background:#3087e7;height:3px}
.df-dag-edge[data-status="running"]::after {border-left-color:#3087e7}
.df-dag-edge[data-status="attention"] {background:#e48280;height:3px}
.df-dag-edge[data-status="attention"]::after {border-left-color:#e48280}
.df-dag-node {position:absolute;z-index:1;box-sizing:border-box;display:flex;align-items:center;
  gap:7px;padding:8px 10px;border:1px solid #cddff4;border-radius:11px;background:#fff;
  box-shadow:0 3px 9px rgba(42,93,154,.09)}
.df-dag-icon {display:grid;place-items:center;flex:0 0 30px;height:32px;border-radius:8px;
  background:#e7f1ff;color:#2475d5;font-size:17px;font-weight:700}
.df-dag-copy {display:block;min-width:0;overflow:hidden}
.df-dag-copy strong {display:block;color:#203a5b;font-size:13px;line-height:1.35;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.df-dag-copy small {display:block;margin-top:4px;color:#63758b;font-size:12px;line-height:1.25;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.df-dag-status {position:absolute;top:7px;right:7px;width:7px;height:7px;border-radius:50%;
  background:#a9b8ca;box-shadow:0 0 0 2px #fff}
.df-dag-node[data-stage="multiturn"] .df-dag-icon,
.df-dag-node[data-stage="agent"] .df-dag-icon {background:#e1f8f3;color:#138a78}
.df-dag-node[data-stage="preference"] .df-dag-icon,
.df-dag-node[data-stage="cot"] .df-dag-icon {background:#f0eaff;color:#8054c7}
.df-dag-node[data-stage="gsm8k"] .df-dag-icon {background:#fff4dd;color:#b77a17}
.df-dag-node[data-stage="package"] .df-dag-icon {background:#e1f8ea;color:#13885d}
.df-dag-node[data-status="completed"] {border-color:#87d0b5;background:#fbfffd}
.df-dag-node[data-status="completed"] .df-dag-status {background:#20aa78}
.df-dag-node[data-status="running"] {border-color:#468fe6;background:#f3f9ff}
.df-dag-node[data-status="running"] .df-dag-status {background:#267de0}
.df-dag-node[data-status="failed"],.df-dag-node[data-status="cancelled"] {border-color:#e6a8ae;background:#fff8f9}
.df-dag-node[data-status="failed"] .df-dag-status,
.df-dag-node[data-status="cancelled"] .df-dag-status {background:#d9606c}
.df-dag-node[data-selected="true"] {border:2px solid #247de5;
  box-shadow:0 4px 12px rgba(34,111,215,.2)}
.df-dag-node[data-intermediate="true"] {border-style:dashed}
.df-dag-caption {display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px 14px;
  padding:10px 2px 1px;border-top:1px solid #e3edf8;color:#7186a3;font-size:12px;line-height:1.45}
.df-dag-caption span:first-child {color:#386eaf;font-weight:650}
.df-dag-legend {display:flex;align-items:center;gap:4px}
.df-dag-legend i {display:inline-block;width:7px;height:7px;margin:0 3px 0 9px;border-radius:50%;background:#a9b8ca}
.df-dag-legend i[data-status="completed"] {background:#20aa78}
.df-dag-legend i[data-status="running"] {background:#267de0}
.df-dag-legend i[data-status="attention"] {background:#d9606c}
[class*="st-key-flow_node_"] {margin:0 0 7px;padding:2px;border:1px solid #e2eaf4;border-radius:11px;
  background:linear-gradient(145deg,#fff,#fbfdff);box-shadow:0 2px 10px rgba(24,65,118,.04);
  transition:border-color .16s,box-shadow .16s,transform .16s}
[class*="st-key-flow_node_"]:hover {border-color:#8ebaf2;box-shadow:0 7px 17px rgba(24,89,175,.1);transform:translateY(-1px)}
[class*="st-key-flow_node_"] .stButton button {display:block;width:100%;min-height:72px;padding:8px 10px;
  border:none;border-radius:9px;background:transparent;color:#182b48;text-align:left;box-shadow:none}
[class*="st-key-flow_node_"] .stButton button:hover,
[class*="st-key-flow_node_"] .stButton button:focus {border:none;background:transparent;color:#125cb6;box-shadow:none}
[class*="st-key-flow_node_"] .stButton button p {font-size:13px;line-height:1.45;white-space:pre-line}
[class*="st-key-flow_node_selected_"] {border-color:#3488ee;box-shadow:0 0 0 2px rgba(35,118,232,.14),0 7px 20px rgba(35,118,232,.08)}
[class*="st-key-flow_node_selected_"] .stButton button {background:linear-gradient(125deg,#f2f8ff,#fff)}
[class*="st-key-flow_node_running_"] {border-color:#68a6ef;background:#f8fbff}
[class*="st-key-flow_node_completed_"] {border-color:#b9e4d3;background:#fbfffd}
[class*="st-key-flow_node_skipped_"] {background:#f8fafd;border-color:#e7ecf3;opacity:.78}
[class*="st-key-flow_node_failed_"], [class*="st-key-flow_node_cancelled_"] {border-color:#f0c2c7;background:#fffafa}
.df-run-inspector-head {display:flex;align-items:center;gap:11px;padding:3px 0 13px;border-bottom:1px solid #edf1f7}
.df-run-inspector-head b {display:grid;place-items:center;width:36px;height:36px;border-radius:10px;
  background:linear-gradient(145deg,#f9fcff,#d5e8ff);border:1px solid #c7def7;
  box-shadow:inset 0 1px 0 #fff,0 3px 6px #346f9f1c;color:#2376db;font-size:17px}
.df-run-inspector-head strong {display:block;font-size:15px;color:#172842}
.df-run-inspector-head small {display:block;margin-top:2px;font-size:12px;color:#74859e}
.df-run-stat-grid {display:flex;align-items:baseline;flex-wrap:wrap;gap:8px 22px;margin:12px 0}
.df-run-stat {display:inline-flex;align-items:baseline;gap:7px;padding:0;border:0;background:transparent}
.df-run-stat b {font-size:18px;color:#184b8e;line-height:1.2;font-variant-numeric:tabular-nums}
.df-run-stat span {font-size:12px;color:#63758b}
.df-run-config {display:grid;gap:0;margin:8px 0 0;border:1px solid #dce6f2;border-radius:12px;overflow:hidden;
  background:linear-gradient(140deg,#f1f6fc,#f8fbff);box-shadow:inset 0 1px 0 #fff,inset 0 2px 5px #38669405}
.df-run-config div {display:flex;justify-content:space-between;align-items:flex-start;gap:14px;padding:9px 11px;
  background:transparent;border-bottom:1px solid #e3eaf4;font-size:12px;line-height:1.6}
.df-run-config div:last-child {border-bottom:none}
.df-run-config div:nth-child(even) {background:#ffffff6b}
.df-run-config span {flex:0 0 38%;color:#71829a}
.df-run-config strong {flex:1;color:#263b59;text-align:right;font-weight:600;overflow-wrap:anywhere}
.df-run-log {display:grid;gap:0;margin:0 0 16px;padding:3px 15px;
  border:1px solid #d8e5f3;border-radius:15px;background:linear-gradient(150deg,#fff,#f5f9ff);
  box-shadow:inset 0 1px 0 #fff,0 2px 4px #2e5c8907,0 10px 24px -18px #3d6c9b38}
.df-run-event {position:relative;display:grid;grid-template-columns:14px minmax(0,1fr);align-items:start;gap:10px;
  padding:14px 0;font-size:13px;color:#4d617e;line-height:1.6}
.df-run-event:not(:last-child) {border-bottom:1px solid #edf2f8}
.df-run-event:not(:last-child)::before {content:"";position:absolute;top:26px;bottom:-11px;left:4px;
  width:1px;background:#d7e6f6}
.df-run-event i {position:relative;z-index:1;display:block;width:9px;height:9px;margin-top:4px;
  border-radius:50%;background:#2d83e7;box-shadow:0 0 0 3px #e9f3ff}
.df-run-event[data-kind="stage_completed"] i,.df-run-event[data-kind="run_finished"] i {background:#15a16f;box-shadow:0 0 0 3px #e5f8ef}
.df-run-event[data-kind="run_failed"] i,.df-run-event[data-kind="run_cancelled"] i {background:#d75b63;box-shadow:0 0 0 3px #fff0f1}
.df-run-event-body {min-width:0}
.df-run-event-head {display:flex;align-items:baseline;justify-content:space-between;gap:9px}
.df-run-event time {color:#63758b;font-variant-numeric:tabular-nums;white-space:nowrap}
.df-run-event strong {color:#263950;font-size:14px;font-weight:650}
.df-run-event-sub {display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 10px;margin-top:4px;
  color:#7b8ca3;overflow-wrap:anywhere;line-height:1.5}
.df-run-event-sub b {display:inline-block;padding:2px 6px;border-radius:5px;background:#edf5ff;
  border:1px solid #dce9f7;box-shadow:inset 0 1px 0 #ffffffc4;color:#4373ad;font-size:12px;font-weight:650}
.df-run-event-sub span {min-width:0;flex:1}
.df-run-empty {padding:19px 14px;border:1px dashed #d7e4f3;border-radius:10px;background:#f9fcff;
  color:#74859d;font-size:13px;text-align:center}
.df-run-quality-grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px;margin:10px 0 16px}
.df-run-quality-card {padding:15px;border:1px solid #d5e3f1;border-radius:15px;
  background:radial-gradient(ellipse at 100% 0,#e4f2ff91,transparent 65%),linear-gradient(145deg,#fff,#f4f9ff);
  box-shadow:var(--df-shadow-panel,0 2px 3px #31598208,0 12px 26px -18px #39689738,inset 0 1px 0 #fff)}
.df-run-quality-card small {display:block;color:#697e9c;font-size:12px}
.df-run-quality-card b {display:block;color:#1d5eab;font-size:23px;line-height:1.35;margin-top:5px}
.df-run-quality-card span {display:block;color:#7b8ca1;font-size:12px;margin-top:2px}
/* Native run details remain owned by Streamlit. The canvas positions this
   container next to its selected node; its host must not reserve a blank row. */
[class*="st-key-workflow-run-node-panel-"]:not([data-workflow-inspector]) {display:none}
[data-workflow-inspector-host="floating"]:has([class*="st-key-workflow-run-node-panel-"]) {
  height:0!important;min-height:0!important;margin:0!important;padding:0!important;overflow:visible!important;
}
[data-testid="stApp"] [class*="st-key-workflow-run-node-panel-"][data-workflow-inspector="floating"] {
  box-sizing:border-box;border:1px solid #bdd4ed;border-top:2px solid #4d94e8;border-radius:19px;
  padding:0 18px 18px;background:linear-gradient(155deg,#fcfeff 0%,#f5f9fff5 52%,#fffffffa 100%);
  box-shadow:inset 0 1px 0 #fff,0 0 0 4px #ffffff8c,0 6px 14px -4px #284e782b,0 25px 65px -15px #244e804d;
  backdrop-filter:blur(16px) saturate(1.12);
  overscroll-behavior:contain;scrollbar-width:thin;scrollbar-color:#b5c9e2 transparent;gap:14px;
}
[class*="st-key-workflow-run-node-panel-"][data-workflow-inspector="floating"]
  > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-workflow-run-node-header-"]) {
  position:sticky;top:0;z-index:2;background:linear-gradient(180deg,#fcfeff,#f5f9ff);
}
[class*="st-key-workflow-run-node-header-"] {padding:14px 0 12px;border-bottom:1px solid #d9e5f2;box-shadow:0 1px 0 #fff;gap:10px}
[class*="st-key-workflow-run-node-header-"] .df-run-inspector-head {padding:0;border:0}
[class*="st-key-workflow-run-node-header-"] .df-run-inspector-head > b {flex:0 0 36px}
[class*="st-key-workflow-run-node-header-"] .df-run-inspector-head > div {min-width:0;overflow-wrap:anywhere}
[class*="st-key-workflow-run-node-header-"] .df-run-inspector-head strong {font-size:17px;line-height:1.4}
[class*="st-key-workflow-run-node-header-"] .df-run-inspector-head small {font-size:13px;color:#60748d}
[class*="st-key-workflow-run-node-header-"] button {min-height:38px;padding:5px 10px;
  color:#3c5c80;border-color:#d0e0ef;background:linear-gradient(180deg,#fff,#f0f6fd);
  box-shadow:inset 0 1px 0 #fff,0 2px 3px #315c850d;
  transition:background .18s,box-shadow .18s,border-color .18s,transform .18s}
[class*="st-key-workflow-run-node-header-"] button:hover {background:linear-gradient(180deg,#fff,#e8f3ff);border-color:#a8c8ee;color:#185fad;
  box-shadow:inset 0 1px 0 #fff,0 4px 9px #3d78b21a;transform:translateY(-1px)}
[class*="st-key-workflow-run-node-header-"] button:active {transform:translateY(1px);box-shadow:inset 0 2px 4px #315e8b14}
[class*="st-key-workflow-run-node-panel-"] [data-testid="stMarkdownContainer"] p {font-size:14px;line-height:1.6}
[class*="st-key-workflow-run-node-panel-"] [data-testid="stCaptionContainer"] p {font-size:12px;line-height:1.6;color:#60748d}
[class*="st-key-workflow-run-node-panel-"] .df-run-reader-heading {margin:0}
[class*="st-key-workflow-run-node-panel-"] .df-run-reader-heading strong {font-size:14px;font-weight:650}
[class*="st-key-workflow-run-node-panel-"] .df-run-recipe-heading {margin:10px 0 0;padding-top:14px;border-top:1px solid #e3ebf5}
[class*="st-key-workflow-run-node-panel-"] .df-run-recipe-heading strong {font-size:15px}
[class*="st-key-workflow-run-node-panel-"] .df-run-config div {font-size:13px;gap:12px;padding:9px 11px}
[class*="st-key-workflow-run-node-panel-"] .df-run-config span {color:#60748d}
[class*="st-key-workflow-run-node-panel-"] .df-run-stat-grid {margin:0}
@media(prefers-reduced-motion:reduce) {
  [class*="st-key-flow_node_"],[class*="st-key-workflow-run-node-header-"] button {transition:none}
  [class*="st-key-flow_node_"]:hover,[class*="st-key-workflow-run-node-header-"] button:is(:hover,:active) {transform:none}
}
@media(max-width:900px) {.df-run-event-head {flex-wrap:wrap}
  .df-run-lane {flex-wrap:wrap}
  .df-dag-canvas {margin-left:0}}
@media(max-width:720px) {
  [class*="st-key-workflow-run-actions-"],
  [class*="st-key-workflow-run-actions-"]:has(> [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"]) {max-width:none}
  :is([class*="st-key-workflow-run-canvas-toolbar-"],
      [class*="st-key-workflow-run-canvas-toolbar-"] > [data-testid="stLayoutWrapper"]) > [data-testid="stHorizontalBlock"] {
    display:grid!important;grid-template-columns:minmax(0,1fr);gap:8px;
  }
  :is([class*="st-key-workflow-run-canvas-toolbar-"],
      [class*="st-key-workflow-run-canvas-toolbar-"] > [data-testid="stLayoutWrapper"])
      > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:is(:first-child,:last-child) {
    flex:none!important;width:100%!important;max-width:none;min-width:0!important;
  }
  [class*="st-key-workflow-run-node-header-"] button {min-height:44px}
  /* These rows belong to the persisted-run inspector. Never target generic
     columns or the node configuration popup in the authoring workbench. */
  [class*="st-key-workflow-run-layout"] > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"],
  [class*="st-key-workflow-run-actions"] > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"],
  [class*="st-key-workflow-run-delivery-actions"] > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"],
  [class*="st-key-workflow-run-output-heading"] > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] {
    display:grid !important;grid-template-columns:minmax(0,1fr);gap:12px;
  }
  [class*="st-key-workflow-run-cache-statistics"] > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"],
  [class*="st-key-workflow-run-input-statistics"] > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] {
    display:grid !important;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;
  }
  :is([class*="st-key-workflow-run-layout"],[class*="st-key-workflow-run-actions"],
      [class*="st-key-workflow-run-delivery-actions"],[class*="st-key-workflow-run-output-heading"],
      [class*="st-key-workflow-run-cache-statistics"],[class*="st-key-workflow-run-input-statistics"])
      > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    flex:none !important;width:100% !important;min-width:0 !important;max-width:100%;
  }
  [class*="st-key-workflow-run-actions"] button,
  [class*="st-key-workflow-run-delivery-actions"] button,
  [class*="st-key-workflow-run-output-heading"] button {min-height:44px}
  .df-run-summary-title {gap:7px 10px}
  .df-run-summary-title h3 {flex-basis:100%;font-size:20px}
  .df-run-section {flex-wrap:wrap;gap:8px}
  .df-run-section > div {min-width:0;flex:1 1 180px}
  .df-run-section .df-run-section-tag {align-self:flex-start}
  .df-run-inspector-head {align-items:flex-start}
  .df-run-inspector-head > b {flex:0 0 36px}
  .df-run-inspector-head > div {min-width:0;overflow-wrap:anywhere}
  .df-run-stat-grid {grid-template-columns:repeat(2,minmax(0,1fr))}
  .df-run-stat b {overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
  .df-run-stat span {line-height:1.5;overflow-wrap:anywhere}
  .df-run-config div {display:grid;grid-template-columns:minmax(0,1fr);gap:3px}
  .df-run-config strong {text-align:left}
  .df-run-log {padding:3px 12px}
  .df-run-event {gap:8px;padding:12px 0}
}
</style>
"""
