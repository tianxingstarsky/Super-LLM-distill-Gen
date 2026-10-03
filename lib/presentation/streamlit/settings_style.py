"""Scoped visual language for workspace gates and generation preferences."""
from __future__ import annotations


SETTINGS_STYLE = """<style>
.df-settings-section {display:flex;align-items:center;justify-content:space-between;gap:16px;margin:7px 0 12px}
.df-settings-section > span {display:flex;align-items:center;gap:11px;min-width:0}
.df-settings-section i {display:grid;place-items:center;flex:0 0 auto;width:35px;height:35px;border-radius:9px;
  background:#eaf3ff;color:#1769d5;font-size:17px;font-style:normal}
.df-settings-section strong {display:block;color:#1c2f4c;font-size:17px;line-height:1.4}
.df-settings-section small {display:block;margin-top:2px;color:#6e8199;font-size:13px;line-height:1.45}
.df-settings-section > b {flex:0 0 auto;padding:5px 9px;border-radius:6px;background:#e7f7ef;
  color:#16815b;font-size:11px}
.df-settings-overview {display:grid;grid-template-columns:minmax(230px,1.35fr) repeat(3,minmax(105px,.75fr));
  align-items:stretch;gap:0;margin-bottom:19px;overflow:hidden;border:1px solid #dce8f7;border-radius:13px;
  background:#fff;box-shadow:0 4px 18px rgba(36,83,145,.045)}
.df-settings-overview-main {padding:16px 18px;background:linear-gradient(105deg,#f3f8ff,#fbfdff)}
.df-settings-overview-main strong {display:block;color:#1c3d69;font-size:15px}
.df-settings-overview-main small {display:block;margin:5px 0 11px;color:#70829a;font-size:11px}
.df-settings-progress {height:6px;overflow:hidden;border-radius:99px;background:#dfebf8}
.df-settings-progress i {display:block;height:100%;border-radius:99px;
  background:linear-gradient(90deg,#22afd1,#2671e3)}
.df-settings-stat {display:grid;align-content:center;gap:4px;padding:13px 17px;border-left:1px solid #e9eff7}
.df-settings-stat span {color:#7889a1;font-size:11px}
.df-settings-stat strong {color:#223c60;font-size:22px;line-height:1.1}
.df-settings-stat[data-kind="approved"] strong {color:#0b946b}
.df-settings-stat[data-kind="attention"] strong {color:#ca8630}
[class*="st-key-settings-gate-"] {min-height:230px;border-color:#dce8f6!important;
  box-shadow:0 4px 14px rgba(36,83,145,.045)!important}
[class*="st-key-settings-gate-"]:has(.df-settings-gate-head[data-status="awaiting"]) {border-top:3px solid #db9d45!important}
[class*="st-key-settings-gate-"]:has(.df-settings-gate-head[data-status="approved"]) {border-top:3px solid #1bad83!important}
.df-settings-gate-head {display:flex;align-items:center;gap:10px;padding:0 0 13px;
  border-bottom:1px solid #eaf0f7}
.df-settings-gate-head > b {display:grid;place-items:center;flex:0 0 auto;width:37px;height:37px;
  border-radius:9px;background:#eaf3ff;color:#1769d5;font-size:12px}
.df-settings-gate-head > div {min-width:0;flex:1}
.df-settings-gate-head strong {display:block;color:#1c304d;font-size:15px;line-height:1.35}
.df-settings-gate-head small {display:block;margin-top:3px;color:#74869c;font-size:12px;line-height:1.45}
.df-settings-gate-head > span {flex:0 0 auto;padding:5px 8px;border-radius:6px;
  background:#edf4ff;color:#236bbf;font-size:11px;font-weight:700;white-space:nowrap}
.df-settings-gate-head > span[data-status="approved"] {background:#e5f7ee;color:#0e865e}
.df-settings-gate-head > span[data-status="awaiting"] {background:#fff1de;color:#a96b19}
.df-settings-gate-head > span[data-status="rejected"] {background:#fcebee;color:#ae384b}
.df-settings-gate-prompt {min-height:75px;margin:13px 0 11px;color:#4d637e;font-size:13px;line-height:1.65}
.df-settings-gate-prompt[data-status="approved"] {min-height:75px}
.df-settings-gate-time {display:flex;align-items:center;gap:7px;margin-top:8px;color:#71869e;font-size:12px}
.df-settings-gate-time b {color:#0d956c;font-size:14px}
.df-settings-footnote {margin:15px 0 3px;padding:12px 14px;border-left:3px solid #4a93e7;
  border-radius:0 8px 8px 0;background:#f4f9ff;color:#59718e;font-size:11px;line-height:1.55}
.df-settings-pref-overview {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:8px 0 14px}
.df-settings-pref-overview > div {display:grid;gap:5px;min-height:104px;padding:15px 16px;border:1px solid #dce7f6;
  border-radius:11px;background:linear-gradient(140deg,#fff,#f7fbff);box-shadow:0 3px 12px rgba(30,79,145,.035)}
.df-settings-pref-overview span {color:#71849d;font-size:11px}
.df-settings-pref-overview strong {color:#1c3a61;font-size:22px;line-height:1.1}
.df-settings-pref-overview small {color:#8797a9;font-size:11px;line-height:1.4}
.df-settings-pref-overview > div:nth-child(3) strong {color:#109266;font-size:18px}
.df-settings-pref-overview > div:nth-child(4) strong {color:#7354bf;font-size:18px}
.df-settings-distribution {display:flex;align-items:center;gap:15px;margin:0 0 19px;padding:13px 16px;
  border:1px solid #dfeaf7;border-radius:11px;background:#fff}
.df-settings-distribution > strong {flex:0 0 auto;color:#244265;font-size:12px}
.df-settings-distribution-body {display:grid;gap:7px;min-width:0;flex:1}
.df-settings-distribution-bar {display:flex;width:100%;height:9px;overflow:hidden;border-radius:99px;background:#eaf0f6}
.df-settings-distribution-bar i {height:100%}
.df-settings-distribution-legend {display:flex;flex-wrap:wrap;gap:4px 13px}
.df-settings-distribution-legend span {display:inline-flex;align-items:center;gap:5px;color:#71849e;font-size:10px}
.df-settings-distribution-legend i {width:7px;height:7px;border-radius:50%}
[class*="st-key-settings-pref-"] {border-color:#dce8f6!important;box-shadow:0 4px 14px rgba(36,83,145,.04)!important}
[class*="st-key-settings-pref-"] .df-section-heading strong {font-size:15px}
[class*="st-key-settings-pref-"] .df-section-heading small {font-size:11px}
.df-pref-style {padding:16px;row-gap:2px}
.df-pref-style-icon {width:34px;height:34px;font-size:16px}
.df-pref-style strong,.df-pref-style b {font-size:13px}
.df-pref-style p {font-size:12px;line-height:1.6}
.df-pref-rule-list > div {gap:12px;padding:14px 15px}
.df-pref-rule-list b {font-size:12px}
.df-pref-rule-list span {font-size:12px;line-height:1.65}
@media(max-width:1050px) {
  .df-settings-overview {grid-template-columns:repeat(3,minmax(0,1fr))}
  .df-settings-overview-main {grid-column:1/-1}
  .df-settings-stat:first-of-type {border-left:0}
}
@media(max-width:760px) {
  .df-settings-pref-overview {grid-template-columns:repeat(2,minmax(0,1fr))}
  .df-settings-distribution {align-items:flex-start;flex-direction:column}
  .df-settings-distribution-body {width:100%}
  .df-settings-section {align-items:flex-start;flex-direction:column}
}
</style>"""
