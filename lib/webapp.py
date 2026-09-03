"""本地轻量审核+监控应用（Streamlit，无 Docker）。

替代 Argilla + Langfuse 的轻量路径：
  审核页：逐条展示样本（复用 lib.render 的渲染样式），保留/驳回/跳过，
          决定写入 data/output/review.jsonl，通过率达标自动放行 G3 闸门。
  监控页：runs.jsonl 运行审计（kind/时间/token/统计）+ manifest 数量 + 闸门状态。

启动：df review app   （即 streamlit run lib/webapp.py，http://localhost:8501）
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from lib.render import _CSS, _render_message  # noqa: E402

OUT_DIR = ROOT / "data" / "output"
SAMPLES_PATH = OUT_DIR / "rollout_samples.jsonl"
REPORT_PATH = OUT_DIR / "distill_report.json"
REVIEW_PATH = OUT_DIR / "review.jsonl"
RUNS_PATH = OUT_DIR / "runs.jsonl"

st.set_page_config(page_title="数据审核与监控 — Super-LLM-distill-Gen", layout="wide")
st.html(f"<style>{_CSS} body {{ max-width: none; padding: 12px; }}</style>")


def _load_samples() -> list[dict]:
    if not SAMPLES_PATH.exists():
        return []
    return [json.loads(l) for l in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]


def _load_scores() -> dict[str, str]:
    if not REPORT_PATH.exists():
        return {}
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    return {s.get("id"): s.get("score", "") for s in report.get("llm_scores", [])}


def _load_decisions() -> list[dict]:
    if not REVIEW_PATH.exists():
        return []
    return [json.loads(l) for l in REVIEW_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]


def _append_decision(sample_id: str, decision: str) -> None:
    with open(REVIEW_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"sample_id": sample_id, "decision": decision}, ensure_ascii=False) + "\n")


def _render_sample_html(sample: dict, score: str | None) -> str:
    msgs = sample.get("messages", [])
    if len(msgs) > 60:
        msgs = msgs[-59:] + [msgs[-1]]
    # 注意：不能对拼接后的字符串做 " ".join（会按字符拆开）；直接字符串相加
    badges = (
        f'<span class="badge b-model">{sample.get("model", "?")}</span>'
        f'<span class="badge b-finish">finish: {sample.get("finish_reason", "?")}</span>'
        f'<span class="badge">msgs: {len(sample.get("messages", []))}</span>'
        + (f'<span class="badge b-err">错误步骤: {sample.get("error_tool_steps", 0)}</span>'
           if sample.get("error_tool_steps") else "")
        + (f'<span class="badge b-score">{score[:80]}</span>' if score else "")
    )
    body = "\n".join(_render_message(m) for m in msgs)
    return f'<div class="card"><div class="meta">{badges}</div>{body}</div>'


def review_page() -> None:
    st.title("数据审核（HITL）")
    samples = _load_samples()
    scores = _load_scores()
    decided = {d["sample_id"] for d in _load_decisions()}
    if not samples:
        st.warning("暂无样本：先运行 df import")
        return

    pending = [s for s in samples if s["id"] not in decided]
    st.caption(f"共 {len(samples)} 条，已审 {len(samples) - len(pending)} 条，待审 {len(pending)} 条")

    if not pending:
        st.success("全部样本已审核。")
    else:
        idx = st.session_state.get("review_idx", 0) % len(pending)
        sample = pending[idx]
        st.html(_render_sample_html(sample, scores.get(sample["id"])))

        col_keep, col_reject, col_skip = st.columns([1, 1, 1])
        if col_keep.button("✅ 保留", use_container_width=True):
            _append_decision(sample["id"], "keep")
            st.session_state["review_idx"] = idx + 1
            st.rerun()
        if col_reject.button("❌ 驳回", use_container_width=True):
            _append_decision(sample["id"], "reject")
            st.session_state["review_idx"] = idx + 1
            st.rerun()
        if col_skip.button("⏭ 跳过", use_container_width=True):
            st.session_state["review_idx"] = idx + 1
            st.rerun()

    decisions = _load_decisions()
    if decisions:
        keeps = sum(1 for d in decisions if d["decision"] == "keep")
        rate = keeps / len(decisions)
        st.progress(rate)
        st.caption(f"通过率 {rate:.0%}（keep {keeps} / reject {len(decisions) - keeps}）")
        if len(decisions) >= 10 and rate >= 0.9:
            from lib.gates import GateKeeper

            gate = GateKeeper(ROOT / "configs" / "gates.yaml", OUT_DIR / "gates_state.json")
            if st.button("✔ 按当前通过率放行 G3（小批量放量闸）"):
                gate.decide("G3", True, note=f"本地审核通过率 {rate:.0%}")
                st.success("G3 已放行，可执行 df export --bulk")
            elif gate.status("G3") == "approved":
                st.success("G3 已放行")


def monitor_page() -> None:
    st.title("运行监控")
    runs: list[dict] = []
    if RUNS_PATH.exists():
        runs = [json.loads(l) for l in RUNS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    st.caption(f"本地运行审计共 {len(runs)} 条（Langfuse 未配置时为兜底记录）")

    if runs:
        rows = []
        for r in runs[-50:]:
            usage = r.get("usage") or {}
            rows.append({
                "时间": str(r.get("at", ""))[11:19],
                "类型": r.get("kind", ""),
                "调用数": usage.get("calls", ""),
                "prompt tokens": usage.get("prompt_tokens", ""),
                "completion tokens": usage.get("completion_tokens", ""),
            })
        st.dataframe(rows, use_container_width=True)

    manifest = OUT_DIR / "manifest_rollout.txt"
    dpo = OUT_DIR / "dpo_pairs.jsonl"
    stats = {}
    if manifest.exists():
        stats["manifest 样本数"] = len(manifest.read_text(encoding="utf-8").splitlines())
    if dpo.exists():
        stats["DPO 负样本对"] = len(dpo.read_text(encoding="utf-8").splitlines())
    st.json(stats)

    from lib.gates import GateKeeper

    gate = GateKeeper(ROOT / "configs" / "gates.yaml", OUT_DIR / "gates_state.json")
    st.markdown("**闸门状态**")
    for gid, g in gate.defs.items():
        st.write(f"- {gid} [{gate.status(gid)}] {g.title}")


tab = st.sidebar.radio("页面", ["审核", "监控"])
if tab == "审核":
    review_page()
else:
    monitor_page()
