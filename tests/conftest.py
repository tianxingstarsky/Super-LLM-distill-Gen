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

@pytest.fixture(autouse=True)
def isolated_review_store(tmp_path, monkeypatch):
    from lib import review_center as rc
    rc.stop_thread()
    monkeypatch.setattr(rc, "DB_PATH", tmp_path / "review.db")
    monkeypatch.setenv("DF_WORKSPACE", "default")
    yield
    rc.stop_thread()


# 端口避开用户本机的 llama.cpp bridge_server（监听 8765，勿动）；mock 用 18765
MOCK_PORT = 18765


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """会话级 mock OpenAI 服务，启动后自证所有权（探针请求必须打到自己的服务器）。

    若端口被残留进程占用：绑定失败 → 探针测不到 → fixture 大声失败，
    而不是让管线静默连上别人的实例（曾导致 request_count 恒为 0 的误判）。"""
    import sys
    import urllib.request

    sys.path.insert(0, str(ROOT / "tests"))
    import mock_llm_server

    thread = threading.Thread(target=mock_llm_server.serve, args=(MOCK_PORT,), daemon=True)
    thread.start()
    time.sleep(0.6)
    # 自证：一次真实请求必须由我们的 handler 计数
    before = mock_llm_server.MockLLMHandler.request_count
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:%d/v1/chat/completions" % MOCK_PORT,
            data=b'{"model":"mock","messages":[{"role":"user","content":"probe"}]}',
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as response:
            assert response.status == 200
    except OSError as error:  # noqa: BLE001
        raise RuntimeError(f"mock 服务未就绪/端口 {MOCK_PORT} 被占用: {error}") from error
    assert mock_llm_server.MockLLMHandler.request_count - before == 1, "探测请求未到达本会话 mock（疑似端口被残留进程占用）"
    yield mock_llm_server
    # 守护线程随进程退出


