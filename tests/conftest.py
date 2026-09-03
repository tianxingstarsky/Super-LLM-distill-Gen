"""pytest 公共配置：sys.path、代理绕过、会话级共享 mock LLM 服务器。"""
import os
import pathlib
import sys
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# 子进程 worker（distilabel spawn）同样需要该路径
os.environ.setdefault("PYTHONPATH", str(ROOT))

# 本机 httpx 会按系统代理(trust_env)转发 localhost 请求并导致请求体帧错乱；
# 本地端点（mock/Ollama/LM Studio）必须绕过代理。真实管线同理，见 spike 报告 F2。
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

# 端口避开用户本机的 llama.cpp bridge_server（监听 8765，勿动）；mock 用 18765
MOCK_PORT = 18765


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """全会话共享一个 mock OpenAI 服务（每个测试模块各自绑定同端口会被 Windows 拒绝）。"""
    sys.path.insert(0, str(ROOT / "tests"))
    import mock_llm_server

    thread = threading.Thread(target=mock_llm_server.serve, args=(MOCK_PORT,), daemon=True)
    thread.start()
    time.sleep(0.6)
    yield mock_llm_server
    # 守护线程随进程退出


