"""Styles used only by the verified output-package page."""
from __future__ import annotations


PACKAGE_STYLE = """<style>
.df-pack-heading { display:flex; align-items:center; gap:10px; margin:0 0 15px; }
.df-pack-heading-icon { display:grid; place-items:center; flex:0 0 auto; width:33px; height:33px; border-radius:8px; background:#EAF2FF; color:#1769E0; font-size:17px; font-weight:750; }
.df-pack-heading strong { display:block; color:#17243A; font-size:15px; font-weight:750; line-height:1.4; }
.df-pack-heading small { display:block; margin-top:2px; color:#78889C; font-size:11px; line-height:1.5; }
.df-pack-run-title { display:flex; align-items:center; gap:16px; min-height:82px; padding:2px 0 19px; }
.df-pack-run-title > span:nth-child(2) { display:grid; min-width:0; gap:4px; flex:1; }
.df-pack-run-title strong { overflow:hidden; color:#15233B; font-size:20px; font-weight:760; line-height:1.3; text-overflow:ellipsis; white-space:nowrap; }
.df-pack-run-title small { overflow:hidden; color:#8090A4; font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
.df-pack-eyebrow { color:#4B77B6; font-size:10px; font-weight:700; letter-spacing:.08em; }
.df-pack-success { display:grid; place-items:center; flex:0 0 auto; width:58px; height:58px; border:11px solid #DFF9EE; border-radius:50%; background:#19A16F; color:#fff; font-size:23px; font-weight:700; box-shadow:0 2px 10px rgba(20,154,105,.12); }
.df-pack-success[data-attention="true"] { border-color:#FFF1D1; background:#DA9830; box-shadow:0 2px 10px rgba(189,122,29,.12); }
.df-pack-badge { display:inline-flex; align-items:center; width:max-content; flex:0 0 auto; padding:5px 9px; border-radius:6px; background:#E6F7EF; color:#16815B; font-size:11px; font-weight:700; }
.df-pack-badge[data-status="attention"] { background:#FFF3DD; color:#976B14; }
.df-pack-badge[data-status="failed"] { background:#FDECEF; color:#B3273D; }
.df-pack-metrics { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); border-top:1px solid #EAF0F7; }
.df-pack-metrics > div { display:grid; align-content:start; gap:5px; min-width:0; padding:15px 14px 2px; border-left:1px solid #EAF0F7; }
.df-pack-metrics > div:first-child { border-left:0; padding-left:0; }
.df-pack-metrics span { color:#8290A2; font-size:11px; }
.df-pack-metrics strong { overflow:hidden; color:#14243C; font-size:17px; font-weight:760; text-overflow:ellipsis; white-space:nowrap; }
.df-pack-metrics small { overflow:hidden; color:#8090A4; font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
.df-pack-format { display:flex; align-items:center; gap:14px; padding:15px; border:1px solid #A9C8F5; border-radius:10px; background:linear-gradient(110deg,#F1F7FF,#FBFDFF); }
.df-pack-format > span { display:grid; place-items:center; flex:0 0 auto; width:45px; height:49px; border-radius:8px; background:linear-gradient(145deg,#A55CED,#7446CA); color:#fff; font-size:12px; font-weight:800; box-shadow:0 3px 8px rgba(117,71,205,.18); }
.df-pack-format div { flex:1; min-width:0; }
.df-pack-format strong { display:block; color:#182842; font-size:13px; }
.df-pack-format small { display:block; margin-top:4px; color:#697C94; font-size:10px; line-height:1.5; }
.df-pack-format > b { display:grid; place-items:center; width:20px; height:20px; border-radius:6px; background:#1769E0; color:#fff; font-size:12px; }
.df-pack-files { overflow-x:auto; border:1px solid #E4EBF5; border-radius:10px; }
.df-pack-file-head,.df-pack-file-row { display:grid; grid-template-columns:minmax(165px,1.4fr) minmax(155px,1.3fr) 85px 115px; align-items:center; gap:10px; min-width:570px; padding:10px 12px; }
.df-pack-file-head { background:#F2F6FC; color:#5F718B; font-size:10px; font-weight:700; }
.df-pack-file-row { border-top:1px solid #E9EFF6; color:#596D88; font-size:11px; }
.df-pack-file-row > span,.df-pack-file-row code { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.df-pack-filename { display:flex; align-items:center; min-width:0; gap:8px; }
.df-pack-filename b { display:grid; place-items:center; flex:0 0 auto; width:28px; height:29px; border-radius:6px; background:#E8F1FE; color:#2070D7; font-size:8px; }
.df-pack-filename strong { overflow:hidden; color:#21324B; font-size:11px; font-weight:650; text-overflow:ellipsis; white-space:nowrap; }
.df-pack-file-row code { color:#72849B; font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:9px; }
.df-pack-preview-index { margin:13px 0 7px; color:#2869B6; font-size:10px; font-weight:750; letter-spacing:.1em; }
.df-pack-quality { overflow:hidden; border:1px solid #E3EBF5; border-radius:10px; }
.df-pack-quality-row { display:grid; grid-template-columns:66px 130px minmax(0,1fr); align-items:center; gap:10px; min-height:43px; padding:8px 12px; border-top:1px solid #E9EFF6; font-size:11px; }
.df-pack-quality-row:first-child { border-top:0; }
.df-pack-quality-row b { color:#276ABF; font-size:10px; }
.df-pack-quality-row span { color:#657890; }
.df-pack-quality-row strong { color:#1B825D; }
.df-pack-quality-row small { overflow:hidden; color:#8492A6; text-overflow:ellipsis; white-space:nowrap; }
.df-pack-source { display:grid; gap:3px; padding:8px 0; border-bottom:1px solid #EDF1F7; }
.df-pack-source strong { color:#263951; font-size:12px; }
.df-pack-source small { overflow:hidden; color:#8593A5; font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
.df-pack-export-state { display:flex; align-items:center; gap:12px; margin:16px 0 8px; }
.df-pack-export-state > span { display:grid; place-items:center; flex:0 0 auto; width:27px; height:27px; border-radius:50%; background:#16A16D; color:#fff; font-size:16px; }
.df-pack-export-state[data-attention="true"] > span { background:#DA9830; }
.df-pack-export-state strong { display:block; color:#18324B; font-size:12px; }
.df-pack-export-state small { display:block; margin-top:3px; color:#7E8DA0; font-size:10px; line-height:1.5; }
.df-pack-zip-ready { display:flex; align-items:center; gap:10px; margin:12px 0 3px; padding:10px; border-radius:9px; background:#F0F7FF; }
.df-pack-zip-ready b { display:grid; place-items:center; width:32px; height:34px; border-radius:6px; background:#EDE6FA; color:#764CC1; font-size:9px; }
.df-pack-zip-ready strong { display:block; color:#18324B; font-size:11px; }
.df-pack-zip-ready small { display:block; color:#7788A0; font-size:10px; }
.df-pack-trainer { display:grid; grid-template-columns:52px 53px 85px minmax(0,1fr); align-items:center; gap:7px; padding:9px 0; border-top:1px solid #EAF0F7; }
.df-pack-trainer > b { color:#256ABF; font-size:10px; }
.df-pack-trainer-status { padding:4px 5px; border-radius:5px; background:#E5F7EF; color:#13815C; text-align:center; font-size:9px; font-weight:700; }
.df-pack-trainer-status[data-ready="false"] { background:#FFF2E0; color:#966A1D; }
.df-pack-trainer > span:nth-child(3) { color:#596C84; font-size:10px; }
.df-pack-trainer small { overflow:hidden; color:#7E8DA0; font-size:9px; text-overflow:ellipsis; white-space:nowrap; }
.df-pack-recent { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:4px; padding:10px 0; border-top:1px solid #E9EFF6; }
.df-pack-recent strong { overflow:hidden; color:#24364D; font-size:11px; text-overflow:ellipsis; white-space:nowrap; }
.df-pack-recent > span:not(.df-pack-badge) { color:#8492A6; font-size:10px; }
.df-pack-recent small { grid-column:1 / -1; color:#8A99AC; font-size:10px; }
.df-pack-release-title { display:flex; align-items:center; gap:12px; margin:10px 0 14px; padding:14px; border:1px solid #D9E8F7; border-radius:10px; background:#F6FAFF; }
.df-pack-release-title > span { display:grid; place-items:center; flex:0 0 auto; width:36px; height:36px; border-radius:50%; background:#DDF6E9; color:#18815D; font-size:20px; font-weight:750; }
.df-pack-release-title[data-verified="false"] > span { background:#FFF0D9; color:#AB7519; }
.df-pack-release-title > div { display:grid; gap:3px; min-width:0; }
.df-pack-release-title strong { overflow:hidden; color:#1B2D47; font-size:16px; text-overflow:ellipsis; white-space:nowrap; }
.df-pack-release-title small { color:#71859C; font-size:11px; }
.df-pack-release-files { overflow:hidden; margin:8px 0 16px; border:1px solid #E2EAF5; border-radius:9px; }
.df-pack-release-files > div { display:grid; grid-template-columns:minmax(0,1fr) 80px 105px; gap:8px; align-items:center; padding:9px 12px; border-top:1px solid #EAF0F7; color:#6D7E94; font-size:12px; }
.df-pack-release-files > div:first-child { border-top:0; }
.df-pack-release-files strong { overflow:hidden; color:#293C55; text-overflow:ellipsis; white-space:nowrap; }
.df-pack-release-files code { overflow:hidden; font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
.df-pack-empty { display:grid; justify-items:start; gap:10px; padding:22px 8px 25px; }
.df-pack-empty > span { display:grid; place-items:center; width:50px; height:50px; border-radius:13px; background:#EBF3FF; color:#1769E0; font-size:22px; }
.df-pack-empty strong { color:#192A43; font-size:18px; }
.df-pack-empty p { max-width:560px; margin:0; color:#6F829B; font-size:12px; line-height:1.7; }
.df-pack-targets { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:9px; }
.df-pack-targets > div { display:grid; align-content:center; gap:5px; min-height:83px; padding:10px; border:1px solid #E3EBF6; border-radius:9px; background:#FBFDFF; }
.df-pack-targets b { color:#1769E0; font-size:10px; }
.df-pack-targets strong { color:#20334C; font-size:11px; }
.df-pack-targets small { color:#8A98A9; font-size:9px; line-height:1.4; }
.df-pack-steps { display:grid; gap:11px; margin:9px 0 13px; }
.df-pack-steps > div { display:grid; grid-template-columns:25px 1fr; gap:8px; }
.df-pack-steps b { display:grid; place-items:center; width:23px; height:23px; border-radius:50%; background:#EAF2FF; color:#1769E0; font-size:9px; }
.df-pack-steps strong { display:block; color:#26384F; font-size:11px; }
.df-pack-steps small { display:block; color:#8291A4; font-size:10px; }
.df-pack-review-target { display:flex; align-items:center; gap:11px; margin:2px 0 14px; padding:12px; border:1px solid #D8E6F9; border-radius:9px; background:#F5F9FF; color:#314664; font-size:13px; }
.df-pack-review-target b { display:grid; place-items:center; min-width:44px; height:31px; padding:0 6px; border-radius:7px; background:#E5F0FF; color:#1769D5; font-size:11px; }
.df-pack-metrics > div { min-height:100px; padding-top:17px; }
.df-pack-target-chips { display:flex; align-content:start; flex-wrap:wrap; gap:4px; max-height:53px; overflow:auto; }
.df-pack-target-chips b { padding:4px 6px; border-radius:5px; background:#EAF3FF; color:#226AC1; font-size:10px; line-height:1.2; }
.df-pack-target-chips span { color:#7D8FA6; }
.df-pack-rate { display:flex; align-items:center; height:43px; }
.df-pack-rate i { display:grid; place-items:center; width:43px; height:43px; border-radius:50%; background:conic-gradient(#2377DA var(--pack-rate),#E7EFFB 0); font-style:normal; }
.df-pack-rate b { display:grid; place-items:center; width:32px; height:32px; border-radius:50%; background:white; color:#1B314C; font-size:11px; }
.df-pack-metrics .df-pack-date { overflow:visible; font-size:13px; line-height:1.45; white-space:normal; }
.df-pack-format-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
.df-pack-format-card { display:grid; grid-template-columns:38px minmax(0,1fr); grid-template-rows:auto auto 1fr; column-gap:10px; min-height:106px; padding:13px; border:1px solid #DFE8F5; border-radius:10px; background:#FBFDFF; }
.df-pack-format-card[data-kind="zip"] { border-color:#9BC1F3; background:linear-gradient(135deg,#EDF6FF,#FAFCFF 75%); box-shadow:0 2px 10px rgba(39,111,208,.05); }
.df-pack-format-card[data-available="false"] { background:#FAFBFD; border-color:#ECF0F5; opacity:.73; }
.df-pack-format-icon { grid-column:1; grid-row:1 / 4; display:grid; place-items:center; align-self:start; width:36px; height:38px; border-radius:9px; background:#E8F1FF; color:#236DCF; font-size:12px; font-weight:800; }
.df-pack-format-card[data-kind="zip"] .df-pack-format-icon { background:linear-gradient(150deg,#9862E2,#7549C7); color:white; }
.df-pack-format-card[data-kind="trl"] .df-pack-format-icon { background:#E8F6EF; color:#139968; }
.df-pack-format-card[data-kind="proof"] .df-pack-format-icon { background:#EAF4FA; color:#2280AD; }
.df-pack-format-label { grid-column:2; justify-self:start; margin-bottom:3px; padding:2px 6px; border-radius:5px; background:#E9F2FF; color:#1E6DCA; font-size:10px; font-weight:700; }
.df-pack-format-card[data-available="false"] .df-pack-format-label { background:#EFF2F6; color:#8794A4; }
.df-pack-format-card strong { grid-column:2; color:#172B47; font-size:13px; line-height:1.4; }
.df-pack-format-card small { grid-column:2; color:#73859C; font-size:11px; line-height:1.5; }
.df-pack-delivery-step { display:flex; align-items:center; gap:12px; margin:8px 0 10px; padding-top:13px; border-top:1px solid #EAF0F7; }
.df-pack-delivery-step > b { display:grid; place-items:center; flex:0 0 auto; width:27px; height:27px; border-radius:50%; background:#EDF3FC; color:#2771CD; font-size:12px; }
.df-pack-delivery-step[data-ready="true"] > b { background:#16A16D; color:white; }
.df-pack-delivery-step strong { display:block; color:#263951; font-size:12px; }
.df-pack-delivery-step small { display:block; margin-top:3px; color:#7E8DA0; font-size:11px; line-height:1.45; }
/* The reference uses compact cards, but the original 9–10 px labels were hard to read at desktop width. */
.df-pack-heading strong { font-size:16px; }
.df-pack-heading small, .df-pack-metrics span, .df-pack-metrics small, .df-pack-format small,
.df-pack-export-state small, .df-pack-zip-ready small, .df-pack-source small,
.df-pack-recent small, .df-pack-steps small { font-size:12px; }
.df-pack-run-title strong { font-size:22px; }
.df-pack-run-title small, .df-pack-eyebrow, .df-pack-badge { font-size:11px; }
.df-pack-metrics strong { font-size:19px; }
.df-pack-format strong, .df-pack-export-state strong { font-size:14px; }
.df-pack-file-head, .df-pack-file-row, .df-pack-filename strong,
.df-pack-quality-row, .df-pack-source strong, .df-pack-recent strong,
.df-pack-targets strong, .df-pack-steps strong { font-size:12px; }
.df-pack-file-row code, .df-pack-quality-row small, .df-pack-trainer small { font-size:11px; }
.df-pack-zip-ready strong, .df-pack-trainer > span:nth-child(3) { font-size:12px; }
.df-pack-empty p { font-size:13px; }
@media(max-width:1000px) {
  .df-pack-metrics { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .df-pack-metrics > div:nth-child(3) { border-left:0; padding-left:0; }
  .df-pack-targets { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .df-pack-quality-row { grid-template-columns:55px 115px minmax(0,1fr); }
}
@media(max-width:1350px) {
  .df-pack-targets { grid-template-columns:repeat(2,minmax(0,1fr)); }
}
@media(max-width:620px) {
  .df-pack-run-title { flex-wrap:wrap; }
  .df-pack-run-title > .df-pack-badge { margin-left:73px; }
  .df-pack-metrics strong { font-size:14px; }
  .df-pack-quality-row { grid-template-columns:55px 1fr; }
  .df-pack-quality-row small { grid-column:2; }
  .df-pack-format-grid { grid-template-columns:1fr; }
  .df-pack-release-files > div { grid-template-columns:minmax(0,1fr) 70px; }
  .df-pack-release-files code { grid-column:1 / -1; }
}
</style>"""
