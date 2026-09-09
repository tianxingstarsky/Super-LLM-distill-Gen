"""Single-instance, windowless console entry. No restart loop or watchdog.

单实例语义：拿不到锁说明已有一个控制台在跑——直接退出（只记日志），
**不要**在这里调 webbrowser.open（分离进程里会挂死并留下僵尸进程，实测教训）。
"""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from filelock import FileLock, Timeout

output = ROOT / "data/output"
output.mkdir(parents=True, exist_ok=True)
lock = FileLock(str(output / "console.lock"), timeout=0)
try:
    lock.acquire()
except Timeout:
    with (output / "console.log").open("a", encoding="utf-8", buffering=1) as log:
        log.write("[launch] 已有控制台在运行（单实例），本次启动跳过。若页面打不开，先关闭残留进程再重试。\n")
    raise SystemExit(0)
with (output / "console.log").open("a", encoding="utf-8", buffering=1) as log:
    sys.stdout = sys.stderr = log
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for key, name in (("PIP_CACHE_DIR", "pip-cache"), ("HF_HOME", "hf-home"), ("DSH_HOME", "dsh-home")):
        os.environ.setdefault(key, str(ROOT.parent / "tools" / name))
    os.chdir(ROOT)
    from lib.cli import _launch_console
    try:
        _launch_console()
    finally:
        lock.release()
