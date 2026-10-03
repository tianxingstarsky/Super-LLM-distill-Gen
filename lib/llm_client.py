"""One workflow client for Chat Completions, Responses, and Anthropic Messages."""
from __future__ import annotations

import json
import hashlib
import os
import pathlib
import time
from typing import Any, Dict, List
from urllib.parse import urlsplit

import yaml

from lib.domain.backend_config import validate_backend_url, validate_credential_reference
from lib.model_protocols import (ANTHROPIC_DEFAULT_MAX_TOKENS, anthropic_request,
                                 anthropic_text, response_text, responses_input,
                                 validate_api_format)

DEFAULT_NO_PROXY = "127.0.0.1,localhost"


def parse_json_robust(output: str) -> Dict[str, Any]:
    """容错 JSON 解析：剥代码围栏 → 取首个平衡对象 → 尾部截断重试。"""
    text = output.strip()
    if text.count("```") >= 2:
        # 取首对围栏之间的内容（split 后中间段），并去掉可能的语言标签行
        text = text.split("```", 2)[1].strip()
        first_line = text.split("\n", 1)[0].strip()
        if first_line and first_line.isalpha() and "\n" in text:
            text = text.split("\n", 1)[1].strip()
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
    raise ValueError("JSON 解析失败")


def chat_json(
    client: "ChatClient",
    messages: List[Dict[str, Any]],
    temperature: float = 0.2,
    retries: int = 3,
    thinking: bool = False,
    max_tokens: int | None = None,
) -> Dict[str, Any]:
    """严格 JSON 调用：response_format 解码层强制 + 容错解析 + 降温度重试。"""
    if max_tokens is not None and (type(max_tokens) is not int or max_tokens <= 0):
        raise ValueError("max_tokens must be a positive integer")
    last_err: Exception | None = None
    for attempt in range(retries):
        temp = temperature if attempt == 0 else min(temperature, 0.3)
        out = client.chat(messages, max_tokens=max_tokens, temperature=temp,
                          thinking=thinking, json_mode=True)
        try:
            return parse_json_robust(out)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"JSON 调用失败（{retries} 次重试后）: {type(last_err).__name__}") from None


class BudgetExceeded(RuntimeError):
    """累计成本超过配置上限（budget.hard_stop 时抛出）。"""


class BudgetGuard:
    """预算守卫：按 token 价格累计成本，持久化到 data/output/budget.json。"""

    def __init__(self, root: pathlib.Path, limit_usd: float, hard_stop: bool = True):
        self.path = pathlib.Path(root) / "data" / "output" / "budget.json"
        self.limit = float(limit_usd)
        self.hard_stop = hard_stop
        self.spent = 0.0
        if self.path.exists():
            try:
                self.spent = float(json.loads(self.path.read_text(encoding="utf-8")).get("spent_usd", 0.0))
            except (json.JSONDecodeError, OSError):
                self.spent = 0.0

    def check(self) -> None:
        if self.path.exists():
            self.spent = float(json.loads(self.path.read_text(encoding="utf-8")).get("spent_usd", 0))
        if self.hard_stop and self.spent >= self.limit:
            raise BudgetExceeded("Budget exhausted; no request sent")

    def add_usd(self, amount: float) -> None:
        from filelock import FileLock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + '.lock', timeout=30):
            if self.path.exists():
                self.spent = float(json.loads(self.path.read_text(encoding="utf-8")).get("spent_usd", 0))
            self.spent += amount
            self.save()
        if self.hard_stop and self.spent >= self.limit:
            raise BudgetExceeded(
                f"预算上限已到：累计 ${self.spent:.4f} ≥ ${self.limit}（data/output/budget.json 可查看/清零）"
            )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        from lib.io_utils import atomic_json
        atomic_json(self.path, {"spent_usd": round(self.spent, 6), "limit_usd": self.limit})


