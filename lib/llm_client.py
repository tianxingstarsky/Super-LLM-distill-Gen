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

    def add_usd(self, amount: float) -> None:
        self.spent += amount
        self.save()
        if self.hard_stop and self.spent >= self.limit:
            raise BudgetExceeded(
                f"预算上限已到：累计 ${self.spent:.4f} ≥ ${self.limit}（data/output/budget.json 可查看/清零）"
            )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"spent_usd": round(self.spent, 6), "limit_usd": self.limit}, indent=1), encoding="utf-8")


class ChatClient:
    """带空回复重试的 OpenAI 兼容客户端（DeepSeek V4 Flash 实测 ~20% 空 completion）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        price_input_per_1m: float = 0.0,
        price_output_per_1m: float = 0.0,
        budget: BudgetGuard | None = None,
    ):
        from openai import OpenAI  # 延迟导入：离线测试无需该依赖路径

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
        thinking=False 时请求 API 禁用思考（thinking: {type: disabled}）。
        json_mode=True 时优先用 response_format json_object 解码层强制合法 JSON；
        若 API 不支持（本实例已探测为 False 或调用被拒）则自动降级为纯提示词约束。"""
        last_err: Exception | None = None
        for _ in range(retries):
            try:
                kwargs: Dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature}
                if max_tokens is not None:
                    kwargs["max_tokens"] = max_tokens
                if json_mode and self.json_supported is not False:
                    kwargs["response_format"] = {"type": "json_object"}
                if not thinking:
                    kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
                resp = self.client.chat.completions.create(**kwargs)
                if json_mode and self.json_supported is None:
                    self.json_supported = True  # 首次成功即确认能力
                self.usage["calls"] += 1
                if resp.usage:
                    self.usage["prompt_tokens"] += resp.usage.prompt_tokens or 0
                    self.usage["completion_tokens"] += resp.usage.completion_tokens or 0
                    if self.budget and (self.price_input or self.price_output):
                        cost = (
                            (resp.usage.prompt_tokens or 0) / 1e6 * self.price_input
                            + (resp.usage.completion_tokens or 0) / 1e6 * self.price_output
                        )
                        self.budget.add_usd(cost)
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
                # 分层降级 L1→L3：json_mode 被 API 拒绝（不支持 response_format）
                # → 记录能力探测结果，同次循环降级重试（不消耗用户配置的重试次数）
                if json_mode and self.json_supported is not False and _is_format_unsupported(e):
                    self.json_supported = False
                    continue
                last_err = e
            time.sleep(1.0)
        raise RuntimeError(f"chat failed after {retries} retries: {last_err}")


def _is_format_unsupported(err: Exception) -> bool:
    """判断异常是否为 'response_format 不支持' 类（400/BadRequest + 关键字）。"""
    text = str(err).lower()
    if "response_format" in text:
        return True
    if "badrequest" in text and ("unsupported" in text or "unavailable" in text or "not supported" in text):
        return True
    return False


def load_backend(
    root: pathlib.Path,
    backend: str | None = None,
    model: str | None = None,
    judge: bool = False,
) -> tuple[ChatClient, str]:
    """按 backends.local.yaml（覆盖）→ backends.yaml 顺序加载后端配置。

    backend/model 显式指定优先；judge=True 时默认走 judge_backend/judge_model。
    预算：backends.yaml 的 budget（max_total_usd/hard_stop）+ 各后端 prices 生效，
    超限抛 BudgetExceeded。"""
    local = root / "configs" / "backends.local.yaml"
    base = root / "configs" / "backends.yaml"
    cfg: Dict[str, Any] = {}
    if base.exists():
        cfg = yaml.safe_load(base.read_text(encoding="utf-8")) or {}
    if local.exists():
        local_cfg = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        cfg["backends"] = {**cfg.get("backends", {}), **local_cfg.get("backends", {})}
        cfg.update({k: v for k, v in local_cfg.items() if k != "backends"})

    if judge and backend is None:
        backend = cfg.get("judge_backend")
        model = model or cfg.get("judge_model")
    name = backend or cfg.get("default_backend", "deepseek")
    b = (cfg.get("backends") or {}).get(name) or {}
    api_key = b.get("api_key") or os.environ.get(b.get("api_key_env") or "", "")
    model = model or (b.get("models", [""])[0] if b.get("models") else "") or cfg.get("default_model", "")

    budget_cfg = cfg.get("budget") or {}
    guard = None
    if budget_cfg.get("max_total_usd"):
        guard = BudgetGuard(root, budget_cfg["max_total_usd"], bool(budget_cfg.get("hard_stop", True)))
    prices = b.get("prices") or {}
    client = ChatClient(
        base_url=b.get("base_url", ""),
        api_key=api_key,
        model=model,
        price_input_per_1m=float(prices.get("input_per_1m_usd", 0.0)),
        price_output_per_1m=float(prices.get("output_per_1m_usd", 0.0)),
        budget=guard,
    )

    # 本地端点绕代理（spike 报告 F2）
    os.environ.setdefault("NO_PROXY", DEFAULT_NO_PROXY)
    os.environ.setdefault("no_proxy", DEFAULT_NO_PROXY)
    return client, model
