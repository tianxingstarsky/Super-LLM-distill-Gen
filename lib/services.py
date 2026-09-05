"""服务托管：注册表（单一事实源）+ 看门狗守护进程 + 控制台状态接口。

背景：本会话环境下后台任务会被回收器清理，导致"服务健康"反复异常。
方案：所有服务由独立看门狗进程（pythonw 守护，脱离会话存活）统一拉起与守护；
注册表为单一事实源；状态写入 data/output/services.json，控制台读取并可控（重启）。
"""
from __future__ import annotations

import json
import sys
import os
import pathlib
import socket
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "output"
LOG_DIR = OUT_DIR / "logs"
STATE_PATH = OUT_DIR / "services.json"
CMD_PATH = OUT_DIR / "services_cmd.json"
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")
PYTHONW = str(ROOT / ".venv" / "Scripts" / "pythonw.exe")
TOOLS = pathlib.Path("F:/无项目工作文件夹/tools")
ES_HOME = TOOLS / "elasticsearch-8.17.0"
REDIS_HOME = TOOLS / "redis-win"

# 单一事实源：所有服务/端口/探针/启动命令
SERVICES = [
    {
        "name": "redis", "port": 6379, "probe": "redis",
        "exe": str(REDIS_HOME / "redis-server.exe"),
        "args": ["--bind", "127.0.0.1", "--port", "6379", "--save", "", "--appendonly", "no"],
        "cwd": str(REDIS_HOME), "env": {}, "startup_sec": 5,
    },
    {
        "name": "elasticsearch", "port": 9200, "probe": "http",
        "exe": str(ES_HOME / "bin" / "elasticsearch.bat"),
        "args": [], "cwd": str(ES_HOME), "env": {}, "startup_sec": 60,
    },
    {
        "name": "argilla", "port": 6900, "probe": "http",
        "exe": PYTHON,
        "args": ["-W", "ignore", "-m", "uvicorn", "argilla_server:app", "--host", "127.0.0.1", "--port", "6900"],
        "cwd": str(ROOT), "startup_sec": 30,
        "env": {
            # sqlite 绝对路径（Windows 盘符后跟反斜杠语法）
            "ARGILLA_DATABASE_URL": "sqlite:///" + str(ROOT / "data" / "output" / "argilla.db").replace("\\", "/").replace(":/", ":/"),
            "ARGILLA_AUTH_SECRET_KEY": "super-llm-distill-gen-secret",
            "ARGILLA_API_URL": "http://127.0.0.1:6900",
            "ARGILLA_WORKSPACE": "admin", "USERNAME": "admin", "PASSWORD": "distill123456",
            "ARGILLA_ENABLE_TELEMETRY": "0", "ELASTICSEARCH": "http://127.0.0.1:9200",
            "ARGILLA_REDIS": "redis://127.0.0.1:6379/0",
        },
    },
    {
        "name": "preview", "port": 18700, "probe": "http",
        "exe": PYTHON,
        "args": ["-m", "http.server", "18700", "--bind", "127.0.0.1", "--directory", str(ROOT / "data" / "output")],
        "cwd": str(ROOT), "env": {}, "startup_sec": 5,
    },
    {
        "name": "console", "port": 8501, "probe": "http",
        "exe": PYTHON,
        "args": ["-W", "ignore", "-m", "lib.cli", "console"],
        "cwd": str(ROOT), "env": {}, "startup_sec": 20,
    },
]

# 运行引擎模式（单一事实源）：
#   light  = 单进程：只跑控制台（全部数据操作/审核/监控/资产在控制台内，~300MB）
#   share  = light + 静态预览页（分享链接用，+30MB）
#   collab = share + Argilla 协作审核栈（Redis+ES(JVM 2GB)+Argilla，多人协作用）
MODES = {
    "light": ["console"],
    "share": ["console", "preview"],
    "collab": ["console", "preview", "redis", "elasticsearch", "argilla"],
}
MODE_PATH = OUT_DIR / "services_mode.json"
MEMORY_HINT = {"light": "单进程 ~300MB，全部操作在控制台内完成",
               "share": "单进程 + 预览页 ~330MB",
               "collab": "完整栈 ~2.5GB+（Redis+ES JVM 2GB+Argilla，多人协作审核）"}


def load_mode() -> str:
    if MODE_PATH.exists():
        try:
            return json.loads(MODE_PATH.read_text(encoding="utf-8")).get("mode", "light")
        except json.JSONDecodeError:
            pass
    return "light"


def set_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"未知模式 {mode}，可用: {sorted(MODES)}")
    MODE_PATH.write_text(json.dumps({"mode": mode}, indent=1), encoding="utf-8")


def active_services() -> list:
    return [s for s in SERVICES if s["name"] in MODES[load_mode()]]


