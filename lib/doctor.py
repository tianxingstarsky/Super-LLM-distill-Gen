"""Read-only diagnostics: never spawn tests, install packages, or rewrite state."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
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
    # ── GPU 与本地推理（CUDA 接入点） ────────────────────────────────────
    gpu = {}
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run([shutil.which("nvidia-smi"), "--query-gpu=name,memory.total,memory.used",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=15).stdout
            for line in out.strip().splitlines():
                name, total, used = [x.strip() for x in line.split(",")]
                gpu = {"name": name, "total_mb": int(total), "used_mb": int(used)}
        except Exception:  # noqa: BLE001
            gpu = {}
    if gpu:
        pressure = gpu["used_mb"] / max(gpu["total_mb"], 1)
        add(f"CUDA GPU: {gpu['name']}（{gpu['used_mb']}/{gpu['total_mb']} MiB）",
            pressure < 0.9,
            "显存占用 ≥90%：不要在此机器启动本地模型（本地推理会抢占训练任务显存）" if pressure >= 0.9 else "")
    else:
        add("CUDA GPU", False, "未检测到 nvidia-smi/GPU；本地推理需在有 GPU 的机器开启")
    torch_cuda = False
    try:
        import torch
        torch_cuda = bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        torch_cuda = False
    add("torch+cuda（vLLM/嵌入等 CUDA 依赖）", torch_cuda,
        "安装 torch 的 CUDA 版：pip install torch --index-url https://download.pytorch.org/whl/cu129")
    for port, label in ((11434, "Ollama"), (8765, "llama.cpp 桥"), (8000, "vLLM")):
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{port}/", timeout=2) as response:
                up = response.status == 200
        except OSError:
            up = False
        if up:
            add(f"本地推理端点：{label}（:{port}）", True)
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
