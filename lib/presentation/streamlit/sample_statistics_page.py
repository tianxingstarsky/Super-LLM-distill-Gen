"""A compact native-record dashboard; heavy scans run in an isolated worker."""
import html
import json

import streamlit as st

from lib.domain.workflow_delivery import has_deliverable_results, delivery_manifest_matches
from lib.domain.workflow_targets import TARGETS
from lib.presentation.file_sizes import format_file_size as size_label
from lib.presentation.streamlit.dataset_preview_page import TARGET_LABELS
from lib.presentation.streamlit.i18n import UntranslatedText, translate


STYLE = """<style>
.df-dist-intro{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:19px 24px;margin:12px 0 16px;border:1px solid #d8e7fb;border-radius:16px;background:linear-gradient(115deg,#fff,#edf6ff);box-shadow:inset 0 1px 0 #fff,0 6px 22px #315c890a}
.df-dist-intro strong{display:block;color:#183957;font-size:19px}.df-dist-intro p{margin:5px 0 0;color:#677e96;font-size:13px;line-height:1.6}.df-dist-orbit{display:grid;place-items:center;flex:none;width:68px;height:68px;border:9px solid #daeaff;border-top-color:#2480ee;border-right-color:#38c8b1;border-radius:50%;background:#fff;color:#2768a8;font-size:18px;font-weight:750;box-shadow:inset 0 2px 8px #2258a31a,0 3px 8px #2258a312}
.df-dist-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:0 0 18px}.df-dist-metrics>div{padding:17px 19px;border:1px solid #e0e9f4;border-radius:12px;background:linear-gradient(145deg,#fff,#f6faff);box-shadow:inset 0 1px 0 #fff,0 3px 10px #204f7d07}.df-dist-metrics span,.df-dist-metrics small{display:block;color:#70849b;font-size:12px;line-height:1.55}.df-dist-metrics strong{display:block;margin:6px 0;color:#174d89;font-size:26px;font-variant-numeric:tabular-nums;line-height:1.25}
.df-dist-panel{padding:19px 21px;margin:0 0 15px;border:1px solid #e0e8f3;border-radius:13px;background:#fff;box-shadow:0 3px 12px #315c8906}.df-dist-panel h3{margin:0 0 15px;color:#244360;font-size:15px}.df-dist-row{margin:0 0 12px}.df-dist-row:last-child{margin:0}.df-dist-row>div{display:flex;justify-content:space-between;gap:10px;margin:0 0 6px;color:#54708c;font-size:12px}.df-dist-row b{color:#24496d;font-size:12px;font-variant-numeric:tabular-nums}.df-dist-track{height:7px;overflow:hidden;border-radius:8px;background:#edf3fa}.df-dist-track i{display:block;height:100%;border-radius:8px;background:linear-gradient(90deg,#3b91ef,#52c8c4)}
.df-dist-note{color:#778ba1;font-size:12px;line-height:1.65}.df-dist-tags{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 15px}.df-dist-tags span{padding:6px 11px;border:1px solid #dde9f6;border-radius:8px;background:#f5f9ff;color:#416986;font-size:12px}.df-dist-size{font-variant-numeric:tabular-nums}
</style>"""


def _l(zh, en):
    return en if st.session_state.get("ui_language") == "en" else zh


def _safe(value):
    return html.escape(str(value), quote=True)


def _target_label(value):
    return translate(TARGET_LABELS.get(value, value.upper()), st.session_state.get("ui_language", "zh"))


def _bars(title, values, labels=None):
    total = sum(values.values())
    rows = []
    for key, count in values.items():
        percent = 100 * count / total if total else 0
        label = (labels or {}).get(key, key)
        rows.append('<div class="df-dist-row"><div><span>' + _safe(label)
                    + '</span><b>' + f'{count:,} · {percent:.1f}%' + '</b></div>'
                    + '<div class="df-dist-track"><i style="width:' + f'{percent:.3f}%'
                    + '"></i></div></div>')
    return '<section class="df-dist-panel"><h3>' + _safe(title) + '</h3>' + ''.join(rows) + '</section>'


def open_sample_statistics(run_id):
    workspace = st.session_state["ws"]
    st.session_state[f"sample-stat-run:{workspace}"] = run_id
    st.session_state[f"data-view:{workspace}"] = "样本统计"
    st.session_state["nav"] = "数据管理"


