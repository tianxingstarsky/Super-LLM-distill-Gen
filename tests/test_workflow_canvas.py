"""Exercise the real canvas script with browser-style selection events."""
from pathlib import Path
from itertools import combinations
import json
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]

def _spec(targets):
    from lib.domain.workflow_graph import execution_graph
    from lib.presentation.streamlit.workflow_canvas import canvas_spec

    labels = {key: key for key in execution_graph(targets)[0]}
    return canvas_spec(targets, {}, "package", labels, labels, language="en")


def test_all_target_combinations_fit_without_overlapping_nodes():
    from lib.domain.workflow_targets import TARGETS

    targets = tuple(TARGETS)
    for count in range(len(targets) + 1):
        for selected in combinations(targets, count):
            spec = _spec(selected)
            columns = {}
            for node in spec["nodes"]:
                assert 0 <= node["x"] <= spec["width"] - 218, selected
                assert 0 <= node["y"] <= spec["height"] - 78, selected
                columns.setdefault(node["x"], []).append(node["y"])
            for column in columns.values():
                positions = sorted(column)
                assert all(after - before >= 78 for before, after in zip(positions, positions[1:])), selected

    assert _spec(["cpt"])["height"] < 200
    assert _spec(["dpo", "cot"])["height"] > _spec(["dpo"])["height"]


def test_canvas_node_navigation_and_dependency_highlight():
    node = shutil.which('node')
    if not node or not (ROOT / 'node_modules/jsdom').is_dir():
        pytest.skip('Node and jsdom are required')
    from lib.domain.workflow_targets import TARGETS

    specs = [_spec(targets) for targets in ([], ["cpt"], ["dpo", "cot"],
                                           ["sft", "dpo", "cot"], list(TARGETS))]
    result = subprocess.run([node, str(ROOT / 'tests/workflow_canvas_harness.mjs'), '--check-specs'],
                            input=json.dumps(specs), cwd=ROOT, capture_output=True,
                            text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
