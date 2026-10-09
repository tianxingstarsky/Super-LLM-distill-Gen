"""Pure rules for backend configuration and safe inventory display."""
from __future__ import annotations

import re
import math
from typing import Any
from urllib.parse import urlsplit

from lib.model_protocols import validate_api_format


VALID_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
VALID_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
VALID_ROLES = frozenset({"generation", "judge", "jev", "vision", "refine", "simulate", "translation"})
TOKEN_PRICE_KEYS = frozenset({"input_per_1m_usd", "output_per_1m_usd"})


def validate_token_prices(value: object) -> dict[str, float]:
    """A budget needs both rates; an explicit pair of zeros means free service."""
    if type(value) is not dict or set(value) != TOKEN_PRICE_KEYS:
        raise ValueError("model_budget_prices_required")
    prices = {}
    for key in TOKEN_PRICE_KEYS:
        rate = value[key]
        if type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0:
            raise ValueError("model_budget_prices_required")
        prices[key] = float(rate)
    return prices


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 6:
        return "***"
    return f"{key[:3]}***{key[-4:]}"


def validate_backend_url(base_url: str) -> str:
    """Keep credentials out of endpoint URLs that may enter run recipes."""
    if (type(base_url) is not str or not base_url or len(base_url) > 2048
            or any(char.isspace() or ord(char) < 32 for char in base_url)
            or any(char in base_url for char in ("?", "#", "\\"))):
        raise ValueError("base_url 必须是不含凭据的 http(s):// 地址")
    try:
        parsed = urlsplit(base_url)
        valid = (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                 and parsed.username is None and parsed.password is None
                 and not parsed.query and not parsed.fragment)
        # Accessing port also rejects malformed authorities such as :not-a-port.
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("base_url 必须是不含凭据的 http(s):// 地址")
    return base_url


def validate_credential_reference(name: str) -> str:
    """Reject pasted key values where an environment-variable name is expected."""
    if type(name) is not str or (name and not VALID_ENV_NAME.fullmatch(name)):
        raise ValueError("api_key_env 必须是环境变量名")
    return name


def validate_endpoint(name: str, base_url: str, models: list[str], api_format: str = "chat") -> None:
    validate_api_format(api_format)
    if not VALID_NAME.match(name or ""):
        raise ValueError("后端名只允许字母/数字/下划线/连字符（1-32）")
    validate_backend_url(base_url)
    if not isinstance(models, list) or not models:
        raise ValueError("至少填一个模型名（models）")
    if any(type(model) is not str or not model.strip() or len(model) > 200
           or any(ord(character) < 32 for character in model) for model in models):
        raise ValueError("invalid_model_capability")


def validate_role(role: str) -> None:
    if not VALID_NAME.match(role or "") or role not in VALID_ROLES:
        raise ValueError(f"角色不合法：{role!r}（可用 generation/judge/jev/vision/refine/simulate/translation）")


def merged_backends(base: dict, local: dict) -> dict[str, dict[str, Any]]:
    merged = {name: dict(value) for name, value in (base.get("backends") or {}).items()}
    for name, value in (local.get("backends") or {}).items():
        merged[name] = {**merged.get(name, {}), **value}
    for backend in merged.values():
        backend["api_format"] = validate_api_format(backend.get("api_format", "chat"))
    return merged


def key_display(backend: dict, *, env_present: bool) -> dict[str, str]:
    env_name = str(backend.get("api_key_env") or "")
    cfg_val = backend.get("api_key") or ""
    if env_name and env_present:
        return {"source": f"env:{env_name}", "status": "存在"}
    if cfg_val:
        return {"source": "configs/backends.local.yaml", "status": mask_key(str(cfg_val))}
    return {"source": "未配置", "status": "缺失"}
