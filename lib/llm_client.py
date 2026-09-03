"""ChatClient：OpenAI 兼容聊天客户端（空回复重试 + token 记账），配置来自 backends.yaml。"""
from __future__ import annotations

import json
import os
import pathlib
import time
from typing import Any, Dict, List

import yaml

DEFAULT_NO_PROXY = "127.0.0.1,localhost"


def parse_json_robust(output: str) -> Dict[str, Any]:
    """容错 JSON 解析：剥代码围栏 → 取首个平衡对象 → 尾部截断重试。"""
    text = output.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[-1].strip() if text.count("```") >= 2 else text.replace("```", "").strip()
    for candidate in (text,):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    # 平衡扫描取首个完整对象
    start = text.find("{")
    if start >= 0:
        depth = 0
        in_str = False
        for i in range(start, len(text)):
            ch = text[i]
            if ch == '"' and (i == 0 or text[i - 1] != "\\"):
                in_str = not in_str
            elif not in_str:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start : i + 1])
                        except json.JSONDecodeError:
                            break
    # 尾部截断重试（多余尾随文本）
    last = text.rfind("}")
    if last > 0:
        try:
            return json.loads(text[: last + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"JSON 解析失败: {output[:200]!r}")


def chat_json(
    client: "ChatClient",
    messages: List[Dict[str, Any]],
    temperature: float = 0.2,
    retries: int = 3,
    thinking: bool = False,
) -> Dict[str, Any]:
    """严格 JSON 调用：response_format 解码层强制 + 容错解析 + 降温度重试。"""
    last_err: Exception | None = None
    for attempt in range(retries):
        temp = temperature if attempt == 0 else min(temperature, 0.3)
        out = client.chat(messages, max_tokens=None, temperature=temp, thinking=thinking, json_mode=True)
        try:
            return parse_json_robust(out)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"JSON 调用失败（{retries} 次重试后）: {last_err}")


class ChatClient:
    """带空回复重试的 OpenAI 兼容客户端（DeepSeek V4 Flash 实测 ~20% 空 completion）。"""

    def __init__(self, base_url: str, api_key: str, model: str):
        from openai import OpenAI  # 延迟导入：离线测试无需该依赖路径

        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    def chat(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int | None = 1024,
        temperature: float = 0.7,
        retries: int = 3,
        thinking: bool = True,
        json_mode: bool = False,
    ) -> str:
        """max_tokens=None 时不限制输出长度（思考允许无限长度；正文在思考后输出）。
        thinking=False 时请求 API 禁用思考（thinking: {type: disabled}）——
        严格 JSON 输出类任务必须关闭思考，否则答案会进 reasoning 而 content 为空
        （v4-pro 实测，见 prompt-eval 迭代记录）。
        json_mode=True 时用 response_format json_object 在解码层强制合法 JSON
        （DeepSeek V4 实测支持；提示词需含 "json" 字样——本库 JSON 提示词均已满足）。"""
        last_err: Exception | None = None
        for _ in range(retries):
            try:
                kwargs: Dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature}
                if max_tokens is not None:
                    kwargs["max_tokens"] = max_tokens
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                if not thinking:
                    kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
                resp = self.client.chat.completions.create(**kwargs)
                self.usage["calls"] += 1
                if resp.usage:
                    self.usage["prompt_tokens"] += resp.usage.prompt_tokens or 0
                    self.usage["completion_tokens"] += resp.usage.completion_tokens or 0
                msg = resp.choices[0].message
                content = (msg.content or "").strip()
                # 思考型模型（v4-pro 等）长输入时思考可能吃满 max_tokens 导致 content 空；
                # 兜底取 reasoning_content 作为输出
                if not content:
                    rc = getattr(msg, "reasoning_content", None)
                    if rc:
                        content = rc.strip()
                if content:
                    return content
                last_err = ValueError("empty completion")
            except Exception as e:  # noqa: BLE001
                last_err = e
            time.sleep(1.0)
        raise RuntimeError(f"chat failed after {retries} retries: {last_err}")


def load_backend(
    root: pathlib.Path,
    backend: str = "deepseek",
    model: str | None = None,
    judge: bool = False,
) -> tuple[ChatClient, str]:
    """按 backends.local.yaml（覆盖）→ backends.yaml 顺序加载后端配置。

    judge=True 时使用 judge_backend/judge_model（打分角色用更稳的模型）。"""
    local = root / "configs" / "backends.local.yaml"
    base = root / "configs" / "backends.yaml"
    cfg: Dict[str, Any] = {}
    if base.exists():
        cfg = yaml.safe_load(base.read_text(encoding="utf-8")) or {}
    if local.exists():
        local_cfg = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        cfg["backends"] = {**cfg.get("backends", {}), **local_cfg.get("backends", {})}
        cfg.update({k: v for k, v in local_cfg.items() if k != "backends"})

    if judge:
        backend = cfg.get("judge_backend", backend)
        model = model or cfg.get("judge_model")
    name = backend
    b = (cfg.get("backends") or {}).get(name) or {}
    api_key = b.get("api_key") or os.environ.get(b.get("api_key_env") or "", "")
    model = model or (b.get("models", [""])[0] if b.get("models") else "") or cfg.get("default_model", "")
    client = ChatClient(base_url=b.get("base_url", ""), api_key=api_key, model=model)

    # 本地端点绕代理（spike 报告 F2）
    os.environ.setdefault("NO_PROXY", DEFAULT_NO_PROXY)
    os.environ.setdefault("no_proxy", DEFAULT_NO_PROXY)
    return client, model
