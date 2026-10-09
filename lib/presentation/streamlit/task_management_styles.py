"""Visual language for the persisted-workflow task center."""
from __future__ import annotations


def task_management_styles() -> str:
    return """
<style>
.st-key-task-manager-navigation {container-type:inline-size;gap:0;padding:0;margin:0}
.st-key-task-manager-navigation [data-testid="stPopover"] > button {min-height:44px}
:is(.st-key-task-manager-navigation, .st-key-task-manager-navigation > [data-testid="stLayoutWrapper"]) > [data-testid="stHorizontalBlock"] {
  gap:12px;align-items:center}
:is(.st-key-task-manager-navigation, .st-key-task-manager-navigation > [data-testid="stLayoutWrapper"]) > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child {
  flex:1 1 0 !important;width:auto !important;min-width:0 !important}
:is(.st-key-task-manager-navigation, .st-key-task-manager-navigation > [data-testid="stLayoutWrapper"]) > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child {
  flex:0 0 180px !important;width:180px !important;max-width:180px;min-width:0 !important;margin-left:auto}
.st-key-task-center-toolbar {container-type:inline-size;gap:0;padding:0 0 4px;margin:0}
:is(.st-key-task-center-toolbar, .st-key-task-center-toolbar > [data-testid="stLayoutWrapper"]) > [data-testid="stHorizontalBlock"] {align-items:center}
.df-task-summary {display:flex;align-items:center;flex-wrap:wrap;gap:5px 15px;
  min-height:40px;margin:0;padding:0;background:transparent}
.df-task-summary-item {display:inline-flex;align-items:baseline;gap:6px;padding:0;
  line-height:1.4;white-space:nowrap}
.df-task-summary-item strong {color:#23425f;font-size:17px;font-weight:720;font-variant-numeric:tabular-nums;letter-spacing:-.025em}
.df-task-summary-item span {color:#5b6d84;font-size:12px}
.df-task-summary-item[data-kind="completed"] strong {color:#087d5c}
.df-task-summary-item[data-kind="attention"] strong {color:#9e4e25}
.df-task-summary-item[data-kind="processing"] strong {color:#1768d5}
.df-task-list-head {display:flex;align-items:center;justify-content:space-between;gap:8px;margin:0 0 3px}
.df-task-list-head strong {color:#1c304d;font-size:16px;letter-spacing:.01em}
.df-task-list-head small {color:#64758b;font-size:12px;text-align:right}
.st-key-task-center-body {container-type:inline-size}
:is(.st-key-task-center-body, .st-key-task-center-body > [data-testid="stLayoutWrapper"]) > [data-testid="stHorizontalBlock"]:has([class*="st-key-task-center-list-"]) {
  gap:20px;align-items:flex-start}
:is(.st-key-task-center-body, .st-key-task-center-body > [data-testid="stLayoutWrapper"]) > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:has([class*="st-key-task-center-list-"]) {
  flex:0 0 290px !important;width:290px !important;min-width:0 !important;max-width:290px}
:is(.st-key-task-center-body, .st-key-task-center-body > [data-testid="stLayoutWrapper"]) > [data-testid="stHorizontalBlock"]:has([class*="st-key-task-center-list-"]) > [data-testid="stColumn"]:not(:has([class*="st-key-task-center-list-"])) {
  flex:1 1 0 !important;width:auto !important;min-width:0 !important}
[class*="st-key-task-center-list-"] {padding:3px 16px 3px 0;border:0;border-right:1px solid #dce7f4;
  border-radius:0;background:transparent;box-shadow:none;gap:9px}
[class*="st-key-task-center-filter"] {max-width:100%}
[class*="st-key-task-center-filter"] [role="radiogroup"] {max-width:100%;box-sizing:border-box}
[class*="st-key-task-center-filter"] [role="radio"] {min-width:0;padding:4px 7px;font-size:12px}
[class*="st-key-task-center-filter"] [data-testid="stButtonGroup"] {width:100%}
[class*="st-key-task-center-filter"] [role="radiogroup"] {display:grid!important;width:100%;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px}
[class*="st-key-task-center-filter"] [data-testid="stButtonGroup"] button {width:100%;min-width:0;min-height:40px}
.st-key-task-center-toolbar [data-testid="stWidgetLabel"] :is([title],[data-testid="stMarkdownContainer"],p) {white-space:normal;overflow:visible;text-overflow:clip}
[class*="st-key-task_card_"] {padding:10px 9px;margin:0;border:1px solid #dbe5f1;
  border-left:3px solid #dbe5f1;border-radius:10px;background:linear-gradient(145deg,#fff,#f3f7fd);
  box-shadow:inset 0 1px 0 #fff,0 1px 2px #264a700c,0 6px 12px -10px #315c8938}
[class*="st-key-task_card_"][data-testid="stVerticalBlock"],
[class*="st-key-task_card_"] > [data-testid="stVerticalBlock"] {gap:4px}
[class*="st-key-task_card_selected_"] {border-color:#b9d3f4;border-left-color:#3d8ce8;
  background:linear-gradient(125deg,#f9fcff,#e4f0ff);box-shadow:inset 0 1px 0 #fff,0 2px 4px #2462a512,0 9px 16px -12px #2462a54a}
[class*="st-key-task_card_completed_"] {border-left:3px solid #15a97e}
[class*="st-key-task_card_running_"] {border-left:3px solid #277fe4}
[class*="st-key-task_card_failed_"] {border-left:3px solid #d4596d}
[class*="st-key-task_card_needs_attention_"] {border-left:3px solid #e4864b}
.df-task-card-top,.df-task-card-progress {display:flex;align-items:center;justify-content:space-between;gap:7px}
.df-task-card-top {flex-wrap:wrap}
.df-task-card-top time,.df-task-card-progress {color:#64758b;font-size:12px;line-height:1.5}
.df-task-status {display:inline-flex;align-items:center;gap:5px;padding:4px 8px;border-radius:6px;
  background:#eaf3ff;box-shadow:inset 0 1px 0 #ffffffd9,0 1px 2px #315c8910;
  color:#256ab9;font-size:12px;font-weight:700;line-height:1;white-space:nowrap;flex-shrink:0}
.df-task-status i {font-size:12px;font-style:normal}
.df-task-status[data-status="completed"] {background:#e2f7ee;color:#087d5c}
.df-task-status[data-status="failed"] {background:#fcebef;color:#af354b}
.df-task-status[data-status="needs_attention"] {background:#fff0e7;color:#9e4e25}
.df-task-status[data-status="cancelled"] {background:#f0f2f6;color:#627184}
[class*="st-key-task_card_"] .stButton button {display:block;width:100%;min-height:44px;padding:6px 0;
  border:0;border-radius:6px;background:transparent;text-align:left;justify-content:flex-start;
  color:#20334f;box-shadow:none;transition:background .15s ease,color .15s ease}
[class*="st-key-task_card_"] .stButton button > div,
[class*="st-key-task_card_"] .stButton button > div > span {justify-content:flex-start;width:100%}
[class*="st-key-task_card_"] .stButton button:hover,
[class*="st-key-task_card_"] .stButton button:focus {border:0;background:linear-gradient(100deg,#e1edfc,#f2f7ff);color:#145dad;box-shadow:inset 0 1px 0 #fff,0 1px 2px #315c8910}
[class*="st-key-task_card_"] .stButton button:active {background:#deebfc;box-shadow:inset 0 2px 4px #315c8920}
[class*="st-key-task_card_"] .stButton button:focus-visible {outline:2px solid #1769e0;outline-offset:2px;border-radius:5px}
[class*="st-key-task_card_"] .stButton button p {font-size:14px;line-height:1.4;font-weight:650;overflow-wrap:anywhere}
.df-task-card-targets {display:flex;flex-wrap:wrap;gap:4px;margin:0 0 4px}
.df-task-card-targets > span {display:inline-block;flex:0 0 auto;padding:2px 6px;border-radius:5px;
  background:#eff5ff;color:#4f6788;font-size:12px;line-height:1.4;white-space:nowrap}
.df-task-sr-only {position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
  clip:rect(0,0,0,0);clip-path:inset(50%);white-space:nowrap;border:0}
.df-task-card-source {display:flex;align-items:baseline;gap:6px;min-width:0;margin:0 0 4px;
  color:#5b6d84;font-size:12px}
.df-task-card-source b {flex:0 0 auto;color:#4c6c93;font-size:12px;font-weight:600}
.df-task-card-source span {min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.df-task-card-progress b {color:#315a8e;font-size:12px}
.df-task-card-output {display:flex;align-items:center;justify-content:space-between;gap:7px;margin-bottom:4px;
  color:#64758b;font-size:12px;line-height:1.5}
.df-task-card-output b {color:#315a8e;font-size:12px}
.df-task-meter {height:4px;margin-top:7px;border-radius:3px;background:#e1eaf6;box-shadow:inset 0 1px 2px #315c8917;overflow:hidden}
.df-task-meter i {display:block;height:100%;border-radius:100px;background:linear-gradient(90deg,#26a7db,#2469dd);box-shadow:inset 0 1px 0 #ffffff4d}
.df-task-meter[data-status="completed"] i {background:linear-gradient(90deg,#34c9a0,#139967)}
.df-task-activity {display:grid;grid-template-columns:100px minmax(0,1fr);align-items:start;gap:15px;
  padding:15px 17px;margin:1px 0 15px;border:1px solid #d6e3f2;border-radius:14px;
  background:linear-gradient(145deg,#fff,#edf5ff);box-shadow:inset 0 1px 0 #fff,0 2px 4px #315c8910}
.df-task-activity-head strong {display:block;font-size:12px;color:#1f4d82}
.df-task-activity-head small {display:block;margin-top:3px;color:#64758b;font-size:12px}
.df-task-activity-list {display:grid;gap:6px;min-width:0}
.df-task-activity-event {display:grid;grid-template-columns:8px minmax(0,1fr) auto;
  align-items:center;gap:8px;min-width:0;font-size:12px;color:#4e6280}
.df-task-activity-event > i {width:7px;height:7px;border-radius:50%;background:#3487e5}
.df-task-activity-event > i[data-kind="stage_completed"],.df-task-activity-event > i[data-kind="run_finished"] {background:#17a87e}
.df-task-activity-event > i[data-kind="run_failed"] {background:#de7955}
.df-task-activity-event span {min-width:0;overflow-wrap:anywhere}
.df-task-activity-event b {color:#2a527e;font-weight:700}
.df-task-activity-event time {flex:0 0 auto;color:#64758b;font-size:12px}
.df-task-activity-empty {align-items:center}
.df-task-activity-empty span {font-size:12px;color:#5b6d84}
.df-task-no-match {padding:22px;border:1px dashed #d5e2f2;border-radius:10px;color:#5b6d84;
  background:#f9fcff;font-size:12px;text-align:center;line-height:1.6}
.st-key-task-center-empty {padding:24px;border:1px solid #d4e2f3;border-radius:16px;
  background:radial-gradient(circle at 95% 8%,#d6e8ff,transparent 47%),linear-gradient(120deg,#fff,#f1f7ff);
  box-shadow:var(--df-shadow-panel,0 2px 3px #27466e06,0 12px 32px -14px #27466e30,inset 0 1px 0 #fff)}
.df-task-empty {position:relative;overflow:hidden;padding:0 0 20px;border:0;background:transparent}
.df-task-empty::after {content:"";position:absolute;top:28px;right:20px;width:54px;height:54px;
  border:1px solid #c7dcf5;border-radius:14px;transform:rotate(25deg);pointer-events:none;
  background:linear-gradient(145deg,#ffffffbb,#93c2f526);box-shadow:9px 10px 0 -2px #d9e8fb66}
.df-task-empty-icon {display:grid;place-items:center;width:54px;height:54px;margin-bottom:19px;border-radius:15px;
  border:1px solid #ccdff6;background:linear-gradient(145deg,#fff,#d8e6fc);box-shadow:inset 0 1px 0 #fff,0 5px 9px #315c8921;color:#1769e0;font-size:28px}
.df-task-empty h3 {margin:0 0 9px;color:#172b46;font-size:22px}
.df-task-empty p {max-width:470px;margin:0;color:#64758b;font-size:14px;line-height:1.75}
.df-task-empty-steps {display:grid;gap:0;margin:13px 0 2px}
.df-task-empty-steps > div {display:flex;gap:13px;min-height:65px;position:relative}
.df-task-empty-steps > div:not(:last-child)::after {content:"";position:absolute;top:31px;left:13px;height:32px;
  border-left:1px solid #d4e3f4}
.df-task-empty-steps b {display:grid;place-items:center;width:27px;height:27px;flex:0 0 auto;border-radius:8px;
  border:1px solid #d3e2f6;background:linear-gradient(145deg,#fff,#e0ecfc);box-shadow:inset 0 1px 0 #fff,0 2px 3px #315c8912;color:#1769e0;font-size:12px}
.df-task-empty-steps strong {display:block;color:#233951;font-size:14px}
.df-task-empty-steps small {display:block;margin-top:3px;color:#5b6d84;font-size:12px}
@media(prefers-reduced-motion:reduce) { [class*="st-key-task_card_"] .stButton button {transition:none} }
@container(max-width:880px) {
  :is(.st-key-task-center-toolbar, .st-key-task-center-toolbar > [data-testid="stLayoutWrapper"]) > [data-testid="stHorizontalBlock"] {flex-wrap:wrap;gap:8px 12px}
  :is(.st-key-task-center-toolbar, .st-key-task-center-toolbar > [data-testid="stLayoutWrapper"]) > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child {
    flex:1 1 100% !important;width:100% !important;min-width:0 !important}
}
@container(max-width:760px) {
  :is(.st-key-task-center-body, .st-key-task-center-body > [data-testid="stLayoutWrapper"]) > [data-testid="stHorizontalBlock"]:has([class*="st-key-task-center-list-"]) {
    display:grid !important;grid-template-columns:minmax(0,1fr);gap:16px}
  :is(.st-key-task-center-body, .st-key-task-center-body > [data-testid="stLayoutWrapper"]) > [data-testid="stHorizontalBlock"]:has([class*="st-key-task-center-list-"]) > [data-testid="stColumn"] {
    flex:none !important;width:100% !important;min-width:0 !important;max-width:100% !important}
  [class*="st-key-task-center-list-"] {padding:0 0 14px;border-right:0;border-bottom:1px solid #dce7f4}
}
@media(max-width:850px) {
  .df-task-activity {grid-template-columns:1fr;gap:7px}.df-task-activity-list {width:100%}}
@media(max-width:720px) {
  /* Match the page's own rows by their existing content/key. Nested runtime
     forms and the floating node inspector keep their independent layouts. */
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [class*="st-key-task-center-list-"]),
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .df-task-empty) {
    display:grid !important;grid-template-columns:minmax(0,1fr);gap:14px;
  }
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [class*="st-key-task-center-list-"]) > [data-testid="stColumn"],
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .df-task-empty) > [data-testid="stColumn"] {
    flex:none !important;width:100% !important;min-width:0 !important;max-width:100%;
  }
  [class*="st-key-task-center-new"] button,
  [class*="st-key-task-center-create"] button {min-height:44px}
  :is(.st-key-task-manager-navigation, .st-key-task-manager-navigation > [data-testid="stLayoutWrapper"]) > [data-testid="stHorizontalBlock"] {flex-wrap:wrap;row-gap:8px}
  :is(.st-key-task-manager-navigation, .st-key-task-manager-navigation > [data-testid="stLayoutWrapper"]) > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child {
    flex:1 1 100% !important;width:100% !important;min-width:0 !important}
  :is(.st-key-task-manager-navigation, .st-key-task-manager-navigation > [data-testid="stLayoutWrapper"]) > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child {
    flex:0 1 180px !important;width:180px !important;min-width:0 !important}
  :is(.st-key-task-center-toolbar, .st-key-task-center-toolbar > [data-testid="stLayoutWrapper"]) > [data-testid="stHorizontalBlock"] {
    display:grid !important;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 12px}
  :is(.st-key-task-center-toolbar, .st-key-task-center-toolbar > [data-testid="stLayoutWrapper"]) > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    width:100% !important;min-width:0 !important;max-width:100%;flex:none !important}
  :is(.st-key-task-center-toolbar, .st-key-task-center-toolbar > [data-testid="stLayoutWrapper"]) > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child {grid-column:1/-1}
  .df-task-summary {gap:7px 14px}
  [class*="st-key-task-center-list-"] {padding:0 0 14px;border-right:0;border-bottom:1px solid #dce7f4}
  [class*="st-key-task-center-filter"] [role="radiogroup"] {
    display:grid !important;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;
  }
  [class*="st-key-task-center-filter"] [role="radio"] {
    width:100%;min-width:0;min-height:36px;padding:7px 8px;white-space:normal;
  }
  .st-key-task-center-empty {padding:20px}
  .df-task-empty {padding:0 0 12px}
  .df-task-empty h3 {font-size:20px}
}
</style>
"""
