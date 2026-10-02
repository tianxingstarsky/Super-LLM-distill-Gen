"""Visual hierarchy for the task-based getting-started guide."""

GUIDE_STYLE = """<style>
.df-guide-workspace {display:flex;align-items:center;flex-wrap:wrap;gap:8px 16px;margin:3px 0 18px;
  padding:12px 16px;border:1px solid #dbe8f8;border-radius:12px;background:linear-gradient(105deg,#f5f9ff,#fff);
  color:#637994;font-size:12px}
.df-guide-workspace strong {color:#24476e;font-size:13px}
.df-guide-workspace span {display:inline-flex;align-items:center;gap:6px}
.df-guide-workspace span::before {content:"";display:block;width:6px;height:6px;border-radius:50%;background:#2395e8}
.df-guide-route {display:flex;flex-wrap:wrap;gap:7px;margin:10px 0 15px}
.df-guide-route span {padding:5px 9px;border:1px solid #dfeaf7;border-radius:7px;background:#f5f9ff;
  color:#477099;font-size:11px}
.df-guide-help {display:grid;gap:9px;margin:11px 0 16px}
.df-guide-help div {display:flex;align-items:flex-start;gap:10px;padding:10px 12px;border:1px solid #e3edf8;
  border-radius:9px;background:#f9fcff;color:#5b7089;font-size:12px;line-height:1.5}
.df-guide-help b {flex:0 0 auto;display:grid;place-items:center;width:21px;height:21px;border-radius:6px;
  background:#e6f2ff;color:#126bdd;font-size:11px}
.df-guide-help strong {color:#264365}
.df-guide-flow {display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:7px 0 17px}
.df-guide-flow div {position:relative;min-height:104px;padding:15px 13px;border:1px solid #deebf7;border-radius:11px;
  background:linear-gradient(140deg,#fff,#f7fbff);box-shadow:0 3px 12px rgba(32,84,147,.04)}
.df-guide-flow b {display:block;margin-bottom:8px;color:#2481e6;font-size:11px;letter-spacing:.09em}
.df-guide-flow strong {display:block;color:#273d5b;font-size:14px}
.df-guide-flow small {display:block;margin-top:5px;color:#8191a5;font-size:11px;line-height:1.45}
[class*="st-key-guide-"] {border-color:#dce9f7!important;box-shadow:0 4px 15px rgba(32,84,147,.05)!important}
[class*="st-key-guide-"] .df-section-heading strong {font-size:16px}
.df-guide-settings {display:flex;align-items:center;gap:8px;margin:6px 0 11px;padding:12px 14px;
  border-left:3px solid #408ee9;border-radius:0 8px 8px 0;background:#f3f8ff;color:#56718e;font-size:12px;line-height:1.55}
@media(max-width:1000px) {.df-guide-flow {grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:650px) {.df-guide-flow {grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>"""
