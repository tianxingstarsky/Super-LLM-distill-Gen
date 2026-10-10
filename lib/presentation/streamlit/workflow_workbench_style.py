"""Workbench-only styles for source, target and generation configuration."""
from __future__ import annotations


WORKBENCH_STYLE = """<style>
/* Override the generic segmented-control flex layout only for source cards.
   Container width accounts for the expanded sidebar and nested workbench. */
[data-testid="stApp"] [class*="st-key-workflow-source-mode"] {
  container-type:inline-size;container-name:workbench-sources;width:100%}
[data-testid="stApp"] [class*="st-key-workflow-source-mode"] [data-testid="stButtonGroup"] [role="radiogroup"]:has(> button[data-variant="segmented_control"]) {
  display:grid;grid-template-columns:repeat(4,minmax(0,1fr));width:100%;max-width:none;
  gap:12px;padding:0;border:0;background:transparent}
[data-testid="stApp"] [class*="st-key-workflow-source-mode"] [data-testid="stButtonGroup"] [role="radiogroup"]:has(> button[data-variant="segmented_control"]) > button {
  width:100%;min-width:0;height:auto;white-space:normal}
@container workbench-sources (max-width:959px) {
  [data-testid="stApp"] [class*="st-key-workflow-source-mode"] [data-testid="stButtonGroup"] [role="radiogroup"]:has(> button[data-variant="segmented_control"]) {
    grid-template-columns:repeat(2,minmax(0,1fr))}
}
@container workbench-sources (max-width:559px) {
  [data-testid="stApp"] [class*="st-key-workflow-source-mode"] [data-testid="stButtonGroup"] [role="radiogroup"]:has(> button[data-variant="segmented_control"]) {
    grid-template-columns:minmax(0,1fr)}
}
.st-key-workbench-layout [data-testid="stColumn"] {min-width:0}
.st-key-workbench-source-entry [data-baseweb="select"] > div {max-height:144px;overflow-y:auto;scrollbar-width:thin}
.st-key-workbench-source-entry [data-testid="stTextArea"] textarea {resize:vertical}
.st-key-workbench-targets .df-section-heading {margin:0;padding:0;border:0}
.st-key-workbench-targets [data-testid="stToggle"] {margin-top:2px}
.st-key-workbench-targets .df-wb-plan {font-size:12px}
.df-source-issue {display:block;padding:11px 14px;border:1px solid #e8cdaa;border-radius:10px;
  background:linear-gradient(145deg,#fffcf5,#fff7e8);color:#805e2c;text-decoration:none;font-size:13px;line-height:1.6;
  box-shadow:inset 0 1px 0 #fff;transition:background .18s,border-color .18s}
.df-source-issue:hover {background:#fff2da;border-color:#dcb576;color:#6d4c1f}
.df-source-issue:focus-visible {outline:3px solid #83b7f066;outline-offset:2px}
#workflow-source-entry {scroll-margin-top:90px}
.st-key-workbench-entry-bar {container-type:inline-size;container-name:workbench-entry}
.st-key-workbench-entry-bar .df-wizard-step {flex:1 1 0;min-width:0}
.st-key-workbench-entry-bar .df-wizard-step > span {min-width:0}
.st-key-workbench-entry-bar .df-wizard-step :is(strong,small) {white-space:normal;line-height:1.45}
.st-key-workbench-entry-bar .df-wizard-steps > i {width:12px}
@container workbench-entry (max-width:1000px) {
  .st-key-workbench-entry-bar [data-testid="stHorizontalBlock"] {flex-wrap:wrap;gap:12px}
  .st-key-workbench-entry-bar [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {flex:1 1 100%;width:100%;min-width:0}
}
[data-testid="stApp"] .st-key-workbench-canvas-panel {position:relative;
  background:radial-gradient(ellipse at 92% 0,#e8f3ff7d,transparent 50%),linear-gradient(155deg,#fff,#f3f8fe);
  border:1px solid #d2e2f2;border-radius:21px;
  box-shadow:inset 0 1px 0 #fff,0 2px 4px #31598208,0 14px 32px -18px #3b6c9e42}
.st-key-workbench-canvas-panel .df-panel-icon {background:linear-gradient(145deg,#f7fbff,#d3e8ff);
  border:1px solid #c9dff7;box-shadow:inset 0 1px 0 #fff,0 3px 6px #4c88c124}
.st-key-workbench-canvas-panel .df-section-heading {padding-bottom:14px;margin-bottom:2px;border-bottom:0}
[data-testid="stApp"] .st-key-workbench-targets {background:linear-gradient(145deg,#fff,#f3f8ff);border-color:#d2e2f2;
  box-shadow:inset 0 1px 0 #fff,0 3px 6px #365f8c07,0 12px 26px -20px #467ba740}
.st-key-workbench-node-panel {border-top:2px solid #78acf1;background:linear-gradient(160deg,#f9fcff,#fff 45%)}
[data-workflow-inspector-host="floating"] {height:0!important;min-height:0!important;margin:0!important;padding:0!important;overflow:visible!important}
[data-testid="stApp"] .st-key-workbench-node-panel[data-workflow-inspector="floating"] {
  border:1px solid #bdd4ed;border-top:2px solid #4d94e8;border-radius:19px;
  padding:0 18px 18px;background:linear-gradient(155deg,#fcfeff 0%,#f5f9fff5 52%,#fffffffa 100%);
  box-shadow:inset 0 1px 0 #fff,0 0 0 4px #ffffff8c,0 6px 14px -4px #284e782b,0 25px 65px -15px #244e804d;
  backdrop-filter:blur(16px) saturate(1.12);
  overscroll-behavior:contain;scrollbar-width:thin;scrollbar-color:#b5c9e2 transparent;gap:14px}
.st-key-workbench-node-panel[data-workflow-inspector="floating"] > [data-testid="stLayoutWrapper"]:has(> .st-key-workbench-node-header) {
  position:sticky;top:0;z-index:2;background:linear-gradient(180deg,#fcfeff,#f5f9ff);
}
.st-key-workbench-node-panel[data-workflow-inspector="floating"] .st-key-workbench-node-header {
  padding:16px 0 13px;border-bottom:1px solid #d9e5f2;box-shadow:0 1px 0 #fff}
.st-key-workbench-node-header .df-section-heading {margin-bottom:0;padding-bottom:0;border-bottom:0}
.st-key-workbench-node-header .df-section-heading strong {font-size:17px;letter-spacing:-.25px}
.st-key-workbench-node-header .df-section-heading small {font-size:12px;color:#60748d}
.st-key-workbench-node-header .df-panel-icon {width:34px;height:34px;border-radius:11px;border:1px solid #c9dff7;
  background:linear-gradient(145deg,#f9fcff,#d3e7fe);box-shadow:inset 0 1px 0 #fff,0 3px 6px #4776a51c}
.st-key-workbench-node-header button {min-height:36px;padding:4px 10px;color:#3c5c80;border-color:#d0e0ef;
  background:linear-gradient(180deg,#fff,#f0f6fd);box-shadow:inset 0 1px 0 #fff,0 2px 3px #315c850d;
  transition:background .18s,box-shadow .18s,border-color .18s,transform .18s}
.st-key-workbench-node-header button:hover {background:linear-gradient(180deg,#fff,#e8f3ff);border-color:#a8c8ee;color:#185fad;
  box-shadow:inset 0 1px 0 #fff,0 4px 9px #3d78b21a;transform:translateY(-1px)}
.st-key-workbench-node-header button:active {transform:translateY(1px);box-shadow:inset 0 2px 4px #315e8b14}
.st-key-workbench-node-panel [data-testid="stTabs"] :is([data-baseweb="tab-list"],[role="tablist"]) {gap:24px}
.st-key-workbench-node-panel [data-testid="stTabs"] :is([data-baseweb="tab"],[role="tab"]) {font-size:14px;height:42px;font-weight:550}
.st-key-workbench-node-panel [data-testid="stTabs"] :is([data-baseweb="tab-panel"],[role="tabpanel"]) {padding-top:16px}
.st-key-workbench-node-panel [data-testid="stMarkdownContainer"] p {font-size:14px;line-height:1.65;color:#334b67}
.st-key-workbench-node-panel button [data-testid="stMarkdownContainer"] p {color:inherit}
.st-key-workbench-node-panel [data-testid="stCaptionContainer"] p {font-size:12px;line-height:1.6;color:#64768d;opacity:1}
.st-key-workbench-node-panel :is(input,textarea) {font-size:14px!important;line-height:1.55}
.st-key-workbench-node-panel [data-testid="stWidgetLabel"] p {font-size:13px;font-weight:500;color:#334b67}
.st-key-workbench-node-panel [data-testid="stExpander"] summary {font-size:13px}
.st-key-workbench-node-panel :is(button,input,textarea,[role="tab"]):focus-visible {outline:3px solid #83b7f066;outline-offset:2px}
.st-key-workbench-node-panel [class*="st-key-workbench-node-prompts"] {padding-top:4px;border-bottom:0}
.st-key-workbench-source-preview {border-top:3px solid #83c9bf}
.df-knowledge-route {position:relative;overflow:hidden;padding:18px 12px 14px;border:1px solid #cfdff1;
  border-radius:15px;background:radial-gradient(circle at 85% 10%,#dcecff,transparent 48%),linear-gradient(155deg,#fff,#eef6ff);
  box-shadow:inset 0 1px 0 #fff,0 7px 15px -10px #366fa834;text-align:center}
.df-knowledge-route > i {display:grid;place-items:center;margin:0 auto 10px;width:52px;height:52px;border-radius:15px;
  background:linear-gradient(145deg,#78b4fa,#246ed6);color:white;font-size:34px;font-style:normal;box-shadow:0 7px 16px #367fe62b}
.df-knowledge-route > strong {display:block;color:#25496e;font-size:15px}
.df-knowledge-route p {color:#526a85;font-size:13px;line-height:1.6;margin:7px 0 14px}
.df-knowledge-route > div {display:flex;align-items:center;justify-content:center;gap:6px;flex-wrap:wrap}
.df-knowledge-route span {padding:6px 9px;border:1px solid #d9e7f6;border-radius:8px;background:linear-gradient(180deg,#fff,#f5f9ff);
  box-shadow:inset 0 1px 0 #fff,0 2px 3px #3464910b;color:#41658b;font-size:12px}
.df-knowledge-route b {color:#7e9fc5;font-weight:400}
.df-wb-plan {display:flex;align-items:center;flex-wrap:wrap;gap:5px 12px;
  margin:0;color:#365579;font-size:13px;line-height:1.6}
.df-wb-plan strong {color:#213b5f;font-size:13px}
.df-wb-plan small {color:#5a708b;font-size:12px}
.df-wb-plan-intermediate {color:#7054a8;font-size:12px}
.df-wb-plan-empty {color:#64768d;font-size:12px}
.df-wb-plan-detail {padding:2px 0 5px}
.df-wb-plan-detail p {margin:0 0 10px;color:#526a85;font-size:13px;line-height:1.6}
.df-wb-plan-edges {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.df-wb-plan-edge {display:flex;align-items:center;gap:8px;min-width:0;padding:9px 10px;
  border:1px solid #dce7f3;border-radius:9px;background:linear-gradient(150deg,#fff,#f0f6fe);
  box-shadow:inset 0 1px 0 #fff,0 2px 4px #3c658c08;color:#365579;font-size:12px}
.df-wb-plan-edge span {min-width:0;overflow-wrap:anywhere;line-height:1.45}
.df-wb-plan-edge i {flex:0 0 auto;color:#2d7ad0;font-size:14px;font-style:normal}
:is([class*="st-key-workbench-source-panel"],.st-key-workbench-upload-entry) [data-testid="stFileUploader"] section {
  background:linear-gradient(145deg,#f9fcff,#edf5ff);border-color:#b8d0ed;box-shadow:inset 0 1px 0 #fff,inset 0 2px 8px #356c9a05}
[data-testid="stApp"] [class*="st-key-workbench-submit"] {border-color:#cfe1f3;
  background:linear-gradient(120deg,#edf6ff,#fff 68%);box-shadow:inset 0 1px 0 #fff,0 9px 20px -15px #3977ad36}
[class*="st-key-workflow-create"] button[kind="primary"]:disabled,
[class*="st-key-workflow-create"] button[kind="primary"]:disabled:hover {
  background:#e8eef6;border-color:#c9d6e5;color:#53657c;box-shadow:none;cursor:not-allowed;opacity:1}
.df-wb-submit-summary {display:flex;align-items:center;flex-wrap:wrap;gap:9px 14px}
.df-wb-submit-summary b {display:grid;place-items:center;padding:6px 9px;border-radius:7px;
  background:#e6f0ff;color:#1b67c4;font-size:12px}
.df-wb-submit-summary strong {color:#1f3653;font-size:15px;overflow-wrap:anywhere}
.df-wb-submit-summary span {color:#526a85;font-size:12px;line-height:1.6;overflow-wrap:anywhere}
[class*="st-key-workbench-generation"] {padding:0 0 12px;border-bottom:1px solid #e0eaf5;margin-bottom:2px}
[class*="st-key-workbench-generation"] [data-testid="stToggle"] {margin-bottom:3px}
.df-generation-preview {padding:13px 14px;border:1px solid #cddff3;border-radius:12px;
  background:linear-gradient(130deg,#eaf4ff,#fcfeff);box-shadow:inset 0 1px 0 #fff,0 3px 8px -5px #3979b824;overflow:hidden}
.df-generation-preview > div {display:flex;align-items:center;gap:7px;flex-wrap:wrap;line-height:1.45}
.df-generation-preview i {display:grid;place-items:center;flex:0 0 24px;height:24px;border-radius:7px;
  background:#dceaff;color:#3575c9;font-size:17px;font-style:normal}
.df-generation-preview strong {color:#254c7b;font-size:14px;font-weight:650}
.df-generation-preview span {margin-left:auto;color:#4b73a2;font-size:12px;padding:3px 8px;
  background:#fff;border:1px solid #d8e6f7;border-radius:5px}
.df-generation-preview p {margin:8px 0 0;color:#49617d;font-size:13px;line-height:1.7;overflow-wrap:anywhere;white-space:pre-wrap}
.df-generation-preview.is-disabled {background:#f7f9fc;border-color:#e2e9f2}
.df-generation-preview.is-disabled i {background:#e8edf5;color:#7890ab}
.df-generation-preview.is-disabled strong,.df-generation-preview.is-disabled p {color:#71849b}
[class*="st-key-workbench-node-prompts"] {padding:12px 0;border-bottom:1px solid #e0eaf5}
[class*="st-key-workbench-node-prompts"] [data-testid="stTextArea"] textarea {
  background:#f8fbff;border-color:#cfdff2;font-size:14px;line-height:1.7}
[class*="st-key-workbench-node-prompts"] [data-testid="stFileUploader"] section {
  background:#f8fbff;border-color:#cfdff2}
.df-node-prompt-heading {display:flex;align-items:center;gap:8px;color:#294d78;
  font-size:14px;font-weight:650;line-height:1.5;margin-bottom:4px}
.df-node-prompt-heading i {display:grid;place-items:center;width:24px;height:24px;
  border-radius:7px;background:#e6efff;color:#3575c9;font-style:normal}
@media(prefers-reduced-motion:reduce) {
  .st-key-workbench-node-header button {transition:none}
  .st-key-workbench-node-header button:is(:hover,:active) {transform:none}
}
@media(max-width:1050px) {
  .st-key-workbench-layout > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] {flex-wrap:wrap}
  .st-key-workbench-layout > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {flex:1 1 100%;width:100%;min-width:0}
}
@media(max-width:959px) {
  .df-wizard-steps > .df-wizard-step {flex:1 1 0;min-width:0}
  .df-wizard-steps .df-wizard-step > span {min-width:0}
  .df-wizard-steps .df-wizard-step :is(strong,small) {white-space:normal;overflow-wrap:anywhere;line-height:1.45}
}
@media(max-width:559px) {
  .df-wizard-steps {flex-direction:column;align-items:stretch;gap:12px;padding:14px 16px}
  .df-wizard-steps > .df-wizard-step {flex:none;width:100%;gap:10px}
  .df-wizard-steps > i {display:none}
}
@media(max-width:700px) {.df-wb-plan-edges {grid-template-columns:1fr}}
</style>"""


def workbench_style(language: str = "zh") -> str:
    """Return styles shared by both languages; visible copy lives in widgets."""
    return WORKBENCH_STYLE
