"""Workbench-only styles for source, target and generation configuration."""
from __future__ import annotations


WORKBENCH_STYLE = """<style>
.df-wb-preset-help {margin:-4px 0 12px;color:#7a8da5;font-size:11px}
[class*="st-key-workbench-target-card-"] {margin:8px 0 14px}
[class*="st-key-workbench-target-card-"] button {position:relative;display:block;min-height:104px;
  padding:13px 11px 38px 55px;border:1px solid #dee8f5;border-radius:11px;
  background:linear-gradient(140deg,#fff,#f9fbff);text-align:left;
  box-shadow:0 2px 8px rgba(33,91,161,.03);transition:border-color .16s,box-shadow .16s,transform .16s}
[class*="st-key-workbench-target-card-"] button:hover {border-color:#8cb8ed;
  box-shadow:0 5px 14px rgba(33,108,206,.10);transform:translateY(-1px)}
[class*="st-key-workbench-target-card-"] button p {margin:0;color:#1c304b;font-size:13px;
  line-height:1.4;font-weight:750;white-space:normal;text-align:left}
[class*="st-key-workbench-target-card-"] button::before {position:absolute;top:13px;left:11px;
  display:grid;place-items:center;width:35px;height:35px;border-radius:9px;
  background:#e8f2ff;color:#1769d5;font-size:10px;font-weight:800}
[class*="st-key-workbench-target-card-"] button::after {position:absolute;left:55px;right:11px;bottom:12px;
  color:#74869e;font-size:11px;line-height:1.35;white-space:normal;text-align:left}
[class*="st-key-workbench-target-card-"][class*="-on"] button {border-color:#8cb8ed;
  background:linear-gradient(140deg,#f1f7ff,#fff);box-shadow:0 3px 12px rgba(33,108,206,.075)}
[class*="st-key-workbench-target-card-cpt-"] button::before {content:"CPT"}
[class*="st-key-workbench-target-card-cpt-"] button::after {content:"文档清洗、分块与去重"}
[class*="st-key-workbench-target-card-dialogue-"] button::before {content:"对话";
  background:#e6f7ef;color:#128362}
[class*="st-key-workbench-target-card-dialogue-"] button::after {content:"SFT、多轮与 Agent 轨迹"}
[class*="st-key-workbench-target-card-preference-"] button::before {content:"偏好";
  background:#f0ecfb;color:#7852bd}
[class*="st-key-workbench-target-card-preference-"] button::after {content:"ORPO、DPO 与 RLAIF"}
[class*="st-key-workbench-target-card-reasoning-"] button::before {content:"推理";
  background:#fff2e6;color:#b8651d}
[class*="st-key-workbench-target-card-reasoning-"] button::after {content:"CoT 与算术核验"}
.df-wb-selected {display:flex;align-items:flex-start;gap:10px;margin:13px 0 0;padding:10px 12px;
  border:1px solid #dceaf9;border-radius:8px;background:#f5f9ff}
.df-wb-selected > b {flex:0 0 auto;color:#1e62ad;font-size:11px}
.df-wb-selected > div {display:flex;flex-wrap:wrap;gap:5px}
.df-wb-selected span {padding:3px 7px;border-radius:5px;background:#fff;border:1px solid #dce8f5;
  color:#4a6687;font-size:11px}
.df-wb-selected small {color:#697e98;font-size:11px}
.df-wb-plan {margin:10px 0 2px;padding:11px 12px;border:1px solid #d9e7f7;border-radius:9px;
  background:linear-gradient(120deg,#f7fbff,#fff)}
.df-wb-plan-head {display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}
.df-wb-plan-head strong,.df-wb-plan-empty strong {color:#213b5f;font-size:12px}
.df-wb-plan-head small {color:#5e7899;font-size:11px;white-space:nowrap}
.df-wb-plan-nodes {display:flex;flex-wrap:wrap;gap:5px}
.df-wb-plan-node {display:inline-flex;align-items:center;gap:5px;min-height:28px;padding:3px 8px;
  border:1px solid #d8e5f5;border-radius:6px;background:#fff;color:#365579;font-size:11px}
.df-wb-plan-node i {font-style:normal;color:#407ccb;font-size:13px}
.df-wb-plan-node b {font-weight:650}
.df-wb-plan-node[data-stage="ingest"] {border-color:#cae6ea;background:#f2fcfc;color:#22737c}
.df-wb-plan-node[data-stage="package"] {border-color:#cce8dc;background:#f2fbf5;color:#227c55}
.df-wb-plan-node[data-intermediate="true"] {border-color:#ded4f3;background:#f9f6ff;color:#7054a8}
.df-wb-plan-note {margin-top:7px;color:#70839b;font-size:10px;line-height:1.5}
.df-wb-plan-note span {display:block;color:#6c618b}
.df-wb-plan-empty {display:flex;gap:8px;align-items:center;color:#72849b;font-size:11px}
.df-wb-plan-detail {padding:2px 0 5px}
.df-wb-plan-detail p {margin:0 0 9px;color:#607a99;font-size:11px;line-height:1.5}
.df-wb-plan-edges {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}
.df-wb-plan-edge {display:flex;align-items:center;gap:6px;min-width:0;padding:7px 8px;
  border:1px solid #e0eaf5;border-radius:7px;background:#f9fbff;color:#365579;font-size:11px}
.df-wb-plan-edge span {overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.df-wb-plan-edge i {flex:0 0 auto;color:#2d7ad0;font-size:14px;font-style:normal}
[class*="st-key-workbench-targets"] {margin:0 0 14px;border-color:#d9e6f5!important;
  box-shadow:0 4px 14px rgba(30,77,144,.045)!important}
[class*="st-key-workbench-source-panel"], [class*="st-key-workbench-parameters-panel"] {
  min-height:380px;border-color:#d9e6f5!important;box-shadow:0 4px 14px rgba(30,77,144,.04)!important}
[class*="st-key-workbench-source-panel"] .df-section-heading,
[class*="st-key-workbench-parameters-panel"] .df-section-heading {margin-bottom:13px}
[class*="st-key-workbench-source-panel"] [data-testid="stFileUploader"] section {
  min-height:82px;background:#f8fbff;border-color:#b8d0ed}
[class*="st-key-workbench-submit"] {margin:12px 0 0;border-color:#d9e6f5!important;
  background:linear-gradient(105deg,#f7fbff,#fff)!important}
.df-wb-submit-summary {display:flex;align-items:center;flex-wrap:wrap;gap:8px 13px}
.df-wb-submit-summary b {display:grid;place-items:center;padding:5px 8px;border-radius:6px;
  background:#e6f0ff;color:#1b67c4;font-size:11px}
.df-wb-submit-summary strong {color:#1f3653;font-size:14px}
.df-wb-submit-summary span {color:#697f9b;font-size:11px}
@media(max-width:980px) {[class*="st-key-workbench-target-card-"] button {min-height:114px;padding-left:13px;padding-top:55px}
  [class*="st-key-workbench-target-card-"] button::after {left:13px}}
@media(max-width:700px) {.df-wb-plan-edges {grid-template-columns:1fr}}
</style>"""
