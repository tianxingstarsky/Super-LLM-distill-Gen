"""Chinese operations console: session-isolated workspaces and schema-driven forms."""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import streamlit as st
from lib import workspace as WS
from lib.render import _CSS, _render_message
from lib.console_jobs import Job

st.set_page_config(page_title="DataForge 运营控制台", layout="wide")
st.html(f"<style>{_CSS} body {{max-width:none}} .stMarkdown p {{font-size:17px}}</style>")


def _ws_out():
    return WS.out(st.session_state.get("ws", "default"))


def _OUT(name):
    return _ws_out() / name


def _ws_choice():
    options = WS.list_all()
    current = st.session_state.get("ws", WS.current())
    st.sidebar.selectbox("工作区", options, index=options.index(current) if current in options else 0, key="ws")
    with st.sidebar.expander("新建工作区"):
        with st.form("new-workspace"):
            name = st.text_input("工作区名称")
            create = st.form_submit_button("创建")
        if create:
            try:
                WS.out(WS.validate(name))
                st.success("已创建")
                st.rerun()
            except ValueError as error:
                st.error(str(error))


def _sample_files():
    return sorted(p for p in _ws_out().glob("*.jsonl") if "samples" in p.name or "combined_preview" in p.name)


def _selected_samples(key):
    files = _sample_files()
    if not files:
        st.info("当前工作区暂无样本")
        return None, []
    source = st.selectbox("样本文件", files, format_func=lambda p: p.name, key=key)
    from lib.quality import read_samples
    return source, read_samples(source)


def _gate():
    from lib.gates import GateKeeper
    return GateKeeper(ROOT / "configs/gates.yaml", _OUT("gates_state.json"))


def _command_meta():
    from lib.extensions import load_commands
    return load_commands(ROOT)


def _begin(command):
    ws = st.session_state["ws"]
    key = "job:" + ws
    existing = st.session_state.get(key)
    if existing and existing.snapshot()[0] is None:
        st.warning("当前工作区已有运行任务")
        return
    job = Job(command, ws, ROOT)
    st.session_state[key] = job
    job.start()


@st.fragment(run_every=1)
def _job_status():
    job = st.session_state.get("job:" + st.session_state["ws"])
    if not job:
        return
    code, lines = job.snapshot()
    if code is None:
        st.info("任务运行中")
    elif code == 0:
        st.success("任务完成")
    else:
        st.error(f"任务未完成，退出码 {code}")
    st.code("\n".join(lines[-80:]) or "等待输出", language="text")


def page_overview():
    st.title("总览")
    st.write(f"工作区：**{st.session_state['ws']}**")
    files = _sample_files()
    left, middle, right = st.columns(3)
    left.metric("样本文件", len(files))
    middle.metric("导出版本", len(list(_OUT("export").glob("*/manifest.json"))))
    right.metric("G3 放量确认", _gate().status("G3"))
    from lib.doctor import checks
    with st.expander("环境诊断"):
        st.dataframe(checks(), hide_index=True, use_container_width=True)
    _job_status()


def page_preview():
    st.title("数据预览")
    _, samples = _selected_samples("preview-file")
    if not samples:
        return
    index = st.number_input("样本序号", 1, len(samples), 1)
    sample = samples[index - 1]
    st.caption(f"ID: {sample.get('id', index)} / {len(sample.get('messages', []))} 条消息")
    for message in sample.get("messages", []):
        st.html(_render_message(message))