class ChatClient:
    """Text client with bounded retries, normalized output and token accounting."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        price_input_per_1m: float = 0.0,
        price_output_per_1m: float = 0.0,
        budget: BudgetGuard | None = None,
        api_format: str = "chat",
        context_window_tokens: int | None = None,
    ):
        # 本地端点绕代理（spike 报告 F2）：httpx 在 OpenAI 客户端构造时快照代理
        # 环境变量，必须在构造前设置；构造后再 setdefault 对本客户端无效。
        os.environ.setdefault("NO_PROXY", DEFAULT_NO_PROXY)
        os.environ.setdefault("no_proxy", DEFAULT_NO_PROXY)
        self.api_format = validate_api_format(api_format)
        if context_window_tokens is not None and (type(context_window_tokens) is not int or context_window_tokens <= 0):
            raise ValueError("context_window_tokens must be a positive integer")
        self.context_window_tokens = context_window_tokens
        if self.api_format == "anthropic":
            from anthropic import Anthropic

            self.client = Anthropic(base_url=base_url, api_key=api_key)
        else:
            from openai import OpenAI  # delayed import keeps offline domain tests lightweight

            self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
        self.price_input = price_input_per_1m
        self.price_output = price_output_per_1m
        self.budget = budget
        # response_format 能力探测结果：None=未知 / True=支持 / False=不支持。
        # 首次 json_mode 调用被 API 拒绝后置 False，同会话后续自动降级为
        # "提示词要求 JSON + 容错解析"路径，不再硬重试（分层降级 L1→L3）。
        self.json_supported: bool | None = None

    def _check_context(self, messages: List[Dict[str, Any]], max_tokens: int | None) -> None:
        """Conservative text-only preflight; providers remain the final token authority."""
        if self.context_window_tokens is None:
            return
        output = max_tokens or (ANTHROPIC_DEFAULT_MAX_TOKENS if self.api_format == "anthropic" else 0)
        if output >= self.context_window_tokens:
            raise ValueError("max_output_tokens_exceeds_context_window")
        # UTF-8 bytes upper-bound byte-level text tokenization. Image payloads
        # have provider-specific token costs, so do not miscount base64 bytes.
        text_parts: list[str] = []
        for message in messages:
            content = message.get("content", "")
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                text_parts.extend(part.get("text", "") for part in content
                                  if isinstance(part, dict) and isinstance(part.get("text"), str))
        if sum(len(part.encode("utf-8")) for part in text_parts) + output > self.context_window_tokens:
            raise ValueError("model_context_window_exceeded")

    def _request(self, messages: List[Dict[str, Any]], *, max_tokens: int | None,
                 temperature: float, thinking: bool, json_mode: bool) -> tuple[str, int, int]:
        if self.api_format == "anthropic":
            kwargs = anthropic_request(messages, json_mode=json_mode)
            kwargs.update(model=self.model, temperature=temperature,
                          max_tokens=max_tokens or ANTHROPIC_DEFAULT_MAX_TOKENS)
            try:
                response = self.client.messages.create(**kwargs)
            except Exception as error:
                if not _is_temperature_unsupported(error):
                    raise
                kwargs.pop("temperature")
                response = self.client.messages.create(**kwargs)
            usage = getattr(response, "usage", None)
            return (anthropic_text(response), getattr(usage, "input_tokens", 0) or 0,
                    getattr(usage, "output_tokens", 0) or 0)
        if self.api_format == "responses":
            kwargs: Dict[str, Any] = {"model": self.model, "input": responses_input(messages),
                                      "temperature": temperature, "store": False}
            if max_tokens is not None:
                kwargs["max_output_tokens"] = max_tokens
            if json_mode and self.json_supported is not False:
                kwargs["text"] = {"format": {"type": "json_object"}}
            try:
                response = self.client.responses.create(**kwargs)
            except Exception as error:
                if not _is_temperature_unsupported(error):
                    raise
                kwargs.pop("temperature")
                response = self.client.responses.create(**kwargs)
            if json_mode and self.json_supported is None:
                self.json_supported = True
            usage = getattr(response, "usage", None)
            return (response_text(response), getattr(usage, "input_tokens", 0) or 0,
                    getattr(usage, "output_tokens", 0) or 0)
        kwargs = {"model": self.model, "messages": messages, "temperature": temperature}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if json_mode and self.json_supported is not False:
            kwargs["response_format"] = {"type": "json_object"}
        if not thinking:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        for _ in range(4):
            try:
                response = self.client.chat.completions.create(**kwargs)
                break
            except Exception as error:
                if "max_tokens" in kwargs and _is_parameter_unsupported(error, "max_tokens"):
                    kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                elif "temperature" in kwargs and _is_temperature_unsupported(error):
                    kwargs.pop("temperature")
                elif "extra_body" in kwargs and _is_parameter_unsupported(error, "thinking"):
                    kwargs.pop("extra_body")
                else:
                    raise
        else:
            raise RuntimeError("chat parameter negotiation failed")
        if json_mode and self.json_supported is None:
            self.json_supported = True
        usage = getattr(response, "usage", None)
        message = response.choices[0].message
        content = (message.content or "").strip()
        if not content:
            reasoning = getattr(message, "reasoning_content", None)
            if isinstance(reasoning, str):
                content = reasoning.strip()
        return (content, getattr(usage, "prompt_tokens", 0) or 0,
                getattr(usage, "completion_tokens", 0) or 0)

    def chat(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int | None = 1024,
        temperature: float = 0.7,
        retries: int = 3,
        thinking: bool = True,
        json_mode: bool = False,
    ) -> str:
        """Return text through the selected protocol and count its usage."""
        if max_tokens is not None and (type(max_tokens) is not int or max_tokens <= 0):
            raise ValueError("max_tokens must be a positive integer")
        self._check_context(messages, max_tokens)
        last_err: Exception | None = None
        attempts = 0
        while attempts < retries:
            try:
                if self.budget:
                    self.budget.check()
                content, prompt_tokens, completion_tokens = self._request(
                    messages, max_tokens=max_tokens, temperature=temperature,
                    thinking=thinking, json_mode=json_mode)
                self.usage["calls"] += 1
                self.usage["prompt_tokens"] += prompt_tokens
                self.usage["completion_tokens"] += completion_tokens
                if self.budget and (self.price_input or self.price_output):
                    self.budget.add_usd(prompt_tokens / 1e6 * self.price_input
                                        + completion_tokens / 1e6 * self.price_output)
                if content:
                    return content
                last_err = ValueError("empty completion")
            except BudgetExceeded:
                raise
            except ValueError:
                raise
            except Exception as e:  # noqa: BLE001
                # 分层降级 L1→L3：json_mode 被 API 拒绝（不支持 response_format）
                # → 记录能力探测结果，同次循环降级重试（不消耗用户配置的重试次数）
                if (self.api_format != "anthropic" and json_mode
                        and self.json_supported is not False and _is_format_unsupported(e)):
                    self.json_supported = False
                    continue
                last_err = e
            attempts += 1
            time.sleep(1.0)
        # Provider error messages may echo credentials or source material.
        raise RuntimeError(f"chat failed after {retries} retries: {type(last_err).__name__}") from None


def _is_format_unsupported(err: Exception) -> bool:
    """判断异常是否为 'response_format 不支持' 类（400/BadRequest + 关键字）。"""
    text = str(err).lower()
    if "response_format" in text or "text.format" in text or "json_object" in text:
        return True
    if "badrequest" in text and ("unsupported" in text or "unavailable" in text or "not supported" in text):
        return True
    return False


def _is_temperature_unsupported(err: Exception) -> bool:
    return _is_parameter_unsupported(err, "temperature")


def _is_parameter_unsupported(err: Exception, parameter: str) -> bool:
    text = str(err).lower()
    return parameter in text and any(value in text for value in
                                     ("unsupported", "not supported", "unavailable", "unknown parameter"))


def _configured_backends(root: pathlib.Path) -> Dict[str, Any]:
    """Read the same effective configuration for snapshots and requests."""
    local = root / "configs" / "backends.local.yaml"
    base = root / "configs" / "backends.yaml"
    cfg: Dict[str, Any] = {}
    if base.exists():
        cfg = yaml.safe_load(base.read_text(encoding="utf-8")) or {}
    if local.exists():
        local_cfg = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        merged = dict(cfg.get("backends", {}))
        for key, value in local_cfg.get("backends", {}).items():
            base_backend = merged.get(key, {})
            merged[key] = {**base_backend, **value, "prices": {**base_backend.get("prices", {}),
                                                               **value.get("prices", {})}}
        cfg["backends"] = merged
        cfg.update({k: v for k, v in local_cfg.items() if k != "backends"})
    return cfg


def _endpoint_pin(name: str, model: str, backend: dict) -> dict:
    """Keep only safe transport metadata and a digest of those public fields."""
    if not isinstance(backend, dict) or not backend.get("base_url"):
        raise ValueError("workflow_node_service_not_configured")
    try:
        base_url = validate_backend_url(backend["base_url"])
        api_format = validate_api_format(backend.get("api_format", "chat"))
    except ValueError:
        raise ValueError("workflow_endpoint_url_or_protocol_invalid") from None
    models = backend.get("models") or []
    if not isinstance(models, list) or any(not isinstance(item, str) for item in models):
        raise ValueError("workflow_endpoint_models_invalid")
    default_env = "ANTHROPIC_API_KEY" if api_format == "anthropic" else "OPENAI_API_KEY"
    if backend.get("api_key"):
        credential_ref = "inline"
    else:
        try:
            env_name = validate_credential_reference(
                backend.get("api_key_env") if "api_key_env" in backend else default_env)
        except ValueError:
            raise ValueError("workflow_endpoint_credential_ref_invalid") from None
        credential_ref = "env:" + env_name
    parsed = urlsplit(base_url)
    payload = {"version": 1, "backend": name, "model": model,
               "endpoint_origin": parsed.scheme + "://" + parsed.netloc,
               "base_url_sha256": hashlib.sha256(base_url.encode("utf-8")).hexdigest(),
               "api_format": api_format, "models": sorted(set(models)),
               "credential_ref": credential_ref}
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**payload, "config_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest()}


def snapshot_backend_endpoint(root: pathlib.Path, backend: str, model: str) -> dict:
    """Pin a node's selected service before its queued run is created."""
    config = _configured_backends(pathlib.Path(root))
    selected = (config.get("backends") or {}).get(backend)
    return _endpoint_pin(backend, model, selected)


