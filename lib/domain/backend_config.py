"""Pure rules for backend configuration and safe inventory display."""
from __future__ import annotations

import re
from typing import Any

from lib.model_protocols import validate_api_format


VALID_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
VALID_ROLES = frozenset({"generation", "judge", "jev", "vision", "refine", "simulate", "translation"})


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 6:
        return "***"
    return f"{key[:3]}***{key[-4:]}"


def validate_endpoint(name: str, base_url: str, models: list[str], api_format: str = "chat") -> None:
    validate_api_format(api_format)
    if not VALID_NAME.match(name or ""):
        raise ValueError("后端名只允许字母/数字/下划线/连字符（1-32）")
    if not re.match(r"^https?://", base_url or ""):
        raise ValueError("base_url 必须是 http(s):// 地址")
    if not models:
        raise ValueError("至少填一个模型名（models）")


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
