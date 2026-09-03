"""ChatClient：OpenAI 兼容聊天客户端（空回复重试 + token 记账），配置来自 backends.yaml。"""
from __future__ import annotations

import os
import pathlib
import time
from typing import Any, Dict, List

import yaml

DEFAULT_NO_PROXY = "127.0.0.1,localhost"


class ChatClient:
    """带空回复重试的 OpenAI 兼容客户端（DeepSeek V4 Flash 实测 ~20% 空 completion）。"""

    def __init__(self, base_url: str, api_key: str, model: str):
        from openai import OpenAI  # 延迟导入：离线测试无需该依赖路径

        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    def chat(self, messages: List[Dict[str, Any]], max_tokens: int = 1024, temperature: float = 0.7, retries: int = 3) -> str:
        last_err: Exception | None = None
        for _ in range(retries):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model, messages=messages, max_tokens=max_tokens, temperature=temperature
                )
                self.usage["calls"] += 1
                if resp.usage:
                    self.usage["prompt_tokens"] += resp.usage.prompt_tokens or 0
                    self.usage["completion_tokens"] += resp.usage.completion_tokens or 0
                content = (resp.choices[0].message.content or "").strip()
                if content:
                    return content
                last_err = ValueError("empty completion")
            except Exception as e:  # noqa: BLE001
                last_err = e
            time.sleep(1.0)
        raise RuntimeError(f"chat failed after {retries} retries: {last_err}")


def load_backend(root: pathlib.Path, backend: str = "deepseek") -> tuple[ChatClient, str]:
    """按 backends.local.yaml（覆盖）→ backends.yaml 顺序加载后端配置。"""
    local = root / "configs" / "backends.local.yaml"
    base = root / "configs" / "backends.yaml"
    cfg: Dict[str, Any] = {}
    if base.exists():
        cfg = yaml.safe_load(base.read_text(encoding="utf-8")) or {}
    if local.exists():
        local_cfg = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        cfg["backends"] = {**cfg.get("backends", {}), **local_cfg.get("backends", {})}
        cfg.update({k: v for k, v in local_cfg.items() if k != "backends"})

    b = (cfg.get("backends") or {}).get(backend) or {}
    api_key = b.get("api_key") or os.environ.get(b.get("api_key_env") or "", "")
    model = b.get("models", [""])[0] if b.get("models") else cfg.get("default_model", "")
    client = ChatClient(base_url=b.get("base_url", ""), api_key=api_key, model=model)

    # 本地端点绕代理（spike 报告 F2）
    os.environ.setdefault("NO_PROXY", DEFAULT_NO_PROXY)
    os.environ.setdefault("no_proxy", DEFAULT_NO_PROXY)
    return client, model
