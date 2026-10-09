"""Scoped visual language for local settings and generation preferences."""
from __future__ import annotations


SETTINGS_STYLE = """<style>
.df-settings-section {display:flex;align-items:center;justify-content:space-between;gap:16px;margin:7px 0 12px}
.df-settings-section > span {display:flex;align-items:center;gap:11px;min-width:0}
.df-settings-section i {display:grid;place-items:center;flex:0 0 auto;width:35px;height:35px;border-radius:9px;
  border:1px solid #cddff6;background:linear-gradient(145deg,#fff,#dceafb);
  box-shadow:inset 0 1px 0 #fff,0 3px 5px #315c891a;color:#1769d5;font-size:17px;font-style:normal}
.df-settings-section strong {display:block;color:#1c2f4c;font-size:17px;line-height:1.4}
.df-settings-section small {display:block;margin-top:2px;color:#5c718d;font-size:14px;line-height:1.45}
.df-settings-section > b {flex:0 0 auto;padding:5px 9px;border-radius:6px;background:#e7f7ef;
  color:#16815b;font-size:12px}
.df-settings-overview {display:grid;grid-template-columns:minmax(230px,1.35fr) repeat(3,minmax(105px,.75fr));
  align-items:stretch;gap:0;margin-bottom:19px;overflow:hidden;border:1px solid #d6e2f0;border-radius:16px;
  background:var(--df-surface,linear-gradient(150deg,#fff,#f8fbff));
  box-shadow:var(--df-shadow-panel,0 2px 3px #27466e06,0 12px 32px -14px #27466e30,inset 0 1px 0 #fff)}
.df-settings-overview-main {padding:16px 18px;background:linear-gradient(125deg,#fbfdff,#e9f2fe);box-shadow:inset 0 1px 0 #fff}
.df-settings-overview-main strong {display:block;color:#1c3d69;font-size:15px}
.df-settings-overview-main small {display:block;margin:5px 0 11px;color:#5d7390;font-size:12px}
.df-settings-progress {height:6px;overflow:hidden;border-radius:99px;background:#dce8f7;box-shadow:inset 0 1px 2px #315c891a}
.df-settings-progress i {display:block;height:100%;border-radius:99px;
  background:linear-gradient(90deg,#22afd1,#2671e3);box-shadow:inset 0 1px 0 #ffffff66}
.df-settings-stat {display:grid;align-content:center;gap:4px;padding:13px 17px;border-left:1px solid #e9eff7}
.df-settings-stat span {color:#617591;font-size:12px}
.df-settings-stat strong {color:#223c60;font-size:22px;line-height:1.1}
.df-settings-stat[data-kind="approved"] strong {color:#0b946b}
.df-settings-stat[data-kind="attention"] strong {color:#ca8630}
[class*="st-key-settings-gate-"] {border-color:#d6e2f0!important;
  background:var(--df-surface,linear-gradient(150deg,#fff,#f8fbff));
  box-shadow:var(--df-shadow-panel,0 2px 3px #27466e06,0 12px 32px -14px #27466e30,inset 0 1px 0 #fff)!important}
[class*="st-key-settings-gate-"]:has(.df-settings-gate-head[data-status="awaiting"]) {border-top:3px solid #db9d45!important}
[class*="st-key-settings-gate-"]:has(.df-settings-gate-head[data-status="approved"]) {border-top:3px solid #1bad83!important}
.df-settings-gate-head {display:flex;align-items:center;gap:10px;padding:0 0 13px;
  border-bottom:1px solid #eaf0f7}
.df-settings-gate-head > b {display:grid;place-items:center;flex:0 0 auto;width:37px;height:37px;
  border:1px solid #cddff6;border-radius:9px;background:linear-gradient(145deg,#fff,#dceafb);
  box-shadow:inset 0 1px 0 #fff,0 3px 5px #315c891a;color:#1769d5;font-size:13px}
.df-settings-gate-head > div {min-width:0;flex:1}
.df-settings-gate-head strong {display:block;color:#1c304d;font-size:15px;line-height:1.35}
.df-settings-gate-head small {display:block;margin-top:3px;color:#607590;font-size:13px;line-height:1.45}
.df-settings-gate-head > span {flex:0 0 auto;padding:5px 8px;border-radius:6px;
  background:#edf4ff;color:#236bbf;font-size:12px;font-weight:700;white-space:nowrap}
.df-settings-gate-head > span[data-status="approved"] {background:#e5f7ee;color:#0e865e}
.df-settings-gate-head > span[data-status="awaiting"] {background:#fff1de;color:#a96b19}
.df-settings-gate-head > span[data-status="rejected"] {background:#fcebee;color:#ae384b}
.df-settings-gate-prompt {margin:13px 0 11px;color:#4d637e;font-size:14px;line-height:1.65}
.df-settings-gate-time {display:flex;align-items:center;gap:7px;margin-top:8px;color:#71869e;font-size:13px}
.df-settings-gate-time b {color:#0d956c;font-size:14px}
.df-settings-footnote {margin:15px 0 3px;padding:12px 14px;border-left:3px solid #4a93e7;
  border-radius:0 8px 8px 0;background:linear-gradient(110deg,#eaf3ff,#f7fbff);
  box-shadow:inset 0 1px 0 #fff;color:#59718e;font-size:12px;line-height:1.55}
.df-settings-pref-overview {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:8px 0 14px}
.df-settings-pref-overview > div {display:grid;align-content:start;min-width:0;gap:5px;padding:14px 16px;border:1px solid #d7e4f4;
  border-radius:12px;background:linear-gradient(145deg,#fff,#ebf3fe);
  box-shadow:inset 0 1px 0 #fff,0 2px 3px #315c890e,0 8px 16px -12px #315c8933;overflow-wrap:anywhere}
.df-settings-pref-overview span {color:#607591;font-size:12px}
.df-settings-pref-overview strong {color:#1c3a61;font-size:22px;line-height:1.1}
.df-settings-pref-overview small {color:#64758b;font-size:12px;line-height:1.4}
.df-settings-pref-overview > div:nth-child(3) strong {color:#109266;font-size:18px}
.df-settings-pref-overview > div:nth-child(4) strong {color:#7354bf;font-size:18px}
.df-settings-distribution {display:flex;align-items:center;gap:15px;margin:0 0 19px;padding:13px 16px;
  border:1px solid #d7e4f4;border-radius:11px;background:linear-gradient(145deg,#fff,#f0f6ff);
  box-shadow:inset 0 1px 0 #fff,0 2px 4px #315c8910}
.df-settings-distribution > strong {flex:0 0 auto;color:#244265;font-size:13px}
.df-settings-distribution-body {display:grid;gap:7px;min-width:0;flex:1}
.df-settings-distribution-bar {display:flex;width:100%;height:9px;overflow:hidden;border-radius:99px;background:#e4edf8;box-shadow:inset 0 1px 2px #315c891a}
.df-settings-distribution-bar i {height:100%;box-shadow:inset 0 1px 0 #ffffff66}
.df-settings-distribution-legend {display:flex;flex-wrap:wrap;gap:4px 13px}
.df-settings-distribution-legend span {display:inline-flex;align-items:center;gap:5px;color:#607591;font-size:12px}
.df-settings-distribution-legend i {width:7px;height:7px;border-radius:50%}
[class*="st-key-settings-pref-"] {border-color:#d6e2f0!important;
  background:var(--df-surface,linear-gradient(150deg,#fff,#f8fbff));
  box-shadow:var(--df-shadow-panel,0 2px 3px #27466e06,0 12px 32px -14px #27466e30,inset 0 1px 0 #fff)!important}
[class*="st-key-settings-pref-"] .df-section-heading strong {font-size:15px}
[class*="st-key-settings-pref-"] .df-section-heading small {font-size:12px}
.df-pref-style {padding:16px;row-gap:2px}
.df-pref-style-icon {width:34px;height:34px;font-size:16px}
.df-pref-style strong,.df-pref-style b {font-size:14px}
.df-pref-style p {font-size:13px;line-height:1.6}
.df-pref-rule-list > div {gap:12px;padding:14px 15px}
.df-pref-rule-list b {font-size:13px}
.df-pref-rule-list span {font-size:13px;line-height:1.65}
.st-key-settings-workflow-defaults {border-color:#d6e2f0!important;border-radius:16px!important;
  background:var(--df-surface,linear-gradient(150deg,#fff,#f8fbff));
  box-shadow:var(--df-shadow-panel,0 2px 3px #27466e06,0 12px 32px -14px #27466e30,inset 0 1px 0 #fff)}
.st-key-settings-workflow-defaults > [data-testid="stVerticalBlock"] {gap:14px}
.st-key-settings-default-format {max-width:880px}
.df-settings-format-note {display:flex;align-items:center;flex-wrap:wrap;gap:9px 13px;margin:0;
  color:#536b86;font-size:13px;line-height:1.6}
.df-settings-format-note > span {display:inline-flex;align-items:center;gap:6px;flex:0 0 auto;
  padding:4px 9px;border-radius:6px;background:#edf6f2;color:#26785c;font-size:12px;font-weight:600}
.df-settings-format-note > span[data-state="changed"] {background:#fff4e3;color:#996414}
.df-settings-format-note i {font-style:normal;font-size:14px;line-height:1}
.df-settings-format-note p {margin:0;min-width:0;overflow-wrap:anywhere}
[data-testid="stVerticalBlockBorderWrapper"]:has(.st-key-ui-language-choice) {border-color:#d6e2f0;border-radius:16px;
  background:radial-gradient(ellipse at 100% 0,#e0edff99,transparent 55%),linear-gradient(150deg,#fff,#f5f9ff);
  box-shadow:var(--df-shadow-panel,0 2px 3px #27466e06,0 12px 32px -14px #27466e30,inset 0 1px 0 #fff)}
@media(max-width:1050px) {
  .df-settings-overview {grid-template-columns:repeat(3,minmax(0,1fr))}
  .df-settings-overview-main {grid-column:1/-1}
  .df-settings-stat:first-of-type {border-left:0}
}
@media(max-width:760px) {
  .st-key-settings-default-format [data-testid="stHorizontalBlock"] {flex-direction:column;align-items:stretch;gap:12px}
  .st-key-settings-default-format [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    width:100%!important;min-width:0!important;max-width:100%!important;flex:1 1 auto!important}
  .st-key-settings-default-format button {min-height:44px}
  .st-key-settings-default-format [data-baseweb="select"] > div {min-height:44px}
  .df-settings-pref-overview {grid-template-columns:repeat(2,minmax(0,1fr))}
  .df-settings-distribution {align-items:flex-start;flex-direction:column}
  .df-settings-distribution-body {width:100%}
  .df-settings-section {align-items:flex-start;flex-direction:column}
}
</style>"""
