"""Workbench-only styles for source, target and generation configuration."""
from __future__ import annotations


WORKBENCH_STYLE = """<style>
.st-key-workbench-layout [data-testid="stColumn"] {min-width:0}
.st-key-workbench-canvas-panel {position:relative;background:radial-gradient(ellipse at 90% 0%,#e9f3ff,transparent 55%),#fff}
.st-key-workbench-canvas-panel .df-panel-icon {background:linear-gradient(145deg,#edf5ff,#dbeaff);box-shadow:0 3px 9px #bdd5f44d}
.st-key-workbench-targets {background:linear-gradient(110deg,#fff,#f7faff);border-color:#d7e5f5}
.st-key-workbench-node-panel {border-top:3px solid #78acf1;background:linear-gradient(160deg,#f9fcff,#fff 45%)}
.st-key-workbench-source-preview {border-top:3px solid #83c9bf}
.df-knowledge-route {position:relative;overflow:hidden;padding:18px 12px 14px;border:1px solid #dbe9f6;
  border-radius:12px;background:radial-gradient(circle at 85% 10%,#e0efff,transparent 45%),#f8fbff;text-align:center}
.df-knowledge-route > i {display:grid;place-items:center;margin:0 auto 10px;width:52px;height:52px;border-radius:15px;
  background:linear-gradient(145deg,#78b4fa,#246ed6);color:white;font-size:34px;font-style:normal;box-shadow:0 7px 16px #367fe62b}
.df-knowledge-route > strong {display:block;color:#25496e;font-size:14px}
.df-knowledge-route p {color:#6a819a;font-size:12px;margin:7px 0 14px}
.df-knowledge-route > div {display:flex;align-items:center;justify-content:center;gap:6px;flex-wrap:wrap}
.df-knowledge-route span {padding:5px 8px;border:1px solid #d9e7f6;border-radius:7px;background:#fff;color:#41658b;font-size:11px}
.df-knowledge-route b {color:#7e9fc5;font-weight:400}
.df-wb-plan {display:flex;align-items:center;flex-wrap:wrap;gap:5px 12px;
  margin:0;color:#365579;font-size:12px;line-height:1.5}
.df-wb-plan strong {color:#213b5f;font-size:12px}
.df-wb-plan small {color:#5e7899;font-size:11px}
.df-wb-plan-intermediate {color:#7054a8;font-size:11px}
.df-wb-plan-empty {color:#72849b;font-size:11px}
.df-wb-plan-detail {padding:2px 0 5px}
.df-wb-plan-detail p {margin:0 0 9px;color:#607a99;font-size:11px;line-height:1.5}
.df-wb-plan-edges {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}
.df-wb-plan-edge {display:flex;align-items:center;gap:6px;min-width:0;padding:7px 8px;
  border:1px solid #e0eaf5;border-radius:7px;background:#f9fbff;color:#365579;font-size:11px}
.df-wb-plan-edge span {min-width:0;overflow-wrap:anywhere;line-height:1.45}
.df-wb-plan-edge i {flex:0 0 auto;color:#2d7ad0;font-size:14px;font-style:normal}
[class*="st-key-workbench-source-panel"] [data-testid="stFileUploader"] section {
  background:#f8fbff;border-color:#b8d0ed}
[class*="st-key-workbench-submit"] {border-color:#d9e6f5;
  background:linear-gradient(105deg,#f7fbff,#fff)}
[class*="st-key-workflow-create"] button[kind="primary"]:disabled,
[class*="st-key-workflow-create"] button[kind="primary"]:disabled:hover {
  background:#e8eef6;border-color:#c9d6e5;color:#53657c;box-shadow:none;cursor:not-allowed;opacity:1}
.df-wb-submit-summary {display:flex;align-items:center;flex-wrap:wrap;gap:8px 13px}
.df-wb-submit-summary b {display:grid;place-items:center;padding:5px 8px;border-radius:6px;
  background:#e6f0ff;color:#1b67c4;font-size:11px}
.df-wb-submit-summary strong {color:#1f3653;font-size:14px;overflow-wrap:anywhere}
.df-wb-submit-summary span {color:#697f9b;font-size:11px;overflow-wrap:anywhere}
[class*="st-key-workbench-generation"] {padding:0 0 12px;border-bottom:1px solid #e0eaf5;margin-bottom:2px}
[class*="st-key-workbench-generation"] [data-testid="stToggle"] {margin-bottom:3px}
.df-generation-preview {padding:11px 12px;border:1px solid #d5e4f5;border-radius:9px;
  background:linear-gradient(120deg,#edf5ff,#f8fbff);overflow:hidden}
.df-generation-preview > div {display:flex;align-items:center;gap:7px;flex-wrap:wrap;line-height:1.45}
.df-generation-preview i {display:grid;place-items:center;flex:0 0 24px;height:24px;border-radius:7px;
  background:#dceaff;color:#3575c9;font-size:17px;font-style:normal}
.df-generation-preview strong {color:#254c7b;font-size:12px;font-weight:650}
.df-generation-preview span {margin-left:auto;color:#4b73a2;font-size:11px;padding:2px 7px;
  background:#fff;border:1px solid #d8e6f7;border-radius:5px}
.df-generation-preview p {margin:7px 0 0;color:#536e90;font-size:11px;line-height:1.65;overflow-wrap:anywhere;white-space:pre-wrap}
.df-generation-preview.is-disabled {background:#f7f9fc;border-color:#e2e9f2}
.df-generation-preview.is-disabled i {background:#e8edf5;color:#7890ab}
.df-generation-preview.is-disabled strong,.df-generation-preview.is-disabled p {color:#71849b}
[class*="st-key-workbench-node-prompts"] {padding:12px 0;border-bottom:1px solid #e0eaf5}
[class*="st-key-workbench-node-prompts"] [data-testid="stTextArea"] textarea {
  background:#f8fbff;border-color:#cfdff2;font-size:12px;line-height:1.65}
[class*="st-key-workbench-node-prompts"] [data-testid="stFileUploader"] section {
  background:#f8fbff;border-color:#cfdff2}
.df-node-prompt-heading {display:flex;align-items:center;gap:7px;color:#294d78;
  font-size:13px;font-weight:650;line-height:1.5;margin-bottom:3px}
.df-node-prompt-heading i {display:grid;place-items:center;width:24px;height:24px;
  border-radius:7px;background:#e6efff;color:#3575c9;font-style:normal}
@media(max-width:1050px) {
  .st-key-workbench-layout > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] {flex-wrap:wrap}
  .st-key-workbench-layout > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {flex:1 1 100%;width:100%;min-width:0}
}
@media(max-width:700px) {.df-wb-plan-edges {grid-template-columns:1fr}}
</style>"""


def workbench_style(language: str = "zh") -> str:
    """Return styles shared by both languages; visible copy lives in widgets."""
    return WORKBENCH_STYLE
