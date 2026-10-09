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
.st-key-workbench-canvas-panel {position:relative;background:linear-gradient(145deg,#fff,#fbfdff);
  border-color:#d9e5f2;border-radius:16px;box-shadow:0 5px 20px #24436307}
.st-key-workbench-canvas-panel .df-panel-icon {background:linear-gradient(145deg,#edf5ff,#dbeaff);box-shadow:0 3px 9px #bdd5f433}
.st-key-workbench-canvas-panel .df-section-heading {padding-bottom:14px;margin-bottom:2px;border-bottom:0}
.st-key-workbench-targets {background:linear-gradient(110deg,#fff,#f7faff);border-color:#d7e5f5}
.st-key-workbench-node-panel {border-top:2px solid #78acf1;background:linear-gradient(160deg,#f9fcff,#fff 45%)}
[data-workflow-inspector-host="floating"] {height:0!important;min-height:0!important;margin:0!important;padding:0!important;overflow:visible!important}
.st-key-workbench-node-panel[data-workflow-inspector="floating"] {
  border:1px solid #cbdcf0;border-top:2px solid #2c7de2;border-radius:16px;
  padding:0 18px 18px;background:#fff;box-shadow:0 20px 60px #152e501f,0 5px 15px #264e7010;
  overscroll-behavior:contain;scrollbar-width:thin;scrollbar-color:#b5c9e2 transparent;gap:14px}
.st-key-workbench-node-panel[data-workflow-inspector="floating"] > [data-testid="stLayoutWrapper"]:has(> .st-key-workbench-node-header) {
  position:sticky;top:0;z-index:2;background:#fff;
}
.st-key-workbench-node-panel[data-workflow-inspector="floating"] .st-key-workbench-node-header {
  padding:16px 0 13px;border-bottom:1px solid #e3ebf5}
.st-key-workbench-node-header .df-section-heading {margin-bottom:0;padding-bottom:0;border-bottom:0}
.st-key-workbench-node-header .df-section-heading strong {font-size:17px;letter-spacing:-.25px}
.st-key-workbench-node-header .df-section-heading small {font-size:12px;color:#60748d}
.st-key-workbench-node-header .df-panel-icon {width:34px;height:34px;border-radius:10px}
.st-key-workbench-node-header button {min-height:36px;padding:4px 10px;color:#47627f;border-color:#dce6f2;background:#f9fbfe}
.st-key-workbench-node-header button:hover {background:#eef5ff;border-color:#a8c8ee;color:#185fad}
.st-key-workbench-node-panel [data-testid="stTabs"] :is([data-baseweb="tab-list"],[role="tablist"]) {gap:24px}
.st-key-workbench-node-panel [data-testid="stTabs"] :is([data-baseweb="tab"],[role="tab"]) {font-size:14px;height:42px;font-weight:550}
.st-key-workbench-node-panel [data-testid="stTabs"] :is([data-baseweb="tab-panel"],[role="tabpanel"]) {padding-top:16px}
.st-key-workbench-node-panel [data-testid="stMarkdownContainer"] p {font-size:14px;line-height:1.65;color:#334b67}
.st-key-workbench-node-panel [data-testid="stCaptionContainer"] p {font-size:12px;line-height:1.6;color:#64768d;opacity:1}
.st-key-workbench-node-panel :is(input,textarea) {font-size:14px!important;line-height:1.55}
.st-key-workbench-node-panel [data-testid="stWidgetLabel"] p {font-size:13px;font-weight:500;color:#334b67}
.st-key-workbench-node-panel [data-testid="stExpander"] summary {font-size:13px}
.st-key-workbench-node-panel :is(button,input,textarea,[role="tab"]):focus-visible {outline:3px solid #83b7f066;outline-offset:2px}
.st-key-workbench-node-panel [class*="st-key-workbench-node-prompts"] {padding-top:4px;border-bottom:0}
.st-key-workbench-source-preview {border-top:3px solid #83c9bf}
.df-knowledge-route {position:relative;overflow:hidden;padding:18px 12px 14px;border:1px solid #dbe9f6;
  border-radius:14px;background:radial-gradient(circle at 85% 10%,#e0efff,transparent 45%),#f8fbff;text-align:center}
.df-knowledge-route > i {display:grid;place-items:center;margin:0 auto 10px;width:52px;height:52px;border-radius:15px;
  background:linear-gradient(145deg,#78b4fa,#246ed6);color:white;font-size:34px;font-style:normal;box-shadow:0 7px 16px #367fe62b}
.df-knowledge-route > strong {display:block;color:#25496e;font-size:15px}
.df-knowledge-route p {color:#526a85;font-size:13px;line-height:1.6;margin:7px 0 14px}
.df-knowledge-route > div {display:flex;align-items:center;justify-content:center;gap:6px;flex-wrap:wrap}
.df-knowledge-route span {padding:6px 9px;border:1px solid #d9e7f6;border-radius:8px;background:#fff;color:#41658b;font-size:12px}
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
  border:1px solid #e0eaf5;border-radius:9px;background:#f9fbff;color:#365579;font-size:12px}
.df-wb-plan-edge span {min-width:0;overflow-wrap:anywhere;line-height:1.45}
.df-wb-plan-edge i {flex:0 0 auto;color:#2d7ad0;font-size:14px;font-style:normal}
[class*="st-key-workbench-source-panel"] [data-testid="stFileUploader"] section {
  background:#f8fbff;border-color:#b8d0ed}
[class*="st-key-workbench-submit"] {border-color:#d9e6f5;
  background:linear-gradient(105deg,#f7fbff,#fff)}
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
.df-generation-preview {padding:13px 14px;border:1px solid #d5e4f5;border-radius:11px;
  background:linear-gradient(120deg,#edf5ff,#f8fbff);overflow:hidden}
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
