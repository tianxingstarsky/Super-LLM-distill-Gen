"""Exercise the real canvas script with browser-style selection events."""
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]

def test_canvas_node_navigation_and_dependency_highlight():
    node = shutil.which('node')
    if not node or not (ROOT / 'node_modules/jsdom').is_dir():
        pytest.skip('Node and jsdom are required')
    result = subprocess.run([node, str(ROOT / 'tests/workflow_canvas_harness.mjs')],
                            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
