"""Visual language for the persisted-workflow task center."""
from __future__ import annotations


def task_management_styles() -> str:
    return """
<style>
.df-task-summary {display:flex;align-items:center;flex-wrap:wrap;gap:8px 0;
  margin:0 0 10px;padding:12px 16px;border:1px solid #dce6f1;border-radius:11px;
  background:linear-gradient(110deg,#fff,#f7faff)}
.df-task-summary-item {display:inline-flex;align-items:center;gap:8px;padding:0 22px;
  border-left:1px solid #e1e8f1;line-height:1.4}
.df-task-summary-item:first-child {padding-left:0;border-left:0}
.df-task-summary-item i {display:grid;place-items:center;flex:0 0 29px;width:29px;height:29px;
  border-radius:8px;background:#eaf2ff;color:#2475cf;font-size:15px;font-style:normal}
.df-task-summary-item[data-kind="completed"] i {background:#e6f6ef;color:#148365}
.df-task-summary-item[data-kind="attention"] i {background:#fff1e6;color:#aa6732}
.df-task-summary-item strong {color:#23425f;font-size:17px;font-weight:700}
.df-task-summary-item span {color:#72859b;font-size:12px}
.df-task-summary-item[data-kind="completed"] strong {color:#087d5c}
.df-task-summary-item[data-kind="attention"] strong {color:#b86131}
.df-task-summary-item[data-kind="processing"] strong {color:#1768d5}
.df-task-list-head {display:flex;align-items:center;justify-content:space-between;gap:8px;margin:3px 0 11px}
.df-task-list-head strong {color:#1c304d;font-size:16px;letter-spacing:.01em}
.df-task-list-head small {color:#8291a7;font-size:12px;text-align:right}
[class*="st-key-task-center-filter"] {max-width:100%}
[class*="st-key-task-center-filter"] [role="radiogroup"] {max-width:100%;box-sizing:border-box}
[class*="st-key-task-center-filter"] [role="radio"] {min-width:0;padding:4px 7px;font-size:11px}
[class*="st-key-task_card_"] {padding:8px 10px;margin:0;border:0;border-bottom:1px solid #e1e9f3;
  border-left:3px solid transparent;border-radius:0;background:transparent;box-shadow:none;
  transition:background .15s ease}
[class*="st-key-task_card_"]:hover {background:#f5f8fc}
[class*="st-key-task_card_selected_"] {border-left-color:#3d8ce8;background:#edf5ff;box-shadow:none}
[class*="st-key-task_card_completed_"] {border-left:3px solid #15a97e}
[class*="st-key-task_card_running_"] {border-left:3px solid #277fe4}
[class*="st-key-task_card_failed_"], [class*="st-key-task_card_needs_attention_"] {border-left:3px solid #e4864b}
.df-task-card-top,.df-task-card-progress {display:flex;align-items:center;justify-content:space-between;gap:7px}
.df-task-card-top {flex-wrap:wrap}
.df-task-card-top time,.df-task-card-progress {color:#8392a7;font-size:10px}
.df-task-status {display:inline-flex;align-items:center;gap:5px;padding:4px 8px;border-radius:6px;
  background:#eaf3ff;color:#256ab9;font-size:11px;font-weight:700;line-height:1;white-space:nowrap;flex-shrink:0}
.df-task-status i {font-size:12px;font-style:normal}
.df-task-status[data-status="completed"] {background:#e2f7ee;color:#087d5c}
.df-task-status[data-status="failed"],.df-task-status[data-status="needs_attention"] {background:#fff0e7;color:#b86131}
.df-task-status[data-status="cancelled"] {background:#f0f2f6;color:#627184}
[class*="st-key-task_card_"] .stButton button {display:block;width:100%;min-height:29px;padding:4px 0 2px;
  border:0;border-radius:0;background:transparent;text-align:left;justify-content:flex-start;
  color:#20334f;box-shadow:none}
[class*="st-key-task_card_"] .stButton button > div,
[class*="st-key-task_card_"] .stButton button > div > span {justify-content:flex-start;width:100%}
[class*="st-key-task_card_"] .stButton button:hover,
[class*="st-key-task_card_"] .stButton button:focus {border:0;background:transparent;color:#145dad;box-shadow:none}
[class*="st-key-task_card_"] .stButton button p {font-size:13px;line-height:1.4;font-weight:650}
.df-task-card-targets {display:flex;flex-wrap:wrap;gap:4px;margin:0 0 4px}
.df-task-card-targets span {display:inline-block;padding:2px 5px;border-radius:4px;
  background:#eff5ff;color:#5b7191;font-size:10px;line-height:1.3}
.df-task-card-source {display:flex;align-items:baseline;gap:6px;min-width:0;margin:0 0 4px;
  color:#71839b;font-size:11px}
.df-task-card-source b {flex:0 0 auto;color:#4c6c93;font-size:10px;font-weight:600}
.df-task-card-source span {min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.df-task-card-progress b {color:#315a8e;font-size:11px}
.df-task-meter {height:3px;margin-top:4px;border-radius:2px;background:#e8eff8;overflow:hidden}
.df-task-meter i {display:block;height:100%;border-radius:100px;background:linear-gradient(90deg,#26a7db,#2469dd)}
[class*="st-key-task_card_completed_"] .df-task-meter i {background:linear-gradient(90deg,#34c9a0,#139967)}
.df-task-activity {display:grid;grid-template-columns:90px minmax(0,1fr);align-items:start;gap:15px;
  padding:13px 15px;margin:1px 0 15px;border:1px solid #dfe9f7;border-radius:12px;
  background:linear-gradient(105deg,#f4f9ff,#fff 65%)}
.df-task-activity-head strong {display:block;font-size:12px;color:#1f4d82}
.df-task-activity-head small {display:block;margin-top:3px;color:#8497ac;font-size:10px}
.df-task-activity-list {display:grid;gap:6px;min-width:0}
.df-task-activity-event {display:grid;grid-template-columns:8px minmax(0,1fr) auto;
  align-items:center;gap:8px;min-width:0;font-size:11px;color:#4e6280}
.df-task-activity-event > i {width:7px;height:7px;border-radius:50%;background:#3487e5}
.df-task-activity-event > i[data-kind="stage_completed"],.df-task-activity-event > i[data-kind="run_finished"] {background:#17a87e}
.df-task-activity-event > i[data-kind="run_failed"] {background:#de7955}
.df-task-activity-event span {min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.df-task-activity-event b {color:#2a527e;font-weight:700}
.df-task-activity-event time {flex:0 0 auto;color:#96a4b7;font-size:10px}
.df-task-activity-empty {align-items:center}
.df-task-activity-empty span {font-size:12px;color:#788ca5}
.df-task-no-match {padding:22px;border:1px dashed #d5e2f2;border-radius:10px;color:#7889a0;
  background:#f9fcff;font-size:12px;text-align:center;line-height:1.6}
.df-task-empty {padding:14px 8px 24px}
.df-task-empty-icon {display:grid;place-items:center;width:54px;height:54px;margin-bottom:19px;border-radius:15px;
  background:linear-gradient(145deg,#dff0ff,#e9e8ff);color:#1769e0;font-size:28px}
.df-task-empty h3 {margin:0 0 9px;color:#172b46;font-size:22px}
.df-task-empty p {max-width:470px;margin:0;color:#64758b;font-size:14px;line-height:1.75}
.df-task-empty-steps {display:grid;gap:0;margin:13px 0 2px}
.df-task-empty-steps > div {display:flex;gap:13px;min-height:65px;position:relative}
.df-task-empty-steps > div:not(:last-child)::after {content:"";position:absolute;top:31px;left:13px;height:32px;
  border-left:1px solid #d4e3f4}
.df-task-empty-steps b {display:grid;place-items:center;width:27px;height:27px;flex:0 0 auto;border-radius:8px;
  background:#eaf2ff;color:#1769e0;font-size:11px}
.df-task-empty-steps strong {display:block;color:#233951;font-size:13px}
.df-task-empty-steps small {display:block;margin-top:3px;color:#75859a;font-size:12px}
@media(min-width:1100px) {
  [data-testid="stColumn"]:has([class*="st-key-task-center-list-"]) {min-width:270px}
}
@media(max-width:850px) {.df-task-summary-item {padding:0 12px}
  .df-task-activity {grid-template-columns:1fr;gap:7px}.df-task-activity-list {width:100%}}
</style>
"""
