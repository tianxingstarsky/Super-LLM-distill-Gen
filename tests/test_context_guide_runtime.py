"""Run AppTest's real guide scripts through deterministic DOM mutations.

No HTTP service or browser is started. The Node harness uses only built-ins and
virtual time, so a widget appearing after the old five-second limit is instant.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).with_name("context_guide_harness.mjs")
SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.context_guide import _highlight, _clear_highlight
if st.session_state.get('clear'):
    _clear_highlight()
else:
    _highlight(st.session_state['route'], st.session_state['step'], 7)
'''
SCENARIOS = (
    "late_target_after_retry_window",
    "react_overwrites_class_on_same_node",
    "fallback_upgrades_and_replaced_target",
    "same_token_script_rerender_keeps_scroll",
    "change_step_disconnects_old_observer_and_frame",
    "exit_cancels_pending_frame_and_future_repairs",
)


def _export_script(route: str = "总览", step: int = 0, *, clear: bool = False) -> str:
    """Use the app's st.html output, including its actual target selectors."""
    ui = AppTest.from_string(SCRIPT)
    for key, value in {"route": route, "step": step, "clear": clear}.items():
        ui.session_state[key] = value
    ui.run()
    assert not ui.exception
    blocks = [item.proto for item in ui.get("html")]
    assert len(blocks) == 1
    assert blocks[0].unsafe_allow_javascript
    scripts = re.findall(r"<script>(.*?)</script>", blocks[0].body, re.DOTALL)
    assert len(scripts) == 1
    return scripts[0]


@pytest.fixture(scope="module")
def runtime_results():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute the guide JavaScript")
    scripts = {
        "package_export": _export_script("输出打包", 1),
        "overview_input": _export_script("总览", 0),
        "overview_target": _export_script("总览", 1),
        "clear": _export_script(clear=True),
    }
    result = subprocess.run(
        [node, str(HARNESS)], input=json.dumps(scripts), cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 0, f"Node guide harness failed:\n{result.stderr}\n{result.stdout}"
    results = json.loads(result.stdout)
    assert set(results) == set(SCENARIOS)
    return results


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_context_guide_survives_real_runtime_mutations(runtime_results, scenario):
    result = runtime_results[scenario]
    assert result["ok"], f"{scenario}:\n{result.get('error')}"
    assert result["observed"], "Harness must report observations from the executed app script"