def page_run():
    st.title("管线运行")
    presets = {
        "minimind 兼容草稿导出": ("export", {"format": "minimind", "bulk": False}),
        "文档语料整理": ("doc2corpus", {}),
        "文档问答生成": ("doc2data", {}),
        "质量报告": ("quality-report", {}),
        "AI 审核小队": ("dsh", {"team": True, "task": "先读取 review-team 技能，依据用户选定的工作区与审核配置派发子智能体。未提供配置先报告缺项，不创建账号、不自行放行。"}),
        "协作者拉取": ("review-remote", {"action": "pull"}),
        "环境自检": ("doctor", {}),
        "全部命令": (None, {}),
    }
    preset = st.selectbox("任务", list(presets))
    command, defaults = presets[preset]
    from lib.cli import build_parser
    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices
    if command is None:
        available = [c for c in _command_meta() if c not in ("review-server", "user")]
        command = st.selectbox("命令", available)
    action_parser = sub[command]
    keybase = f"form:{st.session_state['ws']}:{preset}:{command}"
    values = []
    with st.form(keybase):
        columns = st.columns(2)
        for index, action in enumerate(a for a in action_parser._actions if a.dest not in ("help", "ws")):
            default = defaults.get(action.dest, action.default)
            label = action.dest.replace("_", " ")
            labels = {"input": "输入路径", "out": "输出目录", "format": "训练格式", "bulk": "放量导出（需审核）", "tag": "版本标签", "model": "模型", "backend": "模型后端", "action": "操作", "config": "审核配置路径", "batch": "每批条数", "task": "任务要求", "team": "启用子智能体团队"}
            label = labels.get(action.dest, label)
            with columns[index % 2]:
                key = keybase + ":" + action.dest
                if action.dest == "review_config":
                    candidates = sorted(str(p) for p in (ROOT / "configs").glob("review_remote*.yaml") if "example" not in p.name)
                    selected = st.multiselect("审核配置", candidates, format_func=lambda p: Path(p).name, key=key)
                    value = []
                    for filename in selected:
                        import yaml
                        config = yaml.safe_load(Path(filename).read_text(encoding="utf-8")) or {}
                        value.append(config.get("policy", "safety" if "safety" in filename else "quality") + "=" + filename)
                elif isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
                    value = st.checkbox(label, value=bool(default), key=key)
                elif action.choices:
                    choices = list(action.choices)
                    if command == "review-remote" and action.dest == "action":
                        choices = [c for c in choices if c not in ("setup", "human")]
                    if command == "review" and action.dest == "action":
                        choices = [c for c in choices if c != "app"]
                    value = st.selectbox(label, choices, index=choices.index(default) if default in choices else 0, key=key)
                elif action.type is int and default is not None:
                    value = st.number_input(label, min_value=0, value=int(default), step=1, key=key)
                else:
                    value = st.text_input(label, value=str(default or ""), key=key)
                values.append((action, value))
        submit = st.form_submit_button("运行", type="primary")
    if submit:
        argv = [command]
        for action, value in values:
            if isinstance(action, argparse._AppendAction):
                for entry in value:
                    argv.extend([action.option_strings[0], str(entry)])
            elif isinstance(action, argparse._StoreTrueAction):
                if value:
                    argv.append(action.option_strings[0])
            elif value not in (None, ""):
                if action.option_strings:
                    argv.append(action.option_strings[0])
                argv.append(str(value))
        _begin(argv)
    _job_status()


def page_review():
    st.title("人工审核")
    with st.expander("协作者接入配置"):
        with st.form("collaborator-setup"):
            config_name = st.text_input("配置名称", "review_remote.local")
            server = st.text_input("中心地址", "http://127.0.0.1:6900")
            key = st.text_input("API 密钥", type="password")
            model = st.text_input("评审模型（可留空）")
            policy = st.selectbox("审核规则", ["quality", "safety"])
            save = st.form_submit_button("验证并保存配置")
        if save:
            import re
            from lib.review_remote import AuthClient
            if not re.fullmatch(r"review_remote\.[a-zA-Z0-9_-]+", config_name):
                st.error("名称格式：review_remote.自定义名称")
            else:
                target = ROOT / "configs" / (config_name + ".yaml")
                try:
                    client = AuthClient(server, key)
                    import yaml
                    with target.open("x", encoding="utf-8") as handle:
                        yaml.safe_dump({"server": server, "api_key": key, "model": model,
                            "policy": policy, "dataset": WS.dataset_name(st.session_state["ws"])}, handle, allow_unicode=True)
                    st.success(f"已连接账号 {client.me['username']}，保存为 {target.name}")
                except (OSError, ValueError, ConnectionError) as error:
                    st.error(str(error))
    from lib import review_center as rc
    dataset = WS.dataset_name(st.session_state["ws"])
    rc.ensure_admin()
    _, samples = _selected_samples("review-source")
    if samples and st.button("将所选样本加入待审"):
        from lib.review import push_samples
        try:
            push_samples(samples, {}, dataset_name=dataset)
            st.success("样本已加入待审")
        except ValueError as error:
            st.error(str(error))
    rows = rc.pending(dataset, "admin", 100)
    if not rows:
        st.info("当前工作区没有待审记录")
    else:
        row = st.selectbox("待审样本", rows, format_func=lambda r: r["sample_id"])
        st.markdown(row["instruction"])
        st.text(row["conversation"])
        with st.form("review-decision:" + str(row["record_id"])):
            decision = st.radio("判定", ["keep", "reject"], format_func=lambda v: "保留" if v == "keep" else "驳回", horizontal=True)
            reason = st.text_area("判定理由")
            send = st.form_submit_button("提交审核")
        if send:
            try:
                rc.submit(dataset, "admin", [{**row, "decision": decision, "reason": reason, "model": "human"}])
                st.rerun()
            except ValueError as error:
                st.error(str(error))
    from lib.review import decide_gate
    summary = decide_gate(rc.responses(dataset))
    st.dataframe([summary], hide_index=True)