def probe(service: dict) -> bool:
    name, port = service["name"], service["port"]
    try:
        if service.get("probe") == "redis":
            import redis

            return bool(redis.Redis(host="127.0.0.1", port=port).ping())
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
            return 100 <= r.status < 500
    except Exception:  # noqa: BLE001
        return False


def _detached_start(service: dict) -> None:
    """原生分离启动（Windows DETACHED_PROCESS：父进程退出后仍存活，脱离会话）。"""
    import subprocess as _sp

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{service['name']}.log"
    log_handle = open(log_path, "ab", buffering=0)
    exe = service["exe"]
    args = list(service["args"])
    if str(exe).lower().endswith(".bat") or str(exe).lower().endswith(".cmd"):
        # .bat 需要 cmd /c 包装
        argv = ["cmd", "/c", exe, *args]
    else:
        argv = [exe, *args]
    env = {**os.environ, **service.get("env", {})}
    env.setdefault("NO_PROXY", "127.0.0.1,localhost")
    env.setdefault("no_proxy", "127.0.0.1,localhost")
    creationflags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        _sp.Popen(
            argv, cwd=service["cwd"], env=env,
            stdout=log_handle, stderr=log_handle, stdin=_sp.DEVNULL,
            creationflags=creationflags,
        )
    except Exception as e:  # noqa: BLE001
        log_handle.write(f"[services] start failed: {e}\n".encode())
    finally:
        log_handle.close()


def start_service(name: str) -> bool:
    svc = next((s for s in SERVICES if s["name"] == name), None)
    if svc is None:
        return False
    _detached_start(svc)
    return True


def health_snapshot() -> dict:
    import time as _t

    snap = {"updated_at": _t.strftime("%Y-%m-%dT%H:%M:%S"), "mode": load_mode(),
            "memory": MEMORY_HINT.get(load_mode(), ""), "services": {}}
    for svc in active_services():
        snap["services"][svc["name"]] = {"port": svc["port"], "up": probe(svc)}
    return snap


def watchdog_loop(interval: int = 20, max_restart_per_hour: int = 3) -> None:
    """看门狗主循环：常保 services.json 最新+重启下线的服务+处理控制命令。"""
    from collections import defaultdict

    restart_log: dict[str, list[float]] = defaultdict(list)
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

    while True:
        now = time.time()
        mode = load_mode()
        out = {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "watchdog": "alive",
               "mode": mode, "memory": MEMORY_HINT.get(mode, ""), "services": {}}
        for svc in active_services():
            up = probe(svc)
            out["services"][svc["name"]] = {"port": svc["port"], "up": up, "startup_sec": svc["startup_sec"]}
            if not up:
                restart_log[svc["name"]] = [t for t in restart_log[svc["name"]] if now - t < 3600]
                if len(restart_log[svc["name"]]) < max_restart_per_hour:
                    start_service(svc["name"])
                    restart_log[svc["name"]].append(now)
                    out["services"][svc["name"]]["restarted_at"] = time.strftime("%H:%M:%S")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        _handle_commands()
        time.sleep(interval)


def _handle_commands() -> None:
    """services_cmd.json 控制文件：{action: restart|stop_all, service?: name}。"""
    if not CMD_PATH.exists():
        return
    try:
        cmd = json.loads(CMD_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    CMD_PATH.unlink(missing_ok=True)
    if cmd.get("action") == "restart_all":
        for svc in SERVICES:
            start_service(svc["name"])
    elif cmd.get("action") == "restart" and cmd.get("service"):
        start_service(cmd["service"])


def load_status() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def request_restart(service: str | None = None) -> None:
    """控制台写入控制命令（看门狗下轮执行）。service=None 表示全部重启。"""
    payload = {"action": "restart" if service else "restart_all", "service": service}
    CMD_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def start_watchdog() -> None:
    """以 pythonw 脱离会话启动看门狗（幂等：已在跑则跳过）。"""
    import subprocess

    if STATE_PATH.exists():
        status = load_status()
        if status.get("watchdog") == "alive":
            return
    subprocess.run([
        sys.executable, "-c",
        "import subprocess,sys;"
        f"subprocess.Popen([r'{PYTHONW}','-W','ignore','-m','lib.services'], cwd=r'{ROOT}',"
        "creationflags=0x00000008 | 0x00000200)",
    ], capture_output=True, timeout=30)


# ── CLI 入口 ────────────────────────────────────────────────────────────────
def cmd_start_watchdog() -> None:
    watchdog_loop()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print(json.dumps(health_snapshot(), ensure_ascii=False, indent=1))
    elif len(sys.argv) > 1 and sys.argv[1] == "mode" and len(sys.argv) > 2:
        set_mode(sys.argv[2])
        print(f"模式已切换: {sys.argv[2]}（{MEMORY_HINT[sys.argv[2]]}）；重启看门狗生效: python -m lib.services start")
    elif len(sys.argv) > 1 and sys.argv[1] == "start":
        start_watchdog()
    else:
        cmd_start_watchdog()
