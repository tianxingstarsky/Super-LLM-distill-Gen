"""Read-only diagnostics: never spawn tests, install packages, or rewrite state."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import shutil
import urllib.request
import yaml

ROOT = Path(__file__).resolve().parent.parent


def checks(root=None):
    root = Path(root or ROOT)
    rows = []
    def add(name, ok, hint="", required=False):
        rows.append({"check": name, "status": "ok" if ok else "fail" if required else "warn", "hint": "" if ok else hint})
    for package in ("streamlit", "openai", "yaml", "filelock"):
        add("Python: " + package, importlib.util.find_spec(package) is not None, "Install requirements.txt", True)
    cfg = {}
    for path in (root / "configs/backends.yaml", root / "configs/backends.local.yaml"):
        if path.exists():
            try:
                part = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                cfg["backends"] = {**cfg.get("backends", {}), **part.get("backends", {})}
            except yaml.YAMLError:
                add("Backend YAML", False, "Invalid YAML", True)
    configured = bool(os.environ.get("OPENAI_API_KEY")) or any(b.get("api_key") or os.environ.get(b.get("api_key_env", "")) for b in cfg.get("backends", {}).values())
    add("Model credentials", configured, "Configure an environment credential or backends.local.yaml")
    for port, path in ((8501, "/_stcore/health"), (6900, "/health")):
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
                ok = response.status == 200
        except OSError:
            ok = False
        add(f"Local service {port}", ok, "Start the console once; no watchdog is required")
    harness = root / "components/deepseek-harness"
    add("dsh source build", (harness / "apps/cli/lib/bin.js").exists(), "Build the pinned harness source first")
    add("Node", shutil.which("node") is not None, "Install Node supported by harness")
    for variable in ("PIP_CACHE_DIR", "HF_HOME", "DSH_HOME"):
        value = os.environ.get(variable)
        add(variable, bool(value) and not (os.name == "nt" and value.lower().startswith("c:")), "Set a workspace/tools path; unset is not proof of F-drive isolation")
    return rows


def main():
    rows = checks()
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return int(any(r["status"] == "fail" for r in rows))


if __name__ == "__main__":
    raise SystemExit(main())