def page_quality():
    st.title("质量报告")
    from lib.quality import report
    from lib import review_center as rc
    _, samples = _selected_samples("quality-file")
    if not samples:
        return
    data = report(samples, rc.responses(WS.dataset_name(st.session_state["ws"])))
    a, b, c = st.columns(3)
    a.metric("样本", data["samples"])
    b.metric("结构问题", data["issue_count"])
    c.metric("有效审核覆盖", f"{data['review_coverage']:.0%}")
    if data["ready_for_bulk"]:
        st.success("达到当前放量检查条件；仍需人工确认 G3")
    else:
        st.warning("未达到放量条件：" + ", ".join(data["block_reasons"]))
    st.dataframe(data["issues"], hide_index=True, use_container_width=True)


def page_monitor():
    st.title("运行监控")
    path = _OUT("runs.jsonl")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
    st.dataframe(rows[-100:], hide_index=True, use_container_width=True)
    _job_status()


def page_backends():
    st.title("模型与密钥")
    from lib import backend_manager as bm
    info = bm.list_backends()
    st.markdown("**后端清单**（密钥只显示掩码）")
    rows = [{"名称": r["name"], "地址": r["base_url"], "模型": "、".join(r["models"]),
             "密钥来源": r["api_key"]["source"], "密钥": r["api_key"]["status"],
             "角色": "、".join(r["roles"]) or "-", "默认": "是" if r["is_default"] else ""}
            for r in info["backends"]]
    st.dataframe(rows, hide_index=True, use_container_width=True)
    with st.expander("新增 / 覆盖端点（写入 gitignored configs/backends.local.yaml，自动备份）"):
        with st.form("backend-add"):
            name = st.text_input("后端名", "local_gpu")
            base_url = st.text_input("OpenAI 兼容地址", "http://127.0.0.1:11434/v1")
            models = st.text_input("模型名（逗号分隔）", "qwen2.5:7b-instruct")
            mode = st.radio("密钥来源", ["环境变量（推荐）", "写入本地配置"], horizontal=True)
            secret = st.text_input("密钥（环境变量名 或 密钥值）", type="password")
            prices = st.text_input("价格 JSON（可选，如 {\"input_per_1m_usd\": 0}）")
            overwrite = st.checkbox("覆盖同名后端")
            submit = st.form_submit_button("保存", type="primary")
        if submit:
            try:
                import json as _json
                prices_obj = _json.loads(prices) if prices.strip() else None
                if mode.startswith("环境变量"):
                    bm.save_endpoint(name, base_url, [m.strip() for m in models.split(",") if m.strip()],
                                     api_key_env=secret or "OPENAI_API_KEY", prices=prices_obj, explicit_replace=overwrite)
                else:
                    bm.save_endpoint(name, base_url, [m.strip() for m in models.split(",") if m.strip()],
                                     api_key=secret, prices=prices_obj, explicit_replace=overwrite)
                st.success(f"已保存 {name}（原文件备份）")
            except (ValueError, FileExistsError) as e:
                st.error(str(e))
    with st.expander("连接测试（models.list，不产生 token 费用）"):
        names = [r["name"] for r in info["backends"]]
        target = st.selectbox("选择后端", names, key="backend-test-name")
        if st.button("测试连接"):
            try:
                out = bm.test_backend(target)
                st.success(f"{target} 可达：{', '.join(out['models'][:12])}")
            except Exception as e:  # noqa: BLE001
                st.error(f"连接失败：{str(e)[:200]}")
    st.markdown("**角色槽位**（各角色的默认后端+模型；覆盖写入本地配置）")
    roles = info["roles"]
    for role in ("generation", "judge", "vision", "refine", "simulate", "translation"):
        slot = roles.get(role, {})
        local_key = f"role-slot:{role}"
        c1, c2 = st.columns([1, 3])
        with c1:
            st.write(f"{role}：{slot.get('backend', '（未配）')} / {slot.get('model', '')}")
        with c2:
            with st.form("role-form:" + role):
                backend = st.selectbox("后端", names, key=local_key + ":b", index=names.index(slot["backend"]) if slot.get("backend") in names else 0)
                model = st.text_input("模型", slot.get("model", ""), key=local_key + ":m")
                if st.form_submit_button("保存角色"):
                    try:
                        bm.set_role(role, backend, model)
                        st.success("角色槽位已更新")
                    except ValueError as e:
                        st.error(str(e))
    st.markdown("**预算（全局）**")
    b1, b2 = st.columns(2)
    b1.metric("上限", f"${info['budget'].get('max_total_usd', 0)}")
    b2.metric("已花费（当前会话进程之外以文件为准）", f"${info['spent']:.4f}")
    confirm = st.checkbox("我确认清零预算（审计记录本次操作）", key="budget-reset-confirm")
    if st.button("清零预算", disabled=not confirm):
        spent = bm.reset_budget("console")
        st.success(f"预算已清零（原已用 ${spent:.4f}，已记审计）")