def load_backend(
    root: pathlib.Path,
    backend: str | None = None,
    model: str | None = None,
    judge: bool = False,
    base_url: str | None = None,
    role: str | None = None,
    allow_global_endpoint_override: bool = True,
    context_window_tokens: int | None = None,
    expected_endpoint_pin: dict | None = None,
) -> tuple[ChatClient, str]:
    """按 backends.local.yaml（覆盖）→ backends.yaml 顺序加载后端配置。

    解析优先级：显式 backend/model/base_url > 角色专属环境变量 > role 槽位 > 默认配置。
    role ∈ {generation, judge, jev, vision, refine, simulate, translation}。JEV 可用 JEV_BACKEND/JEV_MODEL 环境变量独立配置；
    judge=True 等价 role='judge'。base_url 自定义端点（本地 Ollama/llama.cpp 等）
    时 api_key 走 OPENAI_API_KEY 环境变量。
    预算：backends.yaml 的 budget + 各后端 prices 生效，超限抛 BudgetExceeded。"""
    cfg = _configured_backends(root)

    if judge and role is None:
        role = "judge"
    if role and base_url is None:
        slot = (cfg.get("model_roles") or {}).get(role) or {}
        role_backend = os.environ.get(f"{role.upper()}_BACKEND")
        role_model = os.environ.get(f"{role.upper()}_MODEL")
        backend = backend or role_backend or slot.get("backend") or (cfg.get("judge_backend") if role == "judge" else None)
        global_model = os.environ.get("LLM_MODEL") if role != "jev" else None
        model = model or role_model or global_model or slot.get("model") or (cfg.get("judge_model") if role == "judge" else None)
    if judge and backend is None and base_url is None:
        backend = cfg.get("judge_backend")
        model = model or cfg.get("judge_model")
    if base_url:
        # 自定义端点：无配置条目，key 走 OPENAI_API_KEY 环境变量
        b = {"base_url": base_url, "api_key_env": "OPENAI_API_KEY", "models": []}
    else:
        name = backend or cfg.get("default_backend", "deepseek")
        b = (cfg.get("backends") or {}).get(name) or {}
        if not allow_global_endpoint_override and not b.get("base_url"):
            raise ValueError("workflow_node_service_not_configured")
    # 环境变量级全局覆盖（任意命令的临时操作空间，无需加 CLI 参数）
    if allow_global_endpoint_override and not base_url and os.environ.get("LLM_BASE_URL"):
        b = {"base_url": os.environ["LLM_BASE_URL"], "api_key_env": "OPENAI_API_KEY", "models": []}
    role_model = os.environ.get(f"{role.upper()}_MODEL") if role else None
    global_model = os.environ.get("LLM_MODEL") if role != "jev" else None
    model = model or role_model or global_model or (b.get("models", [""])[0] if b.get("models") else "") or cfg.get("default_model", "")
    if expected_endpoint_pin is not None:
        if base_url or allow_global_endpoint_override:
            raise ValueError("workflow_endpoint_pin_invalid")
        try:
            current_pin = _endpoint_pin(name, model, b)
        except ValueError:
            raise ValueError("workflow_endpoint_changed_create_new_run") from None
        if current_pin != expected_endpoint_pin:
            raise ValueError("workflow_endpoint_changed_create_new_run")
    api_format = validate_api_format(b.get("api_format", "chat"))
    default_key_env = "ANTHROPIC_API_KEY" if api_format == "anthropic" else "OPENAI_API_KEY"
    configured_key_env = b.get("api_key_env") if "api_key_env" in b else default_key_env
    api_key = b.get("api_key") or os.environ.get(configured_key_env or "", "")

    budget_cfg = cfg.get("budget") or {}
    guard = None
    if budget_cfg.get("max_total_usd"):
        guard = BudgetGuard(root, budget_cfg["max_total_usd"], bool(budget_cfg.get("hard_stop", True)))
    prices = b.get("prices") or {}
    client = ChatClient(
        base_url=b.get("base_url", ""),
        api_key=api_key,
        model=model,
        api_format=api_format,
        context_window_tokens=context_window_tokens,
        price_input_per_1m=float(prices.get("input_per_1m_usd", 0.0)),
        price_output_per_1m=float(prices.get("output_per_1m_usd", 0.0)),
        budget=guard,
    )
    return client, model
