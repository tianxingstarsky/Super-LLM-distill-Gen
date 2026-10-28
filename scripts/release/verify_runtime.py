"""Check an installed distribution without network or model calls."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path, PurePosixPath
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    expected_python = ROOT / ".runtime/python/cpython-3.11.9-windows-x86_64-none"
    if (sys.version_info[:3] != (3, 11, 9)
            or Path(sys.base_prefix).resolve() != expected_python.resolve()
            or Path(sys.prefix).resolve() != (ROOT / ".venv").resolve()):
        raise ValueError("Verification must use this app's private Python environment")
    payload = json.loads((ROOT / "PAYLOAD.json").read_text(encoding="utf-8"))
    files = payload["files"]
    for relative, expected in files.items():
        source = PurePosixPath(relative)
        if source.is_absolute() or ".." in source.parts:
            raise ValueError("Invalid installed-file path")
        target = ROOT.joinpath(*source.parts)
        with target.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"Installed file does not match release payload: {relative}")
    for relative in payload.get("initial_seeds", {}):
        source = PurePosixPath(relative)
        if source.is_absolute() or ".." in source.parts or source.parts[0] not in {"configs", "data"}:
            raise ValueError("Invalid configuration or sample seed path")
    manifest = json.loads((ROOT / "INSTALLATION.json").read_text(encoding="utf-8"))
    if manifest["version"] != "0.0.1" or (ROOT / "VERSION").read_text().strip() != "0.0.1":
        raise ValueError("Installed release version does not match")
    lock = ROOT / "scripts/release/requirements-windows-x64.lock"
    if hashlib.sha256(lock.read_bytes()).hexdigest() != manifest["requirements_sha256"]:
        raise ValueError("The installed dependency lock does not match its manifest")
    for relative in (
        "assets/brand/shujian-cube-mark.png", "assets/brand/shujian-cube.ico",
        "lib/webapp.py", "lib/components/editable_chat/index.html",
        "lib/components/review_workspace/index.html",
        "lib/components/workflow_stream/index.html",
        "lib/presentation/streamlit/workflow_canvas_frontend/index.html",
        "scripts/start_all.vbs", "scripts/release/start_browser.vbs",
        "configs/backends.yaml", "configs/preferences.yaml",
    ):
        if not (ROOT / relative).is_file():
            raise FileNotFoundError(relative)
    for name in (
        "streamlit.web.bootstrap", "webview", "openai", "anthropic", "distilabel",
        "lib.cli", "lib.contexts.models.api", "lib.contexts.workflows.api",
        "lib.contexts.datasets.api", "lib.contexts.review.api", "lib.contexts.documents.api",
    ):
        importlib.import_module(name)
    receipt = {
        "version": manifest["version"], "source_tree": manifest["source_tree"],
        "requirements_sha256": manifest["requirements_sha256"],
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable, "status": "runtime_verified",
        "verified_payload_files": len(files),
        "preserved_seed_files": len(payload.get("initial_seeds", {})),
        "model_api_calls": 0,
    }
    path = ROOT / ".runtime/verified.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
