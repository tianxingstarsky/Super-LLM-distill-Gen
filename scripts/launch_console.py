"""Single-instance, windowless console entry. No restart loop or watchdog."""
from pathlib import Path
import os
import sys
import webbrowser

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from filelock import FileLock, Timeout

output = ROOT / "data/output"
output.mkdir(parents=True, exist_ok=True)
lock = FileLock(str(output / "console.lock"), timeout=0)
try:
    lock.acquire()
except Timeout:
    webbrowser.open("http://127.0.0.1:8501")
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
