"""Styles scoped to the interactive workflow-run inspector."""


def workflow_run_styles() -> str:
    return """
<style>
.df-run-meta {display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin:0 0 9px;
  color:#63758b;font-size:12px;line-height:1.5}
.df-run-meta b {color:#47617e;font-weight:650}.df-run-meta i {color:#b4c0ce;font-style:normal}
.df-run-overview {display:flex;flex-wrap:wrap;align-items:center;gap:0;padding:14px 7px;margin:4px 0 17px;
  border:1px solid #e1e8f2;border-radius:14px;background:#fff}
.df-run-overview .df-run-pill {display:inline-flex;align-items:center;gap:5px;padding:6px 10px;
  background:transparent;border:0;border-right:1px solid #e5ebf3;border-radius:0;color:#526780;font-size:13px;font-weight:600}
.df-run-overview .df-run-pill:last-child {border-right:0}
.df-run-overview .df-run-pill strong {color:#1769d7;font-weight:750}
.df-run-overview .df-run-pill[data-status="completed"] strong {color:#16815c}
.df-run-overview .df-run-pill[data-status="failed"] strong,
.df-run-overview .df-run-pill[data-status="cancelled"] strong {color:#bc4650}
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
  background:#eaf3ff;color:#2376db;font-size:17px}
.df-run-inspector-head strong {display:block;font-size:15px;color:#172842}
.df-run-inspector-head small {display:block;margin-top:2px;font-size:12px;color:#74859e}
.df-run-stat-grid {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:12px 0}
.df-run-stat {padding:13px;border:0;border-radius:11px;background:#f4f7fc}
.df-run-stat b {display:block;font-size:18px;color:#184b8e;line-height:1.2}
.df-run-stat span {display:block;font-size:12px;color:#63758b;margin-top:3px}
.df-run-config {display:grid;gap:0;margin:8px 0 0;border:0;border-radius:11px;overflow:hidden;background:#f4f7fc}
.df-run-config div {display:flex;justify-content:space-between;align-items:flex-start;gap:14px;padding:9px 11px;
  background:transparent;border-bottom:1px solid #e3eaf4;font-size:12px;line-height:1.6}
.df-run-config div:last-child {border-bottom:none}
.df-run-config span {flex:0 0 38%;color:#71829a}
.df-run-config strong {flex:1;color:#263b59;text-align:right;font-weight:600;overflow-wrap:anywhere}
.df-run-log {display:grid;gap:0;margin:0 0 16px;padding:3px 15px;
  border:1px solid #e1eaf6;border-radius:12px;background:#fff}
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
  color:#4373ad;font-size:12px;font-weight:650}
.df-run-event-sub span {min-width:0;flex:1}
.df-run-empty {padding:19px 14px;border:1px dashed #d7e4f3;border-radius:10px;background:#f9fcff;
  color:#74859d;font-size:13px;text-align:center}
.df-run-quality-grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px;margin:10px 0 16px}
.df-run-quality-card {padding:15px;border:1px solid #e1e8f2;border-radius:14px;background:#fff;box-shadow:0 4px 16px #1a3e6d04}
.df-run-quality-card small {display:block;color:#697e9c;font-size:12px}
.df-run-quality-card b {display:block;color:#1d5eab;font-size:23px;line-height:1.35;margin-top:5px}
.df-run-quality-card span {display:block;color:#7b8ca1;font-size:12px;margin-top:2px}
@media(prefers-reduced-motion:reduce) {[class*="st-key-flow_node_"] {transition:none} [class*="st-key-flow_node_"]:hover {transform:none}}
@media(max-width:900px) {.df-run-event-head {flex-wrap:wrap}
  .df-run-lane {flex-wrap:wrap}
  .df-dag-canvas {margin-left:0}}
@media(max-width:720px) {
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
  .df-run-overview {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;padding:12px}
  .df-run-overview .df-run-pill {min-width:0;align-items:flex-start;flex-direction:column;gap:3px;padding:6px 8px;border:0;font-weight:500;line-height:1.5}
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
