"""行为测试：在 Node + jsdom 中执行真实的审核工作台组件 index.js。

覆盖安全/核心状态转换：scope 重置、重复渲染保留草稿、保存请求的字段范围与
基准哈希、成功/失败 ack、挂起请求去重、空理由拒绝、输入态快捷键隔离、守卫切换。

只测真实脚本行为（DOM 交互 + 发出的事件 + 组件状态），不做源码字符串检查。
jsdom 是 package.json 的 devDependency，已存在于 node_modules；不安装任何依赖。
独立运行，不加载 conftest，避免会话级 mock LLM 端口冲突：

    .venv/Scripts/python.exe -m pytest tests/test_review_component.py -q --noconftest
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "review_component_harness.mjs"
COMPONENT = ROOT / "lib" / "components" / "review_workspace" / "index.js"

SCENARIOS = (
    "scope_reset",
    "repeat_render_preserves_draft",
    "save_request_is_field_only_and_blocks_repeat",
    "ctrl_s_saves_draft_only",
    "ack_success_clears_draft_and_keeps_scroll",
    "ack_failure_retains_draft_and_reason",
    "pending_blocks_repeat_navigation_and_submit_ack",
    "blank_reason_is_refused",
    "shortcuts_do_not_submit_while_typing",
    "guard_switch_flow",
    "ai_run_requires_instruction",
)


def _temp_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Node 临时目录：优先 DF_TEST_TMP，其次仓库同级的 tools/tmp（相对 ROOT.parent，
    随仓库所在盘符走，不硬编码 F:），最后交给 pytest 的 tmp_path_factory
    （尊重 TMP / --basetemp，调用方把 TMP 指到哪就落在哪）。"""
    candidates = []
    override = os.environ.get("DF_TEST_TMP")
    if override:
        candidates.append(Path(override))
    candidates.append(ROOT.parent / "tools" / "tmp")
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    return tmp_path_factory.mktemp("df-review-component")