def _render_result(result, scope_key):
    choices = ["all", *result["targets"]]
    scope = st.selectbox(_l("统计范围", "Distribution scope"), choices, key=scope_key,
                         format_func=lambda value: _l("全部训练目标", "All training targets") if value == "all"
                         else _l("Agent 失败轨迹（单独统计）", "Agent failure traces (separate)") if value == "agent_negative"
                         else _target_label(value))
    row = result["total"] if scope == "all" else result["targets"][scope]
    st.html('<div class="df-dist-tags">'
            + ''.join('<span>' + _safe(label) + ' <b>' + f'{value:,}' + '</b></span>' for label, value in (
                (_l("当前样本", "Records in scope"), row["rows"]),
                (_l("含推理", "With reasoning"), row["reasoning_rows"]),
                (_l("含图片", "With images"), row["image_rows"]),
                (_l("含工具", "With tools"), row["tool_rows"]))) + '</div>')
    languages = dict(zip(("zh", "en", "mixed", "other", "none"),
                         (_l("中文为主", "Mostly Chinese"), _l("英文为主", "Mostly English"),
                          _l("中英混合", "Chinese / English mix"), _l("其他或无文本", "Other / no text"),
                          _l("无用户发言", "No user utterance"))))
    left, right = st.columns(2, gap="medium")
    with left:
        st.html(_bars(_l("正文语言", "Body language"), row["languages"], languages))
        st.html(_bars(_l("正文长度 · 字符", "Body length · characters"), row["lengths"]))
        st.html(_bars(_l("用户发言语言", "User utterance language"), row["user_languages"], languages))
    with right:
        turn_labels = {"0": _l("无用户发言", "No user utterance"), "1": _l("1 轮", "1 turn"),
                       "2": _l("2 轮", "2 turns"), "3–5": _l("3–5 轮", "3–5 turns"),
                       "6+": _l("6 轮及以上", "6+ turns")}
        st.html(_bars(_l("用户发言轮数", "User turns per record"), row["turns"], turn_labels))
        st.html(_bars(_l("推理长度 · 仅含推理样本", "Reasoning length · records with reasoning"), row["reasoning_lengths"]))
        st.html('<section class="df-dist-panel"><h3>' + _l("统计口径", "How to read these counts")
                + '</h3><p class="df-dist-note">' + _l(
                    "正文仅含训练题面和回答，不计系统提示、工具返回或独立推理字段。轮数按用户发言计，不等于推理步骤。语言按汉字与拉丁字母占比估算；字符数不是 token 数，也不是质量评分。",
                    "Body text includes task inputs and answers. It excludes system prompts, tool results and separate reasoning. Turns count user utterances, not reasoning steps. Language uses Han and Latin character shares. Character counts are not tokens or quality scores.") + '</p></section>')


@st.fragment(run_every="1s")
def _scan_panel(application, run_id, workspace_id):
    try:
        payload = application.sample_statistics(run_id)
        result, job = payload["statistics"], payload["job"]
        if result:
            st.rerun()  # Completed reports render without an ongoing polling timer.
            return
        if not job or job["status"] == "completed":
            job = application.start_sample_statistics(run_id)
        if job["status"] in {"queued", "running"}:
            done, total = job.get("done", 0), job.get("total", 0)
            st.progress(min(done / total, 1) if total else 0,
                        text=_l(f"后台统计中 · {done:,} / {total:,} 条", f"Scanning in background · {done:,} / {total:,} records")
                        if total else _l("正在校验并准备统计，可继续切换页面。", "Verifying files. You can keep using other pages."))
            st.button(_l("停止统计", "Stop statistics"), on_click=application.cancel_sample_statistics,
                      args=(run_id,), key=f"sample-stat-cancel:{workspace_id}:{run_id}")
        else:
            st.warning(_l("统计未完成，训练文件保持原样。", "Statistics did not finish. Training files are unchanged."))
            if job.get("error"):
                st.code(job["error"], language=None)
            st.button(_l("重新统计", "Retry statistics"), on_click=application.start_sample_statistics,
                      args=(run_id,), key=f"sample-stat-retry:{workspace_id}:{run_id}")
    except (OSError, ValueError, KeyError, TypeError) as error:
        st.error(_l("无法读取已校验的样本统计：", "Cannot read verified statistics: ") + str(error))


