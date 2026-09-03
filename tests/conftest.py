"""pytest 公共配置：保证项目根在 sys.path（distilabel 反序列化自定义步骤时需 import lib）。"""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# 子进程 worker（distilabel spawn）同样需要该路径
os.environ.setdefault("PYTHONPATH", str(ROOT))

# 本机 httpx 会按系统代理(trust_env)转发 localhost 请求并导致请求体帧错乱；
# 本地端点（mock/Ollama/LM Studio）必须绕过代理。真实管线同理，见 spike 报告。
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

