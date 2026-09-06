"""Prepare only DataForge-owned links and start the pinned harness without shell scripts."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import subprocess
import sys
import yaml
from lib.io_utils import quiet_process

ROOT = Path(__file__).resolve().parent.parent


def _link(link, target):
    if link.exists():
        if link.resolve() != target.resolve():
            raise ValueError(f"Existing path belongs to another location: {link}")
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["node", "-e", "require('fs').symlinkSync(process.argv[1],process.argv[2],process.platform==='win32'?'junction':'dir')", str(target), str(link)], check=True, **quiet_process())


def ensure_ready(root=None):
    root = Path(root or ROOT)
    harness = root / "components/deepseek-harness"
    if not shutil.which("node") or not (harness / "apps/cli/lib/bin.js").exists():
        raise ValueError("dsh build missing; run doctor and follow plugin README")
    plugin = root / "plugins/dsh-dataforge"
    tools = root.parent / "tools"
    scope = tools / "dsh-plugin-deps/node_modules/@deepseek-ai"
    _link(scope / "cordis", harness / "vendor/cordis")
    _link(scope / "dsh-tools", harness / "packages/core/tools")
    _link(plugin / "node_modules", scope.parent)
    # Configure a custom skill root in the local patch; do not take over an existing user skill directory.
    subprocess.run([sys.executable, str(root / "scripts/make_dsh_patch.py")], cwd=root, check=True, capture_output=True, **quiet_process())
    return {"patch": True, "plugin_deps": True, "skills": True}


def run(task, team=False, root=None):
    root = Path(root or ROOT)
    ensure_ready(root)
    harness = root / "components/deepseek-harness"
    env = dict(os.environ)
    tools = root.parent / "tools"
    env.update(DF_ROOT=str(root), DF_PYTHON=sys.executable, DSH_TELEMETRY_DISABLED="1")
    env.setdefault("DSH_HOME", str(tools / "dsh-home"))
    env.setdefault("DSH_AGENTS_HOME", str(tools / "dsh-home/.agents"))
    local = root / "configs/backends.local.yaml"
    if not env.get("DEEPSEEK_API_KEY") and local.exists():
        cfg = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        backend = cfg.get("backends", {}).get("deepseek", {})
        env["DEEPSEEK_API_KEY"] = backend.get("api_key") or env.get(backend.get("api_key_env", "DEEPSEEK_API_KEY"), "")
    command = ["node", "--import", "tsx/esm", "apps/cli/src/bin.ts", "--profile", "headless", "--patch", str(root / "plugins/dsh-dataforge/cordis.yml")]
    if team:
        command += ["--patch", str(root / "plugins/dsh-dataforge/team.yml")]
    command += [task]
    return subprocess.call(command, cwd=harness, env=env, **quiet_process())