@pytest.fixture(scope="module")
def harness_results(tmp_path_factory):
    """跑一次 Node 行为 harness，返回 {scenario: {ok, observed|error}}。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("node executable not found")
    if not (ROOT / "node_modules" / "jsdom").is_dir():
        pytest.skip("jsdom is not installed; refusing to install dependencies")
    assert COMPONENT.is_file(), f"component under test is missing: {COMPONENT}"

    tmp = _temp_dir(tmp_path_factory)
    env = {
        **os.environ,
        "TMP": str(tmp), "TEMP": str(tmp), "TMPDIR": str(tmp),
        "PYTHONPYCACHEPREFIX": str(tmp / "pycache"),
    }
    proc = subprocess.run(
        [node, str(HARNESS)], cwd=str(ROOT), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip().startswith("{")]
    assert lines, (
        "component harness emitted no JSON\n"
        f"exit={proc.returncode}\nstdout={proc.stdout[-2000:]}\nstderr={proc.stderr[-2000:]}"
    )
    return json.loads(lines[-1])["scenarios"]


def _observed(results, name):
    entry = results[name]
    assert entry.get("ok"), f"{name} failed:\n{entry.get('error')}"
    observed = entry.get("observed") or {}
    assert observed, f"{name} returned no observations"
    return observed


def test_harness_covers_every_required_scenario(harness_results):
    assert set(harness_results) == set(SCENARIOS)


def test_scope_reset_clears_draft_ai_reason_pending_and_scroll(harness_results):
    obs = _observed(harness_results, "scope_reset")
    assert obs["draft"] is None
    assert obs["ai"] is None
    assert obs["reason"] == ""
    assert obs["pending"] is None
    assert obs["scrollTopAfterScopeChange"] == 0  # 切换 scope 后回到顶部


def test_repeat_render_preserves_draft_without_redraw(harness_results):
    obs = _observed(harness_results, "repeat_render_preserves_draft")
    assert obs["keptSameNode"] is True          # 相同 data 不重绘，草稿输入框节点不变
    assert obs["draftText"] == "重复渲染草稿"    # 数据变化重绘后草稿文本仍在
    assert obs["scrollAfterRedraw"] == 90       # 重绘保滚动


def test_save_request_is_field_only_and_blocks_repeat(harness_results):
    obs = _observed(harness_results, "save_request_is_field_only_and_blocks_repeat")
    assert obs["action"] == "save"
    assert obs["keys"] == ["action", "field", "id", "index", "record_id", "sample_hash", "text"]
    assert obs["sampleHash"] == "hash-1"        # 基准哈希随请求携带（陈旧校验依据）
    assert obs["recordId"] == 1
    assert (obs["index"], obs["field"]) == (2, "content")
    assert obs["text"] == "修改后的正文"
    assert obs["repeatBlocked"] is True
    assert obs["busy"] is True
    assert obs["allControlsDisabled"] is True  # 挂起期间所有输入/按钮禁用，草稿不会丢


def test_ctrl_s_saves_open_draft_only(harness_results):
    obs = _observed(harness_results, "ctrl_s_saves_draft_only")
    assert obs["action"] == "save"
    assert obs["text"] == "Ctrl+S 草稿"
    assert obs["noDraftEmissions"] == 0         # 无草稿时 Ctrl+S 不提交


def test_ack_success_clears_draft_and_keeps_scroll(harness_results):
    obs = _observed(harness_results, "ack_success_clears_draft_and_keeps_scroll")
    assert obs["draft"] is None
    assert obs["pending"] is None
    assert obs["editorPresent"] is False
    assert obs["newContentShown"] is True       # 使用服务端确认的新内容渲染
    assert obs["reason"] == "已填写的理由"       # 保存不清判定理由
    assert obs["scrollAfterAck"] == 180


def test_ack_failure_retains_draft_and_reason_for_retry(harness_results):
    obs = _observed(harness_results, "ack_failure_retains_draft_and_reason")
    assert obs["draftText"] == "失败草稿"
    assert obs["reason"] == "失败也要保留的理由"
    assert obs["pendingAfterFailure"] is None   # 失败后允许重试
    assert obs["retryText"] == "失败草稿"
    assert obs["retryUsesFreshId"] is True
    # 同一 record_id 被服务端刷新（新 sample_hash/内容）时不重绑草稿：
    # 保存仍发送 openEdit 时捕获的原始哈希，后端按陈旧内容拒绝。
    assert obs["capturedHash"] == "hash-1"
    assert obs["capturedRecordId"] == 1
    assert obs["refreshedHash"] == "hash-2"
    assert obs["saveHash"] == "hash-1"
    assert obs["saveRecordId"] == 1


def test_pending_blocks_repeat_and_submit_ack_resets(harness_results):
    obs = _observed(harness_results, "pending_blocks_repeat_navigation_and_submit_ack")
    assert obs["submitAction"] == "submit"
    assert obs["decision"] == "keep"
    assert obs["repeatBlocked"] is True         # 挂起时重复判定/切换被拒
    assert obs["allControlsDisabled"] is True   # 所有输入/按钮禁用，编辑不会丢
    assert "正在处理" in obs["notice"]
    assert obs["pendingAfterAck"] is None
    assert obs["reasonAfterAck"] == ""          # submit ack 清理本地理由
    assert obs["scrollAfterAck"] == 0


def test_blank_reason_is_refused(harness_results):
    obs = _observed(harness_results, "blank_reason_is_refused")
    assert obs["refusals"] == 0                 # 空/纯空白理由不提交
    assert obs["errorNotice"] is True
    assert obs["focusedReason"] is True
    assert obs["decisionAfterFill"] == "reject"
    assert obs["emittedReason"] == "有依据"


def test_shortcuts_do_not_submit_while_typing(harness_results):
    obs = _observed(harness_results, "shortcuts_do_not_submit_while_typing")
    assert obs["inputsEmitted"] == 0            # 输入控件内 Alt+Enter/Backspace/→ 不触发
    assert obs["action"] == "submit"
    assert obs["decision"] == "keep"
    assert obs["reason"] == "快捷键理由"
    assert obs["rejectDecision"] == "reject"    # 非输入区 Alt+Backspace 正常驳回


def test_guard_switch_flow(harness_results):
    obs = _observed(harness_results, "guard_switch_flow")
    assert obs["heldNoEmit"] is True            # 有未保存内容时切换被拦截
    assert obs["stayEmits"] == 0                # 继续编辑不产生请求
    assert obs["stayRefocus"] == "reason"       # guard-stay 聚焦回可编辑处（无源码编辑器时用理由框）
    assert obs["stayRefocusSource"] is True     # 有草稿时 guard-stay 聚焦源码编辑器
    assert obs["discardedAction"] == "select"   # 放弃后执行原切换
    assert obs["discardedTarget"] == "s2"
    assert obs["chainedActions"] == ["save", "select"]  # 保存成功后继续原切换
    assert obs["chainedTarget"] == "s2"
    assert obs["draftAfterChain"] is None


def test_ai_run_requires_instruction_and_sends_no_body(harness_results):
    obs = _observed(harness_results, "ai_run_requires_instruction")
    assert obs["blankRefused"] is True          # 空修改要求不请求
    assert obs["action"] == "ai"
    assert (obs["index"], obs["field"]) == (2, "reasoning_content")
    assert obs["instruction"] == "把思考写得更简洁"
    assert obs["sampleHash"] == "hash-1"
    assert obs["sendsBody"] is False            # 只传要求，不传消息正文
    assert obs["disabledWhilePending"] is True
