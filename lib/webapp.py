"""统一运营控制台（Streamlit 多页，无 Docker）。

页面：总览 / 数据预览 / 管线运行 / 人工审核 / 监控 / 模型与闸门 / 偏好设置。
整合原则：所有操作复用 lib 现有逻辑（管线=CLI 子进程、审核=review 流程、
渲染=lib.render 样式、闸门=GateKeeper）；Argilla 作为多人协作审核专页保留，
控制台内提供链接。启动：df console（即 streamlit run lib/webapp.py，端口 8501）。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import threading

# 本机服务探测必须绕代理（控制台是独立进程，不继承 CLI 的环境设置）；
# 否则系统代理开启时健康检查会把 localhost 请求劫走导致误报 ❌
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from lib.render import _CSS, _render_message  # noqa: E402

OUT_DIR = ROOT / "data" / "output"
SAMPLES_PATH = OUT_DIR / "combined_preview.jsonl"
REPORT_PATH = OUT_DIR / "distill_report.json"
REVIEW_PATH = OUT_DIR / "review.jsonl"
RUNS_PATH = OUT_DIR / "runs.jsonl"
BUDGET_PATH = OUT_DIR / "budget.json"

st.set_page_config(page_title="DataForge 运营控制台", layout="wide")
st.html(f"<style>{_CSS} body {{ max-width: none; padding: 12px; }}</style>")

# ── 通用数据加载 ────────────────────────────────────────────────────────────
def _load_samples() -> list[dict]:
    if not SAMPLES_PATH.exists():
        return []
    return [json.loads(l) for l in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]


def _gate() -> object:
    from lib.gates import GateKeeper

    return GateKeeper(ROOT / "configs" / "gates.yaml", OUT_DIR / "gates_state.json")


def _service_health() -> dict[str, bool]:
    import urllib.request

    checks = {
        "Argilla 审核平台 (6900)": "http://127.0.0.1:6900/api/v1/version",
        "Streamlit 控制台 (8501)": "http://127.0.0.1:8501/",
        "静态预览 (18700)": "http://127.0.0.1:18700/preview.html",
        "Elasticsearch (9200)": "http://127.0.0.1:9200/",
    }
    result = {}
    for name, url in checks.items():
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                result[name] = 100 <= r.status < 400
        except Exception:  # noqa: BLE001
            result[name] = False
    try:
        import redis

        result["Redis (6379)"] = bool(redis.Redis(host="127.0.0.1", port=6379).ping())
    except Exception:  # noqa: BLE001
        result["Redis (6379)"] = False
    return result


def _inventory() -> dict[str, int]:
    counts = {}
    for path in sorted(OUT_DIR.glob("*.jsonl")):
        name = path.name
        try:
            n = sum(1 for _ in open(path, encoding="utf-8"))
        except OSError:
            continue
        if n:
            counts[name] = n
    return counts


# ── 页面：总览 ──────────────────────────────────────────────────────────────
def page_overview() -> None:
    st.title("总览")
    st.caption("服务与数据资产一览；其他页面进行具体操作。")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**服务健康**")
        for name, ok in _service_health().items():
            st.write(("✅" if ok else "❌") + " " + name)
    with c2:
        st.markdown("**数据资产（data/output）**")
        inv = _inventory()
        for name, n in sorted(inv.items(), key=lambda x: x[1], reverse=True):
            st.write(f"· {name}: {n} 行")
        st.caption("分组/搜索/下载 → 「资产管理」页面")
    budget = ""
    if BUDGET_PATH.exists():
        budget = BUDGET_PATH.read_text(encoding="utf-8")
    st.markdown("**预算**")
    st.code(budget or "（无预算记录）")
    gate = _gate()
    st.markdown("**闸门**")
    for gid, g in gate.defs.items():
        st.write(f"- {gid} [{gate.status(gid)}] {g.title}")
    st.markdown("**快捷入口**")
    st.write("[Argilla 多人协作审核](http://127.0.0.1:6900) ｜ [静态预览页](http://127.0.0.1:18700/preview.html)")


# ── 页面：数据预览 ──────────────────────────────────────────────────────────
def page_preview() -> None:
    st.title("数据预览")
    samples = _load_samples()
    if not samples:
        st.warning("暂无合并样本：先运行 df import / 各生成命令，或执行合并脚本")
        return
    sources = sorted({s.get("source", "?") for s in samples})
    picked = st.multiselect("按来源过滤", sources, default=[])
    filtered = [s for s in samples if not picked or s.get("source") in picked]
    n = st.slider("展示条数", 1, min(50, max(len(filtered), 1)), min(10, max(len(filtered), 1)))
    for s in filtered[:n]:
        msgs = s.get("messages", [])
        if len(msgs) > 60:
            msgs = msgs[-59:] + [msgs[-1]]
        badges = (
            f'<span class="badge b-model">{s.get("source", "?")}</span>'
            f'<span class="badge b-finish">msgs: {len(s.get("messages", []))}</span>'
        )
        body = "\n".join(_render_message(m) for m in msgs)
        st.html(f'<div class="card"><div class="meta">{badges}</div>{body}</div>')


# ── 页面：管线运行 ──────────────────────────────────────────────────────────
def _command_meta() -> dict:
    """从命令注册表（单一事实源）加载；与 CLI 实际子命令交叉校验，防漂移。"""
    import re
    import subprocess as _sp

    from lib.extensions import load_commands

    cmds = load_commands(ROOT)
    df_help = _sp.run(
        [sys.executable, "-m", "lib.cli", "-h"], cwd=str(ROOT),
        capture_output=True, text=True, timeout=60,
    ).stdout
    block = df_help.split("usage:", 1)[-1].split(chr(10) * 2, 1)[0]
    cli_commands = set(re.findall(r"(?<![a-z0-9])[a-z][a-z0-9-]+(?![a-z0-9])", block)) - {"df", "h"}
    actual = {c for c in cli_commands if c in cmds}
    unknown = set(cmds) - cli_commands
    if unknown:
        st.session_state["cmd_drift"] = f"注册表存在但 CLI 未注册：{sorted(unknown)}"
    return cmds


def _g0_commands(cmds) -> set:
    return {n for n, m in cmds.items() if m.get("g0")}


def page_run() -> None:
    st.title("管线运行")
    cmds = _command_meta()
    if st.session_state.get("cmd_drift"):
        st.warning(st.session_state["cmd_drift"])
    cmd = st.selectbox("命令", list(cmds.keys()), format_func=lambda c: f"{c} — {cmds[c]['help']}")
    st.caption(cmds[cmd]["help"])
    gate = _gate()
    gate_hint = []
    if cmd in _g0_commands(cmds) and gate.status("G0") != "approved":
        gate_hint.append("⚠ 此命令调用付费 API，需 G0 闸门通过")
    if cmd == "import" and gate.status("G1") != "approved":
        gate_hint.append("⚠ 导入私有数据，需 G1 闸门通过")
    if cmd == "export" and gate.status("G3") != "approved":
        gate_hint.append("⚠ 放量导出需 G3 闸门通过（可先 --bulk 不加，或先过门）")
    for h in gate_hint:
        st.warning(h)

    options_text = st.text_input("命令参数（键值对，每行一个；布尔值只写键）", "{\n}")
    run_clicked = st.button("▶ 运行", type="primary")

    if run_clicked:
        argv = [sys.executable, "-m", "lib.cli", cmd]
        try:
            opts = json.loads(options_text or "{}")
        except json.JSONDecodeError:
            st.error("参数不是合法 JSON")
            return
        for k, v in opts.items():
            argv.append(f"--{k}")
            if v not in (True, False, None):
                argv.append(str(v))
        st.session_state["run_argv"] = argv

    if st.session_state.get("run_argv"):
        argv = st.session_state["run_argv"]
        log_box = st.empty()

        def _stream() -> None:
            import os

            env = {**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
            proc = subprocess.Popen(
                argv, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env,
            )
            lines: list[str] = []
            for line in iter(proc.stdout.readline, ""):
                lines.append(line.rstrip())
                log_box.code("\n".join(lines[-40:]))
            proc.wait()
            st.session_state["run_exit"] = proc.returncode
            st.session_state["run_log"] = lines

        if not st.session_state.get("run_done"):
            thread = threading.Thread(target=_stream, daemon=True)
            thread.start()
            st.session_state["run_done"] = True
        if "run_exit" in st.session_state:
            mark = "✅ 完成" if st.session_state["run_exit"] == 0 else f"❌ 退出码 {st.session_state['run_exit']}"
            st.write(mark + f"（{' '.join(argv[3:])[:60]}）")
            if st.button("再次运行"):
                for k in ("run_argv", "run_exit", "run_log", "run_done"):
                    st.session_state.pop(k, None)
                st.rerun()


# ── 页面：人工审核 ──────────────────────────────────────────────────────────
def page_review() -> None:
    st.title("人工审核（HITL）")
    samples = _load_samples()
    decided = {d["sample_id"] for d in _load_decisions()}
    pending = [s for s in samples if s["id"] not in decided]
    st.caption(f"共 {len(samples)} 条，已审 {len(samples) - len(pending)} 条，待审 {len(pending)} 条")
    st.info("多人协作/大规模审核请用 [Argilla](http://127.0.0.1:6900)（admin / distill123456）；本页为轻量单人审核。")
    if not pending:
        st.success("全部样本已审核。")
        return
    idx = st.session_state.get("review_idx", 0) % len(pending)
    sample = pending[idx]
    msgs = sample.get("messages", [])
    if len(msgs) > 60:
        msgs = msgs[-59:] + [msgs[-1]]
    body = "\n".join(_render_message(m) for m in msgs)
    st.html(f'<div class="card"><div class="meta">'
            f'<span class="badge b-model">{sample.get("source", "?")}</span></div>{body}</div>')
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
        st.progress(keeps / len(decisions))
        st.caption(f"通过率 {keeps / len(decisions):.0%}（keep {keeps} / reject {len(decisions) - keeps}）")
        if len(decisions) >= 10 and keeps / len(decisions) >= 0.9:
            if st.button("✔ 按当前通过率放行 G3"):
                _gate().decide("G3", True, note=f"控制台审核通过率 {keeps / len(decisions):.0%}")
                st.success("G3 已放行，可 df export --bulk")


def _load_decisions() -> list[dict]:
    if not REVIEW_PATH.exists():
        return []
    return [json.loads(l) for l in REVIEW_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]


def _append_decision(sample_id: str, decision: str) -> None:
    with open(REVIEW_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"sample_id": sample_id, "decision": decision}, ensure_ascii=False) + "\n")


# ── 页面：监控 ──────────────────────────────────────────────────────────────
def page_monitor() -> None:
    st.title("运行监控")
    runs: list[dict] = []
    if RUNS_PATH.exists():
        runs = [json.loads(l) for l in RUNS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    st.caption(f"本地运行审计共 {len(runs)} 条（Langfuse 未配置时的兜底记录）")
    if runs:
        rows = []
        for r in runs[-50:]:
            usage = r.get("usage") or {}
            rows.append({
                "时间": str(r.get("at", ""))[11:19], "类型": r.get("kind", ""),
                "调用数": usage.get("calls", ""), "prompt": usage.get("prompt_tokens", ""),
                "completion": usage.get("completion_tokens", ""),
            })
        st.dataframe(rows, use_container_width=True)
    if BUDGET_PATH.exists():
        st.markdown("**预算**")
        st.code(BUDGET_PATH.read_text(encoding="utf-8"))


# ── 页面：模型与闸门 ────────────────────────────────────────────────────────
def page_gates() -> None:
    st.title("模型与闸门")
    gate = _gate()
    st.markdown("**闸门状态（HITL）**")
    for gid, g in gate.defs.items():
        c1, c2 = st.columns([3, 1])
        with c1:
            st.write(f"{gid} [{gate.status(gid)}] {g.title} — {g.trigger}")
        with c2:
            if gate.status(gid) != "approved" and st.button(f"通过 {gid}", key=f"appr_{gid}"):
                gate.decide(gid, True, note="控制台确认")
                st.rerun()
    st.markdown("**可用模型（models 网关）**")
    if st.button("刷新模型列表"):
        st.session_state.pop("models_cache", None)
    if "models_cache" not in st.session_state:
        import yaml

        local_cfg = yaml.safe_load((ROOT / "configs" / "backends.local.yaml").read_text(encoding="utf-8"))
        b = local_cfg.get("backends", {}).get("deepseek", {})
        try:
            from openai import OpenAI

            client = OpenAI(base_url=b.get("base_url"), api_key=b.get("api_key"))
            st.session_state["models_cache"] = sorted(m.id for m in client.models.list().data)
        except Exception as e:  # noqa: BLE001
            st.session_state["models_cache"] = [f"网关不可用: {str(e)[:80]}"]
    st.write("\n".join("· " + m for m in st.session_state["models_cache"]))
    st.markdown("**角色槽位（configs/backends.yaml → model_roles）**")
    st.code("generation/judge/vision/refine/simulate/translation — 各配后端与模型；"
            "单次运行可 --backend/--model/环境变量 LLM_MODEL 覆盖")



def _asset_categories() -> dict:
    """按文件名模式分类资产。"""
    rules = [
        ("样本", lambda n: ("samples.jsonl" in n) or "combined_preview" in n),
        ("DPO 偏好对", lambda n: n.startswith("dpo") or "stylefix_dpo" in n or "cot_style_dpo" in n),
        ("语料", lambda n: "corpus" in n and n.endswith(".jsonl")),
        ("报告与状态", lambda n: n.endswith(".json") or n.endswith(".txt") or n == "runs.jsonl"),
    ]
    cats: dict[str, dict] = {}
    for path in sorted(OUT_DIR.glob("*")):
        if not path.is_file():
            continue
        cat = next((c for c, fn in rules if fn(path.name)), "其他")
        try:
            n_rows = sum(1 for _ in open(path, encoding="utf-8")) if not path.name.endswith(".txt") else len(path.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeDecodeError):
            n_rows = -1
        size = path.stat().st_size
        cats.setdefault(cat, {})[path.name] = {"rows": n_rows, "size": size}
    return cats


def page_assets() -> None:
    st.title("资产管理")
    st.caption("全部产物按类别分组；搜索过滤；每项可下载。输入侧资产（rollout 原始记录/图片）不在 data/output，见 data/seeds。")
    search = st.text_input("搜索文件名", "")
    cats = _asset_categories()
    total = sum(len(files) for files in cats.values())
    st.write(f"共 {total} 个资产文件")
    for cat, files in cats.items():
        with st.expander(f"{cat}（{len(files)}）", expanded=cat == "样本"):
            for name, meta in sorted(files.items()):
                if search and search.lower() not in name.lower():
                    continue
                c1, c2, c3 = st.columns([4, 2, 1])
                c1.write(f"· {name}")
                c2.caption(f"{meta['rows']} 行 / {meta['size'] / 1024:.0f} KB" if meta['rows'] >= 0 else f"{meta['size'] / 1024:.0f} KB")
                c3.download_button("下载", open(OUT_DIR / name, "rb").read(), file_name=name,
                                   key=f"dl_{name}", use_container_width=True)


# ── 页面：偏好设置 ──────────────────────────────────────────────────────────
PREF_FILES = {
    "生成偏好（preferences.yaml）": ROOT / "configs" / "preferences.yaml",
    "CoT 风格（cot_styles.yaml）": ROOT / "configs" / "cot_styles.yaml",
    "风格矫正规则（style_rules.example.yaml）": ROOT / "configs" / "style_rules.example.yaml",
}


def page_prefs() -> None:
    st.title("偏好设置")
    st.caption("直接编辑 YAML 并保存（保存前自动备份为 .bak）")
    name = st.selectbox("配置文件", list(PREF_FILES.keys()))
    path = PREF_FILES[name]
    text = st.text_area(name, path.read_text(encoding="utf-8"), height=320)
    if st.button("保存", type="primary"):
        try:
            import yaml as _y

            _y.safe_load(text)  # 校验
            path.with_name(path.name + ".bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            path.write_text(text, encoding="utf-8")
            st.success(f"已保存（备份 {path.name}.bak）")
        except Exception as e:  # noqa: BLE001
            st.error(f"YAML 校验失败，未保存：{e}")


PAGES = {
    "总览": page_overview,
    "资产管理": page_assets,
    "数据预览": page_preview,
    "管线运行": page_run,
    "人工审核": page_review,
    "监控": page_monitor,
    "模型与闸门": page_gates,
    "偏好设置": page_prefs,
}

page = st.sidebar.radio("DataForge 控制台", list(PAGES.keys()))
PAGES[page]()
