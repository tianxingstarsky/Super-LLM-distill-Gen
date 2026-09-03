"""M0 冒烟测试用：最小 OpenAI 兼容 mock 服务（仅标准库，不引入依赖）。

用途：验证 distilabel 管线在 Windows 上的 DAG 执行与步骤缓存机制，
      无需任何 API key 或本地模型。仅限测试使用。
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockLLMHandler(BaseHTTPRequestHandler):
    """按 OpenAI /v1/chat/completions 协议回包；/v1/models 返回模型列表。"""

    server_version = "MockLLM/0.1"
    request_count = 0  # 类级计数器：缓存生效时重跑管线不应再增加

    def log_message(self, fmt, *args):  # 静默
        pass

    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/v1/models":
            self._send_json({"object": "list", "data": [{"id": "mock-model", "object": "model"}]})
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send_json({"error": "not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length", 0))
        # rfile.read(n) 可能一次读不满，需循环读满
        body = b""
        while len(body) < length:
            chunk = self.rfile.read(length - len(body))
            if not chunk:
                break
            body += chunk
        MockLLMHandler.request_count += 1
        try:
            # 容错：部分环境下请求体会被代理附加 \r\n\r\n 前缀/帧错乱，
            # 只取首个 '{' 到末个 '}' 之间的 JSON 对象
            payload = json.loads(body[body.find(b"{"): body.rfind(b"}") + 1] or b"{}")
        except json.JSONDecodeError:
            print(f"[mock] parse failed, content-length={length}, body={body[:200]!r}")
            self._send_json({"error": "invalid json"}, status=400)
            return
        last_msg = (payload.get("messages") or [{}])[-1].get("content", "")
        content = f"[mock 回复] 收到请求：{str(last_msg)[:80]}"
        self._send_json(
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "model": payload.get("model", "mock-model"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )


def serve(port: int = 8765) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), MockLLMHandler)
    print(f"mock llm listening on http://127.0.0.1:{port}/v1")
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(args.port)