def render_sample_statistics(application, workspace_id):
    st.html(STYLE)
    runs = [row for row in application.task_runs() if has_deliverable_results(row) and row.get("id")]
    if not runs:
        st.info(_l("生成训练数据后，即可查看样本分布与实际导出大小。", "Create training data to see sample distributions and actual export sizes."))
        return
    by_id = {row["id"]: row for row in runs}
    key = f"sample-stat-run:{workspace_id}"
    if st.session_state.get(key) not in by_id:
        st.session_state.pop(key, None)
    run_id = st.selectbox(_l("选择工作流产物", "Choose workflow outputs"), list(by_id), key=key,
                          format_func=lambda value: UntranslatedText(f"{by_id[value].get('name', value)} · {value[:8]}"))
    try:
        payload = application.sample_statistics(run_id)
        manifest = payload["manifest"]
        if not delivery_manifest_matches(by_id[run_id], manifest):
            raise ValueError("artifact_integrity_error")
    except (OSError, ValueError, KeyError, TypeError) as error:
        st.error(_l("无法读取已校验的样本统计：", "Cannot read verified statistics: ") + str(error))
        return
    files = [row for row in payload["files"] if row["name"] in manifest.get("sha256", {})]
    counts = {target: int((manifest.get("counts") or {}).get(target, 0)) for target in TARGETS
              if target in (manifest.get("counts") or {})}
    native = sum(row["bytes"] for row in files if row["name"] in {f"{t}.jsonl" for t in TARGETS})
    all_bytes = sum(row["bytes"] for row in files) + payload.get("manifest_bytes", 0)
    bundle = payload.get("bundle")
    st.html('<div class="df-dist-intro"><div><strong>' + _l("看清数据组成，再决定如何训练", "Know the data before you train")
            + '</strong><p>' + _l("仅统计所选任务的原生训练记录；格式转换不增加样本量，历史任务不自动合并。",
                                "Counts cover native records in this run. Converted formats add no samples. Historical runs stay separate.")
            + '</p></div><div class="df-dist-orbit" aria-hidden="true">◈</div></div>')
    cards = (
        (_l("原生样本", "Native records"), f'{sum(counts.values()):,}', _l("按训练目标计数，非跨目标去重", "Per target; not deduplicated across targets")),
        (_l("原生训练文件", "Native training files"), size_label(native), _l("不含格式副本与审计附件", "Excludes conversions and audit files")),
        (_l("全部导出文件", "All export files"), size_label(all_bytes), _l("含资源、审计与清单，不含 ZIP", "Includes assets, audit and manifest; no ZIP")),
        (_l("实际 ZIP 大小", "Actual ZIP size"), size_label(bundle["bytes"]) if bundle else _l("尚未生成", "Not built"),
         _l("压缩完成后显示，不估算", "Measured after compression; never estimated")),
    )
    st.html('<div class="df-dist-metrics">' + ''.join('<div><span>' + _safe(label) + '</span><strong>'
            + _safe(value) + '</strong><small>' + _safe(note) + '</small></div>' for label, value, note in cards) + '</div>')
    left, right = st.columns([1, 1.4], gap="medium")
    with left:
        st.html(_bars(_l("训练目标分布", "Training target distribution"), counts,
                      {k: _target_label(k) for k in counts}))
        negative = sum((manifest.get("negative_counts") or {}).values())
        if negative:
            st.caption(_l(f"另有 {negative:,} 条失败轨迹单独统计，不计入正例总数。",
                          f"{negative:,} failure traces are counted separately from positive records."))
    with right:
        st.markdown('**' + _l("导出文件大小", "Export file sizes") + '**')
        rows = [{_l("文件", "File"): row["name"], _l("大小", "Size"): size_label(row["bytes"]),
                 _l("字节", "Bytes"): row["bytes"]} for row in files]
        rows.append({_l("文件", "File"): "manifest.json", _l("大小", "Size"): size_label(payload.get("manifest_bytes", 0)),
                     _l("字节", "Bytes"): payload.get("manifest_bytes", 0)})
        st.dataframe(rows, hide_index=True, width="stretch", height=min(300, 38 + len(rows) * 35))
    st.caption(_l("详细统计逐条读取并缓存，资料不离开本机；不会触发生成或模型 API。数据版本变化后自动失效。",
                  "Detailed counts are scanned and cached locally. No generation or model API is used. Data changes invalidate the cache."))
    if result := payload.get("statistics"):
        _render_result(result, f"sample-stat-scope:{workspace_id}:{run_id}:{result['manifest_digest']}")
        report = {**result, "files": files, "manifest_bytes": payload.get("manifest_bytes", 0),
                  "zip_bytes": (bundle or {}).get("bytes")}
        st.download_button(_l("下载统计 JSON", "Download statistics JSON"),
                           json.dumps(report, ensure_ascii=False, indent=2),
                           file_name=f"statistics-{run_id[:8]}.json", mime="application/json",
                           key=f"sample-stat-download:{workspace_id}:{run_id}", on_click="ignore")
    else:
        _scan_panel(application, run_id, workspace_id)
