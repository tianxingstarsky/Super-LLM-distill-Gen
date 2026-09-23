"""Visual language for the persisted-workflow task center."""
from __future__ import annotations


def task_management_styles() -> str:
    return """
<style>
.df-task-summary {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:4px 0 19px}
.df-task-summary-item {position:relative;min-height:105px;padding:17px 18px 14px 58px;
  border:1px solid #e0e9f5;border-radius:14px;background:linear-gradient(145deg,#fff,#f8fbff);
  box-shadow:0 5px 18px rgba(31,85,148,.035)}
.df-task-summary-item i {position:absolute;left:16px;top:17px;display:grid;place-items:center;width:30px;height:30px;
  border-radius:9px;background:#e9f3ff;color:#1768d5;font-size:15px;font-style:normal}
.df-task-summary-item strong {display:block;color:#17385f;font-size:27px;line-height:1.05;letter-spacing:-.04em}
.df-task-summary-item span {display:block;color:#6c7f99;font-size:12px;margin-top:10px}
.df-task-summary-item[data-kind="completed"] i {background:#e1f7ed;color:#078b63}
.df-task-summary-item[data-kind="attention"] i {background:#fff0e8;color:#d46b35}
.df-task-summary-item[data-kind="processing"] i {background:#e8f1ff;color:#1768d5}
.df-task-list-head {display:flex;align-items:center;justify-content:space-between;gap:8px;margin:3px 0 11px}
.df-task-list-head strong {color:#1c304d;font-size:16px;letter-spacing:.01em}
.df-task-list-head small {color:#8291a7;font-size:12px;text-align:right}
[class*="st-key-task-center-filter"] {max-width:100%}
[class*="st-key-task-center-filter"] [role="radiogroup"] {max-width:100%;box-sizing:border-box}
[class*="st-key-task-center-filter"] [role="radio"] {min-width:0;padding:4px 7px;font-size:11px}
[class*="st-key-task_card_"] {padding:11px 13px 12px;margin:0 0 9px;border:1px solid #dfebf7;
  border-radius:13px;background:#fff;box-shadow:0 3px 12px rgba(27,80,151,.045);
  transition:border-color .18s ease,box-shadow .18s ease,background .18s ease}
[class*="st-key-task_card_"]:hover {border-color:#8ebaf2;box-shadow:0 6px 17px rgba(36,112,208,.11)}
[class*="st-key-task_card_selected_"] {border-color:#3d8ce8;background:linear-gradient(115deg,#f0f7ff,#fff 80%);
  box-shadow:0 0 0 2px rgba(49,124,225,.11)}
[class*="st-key-task_card_completed_"] {border-left:3px solid #15a97e}
[class*="st-key-task_card_running_"] {border-left:3px solid #277fe4}
[class*="st-key-task_card_failed_"], [class*="st-key-task_card_needs_attention_"] {border-left:3px solid #e4864b}
.df-task-card-top,.df-task-card-progress {display:flex;align-items:center;justify-content:space-between;gap:7px}
.df-task-card-top time,.df-task-card-progress {color:#8392a7;font-size:11px}
.df-task-status {display:inline-flex;align-items:center;gap:5px;padding:4px 8px;border-radius:6px;
  background:#eaf3ff;color:#256ab9;font-size:11px;font-weight:700;line-height:1}
.df-task-status i {font-size:12px;font-style:normal}
.df-task-status[data-status="completed"] {background:#e2f7ee;color:#087d5c}
.df-task-status[data-status="failed"],.df-task-status[data-status="needs_attention"] {background:#fff0e7;color:#b86131}
.df-task-status[data-status="cancelled"] {background:#f0f2f6;color:#627184}
[class*="st-key-task_card_"] .stButton button {display:block;width:100%;min-height:34px;padding:6px 0 4px;
  border:0;border-radius:0;background:transparent;text-align:left;justify-content:flex-start;
  color:#20334f;box-shadow:none}
[class*="st-key-task_card_"] .stButton button > div,
[class*="st-key-task_card_"] .stButton button > div > span {justify-content:flex-start;width:100%}
[class*="st-key-task_card_"] .stButton button:hover,
[class*="st-key-task_card_"] .stButton button:focus {border:0;background:transparent;color:#145dad;box-shadow:none}
[class*="st-key-task_card_"] .stButton button p {font-size:13px;line-height:1.4;font-weight:650;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.df-task-card-targets {display:flex;flex-wrap:wrap;gap:4px;max-height:41px;overflow:hidden;margin:1px 0 9px}
.df-task-card-targets span {display:inline-block;padding:2px 5px;border-radius:4px;
  background:#eff5ff;color:#5b7191;font-size:10px;line-height:1.3}
.df-task-card-progress b {color:#315a8e;font-size:11px}
.df-task-meter {height:5px;margin-top:6px;border-radius:100px;background:#e8eff8;overflow:hidden}
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
@media(max-width:850px) {.df-task-summary {grid-template-columns:repeat(2,minmax(0,1fr))}
  .df-task-activity {grid-template-columns:1fr;gap:7px}.df-task-activity-list {width:100%}}
</style>
"""