def page_gates():
    st.title("闸门（HITL）")
    gate = _gate()
    for gid, definition in gate.defs.items():
        st.subheader(f"{gid} · {definition.title}（{gate.status(gid)}）")
        st.write(definition.trigger)
        confirmation = st.checkbox("已阅读并确认：" + definition.prompt, key="confirm:" + st.session_state["ws"] + ":" + gid)
        if st.button("确认通过 " + gid, disabled=not confirmation):
            gate.decide(gid, True, note="控制台人工确认")
            st.rerun()
    st.caption("注意：审核汇总不会自动放行 G3；bulk 导出还会校验当前样本的审核覆盖。")


def _asset_categories():
    cats = {}
    for path in sorted(_ws_out().rglob("*")):
        if not path.is_file() or path.suffix not in (".jsonl", ".json", ".html", ".txt"):
            continue
        if "review_batches" in path.parts:
            continue
        category = "DPO 偏好对" if "dpo" in path.name else "语料" if "corpus" in path.parts else "样本" if "samples" in path.name else "报告与状态"
        cats.setdefault(category, []).append(path)
    return cats


def page_assets():
    st.title("资产管理")
    search = st.text_input("搜索文件名")
    for category, paths in _asset_categories().items():
        with st.expander(category, expanded=True):
            for path in paths:
                if search.lower() not in str(path.relative_to(_ws_out())).lower():
                    continue
                a, b = st.columns([4, 1])
                a.write(str(path.relative_to(_ws_out())))
                b.download_button("下载", path.read_bytes(), file_name=path.name, key=str(path))


PREF_FILES = {
    "生成偏好": ROOT / "configs/preferences.yaml",
    "思考风格": ROOT / "configs/cot_styles.yaml",
    "语言规则": ROOT / "configs/style_rules.example.yaml",
}


def page_prefs():
    st.title("偏好设置")
    import yaml
    path = PREF_FILES[st.selectbox("配置", list(PREF_FILES))]
    original = path.read_text(encoding="utf-8")
    text = st.text_area("配置内容", original, height=380)
    if st.button("保存配置"):
        try:
            yaml.safe_load(text)
            from datetime import datetime
            backup = path.with_suffix(path.suffix + "." + datetime.now().strftime("%Y%m%d%H%M%S%f") + ".bak")
            backup.write_text(original, encoding="utf-8")
            path.write_text(text, encoding="utf-8")
            st.success("已保存并备份")
        except (ValueError, yaml.YAMLError) as error:
            st.error(str(error))


PAGES = {"总览": page_overview, "资产管理": page_assets, "数据预览": page_preview,
         "管线运行": page_run, "人工审核": page_review, "质量报告": page_quality,
         "监控": page_monitor, "模型与密钥": page_backends, "闸门": page_gates,
         "偏好设置": page_prefs}
_ws_choice()
page = st.sidebar.radio("DataForge 控制台", list(PAGES))
try:
    PAGES[page]()
except (ValueError, OSError) as error:
    st.error(str(error))
